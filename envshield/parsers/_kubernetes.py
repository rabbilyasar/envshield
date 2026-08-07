# envshield/parsers/_kubernetes.py
import os
from typing import Any

import yaml

from ..core.exceptions import EnvShieldException
from ._base import BaseParser

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

    A value sourced from 'envFrom' (a ConfigMap/Secret reference) is
    resolved only if that ConfigMap/Secret is itself defined in the same
    file; a 'valueFrom' entry, or an unresolvable envFrom reference, is
    reported as present with a placeholder value, since its real value
    lives in the cluster, not in this file. A Secret's own data values are
    never decoded even when present (they're base64, and this is a
    presence check, not a content check).
    """

    UNRESOLVED_VALUE = "<value not visible in this file>"

    def __init__(self, container: str | None = None, prefer: str | None = None):
        self.container = container
        # A soft hint (typically the --service name), tried only when the
        # manifest is otherwise ambiguous and no explicit --container was
        # given -- see DockerComposeParser for the same reasoning.
        self.prefer = prefer

    def get_vars(
        self, file_path: str, get_values: bool = False
    ) -> set[str] | dict[str, str]:
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        with open(file_path, "r") as f:
            docs = [d for d in yaml.safe_load_all(f) if isinstance(d, dict)]

        config_maps: dict[str, dict[str, str]] = {}
        secrets: dict[str, set[str]] = {}
        containers: list[dict[str, Any]] = []

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
                containers.extend(pod_spec.get("containers") or [])

        if not containers:
            return {} if get_values else set()

        container_name = self.container
        if container_name is None:
            names_in_order = [c.get("name") for c in containers]
            if len(containers) == 1:
                container_name = names_in_order[0]
            elif self.prefer and self.prefer in names_in_order:
                container_name = self.prefer
            else:
                names = ", ".join(n or "?" for n in names_in_order)
                raise EnvShieldException(
                    f"This manifest declares multiple containers ({names}) -- pass --container to pick one."
                )

        target = next((c for c in containers if c.get("name") == container_name), None)
        if target is None:
            names = ", ".join(c.get("name", "?") for c in containers)
            raise EnvShieldException(
                f"Container '{container_name}' not found in this manifest. Available: {names}"
            )

        variables: dict[str, str] = {}
        for env_entry in target.get("env") or []:
            name = env_entry.get("name")
            if not name:
                continue
            variables[name] = (
                str(env_entry["value"])
                if "value" in env_entry
                else self.UNRESOLVED_VALUE
            )

        for env_from in target.get("envFrom") or []:
            cm_ref = (env_from.get("configMapRef") or {}).get("name")
            if cm_ref and cm_ref in config_maps:
                for key, value in config_maps[cm_ref].items():
                    variables.setdefault(key, value)
            secret_ref = (env_from.get("secretRef") or {}).get("name")
            if secret_ref and secret_ref in secrets:
                for key in secrets[secret_ref]:
                    variables.setdefault(key, self.UNRESOLVED_VALUE)

        return variables if get_values else set(variables.keys())
