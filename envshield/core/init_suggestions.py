# envshield/core/init_suggestions.py
"""
What 'init' offers to add to a first contract (V2-3, #44; BL-175, BL-176,
BL-181): the variables the code reads that no config file declared, and
the deployment manifests that run the services. Suggestions only: 'init'
shows them and the user (or --yes) accepts. Nothing here writes or prints.

A suggestion never invents a requirement (BL-180): a variable read by code
is required only when the code itself fails without it (`os.environ[...]`);
any other read becomes `required = false`.
"""

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import yaml

from envshield.config import manager as config_manager
from envshield.core import (
    contract_writer,
    evaluator,
    importer,
    schema_types,
    service_discovery,
)
from envshield.core.exceptions import EnvShieldException
from envshield.parsers._deployment import detect_deployment_format

# Reads that raise when the variable is missing: they prove a requirement.
_REQUIRING_ACCESS = {"os.environ[]"}


@dataclass
class CodeRead:
    variable: str
    service: str
    # Every place it's read: (file, line).
    locations: List[Tuple[str, int]] = field(default_factory=list)
    access_types: List[str] = field(default_factory=list)

    @property
    def secret(self) -> bool:
        return importer.key_contains_secret_keyword(self.variable)

    @property
    def required(self) -> bool:
        return any(a in _REQUIRING_ACCESS for a in self.access_types)

    def entry(self) -> Dict[str, Any]:
        """The contract table to append for this read."""
        details: Dict[str, Any] = {
            "description": schema_types.PLACEHOLDER_DESCRIPTION,
            "secret": self.secret,
        }
        if not self.required:
            details["required"] = False
        details[contract_writer.FROM_CODE] = True
        return details


def code_read_suggestions(
    services: List[str], declared: set, covered: Optional[set] = None
) -> Tuple[List[CodeRead], List[str]]:
    """
    Every variable each service's code reads (high confidence, a literal
    name) that isn't in `declared`, and the services whose code couldn't be
    read (with why). `covered` holds (service, name) pairs about to be
    declared in the same run (from config files): a read of such a name by
    another service is still suggested, so the one new table can grant it
    to every service that reads it. The writer joins them.
    """
    covered = covered or set()
    reads: Dict[Tuple[str, str], CodeRead] = {}
    problems: List[str] = []
    for service in services:
        try:
            refs = evaluator.discover_code_references(service)
        except EnvShieldException as e:
            problems.append(f"{service}: {e}")
            continue
        for usage in refs.usages:
            if usage.confidence != "high" or usage.variable in declared:
                continue
            if (service, usage.variable) in covered:
                continue
            read = reads.setdefault(
                (service, usage.variable), CodeRead(usage.variable, service)
            )
            read.locations.append((usage.file_path, usage.line))
            if usage.access_type not in read.access_types:
                read.access_types.append(usage.access_type)
    ordered = sorted(reads.values(), key=lambda r: (r.service, r.variable))
    return ordered, problems


# --- Deployment manifests ---------------------------------------------------

_K8S_DIRS = ("k8s", "kubernetes", "deploy", "deployment", "manifests", ".k8s", "helm")
_K8S_MAX_FILES = 200


@dataclass
class ManifestProposal:
    file: str
    kind: str  # "compose" or "kubernetes"
    containers: Dict[str, str] = field(default_factory=dict)  # container -> service
    unmapped: List[str] = field(default_factory=list)

    def registration(self) -> Dict[str, Any]:
        return {"file": self.file, "containers": dict(self.containers)}


def _compose_services(path: str) -> Dict[str, Any]:
    try:
        with open(path) as f:
            doc = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError):
        return {}
    services = doc.get("services") if isinstance(doc, dict) else None
    return services if isinstance(services, dict) else {}


def _build_context(compose_path: str, spec: Any) -> Optional[str]:
    """A compose service's build directory, relative to the project root."""
    if not isinstance(spec, dict):
        return None
    build = spec.get("build")
    if isinstance(build, dict):
        build = build.get("context", ".")
    if not isinstance(build, str) or "://" in build:
        return None
    return os.path.normpath(os.path.join(os.path.dirname(compose_path) or ".", build))


def _compose_proposal(path: str, service_dirs: Dict[str, str]) -> ManifestProposal:
    """
    A container maps to the service with its name, or to the service whose
    directory is its build context. Several containers building from the
    project root are different apps sharing one build root (Plane's web,
    admin and space): mapping them all to a root service would check each
    against the whole project's contract, a false "missing" for everything
    it doesn't use, so they map only by name. Containers building from one
    service's own directory are that service (an app and its worker).
    """
    proposal = ManifestProposal(path, "compose")
    by_dir = {os.path.normpath(d): name for name, d in service_dirs.items()}
    services = _compose_services(path)
    contexts = {c: _build_context(path, spec) for c, spec in services.items()}
    root_builders = [c for c, ctx in contexts.items() if ctx == "."]
    for container in services:
        context = contexts[container]
        if container in service_dirs:
            proposal.containers[container] = container
        elif (
            context is not None
            and context in by_dir
            and (context != "." or len(root_builders) == 1)
        ):
            proposal.containers[container] = by_dir[context]
        else:
            # Infrastructure (an image with no build: a database, a cache),
            # or an app no single service accounts for.
            proposal.unmapped.append(container)
    return proposal


def _kubernetes_files() -> List[str]:
    found: List[str] = []
    for base in _K8S_DIRS:
        if not os.path.isdir(base):
            continue
        for root, dirs, files in os.walk(base):
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))
            for name in sorted(files):
                if name.endswith((".yml", ".yaml")):
                    found.append(os.path.normpath(os.path.join(root, name)))
                    if len(found) >= _K8S_MAX_FILES:
                        return found
    return found


def _k8s_container_names(path: str) -> List[str]:
    try:
        with open(path) as f:
            docs = list(yaml.safe_load_all(f))
    except (OSError, yaml.YAMLError):
        return []
    names: List[str] = []
    for doc in docs:
        spec = doc
        for key in ("spec", "template", "spec"):
            spec = spec.get(key) if isinstance(spec, dict) else None
        containers = spec.get("containers") if isinstance(spec, dict) else None
        for c in containers or []:
            if isinstance(c, dict) and isinstance(c.get("name"), str):
                names.append(c["name"])
    return names


def manifest_proposals(service_dirs: Dict[str, str]) -> List[ManifestProposal]:
    """
    Compose files at the root or in a service's directory, and Kubernetes
    manifests under the usual directories, with a `containers:` map proposed
    from build directories and names. Only files that map at least one
    container are proposed.
    """
    proposals: List[ManifestProposal] = []
    seen = set()
    for directory in ["."] + sorted(set(service_dirs.values())):
        path = service_discovery.find_compose_file(directory, directory)
        if path and path not in seen:
            seen.add(path)
            proposal = _compose_proposal(path, service_dirs)
            if proposal.containers:
                proposals.append(proposal)
    for path in _kubernetes_files():
        if path in seen or detect_deployment_format(path) != "kubernetes":
            continue
        names = _k8s_container_names(path)
        mapped = {n: n for n in names if n in service_dirs}
        if mapped:
            proposals.append(
                ManifestProposal(
                    path,
                    "kubernetes",
                    mapped,
                    [n for n in names if n not in mapped],
                )
            )
    return proposals


def already_registered(path: str) -> bool:
    manifests = config_manager.load_config().get("manifests") or []
    return any(
        isinstance(m, dict)
        and os.path.normpath(str(m.get("file", ""))) == os.path.normpath(path)
        for m in manifests
    )
