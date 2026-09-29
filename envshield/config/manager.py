import os
from typing import Any, Dict, List, Optional

import toml
import yaml
from rich.console import Console

from envshield.core import schema_scope, schema_types
from envshield.core.exceptions import (
    ConfigNotFoundError,
    ConfigParseError,
    EnvShieldException,
    InvalidManifestDefinitionError,
    SchemaNotFoundError,
    SchemaParseError,
    SchemaScopeError,
    SecretDefaultConflictError,
    ServiceConfigError,
    UnsafePathError,
)
from envshield.utils import git_utils
from envshield.utils.paths import is_within

CONFIG_FILE_NAME = "envshield.yml"
SCHEMA_FILE_NAME = "env.schema.toml"
GITIGNORE_FILE_NAME = ".gitignore"
console = Console()


def _ensure_within_project(path: str, label: str) -> str:
    """
    Validates that `path` -- typically a service's schema/local_file/
    example_file entry read from envshield.yml -- stays within the current
    project directory, and returns it unchanged if so.

    Raises UnsafePathError otherwise. See UnsafePathError's docstring for
    why this matters: envshield.yml is committed to the repo, so an
    unvalidated override is a supply-chain-style arbitrary read/write vector
    for anyone who clones the repo and runs ordinary commands.

    The containment decision is made on the *resolved* (symlink-followed)
    location of both sides (see utils.paths.is_within), not just the
    lexically normalized abspath. A committed symlink inside the project
    pointing outside it (or a chain of them) satisfies a purely lexical
    abspath/commonpath check while its real target does not, which is the
    P0-6 vulnerability this guards against. realpath resolves as much of
    the path as exists and appends the remainder unresolved, so a dangling
    symlink or a not-yet-existing target under a symlinked parent directory
    is still resolved and checked correctly -- no separate existence check
    is needed. The *returned* value is still the original `path` string,
    unresolved: callers persist this into envshield.yml, and swapping in a
    resolved absolute path there would replace a portable relative path
    with a machine-specific one.
    """
    project_root = os.path.abspath(os.getcwd())
    candidate = os.path.join(project_root, path)
    if not is_within(candidate, project_root):
        raise UnsafePathError(label, path, project_root)
    return path


def _check_schema_extends_cycle(schema_path: str, visited: frozenset) -> str:
    """
    Returns the resolved real path for `schema_path` (for the caller to add
    to its own `visited` set), raising SchemaParseError if it's already in
    `visited`. Shared by _load_schema_file and resolve_field_provenance so
    a future fix to this check can't patch only one of the two (see
    BL-010) -- both already mirror each other's extends-recursion exactly.

    Uses os.path.realpath, not os.path.abspath: abspath only normalizes a
    path lexically (collapsing '.'/'..' segments) and never follows
    symlinks, so a self-referencing symlink chain (e.g. 'real_dir/self'
    pointing back to 'real_dir') produces a different-looking-but-
    identical-target path on every recursive visit -- 'real_dir/x.toml',
    'real_dir/self/x.toml', 'real_dir/self/self/x.toml', ... -- and never
    repeats lexically, so this check never fires. The recursion was then
    only bounded by the OS's own symlink-resolution depth limit (ELOOP),
    producing a garbled SchemaNotFoundError instead of a clean, immediate
    "circular 'extends' chain detected". realpath collapses every one of
    those spellings to the same real target, so the true cycle is caught
    on its second visit, exactly like an ordinary non-symlinked cycle
    already is.
    """
    real_path = os.path.realpath(schema_path)
    if real_path in visited:
        raise SchemaParseError(schema_path, "circular 'extends' chain detected")
    return real_path


def find_project_root(start: str = ".") -> Optional[str]:
    """
    Walks upward from `start` looking for envshield.yml, the same way Git
    locates a repository from any subdirectory -- so a command run from
    inside a service's own directory (e.g. 'services/api') still finds the
    project's config instead of reporting an uninitialized project.

    The walk never crosses upward past the nearest enclosing Git repository
    boundary relative to `start` (a '.git' directory or file -- see
    git_utils.find_nearest_git_boundary). Once `start` is inside an
    independent Git repository -- a nested checkout, a vendored dependency,
    or a submodule sitting inside an EnvShield-managed project -- an
    'envshield.yml' living in some unrelated outer repository must never be
    silently adopted as this invocation's project, the same way a real
    'git' command run inside a submodule never reaches up into the
    superproject's own '.git'. If `start` isn't inside any Git repository
    at all, there's no boundary to respect, and the walk proceeds all the
    way to the filesystem root exactly as before this existed.

    Returns the absolute directory containing envshield.yml, or None if
    there isn't one within `start`'s own Git repository boundary (or
    anywhere above `start`, if it's not inside a Git repository at all).
    """
    current = os.path.abspath(start)
    git_boundary = git_utils.find_nearest_git_boundary(current)
    while True:
        if os.path.isfile(os.path.join(current, CONFIG_FILE_NAME)):
            return current
        if git_boundary is not None and current == git_boundary:
            return None
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def load_config(path: Optional[str] = None) -> Dict[str, Any]:
    """
    Loads and parses the envshield.yml file.
    """
    config_path = path or CONFIG_FILE_NAME
    if not os.path.exists(config_path):
        if path:
            raise ConfigNotFoundError(
                f"Configuration file not found at '{config_path}'"
            )
        return {}
    try:
        with open(config_path, "r") as f:
            config_data = yaml.safe_load(f)
            return config_data if config_data else {}
    except yaml.YAMLError as e:
        raise ConfigParseError(config_path, str(e))
    except IOError as e:
        raise ConfigParseError(config_path, str(e))


def load_schema(service_name: str) -> Dict[str, Any]:
    """
    Loads and parses a service's env.schema.toml file.

    envshield.yml is "the brain": every project, single-service or not,
    always has at least one named entry under `services` (see
    generate_default_config_content) -- there is no separate root/rootless
    shape to special-case here anymore. The schema itself stays "the
    documentation": types, descriptions, secret flags, and everything else
    describing what a variable *means* lives only in the TOML file this
    loads, never in envshield.yml.

    A schema can also declare a top-level `extends` key (a path, or a list
    of paths, to one or more base schemas) to share common variables across
    services without copy-pasting them -- see `_load_schema_file`.

    Returns the service's *effective* schema -- for a schema shared by
    several services, only what this service is granted, with its
    per-service overrides applied (see load_schema_view). For a schema with
    one user and no 'services' scoping, that's the merged schema, exactly
    as before shared schemas existed.
    """
    return load_schema_view(service_name).fields


def load_schema_view(service_name: str) -> schema_scope.ServiceSchemaView:
    """
    load_schema's full result: the service's effective fields plus the
    whole system schema and each variable's service grants -- for callers
    that must tell "defined but not granted to this service" apart from
    "not defined at all". Every live schema load goes through here.
    """
    schema_path = get_service_schema_path(service_name)
    if not schema_path:
        available = ", ".join(sorted(get_services().keys())) or "none registered yet"
        raise SchemaNotFoundError(
            f"Service '{service_name}' not found in configuration. "
            f"Available: {available}. Run 'envshield service list' to check."
        )

    if not os.path.exists(schema_path):
        raise SchemaNotFoundError(
            f"Schema file not found: {schema_path}. Run 'envshield init' to "
            "recreate it, or restore the file at that path."
        )
    merged = _load_schema_file(schema_path)
    view = schema_scope.project(
        merged,
        schema_path,
        service_name,
        get_schema_users(schema_path),
        registered=get_services().keys(),
    )
    # The whole system, not only this service's fields: a secret carrying a
    # default is refused even while it's out of this service's scope.
    _reject_secret_defaults(view.system, schema_path)
    return view


def get_schema_users(schema_path: str) -> List[str]:
    """
    Every registered service whose `schema` is this same file (compared by
    resolved real path, so './env.schema.toml' or a symlink to it counts),
    in envshield.yml order.

    Fails closed: a registered service whose entry can't be read well
    enough to tell which schema it uses raises ServiceConfigError instead
    of being skipped -- silently dropping it could make a shared schema
    look single-user, and single-user schemas don't require secrets to be
    scoped. A legacy 'path:' entry is still compared by that path: it's
    broken for its own commands, but its schema is still knowable.
    """
    target = os.path.realpath(os.path.join(os.getcwd(), schema_path))
    users = []
    for name, entry in get_services().items():
        ref = (
            entry.get("schema", entry.get("path")) if isinstance(entry, dict) else None
        )
        if not isinstance(ref, str) or not ref:
            raise ServiceConfigError(
                f"Service '{name}' in {CONFIG_FILE_NAME} has no usable 'schema:' "
                f"path, so EnvShield can't tell whether it shares '{schema_path}' "
                "with other services -- and secret scoping depends on exactly "
                f"that. Give '{name}' a 'schema:' path, or remove it with "
                f"'envshield service remove {name}'."
            )
        if os.path.realpath(os.path.join(os.getcwd(), ref)) == target:
            users.append(name)
    return users


def load_bare_schema(path: str = SCHEMA_FILE_NAME) -> Dict[str, Any]:
    """
    Loads a schema file directly by path, with no service registration
    involved at all.

    For a project that hasn't been registered in envshield.yml yet --
    stateless, read-only commands like 'generate' (and 'import', writing a
    fresh schema for the first time) have no real need for the "brain" at
    all: they don't touch a service's local files, so there's no directory
    or path resolution that actually depends on registration existing.
    Requiring it anyway would just be ceremony for ceremony's sake. This is
    the escape hatch for exactly that case -- callers that DO have a
    registered service should still go through load_schema, which is what
    keeps 'check'/'doctor'/'setup' safe from the single-service orphaning
    bug this file's `services`-always-populated design exists to prevent.
    """
    if not os.path.exists(path):
        raise SchemaNotFoundError(
            f"Schema file not found: {path}. Run 'envshield import <file>' to "
            "generate one from an existing config, or 'envshield init'."
        )
    schema = _load_schema_file(path)
    if any(
        isinstance(details, dict) and schema_scope.SCOPE_KEY in details
        for details in schema.values()
    ):
        raise SchemaScopeError(
            path,
            "this schema scopes variables to services ('services = ...'), which "
            "needs envshield.yml to say which services exist. Register the "
            "service(s) with 'envshield service add' and pass --service.",
        )
    _reject_secret_defaults(schema, path)
    return schema


def _reject_secret_defaults(schema: Dict[str, Any], schema_path: str) -> None:
    """
    Refuses a schema where any field is 'secret' and also carries a real
    defaultValue -- see schema_types.secret_default_conflict. Called from
    every entry point that resolves a full (post-extends-merge) schema, so
    every downstream command ('check', 'doctor', 'setup', 'schema sync',
    'generate', 'explain', 'scan') inherits the same refusal instead of
    each re-deciding whether it's safe to echo the field's default -- the
    same reasoning that makes an unsafe schema key name rejected outright
    here rather than left for each generator to notice on its own (see
    schema_manager.sync_schema).
    """
    offending = sorted(
        key
        for key, details in schema.items()
        if schema_types.secret_default_conflict(details)
    )
    if offending:
        raise SecretDefaultConflictError(schema_path, offending)


def load_toml_schema(schema_path: str) -> Dict[str, Any]:
    """Reads and parses one TOML schema file, with friendlier error messages on malformed TOML."""
    try:
        with open(schema_path, "r") as f:
            return toml.load(f)
    except (TypeError, IndexError) as e:
        raise SchemaParseError(schema_path, invalid_toml_structure_message(e))
    except toml.TomlDecodeError as e:
        error_msg = str(e)
        if "already exists" in error_msg:
            dup_key = (
                error_msg.split("What? ")[1].split(" ")[0]
                if "What? " in error_msg
                else "unknown"
            )
            details = f"Duplicate key found: {dup_key}\nCheck your schema file for duplicate [{dup_key}] definitions"
        else:
            details = (
                error_msg.split("(line")[0].strip()
                if "(line" in error_msg
                else error_msg
            )
        raise SchemaParseError(schema_path, details)


def invalid_toml_structure_message(error: Exception) -> str:
    """
    The 'toml' library doesn't always raise TomlDecodeError for invalid
    TOML: a key defined as both a value and a table -- e.g.
    `services = [...]` alongside a `[VAR.services.NAME]` table -- crashes
    it with a bare TypeError/IndexError instead. Turned into a clear
    SchemaParseError rather than an uncaught crash.
    """
    return (
        "invalid TOML structure -- a key is probably defined twice with "
        "incompatible types (e.g. 'services = [...]' together with a "
        "'[VAR.services.NAME]' table; use only the table form when a "
        f"variable needs per-service overrides). ({type(error).__name__})"
    )


def _load_schema_file(
    schema_path: str, _visited: Optional[frozenset] = None
) -> Dict[str, Any]:
    """
    Loads one schema file, resolving and merging any `extends` base
    schema(s) it declares (a string or list of strings, each a path
    relative to *this* schema file's own directory).

    A variable declared in both a base schema and the schema that extends
    it is fully replaced by the child's own definition -- no per-field
    deep-merge -- and later entries in an `extends` list override earlier
    ones on a conflict, same as the child overrides all of them. This
    keeps the merge predictable: whichever definition is "closest" to the
    schema actually being loaded always wins.

    `extends` paths are validated the same way service overrides in
    envshield.yml are (see _ensure_within_project) -- a schema file is just
    as committed-and-shared as envshield.yml, so an unvalidated `extends`
    would be the same supply-chain-style path-traversal risk.
    """
    visited = _visited or frozenset()
    real_path = _check_schema_extends_cycle(schema_path, visited)
    visited = visited | {real_path}

    raw = load_toml_schema(schema_path)
    extends = raw.pop("extends", None)

    merged: Dict[str, Any] = {}
    if extends:
        base_refs = [extends] if isinstance(extends, str) else list(extends)
        base_dir = os.path.dirname(schema_path) or "."
        for base_ref in base_refs:
            base_path = _ensure_within_project(
                os.path.normpath(os.path.join(base_dir, base_ref)),
                f"'extends' reference in '{schema_path}'",
            )
            if not os.path.exists(base_path):
                raise SchemaNotFoundError(
                    f"'{schema_path}' extends '{base_path}', which doesn't exist. "
                    f"Fix the 'extends' path in {schema_path}, or create the missing file."
                )
            merged.update(_load_schema_file(base_path, _visited=visited))

    merged.update(raw)
    return merged


def resolve_field_provenance(
    schema_path: str, _visited: Optional[frozenset] = None
) -> Dict[str, str]:
    """
    Mirrors _load_schema_file's extends-merge exactly (same recursion, same
    circular-extends detection, same within-project path validation), but
    returns {variable_name: contributing_schema_path} instead of the
    merged field values -- which schema file's own declaration of each key
    actually won the merge (whole-field-replace: a variable redeclared
    both in a base schema and the schema that extends it always resolves
    to the *child*, exactly matching _load_schema_file's own last-write-
    wins semantics -- see 'merged.update(raw)' there).

    Additive only: does not change how schemas are loaded or merged
    anywhere else. Used by 'envshield explain' to report where a field
    actually came from -- never guessed at from anything but this same
    resolution path.
    """
    visited = _visited or frozenset()
    real_path = _check_schema_extends_cycle(schema_path, visited)
    visited = visited | {real_path}

    raw = load_toml_schema(schema_path)
    extends = raw.pop("extends", None)

    provenance: Dict[str, str] = {}
    if extends:
        base_refs = [extends] if isinstance(extends, str) else list(extends)
        base_dir = os.path.dirname(schema_path) or "."
        for base_ref in base_refs:
            base_path = _ensure_within_project(
                os.path.normpath(os.path.join(base_dir, base_ref)),
                f"'extends' reference in '{schema_path}'",
            )
            if not os.path.exists(base_path):
                raise SchemaNotFoundError(
                    f"'{schema_path}' extends '{base_path}', which doesn't exist. "
                    f"Fix the 'extends' path in {schema_path}, or create the missing file."
                )
            provenance.update(resolve_field_provenance(base_path, _visited=visited))

    provenance.update({key: schema_path for key in raw.keys()})
    return provenance


def get_services() -> Dict[str, Dict[str, Any]]:
    """
    Returns the services defined in envshield.yml, or an empty dict if the
    project hasn't been initialized yet.

    Example return value:
    {
        "api": {"schema": "services/api/env.schema.toml", "description": "Backend API"},
        "web": {"schema": "services/web/env.schema.toml"},
    }
    """
    config = load_config()
    services = config.get("services")
    return services if isinstance(services, dict) else {}


def get_service_schema_path(service_name: str) -> Optional[str]:
    """
    Returns the schema path for a given service, or None if the service
    doesn't exist.
    """
    services = get_services()
    if service_name not in services:
        return None
    service_config = services[service_name]
    if isinstance(service_config, dict) and "schema" in service_config:
        return _ensure_within_project(
            service_config["schema"], f"service '{service_name}' schema path"
        )
    if isinstance(service_config, dict) and "path" in service_config:
        raise SchemaNotFoundError(
            f"Service '{service_name}' uses the legacy 'path:' key in "
            "envshield.yml, which was renamed to 'schema:' in 4.5.0. "
            "Rename it to 'schema:' to continue."
        )
    return None


def add_service(
    name: str,
    schema_path: str,
    local_file: Optional[str] = None,
    example_file: Optional[str] = None,
    description: Optional[str] = None,
    config_source: Optional[str] = None,
    service_dir: Optional[str] = None,
) -> None:
    """
    Adds (or updates) one service entry in envshield.yml, creating the file
    if it doesn't exist yet. A second call for the same name merges into the
    existing entry -- each field given here overwrites that field, and any
    field left unset (None) keeps whatever the entry already had -- instead
    of replacing the entry outright, matching add_manifest's already-
    additive behavior. Every other top-level key and every other
    already-configured service is left untouched -- this is what makes
    `envshield service discover`/`add` safe to run repeatedly to extend an
    existing setup, not just bootstrap a fresh one.

    A deployment manifest is registered separately, via add_manifest -- it
    maps a container name to a service name and isn't owned by any one
    service entry (the common real shape is one compose file naming several
    services at once, and duplicating "which manifest, which container" on
    every one of them is exactly the kind of topology-vs-meaning drift this
    file exists to avoid).

    `config_source`, when given, records which real file this service's
    schema was originally built from (e.g. 'config/settings.py') -- so a
    later 'init --force' re-scans that same file again instead of
    re-running auto-detection, which could silently pick a different file
    (e.g. a locally-drifted '.env') and regress the schema based on it.

    `service_dir`, when given, is persisted as the service's explicit
    `dir` (see get_service_dir) -- required for every service sharing a
    schema file, optional otherwise.

    Note: envshield.yml is rewritten via a full YAML re-serialization, so
    any hand-written comments in an existing file won't survive.
    """
    schema_path = _ensure_within_project(schema_path, f"service '{name}' schema path")
    if local_file:
        local_file = _ensure_within_project(local_file, f"service '{name}' local_file")
    if example_file:
        example_file = _ensure_within_project(
            example_file, f"service '{name}' example_file"
        )
    if service_dir:
        service_dir = _ensure_within_project(service_dir, f"service '{name}' dir")

    config = load_config()
    services = config.get("services")
    if not isinstance(services, dict):
        services = {}
    config["services"] = services

    existing = services.get(name)
    entry: Dict[str, Any] = dict(existing) if isinstance(existing, dict) else {}
    entry["schema"] = schema_path
    if description:
        entry["description"] = description
    if local_file:
        entry["local_file"] = local_file
    if example_file:
        entry["example_file"] = example_file
    if config_source:
        entry["config_source"] = config_source
    if service_dir:
        entry["dir"] = service_dir
    services[name] = entry

    if (
        entry.get("local_file")
        and not entry.get("example_file")
        and not entry["local_file"].endswith(".py")
        and os.path.basename(entry["local_file"]) != ".env"
    ):
        console.print(
            f"[yellow]Note:[/yellow] service '{name}' overrides local_file to "
            f"'{entry['local_file']}' but not example_file -- the template "
            "will still default to '.env.example' in this service's "
            "directory. If this local_file follows a different naming "
            "convention (e.g. '.env.local' pairs with '.env.local.example'), "
            "set example_file explicitly too, or Template Sync will look at "
            "the wrong file."
        )

    with open(CONFIG_FILE_NAME, "w") as f:
        yaml.dump(config, f, sort_keys=False, indent=2)


def get_service_config_source(name: str) -> Optional[str]:
    """
    Returns the real file this service's schema was originally built from
    (see add_service's `config_source`), or None if it was never recorded
    -- an older envshield.yml, or a schema built from a generic template
    with no real source at all.
    """
    services = get_services()
    entry = services.get(name)
    if not isinstance(entry, dict):
        return None
    return entry.get("config_source")


def get_service_additional_source_roots(name: str) -> List[str]:
    """
    Returns the service's `additional_source_roots` from envshield.yml
    (BL-106) -- extra directories, outside the service's own directory
    (get_service_dir), that source discovery should also cover for
    'undeclared'/'explain'. Empty list if unset -- the default, under
    which only the service's own directory is walked, exactly as before
    this existed.

    The same directory may legitimately be listed by more than one
    service (e.g. a shared internal library imported by several of
    them) -- this returns whatever is configured with no cross-service
    exclusivity check.

    Each entry is validated the same way schema/local_file/example_file
    paths already are (see _ensure_within_project) -- envshield.yml is
    committed, PR-editable, untrusted input, and an unvalidated root here
    would be the same supply-chain-style path-traversal risk. A
    nonexistent root is not rejected here -- it simply contributes no
    files once a caller tries to walk it (see explain.py/
    dependency_snapshot.py), the same as an already-empty directory.
    """
    services = get_services()
    entry = services.get(name)
    if not isinstance(entry, dict):
        return []
    roots = entry.get("additional_source_roots")
    if not isinstance(roots, list):
        return []
    return [
        _ensure_within_project(root, f"service '{name}' additional_source_roots")
        for root in roots
        if isinstance(root, str)
    ]


def get_service_completeness_mode(name: str) -> Optional[str]:
    """
    Returns the service's `completeness` mode from envshield.yml (currently
    only `"union"` is meaningful), or None if unset -- the default, under
    which every registered source (local_file, each deployment manifest)
    must independently satisfy the whole schema, exactly as before this
    existed. Opt-in only (BL-030): never inferred just because a service
    happens to have more than one registered source.
    """
    services = get_services()
    entry = services.get(name)
    if not isinstance(entry, dict):
        return None
    return entry.get("completeness")


def remove_service(name: str) -> None:
    """
    De-registers one service from envshield.yml. Never deletes the
    service's own files (schema, local env file, etc.) -- only the
    registration entry, plus any manifest container mappings that pointed
    at it (a mapping to a service that no longer exists is dead weight, not
    a useful record).
    """
    config = load_config()
    services = config.get("services")
    if not isinstance(services, dict) or name not in services:
        available = (
            ", ".join(sorted(services.keys()))
            if isinstance(services, dict) and services
            else "none registered yet"
        )
        raise SchemaNotFoundError(
            f"Service '{name}' not found in configuration. Available: {available}. "
            "Run 'envshield service list' to check."
        )
    del services[name]

    manifests = config.get("manifests")
    if isinstance(manifests, list):
        for entry in manifests:
            containers = entry.get("containers")
            if isinstance(containers, dict):
                for container_name in [k for k, v in containers.items() if v == name]:
                    del containers[container_name]
        config["manifests"] = [entry for entry in manifests if entry.get("containers")]

    with open(CONFIG_FILE_NAME, "w") as f:
        yaml.dump(config, f, sort_keys=False, indent=2)


def add_manifest(
    file: Optional[str] = None,
    containers: Optional[Dict[str, str]] = None,
    files: Optional[List[str]] = None,
) -> None:
    """
    Registers (or extends) one deployment manifest, mapping its container
    names to already-registered service names. Calling this again for the
    same file(s) merges in whatever new container mappings are given,
    rather than replacing the entry outright -- the same "safe to run
    repeatedly" property add_service has.

    Exactly one of `file` (a single manifest) or `files` (BL-025: an
    ordered list of Docker Compose base+override layers -- earlier entries
    are base layers, later ones override them) must be given.
    """
    containers = containers or {}
    if (file is None) == (files is None):
        raise InvalidManifestDefinitionError(
            "add_manifest requires exactly one of 'file' or 'files', not "
            "both and not neither."
        )

    if files is not None:
        if not isinstance(files, list) or not files:
            raise InvalidManifestDefinitionError(
                "'files' must be a non-empty list of Compose layer file paths."
            )
        resolved = [
            _ensure_within_project(f, "deployment manifest path") for f in files
        ]
        match_key, match_value, entry_fields = (
            "files",
            resolved,
            {"files": resolved},
        )
    else:
        resolved_file = _ensure_within_project(file, "deployment manifest path")
        match_key, match_value, entry_fields = (
            "file",
            resolved_file,
            {"file": resolved_file},
        )

    config = load_config()
    manifests = config.get("manifests")
    if not isinstance(manifests, list):
        manifests = []
    config["manifests"] = manifests

    for entry in manifests:
        if entry.get(match_key) == match_value:
            existing = entry.get("containers")
            entry["containers"] = {
                **(existing if isinstance(existing, dict) else {}),
                **containers,
            }
            break
    else:
        manifests.append({**entry_fields, "containers": dict(containers)})

    with open(CONFIG_FILE_NAME, "w") as f:
        yaml.dump(config, f, sort_keys=False, indent=2)


def _resolve_manifest_entry_paths(entry: Dict[str, Any]) -> Optional[List[str]]:
    """
    Normalizes one 'manifests:' entry's 'file'/'files' key into an ordered
    list of validated, within-project paths -- 'file' becomes a single-
    element list, 'files' (BL-025) is used as declared. Returns None for an
    entry with neither key (nothing to validate, nothing to register --
    matches the pre-BL-025 behavior of silently skipping such an entry).

    Raises InvalidManifestDefinitionError for a malformed entry: both keys
    given at once, or 'files' present but not a non-empty list -- a clear,
    fail-fast error rather than silently picking one key or the other.
    """
    has_file = bool(entry.get("file"))
    has_files = "files" in entry and entry.get("files") is not None
    if has_file and has_files:
        raise InvalidManifestDefinitionError(
            "A 'manifests:' entry cannot declare both 'file' and 'files' -- "
            "use 'file' for a single manifest, or 'files' for an ordered "
            "list of Docker Compose base+override layers (BL-025), not both."
        )
    if has_files:
        files = entry["files"]
        if not isinstance(files, list) or not files:
            raise InvalidManifestDefinitionError(
                "A 'manifests:' entry's 'files' must be a non-empty list of file paths."
            )
        return [_ensure_within_project(f, f"deployment manifest '{f}'") for f in files]
    if has_file:
        file = entry["file"]
        return [_ensure_within_project(file, f"deployment manifest '{file}'")]
    return None


def _manifest_display_path(paths: List[str]) -> str:
    """
    A single, human-readable label for a (possibly multi-file) manifest --
    used anywhere a message names "the manifest" as one thing (e.g. "'X' is
    in sync with schema"), so a base+override pair reads as the one logical
    manifest BL-025 makes it, never as if either file alone were expected to
    be self-sufficient.
    """
    return paths[0] if len(paths) == 1 else " + ".join(paths)


def get_deployment_manifests(service_name: str) -> List[Dict[str, Any]]:
    """
    Returns every registered deployment manifest that maps one of its
    containers to `service_name`, as
    [{"path": ..., "paths": [...], "container": ...}, ...].

    "paths" is always a list (one element for a single-file manifest,
    ordered base+override layers for BL-025's "files:" form) -- the one
    representation every caller (check/doctor/explain) parses through, via
    parsers.factory.get_manifest_parser_and_vars. "path" is a single,
    human-readable label (see _manifest_display_path) kept for existing
    display/error-message call sites that only ever expected one string.

    A service can legitimately show up in more than one manifest (a local
    docker-compose.yml and a production Kubernetes manifest, say), so this
    returns a list rather than assuming at most one -- callers that only
    ever expect zero-or-one should just take the first element.
    """
    config = load_config()
    manifests = config.get("manifests")
    if not isinstance(manifests, list):
        return []

    results = []
    for entry in manifests:
        containers = entry.get("containers")
        if not isinstance(containers, dict):
            continue
        paths = _resolve_manifest_entry_paths(entry)
        if paths is None:
            continue
        for container_name, mapped_service in containers.items():
            if mapped_service == service_name:
                results.append(
                    {
                        "path": _manifest_display_path(paths),
                        "paths": paths,
                        "container": container_name,
                    }
                )
    return results


def get_service_dir(service_name: str) -> str:
    """
    Returns the service's root directory -- the one EnvShield treats as
    "this service's code" throughout (env file defaults, discovery, scan
    routing, invocation-dir inference).

    An explicit `dir` in envshield.yml always wins. Without one, a schema
    used by exactly one service falls back to the directory that schema
    lives in (e.g. 'alpha' for 'alpha/env.schema.toml') -- the original
    behavior, unchanged. A schema shared by several services never falls
    back: every sharing service would get the same directory (typically the
    project root), silently claiming each other's code -- so a missing
    `dir` there is a ServiceConfigError instead.

    Raises SchemaNotFoundError if the service isn't declared in
    envshield.yml.
    """
    schema_path = get_service_schema_path(service_name)
    if not schema_path:
        raise SchemaNotFoundError(
            f"Service '{service_name}' not found in configuration."
        )
    entry = get_services()[service_name]
    if "dir" in entry:
        explicit = entry["dir"]
        if not isinstance(explicit, str) or not explicit:
            raise ServiceConfigError(
                f"Service '{service_name}' has an invalid 'dir:' in "
                f"{CONFIG_FILE_NAME} -- it must be a directory path relative to "
                "the project root (use '.' for the root itself)."
            )
        return os.path.normpath(
            _ensure_within_project(explicit, f"service '{service_name}' dir")
        )

    users = get_schema_users(schema_path)
    if len(users) > 1:
        raise ServiceConfigError(
            f"Services {', '.join(users)} share '{schema_path}', so EnvShield "
            f"can't tell their code apart by where the schema lives. Add 'dir:' "
            f"to '{service_name}' (and every service sharing that schema) in "
            f"{CONFIG_FILE_NAME} -- e.g. 'envshield service add {service_name} "
            f"<directory> --schema {schema_path}'."
        )
    return os.path.dirname(schema_path) or "."


def normalize_path_for_service_match(file_path: str) -> str:
    """
    Best-effort normalization to a cwd-relative path, for comparing a file
    (or a service's own directory, from get_service_dir) against another
    such path. Extracted from scanner.py's per-service undeclared-variable
    router -- mechanical move, not a rewrite -- so this same normalization
    isn't duplicated by every caller that needs to know "is this file
    inside that service's directory."
    """
    if os.path.isabs(file_path):
        try:
            file_path = os.path.relpath(file_path, os.getcwd())
        except ValueError:
            pass
    return os.path.normpath(file_path)


def service_dir_contains(file_path: str, service_dir: str) -> bool:
    """
    Whether `file_path` (absolute or relative) falls inside `service_dir`
    (as returned by get_service_dir, normalized or not -- this normalizes
    both sides itself). A service_dir of '.' (the project root) matches
    every path -- the single-service catch-all, since there's no separate
    "root schema" concept.

    This is a lexical, cwd-relative containment check for routing an
    already-discovered file to the right service's schema -- not a
    security boundary, and deliberately not realpath-based. See
    _ensure_within_project for the security check that validates an
    untrusted envshield.yml path instead.
    """
    normalized_file = normalize_path_for_service_match(file_path)
    normalized_dir = normalize_path_for_service_match(service_dir)
    return (
        normalized_dir == "."
        or normalized_file == normalized_dir
        or normalized_file.startswith(normalized_dir + os.sep)
    )


def get_env_paths(service_name: str) -> Dict[str, str]:
    """
    Resolves the 'template' (tracked, e.g. '.env.example') and 'local' (real,
    per-developer, e.g. '.env') environment file paths for a service.

    These default to '.env.example' / '.env' inside the service's own
    directory (the directory its schema lives in) -- for a single-service
    project, that directory is the project root itself, so this looks
    exactly like EnvShield's original single-project behaviour; nothing
    moves just because the project is technically "a service" now.

    Either can be overridden via 'example_file' / 'local_file' in
    envshield.yml. An override is required whenever the local config isn't a
    dotenv file at all -- e.g. a Python module such as `env_config.local.py`
    in a Flask project. EnvShield picks its reader/writer by the file's
    extension (see parsers.factory.get_parser), so a '.py' override is enough
    to make 'sync' and 'setup' treat it as source code instead of a dotenv
    file: they patch/append plain assignments in place rather than
    regenerating the file wholesale.
    """
    service_dir = get_service_dir(service_name)
    service_config = get_services().get(service_name)
    if not isinstance(service_config, dict):
        service_config = {}

    def _resolve(override_key: str, default_name: str) -> str:
        override = service_config.get(override_key)
        if override:
            return _ensure_within_project(
                override, f"service '{service_name}' {override_key}"
            )
        return (
            default_name
            if service_dir == "."
            else os.path.join(service_dir, default_name)
        )

    return {
        "example_file": _resolve("example_file", ".env.example"),
        "local_file": _resolve("local_file", ".env"),
    }


def same_physical_file(a: str, b: str) -> bool:
    cwd = os.getcwd()
    return os.path.realpath(os.path.join(cwd, a)) == os.path.realpath(
        os.path.join(cwd, b)
    )


_ENV_FILE_DEFAULTS = {"local_file": ".env", "example_file": ".env.example"}


def _unresolved_peer_path(entry: Any, key: str) -> Optional[str]:
    """
    Where a service whose paths can't be resolved (e.g. a shared-schema
    service with no `dir` yet) could have its `key` file: its own override
    if it sets one (that doesn't depend on its directory), otherwise the
    default file beside its schema -- where EnvShield resolved it before a
    `dir` became required. None when there's nothing to anchor it to.
    """
    if not isinstance(entry, dict):
        return None
    override = entry.get(key)
    if override:
        return override if isinstance(override, str) else None
    schema = entry.get("schema", entry.get("path"))
    if not isinstance(schema, str) or not schema:
        return None
    return os.path.join(os.path.dirname(schema), _ENV_FILE_DEFAULTS[key])


def get_file_peers(service_name: str, key: str) -> List[str]:
    """
    Every registered service whose `key` file ('local_file' or
    'example_file', resolved via get_env_paths) is the same physical file
    as `service_name`'s -- compared by resolved real path -- sorted by name,
    `service_name` included. The two keys are resolved independently:
    sharing '.env' says nothing about sharing '.env.example'.

    A service whose paths can't be resolved is skipped only when it
    provably can't use this file (see _unresolved_peer_path); otherwise
    this raises ServiceConfigError -- dropping a real peer would narrow
    the file's contract, and a write could then discard its variables.
    """
    target = get_env_paths(service_name)[key]
    peers = []
    for name, entry in get_services().items():
        try:
            path = get_env_paths(name)[key]
        except EnvShieldException as e:
            candidate = _unresolved_peer_path(entry, key)
            if candidate is not None and not same_physical_file(candidate, target):
                continue
            raise ServiceConfigError(
                f"Can't tell whether service '{name}' also uses '{target}' "
                f"({e}). Fix '{name}' in {CONFIG_FILE_NAME} first -- changing "
                "a file it may share could drop its variables."
            ) from e
        if same_physical_file(path, target):
            peers.append(name)
    return sorted(peers)


def load_file_contract(service_name: str, key: str) -> Dict[str, Any]:
    """
    The physical-file contract for `service_name`'s `key` file: what that
    file must be able to hold. For a file only this service uses, exactly
    load_schema(service_name). For a file several services share, the
    union of their projections (schema_scope.union_fields), raising
    FileContractConflictError if they define a shared variable differently.

    For materializing or validating the file itself -- never as what
    `service_name` is granted; that's load_schema.
    """
    peers = get_file_peers(service_name, key)
    if len(peers) == 1:
        return load_schema(service_name)
    return schema_scope.union_fields(
        [load_schema_view(peer) for peer in peers], get_env_paths(service_name)[key]
    )


def get_file_contract_vars(service_name: str, file_path: str) -> set:
    """
    Every variable `file_path` may legitimately hold when validated for
    `service_name`: the union of every sharing service's projected names if
    it's that service's (shared) local_file or example_file, otherwise just
    the service's own. Names only -- never raises a definition conflict,
    since validating one service's requirements doesn't need a merged
    definition.
    """
    if service_name not in get_services():
        return set(load_schema(service_name))  # unregistered: no file peers
    paths = get_env_paths(service_name)
    for key in ("local_file", "example_file"):
        if same_physical_file(file_path, paths[key]):
            peers = get_file_peers(service_name, key)
            if len(peers) > 1:
                return set().union(*(load_schema(peer) for peer in peers))
    return set(load_schema(service_name))


def generate_default_config_content(
    project_name: str,
    service_name: str,
    schema_path: str = SCHEMA_FILE_NAME,
    deployment_manifest: Optional[str] = None,
    container: Optional[str] = None,
    config_source: Optional[str] = None,
) -> str:
    """
    Generates the YAML content for a default envshield.yml configuration
    file.

    Always registers exactly one service (`service_name`), even for a
    brand-new single-service project -- there is no separate "rootless"
    shape. Growing from one service to several is then just appending
    another entry to the same `services` map, not a structural migration.

    `deployment_manifest`, when given (a docker-compose file auto-detected
    at the project root -- see service_discovery.find_compose_file), is
    registered up front under `manifests` so `doctor`/`check` validate it
    automatically from the very first run, with no separate opt-in step.

    `config_source`, when given, records which real file the schema was
    built from -- see add_service's docstring for why a later 'init
    --force' needs to remember this instead of re-detecting it.
    """
    service_entry: Dict[str, Any] = {"schema": schema_path}
    if config_source:
        service_entry["config_source"] = config_source
    config_data: Dict[str, Any] = {
        "project_name": project_name,
        "services": {
            service_name: service_entry,
        },
        "secret_scanning": {
            "exclude_files": [
                "**/tests/*",
                "**/test/*",
            ],
        },
    }
    if deployment_manifest:
        config_data["manifests"] = [
            {
                "file": deployment_manifest,
                "containers": {container or service_name: service_name},
            }
        ]
    header = "# EnvShield Configuration File\n# This file manages your project's security settings.\n\n"
    return header + yaml.dump(config_data, sort_keys=False, indent=2)


def get_framework_schema(project_type: Optional[str]) -> dict:
    """Returns a dictionary of common variables for a given framework."""
    if project_type == "nextjs":
        return {
            "DATABASE_URL": {
                "description": "Database connection string.",
                "secret": True,
            },
            "NEXTAUTH_SECRET": {
                "description": "A secret for NextAuth session signing.",
                "secret": True,
            },
            "NEXT_PUBLIC_API_URL": {
                "description": "Public URL for the frontend to call the API.",
                "secret": False,
            },
        }

    if project_type == "python-django":
        return {
            "SECRET_KEY": {
                "description": "Django's secret key for cryptographic signing.",
                "secret": True,
            },
            "DEBUG": {
                "description": "Django's debug mode.",
                "secret": False,
                "defaultValue": "True",
            },
            "DATABASE_URL": {
                "description": "Database connection string (e.g., dj-database-url).",
                "secret": True,
            },
            "ALLOWED_HOSTS": {
                "description": "A comma-separated list of allowed hostnames.",
                "secret": False,
                "defaultValue": "localhost,127.0.0.1",
            },
        }

    if project_type == "python-flask" or project_type == "python":
        return {
            "SECRET_KEY": {
                "description": "Flask's secret key for signing sessions.",
                "secret": True,
            },
            "FLASK_ENV": {
                "description": "The environment for Flask (e.g., development, production).",
                "secret": False,
                "defaultValue": "development",
            },
            "DATABASE_URL": {
                "description": "Database connection string.",
                "secret": True,
            },
        }

    # Default for all other project types
    return {
        "DATABASE_URL": {
            "description": "The full connection string for the database.",
            "secret": True,
        },
        "LOG_LEVEL": {
            "description": "Controls the log verbosity.",
            "secret": False,
            "defaultValue": "info",
        },
    }


def generate_default_schema_content(project_type: Optional[str]) -> str:
    """Generates TOML content for a default env.schema.toml."""
    header = (
        "# Welcome to your EnvShield Schema!\n"
        "# This is the single source of truth for your project's environment variables.\n\n"
        "# The 'secret' flag marks a variable as sensitive.\n"
        "# In future versions, commands like 'onboard' will use this flag to know\n"
        "# which variables to securely prompt for.\n\n"
    )

    schema_dict = get_framework_schema(project_type)
    return header + toml.dumps(schema_dict)


def update_gitignore():
    """Appends EnvShield patterns to the project's .gitignore file if they don't exist."""
    # '.env' comes first and matters most: it's the actual secrets file every
    # other command assumes is never committed. The '.local'/'.envshield'
    # variants are for per-developer overrides and EnvShield's own state.
    patterns_to_add = [
        ".env",
        ".env.local",
        ".env.*.local",
        ".envshield/",
    ]

    try:
        existing_content = ""
        if os.path.exists(GITIGNORE_FILE_NAME):
            with open(GITIGNORE_FILE_NAME, "r") as f:
                existing_content = f.read()

        # Check each pattern independently -- a project that already has
        # '.env.local' ignored (e.g. from an older EnvShield version) should
        # still get '.env' added, not have the whole update skipped.
        existing_lines = {line.strip() for line in existing_content.splitlines()}
        missing_patterns = [p for p in patterns_to_add if p not in existing_lines]

        if not missing_patterns:
            console.print(
                f"[dim]'{GITIGNORE_FILE_NAME}' already contains EnvShield patterns. Skipping.[/dim]"
            )
            return

        with open(GITIGNORE_FILE_NAME, "a") as f:
            f.write("\n# EnvShield Files\n")
            for pattern in missing_patterns:
                f.write(pattern + "\n")

        console.print(
            f"[bold green]✓[/bold green] Updated [bold cyan]{GITIGNORE_FILE_NAME}[/bold cyan] with EnvShield patterns."
        )
    except IOError as e:
        console.print(f"[bold red]Error:[/bold red] Could not update .gitignore: {e}")


def write_file(file_name: str, content: str, success_message: str):
    """Generic file writing function."""
    try:
        with open(file_name, "w") as f:
            f.write(content)
        console.print(f"[bold green]✓[/bold green] {success_message}")
    except IOError as e:
        console.print(f"[bold red]Error:[/bold red] Failed to write {file_name}: {e}")
        raise
