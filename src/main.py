"""
Main Controller for Timepix Control System
Orchestrates device management, acquisition, and data processing
"""

import sys
import logging
import signal
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from config_manager import ConfigManager
from device_manager import DeviceManager
from data_processor import DataProcessor


class TimepixController:
    """Main controller for the Timepix measurement system"""
    
    def __init__(self, config_dir: str = "config"):
        """
        Initialize the Timepix controller
        
        Args:
            config_dir: Directory containing configuration files
        """
        self.config_manager = None
        self.device_manager = None
        self.data_processor = None
        self.pypixet = None
        self._setup_logging()
        self._load_config(config_dir)
    
    def _setup_logging(self):
        """Setup logging configuration"""
        # Create logs directory if it doesn't exist
        Path("logs").mkdir(exist_ok=True)
        
        # Configure root logger
        log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        
        # Console handler
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(logging.Formatter(log_format))
        
        # File handler
        from datetime import datetime
        log_filename = f"logs/timepix_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        file_handler = logging.FileHandler(log_filename)
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(log_format))
        
        # Configure root logger
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.DEBUG)
        root_logger.addHandler(console_handler)
        root_logger.addHandler(file_handler)
        
        self.logger = logging.getLogger(__name__)
        self.logger.info(f"Logging initialized. Log file: {log_filename}")
    
    def _load_config(self, config_dir: str):
        """Load configuration"""
        try:
            self.config_manager = ConfigManager(config_dir)
            self.logger.info("Configuration loaded successfully")
            
            # Check for missing config files
            missing_files = self.config_manager.validate_config_files_exist()
            if missing_files:
                self.logger.warning(f"Missing device config files: {missing_files}")
                self.logger.warning("Devices will use factory defaults")
            
        except Exception as e:
            self.logger.error(f"Failed to load configuration: {e}")
            raise
    
    def initialize(self) -> bool:
        """
        Initialize the system (pypixet, devices, etc.)
        
        Returns:
            True if initialization successful
        """
        try:
            self.logger.info("Initializing Timepix Control System...")
            
            # Import pypixet
            try:
                import pypixet
                self.pypixet = pypixet
                self.logger.info("pypixet module imported successfully")
            except ImportError as e:
                self.logger.error(f"Failed to import pypixet: {e}")
                self.logger.error("Make sure pypixet.pyd and required DLLs are in the path")
                return False
            
            # Initialize data processor
            data_dir = self.config_manager.get_setting("acquisition", "data_directory")
            self.data_processor = DataProcessor(data_dir or "data")
            self.logger.info("Data processor initialized")
            
            # Initialize device manager
            self.device_manager = DeviceManager(self.config_manager, self.pypixet)
            
            if not self.device_manager.initialize():
                self.logger.error("Failed to initialize device manager")
                return False
            
            # Register callbacks
            self.device_manager.register_callback("frame_acquired", self._on_frame_acquired)
            self.device_manager.register_callback("state_changed", self._on_state_changed)
            self.device_manager.register_callback("error", self._on_error)
            
            self.logger.info("Timepix Control System initialized successfully")
            return True
            
        except Exception as e:
            self.logger.error(f"Initialization failed: {e}")
            return False
    
    def _on_frame_acquired(self, data: dict):
        """Callback when a frame is acquired"""
        device_id = data.get("device_id")
        frame_number = data.get("frame_number")
        filename = data.get("filename")
        
        self.logger.debug(f"Frame {frame_number} acquired on device {device_id}")
        
        # Process CLOG file if available
        if filename and filename.endswith(".clog"):
            try:
                stats = self.data_processor.get_real_time_stats(filename)
                particles = stats.get("latest_frame_particles", 0)
                
                # Update device manager with particle count
                if device_id in self.device_manager.managed_devices:
                    managed_dev = self.device_manager.managed_devices[device_id]
                    managed_dev.update_particle_count(particles)
                    
                    self.logger.info(f"Device {device_id}: Frame {frame_number}, "
                                   f"Particles: {particles}")
            except Exception as e:
                self.logger.error(f"Error processing frame data: {e}")
    
    def _on_state_changed(self, data: dict):
        """Callback when device state changes"""
        device_id = data.get("device_id")
        name = data.get("name")
        state = data.get("state")
        
        self.logger.info(f"Device {device_id} ({name}) state changed to: {state}")
    
    def _on_error(self, data: dict):
        """Callback when an error occurs"""
        device_id = data.get("device_id")
        name = data.get("name")
        error = data.get("last_error")
        
        self.logger.error(f"Error on device {device_id} ({name}): {error}")
    
    def start_measurement(self, frame_time: float = None, 
                         bias_voltages: dict = None) -> bool:
        """
        Start measurement on all connected devices
        
        Args:
            frame_time: Frame acquisition time in seconds (None = use config)
            bias_voltages: Dict of {device_id: bias_voltage}
            
        Returns:
            True if measurement started successfully
        """
        if not self.device_manager:
            self.logger.error("System not initialized")
            return False
        
        return self.device_manager.start_acquisition(frame_time, bias_voltages)
    
    def stop_measurement(self):
        """Stop ongoing measurement"""
        if self.device_manager:
            self.device_manager.stop_acquisition()
    
    def get_status(self) -> dict:
        """
        Get current system status
        
        Returns:
            Dictionary with status information
        """
        if not self.device_manager:
            return {
                "initialized": False,
                "measuring": False,
                "devices": []
            }
        
        return {
            "initialized": self.device_manager.is_initialized,
            "measuring": self.device_manager.is_measuring,
            "devices": self.device_manager.get_all_device_status()
        }
    
    def shutdown(self):
        """Shutdown the system cleanly"""
        self.logger.info("Shutting down Timepix Control System...")
        
        if self.device_manager:
            self.device_manager.shutdown()
        
        self.logger.info("Shutdown complete")


def main():
    """Main entry point for standalone operation"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Timepix Control System")
    parser.add_argument("--config-dir", default="config", 
                       help="Configuration directory (default: config)")
    parser.add_argument("--frame-time", type=float, default=None,
                       help="Frame acquisition time in seconds")
    parser.add_argument("--no-auto-start", action="store_true",
                       help="Don't automatically start measurement")
    
    args = parser.parse_args()
    
    # Create controller
    controller = TimepixController(args.config_dir)
    
    # Initialize
    if not controller.initialize():
        print("Failed to initialize system")
        return 1
    
    # Setup signal handlers for clean shutdown
    def signal_handler(sig, frame):
        print("\nShutdown signal received...")
        controller.stop_measurement()
        controller.shutdown()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Start measurement if auto-start enabled
    if not args.no_auto_start:
        print(f"\nStarting measurement (frame time: {args.frame_time or 'config default'}s)")
        if controller.start_measurement(frame_time=args.frame_time):
            print("Measurement started successfully")
            print("Press Ctrl+C to stop measurement and exit")
        else:
            print("Failed to start measurement")
            controller.shutdown()
            return 1
    else:
        print("\nSystem initialized. Use API to control measurement.")
        print("Press Ctrl+C to exit")
    
    # Keep running
    try:
        import time
        while True:
            time.sleep(1)
            
            # Print status update every 10 seconds
            if not args.no_auto_start:
                status = controller.get_status()
                if status["measuring"]:
                    for dev in status["devices"]:
                        if dev["state"] in ["measuring", "connected"]:
                            print(f"  Device {dev['device_id']}: "
                                  f"Frames={dev['frames_acquired']}, "
                                  f"Particles={dev['particles_detected']}")
                    
    except KeyboardInterrupt:
        pass
    
    # Cleanup
    controller.stop_measurement()
    controller.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
