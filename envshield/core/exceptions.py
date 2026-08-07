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


class SchemaParseError(EnvShieldException):
    """Raised when the env.schema.toml file cannot be parsed."""

    def __init__(self, schema_path: str, details: str):
        self.message = f"Schema parse error in {schema_path}: {details}"
        super().__init__(self.message)


class ConfigParseError(EnvShieldException):
    """Raised when the envshield.yml file cannot be parsed."""

    def __init__(self, config_path: str, details: str):
        self.message = f"Config parse error in {config_path}: {details}"
        super().__init__(self.message)


class UnsafePathError(EnvShieldException):
    """
    Raised when a path taken from 'envshield.yml' (a service's schema,
    local_file, or example_file) resolves outside the project directory.

    envshield.yml is normally committed to the repo, so a malicious or
    mistaken entry there (e.g. an absolute path, or '../../../.ssh/...')
    could otherwise make ordinary commands like 'setup' or 'schema sync'
    read or overwrite an arbitrary file outside the project when a teammate
    clones the repo and runs them.
    """

    def __init__(self, label: str, path: str, project_root: str):
        self.message = (
            f"Refusing to use {label} '{path}': it resolves outside the project "
            f"directory ({project_root}). Check envshield.yml for a malicious or "
            "mistaken path."
        )
        super().__init__(self.message)
