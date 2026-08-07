# envshield/tests/parsers/test_kubernetes_parser.py
import pytest

from envshield.core.exceptions import EnvShieldException
from envshield.parsers._deployment import detect_deployment_format
from envshield.parsers._kubernetes import KubernetesParser


def test_detect_deployment_format_recognizes_kubernetes(tmp_path):
    f = tmp_path / "deployment.yaml"
    f.write_text(
        "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: api\nspec: {}\n"
    )

    assert detect_deployment_format(str(f)) == "kubernetes"


def test_parser_reads_literal_env_values(tmp_path):
    f = tmp_path / "deployment.yaml"
    f.write_text(
        "apiVersion: apps/v1\n"
        "kind: Deployment\n"
        "metadata:\n  name: api\n"
        "spec:\n"
        "  template:\n"
        "    spec:\n"
        "      containers:\n"
        "        - name: api\n"
        "          env:\n"
        "            - name: LOG_LEVEL\n"
        "              value: info\n"
    )

    variables = KubernetesParser().get_vars(str(f), get_values=True)

    assert variables == {"LOG_LEVEL": "info"}


def test_parser_treats_value_from_as_present_placeholder(tmp_path):
    f = tmp_path / "deployment.yaml"
    f.write_text(
        "apiVersion: apps/v1\n"
        "kind: Deployment\n"
        "metadata:\n  name: api\n"
        "spec:\n"
        "  template:\n"
        "    spec:\n"
        "      containers:\n"
        "        - name: api\n"
        "          env:\n"
        "            - name: DATABASE_URL\n"
        "              valueFrom:\n"
        "                secretKeyRef:\n"
        "                  name: db-secret\n"
        "                  key: url\n"
    )

    variables = KubernetesParser().get_vars(str(f), get_values=True)

    assert variables["DATABASE_URL"] == KubernetesParser.UNRESOLVED_VALUE


def test_parser_resolves_configmap_defined_in_the_same_multi_doc_file(tmp_path):
    f = tmp_path / "deployment.yaml"
    f.write_text(
        "apiVersion: v1\n"
        "kind: ConfigMap\n"
        "metadata:\n  name: app-config\n"
        "data:\n  LOG_LEVEL: info\n"
        "---\n"
        "apiVersion: apps/v1\n"
        "kind: Deployment\n"
        "metadata:\n  name: api\n"
        "spec:\n"
        "  template:\n"
        "    spec:\n"
        "      containers:\n"
        "        - name: api\n"
        "          envFrom:\n"
        "            - configMapRef:\n"
        "                name: app-config\n"
    )

    variables = KubernetesParser().get_vars(str(f), get_values=True)

    assert variables == {"LOG_LEVEL": "info"}


def test_parser_resolves_secret_keys_as_present_but_unresolved(tmp_path):
    f = tmp_path / "deployment.yaml"
    f.write_text(
        "apiVersion: v1\n"
        "kind: Secret\n"
        "metadata:\n  name: db-secret\n"
        "data:\n  DATABASE_URL: c29tZS1iYXNlNjQ=\n"
        "---\n"
        "apiVersion: apps/v1\n"
        "kind: Deployment\n"
        "metadata:\n  name: api\n"
        "spec:\n"
        "  template:\n"
        "    spec:\n"
        "      containers:\n"
        "        - name: api\n"
        "          envFrom:\n"
        "            - secretRef:\n"
        "                name: db-secret\n"
    )

    variables = KubernetesParser().get_vars(str(f), get_values=True)

    assert variables["DATABASE_URL"] == KubernetesParser.UNRESOLVED_VALUE


def test_parser_raises_when_container_ambiguous(tmp_path):
    f = tmp_path / "deployment.yaml"
    f.write_text(
        "apiVersion: apps/v1\n"
        "kind: Deployment\n"
        "metadata:\n  name: api\n"
        "spec:\n"
        "  template:\n"
        "    spec:\n"
        "      containers:\n"
        "        - name: api\n"
        "          env: []\n"
        "        - name: sidecar\n"
        "          env: []\n"
    )

    with pytest.raises(EnvShieldException, match="multiple containers"):
        KubernetesParser().get_vars(str(f))


def test_parser_uses_prefer_hint_to_resolve_ambiguity(tmp_path):
    f = tmp_path / "deployment.yaml"
    f.write_text(
        "apiVersion: apps/v1\n"
        "kind: Deployment\n"
        "metadata:\n  name: api\n"
        "spec:\n"
        "  template:\n"
        "    spec:\n"
        "      containers:\n"
        "        - name: api\n"
        "          env:\n"
        "            - name: FOO\n"
        "              value: api-value\n"
        "        - name: sidecar\n"
        "          env:\n"
        "            - name: FOO\n"
        "              value: sidecar-value\n"
    )

    variables = KubernetesParser(prefer="sidecar").get_vars(str(f), get_values=True)

    assert variables == {"FOO": "sidecar-value"}


def test_parser_handles_bare_pod_manifest(tmp_path):
    f = tmp_path / "pod.yaml"
    f.write_text(
        "apiVersion: v1\nkind: Pod\nmetadata:\n  name: debug-pod\nspec:\n  containers:\n    - name: shell\n      env:\n        - name: FOO\n          value: bar\n"
    )

    variables = KubernetesParser().get_vars(str(f), get_values=True)

    assert variables == {"FOO": "bar"}
