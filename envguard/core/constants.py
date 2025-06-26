# envguard/core/constants.py
import os
from pathlib import Path

# --- Application Metadata ---
APP_NAME = "EnvGuard"
APP_VERSION = "0.1.0" # Current version of EnvGuard

# --- Global Configuration Paths ---
# The global configuration directory.
# On Linux/macOS, typically ~/.config/envguard. On Windows, %APPDATA%\EnvGuard.
# Special handling for root user (e.g., in Docker containers) to use /etc/envguard.
GLOBAL_CONFIG_DIR = Path.home() / '.config' / APP_NAME.lower() if Path.home().name != 'root' else Path('/etc') / APP_NAME.lower()
# The global configuration file.
GLOBAL_CONFIG_FILE = GLOBAL_CONFIG_DIR / 'config.yml'

# --- Project-Specific Configuration Paths ---
# The default name for the project-specific configuration file.
PROJECT_CONFIG_FILE_NAME = 'envguard.yml'

# --- Asset Paths ---
# Base directory for application assets (templates, default patterns).
ASSETS_DIR = Path(__file__).parent.parent / 'assets'

# Path to the default global config template (used during `envguard init` for initial global setup).
DEFAULT_GLOBAL_CONFIG_TEMPLATE = ASSETS_DIR / 'default_global_config.yml'
# Path to the default project config template (used during `envguard init` for initial project setup).
DEFAULT_PROJECT_CONFIG_TEMPLATE = ASSETS_DIR / 'default_project_config.yml'
# Path to the JSON file containing default regex patterns for secret detection.
DEFAULT_SECRET_PATTERNS_FILE = ASSETS_DIR / 'default_secret_patterns.json'


# --- Default File Names & General Settings ---
# Default name for the primary environment variable file (e.g., '.env').
DEFAULT_MAIN_ENV_FILE_NAME = '.env'
# Default name for the environment variable template file (e.g., '.env.template').
DEFAULT_TEMPLATE_ENV_FILE_NAME = '.env.template'
# Default profile name used if no other is specified or configured.
DEFAULT_PROFILE_NAME = 'dev' # Changed from 'default' to 'dev' as it's more common for primary dev env

# The name of the symbolic link that points to the currently active environment file.
# This link is typically created in the project root (e.g., `my_project/.env -> my_project/.env_dev`).
ACTIVE_ENV_SYMLINK_NAME = DEFAULT_MAIN_ENV_FILE_NAME # Often .env itself becomes the symlink

# --- Supported Parser Types ---
# List of environment file parser types that EnvGuard supports.
# Extend this list as you add more parser implementations (e.g., 'ini', 'xml').
SUPPORTED_PARSERS = ['default', 'json', 'yaml']

# --- Schema Validation Keywords ---
# Standard keywords used in the schema definition within envguard.yml.
SCHEMA_TYPES = ['string', 'integer', 'boolean', 'float', 'array'] # Supported data types
SCHEMA_REQUIRED_KEY = 'required'        # Boolean: if the variable is mandatory
SCHEMA_TYPE_KEY = 'type'                # String: data type (from SCHEMA_TYPES)
SCHEMA_DEFAULT_KEY = 'default'          # Any: default value if missing
SCHEMA_DESCRIPTION_KEY = 'description'  # String: description for user prompts
SCHEMA_ENUM_KEY = 'enum'                # List: allowed values for the variable
SCHEMA_PATTERN_KEY = 'pattern'          # String: regex pattern the value must match
SCHEMA_MIN_KEY = 'min'                  # Numeric: minimum value (for numbers)
SCHEMA_MAX_KEY = 'max'                  # Numeric: maximum value (for numbers)
SCHEMA_LENGTH_KEY = 'length'            # Integer: exact string length (for strings)
SCHEMA_MIN_LENGTH_KEY = 'minLength'     # Integer: minimum string length
SCHEMA_MAX_LENGTH_KEY = 'maxLength'     # Integer: maximum string length
SCHEMA_PLACEHOLDER_KEY = 'placeholder'  # String: custom placeholder for templates (overrides sensitive keys)

# --- Rich CLI Output Symbols ---
# Unicode symbols for consistent visual cues in terminal output.
SYMBOL_SUCCESS = '✓'
SYMBOL_WARNING = '!'
SYMBOL_ERROR = '✗'
SYMBOL_INFO = 'i'
SYMBOL_QUESTION = '?'
SYMBOL_STAR = '*' # Generic symbol for lists/items