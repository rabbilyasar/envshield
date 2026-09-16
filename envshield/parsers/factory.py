# envshield/parsers/factory.py
# A factory to select the appropriate parser based on file extension (and,
# for YAML, on content -- docker-compose and Kubernetes manifests share the
# '.yml'/'.yaml' extension with everything else).

import os
from typing import Dict, List, Optional, Set, Tuple, Union

from ..core.exceptions import EnvShieldException
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


def get_manifest_parser_and_vars(
    paths: List[str],
    container: Optional[str] = None,
    prefer: Optional[str] = None,
    get_values: bool = True,
) -> Tuple[Optional[BaseParser], Optional[Union[Dict[str, str], Set[str]]]]:
    """
    Resolves and parses ONE logical deployment manifest from its ordered
    layer file(s) -- the single place 'check'/'doctor'/'explain' all get a
    manifest's variables from, so BL-025's Compose base+override merging
    never needs a second implementation.

    A single path behaves exactly like `get_parser(path, ...).get_vars(path,
    ...)` -- unchanged from before BL-025. More than one path is meaningful
    only for Docker Compose (an ordered base + override layer list); any
    other manifest format raises, since 'files:' is explicitly Compose-only
    -- see envshield.yml's own manifest registration rules.

    Returns (None, None) if no parser could be found for a single path
    (mirroring get_parser's own None contract). Raises FileNotFoundError,
    ValueError, or EnvShieldException exactly as get_parser()/.get_vars()
    already do -- callers catch the same exception types as before.
    """
    if len(paths) == 1:
        parser = get_parser(paths[0], container=container, prefer=prefer)
        if parser is None:
            return None, None
        return parser, parser.get_vars(paths[0], get_values=get_values)

    fmt = detect_deployment_format(paths[0])
    if fmt != "docker-compose":
        raise EnvShieldException(
            "A manifest registered with multiple 'files:' is only supported "
            f"for Docker Compose base+override layering -- '{paths[0]}' is "
            "not a docker-compose file."
        )
    parser = DockerComposeParser(container=container, prefer=prefer)
    values = parser.get_vars_for_layers(paths, get_values=get_values)
    return parser, values
