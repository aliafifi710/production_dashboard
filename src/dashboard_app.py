import sys
import json
import socket
import queue
import threading
import logging
from dataclasses import dataclass, field
from datetime import datetime
from collections import deque
from typing import Dict, Deque, Optional, Any, List
import select

import yaml
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor, QBrush
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QGridLayout,
    QHBoxLayout,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QHeaderView,
    QSplitter,
    QLineEdit,
    QPushButton,
    QTextEdit,
)

import pyqtgraph as pg

from fastapi import FastAPI
import uvicorn


# =========================
# Logging to UI (thread-safe)
# =========================
class QueueLogHandler(logging.Handler):
    def __init__(self, out_queue: queue.Queue):
        super().__init__()
        self.out_queue = out_queue

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            self.out_queue.put(msg)
        except Exception:
            pass


# =========================
# Remote API Shared State
# =========================
class SharedApiState:
    def __init__(self):
        self._lock = threading.Lock()
        self._system_status: str = "LISTENING"
        self._sensors: Dict[str, Dict[str, Any]] = {}
        self._alarms: List[Dict[str, Any]] = []

    def update_sensor(self, name: str, snapshot: Dict[str, Any]) -> None:
        with self._lock:
            self._sensors[name] = dict(snapshot)

    def set_system_status(self, status: str) -> None:
        with self._lock:
            self._system_status = status

    def add_alarm(self, alarm: Dict[str, Any], cap: int = 500) -> None:
        with self._lock:
            self._alarms.append(dict(alarm))
            if len(self._alarms) > cap:
                self._alarms = self._alarms[-cap:]

    def clear_alarms(self) -> None:
        with self._lock:
            self._alarms.clear()

    def snapshot_sensors(self) -> Dict[str, Any]:
        with self._lock:
            sensors_list = [{"name": k, **v} for k, v in self._sensors.items()]
            return {
                "system_status": self._system_status,
                "sensors": sensors_list,
                "alarms_count": len(self._alarms),
            }

    def snapshot_alarms(self, last_n: int = 200) -> Dict[str, Any]:
        with self._lock:
            data = self._alarms[-last_n:]
            return {"count": len(self._alarms), "alarms": list(data)}


def create_api_app(state: SharedApiState) -> FastAPI:
    app = FastAPI(title="Production Line Remote API", version="1.0")

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/sensors")
    def sensors():
        return state.snapshot_sensors()

    @app.get("/api/alarms")
    def alarms():
        return state.snapshot_alarms()

    return app


class ApiServerThread(threading.Thread):
    def __init__(self, app: FastAPI, host: str, port: int):
        super().__init__(daemon=True)
        self._config = uvicorn.Config(app, host=host, port=port, log_level="warning")
        self._server = uvicorn.Server(self._config)

    def run(self):
        self._server.run()

    def stop(self):
        self._server.should_exit = True


# =========================
# Models
# =========================
@dataclass
class SensorState:
    name: str
    low: float
    high: float
    value: Optional[float] = None
    ts: str = "-"
    status: str = "N/A"  # OK / FAULT / N/A
    in_alarm: bool = False
    t_buf: Deque[float] = field(default_factory=lambda: deque(maxlen=600))
    v_buf: Deque[float] = field(default_factory=lambda: deque(maxlen=600))


# =========================
# TCP SERVER Worker (Dashboard listens)
# + command queue support (dashboard -> simulator)
# =========================
class DashboardTCPServerWorker(threading.Thread):
    """
    Dashboard is TCP server:
    - bind() + listen()
    - accept() simulator connection
    - recv() newline-delimited JSON (robust on Windows)
    - send commands to simulator from cmd_queue
    Never touches GUI directly.
    """
    def __init__(
        self,
        bind_host: str,
        bind_port: int,
        out_queue: queue.Queue,
        cmd_queue: queue.Queue,
        stop_event: threading.Event,
        logger: logging.Logger,
    ):
        super().__init__(daemon=True)
        self.bind_host = bind_host
        self.bind_port = bind_port
        self.out_queue = out_queue
        self.cmd_queue = cmd_queue
        self.stop_event = stop_event
        self.logger = logger

    def _emit_conn(self, state: str) -> None:
        self.out_queue.put({"_type": "conn", "state": state})

    def _try_send_commands(self, conn: socket.socket) -> None:
        while True:
            try:
                cmd = self.cmd_queue.get_nowait()
            except queue.Empty:
                break

            try:
                conn.sendall((json.dumps(cmd) + "\n").encode("utf-8"))
                self.logger.info(f"[CMD] Sent to simulator: {cmd.get('cmd')}")
            except OSError as e:
                self.logger.warning(f"[CMD] Failed to send command: {e}")
                break

    def run(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        try:
            server.bind((self.bind_host, self.bind_port))
            server.listen(1)
            server.settimeout(0.5)

            self.logger.info(f"[TCP] Dashboard listening on {self.bind_host}:{self.bind_port}")
            self._emit_conn("LISTENING")

            while not self.stop_event.is_set():
                try:
                    conn, addr = server.accept()
                except socket.timeout:
                    continue
                except OSError as e:
                    self.logger.warning(f"[TCP] accept() error: {e}")
                    continue

                self.logger.info(f"[TCP] Simulator connected from {addr}")
                self._emit_conn("CONNECTED")

                rx_buf = ""
                try:
                    conn.setblocking(False)

                    while not self.stop_event.is_set():
                        # 1) send commands (if any)
                        self._try_send_commands(conn)

                        # 2) read data (non-blocking)
                        rlist, _, _ = select.select([conn], [], [], 0.05)
                        if not rlist:
                            continue

                        data = conn.recv(4096)
                        if not data:
                            break  # disconnected

                        rx_buf += data.decode("utf-8", errors="ignore")

                        while "\n" in rx_buf:
                            line, rx_buf = rx_buf.split("\n", 1)
                            line = line.strip()
                            if not line:
                                continue

                            try:
                                msg = json.loads(line)

                                # simulator log + snapshot
                                if msg.get("_type") in ("log", "snapshot"):
                                    self.out_queue.put(msg)
                                    continue

                                # sensor data
                                if {"sensor", "value", "ts", "status"}.issubset(msg.keys()):
                                    msg["status"] = str(msg["status"]).upper()
                                    if msg["status"] not in ("OK", "FAULT"):
                                        msg["status"] = "FAULT"
                                    msg["_type"] = "data"
                                    self.out_queue.put(msg)

                            except json.JSONDecodeError:
                                continue

                except Exception as e:
                    # ✅ critical: NEVER let the thread die
                    self.logger.warning(f"[TCP] Connection loop error: {e}")
                finally:
                    try:
                        conn.close()
                    except Exception:
                        pass

                    self.logger.warning("[TCP] Simulator disconnected. Back to listening.")
                    self._emit_conn("DISCONNECTED")
                    self._emit_conn("LISTENING")

        finally:
            try:
                server.close()
            except Exception:
                pass



# =========================
# GUI
# =========================
class MainWindow(QMainWindow):
    def __init__(self, config_path: str):
        super().__init__()
        self.setWindowTitle("Production Line Sensor Dashboard")

        cfg = self._load_config(config_path)

        # Dashboard binds and listens here
        self.bind_host = cfg["simulator"]["host"]
        self.bind_port = int(cfg["simulator"]["port"])

        self.update_hz = float(cfg["ui"]["update_hz"])
        self.plot_window_sec = float(cfg["ui"]["plot_window_sec"])

        # Remote API (still running, but not shown in title)
        self.api_host = cfg.get("api", {}).get("host", "127.0.0.1")
        self.api_port = int(cfg.get("api", {}).get("port", 8000))

        # Maintenance access
        self.maintenance_password = str(cfg.get("maintenance", {}).get("password", "admin"))

        # Logging queue (for maintenance log viewer)
        self.log_queue: queue.Queue = queue.Queue()
        self.logger = logging.getLogger("prod_dashboard")
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False

        if not any(isinstance(h, QueueLogHandler) for h in self.logger.handlers):
            qh = QueueLogHandler(self.log_queue)
            qh.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S"))
            self.logger.addHandler(qh)

        # Stable order from config
        self.sensor_names = [s["name"] for s in cfg["sensors"]]
        self.sensors: Dict[str, SensorState] = {}
        for s in cfg["sensors"]:
            name = s["name"]
            if name in self.sensors:
                raise ValueError(f"Duplicate sensor name in config: {name}")
            self.sensors[name] = SensorState(name=name, low=float(s["low"]), high=float(s["high"]))

        # Connection flags
        self.client_connected = False
        self.has_received_data = False

        # Queues / stop flag
        self.msg_queue: queue.Queue = queue.Queue()  # worker -> GUI (data + conn + log + snapshot)
        self.cmd_queue: queue.Queue = queue.Queue()  # GUI -> worker -> simulator (commands)
        self.stop_event = threading.Event()

        # Start TCP server worker
        self.worker = DashboardTCPServerWorker(
            self.bind_host, self.bind_port,
            self.msg_queue, self.cmd_queue,
            self.stop_event, self.logger
        )
        self.worker.start()

        # Start API server
        self.api_state = SharedApiState()
        api_app = create_api_app(self.api_state)
        self.api_thread = ApiServerThread(api_app, self.api_host, self.api_port)
        self.api_thread.start()

        # Build UI
        self._build_ui()
        self._apply_dark_theme()

        # GUI update timer
        interval_ms = int(1000 / max(self.update_hz, 2.0))
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._on_timer_tick)
        self.timer.start(interval_ms)

        self._set_global_status_listening()
        self.logger.info("[APP] Dashboard started")

    def closeEvent(self, event):
        self.logger.info("[APP] Shutting down...")
        self.stop_event.set()
        if hasattr(self, "api_thread"):
            self.api_thread.stop()
        event.accept()

    def _load_config(self, path: str) -> dict:
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    def _apply_dark_theme(self):
        self.setStyleSheet("""
            QMainWindow { background-color: #1e1e1e; }
            QLabel { color: #ffffff; }
            QTabWidget::pane { border: 1px solid #444444; }
            QTableWidget {
                background-color: #2b2b2b;
                color: #ffffff;
                gridline-color: #444444;
                selection-background-color: #3a3a3a;
                selection-color: #ffffff;
            }
            QHeaderView::section {
                background-color: #333333;
                color: #ffffff;
                padding: 6px;
                border: 1px solid #444444;
            }
            QTextEdit {
                background-color: #111111;
                color: #ffffff;
                border: 1px solid #444444;
                padding: 6px;
            }
            QLineEdit {
                background-color: #2b2b2b;
                color: #ffffff;
                border: 1px solid #444444;
                padding: 6px;
            }
            QPushButton {
                background-color: #333333;
                color: #ffffff;
                border: 1px solid #444444;
                padding: 6px 10px;
            }
            QPushButton:disabled {
                color: #777777;
            }
        """)

    def _build_ui(self):
        root = QWidget()
        root_layout = QVBoxLayout(root)

        self.tabs = QTabWidget()
        root_layout.addWidget(self.tabs)

        # -------- Dashboard tab --------
        dash = QWidget()
        dash_layout = QVBoxLayout(dash)

        self.global_status = QLabel("System: LISTENING...")
        self.global_status.setAlignment(Qt.AlignCenter)
        self.global_status.setStyleSheet("font-size: 16px; font-weight: bold; padding: 8px;")
        dash_layout.addWidget(self.global_status)

        splitter = QSplitter(Qt.Horizontal)

        # Sensor table
        self.table = QTableWidget(len(self.sensor_names), 4)
        self.table.setHorizontalHeaderLabels(["Sensor", "Latest Value", "Timestamp", "Status"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSortingEnabled(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)

        for row, name in enumerate(self.sensor_names):
            self.table.setItem(row, 0, QTableWidgetItem(name))
            self.table.setItem(row, 1, QTableWidgetItem("-"))
            self.table.setItem(row, 2, QTableWidgetItem("-"))
            status_item = QTableWidgetItem("N/A")
            status_item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 3, status_item)
            self._paint_row(row, bg="#2b2b2b", fg="#ffffff")

        splitter.addWidget(self.table)

        # Plots (3x2 grid so 5 sensors fit without scroll)
        plots_widget = QWidget()
        plots_grid = QGridLayout(plots_widget)
        plots_grid.setHorizontalSpacing(10)
        plots_grid.setVerticalSpacing(10)

        self.plot_widgets: Dict[str, pg.PlotWidget] = {}
        self.plot_curves: Dict[str, pg.PlotDataItem] = {}

        pg.setConfigOptions(antialias=True)

        for i, name in enumerate(self.sensor_names):
            pw = pg.PlotWidget()
            pw.setMinimumHeight(150)
            pw.setTitle(name)
            pw.setBackground("#1e1e1e")
            for ax in ("bottom", "left"):
                pw.getAxis(ax).setPen("w")
                pw.getAxis(ax).setTextPen("w")
            pw.showGrid(x=True, y=True, alpha=0.2)

            curve = pw.plot([], [])
            self.plot_widgets[name] = pw
            self.plot_curves[name] = curve

            r = i // 2
            c = i % 2
            plots_grid.addWidget(pw, r, c)

        splitter.addWidget(plots_widget)
        splitter.setSizes([450, 750])
        dash_layout.addWidget(splitter)

        self.tabs.addTab(dash, "Dashboard")

        # -------- Alarm Log tab --------
        alarms = QWidget()
        alarms_layout = QVBoxLayout(alarms)

        self.alarm_table = QTableWidget(0, 4)
        self.alarm_table.setHorizontalHeaderLabels(["Time", "Sensor", "Value", "Alarm Type"])
        self.alarm_table.verticalHeader().setVisible(False)
        self.alarm_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.alarm_table.setSortingEnabled(False)
        self.alarm_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        alarms_layout.addWidget(self.alarm_table)

        self.tabs.addTab(alarms, "Alarm Log")

        # -------- Maintenance Console tab (BONUS A) --------
        maint = QWidget()
        maint_layout = QVBoxLayout(maint)

        # Access control
        access_row = QHBoxLayout()
        access_row.addWidget(QLabel("Maintenance Password:"))
        self.maint_password_input = QLineEdit()
        self.maint_password_input.setEchoMode(QLineEdit.Password)
        access_row.addWidget(self.maint_password_input)

        self.maint_unlock_btn = QPushButton("Unlock")
        self.maint_unlock_btn.clicked.connect(self._on_unlock_clicked)
        access_row.addWidget(self.maint_unlock_btn)

        self.maint_status_label = QLabel("Locked")
        self.maint_status_label.setStyleSheet("font-weight:bold;")
        access_row.addWidget(self.maint_status_label)
        access_row.addStretch(1)

        maint_layout.addLayout(access_row)

        # Commands (the 3 you requested)
        cmd_row = QHBoxLayout()

        self.btn_restart_sim = QPushButton("Restart Simulator")
        self.btn_snapshot_detail = QPushButton("Detailed Snapshot")
        self.btn_clear_alarms = QPushButton("Clear Alarms")

        self.btn_restart_sim.clicked.connect(lambda: self._send_remote_command("RESTART_SIM"))
        self.btn_snapshot_detail.clicked.connect(lambda: self._send_remote_command("SNAPSHOT_DETAIL"))
        self.btn_clear_alarms.clicked.connect(self._clear_alarms)

        cmd_row.addWidget(self.btn_restart_sim)
        cmd_row.addWidget(self.btn_snapshot_detail)
        cmd_row.addWidget(self.btn_clear_alarms)
        cmd_row.addStretch(1)

        maint_layout.addLayout(cmd_row)

        # Log viewer
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        maint_layout.addWidget(self.log_view)

        # Locked by default
        self._set_maintenance_enabled(False)

        self.tabs.addTab(maint, "Maintenance Console")

        self.setCentralWidget(root)

    def _set_maintenance_enabled(self, enabled: bool):
        self.btn_restart_sim.setEnabled(enabled)
        self.btn_snapshot_detail.setEnabled(enabled)
        self.btn_clear_alarms.setEnabled(enabled)

    def _on_unlock_clicked(self):
        if self.maint_password_input.text() == self.maintenance_password:
            self.maint_status_label.setText("Unlocked")
            self.maint_status_label.setStyleSheet("font-weight:bold; color:#4ade80;")
            self._set_maintenance_enabled(True)
            self.logger.info("[MAINT] Maintenance console unlocked")
        else:
            self.maint_status_label.setText("Locked (wrong password)")
            self.maint_status_label.setStyleSheet("font-weight:bold; color:#ff6b6b;")
            self._set_maintenance_enabled(False)
            self.logger.warning("[MAINT] Wrong maintenance password")

    def _paint_row(self, row: int, bg: str, fg: str):
        bg_brush = QBrush(QColor(bg))
        fg_brush = QBrush(QColor(fg))
        for col in range(self.table.columnCount()):
            item = self.table.item(row, col)
            if item is None:
                continue
            item.setBackground(bg_brush)
            item.setForeground(fg_brush)

    def _set_status_text_color(self, row: int, color_hex: str):
        item = self.table.item(row, 3)
        if item is not None:
            item.setForeground(QBrush(QColor(color_hex)))

    def _append_alarm(self, ts: str, sensor: str, value: float, alarm_type: str):
        r = self.alarm_table.rowCount()
        self.alarm_table.insertRow(r)
        self.alarm_table.setItem(r, 0, QTableWidgetItem(ts))
        self.alarm_table.setItem(r, 1, QTableWidgetItem(sensor))
        self.alarm_table.setItem(r, 2, QTableWidgetItem(f"{value:.3f}"))
        self.alarm_table.setItem(r, 3, QTableWidgetItem(alarm_type))
        self.alarm_table.scrollToBottom()

        if self.alarm_table.rowCount() > 500:
            self.alarm_table.removeRow(0)

        self.api_state.add_alarm({"time": ts, "sensor": sensor, "value": value, "type": alarm_type})
    def _clear_alarms(self):
        self.alarm_table.setRowCount(0)
        self.api_state.clear_alarms()
        self.logger.info("[MAINT] Alarms cleared (dashboard)")

        # also tell simulator so it logs acknowledgement
        if self.client_connected:
            self._send_remote_command("CLEAR_ALARMS")

    def _reset_all_sensors_to_default(self):
        for name in self.sensor_names:
            st = self.sensors[name]
            st.value = None
            st.ts = "-"
            st.status = "N/A"
            st.in_alarm = False
            st.t_buf.clear()
            st.v_buf.clear()

        for row, name in enumerate(self.sensor_names):
            self.table.item(row, 1).setText("-")
            self.table.item(row, 2).setText("-")
            self.table.item(row, 3).setText("N/A")
            self._paint_row(row, bg="#2b2b2b", fg="#ffffff")
            self._set_status_text_color(row, "#ffffff")
            self.plot_curves[name].setData([], [])

        for name in self.sensor_names:
            st = self.sensors[name]
            self.api_state.update_sensor(name, {
                "value": st.value, "ts": st.ts, "status": st.status, "low": st.low, "high": st.high
            })

    def _set_global_status_listening(self):
        self.global_status.setText("System: LISTENING...")
        self.global_status.setStyleSheet("font-size:16px; font-weight:bold; padding:8px; color:#ffffff;")
        self.api_state.set_system_status("LISTENING")

    def _set_global_status_connected_waiting(self):
        self.global_status.setText("System: CONNECTED (Waiting for data...)")
        self.global_status.setStyleSheet("font-size:16px; font-weight:bold; padding:8px; color:#ffffff;")
        self.api_state.set_system_status("CONNECTED")

    def _send_remote_command(self, cmd_name: str, payload: Optional[Dict[str, Any]] = None):
        if not self.client_connected:
            self.logger.warning(f"[CMD] Cannot send '{cmd_name}' (simulator not connected)")
            return

        cmd = {"_type": "cmd", "cmd": cmd_name, "ts": datetime.now().isoformat(timespec="milliseconds")}
        if payload:
            cmd["payload"] = payload
        self.cmd_queue.put(cmd)

    def _drain_log_queue_to_view(self):
        appended = 0
        while True:
            try:
                line = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self.log_view.append(line)
            appended += 1

        if appended:
            doc = self.log_view.document()
            if doc.blockCount() > 1200:
                text = self.log_view.toPlainText().splitlines()[-900:]
                self.log_view.setPlainText("\n".join(text))

    def _on_timer_tick(self):
        now_epoch = datetime.now().timestamp()

        # 1) update log viewer
        self._drain_log_queue_to_view()

        # 2) handle TCP messages (conn/data/log/snapshot)
        while True:
            try:
                msg = self.msg_queue.get_nowait()
            except queue.Empty:
                break

            # connection events
            if msg.get("_type") == "conn":
                state = msg.get("state", "")
                if state == "LISTENING":
                    self.client_connected = False
                    self.has_received_data = False
                    self._set_global_status_listening()
                elif state == "CONNECTED":
                    self.client_connected = True
                    self.has_received_data = False
                    self._set_global_status_connected_waiting()
                elif state == "DISCONNECTED":
                    self.client_connected = False
                    self.has_received_data = False
                    self._reset_all_sensors_to_default()
                    self._set_global_status_listening()
                continue

            # simulator log
            if msg.get("_type") == "log":
                ts = msg.get("ts", "")
                m = msg.get("message", "")
                self.logger.info(f"[SIM] {ts} {m}")
                continue

            # detailed snapshot
            if msg.get("_type") == "snapshot":
                ts = msg.get("ts", "")
                sensors = msg.get("sensors", [])
                lines = [f"[SNAPSHOT] {ts} count={len(sensors)}"]
                for s in sensors:
                    lines.append(
                        f"  - {s.get('sensor')}: value={s.get('value')} status={s.get('status')} "
                        f"(base={s.get('base')}, noise={s.get('noise')})"
                    )
                self.logger.info("\n".join(lines))
                continue

            # sensor data
            if msg.get("_type") != "data":
                continue

            name = msg.get("sensor")
            if name not in self.sensors:
                continue

            self.has_received_data = True

            st = self.sensors[name]
            st.value = float(msg["value"])
            st.ts = str(msg["ts"])
            st.status = str(msg["status"]).upper()

            st.t_buf.append(now_epoch)
            st.v_buf.append(st.value)

            # Alarm logic only if OK
            if st.status == "OK":
                if st.value < st.low:
                    if not st.in_alarm:
                        st.in_alarm = True
                        self._append_alarm(st.ts, st.name, st.value, "LOW_LIMIT")
                elif st.value > st.high:
                    if not st.in_alarm:
                        st.in_alarm = True
                        self._append_alarm(st.ts, st.name, st.value, "HIGH_LIMIT")
                else:
                    st.in_alarm = False
            else:
                st.in_alarm = False

        # if not connected: no repaint
        if not self.client_connected:
            return

        # connected but no data
        if self.client_connected and not self.has_received_data:
            self._set_global_status_connected_waiting()
            return

        # 3) refresh UI + API snapshots
        any_alarm = False
        any_fault = False
        any_ok_data = False

        for row, name in enumerate(self.sensor_names):
            st = self.sensors[name]

            self.table.item(row, 1).setText("-" if st.value is None else f"{st.value:.3f}")
            self.table.item(row, 2).setText(st.ts)
            self.table.item(row, 3).setText(st.status)

            is_alarm_now = (
                st.status == "OK"
                and st.value is not None
                and (st.value < st.low or st.value > st.high)
            )

            if st.status == "OK":
                any_ok_data = True
            if st.status == "FAULT":
                any_fault = True
            if is_alarm_now:
                any_alarm = True

            # highlight alarm row in red
            if is_alarm_now:
                self._paint_row(row, bg="#ff0000", fg="#ffffff")
                self._set_status_text_color(row, "#ffffff")
            elif st.status == "FAULT":
                self._paint_row(row, bg="#2b2b2b", fg="#ffffff")
                self._set_status_text_color(row, "#ffd166")  # yellow status text
            elif st.status == "OK":
                self._paint_row(row, bg="#2b2b2b", fg="#ffffff")
                self._set_status_text_color(row, "#4ade80")  # green status text
            else:
                self._paint_row(row, bg="#2b2b2b", fg="#ffffff")
                self._set_status_text_color(row, "#ffffff")

            # plot update (rolling window)
            if len(st.t_buf) >= 2:
                t0 = st.t_buf[-1]
                xs = [x - t0 for x in st.t_buf]
                ys = list(st.v_buf)
                filtered = [(x, y) for x, y in zip(xs, ys) if x >= -self.plot_window_sec]
                if filtered:
                    fx, fy = zip(*filtered)
                    self.plot_curves[name].setData(fx, fy)
                    self.plot_widgets[name].setXRange(-self.plot_window_sec, 0, padding=0.01)

            self.api_state.update_sensor(name, {
                "value": st.value, "ts": st.ts, "status": st.status, "low": st.low, "high": st.high
            })

        # global status
        if any_alarm:
            self.global_status.setText("System: ALARM")
            self.global_status.setStyleSheet("font-size:16px; font-weight:bold; padding:8px; color:#ff6b6b;")
            self.api_state.set_system_status("ALARM")
        elif any_fault:
            self.global_status.setText("System: WARNING (Faulty Sensor)")
            self.global_status.setStyleSheet("font-size:16px; font-weight:bold; padding:8px; color:#ffd166;")
            self.api_state.set_system_status("WARNING")
        elif any_ok_data:
            self.global_status.setText("System: OK")
            self.global_status.setStyleSheet("font-size:16px; font-weight:bold; padding:8px; color:#4ade80;")
            self.api_state.set_system_status("OK")
        else:
            self._set_global_status_connected_waiting()


def main():
    config_path = "configs/config.yaml"
    app = QApplication(sys.argv)
    win = MainWindow(config_path)
    win.resize(1300, 860)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
