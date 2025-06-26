# # envguard/utils/error_handling.py
# class EnvGuardError(Exception):
#     """Custom base exception for all EnvGuard-related operational errors."""
#     pass

# class ConfigError(EnvGuardError):
#     """Exception raised for errors related to configuration."""
#     pass

# class FileOperationError(EnvGuardError):
#     """Exception raised for errors during file read/write operations."""
#     pass

# class GitError(EnvGuardError):
#     """Exception raised for errors during Git command execution."""
#     pass

# class EncryptionError(EnvGuardError):
#     """Exception raised for errors during encryption/decryption."""
#     pass

# class ValidationError(EnvGuardError):
#     """Exception raised when input data fails validation."""
#     pass

# envguard/utils/error_handling.py
# Defines custom exception classes for EnvGuard, allowing for more specific error handling.

class EnvGuardError(Exception):
    """
    Base exception for all custom EnvGuard operational errors.
    All other custom exceptions should inherit from this.
    """
    pass

class ConfigError(EnvGuardError):
    """
    Raised when there's an issue with EnvGuard's configuration files
    (e.g., malformed YAML, missing required settings, invalid profiles).
    """
    pass

class FileOperationError(EnvGuardError):
    """
    Raised when file system operations fail (e.g., file not found,
    permission issues, inability to create/delete files/symlinks).
    """
    pass

class GitError(EnvGuardError):
    """
    Raised when Git-related operations fail (e.g., not inside a Git repository,
    issues with Git commands, problems installing/uninstalling hooks).
    """
    pass

class EncryptionError(EnvGuardError):
    """
    Raised when encryption or decryption processes fail (e.g., incorrect password,
    corrupted data, issues with the cryptography library).
    """
    pass

class ValidationError(EnvGuardError):
    """
    Base class for errors related to data validation (general purpose,
    e.g., invalid user input, data not conforming to expectations).
    """
    pass

class SchemaValidationError(ValidationError):
    """
    Raised specifically when environment variables do not conform to a defined schema.
    This helps distinguish schema issues from other validation failures.
    """
    pass

class ParserError(EnvGuardError):
    """
    Raised when a custom file parser encounters an error during reading or writing,
    indicating an issue specific to the file format interpretation.
    """
    pass

class SecretDetectionError(EnvGuardError):
    """
    Raised when there are issues during secret detection processes,
    such as problems with regex patterns or scanning files.
    This helps in isolating secret-related errors from other operational issues.
    """
    pass