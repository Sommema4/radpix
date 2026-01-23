# Timepix Control System

A robust Python-based control system for managing multiple Timepix detectors with automated reconnection, data acquisition, and remote API access.

## Features

- **Multi-Device Support**: Control up to 2 (or more) Timepix devices simultaneously
- **Automatic Reconnection**: Intelligent reconnection logic handles device disconnections gracefully
- **Continuous Acquisition**: Frame-based acquisition with configurable acquisition times
- **CLOG Data Processing**: Parse and analyze CLOG files for particle counting and statistics
- **REST API**: Full remote control via HTTP API
- **Flexible Configuration**: JSON-based device and application configuration
- **Comprehensive Logging**: Detailed logging for debugging and monitoring

## Architecture

The system consists of several modular components:

- **ConfigManager**: Handles loading and validation of configurations
- **DeviceManager**: Manages device lifecycle, health monitoring, and reconnection
- **DataProcessor**: Processes CLOG files and extracts particle statistics
- **TimepixController**: Main orchestrator coordinating all components
- **API Server**: Flask-based REST API for remote control

## Prerequisites

### Hardware
- Timepix detector(s) (TPX, TPX2, TPX3, MPX2, MPX3)
- Compatible readout hardware (MiniPIX, WidePIX, AdvaPIX, etc.)

### Software
- Python 3.7 or higher (64-bit)
- PIXet SDK with pypixet module
- Required DLLs:
  - `pypixet.pyd`
  - `pxcore.dll` (or `.so` on Linux)
  - Hardware-specific libraries (e.g., `minipix.dll`, `zest.dll`)
- Configuration files:
  - `pixet.ini` (with hwlibs configuration)
  - Device XML configuration files (e.g., `MiniPIX-I08-W0060.xml`)

## Installation

### 1. Clone or download this repository

```bash
cd radpix
```

### 2. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 3. Install PIXet SDK

Follow ADVACAM's instructions to install the PIXet SDK and ensure the following files are accessible:
- `pypixet.pyd` (should be in Python path or current directory)
- `pxcore.dll` and related DLLs
- Hardware libraries specified in `pixet.ini`

### 4. Configure your devices

Edit `config/devices_config.json` to match your connected devices:

```json
{
  "devices": [
    {
      "serial": "I08-W0060",
      "name": "TPX_Device_1",
      "type": "TPX3",
      "config_file": "config/device_xmls/MiniPIX-I08-W0060.xml",
      "default_bias": 50.0,
      "operation_mode": "PX_TPX3_OPM_TOATOT",
      "enabled": true
    }
  ]
}
```

### 5. Place device configuration XML files

Copy your device XML configuration files to `config/device_xmls/`

## Usage

### Standalone Mode (Command Line)

Run the system with automatic measurement start:

```bash
cd src
python main.py
```

With custom frame time:

```bash
python main.py --frame-time 2.0
```

Without auto-start (for manual control):

```bash
python main.py --no-auto-start
```

### API Server Mode

Start the REST API server:

```bash
cd src
python api_server.py
```

With custom host/port:

```bash
python api_server.py --host 0.0.0.0 --port 5000
```

## REST API Endpoints

### System Status

- `GET /health` - Health check
- `GET /status` - Get system status
- `GET /settings` - Get current settings
- `PUT /settings` - Update settings

### Device Management

- `GET /devices` - List all devices
- `GET /devices/<id>` - Get specific device status
- `PUT /devices/<id>/bias` - Set device bias voltage

### Measurement Control

- `POST /measurement/start` - Start measurement
  ```json
  {
    "frame_time": 1.0,
    "bias_voltages": {
      "0": 50.0,
      "1": 60.0
    }
  }
  ```

- `POST /measurement/stop` - Stop measurement

### Data Access

- `GET /data/sessions` - List all data sessions
- `GET /data/sessions/<session_name>` - Get session data
- `GET /data/latest` - Get most recent session data

## API Usage Examples

### Start measurement with curl:

```bash
curl -X POST http://localhost:5000/measurement/start \
  -H "Content-Type: application/json" \
  -d '{"frame_time": 1.0, "bias_voltages": {"0": 50.0}}'
```

### Get system status:

```bash
curl http://localhost:5000/status
```

### Stop measurement:

```bash
curl -X POST http://localhost:5000/measurement/stop
```

### Python client example:

```python
import requests

# Start measurement
response = requests.post(
    'http://localhost:5000/measurement/start',
    json={'frame_time': 1.5, 'bias_voltages': {'0': 55.0}}
)
print(response.json())

# Get status
status = requests.get('http://localhost:5000/status').json()
print(f"Measuring: {status['measuring']}")
print(f"Devices: {len(status['devices'])}")

# Stop measurement
requests.post('http://localhost:5000/measurement/stop')
```

## Configuration

### Application Settings (`config/settings.json`)

```json
{
  "acquisition": {
    "default_frame_time": 1.0,
    "file_format": "clog",
    "save_data": true,
    "data_directory": "data"
  },
  "reconnection": {
    "enabled": true,
    "initial_interval_sec": 30,
    "max_fast_attempts": 10,
    "slow_interval_sec": 300,
    "aggressive_mode_both_offline": true
  },
  "api": {
    "enabled": true,
    "host": "0.0.0.0",
    "port": 5000
  }
}
```

### Device Configuration (`config/devices_config.json`)

See the Installation section for device configuration format.

## Reconnection Logic

The system implements intelligent reconnection handling:

1. **Single Device Disconnect**:
   - Continues measuring on connected devices
   - Attempts reconnection every 30 seconds (configurable)
   - After 10 fast attempts, switches to slow mode (5 minutes)
   - Automatically resumes measurement when reconnected

2. **Both Devices Disconnect**:
   - Enters aggressive reconnection mode
   - Attempts reconnection every 10 seconds
   - No retry limit - continues until reconnection

3. **Reconnection Process**:
   - Calls `device.reconnect()`
   - Re-applies device configuration and bias settings
   - Resumes acquisition if measurement was active

## Data Processing

### CLOG File Format

The system saves data in CLOG (cluster log) format, which contains:
- Frame number and timestamp
- Cluster (particle) positions
- Energy deposits
- Time-of-arrival data (if applicable)

### Particle Counting

The DataProcessor automatically:
- Parses CLOG files in real-time
- Counts particles per frame
- Calculates occupancy statistics
- Exports summary statistics to CSV

### Example CLOG Entry

```
Frame 2 (273697060.937500, 0.000000 s)
[214, 195, 43.1598, 0] [220, 191, 20.6515, 7.8125]
[224, 182, 21.8018, 31.25] [223, 186, 4.58576, 31.25]
```

## Directory Structure

```
radpix/
├── config/
│   ├── devices_config.json      # Device configuration
│   ├── settings.json             # Application settings
│   └── device_xmls/              # Device XML configs
├── src/
│   ├── main.py                   # Main controller
│   ├── api_server.py             # REST API server
│   ├── device_manager.py         # Device management
│   ├── config_manager.py         # Configuration management
│   └── data_processor.py         # Data processing
├── data/                          # Acquired data
│   └── session_YYYYMMDD_HHMMSS/
├── logs/                          # Application logs
├── requirements.txt               # Python dependencies
└── README.md                      # This file
```

## Logging

Logs are saved to `logs/timepix_YYYYMMDD_HHMMSS.log` with:
- Timestamp
- Logger name
- Log level
- Message

Log levels:
- **DEBUG**: Detailed diagnostic information
- **INFO**: General informational messages
- **WARNING**: Warning messages for potential issues
- **ERROR**: Error messages for failures

## Troubleshooting

### pypixet import error

Ensure `pypixet.pyd` and required DLLs are in the Python path or current directory.

### Device not detected

1. Check `pixet.ini` has correct hwlib configuration
2. Verify hardware is connected
3. Check device drivers are installed
4. Review logs for connection errors

### Configuration file not found

Ensure device XML files are in the correct location specified in `devices_config.json`.

### Bias voltage fails to set

Check device specifications for valid bias voltage range using:
```python
print(f"Bias range: {device.biasMin()}V - {device.biasMax()}V")
```

## Future Enhancements

- WebSocket support for real-time status updates
- GPIO button support for Radxa / Raspberry Pi
- Display driver for small screens
- Web-based GUI dashboard
- Calibration management
- Advanced trigger modes

## License

Contact ADVACAM for PIXet SDK licensing information.

## Support

For issues related to:
- **This software**: Check logs and open an issue
- **PIXet SDK**: Contact ADVACAM support
- **Hardware**: Contact your device vendor

## References

- PIXet API Documentation: See `PIXetAPIPython.pdf`
- ADVACAM Website: https://advacam.com/
