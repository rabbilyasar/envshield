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


class TestEnvFromUnresolvedReference:
    """
    Regression: an envFrom.configMapRef/secretRef naming a ConfigMap/Secret
    that ISN'T defined anywhere in the supplied manifest used to silently
    contribute zero variables -- indistinguishable from the reference not
    existing at all. That's a false negative: the object is very likely
    real (just externally managed, e.g. by Helm/Kustomize, or applied
    separately), and could supply variables this parser has no way to
    enumerate. has_unresolved_source now surfaces that ambiguity instead
    of silently resolving to "nothing".
    """

    def test_configmap_defined_in_the_manifest_is_not_unresolved(self, tmp_path):
        """Requirement #1 and #8: a locally-defined ConfigMap still resolves cleanly, no ambiguity flagged."""
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
        parser = KubernetesParser()

        variables = parser.get_vars(str(f), get_values=True)

        assert variables == {"LOG_LEVEL": "info"}
        assert parser.has_unresolved_source is False

    def test_secret_defined_in_the_manifest_is_not_unresolved(self, tmp_path):
        """Requirement #2 and #8: a locally-defined Secret still resolves cleanly, no ambiguity flagged."""
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
        parser = KubernetesParser()

        variables = parser.get_vars(str(f), get_values=True)

        assert variables["DATABASE_URL"] == KubernetesParser.UNRESOLVED_VALUE
        assert parser.has_unresolved_source is False

    def test_configmap_ref_not_in_the_manifest_is_unresolved_not_ignored(
        self, tmp_path
    ):
        """Requirement #3: an externally-managed ConfigMap is flagged unresolved, not silently contributing zero variables."""
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
            "          envFrom:\n"
            "            - configMapRef:\n"
            "                name: externally-managed-config\n"
        )
        parser = KubernetesParser()

        variables = parser.get_vars(str(f), get_values=True)

        assert variables == {}
        assert parser.has_unresolved_source is True

    def test_secret_ref_not_in_the_manifest_is_unresolved_not_ignored(self, tmp_path):
        """Requirement #4: an externally-managed Secret is flagged unresolved, not silently contributing zero variables."""
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
            "          envFrom:\n"
            "            - secretRef:\n"
            "                name: externally-managed-secret\n"
        )
        parser = KubernetesParser()

        variables = parser.get_vars(str(f), get_values=True)

        assert variables == {}
        assert parser.has_unresolved_source is True

    def test_optional_flag_does_not_clear_the_unresolved_signal(self, tmp_path):
        """
        'optional: true' only means K8s won't fail pod startup if the
        object is genuinely absent -- it says nothing about whether the
        object exists elsewhere in the cluster and supplies variables this
        parser can't see, so it must not suppress has_unresolved_source.
        """
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
            "          envFrom:\n"
            "            - configMapRef:\n"
            "                name: externally-managed-config\n"
            "                optional: true\n"
        )
        parser = KubernetesParser()

        parser.get_vars(str(f), get_values=True)

        assert parser.has_unresolved_source is True

    def test_a_fresh_parser_instance_defaults_to_not_unresolved(self):
        """The class-level default (BaseParser.has_unresolved_source) before get_vars is ever called."""
        assert KubernetesParser().has_unresolved_source is False

    def test_unresolved_flag_resets_between_calls_on_the_same_instance(self, tmp_path):
        unresolved_file = tmp_path / "unresolved.yaml"
        unresolved_file.write_text(
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
            "                name: externally-managed-config\n"
        )
        clean_file = tmp_path / "clean.yaml"
        clean_file.write_text(
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
            "              value: bar\n"
        )
        parser = KubernetesParser()

        parser.get_vars(str(unresolved_file))
        assert parser.has_unresolved_source is True

        parser.get_vars(str(clean_file))
        assert parser.has_unresolved_source is False


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
