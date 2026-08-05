# envshield/parsers/factory.py
# A factory to select the appropriate parser based on file extension (and,
# for YAML, on content -- docker-compose and Kubernetes manifests share the
# '.yml'/'.yaml' extension with everything else).

import os

from ._base import BaseParser
from ._deployment import detect_deployment_format
from ._docker_compose import DockerComposeParser
from ._dotenv import DotenvParser
from ._kubernetes import KubernetesParser
from ._python import PythonParser


def get_parser(
    file_path: str, container: str | None = None, prefer: str | None = None
) -> BaseParser | None:
    """
    Selects and returns the correct parser instance based on the file extension.

    Args:
        file_path: The path to the file that needs parsing.
        container: For a docker-compose/Kubernetes manifest declaring more
            than one service/container, which one to parse. Ignored by
            every other parser.
        prefer: A soft hint (typically the --service name) tried when the
            manifest is ambiguous and `container` wasn't given explicitly.
            Ignored by every other parser.

    Returns:
        An instance of a BaseParser subclass, or None if no suitable
        parser is found.
    """
    _, extension = os.path.splitext(file_path)

    if extension == ".py":
        return PythonParser()
    if extension in (".yml", ".yaml"):
        fmt = detect_deployment_format(file_path)
        if fmt == "docker-compose":
            return DockerComposeParser(container=container, prefer=prefer)
        if fmt == "kubernetes":
            return KubernetesParser(container=container, prefer=prefer)
        return None
    # Assume files with no extension (like '.env') or '.env' extension are dotenv files
    elif extension == "" or ".env" in file_path:
        return DotenvParser()

    # In the future, we can add more parsers here (e.g., for .json, .toml)

    return None
