"""
Configuration Manager for Timepix Control System
Handles loading and validation of device and application settings
"""

import json
import os
import logging
from typing import Dict, List, Any, Optional
from pathlib import Path

logger = logging.getLogger(__name__)


class ConfigManager:
    """Manages configuration loading and validation for the Timepix control system"""
    
    def __init__(self, config_dir: str = "config"):
        """
        Initialize the configuration manager
        
        Args:
            config_dir: Directory containing configuration files
        """
        self.config_dir = Path(config_dir)
        self.devices_config: Dict[str, Any] = {}
        self.settings: Dict[str, Any] = {}
        self._load_configurations()
    
    def _load_configurations(self):
        """Load all configuration files"""
        try:
            self._load_devices_config()
            self._load_settings()
            logger.info("Configurations loaded successfully")
        except Exception as e:
            logger.error(f"Failed to load configurations: {e}")
            raise
    
    def _load_devices_config(self):
        """Load device configuration from devices_config.json"""
        config_path = self.config_dir / "devices_config.json"
        
        if not config_path.exists():
            raise FileNotFoundError(f"Device configuration not found: {config_path}")
        
        with open(config_path, 'r') as f:
            self.devices_config = json.load(f)
        
        # Validate device configuration
        if "devices" not in self.devices_config:
            raise ValueError("Device configuration missing 'devices' key")
        
        logger.info(f"Loaded configuration for {len(self.devices_config['devices'])} devices")
    
    def _load_settings(self):
        """Load application settings from settings.json"""
        settings_path = self.config_dir / "settings.json"
        
        if not settings_path.exists():
            raise FileNotFoundError(f"Settings file not found: {settings_path}")
        
        with open(settings_path, 'r') as f:
            self.settings = json.load(f)
        
        logger.info("Application settings loaded")
    
    def get_device_configs(self) -> List[Dict[str, Any]]:
        """
        Get list of all device configurations
        
        Returns:
            List of device configuration dictionaries
        """
        return [dev for dev in self.devices_config.get("devices", []) 
                if dev.get("enabled", True)]
    
    def get_device_config_by_serial(self, serial: str) -> Optional[Dict[str, Any]]:
        """
        Get device configuration by serial number
        
        Args:
            serial: Device serial number
            
        Returns:
            Device configuration dict or None if not found
        """
        for device in self.devices_config.get("devices", []):
            if device.get("serial") == serial:
                return device
        return None
    
    def get_device_config_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """
        Get device configuration by name
        
        Args:
            name: Device name
            
        Returns:
            Device configuration dict or None if not found
        """
        for device in self.devices_config.get("devices", []):
            if device.get("name") == name:
                return device
        return None
    
    def get_operation_mode_for_type(self, device_type: str) -> str:
        """
        Get the operation mode constant for a device type
        
        Args:
            device_type: Device type (TPX, TPX3, etc.)
            
        Returns:
            Operation mode constant string
        """
        modes = self.devices_config.get("device_type_operation_modes", {})
        return modes.get(device_type, "PX_TPX3_OPM_TOATOT")
    
    def get_setting(self, *keys) -> Any:
        """
        Get a setting value using dot notation
        
        Args:
            *keys: Sequence of keys to traverse (e.g., 'acquisition', 'frame_time')
            
        Returns:
            Setting value or None if not found
        """
        value = self.settings
        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return None
        return value
    
    def get_acquisition_settings(self) -> Dict[str, Any]:
        """Get all acquisition settings"""
        return self.settings.get("acquisition", {})
    
    def get_reconnection_settings(self) -> Dict[str, Any]:
        """Get all reconnection settings"""
        return self.settings.get("reconnection", {})
    
    def get_logging_settings(self) -> Dict[str, Any]:
        """Get all logging settings"""
        return self.settings.get("logging", {})
    
    def get_api_settings(self) -> Dict[str, Any]:
        """Get all API settings"""
        return self.settings.get("api", {})
    
    def get_monitoring_settings(self) -> Dict[str, Any]:
        """Get all monitoring settings"""
        return self.settings.get("monitoring", {})
    
    def update_setting(self, value: Any, *keys):
        """
        Update a setting value
        
        Args:
            value: New value to set
            *keys: Sequence of keys to traverse
        """
        if not keys:
            return
        
        target = self.settings
        for key in keys[:-1]:
            if key not in target:
                target[key] = {}
            target = target[key]
        
        target[keys[-1]] = value
        logger.info(f"Updated setting {'.'.join(keys)} = {value}")
    
    def save_settings(self):
        """Save current settings back to file"""
        settings_path = self.config_dir / "settings.json"
        
        with open(settings_path, 'w') as f:
            json.dump(self.settings, f, indent=2)
        
        logger.info("Settings saved to file")
    
    def reload_configurations(self):
        """Reload all configurations from disk"""
        self._load_configurations()
        logger.info("Configurations reloaded")
    
    def validate_config_files_exist(self) -> List[str]:
        """
        Check if all device XML config files exist
        
        Returns:
            List of missing file paths
        """
        missing_files = []
        
        for device in self.get_device_configs():
            config_file = device.get("config_file")
            if config_file:
                file_path = Path(config_file)
                if not file_path.exists():
                    missing_files.append(str(file_path))
        
        return missing_files
