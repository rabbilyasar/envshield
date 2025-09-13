# envshield/core/exceptions.py
class EnvShieldException(Exception):
    """Base exception class for all EnvShield errors."""

    pass


class ConfigNotFoundError(EnvShieldException):
    """Raised when the envshield.yml configuration file cannot be found."""

    def __init__(
        self,
        message="Configuration file 'envshield.yml' not found. Please run 'envshield init'.",
    ):
        self.message = message
        super().__init__(self.message)


class SchemaNotFoundError(EnvShieldException):
    """Raised when the env.schema.toml file cannot be found."""

    def __init__(
        self,
        message="Schema file 'env.schema.toml' not found. Please run 'envshield init' or create the file.",
    ):
        self.message = message
        super().__init__(self.message)


class ProfileNotFoundError(EnvShieldException):
    """Raised when a specified profile is not found in the configuration."""

    def __init__(self, profile_name: str):
        self.message = f"Profile '{profile_name}' not found in 'envshield.yml'."
        super().__init__(self.message)


class SourceFileNotFoundError(EnvShieldException):
    """Raised when a profile's source file does not exist."""

    def __init__(self, source_path: str):
        self.message = f"Source file '{source_path}' does not exist."
        super().__init__(self.message)
