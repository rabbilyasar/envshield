# envshield/parsers/_kubernetes.py
import os
from typing import Any

import yaml

from ..core.exceptions import EnvShieldException
from ._base import (
    BaseParser,
    Concrete,
    External,
    KeyPresent,
    ValueState,
    ValueStates,
    legacy_text,
)

_POD_TEMPLATE_KINDS = {"Deployment", "StatefulSet", "DaemonSet", "Job", "ReplicaSet"}


def _extract_pod_spec(doc: dict[str, Any]) -> dict[str, Any] | None:
    """Navigates a single manifest document down to its pod spec (containers list lives here)."""
    kind = doc.get("kind")
    if kind == "Pod":
        return doc.get("spec") or {}
    if kind == "CronJob":
        return (
            (doc.get("spec") or {})
            .get("jobTemplate", {})
            .get("spec", {})
            .get("template", {})
            .get("spec", {})
        )
    if kind in _POD_TEMPLATE_KINDS:
        return (doc.get("spec") or {}).get("template", {}).get("spec", {})
    return None


class KubernetesParser(BaseParser):
    """
    Parses a Kubernetes manifest -- Deployment/StatefulSet/DaemonSet/Job/
    CronJob/Pod, possibly several in one multi-document YAML file -- for
    one container's declared environment.

    'initContainers' entries are treated as additional, explicitly-
    selectable containers via '--container' -- exactly like a main
    container, since they're just as capable of declaring their own
    required 'env'/'envFrom' (a migration/setup job needing its own
    config). They are never auto-selected by default, though: default
    selection (no '--container' given) considers only the pod's main
    'containers' list, unchanged from before this existed -- a schema is
    conventionally about the main application container's configuration,
    not a one-shot init job's, and a manifest with exactly one main
    container plus one or more init containers must keep auto-selecting
    that main container with no flag required, exactly as it always has
    (BL-011 #4). To check an init container's own variables, pass
    '--container <init-container-name>' explicitly.

    get_states reports each key's ValueState (v5 plan §4):

    - a literal 'value:' (or an entry with neither 'value' nor 'valueFrom',
      which Kubernetes delivers as "") is Concrete;
    - 'valueFrom.secretKeyRef'/'configMapKeyRef' to an object defined in
      this file is KeyPresent when the referenced '.key' exists there, and
      the key is Absent (left out) when it doesn't -- real Kubernetes fails
      the pod at apply-time in that case (or, with 'optional: true', never
      injects it) rather than delivering anything (BL-011 #3);
    - any other 'valueFrom' (an object outside this file, 'fieldRef',
      'resourceFieldRef') is External: the NAME is known, the value isn't;
    - 'envFrom' to an in-file ConfigMap gives each of its keys as Concrete,
      to an in-file Secret as KeyPresent, keyed by the name the container
      actually receives -- the reference's 'prefix' (if any; an absent or
      empty prefix is a no-op) plus the key (BL-011 #2);
    - 'envFrom' to an object NOT in this file can supply names this parser
      can't enumerate at all, so it's recorded in ValueStates.unbound, and
      no key can then be confidently reported as absent. Its own
      'optional: true' doesn't remove that ambiguity.

    A Secret's data values are never decoded (they're base64, and this is
    a presence check, not a content check). get_vars' values are each
    state's legacy_text, "" where the value isn't visible.
    """

    is_deployment_manifest = True

    def __init__(self, container: str | None = None, prefer: str | None = None):
        self.container = container
        # A soft hint (typically the --service name), tried only when the
        # manifest is otherwise ambiguous and no explicit --container was
        # given -- see DockerComposeParser for the same reasoning.
        self.prefer = prefer

    def get_vars(
        self, file_path: str, get_values: bool = False
    ) -> set[str] | dict[str, str]:
        states = self.get_states(file_path)
        if not get_values:
            return set(states)
        return {key: legacy_text(state) or "" for key, state in states.items()}

    def get_states(self, file_path: str) -> ValueStates:
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        with open(file_path, "r") as f:
            docs = [d for d in yaml.safe_load_all(f) if isinstance(d, dict)]

        config_maps: dict[str, dict[str, str]] = {}
        secrets: dict[str, set[str]] = {}
        main_containers: list[dict[str, Any]] = []
        init_containers: list[dict[str, Any]] = []

        for doc in docs:
            kind = doc.get("kind")
            name = (doc.get("metadata") or {}).get("name")
            if kind == "ConfigMap" and name:
                config_maps[name] = {
                    k: str(v) for k, v in (doc.get("data") or {}).items()
                }
            elif kind == "Secret" and name:
                secrets[name] = set((doc.get("data") or {}).keys()) | set(
                    (doc.get("stringData") or {}).keys()
                )

            pod_spec = _extract_pod_spec(doc)
            if pod_spec:
                main_containers.extend(pod_spec.get("containers") or [])
                init_containers.extend(pod_spec.get("initContainers") or [])

        if not main_containers:
            return ValueStates()

        # Default (no --container) selection considers only main
        # containers -- unchanged from before initContainers were
        # selectable at all, so a manifest with one main container plus
        # any number of init containers keeps auto-selecting that main
        # container with no flag required (BL-011 #4).
        container_name = self.container
        if container_name is None:
            names_in_order = [c.get("name") for c in main_containers]
            if len(main_containers) == 1:
                container_name = names_in_order[0]
            elif self.prefer and self.prefer in names_in_order:
                container_name = self.prefer
            else:
                names = ", ".join(n or "?" for n in names_in_order)
                raise EnvShieldException(
                    f"This manifest declares multiple containers ({names}) -- pass --container to pick one."
                    " To check every container, register this file in "
                    "envshield.yml under 'manifests:' with a 'containers:' map "
                    "(container: service), then run 'envshield check'."
                )

        # An explicit --container may name either a main or an init
        # container -- both are real, independently-configurable
        # containers in the same pod. Main containers are searched first,
        # matching the name any single-main-container manifest already
        # auto-selected above.
        all_containers = main_containers + init_containers
        target = next(
            (c for c in all_containers if c.get("name") == container_name), None
        )
        if target is None:
            names = ", ".join(c.get("name", "?") for c in all_containers)
            raise EnvShieldException(
                f"Container '{container_name}' not found in this manifest. Available: {names}"
            )

        variables: dict[str, ValueState] = {}
        unbound: list[External] = []
        for env_entry in target.get("env") or []:
            name = env_entry.get("name")
            if not name:
                continue
            if "value" in env_entry:
                variables[name] = Concrete(str(env_entry["value"]))
                continue

            value_from = env_entry.get("valueFrom") or {}
            if not value_from:
                variables[name] = Concrete("")
                continue
            # env[].name is the injected variable name regardless of what
            # key the Secret/ConfigMap stores it under. The referenced *key*
            # is cross-checked (BL-011 #3) only when the object is defined
            # in this same manifest; otherwise its keys aren't knowable here.
            kind = "secretKeyRef"
            key_ref = value_from.get(kind)
            known_keys: dict[str, str] | set[str] | None = None
            if isinstance(key_ref, dict):
                known_keys = secrets.get(key_ref.get("name"))
            else:
                kind = "configMapKeyRef"
                key_ref = value_from.get(kind)
                if isinstance(key_ref, dict):
                    known_keys = config_maps.get(key_ref.get("name"))

            if known_keys is not None:
                if key_ref.get("key") in known_keys:
                    variables[name] = KeyPresent(key_ref["name"])
                # else: broken same-file key reference -- Absent
                continue
            if key_ref is None:
                kind = next(iter(value_from))
            variables[name] = External(_ref_name(value_from[kind]), kind)

        for env_from in target.get("envFrom") or []:
            # 'prefix' is prepended to every key pulled from this source --
            # it's what the container actually receives (BL-011 #2). An
            # absent or empty prefix is a no-op, matching real Kubernetes
            # semantics exactly (a bare "" is not a distinct case).
            prefix = env_from.get("prefix") or ""
            cm_ref = (env_from.get("configMapRef") or {}).get("name")
            if cm_ref:
                if cm_ref in config_maps:
                    for key, value in config_maps[cm_ref].items():
                        variables.setdefault(prefix + key, Concrete(value))
                else:
                    unbound.append(External(cm_ref, "configMapRef"))
            secret_ref = (env_from.get("secretRef") or {}).get("name")
            if secret_ref:
                if secret_ref in secrets:
                    for key in secrets[secret_ref]:
                        variables.setdefault(prefix + key, KeyPresent(secret_ref))
                else:
                    unbound.append(External(secret_ref, "secretRef"))

        return ValueStates(variables, unbound)


def _ref_name(ref: Any) -> str:
    """The name a valueFrom reference points at, "" if it has none."""
    if not isinstance(ref, dict):
        return ""
    return str(ref.get("name") or ref.get("fieldPath") or ref.get("resource") or "")
