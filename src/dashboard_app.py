import sys
import json
import socket
import queue
import threading
from dataclasses import dataclass, field
from datetime import datetime
from collections import deque
from typing import Dict, Deque, Optional

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
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QHeaderView,
    QSplitter,
)

import pyqtgraph as pg


@dataclass
class SensorState:
    name: str
    low: float
    high: float
    value: Optional[float] = None
    ts: str = "-"
    status: str = "N/A"     # OK / FAULT / N/A
    in_alarm: bool = False
    t_buf: Deque[float] = field(default_factory=lambda: deque(maxlen=600))
    v_buf: Deque[float] = field(default_factory=lambda: deque(maxlen=600))


class SensorTCPWorker(threading.Thread):
    """Background thread reads TCP stream and pushes messages into a queue (no GUI updates)."""
    def __init__(self, host: str, port: int, out_queue: queue.Queue, stop_event: threading.Event):
        super().__init__(daemon=True)
        self.host = host
        self.port = port
        self.out_queue = out_queue
        self.stop_event = stop_event

    def run(self):
        while not self.stop_event.is_set():
            try:
                with socket.create_connection((self.host, self.port), timeout=3) as sock:
                    sock.settimeout(1.0)
                    f = sock.makefile("r", encoding="utf-8", newline="\n")

                    for line in f:
                        if self.stop_event.is_set():
                            break
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            msg = json.loads(line)
                            if {"sensor", "value", "ts", "status"}.issubset(msg.keys()):
                                msg["status"] = str(msg["status"]).upper()
                                if msg["status"] not in ("OK", "FAULT"):
                                    msg["status"] = "FAULT"
                                self.out_queue.put(msg)
                        except json.JSONDecodeError:
                            continue

            except (ConnectionRefusedError, TimeoutError, OSError):
                self.stop_event.wait(0.5)


class MainWindow(QMainWindow):
    def __init__(self, config_path: str):
        super().__init__()
        self.setWindowTitle("Production Line Sensor Dashboard")

        cfg = self._load_config(config_path)
        self.host = cfg["simulator"]["host"]
        self.port = int(cfg["simulator"]["port"])
        self.update_hz = float(cfg["ui"]["update_hz"])
        self.plot_window_sec = float(cfg["ui"]["plot_window_sec"])

        # ✅ Force stable order from config so all 5 always exist in UI
        self.sensor_names = [s["name"] for s in cfg["sensors"]]

        self.sensors: Dict[str, SensorState] = {}
        for s in cfg["sensors"]:
            name = s["name"]
            if name in self.sensors:
                raise ValueError(f"Duplicate sensor name in config: {name}")
            self.sensors[name] = SensorState(name=name, low=float(s["low"]), high=float(s["high"]))

        self.msg_queue: queue.Queue = queue.Queue()
        self.stop_event = threading.Event()

        self.worker = SensorTCPWorker(self.host, self.port, self.msg_queue, self.stop_event)
        self.worker.start()

        self._build_ui()
        self._apply_dark_theme()

        interval_ms = int(1000 / max(self.update_hz, 2.0))
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._on_timer_tick)
        self.timer.start(interval_ms)

    def closeEvent(self, event):
        self.stop_event.set()
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
        """)

    def _build_ui(self):
        root = QWidget()
        root_layout = QVBoxLayout(root)

        self.tabs = QTabWidget()
        root_layout.addWidget(self.tabs)

        # -------- Dashboard tab --------
        dash = QWidget()
        dash_layout = QVBoxLayout(dash)

        self.global_status = QLabel("System: CONNECTING...")
        self.global_status.setAlignment(Qt.AlignCenter)
        self.global_status.setStyleSheet("font-size: 16px; font-weight: bold; padding: 8px;")
        dash_layout.addWidget(self.global_status)

        # Split: table left, plots right (so all 5 plots visible without scrolling)
        splitter = QSplitter(Qt.Horizontal)

        # Table
        self.table = QTableWidget(len(self.sensor_names), 4)
        self.table.setHorizontalHeaderLabels(["Sensor", "Latest Value", "Timestamp", "Status"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSortingEnabled(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)

        for row, name in enumerate(self.sensor_names):
            sensor_item = QTableWidgetItem(name)
            value_item = QTableWidgetItem("-")
            ts_item = QTableWidgetItem("-")
            status_item = QTableWidgetItem("N/A")
            status_item.setTextAlignment(Qt.AlignCenter)

            self.table.setItem(row, 0, sensor_item)
            self.table.setItem(row, 1, value_item)
            self.table.setItem(row, 2, ts_item)
            self.table.setItem(row, 3, status_item)

            self._paint_row(row, bg="#2b2b2b", fg="#ffffff")

        splitter.addWidget(self.table)

        # Plots (3x2 grid, only 5 used)
        plots_widget = QWidget()
        plots_grid = QGridLayout(plots_widget)
        plots_grid.setHorizontalSpacing(10)
        plots_grid.setVerticalSpacing(10)

        self.plot_widgets: Dict[str, pg.PlotWidget] = {}
        self.plot_curves: Dict[str, pg.PlotDataItem] = {}

        pg.setConfigOptions(antialias=True)

        for i, name in enumerate(self.sensor_names):
            pw = pg.PlotWidget()
            pw.setMinimumHeight(150)  # fits in 3 rows on most screens
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

        # Give the plots more width than the table by default
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

        self.setCentralWidget(root)

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

    def _on_timer_tick(self):
        now_epoch = datetime.now().timestamp()

        # Drain messages
        while True:
            try:
                msg = self.msg_queue.get_nowait()
            except queue.Empty:
                break

            name = msg["sensor"]
            if name not in self.sensors:
                continue  # name mismatch => fix config/simulator names

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

        # Refresh UI
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

            # ✅ Requirement: "Highlight the sensor row in red"
            # We use a clear red background for the whole row.
            if is_alarm_now:
                self._paint_row(row, bg="#ff0000", fg="#ffffff")  # RED row
                self._set_status_text_color(row, "#ffffff")
            elif st.status == "FAULT":
                self._paint_row(row, bg="#2b2b2b", fg="#ffffff")
                self._set_status_text_color(row, "#ffd166")  # warning
            elif st.status == "OK":
                self._paint_row(row, bg="#2b2b2b", fg="#ffffff")
                self._set_status_text_color(row, "#4ade80")  # ok
            else:
                self._paint_row(row, bg="#2b2b2b", fg="#ffffff")
                self._set_status_text_color(row, "#ffffff")

            # Plot update (rolling window)
            if len(st.t_buf) >= 2:
                t0 = st.t_buf[-1]
                xs = [x - t0 for x in st.t_buf]
                ys = list(st.v_buf)
                filtered = [(x, y) for x, y in zip(xs, ys) if x >= -self.plot_window_sec]
                if filtered:
                    fx, fy = zip(*filtered)
                    self.plot_curves[name].setData(fx, fy)
                    self.plot_widgets[name].setXRange(-self.plot_window_sec, 0, padding=0.01)

        # Global status
        if any_alarm:
            self.global_status.setText("System: ALARM")
            self.global_status.setStyleSheet("font-size:16px; font-weight:bold; padding:8px; color:#ff6b6b;")
        elif any_fault:
            self.global_status.setText("System: WARNING (Faulty Sensor)")
            self.global_status.setStyleSheet("font-size:16px; font-weight:bold; padding:8px; color:#ffd166;")
        elif any_ok_data:
            self.global_status.setText("System: OK")
            self.global_status.setStyleSheet("font-size:16px; font-weight:bold; padding:8px; color:#4ade80;")
        else:
            self.global_status.setText("System: CONNECTING...")
            self.global_status.setStyleSheet("font-size:16px; font-weight:bold; padding:8px; color:#ffffff;")


def main():
    config_path = "configs/config.yaml"
    app = QApplication(sys.argv)
    win = MainWindow(config_path)
    # Wide enough to show table + 2 plot columns without scrolling
    win.resize(1300, 820)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
