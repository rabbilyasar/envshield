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
from ..parsers._base import Concrete, ValueStates, not_inspected_names
from ..parsers.factory import get_manifest_parser_and_states
from ..utils import git_utils
from . import discovery, schema_manager, schema_types, source_files, verdicts
from . import since as since_check
from .exceptions import EnvShieldException
from .verdicts import Verdict

REPORT_VERSION = 2


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
    # Names whose value EnvShield didn't inspect (R0-5): a placeholder for a
    # value outside the file, or a Compose fallback. Report only; the gate
    # is unchanged.
    not_inspected: Tuple[str, ...] = ()
    # The file exists but no parser recognizes it (a kind of "error").
    unparseable: bool = False
    # v5 (plan §10), all value-free: each name's verdict in this source, the
    # source's requiredness opinion per declared name (verdicts.requirement),
    # envFrom references outside the file, and why an errored source failed.
    verdicts: Dict[str, Verdict] = field(default_factory=dict)
    needs: Dict[str, Optional[str]] = field(default_factory=dict)
    unbound: Tuple[Any, ...] = ()
    error_reason: Optional[str] = None

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
    union: bool = False,
    env: str = schema_types.LOCAL,
) -> SourceEvaluation:
    """
    Evaluates one source against `service_name`'s contract.

    `schema` is the service's effective schema, loaded here if omitted.
    `paths` is a manifest's ordered layer files (BL-025); omitted, the file
    is parsed alone. `environment`, when given, is laid over a non-manifest
    source (D-3). Requiredness is judged for environment `env` (plan §7).
    Every failure is captured in the result, never raised.
    """
    source = SourceEvaluation(
        kind=kind, path=file_path, service=service_name, container=container
    )
    try:
        if schema is None:
            schema = config_manager.load_schema(service_name=service_name)
        parser, local_values = get_manifest_parser_and_states(
            paths if paths is not None else [file_path],
            container=container,
            prefer=service_name,
        )
    except FileNotFoundError as e:
        source.status, source.error = "missing", str(e)
        return source
    except (ValueError, EnvShieldException) as e:
        source.status, source.error = "error", str(e)
        source.error_reason = "source_parse_error"
        return source
    except OSError as e:
        # Unreadable (permissions, a directory, ...): the evaluation can't
        # run. strerror names the problem; the file's content is never read.
        source.status, source.error = (
            "error",
            f"Could not read '{file_path}': {e.strerror}",
        )
        source.error_reason = "source_unreadable"
        return source
    if not parser:
        source.status, source.unparseable = "error", True
        source.error = schema_manager._no_parser_found_message(file_path)
        source.error_reason = "source_parse_error"
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

    # Overlaid names are Concrete now, so never among these.
    source.not_inspected = tuple(sorted(not_inspected_names(local_values)))
    diff = schema_manager.diff_against_schema(schema, local_values, env)
    # An undeclared requiredIf trigger taken from the environment decides
    # requiredness only; it isn't this file's extra variable.
    diff.extra -= set(source.overlaid) - file_names
    try:
        if paths is None:
            schema_manager.drop_file_peer_extras(diff, service_name, file_path)
        schema_manager.mark_out_of_scope(diff, service_name)
    except EnvShieldException as e:
        source.status, source.error = "error", str(e)
        source.error_reason = "topology_error"
        return source

    source.verdicts = verdicts.judge_source(
        schema,
        local_values,
        diff.extra,
        diff.out_of_scope,
        union=union,
        manifest=parser.is_deployment_manifest,
        env=env,
    )
    source.needs = {
        name: verdicts.requirement(details, local_values, env)
        for name, details in schema.items()
    }
    source.unbound = local_values.unbound
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


def _walk_service_files(service_name: str) -> List[str]:
    """discover_code_references' full walk (no `files`), unchanged."""
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

    return files


def service_source_filter(service_name: str):
    """
    `belongs(path)`: whether a source file at `path` (relative to the
    project root) is part of `service_name`'s code for code references --
    the per-file rules of discover_code_references' walk: a discoverable
    suffix, no default-excluded directory on the way, inside the service's
    directory and owned by it (resolve_file_owner), or inside one of its
    additional_source_roots (BL-106: exempt from ownership), and not
    matching secret_scanning.exclude_files. Path rules only: it doesn't
    look at the disk, so it also answers for a file deleted since a
    revision (check --since, D-13).
    """
    service_dir = os.path.normpath(config_manager.get_service_dir(service_name))
    extra_roots = [
        os.path.normpath(r)
        for r in config_manager.get_service_additional_source_roots(service_name)
    ]
    owner = config_manager.file_owner_resolver()
    patterns = _exclude_patterns()

    def belongs(path: str) -> bool:
        path = os.path.normpath(path)
        if not path.endswith(source_files.DISCOVERABLE_SUFFIXES):
            return False
        parts = path.split(os.sep)[:-1]
        if any(source_files.is_default_excluded_dir(d) for d in parts):
            return False
        if not any(config_manager.service_dir_contains(path, r) for r in extra_roots):
            if not config_manager.service_dir_contains(path, service_dir):
                return False
            if owner(path) != service_name:
                return False
        return not any(source_files.matches_exclusion(path, p) for p in patterns)

    return belongs


def discover_code_references(
    service_name: str, files: Optional[List[str]] = None
) -> CodeReferences:
    """
    Walks the service's directory (only files resolve_file_owner gives this
    service) plus its additional_source_roots (BL-106: shared code, exempt
    from ownership), skipping default-excluded directories, symlinks,
    git-ignored files, and secret_scanning.exclude_files -- the file set
    'scan' checks undeclared reads in.

    `files` (paths relative to the project root, or absolute) replaces the
    walk: only those of them that are this service's code
    (service_source_filter), exist, and aren't symlinks are read. 'check
    --since' passes the files changed since its revision (D-13), so a large
    repository isn't walked for a three-file change (BL-205).
    """
    if files is not None:
        belongs = service_source_filter(service_name)
        candidates: List[str] = []
        seen: set = set()
        for f in files:
            path = os.path.normpath(os.path.relpath(f) if os.path.isabs(f) else f)
            if path in seen or os.path.islink(path) or not os.path.isfile(path):
                continue
            seen.add(path)
            if belongs(path):
                candidates.append(path)
        ignored = git_utils.get_ignored_files(candidates)
        files = [f for f in candidates if f not in ignored]
    else:
        files = _walk_service_files(service_name)

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
    # 'check --runtime' (plan §11): the process environment is the only source.
    runtime: bool = False
    # The environment requiredness is judged for (plan §7).
    env: str = schema_types.LOCAL
    # None: not evaluated (explicit file, or the contract didn't load).
    code: Optional[CodeReferences] = None
    # 'check --code-only' (D-13): code references and the contract only; no
    # local file or manifest is read, so no value is judged.
    code_only: bool = False
    # 'check --since REV' (D-13); None without --since.
    since: Optional[since_check.SinceResult] = None

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

    # --- v5 verdicts (plan §10) -------------------------------------------

    def _source_verdict(self, name: str, source: SourceEvaluation):
        """`name`'s verdict in `source`; None when the source says nothing
        about it (a source that errored or is missing, in union mode, or an
        undeclared name another source has)."""
        if source.diff is not None:
            return source.verdicts.get(name)
        fields = self.view.fields if self.view is not None else {}
        if self.is_union or name not in fields:
            return None
        if source.status == "missing":
            need = verdicts.requirement(fields[name], ValueStates(), self.env)
            return verdicts.judge_variable(
                fields[name], None, need, missing_file=source.path
            )
        return verdicts.Verdict(
            verdicts.ERROR, source.error_reason or "internal_error", source.error
        )

    def judged(self) -> Dict[str, Tuple[Any, List[Any]]]:
        """name -> (its verdict, its verdict in each of self.sources or None)."""
        fields = self.view.fields if self.view is not None else {}
        undeclared = self.undeclared_references
        # Code-only judges no value, so a declared variable has no verdict.
        names = set(undeclared) if self.code_only else set(fields) | set(undeclared)
        for source in self.sources:
            names |= set(source.verdicts)
        result = {}
        for name in names:
            per_source = [self._source_verdict(name, s) for s in self.sources]
            seen = [v for v in per_source if v is not None]
            if name in undeclared:
                seen.append(verdicts.UNDECLARED)
                overall = verdicts.worst(seen)
            elif self.is_union and name in fields:
                need = verdicts.union_requirement(
                    fields[name],
                    [s.needs.get(name) for s in self.sources if s.diff is not None],
                )
                unbound = [u for s in self.sources for u in s.unbound]
                overall = verdicts.union_verdict(fields[name], seen, need, unbound)
            else:
                overall = verdicts.worst(seen)
            result[name] = (overall, per_source)
        return result

    def service_findings(self) -> List[Tuple[Any, Optional[SourceEvaluation]]]:
        """Failures that belong to the service or a whole source, not to one variable."""
        found: List[Tuple[Any, Optional[SourceEvaluation]]] = []
        if self.schema_error is not None:
            found.append(
                (
                    verdicts.Verdict(
                        verdicts.ERROR, "contract_error", self.schema_error
                    ),
                    None,
                )
            )
        if self.error is not None:
            found.append(
                (verdicts.Verdict(verdicts.ERROR, "topology_error", self.error), None)
            )
        if self.schema_error is None:
            for source in self.sources:
                if source.status == "error":
                    reason = source.error_reason or "internal_error"
                    found.append(
                        (verdicts.Verdict(verdicts.ERROR, reason, source.error), source)
                    )
                elif source.status == "missing" and self.is_union:
                    found.append(
                        (
                            verdicts.Verdict(
                                verdicts.INVALID, "file_not_found", source.error
                            ),
                            source,
                        )
                    )
        return found

    def verdict(self) -> Any:
        """
        The service's verdict: the highest-precedence one found anywhere.
        A service with nothing required is VALID ("0 required variables").
        """
        found = verdicts.worst(
            [v for v, _ in self.judged().values()]
            + [v for v, _ in self.service_findings()]
        )
        if found.verdict == verdicts.NOT_REQUIRED:
            return verdicts.Verdict(verdicts.VALID)
        return found

    def gate_verdict(self) -> Any:
        """
        The verdict the gate judges (D-13): verdict(), except that with
        --since an undeclared read proven to exist at the revision doesn't
        count. The reported verdict never changes, as with accepted
        COULD NOT VERIFY reasons (D-10).
        """
        if self.since is None:
            return self.verdict()
        old = self.since.read_before
        undeclared = self.undeclared_references
        counted = []
        for name, (overall, per_source) in self.judged().items():
            if name in undeclared and name in old:
                # As judged() would without the undeclared read.
                overall = verdicts.worst(v for v in per_source if v is not None)
            counted.append(overall)
        found = verdicts.worst(counted + [v for v, _ in self.service_findings()])
        if found.verdict == verdicts.NOT_REQUIRED:
            return verdicts.Verdict(verdicts.VALID)
        return found

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
    env: str = schema_types.LOCAL,
    code_files: Optional[List[str]] = None,
) -> ServiceEvaluation:
    """
    Evaluates one service in environment `env` (plan §7). In 'local', the
    way 'check' always has: its local file (or the explicit `file`), then
    -- only without an explicit file -- every untagged (or 'local'-tagged)
    deployment manifest, then the union verdict if the service opted into
    `completeness: union`. In another environment, only the manifests
    tagged with it (or the explicit `file`), judged by its requirements;
    none is an error, never a pass. With `code`, and no explicit file, also
    the service's code references (D-7).
    """
    local = env == schema_types.LOCAL
    evaluation = ServiceEvaluation(
        service=service_name,
        explicit_file=bool(file),
        process_environment=environment is not None,
        env=env,
    )
    # Outside the try, exactly as 'check' always resolved it. Union mode is
    # a local-file-plus-manifests rule: 'local' only.
    evaluation.is_union = (
        local
        and not file
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
                    error_reason="contract_error",
                )
            return evaluate_source(
                path,
                service_name,
                schema=schema,
                union=evaluation.is_union,
                env=env,
                **kwargs,
            )

        if local or file:
            evaluation.sources.append(
                _evaluate(resolved_file, container=container, environment=environment)
            )
        if not file:
            manifests = config_manager.manifests_for_environment(service_name, env)
            if not manifests and not local:
                raise EnvShieldException(
                    f"No deployment manifest for '{service_name}' is tagged "
                    f"'{env}', so there is nothing to check in {env}. Tag one "
                    f"with 'environment: {env}' under 'manifests:' in envshield.yml."
                )
            for manifest in manifests:
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
            evaluation.code = discover_code_references(service_name, code_files)
    except EnvShieldException as e:
        evaluation.error = str(e)
    return evaluation


def evaluate_code_only(
    service_name: str, code_files: Optional[List[str]] = None
) -> ServiceEvaluation:
    """
    'check --code-only' (D-13): the contract and the service's code
    references, nothing else. Without a local file (CI), this is what
    'undeclared' checked. A contract that fails to load is an error, as
    everywhere else.
    """
    evaluation = ServiceEvaluation(service=service_name, code_only=True)
    try:
        evaluation.view = config_manager.load_schema_view(service_name)
    except EnvShieldException as e:
        evaluation.schema_error = str(e)
        return evaluation
    try:
        evaluation.code = discover_code_references(service_name, code_files)
    except EnvShieldException as e:
        evaluation.error = str(e)
    return evaluation


RUNTIME_SOURCE = "process environment"


def evaluate_runtime(
    service_name: str, environment: Mapping[str, str], env: str
) -> ServiceEvaluation:
    """
    'check --runtime' (plan §11, Q9): `service_name`'s contract judged for
    `env` against the process environment alone -- no local file, no
    manifest, no code references. Only the names the contract declares (or
    names as a requiredIf trigger) are read from `environment` (D-3), and a
    defaultValue is never evidence.
    """
    evaluation = ServiceEvaluation(service=service_name, runtime=True, env=env)
    try:
        evaluation.view = config_manager.load_schema_view(service_name)
    except EnvShieldException as e:
        evaluation.schema_error = str(e)
        return evaluation
    schema = evaluation.view.fields
    source = SourceEvaluation(
        kind="process_environment", path=RUNTIME_SOURCE, service=service_name
    )
    names = schema_manager.environment_overlay_names(environment, schema)
    values = ValueStates({name: Concrete(environment[name]) for name in names})
    source.overlaid = tuple(names)
    diff = schema_manager.diff_against_schema(schema, values, env)
    # A requiredIf trigger the contract doesn't declare decides requiredness
    # only; it isn't an extra variable.
    diff.extra -= set(names)
    source.verdicts = verdicts.judge_source(schema, values, env=env)
    source.needs = {
        name: verdicts.requirement(details, values, env)
        for name, details in schema.items()
    }
    source.diff = diff
    evaluation.sources.append(source)
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


def _variable_status(name: str, source: SourceEvaluation) -> str:
    diff = source.diff
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
        # Present and not invalid, but the value was never seen (R0-5).
        return "not_visible" if name in source.not_inspected else "ok"
    return "not_required"


_PRESENT = ("ok", "not_visible", "blank", "invalid")


def _aggregate_presence(statuses: List[str], any_source_failed: bool):
    if "missing" in statuses:
        return False
    if "unresolved" in statuses or any_source_failed:
        return "unknown"
    return any(s in _PRESENT for s in statuses)


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

    judged = evaluation.judged()
    if evaluation.code_only:
        # Only the undeclared reads: no declared variable's value was read,
        # so none gets a verdict.
        names = set(judged)
    else:
        names = set(fields) | set(references)
        for source in checked:
            names |= source.diff.extra
        names |= set(judged)

    variables = []
    unverified: List[str] = []
    for name in sorted(names):
        declared = name in fields
        overall, per_judged = judged.get(
            name,
            (verdicts.Verdict(verdicts.NOT_REQUIRED), [None] * len(evaluation.sources)),
        )
        by_source = {id(s): v for s, v in zip(evaluation.sources, per_judged)}
        per_source = []
        for s in checked:
            judged_here = by_source.get(id(s)) or verdicts.Verdict(
                verdicts.NOT_REQUIRED
            )
            per_source.append(
                {
                    "path": s.path,
                    "status": _variable_status(name, s),
                    "verdict": judged_here.verdict,
                    "reason_code": judged_here.reason,
                }
            )
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
                    present = any(s in _PRESENT for s in statuses)
                # Valid if any source has a valid value, as union decides.
                invalid_reasons = [union.invalid[name]] if name in union.invalid else []
                blank = name in union.blank
            else:
                blank = "blank" in statuses
            # A blank value was read and breaks the contract: never valid.
            if blank and not invalid_reasons:
                invalid_reasons = ["required but blank"]
            trigger_reasons = [
                schema_manager.trigger_reason(s.diff.unresolved_triggers[name])
                for s in checked
                if name in s.diff.unresolved_triggers
            ]
            if invalid_reasons:
                valid = False
            elif present is True and "ok" in statuses:
                valid = True
            else:
                # Not present, or present only as values EnvShield never
                # saw (R0-5): no claim either way.
                valid = None
                if present is True:
                    unverified.append(name)
            entry.update(
                {
                    "secret": bool(field_schema.get("secret", False)),
                    "requiredness": schema_types.requiredness_label(
                        field_schema, evaluation.env
                    ),
                    "present": present,
                    "valid": valid,
                    "reason": (invalid_reasons or trigger_reasons or [None])[0],
                }
            )
        entry["verdict"] = overall.verdict
        entry["reason_code"] = overall.reason
        entry["detail"] = overall.detail
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
    findings = _findings(evaluation, judged)
    service_verdict = evaluation.verdict()
    counts = {v: 0 for v in verdicts.VERDICTS}
    for entry in variables:
        counts[entry["verdict"]] += 1
    return {
        "report_version": REPORT_VERSION,
        "service": evaluation.service,
        "environment": evaluation.env,
        **({"values": "not_checked"} if evaluation.code_only else {}),
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
        "summary": {
            "clean": evaluation.passed and complete,
            "complete": complete,
            # Valid-looking values EnvShield never saw; they pass the gate.
            "unverified": unverified,
            # v2: the service's verdict and how many variables got each one.
            "verdict": service_verdict.verdict,
            "counts": counts,
        },
        "findings": findings,
        **({"since": _since_entry(evaluation)} if evaluation.since else {}),
        "errors": [f for f in findings if f["verdict"] == verdicts.ERROR],
        "invalid": [f for f in findings if f["verdict"] == verdicts.INVALID],
        "unverified": [f for f in findings if f["verdict"] == verdicts.UNVERIFIED],
        "coverage": (
            {
                "files_scanned": code.files_scanned,
                "skipped": sorted(code.skipped),
                "dynamic": [d.to_dict() for d in code.dynamic],
            }
            if code is not None
            else None
        ),
    }


def _since_entry(evaluation: ServiceEvaluation) -> Dict[str, Any]:
    """The report's 'since' block (D-13)."""
    result = evaluation.since
    assert result is not None
    undeclared = set(evaluation.undeclared_references)
    if result.contract_introduced:
        contract = "introduced"
    elif result.contract is None:
        contract = "error"
    else:
        contract = "compared"
    return {
        "revision": result.revision,
        # Code references cover only the files changed since `revision`
        # (#85); plain 'check' covers every file.
        "code_scope": "changed_files",
        "new_undeclared": sorted(undeclared - result.read_before),
        "undeclared_before": sorted(undeclared & result.read_before),
        "contract": contract,
        "contract_error": result.contract_error,
    }


def _finding(
    evaluation: ServiceEvaluation,
    verdict: Any,
    kind: str,
    variable: Optional[str] = None,
    source: Optional[SourceEvaluation] = None,
    line: Optional[int] = None,
    file: Optional[str] = None,
) -> Dict[str, Any]:
    """One finding (BL-191): names, places and reasons only, never a value."""
    fields = evaluation.view.fields if evaluation.view is not None else {}
    field_schema = fields.get(variable or "", {})
    path = file or (source.path if source is not None else None)
    if evaluation.runtime:
        path = None  # the process environment isn't a file
    location = None
    if path is not None:
        location = {
            "file": path,
            "line": line,
            "container": getattr(source, "container", None),
        }
    return {
        "kind": kind,
        "verdict": verdict.verdict,
        "reason_code": verdict.reason,
        "detail": verdict.detail,
        "variable": variable,
        "service": evaluation.service,
        "location": location,
        "secret": bool(field_schema.get("secret", False)),
        "next_step": verdicts.next_step(
            verdict.reason,
            variable=variable,
            service=evaluation.service,
            file=path,
            manifest=bool(source is not None and source.is_deployment_manifest),
            secret=bool(field_schema.get("secret", False)),
            has_default="defaultValue" in field_schema,
            detail=verdict.detail,
            runtime_env=evaluation.env if evaluation.runtime else None,
        ),
    }


def _findings(evaluation: ServiceEvaluation, judged) -> List[Dict[str, Any]]:
    """
    One finding per variable x source that is INVALID or COULD NOT VERIFY
    (in union mode, per variable: the union decides), one per undeclared
    code read, and one per service- or source-level failure. A variable a
    failed source would supply is listed under that source's error.
    """
    findings: List[Dict[str, Any]] = []
    fields = evaluation.view.fields if evaluation.view is not None else {}
    for verdict, source in evaluation.service_findings():
        finding = _finding(
            evaluation, verdict, "source" if source else "service", source=source
        )
        if source is not None:
            finding["variables"] = sorted(fields)
        findings.append(finding)

    undeclared = evaluation.undeclared_references
    reportable = (verdicts.INVALID, verdicts.UNVERIFIED)
    for name in sorted(judged):
        overall, per_source = judged[name]
        if name in undeclared:
            usage = undeclared[name][0]
            finding = _finding(
                evaluation,
                verdicts.UNDECLARED,
                "code_reference",
                name,
                line=usage.line,
                file=usage.file_path,
            )
            if evaluation.since is not None:
                finding["new"] = name not in evaluation.since.read_before
            findings.append(finding)
        if overall.verdict not in reportable or overall == verdicts.UNDECLARED:
            continue
        pairs = list(zip(per_source, evaluation.sources))
        if evaluation.is_union:
            # The union decides: one finding, at the source it came from.
            where = next((src for v, src in pairs if v == overall), None)
            findings.append(
                _finding(evaluation, overall, "variable", name, source=where)
            )
            continue
        for v, src in pairs:
            if v is not None and v.verdict in reportable:
                findings.append(_finding(evaluation, v, "variable", name, source=src))
    if evaluation.since is not None and evaluation.view is not None:
        findings.extend(
            since_check.contract_findings(
                evaluation.since,
                evaluation.service,
                fields,
                evaluation.view.schema_path,
            )
        )
    return findings


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
