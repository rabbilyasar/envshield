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
from ..parsers.factory import get_manifest_parser_and_vars
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


def _discoverable_files(root_dir: str) -> List[str]:
    """
    Every .py/.js/.jsx/.ts/.tsx file under `root_dir` -- symlink-safe,
    excluded-dir-pruned, mirroring scanner.py's own file-collection walk.
    Always the live working tree (like 'doctor'/'check'), never a Git
    revision -- a developer explaining one variable right now wants the
    current state, not history (that's 'undeclared'/'schema diff's job).

    `root_dir` is any directory to walk -- a service's own directory, or
    (BL-106) one of its `additional_source_roots`; this function has no
    concept of "the" service directory, it just walks what it's given.
    """
    if os.path.islink(root_dir):
        return []
    files: List[str] = []
    for root, dirs, filenames in os.walk(root_dir):
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
    roots: List[str], variable: str
) -> List[discovery.DiscoveredVariableUsage]:
    """
    Walks every directory in `roots` (a service's own directory plus any
    BL-106 `additional_source_roots`) and returns usages of `variable`
    found under any of them. A file reachable through more than one root
    -- an additional root nested inside, or equal to, the service's own
    directory -- is only read and reported once: `_discoverable_files`
    already normalizes each file's path (os.path.normpath), so the same
    file discovered via two roots produces an identical string, and
    `seen_files` collapses it to a single entry rather than a duplicated
    "used in source" row.
    """
    usages: List[discovery.DiscoveredVariableUsage] = []
    seen_files: set = set()
    for root_dir in roots:
        for file_path in _discoverable_files(os.path.normpath(root_dir)):
            if file_path in seen_files:
                continue
            seen_files.add(file_path)
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
        try:
            parser, declared_vars = get_manifest_parser_and_vars(
                manifest["paths"], container=container, get_values=False
            )
        except (FileNotFoundError, OSError, ValueError, EnvShieldException) as e:
            references.append(
                ManifestReference(
                    path=path, container=container, status="error", detail=str(e)
                )
            )
            continue
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


@dataclass
class UndeclaredVariableReport:
    """
    Built by build_undeclared_report when `variable` isn't in the service's
    schema -- lets 'explain' degrade gracefully instead of only erroring,
    for exactly the workflow moment it's most needed: a developer just
    found this variable via 'undeclared'/'scan' and wants to understand it
    before deciding whether/how to add it to the schema. Reuses the exact
    same _discover_current_usages/_manifest_references machinery
    build_report uses for a declared variable -- no second discovery
    implementation.
    """

    variable: str
    service: str
    source_usages: List[discovery.DiscoveredVariableUsage]
    manifest_references: List[ManifestReference]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "variable": self.variable,
            "service": self.service,
            "found": False,
            "declared": False,
            "source_usages": [u.to_dict() for u in self.source_usages],
            "manifest_references": [m.to_dict() for m in self.manifest_references],
        }


def build_undeclared_report(
    variable: str, service_name: str
) -> UndeclaredVariableReport:
    """
    The undeclared-variable counterpart to build_report -- called by the
    CLI specifically when build_report has already raised
    VariableNotFoundError, not a general-purpose alternative entry point.
    Never raises VariableNotFoundError itself; any other EnvShieldException
    (e.g. a missing/unreadable envshield.yml) still propagates unchanged,
    matching build_report's own contract.
    """
    service_dir = config_manager.get_service_dir(service_name)
    additional_roots = config_manager.get_service_additional_source_roots(service_name)
    manifests = config_manager.get_deployment_manifests(service_name)
    return UndeclaredVariableReport(
        variable=variable,
        service=service_name,
        source_usages=_discover_current_usages(
            [service_dir] + additional_roots, variable
        ),
        manifest_references=_manifest_references(manifests, variable),
    )


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
    The one public entry point for a *declared* variable: aggregates every
    relationship EnvShield can currently prove about `variable` within
    `service_name`, the same way 'doctor' orchestrates config_manager/
    parsers.factory directly rather than through an injected/pre-loaded
    layer.

    Raises VariableNotFoundError if `variable` isn't declared in the
    service's resolved schema -- this function itself never returns an
    empty/placeholder report for a variable that doesn't exist; see
    build_undeclared_report for the CLI's graceful-degradation path in
    that case, a distinct report type, not a relaxation of this one's own
    contract. Any other EnvShieldException (missing schema, unreadable
    schema file, etc.) propagates unchanged -- the same "found or a clear
    error, never a silent empty result" contract every other command
    already follows.
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
    additional_roots = config_manager.get_service_additional_source_roots(service_name)
    manifests = config_manager.get_deployment_manifests(service_name)

    return ExplainReport(
        variable=variable,
        service=service_name,
        schema=_describe_field(field_schema),
        provenance=provenance,
        required_by=_reverse_required_by(schema, variable),
        source_usages=_discover_current_usages(
            [service_dir] + additional_roots, variable
        ),
        manifest_references=_manifest_references(manifests, variable),
    )
