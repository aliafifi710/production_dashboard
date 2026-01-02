# Production Line Sensor Dashboard (Python)  
Real-time desktop dashboard that monitors 5 sensors concurrently, triggers alarms, and provides remote API access.  
Includes **Bonus A** (Maintenance Console commands) and **Bonus B** (Desktop notifications).

---

## Features
### Mandatory
- Monitor **5 sensors** concurrently (simulator → dashboard over TCP).
- Real-time GUI updates (≥ 2 updates/sec).
- Thread-safe architecture (worker thread for socket I/O, GUI updated via queue).
- Per-sensor **LOW/HIGH** limits with alarm log.
- Alarm highlighting (row turns **red** when out of limits).
- Remote API for current sensors + alarms.

### Bonus
- **Maintenance Console** (password protected): Restart simulator, clear alarms, request snapshot.
- **Desktop notifications** (system tray popup) on alarms.

---

## Project Structure
```text
production_dashboard/
├─ configs/
│ └─ config.yaml
├─ simulator/
│ └─ sensor_simulator.py
├─ src/
│ ├─ dashboard_app.py
│ ├─ api_server.py
│ ├─ core.py
│ └─ init.py
├─ tests/
│ ├─ test_parsing.py
│ ├─ test_alarm_logic.py
│ └─ test_api.py
└─ requirements.txt
```
## Setup Steps

### Clone & Create Virtual Environment

### 1) Clone the repository
```powershell
git clone https://github.com/aliafifi710/production_dashboard.git
cd <REPO_FOLDER_NAME>
```
### 2) Create and activate virtual environment
**Windows (PowerShell):**
```powershell
python -m venv .venv
.\.venv\Scripts\activate
```

**Windows (CMD):**
```
python -m venv .venv
.\.venv\Scripts\activate.bat
```

**Linux / macOS:**
```
python3 -m venv .venv
source .venv/bin/activate
```
### 3) Install dependencies
```powershell
pip install -r requirements.txt
```
> If FastAPI tests fail due to a missing dependency, install:
```powershell
pip install httpx
```
---

## Running Instructions

### A) Start the Dashboard (Server)

In one terminal (with venv activated):

```powershell
python .\src\dashboard_app.py
```

**What happens:**
- Dashboard starts listening on TCP (default `127.0.0.1:9000`)
- GUI opens and waits for sensor simulator
- FastAPI starts (default `127.0.0.1:8000`)


### B) Start the Sensor Simulator (Client)

Open Terminal 2 (with venv activated) and run:

```powershell
python .\simulator\sensor_simulator.py
```
**You should see:**
- Simulator connects to dashboard
- Sensor values start updating in GUI
- Alarms appear when thresholds exceeded
- alarm log tab holds threshold for each sensor
- maintenance consule tab for sending commands with pass configured in config.yaml file

## Protocol Description (TCP)

### Overview
- **Protocol:** TCP socket  
- **Direction:** Simulator (**client**) → Dashboard (**server**)  
- **Dashboard bind:** `127.0.0.1:9000` (configurable)  
- **Message framing:** Newline-delimited JSON (`\n`)  

### 1) Sensor Data Message (Simulator → Dashboard)

Each line is **one JSON object**:
```json
{
  "sensor": "Temp_C",
  "value": 25.3,
  "ts": "2026-01-02T13:12:30.123",
  "status": "OK"
}
```

### 2) Commands (Dashboard → Simulator) (Bonus A)

Dashboard sends newline-delimited JSON commands, Supported commands:

- RESTART_SIM : restart simulator
- CLEAR_ALARMS : acknowledge (simulator side optional)
- SNAPSHOT_DETAIL : simulator returns a snapshot response

## API Documentation (Remote Access)

Base URL (default):

```
http://127.0.0.1:8000
```

 ### 1) Current Sensors Snapshot

 GET ```/api/sensors```

 Response:
 ```json
{
  "system_status": "OK",
  "sensors": [
    {
      "name": "Temp_C",
      "value": 25.3,
      "ts": "2026-01-02T13:12:30.123",
      "status": "OK",
      "low": 10.0,
      "high": 90.0
    }
  ],
  "alarms_count": 2
}
```


 ### 2) alarm log

 GET ```/api/alarms```

 Response:
 ```json
{
  "count": 2,
  "alarms": [
    {"time":"...","sensor":"Temp_C","value":95.1,"type":"HIGH_LIMIT"}
  ]
}
```

## Unit Tests

Mandatory tests included:

- Sensor parsing
- Alarm logic
- API output
  
Run:
```pytest```
