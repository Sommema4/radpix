"""
Device Manager for Timepix Control System
Handles device initialization, health monitoring, and reconnection logic
"""

import logging
import time
import threading
from typing import Dict, List, Optional, Any, Callable
from enum import Enum
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)


class DeviceState(Enum):
    """Device connection states"""
    UNKNOWN = "unknown"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    RECONNECTING = "reconnecting"
    FAILED = "failed"
    MEASURING = "measuring"


@dataclass
class DeviceStatus:
    """Status information for a single device"""
    device_id: int
    name: str
    serial: str
    state: DeviceState
    frames_acquired: int = 0
    particles_detected: int = 0
    last_frame_particles: int = 0
    reconnect_attempts: int = 0
    last_error: Optional[str] = None
    last_reconnect_time: Optional[datetime] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization"""
        return {
            "device_id": self.device_id,
            "name": self.name,
            "serial": self.serial,
            "state": self.state.value,
            "frames_acquired": self.frames_acquired,
            "particles_detected": self.particles_detected,
            "last_frame_particles": self.last_frame_particles,
            "reconnect_attempts": self.reconnect_attempts,
            "last_error": self.last_error,
            "last_reconnect_time": self.last_reconnect_time.isoformat() if self.last_reconnect_time else None
        }


class ManagedDevice:
    """Wrapper for a Timepix device with management capabilities"""
    
    def __init__(self, device, device_id: int, config: Dict[str, Any]):
        """
        Initialize managed device
        
        Args:
            device: pypixet device object
            device_id: Numeric ID for this device
            config: Device configuration dictionary
        """
        self.device = device
        self.device_id = device_id
        self.config = config
        self.status = DeviceStatus(
            device_id=device_id,
            name=config.get("name", f"Device_{device_id}"),
            serial=config.get("serial", "unknown"),
            state=DeviceState.CONNECTED
        )
        self.lock = threading.Lock()
    
    def is_connected(self) -> bool:
        """Check if device is currently connected"""
        try:
            return self.device.isConnected() == 1
        except Exception as e:
            logger.error(f"Error checking connection for {self.status.name}: {e}")
            return False
    
    def update_state(self, new_state: DeviceState):
        """Update device state"""
        with self.lock:
            old_state = self.status.state
            self.status.state = new_state
            if old_state != new_state:
                logger.info(f"{self.status.name} state: {old_state.value} -> {new_state.value}")
    
    def increment_frame_count(self):
        """Increment the frame counter"""
        with self.lock:
            self.status.frames_acquired += 1
    
    def update_particle_count(self, count: int):
        """Update particle detection counts"""
        with self.lock:
            self.status.last_frame_particles = count
            self.status.particles_detected += count


class DeviceManager:
    """Manages multiple Timepix devices with health monitoring and reconnection"""
    
    def __init__(self, config_manager, pypixet_module):
        """
        Initialize device manager
        
        Args:
            config_manager: ConfigManager instance
            pypixet_module: The pypixet module (imported)
        """
        self.config_manager = config_manager
        self.pypixet = pypixet_module
        self.pixet = None
        self.managed_devices: Dict[int, ManagedDevice] = {}
        self.is_initialized = False
        self.is_measuring = False
        self.measurement_thread = None
        self.reconnect_thread = None
        self.stop_reconnect = threading.Event()
        self._callbacks: Dict[str, List[Callable]] = {
            "state_changed": [],
            "frame_acquired": [],
            "error": []
        }
        
    def initialize(self) -> bool:
        """
        Initialize pypixet and discover devices
        
        Returns:
            True if initialization successful
        """
        try:
            logger.info("Initializing PIXet core...")
            self.pypixet.start()
            self.pixet = self.pypixet.pixet
            
            # Small delay after initialization
            time.sleep(0.5)
            
            self.is_initialized = True
            logger.info("PIXet core initialized successfully")
            
            # Discover and setup devices
            self._discover_and_setup_devices()
            
            # Start reconnection monitoring thread
            self._start_reconnect_monitor()
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize PIXet core: {e}")
            self.is_initialized = False
            return False
    
    def _discover_and_setup_devices(self):
        """Discover connected devices and match them to configuration"""
        try:
            # Get all connected devices
            all_devices = self.pixet.devices()
            
            # Filter out FileDevice if present
            physical_devices = [dev for dev in all_devices 
                              if dev.fullName() != "FileDevice 0"]
            
            if not physical_devices:
                logger.warning("No physical devices connected")
                return
            
            logger.info(f"Found {len(physical_devices)} physical device(s)")
            
            # Get device configurations
            device_configs = self.config_manager.get_device_configs()
            
            # Match devices to configurations
            for idx, device in enumerate(physical_devices):
                try:
                    device_name = device.fullName()
                    logger.info(f"Setting up device {idx}: {device_name}")
                    
                    # Try to match by serial or use first available config
                    device_config = None
                    if len(device_configs) > idx:
                        device_config = device_configs[idx]
                    
                    if device_config:
                        self._setup_device(device, idx, device_config)
                    else:
                        logger.warning(f"No configuration found for device {idx}")
                        
                except Exception as e:
                    logger.error(f"Failed to setup device {idx}: {e}")
            
            logger.info(f"Successfully initialized {len(self.managed_devices)} device(s)")
            
        except Exception as e:
            logger.error(f"Error discovering devices: {e}")
    
    def _setup_device(self, device, device_id: int, config: Dict[str, Any]) -> bool:
        """
        Setup a single device with configuration
        
        Args:
            device: pypixet device object
            device_id: Numeric ID for the device
            config: Device configuration
            
        Returns:
            True if setup successful
        """
        try:
            managed_dev = ManagedDevice(device, device_id, config)
            
            # Load device configuration file if specified
            config_file = config.get("config_file")
            if config_file:
                try:
                    rc = device.loadConfigFromFile(config_file)
                    if rc == 0:
                        logger.info(f"Loaded config file for {config['name']}: {config_file}")
                    else:
                        logger.warning(f"Failed to load config file (rc={rc}), using factory defaults")
                        device.loadFactoryConfig()
                except Exception as e:
                    logger.warning(f"Error loading config file: {e}, using factory defaults")
                    device.loadFactoryConfig()
            
            # Set operation mode
            operation_mode = config.get("operation_mode")
            if operation_mode:
                mode_constant = getattr(self.pixet, operation_mode, None)
                if mode_constant is not None:
                    rc = device.setOperationMode(mode_constant)
                    if rc == 0:
                        logger.info(f"Set operation mode: {operation_mode}")
                    else:
                        logger.warning(f"Failed to set operation mode (rc={rc})")
            
            # Set bias voltage
            default_bias = config.get("default_bias")
            if default_bias is not None:
                try:
                    device.setBias(default_bias)
                    logger.info(f"Set bias voltage: {default_bias}V")
                except Exception as e:
                    logger.warning(f"Failed to set bias: {e}")
            
            # Store managed device
            self.managed_devices[device_id] = managed_dev
            
            logger.info(f"Device {device_id} ({config['name']}) setup complete")
            return True
            
        except Exception as e:
            logger.error(f"Failed to setup device {device_id}: {e}")
            return False
    
    def _start_reconnect_monitor(self):
        """Start background thread to monitor and reconnect devices"""
        self.stop_reconnect.clear()
        self.reconnect_thread = threading.Thread(target=self._reconnect_monitor_loop, daemon=True)
        self.reconnect_thread.start()
        logger.info("Reconnection monitor started")
    
    def _reconnect_monitor_loop(self):
        """Background loop to check device health and attempt reconnections"""
        reconnect_settings = self.config_manager.get_reconnection_settings()
        
        if not reconnect_settings.get("enabled", True):
            logger.info("Reconnection monitoring disabled")
            return
        
        initial_interval = reconnect_settings.get("initial_interval_sec", 30)
        max_fast_attempts = reconnect_settings.get("max_fast_attempts", 10)
        slow_interval = reconnect_settings.get("slow_interval_sec", 300)
        
        while not self.stop_reconnect.is_set():
            try:
                # Check health of all devices
                disconnected_devices = []
                
                for dev_id, managed_dev in self.managed_devices.items():
                    if not managed_dev.is_connected():
                        if managed_dev.status.state not in [DeviceState.DISCONNECTED, 
                                                            DeviceState.RECONNECTING,
                                                            DeviceState.FAILED]:
                            logger.warning(f"{managed_dev.status.name} disconnected!")
                            managed_dev.update_state(DeviceState.DISCONNECTED)
                            managed_dev.status.last_error = "Device disconnected"
                            self._emit_callback("error", managed_dev.status.to_dict())
                        
                        disconnected_devices.append(managed_dev)
                
                # Handle reconnection
                if disconnected_devices:
                    all_disconnected = len(disconnected_devices) == len(self.managed_devices)
                    
                    for managed_dev in disconnected_devices:
                        # Determine reconnection interval
                        if all_disconnected and reconnect_settings.get("aggressive_mode_both_offline", True):
                            # Aggressive mode: try every 10 seconds
                            interval = 10
                        elif managed_dev.status.reconnect_attempts < max_fast_attempts:
                            # Fast attempts
                            interval = initial_interval
                        else:
                            # Slow attempts after max fast attempts
                            interval = slow_interval
                        
                        # Check if it's time to attempt reconnection
                        should_attempt = False
                        if managed_dev.status.last_reconnect_time is None:
                            should_attempt = True
                        else:
                            elapsed = (datetime.now() - managed_dev.status.last_reconnect_time).total_seconds()
                            if elapsed >= interval:
                                should_attempt = True
                        
                        if should_attempt:
                            self._attempt_reconnect(managed_dev)
                
                # Sleep for monitoring interval
                monitoring_settings = self.config_manager.get_monitoring_settings()
                check_interval = monitoring_settings.get("health_check_interval_sec", 5)
                self.stop_reconnect.wait(check_interval)
                
            except Exception as e:
                logger.error(f"Error in reconnect monitor loop: {e}")
                self.stop_reconnect.wait(10)
    
    def _attempt_reconnect(self, managed_dev: ManagedDevice):
        """
        Attempt to reconnect a device
        
        Args:
            managed_dev: The managed device to reconnect
        """
        try:
            logger.info(f"Attempting to reconnect {managed_dev.status.name} "
                       f"(attempt {managed_dev.status.reconnect_attempts + 1})")
            
            managed_dev.update_state(DeviceState.RECONNECTING)
            managed_dev.status.reconnect_attempts += 1
            managed_dev.status.last_reconnect_time = datetime.now()
            
            # Attempt reconnection
            rc = managed_dev.device.reconnect()
            
            # Small delay after reconnect attempt
            time.sleep(0.5)
            
            # Check if successful
            if managed_dev.is_connected():
                logger.info(f"{managed_dev.status.name} reconnected successfully!")
                managed_dev.update_state(DeviceState.CONNECTED)
                managed_dev.status.reconnect_attempts = 0
                managed_dev.status.last_error = None
                
                # Re-apply configuration
                config = managed_dev.config
                
                # Set operation mode
                operation_mode = config.get("operation_mode")
                if operation_mode:
                    mode_constant = getattr(self.pixet, operation_mode, None)
                    if mode_constant is not None:
                        managed_dev.device.setOperationMode(mode_constant)
                
                # Set bias
                default_bias = config.get("default_bias")
                if default_bias is not None:
                    managed_dev.device.setBias(default_bias)
                
                self._emit_callback("state_changed", managed_dev.status.to_dict())
                
                # If measuring, restart acquisition on this device
                if self.is_measuring:
                    logger.info(f"Resuming measurement on {managed_dev.status.name}")
                
                return True
            else:
                logger.warning(f"Reconnection attempt failed for {managed_dev.status.name}")
                managed_dev.update_state(DeviceState.FAILED)
                managed_dev.status.last_error = f"Reconnection failed (attempt {managed_dev.status.reconnect_attempts})"
                return False
                
        except Exception as e:
            logger.error(f"Error during reconnection attempt: {e}")
            managed_dev.update_state(DeviceState.FAILED)
            managed_dev.status.last_error = str(e)
            return False
    
    def start_acquisition(self, frame_time: Optional[float] = None, 
                         bias_voltages: Optional[Dict[int, float]] = None) -> bool:
        """
        Start continuous acquisition on all connected devices
        
        Args:
            frame_time: Frame acquisition time in seconds (None = use config default)
            bias_voltages: Optional dict of {device_id: bias_voltage}
            
        Returns:
            True if acquisition started successfully
        """
        if not self.is_initialized:
            logger.error("Cannot start acquisition: system not initialized")
            return False
        
        if self.is_measuring:
            logger.warning("Acquisition already running")
            return False
        
        try:
            # Get frame time from config if not specified
            if frame_time is None:
                frame_time = self.config_manager.get_setting("acquisition", "default_frame_time")
                if frame_time is None:
                    frame_time = 1.0
            
            # Apply bias voltages if specified
            if bias_voltages:
                for dev_id, bias in bias_voltages.items():
                    if dev_id in self.managed_devices:
                        try:
                            self.managed_devices[dev_id].device.setBias(bias)
                            logger.info(f"Set bias {bias}V on device {dev_id}")
                        except Exception as e:
                            logger.error(f"Failed to set bias on device {dev_id}: {e}")
            
            # Start acquisition thread
            self.is_measuring = True
            self.measurement_thread = threading.Thread(
                target=self._acquisition_loop,
                args=(frame_time,),
                daemon=True
            )
            self.measurement_thread.start()
            
            logger.info(f"Acquisition started with frame time {frame_time}s")
            return True
            
        except Exception as e:
            logger.error(f"Failed to start acquisition: {e}")
            self.is_measuring = False
            return False
    
    def _acquisition_loop(self, frame_time: float):
        """
        Main acquisition loop running in background thread
        
        Args:
            frame_time: Frame acquisition time in seconds
        """
        acq_settings = self.config_manager.get_acquisition_settings()
        file_format = acq_settings.get("file_format", "clog")
        save_data = acq_settings.get("save_data", True)
        data_dir = acq_settings.get("data_directory", "data")
        
        # Create session directory
        session_name = f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        session_dir = f"{data_dir}/{session_name}"
        
        try:
            import os
            os.makedirs(session_dir, exist_ok=True)
            logger.info(f"Saving data to: {session_dir}")
        except Exception as e:
            logger.error(f"Failed to create session directory: {e}")
            save_data = False
        
        # Determine file type constant
        file_type_map = {
            "clog": "PX_FTYPE_CLOG",
            "png": "PX_FTYPE_PNG",
            "txt": "PX_FTYPE_TXT",
            "none": "PX_FTYPE_NONE"
        }
        
        file_type_str = file_type_map.get(file_format.lower(), "PX_FTYPE_CLOG")
        file_type = getattr(self.pixet, file_type_str, None)
        
        if not save_data:
            file_type = getattr(self.pixet, "PX_FTYPE_NONE")
        
        frame_counter = 0
        
        while self.is_measuring:
            try:
                # Acquire from all connected devices
                for dev_id, managed_dev in self.managed_devices.items():
                    if not managed_dev.is_connected():
                        continue
                    
                    if managed_dev.status.state != DeviceState.CONNECTED:
                        continue
                    
                    try:
                        # Update state to measuring
                        managed_dev.update_state(DeviceState.MEASURING)
                        
                        # Prepare filename
                        if save_data and file_type != getattr(self.pixet, "PX_FTYPE_NONE"):
                            filename = f"{session_dir}/device{dev_id}_frame{frame_counter:06d}"
                        else:
                            filename = ""
                        
                        # Acquire single frame
                        rc = managed_dev.device.doSimpleAcquisition(
                            1,  # count
                            frame_time,
                            file_type,
                            filename
                        )
                        
                        if rc == 0:
                            managed_dev.increment_frame_count()
                            self._emit_callback("frame_acquired", {
                                "device_id": dev_id,
                                "frame_number": managed_dev.status.frames_acquired,
                                "filename": filename if filename else None
                            })
                        else:
                            logger.error(f"Acquisition failed on device {dev_id}, rc={rc}")
                            error_msg = managed_dev.device.lastError()
                            logger.error(f"Error: {error_msg}")
                            managed_dev.status.last_error = error_msg
                        
                        # Return to connected state
                        managed_dev.update_state(DeviceState.CONNECTED)
                        
                    except Exception as e:
                        logger.error(f"Error during acquisition on device {dev_id}: {e}")
                        managed_dev.status.last_error = str(e)
                
                frame_counter += 1
                
            except Exception as e:
                logger.error(f"Error in acquisition loop: {e}")
                time.sleep(1)
        
        logger.info("Acquisition loop stopped")
    
    def stop_acquisition(self):
        """Stop ongoing acquisition"""
        if not self.is_measuring:
            logger.warning("No acquisition running")
            return
        
        logger.info("Stopping acquisition...")
        self.is_measuring = False
        
        # Abort operations on all devices
        for managed_dev in self.managed_devices.values():
            try:
                managed_dev.device.abortOperation()
            except Exception as e:
                logger.error(f"Error aborting device {managed_dev.device_id}: {e}")
        
        # Wait for measurement thread to finish
        if self.measurement_thread and self.measurement_thread.is_alive():
            self.measurement_thread.join(timeout=5)
        
        # Update device states
        for managed_dev in self.managed_devices.values():
            if managed_dev.is_connected():
                managed_dev.update_state(DeviceState.CONNECTED)
        
        logger.info("Acquisition stopped")
    
    def get_all_device_status(self) -> List[Dict[str, Any]]:
        """Get status of all managed devices"""
        return [dev.status.to_dict() for dev in self.managed_devices.values()]
    
    def get_device_status(self, device_id: int) -> Optional[Dict[str, Any]]:
        """Get status of specific device"""
        managed_dev = self.managed_devices.get(device_id)
        if managed_dev:
            return managed_dev.status.to_dict()
        return None
    
    def register_callback(self, event: str, callback: Callable):
        """Register a callback for events"""
        if event in self._callbacks:
            self._callbacks[event].append(callback)
    
    def _emit_callback(self, event: str, data: Any):
        """Emit callback to registered listeners"""
        if event in self._callbacks:
            for callback in self._callbacks[event]:
                try:
                    callback(data)
                except Exception as e:
                    logger.error(f"Error in callback: {e}")
    
    def shutdown(self):
        """Shutdown device manager and cleanup"""
        logger.info("Shutting down device manager...")
        
        # Stop acquisition if running
        if self.is_measuring:
            self.stop_acquisition()
        
        # Stop reconnection monitor
        self.stop_reconnect.set()
        if self.reconnect_thread and self.reconnect_thread.is_alive():
            self.reconnect_thread.join(timeout=5)
        
        # Cleanup pypixet
        if self.is_initialized:
            try:
                if self.pixet:
                    self.pixet.exitPixet()
                self.pypixet.exit()
                logger.info("PIXet core shut down")
            except Exception as e:
                logger.error(f"Error during shutdown: {e}")
        
        self.is_initialized = False
        logger.info("Device manager shutdown complete")
