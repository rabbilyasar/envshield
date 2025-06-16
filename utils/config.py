# Manages EnvGuard's internal configuration (e.g., project mappings)
import os
import yaml
from ..utils.logger import get_logger

logger = get_logger(__name__)

class ConfigManager:
    def __init__(self):
        self.config_dir = os.path.join(os.path.expanduser("~"), '.envguard')
        self.config_file = os.path.join(self.config_dir, 'config.yaml')
        self._ensure_config_dir()
        self.config = self._load_config()

    def _ensure_config_dir(self):
        """Ensures the ~/.envguard directory exists."""
        os.makedirs(self.config_dir, exist_ok=True)

    def _load_config(self):
        """Loads the configuration from the YAML file."""
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r') as file:
                    return yaml.safe_load(file) or {}
            except yaml.YAMLError as e:
                logger.error(f"Error loading config file: {self.config_file} - {e}")
            return {}
        return {}