# envshield/core/explain.py
"""
'envshield explain <VAR>': a single-variable evidence report.

Aggregates relationship data EnvShield already computes elsewhere -- the
schema's own declared fields, extends provenance (config_manager.
resolve_field_provenance), source-code discovery (discovery.py, Phase 2B,
unmodified), and registered deployment manifests (parsers/factory) -- into
one report about exactly one named variable. No new discovery capability,
no persistent graph, no new schema fields, no cross-service reconciliation.

Every section distinguishes three states, and must never conflate them:
  - EnvShield found this relationship (positive evidence).
  - EnvShield looked, within its own known boundary, and found nothing
    (absence of evidence -- never presented as proof the thing doesn't
    exist elsewhere, in an unsupported language, or via an unrecognized
    access pattern).
  - EnvShield cannot determine this at all (e.g. extends-provenance
    resolution itself failed) -- reported as such, not guessed at.
"""

import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from ..config import manager as config_manager
from ..parsers.factory import get_parser
from . import discovery, schema_types
from .exceptions import EnvShieldException, VariableNotFoundError

# Mirrors scanner.py's DEFAULT_EXCLUDED_DIRS / _is_default_excluded_dir and
# dependency_snapshot.py's _open_disk_source exactly, duplicated rather than
# imported -- the same reason dependency_snapshot.py's own docstring gives
# for duplicating scanner.py's _open_for_scan: a sibling module with its
# own CLI-adjacent dependencies this one has no other reason to pull in.
from .scanner import DEFAULT_EXCLUDED_DIRS

_PYTHON_SUFFIXES = (".py",)
_JS_SUFFIXES = (".js", ".jsx", ".ts", ".tsx")
_DISCOVERABLE_SUFFIXES = _PYTHON_SUFFIXES + _JS_SUFFIXES

_CAN_USE_O_NOFOLLOW = hasattr(os, "O_NOFOLLOW")


def _is_default_excluded_dir(dirname: str) -> bool:
    return (
        dirname in DEFAULT_EXCLUDED_DIRS
        or dirname.endswith(".egg-info")
        or dirname.endswith(".dist-info")
    )


def _open_disk_source(path: str):
    """Symlink-protected open -- see scanner.py's _open_for_scan / dependency_snapshot.py's _open_disk_source, mirrored exactly."""
    flags = os.O_RDONLY
    if _CAN_USE_O_NOFOLLOW:
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags)
    return os.fdopen(fd, "r", encoding="utf-8", errors="ignore")


def _discoverable_files(service_dir: str) -> List[str]:
    """
    Every .py/.js/.jsx/.ts/.tsx file under `service_dir` -- symlink-safe,
    excluded-dir-pruned, mirroring scanner.py's own file-collection walk.
    Always the live working tree (like 'doctor'/'check'), never a Git
    revision -- a developer explaining one variable right now wants the
    current state, not history (that's 'undeclared'/'schema diff's job).
    """
    if os.path.islink(service_dir):
        return []
    files: List[str] = []
    for root, dirs, filenames in os.walk(service_dir):
        dirs[:] = [d for d in dirs if not _is_default_excluded_dir(d)]
        for filename in filenames:
            if not filename.endswith(_DISCOVERABLE_SUFFIXES):
                continue
            file_path = os.path.join(root, filename)
            if os.path.islink(file_path):
                continue
            # normpath rather than the raw os.walk join -- a project-root
            # service_dir of "." would otherwise report every usage's
            # file_path with a "./" prefix, unlike every other path this
            # command shows (schema_path, manifest paths) or unlike what
            # 'undeclared' reports for the same file via git.
            files.append(os.path.normpath(file_path))
    return files


def _discover_current_usages(
    service_dir: str, variable: str
) -> List[discovery.DiscoveredVariableUsage]:
    usages: List[discovery.DiscoveredVariableUsage] = []
    for file_path in _discoverable_files(service_dir):
        try:
            with _open_disk_source(file_path) as f:
                content = f.read()
        except (IOError, OSError):
            continue
        if file_path.endswith(_PYTHON_SUFFIXES):
            found = discovery.discover_python_usages(content, file_path)
        else:
            found = discovery.discover_js_usages(content, file_path)
        usages.extend(u for u in found if u.variable == variable)
    return usages


@dataclass
class ManifestReference:
    path: str
    container: Optional[str]
    status: str  # "declared" | "not_declared" | "unresolved" | "error"
    detail: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "container": self.container,
            "status": self.status,
            "detail": self.detail,
        }


def _manifest_references(
    manifests: List[Dict[str, Any]], variable: str
) -> List[ManifestReference]:
    references: List[ManifestReference] = []
    for manifest in manifests:
        path, container = manifest["path"], manifest.get("container")
        parser = get_parser(path, container=container)
        if not parser:
            references.append(
                ManifestReference(
                    path=path,
                    container=container,
                    status="error",
                    detail=f"No parser available for '{path}'.",
                )
            )
            continue
        try:
            declared_vars = parser.get_vars(path, get_values=False)
        except (FileNotFoundError, OSError, ValueError) as e:
            references.append(
                ManifestReference(
                    path=path, container=container, status="error", detail=str(e)
                )
            )
            continue
        if variable in declared_vars:
            status = "declared"
        elif parser.has_unresolved_source:
            # Not found directly, but this manifest also references an
            # external ConfigMap/Secret EnvShield can't inspect -- "not
            # declared" would be a confident claim of absence this parser
            # can't actually back up, the exact false negative this
            # command's docstring already promises never to make.
            status = "unresolved"
        else:
            status = "not_declared"
        references.append(
            ManifestReference(path=path, container=container, status=status)
        )
    return references


def _describe_field(field_schema: Dict[str, Any]) -> Dict[str, Any]:
    """
    The schema-declarative facts about one field -- never evaluated
    against any ambient local values (that's 'check'/'doctor's job,
    against one specific file's actual content). 'requiredness' is
    'optional' (has a defaultValue -- schema_types.is_required_now's own
    precedence: a default always wins, regardless of requiredIf),
    'conditional' (no default, has requiredIf), or 'required'
    (unconditional) -- describing the contract, not resolving it.
    """
    if "defaultValue" in field_schema:
        requiredness = "optional"
    elif field_schema.get("requiredIf"):
        requiredness = "conditional"
    else:
        requiredness = "required"

    is_secret = bool(field_schema.get("secret", False))
    # A secret field's default is never surfaced here, even though
    # config_manager.load_schema already refuses a schema with a real
    # defaultValue on a secret field (defense in depth for a field_schema
    # dict that reached this function some other way) -- 'requiredness'
    # above still reflects a default's presence without echoing its value.
    default_value = None if is_secret else field_schema.get("defaultValue")

    return {
        "type": schema_types.resolve_field_type(field_schema),
        "requiredness": requiredness,
        "requiredIf": field_schema.get("requiredIf"),
        "default": default_value,
        "secret": is_secret,
        "enum": schema_types.enum_values(field_schema) or None,
        "pattern": field_schema.get("pattern"),
        "description": field_schema.get("description"),
    }


def _reverse_required_by(schema: Dict[str, Any], variable: str) -> List[Dict[str, Any]]:
    return [
        {"variable": key, "condition": other["requiredIf"]}
        for key, other in schema.items()
        if key != variable
        and isinstance(other.get("requiredIf"), dict)
        and other["requiredIf"].get("var") == variable
    ]


@dataclass
class ExplainReport:
    variable: str
    service: str
    schema: Dict[str, Any]
    provenance: Dict[str, Any]
    required_by: List[Dict[str, Any]]
    source_usages: List[discovery.DiscoveredVariableUsage]
    manifest_references: List[ManifestReference]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "variable": self.variable,
            "service": self.service,
            "found": True,
            "schema": self.schema,
            "provenance": self.provenance,
            "dependencies": {
                "required_if": self.schema.get("requiredIf"),
                "required_by": self.required_by,
            },
            "source_usages": [u.to_dict() for u in self.source_usages],
            "manifest_references": [m.to_dict() for m in self.manifest_references],
        }


def error_dict(variable: str, service: Optional[str], message: str) -> Dict[str, Any]:
    """
    The canonical 'explain --json' error shape -- matches
    schema_manager.check_result's own convention exactly: keep the
    identity keys (variable, service) and a boolean status key (found),
    and omit every data key entirely rather than null them out. Mirrors
    check_result's error return (which omits missing/blank/invalid/extra)
    and schema_diff's early-error paths (which omit 'results') -- the same
    single pattern this codebase already uses everywhere else, not a
    bespoke shape invented for this command.
    """
    return {"variable": variable, "service": service, "found": False, "error": message}


def build_report(variable: str, service_name: str) -> ExplainReport:
    """
    The one public entry point: aggregates every relationship EnvShield can
    currently prove about `variable` within `service_name`, the same way
    'doctor' orchestrates config_manager/parsers.factory directly rather
    than through an injected/pre-loaded layer.

    Raises VariableNotFoundError if `variable` isn't declared in the
    service's resolved schema -- never returns an empty/placeholder report
    for a variable that doesn't exist. Any other EnvShieldException
    (missing schema, unreadable schema file, etc.) propagates unchanged --
    the same "found or a clear error, never a silent empty result" contract
    every other command already follows.
    """
    schema = config_manager.load_schema(service_name=service_name)
    if variable not in schema:
        raise VariableNotFoundError(variable, service_name)
    field_schema = schema[variable]

    schema_path = config_manager.get_service_schema_path(service_name)
    provenance: Dict[str, Any] = {
        "schema_path": schema_path,
        "declared_in": None,
        "inherited": None,
    }
    try:
        contributing = config_manager.resolve_field_provenance(schema_path).get(
            variable
        )
    except EnvShieldException:
        contributing = None
    if contributing:
        provenance["declared_in"] = contributing
        provenance["inherited"] = contributing != schema_path

    service_dir = config_manager.get_service_dir(service_name)
    manifests = config_manager.get_deployment_manifests(service_name)

    return ExplainReport(
        variable=variable,
        service=service_name,
        schema=_describe_field(field_schema),
        provenance=provenance,
        required_by=_reverse_required_by(schema, variable),
        source_usages=_discover_current_usages(service_dir, variable),
        manifest_references=_manifest_references(manifests, variable),
    )
