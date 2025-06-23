# envguard/utils/error_handling.py
class EnvGuardError(Exception):
    """Custom base exception for all EnvGuard-related operational errors."""
    pass

class ConfigError(EnvGuardError):
    """Exception raised for errors related to configuration."""
    pass

class FileOperationError(EnvGuardError):
    """Exception raised for errors during file read/write operations."""
    pass

class GitError(EnvGuardError):
    """Exception raised for errors during Git command execution."""
    pass

class EncryptionError(EnvGuardError):
    """Exception raised for errors during encryption/decryption."""
    pass

class ValidationError(EnvGuardError):
    """Exception raised when input data fails validation."""
    pass