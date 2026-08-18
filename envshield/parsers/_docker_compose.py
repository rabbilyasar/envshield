# envshield/parsers/_docker_compose.py
import os
import re

import yaml

from ..core.exceptions import EnvShieldException
from ._base import BaseParser
from ._dotenv import DotenvParser

# Matches a value that is ENTIRELY one Compose variable-substitution
# reference -- '${VAR}' or '${VAR:-default}'. A reference embedded inside a
# larger string ('prefix-${VAR}-suffix') is intentionally left as literal
# text: resolving a partial substitution would require modeling Compose's
# full shell-style expansion grammar, which is out of scope here. The
# common real-world case -- the whole value is one reference, e.g.
# 'DB_PORT=${DB_PORT:-3307}' -- is what this closes.
_INTERPOLATION_RE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)(:-(.*))?\}$")


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
            try:
                doc = yaml.safe_load(f) or {}
            except yaml.YAMLError as e:
                raise EnvShieldException(
                    f"Could not parse '{file_path}': {e}. If this file contains "
                    "multiple YAML documents ('---'-separated), only a single "
                    "document is supported."
                )

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
                if isinstance(env_file, dict):
                    env_file = env_file.get("path")
                if not env_file:
                    continue
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
                raw = str(value) if value is not None else self.UNRESOLVED_VALUE
                variables[key] = self._resolve_interpolation(raw)
        elif isinstance(environment, list):
            for entry in environment:
                entry = str(entry)
                if "=" in entry:
                    key, value = entry.split("=", 1)
                    variables[key.strip()] = self._resolve_interpolation(value)
                else:
                    variables[entry.strip()] = self.UNRESOLVED_VALUE

        return variables if get_values else set(variables.keys())

    def _resolve_interpolation(self, value: str) -> str:
        """
        Resolves a value that is entirely one '${VAR}'/'${VAR:-default}'
        Compose variable-substitution reference. With a fallback, the
        fallback is the value this container actually receives absent a
        real shell environment -- the same reasoning schema.defaultValue
        already uses elsewhere. Without one, the real value legitimately
        lives outside this file, so it's reported the same way as any other
        statically-unknowable value (see UNRESOLVED_VALUE) rather than as
        the literal, un-interpolated template text.
        """
        match = _INTERPOLATION_RE.match(value.strip())
        if not match:
            return value
        _var_name, has_default, default = match.groups()
        return default if has_default else self.UNRESOLVED_VALUE
