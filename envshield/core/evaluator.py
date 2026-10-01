# envshield/core/evaluator.py
"""
The evaluator: one service's contract, evaluated against its sources, as
one result 'check' (and later setup, explain, hooks, and MCP) renders.

Orchestration only. Presence and validity are schema_manager's
diff_against_schema / evaluate_union_completeness (schema_types underneath);
sources come from parsers.factory; the contract from load_schema_view. No
rule is decided here that one of those already decides.

Decisions behind the shape: docs/architecture/evaluator-decisions.md.
Values never leave this module: a SourceEvaluation keeps only a SchemaDiff
(names and constraint descriptions), and the report is built from that.
"""

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

from ..config import manager as config_manager
from ..parsers.factory import get_manifest_parser_and_vars
from ..utils import git_utils
from . import discovery, schema_manager, schema_types, source_files
from .exceptions import EnvShieldException

REPORT_VERSION = 1


@dataclass
class SourceEvaluation:
    """One source (a local file or a deployment manifest) against the contract."""

    kind: str  # "local_file" | "manifest"
    path: str
    service: str
    container: Optional[str] = None
    status: str = "checked"  # checked | missing | error | unresolved
    error: Optional[str] = None
    diff: Optional[schema_manager.SchemaDiff] = None
    is_deployment_manifest: bool = False
    # Names the process environment supplied (D-3); never their values.
    overlaid: Tuple[str, ...] = ()
    # The file exists but no parser recognizes it (a kind of "error").
    unparseable: bool = False

    @property
    def clean(self) -> bool:
        return self.error is None and self.diff is not None and self.diff.is_clean

    def legacy_result(self) -> Dict[str, Any]:
        """Exactly the dict 'check --json' has always put in `results`."""
        if self.error is not None:
            return {
                "file": self.path,
                "service": self.service,
                "clean": False,
                "error": self.error,
            }
        diff = self.diff
        result = {
            "file": self.path,
            "service": self.service,
            "clean": diff.is_clean,
            "missing": sorted(diff.missing),
            "blank": sorted(diff.blank),
            "invalid": dict(diff.invalid),
            "extra": sorted(diff.extra),
            "unresolved": sorted(diff.unresolved),
        }
        if diff.out_of_scope:
            # Only present when non-empty, so a unique schema's shape is unchanged.
            result["out_of_scope"] = sorted(diff.out_of_scope)
        return result


def evaluate_source(
    file_path: str,
    service_name: str,
    container: Optional[str] = None,
    paths: Optional[List[str]] = None,
    schema: Optional[Dict[str, Any]] = None,
    environment: Optional[Mapping[str, str]] = None,
    kind: str = "local_file",
) -> SourceEvaluation:
    """
    Evaluates one source against `service_name`'s contract.

    `schema` is the service's effective schema, loaded here if omitted.
    `paths` is a manifest's ordered layer files (BL-025); omitted, the file
    is parsed alone. `environment`, when given, is laid over a non-manifest
    source (D-3). Every failure is captured in the result, never raised.
    """
    source = SourceEvaluation(
        kind=kind, path=file_path, service=service_name, container=container
    )
    try:
        if schema is None:
            schema = config_manager.load_schema(service_name=service_name)
        parser, local_values = get_manifest_parser_and_vars(
            paths if paths is not None else [file_path],
            container=container,
            prefer=service_name,
            get_values=True,
        )
    except FileNotFoundError as e:
        source.status, source.error = "missing", str(e)
        return source
    except (ValueError, EnvShieldException) as e:
        source.status, source.error = "error", str(e)
        return source
    if not parser:
        source.status, source.unparseable = "error", True
        source.error = schema_manager._no_parser_found_message(file_path)
        return source

    source.is_deployment_manifest = parser.is_deployment_manifest
    file_names = set(local_values)
    if parser.is_deployment_manifest:
        source.kind = "manifest"
    elif environment is not None:
        source.overlaid = tuple(
            schema_manager.environment_overlay_names(environment, schema)
        )
        local_values = schema_manager.overlay_environment(
            local_values, environment, schema
        )

    diff = schema_manager.diff_against_schema(
        schema, local_values, has_unresolved_source=parser.has_unresolved_source
    )
    # An undeclared requiredIf trigger taken from the environment decides
    # requiredness only; it isn't this file's extra variable.
    diff.extra -= set(source.overlaid) - file_names
    try:
        if paths is None:
            schema_manager.drop_file_peer_extras(diff, service_name, file_path)
        schema_manager.mark_out_of_scope(diff, service_name)
    except EnvShieldException as e:
        source.status, source.error = "error", str(e)
        return source

    source.diff = diff
    if diff.unresolved:
        source.status = "unresolved"
    return source


@dataclass
class CodeReferences:
    """
    What the service's own source code reads from the environment (D-7):
    the file set and exclusions the pre-commit 'scan' applies for
    undeclared-variable detection, through discovery.discover_references
    (scan's discovery plus BaseSettings fields and dynamic keys). Source
    text only -- never a value.
    """

    usages: List[discovery.DiscoveredVariableUsage] = field(default_factory=list)
    # Reads with a non-literal key: informational, never a failure.
    dynamic: List[discovery.DynamicReference] = field(default_factory=list)
    files_scanned: int = 0
    # Files discovery couldn't read (too large, unreadable). Informational:
    # discovery is partial by nature, so it never makes a check incomplete.
    skipped: List[str] = field(default_factory=list)

    def by_variable(self, confidence: Optional[str] = None) -> Dict[str, List[Any]]:
        found: Dict[str, List[Any]] = {}
        for usage in self.usages:
            if confidence is None or usage.confidence == confidence:
                found.setdefault(usage.variable, []).append(usage)
        return found


def _exclude_patterns() -> List[str]:
    try:
        config = config_manager.load_config()
    except EnvShieldException:
        return []
    scanning = config.get("secret_scanning") or {}
    return list(scanning.get("exclude_files") or [])


def discover_code_references(service_name: str) -> CodeReferences:
    """
    Walks the service's directory (only files resolve_file_owner gives this
    service) plus its additional_source_roots (BL-106: shared code, exempt
    from ownership), skipping default-excluded directories, symlinks,
    git-ignored files, and secret_scanning.exclude_files -- the file set
    'scan' checks undeclared reads in.
    """
    service_dir = config_manager.get_service_dir(service_name)
    extra_roots = config_manager.get_service_additional_source_roots(service_name)

    owner = config_manager.file_owner_resolver()
    files: List[str] = []
    seen: set = set()
    for root, check_ownership in [(service_dir, True)] + [
        (r, False) for r in extra_roots
    ]:
        for path in source_files.discoverable_files(os.path.normpath(root)):
            if path in seen:
                continue
            if check_ownership and owner(path) != service_name:
                continue
            seen.add(path)
            files.append(path)

    patterns = _exclude_patterns()
    files = [
        f
        for f in files
        if not any(source_files.matches_exclusion(f, p) for p in patterns)
    ]
    ignored = git_utils.get_ignored_files(files)
    files = [f for f in files if f not in ignored]

    result = CodeReferences()
    for path in files:
        try:
            if os.path.getsize(path) > source_files.MAX_SCANNABLE_SIZE_BYTES:
                result.skipped.append(path)
                continue
            with source_files.open_nofollow(path) as f:
                content = f.read()
        except OSError:
            result.skipped.append(path)
            continue
        result.files_scanned += 1
        usages, dynamic = discovery.discover_references(content, path)
        result.usages.extend(usages)
        result.dynamic.extend(dynamic)
    return result


@dataclass
class ServiceEvaluation:
    """One service's whole evaluation: every source, plus union if opted in."""

    service: str
    sources: List[SourceEvaluation] = field(default_factory=list)
    is_union: bool = False
    union: Optional[schema_manager.UnionCompletenessResult] = None
    union_source_errors: List[str] = field(default_factory=list)
    # The service's contract; None when it failed to load (schema_error).
    view: Optional[Any] = None
    schema_error: Optional[str] = None
    # A failure resolving the service itself (its paths, its manifests, or
    # the union step) -- the evaluation stopped there.
    error: Optional[str] = None
    explicit_file: bool = False
    process_environment: bool = False
    # None: not evaluated (explicit file, or the contract didn't load).
    code: Optional[CodeReferences] = None

    @property
    def undeclared_references(self) -> Dict[str, List[Any]]:
        """High-confidence reads of a name the contract doesn't declare (D-7)."""
        if self.code is None or self.view is None:
            return {}
        return {
            name: usages
            for name, usages in self.code.by_variable("high").items()
            if name not in self.view.fields
        }

    @property
    def complete(self) -> bool:
        """
        Whether every source could be fully checked. In union mode the
        union decides: a source that can't confirm a name another source
        supplies doesn't leave the evaluation incomplete (D-4).
        """
        if self.error is not None or self.union_source_errors:
            return False
        if self.is_union:
            return self.union is not None and not self.union.unresolved
        return all(s.status == "checked" for s in self.sources)

    @property
    def passed(self) -> bool:
        """
        'check's verdict for this service: every source clean; in union
        mode the union decides instead, and a source that failed to load
        fails it (D-4). A high-confidence read of an undeclared name fails
        it too (D-7).
        """
        if self.error is not None or self.undeclared_references:
            return False
        if self.is_union:
            return self.union is not None and (
                self.union.is_clean and not self.union_source_errors
            )
        return all(s.clean for s in self.sources)

    def legacy_results(self) -> List[Dict[str, Any]]:
        """This service's entries in 'check --json' `results`, as always."""
        results = [s.legacy_result() for s in self.sources]
        if self.error is not None:
            results.append(
                {"service": self.service, "clean": False, "error": self.error}
            )
        return results

    def legacy_combined(self) -> Optional[Dict[str, Any]]:
        if self.union is None:
            return None
        return schema_manager.union_completeness_result_to_dict(
            self.union, source_errors=self.union_source_errors
        )


def evaluate_service(
    service_name: str,
    file: Optional[str] = None,
    container: Optional[str] = None,
    environment: Optional[Mapping[str, str]] = None,
    code: bool = False,
) -> ServiceEvaluation:
    """
    Evaluates one service the way 'check' always has: its local file (or
    the explicit `file`), then -- only without an explicit file -- every
    registered deployment manifest, then the union verdict if the service
    opted into `completeness: union`. With `code`, and no explicit file,
    also the service's code references (D-7).
    """
    evaluation = ServiceEvaluation(
        service=service_name,
        explicit_file=bool(file),
        process_environment=environment is not None,
    )
    # Outside the try, exactly as 'check' always resolved it.
    evaluation.is_union = (
        not file
        and config_manager.get_service_completeness_mode(service_name) == "union"
    )
    try:
        resolved_file = (
            file
            or config_manager.get_env_paths(service_name=service_name)["local_file"]
        )
        try:
            evaluation.view = config_manager.load_schema_view(service_name)
        except EnvShieldException as e:
            evaluation.schema_error = str(e)
        schema = evaluation.view.fields if evaluation.view is not None else None

        def _evaluate(path, **kwargs):
            if evaluation.schema_error is not None:
                return SourceEvaluation(
                    kind=kwargs.get("kind", "local_file"),
                    path=path,
                    service=service_name,
                    container=kwargs.get("container"),
                    status="error",
                    error=evaluation.schema_error,
                )
            return evaluate_source(path, service_name, schema=schema, **kwargs)

        evaluation.sources.append(
            _evaluate(resolved_file, container=container, environment=environment)
        )
        if not file:
            for manifest in config_manager.get_deployment_manifests(service_name):
                evaluation.sources.append(
                    _evaluate(
                        manifest["path"],
                        container=manifest.get("container") or container,
                        paths=manifest["paths"],
                        kind="manifest",
                    )
                )
        if evaluation.is_union:
            if evaluation.schema_error is not None:
                raise EnvShieldException(evaluation.schema_error)
            sources, errors = schema_manager.load_union_sources(
                service_name, container=container, environment=environment
            )
            evaluation.union = schema_manager.evaluate_union_completeness(
                schema, sources
            )
            evaluation.union_source_errors = errors
        if code and not file and evaluation.view is not None:
            evaluation.code = discover_code_references(service_name)
    except EnvShieldException as e:
        evaluation.error = str(e)
    return evaluation


# --- Report (D-5) ------------------------------------------------------------


def _source_entry(source: SourceEvaluation) -> Dict[str, Any]:
    entry: Dict[str, Any] = {
        "kind": source.kind,
        "path": source.path,
        "status": source.status,
    }
    if source.container:
        entry["container"] = source.container
    if source.error is not None:
        entry["error"] = source.error
    return entry


def _variable_status(name: str, diff: schema_manager.SchemaDiff) -> str:
    if name in diff.missing:
        return "missing"
    if name in diff.blank:
        return "blank"
    if name in diff.invalid:
        return "invalid"
    if name in diff.unresolved:
        return "unresolved"
    if name in diff.out_of_scope:
        return "out_of_scope"
    if name in diff.extra:
        return "extra"
    if name in diff.present:
        return "ok"
    return "not_required"


def _aggregate_presence(statuses: List[str], any_source_failed: bool):
    if "missing" in statuses:
        return False
    if "unresolved" in statuses or any_source_failed:
        return "unknown"
    return any(s in ("ok", "blank", "invalid") for s in statuses)


def build_report(evaluation: ServiceEvaluation) -> Dict[str, Any]:
    """The versioned, value-free report for one ServiceEvaluation (D-5)."""
    sources = [_source_entry(s) for s in evaluation.sources]
    if evaluation.process_environment:
        overlaid = sorted({n for s in evaluation.sources for n in s.overlaid})
        sources.append(
            {"kind": "process_environment", "status": "checked", "variables": overlaid}
        )

    checked = [s for s in evaluation.sources if s.diff is not None]
    any_failed = any(s.diff is None for s in evaluation.sources) or bool(
        evaluation.error
    )
    fields = evaluation.view.fields if evaluation.view is not None else {}
    system = evaluation.view.system if evaluation.view is not None else {}

    code = evaluation.code
    references = code.by_variable() if code is not None else {}
    undeclared = evaluation.undeclared_references

    names = set(fields) | set(references)
    for source in checked:
        names |= source.diff.extra

    variables = []
    for name in sorted(names):
        declared = name in fields
        per_source = [
            {"path": s.path, "status": _variable_status(name, s.diff)} for s in checked
        ]
        statuses = [entry["status"] for entry in per_source]
        entry: Dict[str, Any] = {
            "name": name,
            "declared": declared,
            "scope": "in_scope"
            if declared
            else ("out_of_scope" if name in system else "undefined"),
        }
        if declared:
            field_schema = fields[name]
            invalid_reasons = [
                s.diff.invalid[name] for s in checked if name in s.diff.invalid
            ]
            present = _aggregate_presence(statuses, any_failed)
            if evaluation.union is not None:
                # Union mode: present anywhere counts (D-4).
                union = evaluation.union
                if name in union.missing:
                    present = False
                elif name in union.unresolved:
                    present = "unknown"
                else:
                    present = any(s in ("ok", "blank", "invalid") for s in statuses)
                # Valid if any source has a valid value, as union decides.
                invalid_reasons = [union.invalid[name]] if name in union.invalid else []
            entry.update(
                {
                    "secret": bool(field_schema.get("secret", False)),
                    "requiredness": schema_types.requiredness_label(field_schema),
                    "present": present,
                    "valid": False
                    if invalid_reasons
                    else (True if present is True else None),
                    "reason": invalid_reasons[0] if invalid_reasons else None,
                }
            )
        entry["sources"] = per_source
        if code is not None:
            entry["references"] = [
                {
                    "file": u.file_path,
                    "line": u.line,
                    "language": u.language,
                    "access_type": u.access_type,
                    "confidence": u.confidence,
                }
                for u in references.get(name, [])
            ]
            # Informational only: discovery can't see every kind of read.
            entry["referenced"] = bool(entry["references"])
            if name in undeclared:
                entry["undeclared_reference"] = True
        variables.append(entry)

    complete = evaluation.complete
    return {
        "report_version": REPORT_VERSION,
        "service": evaluation.service,
        "sources": sources,
        "variables": variables,
        "union": evaluation.legacy_combined(),
        "code_references": (
            {
                "status": "checked",
                "files_scanned": code.files_scanned,
                "skipped": sorted(code.skipped),
                "undeclared": sorted(undeclared),
                "dynamic_references": [d.to_dict() for d in code.dynamic],
            }
            if code is not None
            else {"status": "not_checked"}
        ),
        "summary": {"clean": evaluation.passed and complete, "complete": complete},
    }


def legacy_check_payload(evaluations: List[ServiceEvaluation]) -> Dict[str, Any]:
    """'check --json's pre-evaluator keys, built from evaluations: success, results, combined."""
    results: List[Dict[str, Any]] = []
    combined: Dict[str, Any] = {}
    for evaluation in evaluations:
        results.extend(evaluation.legacy_results())
        union = evaluation.legacy_combined()
        if union is not None:
            combined[evaluation.service] = union
    payload: Dict[str, Any] = {
        "success": all(e.passed for e in evaluations),
        "results": results,
    }
    if combined:
        payload["combined"] = combined
    return payload
