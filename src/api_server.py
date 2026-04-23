"""
API Server for Timepix Control System
Provides REST API for remote control and monitoring
"""

import logging
from flask import Flask, jsonify, request
from flask_cors import CORS
from typing import Optional
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from main import TimepixController

logger = logging.getLogger(__name__)


class TimepixAPI:
    """Flask-based REST API for Timepix control"""
    
    def __init__(self, controller: TimepixController, host: str = "0.0.0.0", port: int = 5000):
        """
        Initialize API server
        
        Args:
            controller: TimepixController instance
            host: Host address to bind to
            port: Port to bind to
        """
        self.controller = controller
        self.host = host
        self.port = port
        self.app = Flask(__name__)
        
        # Enable CORS if configured
        if controller.config_manager.get_setting("api", "cors_enabled"):
            CORS(self.app)
        
        self._register_routes()
    
    def _register_routes(self):
        """Register all API routes"""
        
        @self.app.route('/health', methods=['GET'])
        def health_check():
            """Health check endpoint"""
            return jsonify({
                "status": "ok",
                "initialized": self.controller.device_manager.is_initialized if self.controller.device_manager else False
            })
        
        @self.app.route('/status', methods=['GET'])
        def get_status():
            """Get system status"""
            try:
                status = self.controller.get_status()
                return jsonify(status)
            except Exception as e:
                logger.error(f"Error getting status: {e}")
                return jsonify({"error": str(e)}), 500
        
        @self.app.route('/devices', methods=['GET'])
        def get_devices():
            """Get list of all devices and their status"""
            try:
                if not self.controller.device_manager:
                    return jsonify({"error": "System not initialized"}), 400
                
                devices = self.controller.device_manager.get_all_device_status()
                return jsonify({"devices": devices})
            except Exception as e:
                logger.error(f"Error getting devices: {e}")
                return jsonify({"error": str(e)}), 500
        
        @self.app.route('/devices/<int:device_id>', methods=['GET'])
        def get_device(device_id):
            """Get status of specific device"""
            try:
                if not self.controller.device_manager:
                    return jsonify({"error": "System not initialized"}), 400
                
                device_status = self.controller.device_manager.get_device_status(device_id)
                
                if device_status is None:
                    return jsonify({"error": f"Device {device_id} not found"}), 404
                
                return jsonify(device_status)
            except Exception as e:
                logger.error(f"Error getting device {device_id}: {e}")
                return jsonify({"error": str(e)}), 500
        
        @self.app.route('/measurement/start', methods=['POST'])
        def start_measurement():
            """Start measurement"""
            try:
                data = request.get_json() or {}
                
                frame_time = data.get('frame_time')
                bias_voltages = data.get('bias_voltages')
                
                # Validate bias voltages format
                if bias_voltages is not None:
                    if not isinstance(bias_voltages, dict):
                        return jsonify({"error": "bias_voltages must be a dict"}), 400
                    
                    # Convert string keys to int
                    bias_voltages = {int(k): float(v) for k, v in bias_voltages.items()}
                
                success = self.controller.start_measurement(
                    frame_time=frame_time,
                    bias_voltages=bias_voltages
                )
                
                if success:
                    return jsonify({
                        "success": True,
                        "message": "Measurement started",
                        "frame_time": frame_time
                    })
                else:
                    return jsonify({
                        "success": False,
                        "error": "Failed to start measurement"
                    }), 400
                    
            except Exception as e:
                logger.error(f"Error starting measurement: {e}")
                return jsonify({"error": str(e)}), 500
        
        @self.app.route('/measurement/stop', methods=['POST'])
        def stop_measurement():
            """Stop measurement"""
            try:
                self.controller.stop_measurement()
                return jsonify({
                    "success": True,
                    "message": "Measurement stopped"
                })
            except Exception as e:
                logger.error(f"Error stopping measurement: {e}")
                return jsonify({"error": str(e)}), 500
        
        @self.app.route('/settings', methods=['GET'])
        def get_settings():
            """Get current settings"""
            try:
                return jsonify({
                    "acquisition": self.controller.config_manager.get_acquisition_settings(),
                    "reconnection": self.controller.config_manager.get_reconnection_settings(),
                    "monitoring": self.controller.config_manager.get_monitoring_settings()
                })
            except Exception as e:
                logger.error(f"Error getting settings: {e}")
                return jsonify({"error": str(e)}), 500
        
        @self.app.route('/settings', methods=['PUT'])
        def update_settings():
            """Update settings"""
            try:
                data = request.get_json()
                
                if not data:
                    return jsonify({"error": "No data provided"}), 400
                
                # Update settings (this is a simple implementation)
                # In production, you'd want more validation
                for category, settings in data.items():
                    if isinstance(settings, dict):
                        for key, value in settings.items():
                            self.controller.config_manager.update_setting(value, category, key)
                
                # Optionally save to file
                if request.args.get('save') == 'true':
                    self.controller.config_manager.save_settings()
                
                return jsonify({
                    "success": True,
                    "message": "Settings updated"
                })
                
            except Exception as e:
                logger.error(f"Error updating settings: {e}")
                return jsonify({"error": str(e)}), 500
        
        @self.app.route('/devices/<int:device_id>/bias', methods=['PUT'])
        def set_device_bias(device_id):
            """Set bias voltage for specific device"""
            try:
                data = request.get_json()
                
                if not data or 'bias' not in data:
                    return jsonify({"error": "bias value required"}), 400
                
                bias = float(data['bias'])
                
                if not self.controller.device_manager:
                    return jsonify({"error": "System not initialized"}), 400
                
                if device_id not in self.controller.device_manager.managed_devices:
                    return jsonify({"error": f"Device {device_id} not found"}), 404
                
                managed_dev = self.controller.device_manager.managed_devices[device_id]
                managed_dev.device.setBias(bias)
                
                return jsonify({
                    "success": True,
                    "device_id": device_id,
                    "bias": bias
                })
                
            except Exception as e:
                logger.error(f"Error setting bias: {e}")
                return jsonify({"error": str(e)}), 500
        
        @self.app.route('/data/sessions', methods=['GET'])
        def list_sessions():
            """List available data sessions across all device data directories"""
            try:
                device_configs = self.controller.config_manager.get_device_configs()
                data_dirs = list(dict.fromkeys(
                    dev.get("data_directory", "data") for dev in device_configs
                )) if device_configs else ["data"]
                
                sessions = set()
                for data_dir_str in data_dirs:
                    data_dir = Path(data_dir_str)
                    if data_dir.exists():
                        sessions.update(
                            d.name for d in data_dir.iterdir()
                            if d.is_dir() and d.name.startswith("session_")
                        )
                
                return jsonify({"sessions": sorted(sessions, reverse=True)})
                
            except Exception as e:
                logger.error(f"Error listing sessions: {e}")
                return jsonify({"error": str(e)}), 500
        
        @self.app.route('/data/sessions/<session_name>', methods=['GET'])
        def get_session_data(session_name):
            """Get data from specific session"""
            try:
                device_configs = self.controller.config_manager.get_device_configs()
                data_dirs = list(dict.fromkeys(
                    dev.get("data_directory", "data") for dev in device_configs
                )) if device_configs else ["data"]
                
                # Find which data directory contains this session
                session_path = None
                for data_dir_str in data_dirs:
                    candidate = Path(data_dir_str) / session_name
                    if candidate.exists():
                        session_path = str(candidate)
                        break
                
                if session_path is None:
                    return jsonify({"error": f"Session '{session_name}' not found"}), 404
                
                results = self.controller.data_processor.process_session_directory(session_path)
                return jsonify(results)
                
            except Exception as e:
                logger.error(f"Error getting session data: {e}")
                return jsonify({"error": str(e)}), 500
        
        @self.app.route('/data/latest', methods=['GET'])
        def get_latest_session():
            """Get data from most recent session"""
            try:
                latest_session = self.controller.data_processor.get_latest_session()
                
                if not latest_session:
                    return jsonify({"error": "No sessions found"}), 404
                
                results = self.controller.data_processor.process_session_directory(latest_session)
                return jsonify(results)
                
            except Exception as e:
                logger.error(f"Error getting latest session: {e}")
                return jsonify({"error": str(e)}), 500
    
    def run(self, debug: bool = False):
        """
        Run the API server
        
        Args:
            debug: Enable Flask debug mode
        """
        logger.info(f"Starting API server on {self.host}:{self.port}")
        self.app.run(host=self.host, port=self.port, debug=debug, threaded=True)


def main():
    """Main entry point for API server"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Timepix Control System API Server")
    parser.add_argument("--config-dir", default="config",
                       help="Configuration directory (default: config)")
    parser.add_argument("--host", default=None,
                       help="API server host (default: from config)")
    parser.add_argument("--port", type=int, default=None,
                       help="API server port (default: from config)")
    parser.add_argument("--debug", action="store_true",
                       help="Enable debug mode")
    
    args = parser.parse_args()
    
    # Create and initialize controller
    controller = TimepixController(args.config_dir)
    
    if not controller.initialize():
        print("Failed to initialize system")
        return 1
    
    # Get API settings from config
    api_settings = controller.config_manager.get_api_settings()
    host = args.host or api_settings.get("host", "0.0.0.0")
    port = args.port or api_settings.get("port", 5000)
    
    # Create and run API server
    api = TimepixAPI(controller, host=host, port=port)
    
    try:
        api.run(debug=args.debug)
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        controller.shutdown()
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
