# envshield/parsers/_docker_compose.py
import os

import yaml

from ..core.exceptions import EnvShieldException
from ._base import BaseParser
from ._dotenv import DotenvParser


class DockerComposeParser(BaseParser):
    """
    Parses a docker-compose file's declared environment for one service,
    combining its 'environment:' block with whatever 'env_file:' it
    references (resolved relative to the compose file's own directory;
    'environment:' wins over 'env_file:' on a key conflict, matching
    docker-compose's own precedence).

    A value that can't be known statically -- a bare 'KEY' entry with no
    '=' (passed through from the host shell), or any 'env_file' reference
    -- is reported as present with a placeholder value rather than as
    missing or blank, since the real value legitimately lives outside this
    file.
    """

    UNRESOLVED_VALUE = "<value not visible in this file>"

    def __init__(self, container: str | None = None, prefer: str | None = None):
        self.container = container
        # A soft hint (typically the --service name), tried only when the
        # file is otherwise ambiguous and no explicit --container was given
        # -- services and containers are very often named identically, so
        # this resolves the common case without ever overriding an explicit
        # choice or a file that only has one service anyway.
        self.prefer = prefer

    def get_vars(
        self, file_path: str, get_values: bool = False
    ) -> set[str] | dict[str, str]:
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        with open(file_path, "r") as f:
            doc = yaml.safe_load(f) or {}

        services = doc.get("services") if isinstance(doc, dict) else None
        if not isinstance(services, dict) or not services:
            return {} if get_values else set()

        container = self.container
        if container is None:
            if len(services) == 1:
                container = next(iter(services))
            elif self.prefer and self.prefer in services:
                container = self.prefer
            else:
                raise EnvShieldException(
                    f"This docker-compose file declares multiple services ({', '.join(sorted(services))}) -- pass --container to pick one."
                )
        elif container not in services:
            raise EnvShieldException(
                f"Service '{container}' not found in this docker-compose file. Available: {', '.join(sorted(services))}"
            )

        service_def = services.get(container) or {}
        variables: dict[str, str] = {}

        base_dir = os.path.dirname(os.path.abspath(file_path))
        env_files = service_def.get("env_file")
        if env_files:
            if isinstance(env_files, str):
                env_files = [env_files]
            for env_file in env_files:
                env_file_path = os.path.join(base_dir, env_file)
                if os.path.exists(env_file_path):
                    try:
                        variables.update(
                            DotenvParser().get_vars(env_file_path, get_values=True)
                        )
                    except OSError:
                        pass

        environment = service_def.get("environment")
        if isinstance(environment, dict):
            for key, value in environment.items():
                variables[key] = (
                    str(value) if value is not None else self.UNRESOLVED_VALUE
                )
        elif isinstance(environment, list):
            for entry in environment:
                entry = str(entry)
                if "=" in entry:
                    key, value = entry.split("=", 1)
                    variables[key.strip()] = value
                else:
                    variables[entry.strip()] = self.UNRESOLVED_VALUE

        return variables if get_values else set(variables.keys())
