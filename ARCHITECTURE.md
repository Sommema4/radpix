# RadPix – Architecture & Code Navigation Guide

## Table of Contents
1. [What the system does](#1-what-the-system-does)
2. [Component map](#2-component-map)
3. [Data flow](#3-data-flow)
4. [Module reference](#4-module-reference)
   - [main.py – TimepixController](#mainpy--timepixcontroller)
   - [device_manager.py – DeviceManager](#device_managerpy--devicemanager)
   - [data_processor.py – DataProcessor & ClogParser](#data_processorpy--dataprocessor--clogparser)
   - [config_manager.py – ConfigManager](#config_managerpy--configmanager)
   - [api_server.py – TimepixAPI](#api_serverpy--timepixapi)
5. [Configuration reference](#5-configuration-reference)
6. [Startup sequences](#6-startup-sequences)
7. [Known edge cases and bugs](#7-known-edge-cases-and-bugs)

---

## 1. What the system does

RadPix is a Python control layer on top of **Medipix/Timepix detector hardware** accessed through the **pypixet** C-extension (PIXet SDK). Its responsibilities are:

| Concern | Who handles it |
|---|---|
| Hardware enumeration and device setup | `DeviceManager` |
| Continuous frame acquisition per device | `DeviceManager._acquisition_loop` (background thread) |
| USB disconnect / reconnect recovery | `DeviceManager._reconnect_monitor_loop` (background thread) |
| CLOG file parsing and particle counting | `DataProcessor` / `ClogParser` |
| REST API (Flask) | `TimepixAPI` |
| Config loading (JSON) | `ConfigManager` |
| Orchestration | `TimepixController` |

It can be run in two ways:
- **Standalone** – `python src/main.py` starts acquisition immediately and prints status to stdout.
- **API mode** – `python src/api_server.py` exposes a REST API on port 5000 (configurable) while measurement runs underneath.

---

## 2. Component map

```
┌───────────────────────────────────────────────────────────────────┐
│  Entry points                                                     │
│  main.py::main()          api_server.py::main()                   │
└──────────────┬───────────────────────┬────────────────────────────┘
               │                       │
               ▼                       ▼
       ┌──────────────────────────────────────┐
       │         TimepixController             │  main.py
       │  - config_manager                     │
       │  - device_manager                     │
       │  - data_processor                     │
       │  - callbacks: frame / state / error   │
       └───┬───────────────┬──────────────────┘
           │               │
           ▼               ▼
  ┌─────────────┐   ┌───────────────┐
  │ConfigManager│   │ DeviceManager │  device_manager.py
  │  (JSON)     │   │               │
  │settings.json│   │  ManagedDevice│◄── wraps pypixet device
  │devices_     │   │  DeviceStatus │
  │ config.json │   │               │
  └─────────────┘   │ threads:      │
                    │  _acquisition │
                    │  _reconnect   │
                    └───────┬───────┘
                            │ frame_acquired callback
                            ▼
                    ┌───────────────┐
                    │ DataProcessor │  data_processor.py
                    │  ClogParser   │
                    └───────────────┘

  ┌───────────────────────────────┐
  │ TimepixAPI (Flask)            │  api_server.py
  │  holds ref to controller      │
  │  exposes REST routes          │
  └───────────────────────────────┘
```

**Thread map at runtime:**

| Thread | Purpose |
|---|---|
| Main thread | Flask dev server (api mode) or `while True` sleep loop (standalone) |
| `_acquisition_loop` | Continuously calls `doSimpleAcquisition` on each device |
| `_reconnect_monitor_loop` | Polls device connectivity, attempts reconnect |

---

## 3. Data flow

### Acquisition → file
```
pypixet.pixet.devices()
    └─► device.doSimpleAcquisition(count, frame_time, file_type, filename)
            └─► writes  data/<session>/device<N>_frame<XXXXXX>.clog
```

### Frame acquired → particle count update
```
_acquisition_loop emits "frame_acquired" callback
    └─► TimepixController._on_frame_acquired(data)
            └─► DataProcessor.get_real_time_stats(filename)
                    └─► ClogParser.parse_file(filepath)   ← re-parses whole file!
                            └─► managed_dev.update_particle_count(particles)
```

### API → measurement control
```
POST /measurement/start  →  TimepixController.start_measurement()
                                └─► DeviceManager.start_acquisition()
                                        └─► starts _acquisition_loop thread

POST /measurement/stop   →  TimepixController.stop_measurement()
                                └─► DeviceManager.stop_acquisition()
                                        └─► sets is_measuring=False
                                        └─► calls device.abortOperation() on each device
                                        └─► joins thread (5s timeout)
```

### API → data retrieval
```
GET /data/sessions          →  lists session_* dirs under device[0].data_directory
GET /data/sessions/<name>   →  DataProcessor.process_session_directory()
GET /data/latest            →  DataProcessor.get_latest_session() + process
```

---

## 4. Module reference

---

### `main.py` – `TimepixController`

Central orchestrator. Owns the other managers and wires them together via callbacks.

#### `__init__(config_dir)`
- Creates logging handlers (console + rotating timestamped file under `logs/`).
- Calls `_load_config()` → creates `ConfigManager`.
- Does **not** touch hardware yet.

#### `initialize() → bool`
1. Imports `pypixet` (fails cleanly if DLL not found).
2. Creates `DataProcessor` using the `data_directory` of the **first enabled device** in config.
3. Creates `DeviceManager` and calls `device_manager.initialize()`.
4. Registers three callbacks: `frame_acquired`, `state_changed`, `error`.

> **Edge case**: If no devices are configured, `device_configs` is empty and `data_dir` defaults to `"data"`. The DataProcessor will point at `data/` relative to the working directory, which may or may not be the project root depending on how the script is invoked.

#### `_on_frame_acquired(data)`
- Receives `{device_id, frame_number, filename}`.
- Only processes if `filename` ends with `.clog`.
- Calls `DataProcessor.get_real_time_stats(filename)` — this **re-parses the entire file** on every frame. For long sessions this becomes progressively slower.

#### `start_measurement(frame_time, bias_voltages) → bool`
- Thin delegation: passes both arguments straight to `DeviceManager.start_acquisition()`.
- `frame_time=None` means each device will use its own `frame_time` config value.

#### `shutdown()`
- Calls `device_manager.shutdown()` → stops threads, aborts operations.
- Does **not** flush or finalise any open data files (pypixet handles that internally).

---

### `device_manager.py` – `DeviceManager`

Manages hardware lifecycle, acquisition loop and reconnection logic.

#### `initialize() → bool`
1. Calls `pypixet.start()` then retrieves `pypixet.pixet` global.
2. Calls `_discover_and_setup_devices()`.
3. Calls `_start_reconnect_monitor()`.

#### `_discover_and_setup_devices()`
- Gets `pixet.devices()`, filters out `FileDevice 0` (a virtual device PIXet always creates).
- Matches physical devices to config entries **by position** (index 0 → config[0], etc.).
- **Does not match by serial number** even though serials are available in both the config and on the device. If devices enumerate in a different order after a reboot or partial reconnect, they will silently receive the wrong threshold config and bias voltage. See [edge cases](#7-known-edge-cases-and-bugs).

#### `_setup_device(device, device_id, config) → bool`
1. Creates `ManagedDevice` wrapper.
2. Loads XML threshold/config file via `device.loadConfigFromFile(config_file)` → falls back to `device.loadFactoryConfig()` on failure.
3. Sets operation mode via `device.setOperationMode(mode_constant)`.
4. Sets bias voltage via `device.setBias(default_bias)`.

#### `_reconnect_monitor_loop()`
Runs in a daemon thread. Every `health_check_interval_sec` (default 5 s):
- Calls `managed_dev.is_connected()` on each device.
- If disconnected: sets state to `DISCONNECTED`, emits `error` callback.
- Computes an interval before the next reconnect attempt:
  - **Aggressive mode** (both devices offline): every 10 s.
  - **Fast phase** (attempts < `max_fast_attempts`): every `initial_interval_sec` (30 s).
  - **Slow phase** (after max fast attempts): every `slow_interval_sec` (300 s).
- Calls `_attempt_reconnect()` when the interval has elapsed.

> **Edge case**: `aggressive_mode_both_offline` only triggers when **all** managed devices are offline, not just some.

#### `_attempt_reconnect(managed_dev)`
1. Calls `device.reconnect()`.
2. Waits 0.5 s then checks `is_connected()`.
3. On success: re-applies operation mode and bias — but **does not reload the XML config file**. The device's per-pixel thresholds won't be restored after reconnect.
4. On failure: sets state to `FAILED`. The reconnect monitor will keep trying.

#### `start_acquisition(frame_time, bias_voltages) → bool`
- Validates `is_initialized` and `is_measuring`.
- Does **not** substitute a default for `frame_time` — passes `None` through to `_acquisition_loop`.
- Optionally applies per-device bias overrides before starting.

#### `_acquisition_loop(frame_time)`
Runs in a daemon thread. Per-device parameters are resolved once at the start:

| Parameter | Source (in priority order) |
|---|---|
| `frame_time` | call argument → device config `frame_time` → `settings.json acquisition.default_frame_time` → 1.0 |
| `file_format` | device config `file_format` → `"clog"` |
| `save_data` | device config `save_data` → `True` |
| `data_directory` | device config `data_directory` → `"data"` |

A single `session_YYYYMMDD_HHMMSS` directory is created per device under that device's `data_directory`. Files are named `device<N>_frame<XXXXXX>.<ext>`.

The inner loop:
1. Iterates `managed_devices` every cycle.
2. Skips devices that are not connected, or not in `CONNECTED` state.
3. Sets state → `MEASURING`, calls `doSimpleAcquisition`, sets state → `CONNECTED`.
4. Emits `frame_acquired` callback.

> **Edge case**: State is set to `MEASURING` before acquisition and back to `CONNECTED` after, on every single frame. The reconnect monitor runs concurrently and checks the state. If the reconnect thread checks between the two state updates, it will see `MEASURING` and correctly leave the device alone. This is safe but means a device stuck mid-frame will never be flagged as disconnected until acquisition finishes or throws.

#### `stop_acquisition()`
- Sets `is_measuring = False` (the loop's exit condition).
- Calls `device.abortOperation()` on every device.
- Joins the measurement thread with a 5-second timeout. If the thread does not exit within 5 s (e.g. a very long frame), it will be abandoned (it's a daemon thread, so it dies on process exit).

---

### `data_processor.py` – `DataProcessor` & `ClogParser`

#### `ClogParser` — CLOG file format

CLOG (Cluster LOG) is a text format produced by PIXet. Expected structure:
```
Frame 0 (0.000, 1.000 s)
[12, 34, 5.6, 1234.5] [89, 10, 2.1]
Frame 1 (1.000, 1.000 s)
...
```
Each cluster entry is `[x, y, energy]` or `[x, y, energy, toa]`.

#### `ClogParser.parse_file(filepath) → List[FrameData]`
1. Reads the whole file into memory.
2. Splits on the string `"Frame "`.
3. Calls `_parse_frame_section()` on each part after the first.

> **Bug**: `_parse_frame_section` applies `FRAME_PATTERN` (which starts with `Frame\s+`) to the first line of each section. After splitting on `"Frame "`, each section begins with the **frame number**, not the word `"Frame"`. The regex will never match, so `_parse_frame_section` always returns `None` and **no frames are ever parsed**. `parse_file` will always return `[]`. This is the most critical known bug — particle counts will always be zero and session data processing will always fail. See [edge cases](#7-known-edge-cases-and-bugs) for the fix.

#### `ClogParser._parse_frame_section(section) → Optional[FrameData]`
- Parses the frame header line with `FRAME_PATTERN`.
- Scans remaining lines for cluster entries using `CLUSTER_PATTERN`.

#### `DataProcessor.process_clog_file(filepath) → dict`
Returns `{success, frame_count, total_particles, avg_particles_per_frame, total_energy, frames[]}`.

#### `DataProcessor.process_session_directory(session_dir) → dict`
- Globs for `*.clog` files.
- Aggregates `process_clog_file` results.
- Returns cross-file totals.

#### `DataProcessor.get_latest_session() → Optional[str]`
- Finds subdirectories starting with `session_` under `self.data_directory`.
- Returns the one with the highest `st_mtime`.
- Only inspects `self.data_directory` which is set from `device_configs[0]`. Sessions for other devices are invisible to this method.

#### `DataProcessor.monitor_file(filepath, callback)`
- Polls file size every 100 ms and re-parses from scratch when it grows.
- **Blocks the calling thread indefinitely** until the file disappears.
- Not currently called anywhere in the codebase.

#### `DataProcessor.get_real_time_stats(filepath) → dict`
- Parses the full CLOG file and returns stats on the last frame.
- Called on **every frame acquired** from `TimepixController._on_frame_acquired`. Cost grows linearly with the number of frames in the file.

#### `DataProcessor.export_statistics_csv(session_dir, output_file)`
- Writes a CSV summary of all CLOG files in a session directory.
- Not wired to any API endpoint or CLI flag — utility function only.

---

### `config_manager.py` – `ConfigManager`

Loads and provides access to two JSON config files. No hardware interaction.

#### `_load_configurations()`
Calls `_load_devices_config()` then `_load_settings()`. Raises on any error — failure here aborts startup.

#### `get_device_configs() → List[dict]`
Returns only devices with `"enabled": true` (defaults to true if the key is missing).

#### `get_setting(*keys) → Any`
Traverses `settings` dict by key sequence. Returns `None` on any missing key (safe, no KeyError).

#### `update_setting(value, *keys)`
In-memory only — call `save_settings()` to persist.

#### `save_settings()`
Serialises `self.settings` back to `settings.json`. Note: since per-device acquisition fields now live in `devices_config.json`, those are **not** saved by this method.

---

### `api_server.py` – `TimepixAPI`

Flask application. All routes are defined in `_register_routes()`.

#### Route table

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Returns `{status, initialized}` |
| GET | `/status` | Full system status from `controller.get_status()` |
| GET | `/devices` | All device statuses |
| GET | `/devices/<id>` | Single device status |
| POST | `/measurement/start` | Body: `{frame_time?, bias_voltages?}` |
| POST | `/measurement/stop` | Stops acquisition |
| GET | `/settings` | Returns acquisition + reconnection + monitoring settings |
| PUT | `/settings` | Updates in-memory settings; add `?save=true` to persist |
| PUT | `/devices/<id>/bias` | Body: `{bias: float}` – sets bias immediately |
| GET | `/data/sessions` | Lists session directories for device[0] |
| GET | `/data/sessions/<name>` | Processes and returns data for a named session |
| GET | `/data/latest` | Processes and returns data for the most recent session |

#### `run(debug)`
Calls `app.run(threaded=True)` — Flask runs in threading mode, meaning each HTTP request gets its own thread. No `asyncio` involved.

> **Edge case**: The Flask development server is **not** production-ready. For deployment, use a WSGI server (gunicorn, waitress).

---

## 5. Configuration reference

### `config/settings.json`

```jsonc
{
  "acquisition": {
    "default_frame_time": 1.0   // fallback if device config has no frame_time
  },
  "reconnection": {
    "enabled": true,
    "initial_interval_sec": 30, // fast-phase interval between reconnect attempts
    "max_fast_attempts": 10,    // switch to slow phase after this many attempts
    "slow_interval_sec": 300,   // slow-phase interval
    "aggressive_mode_both_offline": true  // 10s interval when ALL devices offline
  },
  "logging": {
    "level": "INFO",
    "log_directory": "logs",
    "log_to_console": true,
    "log_to_file": true
  },
  "api": {
    "enabled": true,
    "host": "0.0.0.0",
    "port": 5000,
    "cors_enabled": true
  },
  "monitoring": {
    "health_check_interval_sec": 5,
    "statistics_update_interval_sec": 1  // not currently used in code
  }
}
```

**Note**: `logging.level`, `log_directory`, `log_to_console`, `log_to_file` are loaded from this file by `ConfigManager` but are **not actually used** by `TimepixController._setup_logging()`. Logging is hardcoded to DEBUG (file) / INFO (console) in `main.py`.

### `config/devices_config.json`

Each entry in `devices[]`:

```jsonc
{
  "serial": "I08-W0060",            // for identification; matching is by index, not serial
  "name": "TPX_Device_1",           // display name, used in logs
  "type": "TPX3",                   // used to look up default operation mode
  "config_file": "config/device_xmls/MiniPIX-I08-W0060.xml",
  "default_bias": 80.0,             // volts, applied at setup and after reconnect
  "operation_mode": "PX_TPX3_OPM_TOATOT",  // pypixet constant name
  "enabled": true,
  // Per-device acquisition settings (moved from global settings):
  "file_format": "clog",            // clog | png | txt | none
  "save_data": true,
  "data_directory": "data",
  "frame_mode": "frame",            // stored but not yet read by acquisition loop
  "frame_time": 1.0                 // seconds per frame
}
```

---

## 6. Startup sequences

### Standalone (`python src/main.py`)

```
TimepixController.__init__
  └─ _setup_logging()
  └─ ConfigManager()            ← reads both JSON files
     └─ raise on missing files

TimepixController.initialize()
  └─ import pypixet             ← needs pypixet.pyd + DLLs on PATH
  └─ DataProcessor(data_dir)
  └─ DeviceManager.initialize()
     └─ pypixet.start()
     └─ _discover_and_setup_devices()
        └─ pixet.devices()
        └─ _setup_device() × N
           └─ loadConfigFromFile or loadFactoryConfig
           └─ setOperationMode
           └─ setBias
     └─ _start_reconnect_monitor()  ← daemon thread starts

TimepixController.start_measurement()    [unless --no-auto-start]
  └─ DeviceManager.start_acquisition()
     └─ _acquisition_loop starts  ← daemon thread starts

while True: sleep(1)   ← main thread blocks here
  └─ prints status every iteration
```

### API mode (`python src/api_server.py`)

Same as above through `initialize()`, then:
```
TimepixAPI(controller, host, port)
  └─ Flask app created
  └─ CORS enabled if configured
  └─ routes registered

api.run()   ← blocks; Flask handles HTTP in threads
```
The API mode does **not** auto-start measurement. A `POST /measurement/start` is required.

---

## 7. Known edge cases and bugs

All issues below have been fixed in the current codebase.

---

### ~~Bug: `ClogParser` never parses any frames~~ — FIXED

**File**: `src/data_processor.py`

`FRAME_PATTERN` previously required the literal text `"Frame"` at the start, but `parse_file` splits the content on `"Frame "` so each section starts with just the frame number. The pattern has been fixed to:
```python
FRAME_PATTERN = re.compile(r'(\d+)\s+\(([0-9.]+),\s+([0-9.]+)\s+s\)')
```

---

### ~~Bug: Device matching by index, not serial~~ — FIXED

**File**: `src/device_manager.py`

`_discover_and_setup_devices` now reads the device serial via `pixet.DevInfo()` and matches against `serial` fields in `devices_config.json`. If no serial match is found it falls back to assigning the first unclaimed config entry and logs a warning.

---

### ~~Edge case: Reconnect does not reload threshold XML~~ — FIXED

**File**: `src/device_manager.py`, `_attempt_reconnect()`

After a successful reconnect, `loadConfigFromFile` is now called before re-applying operation mode and bias. Falls back to `loadFactoryConfig()` if the file load fails.

---

### ~~Edge case: `data_directory` only reads from device[0]~~ — FIXED

**Files**: `src/main.py`, `src/api_server.py`, `src/data_processor.py`

`DataProcessor` now accepts a list of data directories and `get_latest_session()` searches all of them. `main.py` passes all unique `data_directory` values from the device config. The `/data/sessions` API endpoint merges session names from all directories; `/data/sessions/<name>` searches all directories to resolve the full path.

---

### ~~Edge case: `get_real_time_stats` cost grows with session length~~ — FIXED

**File**: `src/data_processor.py`

`DataProcessor` now maintains a `_file_cache` dict keyed by file path. The cached entry stores the file size at last parse and the resulting `FrameData` list. `_parse_cached()` skips re-parsing if the file size is unchanged, making the per-frame callback O(1) for files that haven't grown.

---

### ~~Edge case: `frame_mode` is stored but never read~~ — FIXED (partial)

**File**: `src/device_manager.py`, `_acquisition_loop()`

The acquisition loop now reads `frame_mode` from device config and logs a warning if it is set to anything other than `"frame"`. Only `"frame"` mode (via `doSimpleAcquisition`) is currently implemented; support for other modes (trigger-based, continuous) would require additional PIXet API calls.

---

### ~~Edge case: `logging` settings in `settings.json` are ignored~~ — FIXED

**File**: `src/main.py`

`_setup_logging()` does a minimal bootstrap (console INFO, file DEBUG) before config is available. A new `_reconfigure_logging()` method is called from `_load_config()` once `ConfigManager` is ready; it reads `settings.logging.level` and applies it to the console handler.

---

### ~~Edge case: `stop_acquisition` join timeout~~ — FIXED (warning added)

**File**: `src/device_manager.py`, `stop_acquisition()`

After the 5-second join, the code now checks `thread.is_alive()` and logs a warning if the thread did not stop in time, making the situation visible in the log rather than silently continuing.

---

### ~~Edge case: `save_settings()` does not persist device-level fields~~ — FIXED

**File**: `src/config_manager.py`

A new `save_devices_config()` method serialises `self.devices_config` back to `devices_config.json`. Per-device fields changed at runtime (`file_format`, `save_data`, `data_directory`, `frame_mode`, `frame_time`) can now be persisted by calling this method. The PUT `/settings` API endpoint only updates `settings.json`; a separate API endpoint would be needed to expose device config persistence.

**File**: `src/data_processor.py`

`parse_file` splits the file content on `"Frame "` then calls `_parse_frame_section` on each token. After splitting, each section starts with the frame number, e.g. `"0 (0.000, 1.000 s)\n..."`. But `FRAME_PATTERN` requires the literal text `"Frame"` at the start of the string, so `re.match` always returns `None` and every call to `_parse_frame_section` returns `None`.

**Effect**: Every `parse_file` call returns `[]`. Particle counts are always 0. `process_session_directory` always returns `{"success": False, "error": "No CLOG files found"}` whenever there are no frames — or incorrect zero-particle results when files exist but parse as empty.

**Fix**: Change `FRAME_PATTERN` to not require the leading `Frame` text after splitting:
```python
FRAME_PATTERN = re.compile(r'(\d+)\s+\(([0-9.]+),\s+([0-9.]+)\s+s\)')
```
And update `_parse_frame_section` to use groups 1–3 instead of 1–3 (index shift stays the same after this change).

---

### Bug: Device matching by index, not serial

**File**: `src/device_manager.py`, `_discover_and_setup_devices()`

Physical devices are matched to config entries by enumeration order. If the OS USB stack enumerates devices in a different order on startup or after reconnect, device 0's config (threshold file, bias, operation mode) will be applied to the wrong physical detector.

**Fix**: Match by serial number:
```python
serial = device.fullName()  # or a dedicated serial API call
device_config = self.config_manager.get_device_config_by_serial(serial)
```

---

### Edge case: Reconnect does not reload threshold XML

**File**: `src/device_manager.py`, `_attempt_reconnect()`

After a successful reconnect, operation mode and bias are re-applied, but `loadConfigFromFile` is not called. The per-pixel threshold configuration (stored in the XML) will be at factory defaults until the device is next fully re-initialised.

---

### Edge case: `data_directory` only reads from device[0]

**Files**: `src/main.py`, `src/api_server.py`

`DataProcessor` is initialised with `device_configs[0]["data_directory"]`. The API endpoints `/data/sessions` and `/data/sessions/<name>` also use device[0]'s directory. If devices write to different directories, all data from device 1+ is invisible to the REST API.

---

### Edge case: `get_real_time_stats` cost grows with session length

**File**: `src/data_processor.py`, `get_real_time_stats()`

Called on every frame acquired. Re-reads and re-parses the entire CLOG file from disk each time. A 1-hour session at 1 fps produces a ~3600-frame file; parsing that file on frame 3600 takes ~3600× as long as on frame 1.

**Mitigation**: Cache last parsed frame count; on next call, only parse the delta (new bytes appended since last read). Alternatively, use the existing `monitor_file` pattern in a background thread rather than in the callback.

---

### Edge case: `frame_mode` is stored but never read

**File**: `src/device_manager.py`, `_acquisition_loop()`

`frame_mode` was moved from global settings into per-device config, but the acquisition loop does not read it. PIXet supports different acquisition modes (frame-based, time-based, trigger-based). Currently the system always uses the frame-based `doSimpleAcquisition` regardless of the `frame_mode` value.

---

### Edge case: `logging` settings in `settings.json` are ignored

**File**: `src/main.py`, `_setup_logging()`

`settings.json` has a `logging` section (`level`, `log_to_console`, `log_to_file`, etc.) but `_setup_logging()` hardcodes its configuration. The `ConfigManager` is not yet initialised when `_setup_logging()` is called (it's called from `__init__` before `_load_config`).

---

### Edge case: `stop_acquisition` join timeout

**File**: `src/device_manager.py`, `stop_acquisition()`

The measurement thread is joined with `timeout=5`. If a frame acquisition takes longer than 5 s (e.g. very long `frame_time` with no trigger), the thread is not waited for. It will continue running as a daemon and may write to files after the session directory has logically ended.

---

### Edge case: `save_settings()` does not persist device-level fields

**File**: `src/config_manager.py`, `save_settings()`

The PUT `/settings` endpoint calls `config_manager.update_setting()` + `save_settings()`. This only saves `settings.json`. The per-device fields (`file_format`, `save_data`, `data_directory`, `frame_mode`, `frame_time`) live in `devices_config.json` and have no save path through the API.
