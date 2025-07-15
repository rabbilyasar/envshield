# envguard/core/exceptions.py
# This file defines custom exceptions for the EnvGuard application.
# Using custom exceptions makes error handling more specific and clear.

class EnvGuardException(Exception):
    """Base exception class for all EnvGuard errors."""
    pass

class ConfigNotFoundError(EnvGuardException):
    """Raised when the envguard.yml configuration file cannot be found."""
    def __init__(self, message="Configuration file 'envguard.yml' not found. Please run 'envguard init'."):
        self.message = message
        super().__init__(self.message)

class ProfileNotFoundError(EnvGuardException):
    """Raised when a specified profile is not found in the configuration."""
    def __init__(self, profile_name: str):
        self.message = f"Profile '{profile_name}' not found in 'envguard.yml'."
        super().__init__(self.message)

class SourceFileNotFoundError(EnvGuardException):
    """Raised when a profile's source file does not exist."""
    def __init__(self, source_path: str):
        self.message = f"Source file '{source_path}' does not exist."
        super().__init__(self.message)