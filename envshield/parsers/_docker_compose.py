# envshield/parsers/_docker_compose.py
import os
import re

import yaml

from ..core.exceptions import EnvShieldException
from ._base import (
    LITERAL_OPERATORS,
    BaseParser,
    Concrete,
    Forwarded,
    ValueState,
    ValueStates,
    legacy_text,
)
from ._deployment import ensure_within_project, safe_yaml_error_message
from ._dotenv import DotenvParser

# Matches a value that is ENTIRELY one Compose variable-substitution
# reference: '${VAR}', or '${VAR' + an operator ('-', ':-', '?', ':?', '+',
# ':+') + text + '}'. Each becomes a Forwarded state (v5 plan §4). A
# reference embedded inside a larger string ('prefix-${VAR}-suffix') is
# intentionally left as literal text: resolving a partial substitution
# would require modeling Compose's full shell-style expansion grammar,
# which is out of scope here.
_INTERPOLATION_RE = re.compile(
    r"^\$\{([A-Za-z_][A-Za-z0-9_]*)(?:(:?-|:?\?|:?\+)(.*))?\}$"
)


class DockerComposeParser(BaseParser):
    """
    Parses a docker-compose file's declared environment for one service,
    combining its 'environment:' block with whatever 'env_file:' it
    references (resolved relative to the compose file's own directory;
    'environment:' wins over 'env_file:' on a key conflict, matching
    docker-compose's own precedence).

    get_states reports each key's ValueState: a literal value, or an
    'env_file' that exists, is Concrete; a bare 'KEY' entry (passed through
    from the host shell) or a whole-value interpolation is Forwarded, since
    the real value comes from the deploy host. get_vars' values are each
    state's legacy_text, "" where the value isn't visible.

    A whole-value '${VAR}'/'${VAR:-default}'/'${VAR-default}' reference is reported under
    VAR (the host-facing name the schema declares), not under the
    container-facing key it's assigned to -- e.g.
    'AUTHENTIK_POSTGRESQL__PASSWORD: ${PG_PASS}' contributes a PG_PASS
    entry, not an AUTHENTIK_POSTGRESQL__PASSWORD one, since the rename is
    purely internal to the container and isn't itself part of the
    contract being validated.
    """

    is_deployment_manifest = True

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
        return _as_vars(self.get_states(file_path), get_values)

    def get_states(self, file_path: str) -> ValueStates:
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        doc = self._load_yaml_document(file_path)
        services = doc.get("services") if isinstance(doc, dict) else None
        if not isinstance(services, dict) or not services:
            return ValueStates()

        container = self._resolve_container(services, file_path)
        service_def = services.get(container) or {}
        base_dir = os.path.dirname(os.path.abspath(file_path))
        missing: list[str] = []
        variables = self._extract_service_environment(service_def, base_dir, missing)
        return ValueStates(variables, missing_files=missing)

    def get_vars_for_layers(
        self, file_paths: list[str], get_values: bool = False
    ) -> set[str] | dict[str, str]:
        return _as_vars(self.get_states_for_layers(file_paths), get_values)

    def get_states_for_layers(self, file_paths: list[str]) -> ValueStates:
        """
        BL-025: merges an ordered list of Compose files (a base file plus
        zero or more override layers -- exactly what Compose itself applies
        by default for 'docker-compose.yml' + 'docker-compose.override.yml')
        into ONE logical environment representation, validated as a single
        manifest rather than per-file with reconciliation afterward.

        Only the `environment:`/`env_file:` semantics this parser already
        understands are merged -- volumes, networks, ports, build, etc. are
        never inspected, by design (this is not a generic Compose merge
        engine). The merge itself is Compose's own documented rule for
        these fields: a later file's entry for a given variable replaces an
        earlier one; a variable untouched by a later file keeps whatever
        value an earlier file gave it. Because get_vars already reduces
        each single file's own `environment:` + `env_file:` down to one
        flat {var: value} map (env_file already loses to an explicit
        `environment:` entry within that same file, per docker-compose's
        own precedence), merging layers is exactly an ordered dict.update()
        across those already-resolved per-file maps -- no separate list-
        vs-mapping merge logic is needed, since both syntaxes already
        collapse to the same flat shape before layers are ever combined.

        The service/container name is resolved once, from the first layer
        that actually declares any services (ordinarily the base file) --
        using the exact same resolution algorithm get_vars itself uses, so
        an ambiguous or missing container is reported identically to the
        single-file case. A *later* layer that simply doesn't mention that
        service contributes nothing and is not an error: an override file
        legitimately only touching a subset of services is normal Compose
        usage, not a malformed manifest.
        """
        if not file_paths:
            raise EnvShieldException(
                "get_vars_for_layers requires at least one Compose file."
            )

        missing = [fp for fp in file_paths if not os.path.exists(fp)]
        if missing:
            raise FileNotFoundError(
                "Compose layer file(s) not found: " + ", ".join(missing)
            )

        merged: dict[str, ValueState] = {}
        missing: list[str] = []
        resolved_container: str | None = self.container

        for file_path in file_paths:
            doc = self._load_yaml_document(file_path)
            services = doc.get("services") if isinstance(doc, dict) else None
            if not isinstance(services, dict) or not services:
                continue

            if resolved_container is None:
                resolved_container = self._resolve_container(services, file_path)
            elif resolved_container not in services:
                continue

            service_def = services.get(resolved_container) or {}
            base_dir = os.path.dirname(os.path.abspath(file_path))
            merged.update(
                self._extract_service_environment(service_def, base_dir, missing)
            )

        return ValueStates(merged, missing_files=missing)

    def _load_yaml_document(self, file_path: str) -> dict:
        with open(file_path, "r") as f:
            try:
                return yaml.safe_load(f) or {}
            except yaml.YAMLError as e:
                raise EnvShieldException(
                    f"Could not parse '{file_path}': {safe_yaml_error_message(e)}. "
                    "If this file contains multiple YAML documents "
                    "('---'-separated), only a single document is supported."
                )

    def _resolve_container(self, services: dict, file_path: str) -> str:
        container = self.container
        if container is None:
            if len(services) == 1:
                return next(iter(services))
            elif self.prefer and self.prefer in services:
                return self.prefer
            else:
                raise EnvShieldException(
                    f"This docker-compose file declares multiple services ({', '.join(sorted(services))}) -- pass --container to pick one."
                )
        elif container not in services:
            raise EnvShieldException(
                f"Service '{container}' not found in this docker-compose file. Available: {', '.join(sorted(services))} ({file_path})"
            )
        return container

    def _extract_service_environment(
        self, service_def: dict, base_dir: str, missing: list[str]
    ) -> dict[str, ValueState]:
        """
        The service's {var: ValueState} map. Appends to `missing` each
        required env_file that doesn't exist (Compose refuses to start);
        one marked 'required: false' is skipped, as Compose skips it.
        """
        variables: dict[str, ValueState] = {}

        env_files = service_def.get("env_file")
        if env_files:
            if isinstance(env_files, str):
                env_files = [env_files]
            for env_file in env_files:
                required = True
                if isinstance(env_file, dict):
                    required = env_file.get("required", True) is not False
                    env_file = env_file.get("path")
                if not env_file:
                    continue
                env_file_path = ensure_within_project(
                    base_dir, env_file, "docker-compose 'env_file' reference"
                )
                if os.path.exists(env_file_path):
                    try:
                        variables.update(DotenvParser().get_states(env_file_path))
                    except OSError:
                        pass
                elif required:
                    missing.append(env_file)

        environment = service_def.get("environment")
        if isinstance(environment, dict):
            for key, value in environment.items():
                if value is None:
                    variables[key] = Forwarded(key)
                else:
                    name, state = self._resolve_interpolation(key, str(value))
                    variables[name] = state
        elif isinstance(environment, list):
            for entry in environment:
                entry = str(entry)
                if "=" in entry:
                    key, value = entry.split("=", 1)
                    name, state = self._resolve_interpolation(key.strip(), value)
                    variables[name] = state
                else:
                    variables[entry.strip()] = Forwarded(entry.strip())

        return variables

    def _resolve_interpolation(self, key: str, value: str) -> tuple[str, ValueState]:
        """
        `key: value`'s (name, state). A value that is entirely one
        interpolation reference is Forwarded from the host variable it
        names; anything else is the Concrete literal, under `key`.

        A '${VAR}'/'${VAR-x}'/'${VAR:-x}' reference is keyed under VAR --
        e.g. 'AUTHENTIK_POSTGRESQL__PASSWORD: ${PG_PASS}' is a schema-
        relevant statement about PG_PASS, not about
        AUTHENTIK_POSTGRESQL__PASSWORD, which is purely a container-internal
        rename. A LITERAL_OPERATORS reference stays under `key`, where v4.8.0 kept it
        as literal text (legacy_text reproduces that text, so it must match
        `value` exactly, surrounding whitespace included).
        """
        match = _INTERPOLATION_RE.match(value.strip())
        if not match:
            return key, Concrete(value)
        var_name, operator, text = match.groups()
        if operator in LITERAL_OPERATORS:
            if value != value.strip():
                return key, Concrete(value)
            return key, Forwarded(var_name, operator, text)
        return var_name, Forwarded(var_name, operator, text)


def _as_vars(states: ValueStates, get_values: bool) -> set[str] | dict[str, str]:
    if not get_values:
        return set(states)
    return {key: legacy_text(state) or "" for key, state in states.items()}
