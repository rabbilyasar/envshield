class EnvGuardException(Exception):
    """Base class for all exceptions raised by EnvGuard."""
    pass

class ConfigNotFoundError(EnvGuardException):
    """Raised when then envguard.yml configuration file cannot be found"""
    def __init__(self, message="Configuration file 'envguard.yml' not found. Please run 'envguard init' to create it."):
        self.message = message
        super().__init__(self.message)

class ProfileNotFoundError(EnvGuardException):
    """Raised when the specified profile is not found in the configuration"""
    def __init__(self, profile_name: str):
        self.message = f"Profile '{profile_name}' not found in 'envguard.yml'."
        super().__init__(self.message)

class SourceFileNotFoundError(EnvGuardException):
    """Raised when the source file for a profile is not found"""
    def __init__(self, source_file: str):
        self.message = f"Source file '{source_file}' not found."
        super().__init__(self.message)