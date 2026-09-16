#!/usr/bin/env python3
"""
Product-validation prototype: "This configuration contract changed. Which
services are demonstrably affected, and what evidence proves it?"

NOT production code. Lives outside the `envshield` package on purpose --
not installed, not registered as a CLI command, not part of
envshield/core's architecture. Every capability it uses (schema diff,
revision-aware schema loading, extends provenance, source discovery,
deployment-manifest parsing) is an already-shipped EnvShield domain
function, called directly -- no new discovery/parsing/diff logic, no
persistent graph, no database, no new dependency.

Correctness rule this prototype exists to enforce: two services sharing an
environment-variable *name* is never, by itself, evidence of a
relationship. A service is only reported as "transitively affected" when
EnvShield's own data model can prove a structural link -- shared `extends`
schema provenance, or the same deployment manifest wiring both services to
the same variable. Everything else that merely shares the name is reported
as "observed_unknown", explicitly not "affected". And a service is never
silently omitted because a lookup failed -- a failed evaluation is reported
as "evaluation_unknown" with the (content-free) reason, never treated as
"no relationship found".
"""

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

from envshield.config import manager as config_manager
from envshield.core import contract_diff, discovery, schema_snapshot
from envshield.core.exceptions import EnvShieldException
from envshield.parsers.factory import get_parser

_PYTHON_SUFFIXES = (".py",)
_JS_SUFFIXES = (".js", ".jsx", ".ts", ".tsx")
_DISCOVERABLE_SUFFIXES = _PYTHON_SUFFIXES + _JS_SUFFIXES

# A prototype-scoped subset of scanner.py's DEFAULT_EXCLUDED_DIRS -- not
# imported (see the architecture note in the implementation plan: a fourth,
# deliberate duplication of the small file-walk pattern scanner.py/
# dependency_snapshot.py/explain.py already each have their own copy of).
_EXCLUDED_DIRS = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "dist",
    "build",
    ".mypy_cache",
    ".pytest_cache",
}

Evidence = Dict[str, Any]
ErrorList = List[str]


# --- Evidence-gathering primitives -----------------------------------------
# Each returns (result, errors). A non-empty `errors` list means the check
# could not be completed -- it must never be conflated with "checked and
# found nothing". Every message is deliberately content-free: for anything
# that could have parsed real file/schema content (TOML, YAML, manifest
# formats not re-verified this session for safe exception text), only the
# exception's type name and a hardcoded description are recorded, never
# str(exception). Only pure OS-level errors (a path not existing, a
# permission bit) -- which describe filesystem facts, not parsed content --
# include the exception's own message.


def _schema_shared_provenance(
    changed_service: str, other_service: str, variable: str
) -> Tuple[Optional[str], ErrorList]:
    """
    Returns the contributing schema path if `variable` resolves, via
    `extends`, to the exact same declaration in both services -- proof
    they're sharing one contract, not two independent authors who happened
    to choose the same field name.
    """
    errors: ErrorList = []
    other_path = config_manager.get_service_schema_path(other_service)
    if not other_path:
        return (
            None,
            errors,
        )  # no schema at all for this service -- not an error, just no evidence here

    changed_path = config_manager.get_service_schema_path(changed_service)
    if not changed_path:
        errors.append(
            f"changed service '{changed_service}' has no resolvable schema path"
        )
        return None, errors

    try:
        changed_provenance = config_manager.resolve_field_provenance(changed_path)
    except EnvShieldException as e:
        errors.append(
            f"{type(e).__name__}: could not resolve schema provenance for "
            f"'{changed_service}'"
        )
        return None, errors

    try:
        other_provenance = config_manager.resolve_field_provenance(other_path)
    except EnvShieldException as e:
        errors.append(
            f"{type(e).__name__}: could not resolve schema provenance for "
            f"'{other_service}'"
        )
        return None, errors

    changed_source = changed_provenance.get(variable)
    other_source = other_provenance.get(variable)
    if changed_source and other_source and changed_source == other_source:
        return other_source, errors
    return None, errors


def _manifest_shared_wiring(
    changed_service: str, other_service: str, variable: str
) -> Tuple[List[Evidence], ErrorList]:
    """
    Evidence for every manifest *file* registered to both services (same
    deployment artifact, wiring both containers) that declares `variable`
    for the other service's container.
    """
    errors: ErrorList = []
    evidence: List[Evidence] = []
    try:
        changed_paths = {
            m["path"] for m in config_manager.get_deployment_manifests(changed_service)
        }
    except EnvShieldException as e:
        errors.append(
            f"{type(e).__name__}: could not load manifests for '{changed_service}'"
        )
        return evidence, errors

    try:
        other_manifests = config_manager.get_deployment_manifests(other_service)
    except EnvShieldException as e:
        errors.append(
            f"{type(e).__name__}: could not load manifests for '{other_service}'"
        )
        return evidence, errors

    for manifest in other_manifests:
        if manifest["path"] not in changed_paths:
            continue
        found, manifest_errors = _manifest_declares(manifest, variable)
        errors.extend(manifest_errors)
        if found:
            evidence.append(
                {
                    "manifest_path": manifest["path"],
                    "container": manifest.get("container"),
                }
            )
    return evidence, errors


def _manifest_reference_not_shared(
    changed_service: str, other_service: str, variable: str
) -> Tuple[List[Evidence], ErrorList]:
    """Evidence for a manifest registered to `other_service` only -- not shared with the changed service."""
    errors: ErrorList = []
    evidence: List[Evidence] = []
    try:
        changed_paths = {
            m["path"] for m in config_manager.get_deployment_manifests(changed_service)
        }
    except EnvShieldException as e:
        errors.append(
            f"{type(e).__name__}: could not load manifests for '{changed_service}'"
        )
        return evidence, errors

    try:
        other_manifests = config_manager.get_deployment_manifests(other_service)
    except EnvShieldException as e:
        errors.append(
            f"{type(e).__name__}: could not load manifests for '{other_service}'"
        )
        return evidence, errors

    for manifest in other_manifests:
        if manifest["path"] in changed_paths:
            continue  # already covered by _manifest_shared_wiring
        found, manifest_errors = _manifest_declares(manifest, variable)
        errors.extend(manifest_errors)
        if found:
            evidence.append(
                {
                    "manifest_path": manifest["path"],
                    "container": manifest.get("container"),
                }
            )
    return evidence, errors


def _manifest_declares(
    manifest: Dict[str, Any], variable: str
) -> Tuple[bool, ErrorList]:
    path, container = manifest["path"], manifest.get("container")
    parser = get_parser(path, container=container)
    if parser is None:
        return False, [f"no parser available for manifest '{path}'"]
    try:
        declared = parser.get_vars(path, get_values=False)
    except (FileNotFoundError, OSError, ValueError, EnvShieldException) as e:
        return False, [f"{type(e).__name__}: could not parse manifest '{path}'"]
    return variable in declared, []


def _declares_independently(
    other_service: str, variable: str
) -> Tuple[bool, ErrorList]:
    """Whether the other service's own (resolved) schema also declares this variable -- no provenance claim either way."""
    try:
        schema = config_manager.load_schema(other_service)
    except EnvShieldException as e:
        return False, [
            f"{type(e).__name__}: could not load schema for '{other_service}'"
        ]
    return variable in schema, []


def _walk_errors_seen(errors: ErrorList):
    def _onerror(exc: OSError) -> None:
        errors.append(
            f"{type(exc).__name__}: could not list a directory while walking source tree"
        )

    return _onerror


def _discoverable_files(service_dir: str) -> Tuple[List[str], ErrorList]:
    errors: ErrorList = []
    files: List[str] = []
    if os.path.islink(service_dir):
        errors.append(f"service directory '{service_dir}' is a symlink -- not followed")
        return files, errors
    for root, dirs, filenames in os.walk(
        service_dir, onerror=_walk_errors_seen(errors)
    ):
        dirs[:] = [d for d in dirs if d not in _EXCLUDED_DIRS]
        for filename in filenames:
            if not filename.endswith(_DISCOVERABLE_SUFFIXES):
                continue
            file_path = os.path.join(root, filename)
            if os.path.islink(file_path):
                continue  # matches EnvShield's existing symlink trust-boundary policy -- a deliberate refusal, not an error
            files.append(os.path.normpath(file_path))
    return files, errors


def _source_usages(
    other_service: str, variable: str
) -> Tuple[List["discovery.DiscoveredVariableUsage"], ErrorList]:
    errors: ErrorList = []
    try:
        service_dir = config_manager.get_service_dir(other_service)
    except EnvShieldException as e:
        return [], [
            f"{type(e).__name__}: could not resolve source directory for '{other_service}'"
        ]

    files, walk_errors = _discoverable_files(service_dir)
    errors.extend(walk_errors)

    usages: List[discovery.DiscoveredVariableUsage] = []
    for file_path in files:
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except (IOError, OSError) as e:
            errors.append(f"{type(e).__name__}: {e}")
            continue
        if file_path.endswith(_PYTHON_SUFFIXES):
            found = discovery.discover_python_usages(content, file_path)
        else:
            found = discovery.discover_js_usages(content, file_path)
        usages.extend(u for u in found if u.variable == variable)
    return usages, errors


# --- Classification ----------------------------------------------------------


def classify(
    changed_service: str, other_service: str, variable: str
) -> Optional[Dict[str, Any]]:
    """
    Returns None only when every evidence source was successfully checked
    and none of them found anything -- the one case a service is allowed to
    be silently omitted from the report. Any other outcome (evidence found,
    or a check failed) returns a dict; a failed check never turns into a
    silent None.
    """
    errors: ErrorList = []
    tier2_evidence: List[Evidence] = []

    provenance, e = _schema_shared_provenance(changed_service, other_service, variable)
    errors.extend(e)
    if provenance:
        tier2_evidence.append(
            {"kind": "schema_shared_provenance", "contributing_schema": provenance}
        )

    wiring, e = _manifest_shared_wiring(changed_service, other_service, variable)
    errors.extend(e)
    for w in wiring:
        tier2_evidence.append({"kind": "manifest_shared_wiring", **w})

    if tier2_evidence:
        result = {
            "service": other_service,
            "relationship": "transitive",
            "evidence": tier2_evidence,
        }
        if errors:
            result["evaluation_errors"] = errors
        return result

    tier3_evidence: List[Evidence] = []

    declared, e = _declares_independently(other_service, variable)
    errors.extend(e)
    if declared:
        tier3_evidence.append({"kind": "schema_declared_independently"})

    usages, e = _source_usages(other_service, variable)
    errors.extend(e)
    for u in usages:
        tier3_evidence.append(
            {
                "kind": "source_usage_only",
                "file_path": u.file_path,
                "line": u.line,
                "access_type": u.access_type,
            }
        )

    refs, e = _manifest_reference_not_shared(changed_service, other_service, variable)
    errors.extend(e)
    for r in refs:
        tier3_evidence.append({"kind": "manifest_reference_not_shared", **r})

    if tier3_evidence:
        result = {
            "service": other_service,
            "relationship": "observed_unknown",
            "evidence": tier3_evidence,
        }
        if errors:
            result["evaluation_errors"] = errors
        return result

    if errors:
        return {
            "service": other_service,
            "relationship": "evaluation_unknown",
            "evidence": [{"kind": "evaluation_error", "message": m} for m in errors],
        }

    return None


def build_report(
    changed_service: str,
    revision_a: Optional[str] = "HEAD",
    revision_b: Optional[str] = None,
) -> Dict[str, Any]:
    schema_a = schema_snapshot.load_schema_for_diff(changed_service, revision_a)
    schema_b = schema_snapshot.load_schema_for_diff(changed_service, revision_b)
    diff = contract_diff.diff_schemas(schema_a, schema_b)

    other_services = sorted(
        s for s in config_manager.get_services() if s != changed_service
    )

    variables_report = []
    for change in diff.changes:
        transitive: List[Dict[str, Any]] = []
        observed_unknown: List[Dict[str, Any]] = []
        evaluation_unknown: List[Dict[str, Any]] = []
        for svc in other_services:
            result = classify(changed_service, svc, change.variable)
            if result is None:
                continue
            {
                "transitive": transitive,
                "observed_unknown": observed_unknown,
                "evaluation_unknown": evaluation_unknown,
            }[result["relationship"]].append(result)

        variables_report.append(
            {
                "variable": change.variable,
                "change": change.to_dict(),
                "directly_affected": {"service": changed_service},
                "demonstrably_transitively_affected": transitive,
                "observed_unknown": observed_unknown,
                "evaluation_unknown": evaluation_unknown,
            }
        )

    return {
        "service": changed_service,
        "revision_a": revision_a,
        "revision_b": revision_b,
        "variables": variables_report,
    }


# --- Rendering -----------------------------------------------------------


def _describe_evidence(ev: Evidence) -> str:
    kind = ev["kind"]
    if kind == "schema_shared_provenance":
        return f"shared schema provenance ({ev['contributing_schema']})"
    if kind == "manifest_shared_wiring":
        return f"shared deployment wiring ({ev['manifest_path']}, container: {ev.get('container')})"
    if kind == "schema_declared_independently":
        return "independent schema declaration"
    if kind == "source_usage_only":
        return f"source usage only ({ev['file_path']}:{ev['line']})"
    if kind == "manifest_reference_not_shared":
        return f"referenced in a non-shared manifest ({ev['manifest_path']})"
    if kind == "evaluation_error":
        return ev["message"]
    return kind


def render_text(report: Dict[str, Any]) -> str:
    lines: List[str] = []
    for v in report["variables"]:
        lines.append(f"Variable: {v['variable']}")
        change = v["change"]
        lines.append(
            f"Contract change: {change['description']} — {change['category'].upper()}"
        )
        lines.append("")

        lines.append("DIRECTLY AFFECTED")
        lines.append(f"  {v['directly_affected']['service']}")
        lines.append("    evidence: changed contract")
        lines.append("")

        if v["demonstrably_transitively_affected"]:
            lines.append("DEMONSTRABLY TRANSITIVELY AFFECTED")
            for entry in v["demonstrably_transitively_affected"]:
                lines.append(f"  {entry['service']}")
                for ev in entry["evidence"]:
                    lines.append(f"    evidence: {_describe_evidence(ev)}")
            lines.append("")

        if v["observed_unknown"]:
            lines.append("OBSERVED — RELATIONSHIP UNKNOWN")
            for entry in v["observed_unknown"]:
                lines.append(f"  {entry['service']}")
                for ev in entry["evidence"]:
                    lines.append(f"    evidence: {_describe_evidence(ev)}")
            lines.append("")

        if v["evaluation_unknown"]:
            lines.append("COULD NOT BE EVALUATED")
            for entry in v["evaluation_unknown"]:
                lines.append(f"  {entry['service']}")
                for ev in entry["evidence"]:
                    lines.append(f"    evidence: {_describe_evidence(ev)}")
            lines.append("")

    return "\n".join(lines).rstrip("\n") + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Prototype (not production): what does a configuration contract "
            "change affect? Not a shipped EnvShield command."
        )
    )
    parser.add_argument(
        "--service", required=True, help="the service whose schema changed"
    )
    parser.add_argument("revision_a", nargs="?", default="HEAD")
    parser.add_argument("revision_b", nargs="?", default=None)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    try:
        report = build_report(args.service, args.revision_a, args.revision_b)
    except EnvShieldException as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if args.as_json:
        print(json.dumps(report, indent=2))
    else:
        print(render_text(report), end="")


if __name__ == "__main__":
    main()
