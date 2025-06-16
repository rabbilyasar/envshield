# Centralized logging
import logging
import os
from datetime import datetime

LOG_FILE_PATH = os.path.join(os.path.expanduser("~"), '.envguard', 'envguard.log')

def setup_logging():
    """Sets up the global logging for EnvGuard."""
    log_dir = os.path.dirname(LOG_FILE_PATH)
    os.makedirs(log_dir, exist_ok=True)

    # Basic configuaration
    logging.basicConfig(
        level=logging.INFO, # Default log level
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(LOG_FILE_PATH),
            logging.StreamHandler()
        ]
    )

    # Surpress verbose logging from libraries
    logging.getLogger("cryptography").setLevel(logging.WARNING)
    logging.getLogger("dotenv").setLevel(logging.WARNING)

def get_logger(name):
    """Returns a logger with the specified name."""
    return logging.getLogger(name)