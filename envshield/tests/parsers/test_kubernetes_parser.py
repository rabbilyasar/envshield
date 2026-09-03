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


class TestValueFromKeyValidation:
    """
    Regression coverage for BL-011 #3: 'env[].valueFrom.secretKeyRef'/
    'configMapKeyRef' used to be matched purely by 'env[].name', with no
    check that the referenced '.key' actually exists in a same-file
    Secret/ConfigMap -- a broken key reference was reported identically to
    a genuinely-satisfied one. Real Kubernetes fails the pod at apply-time
    (or, with 'optional: true', simply never injects the variable) when
    the key doesn't exist, so a broken same-file reference is now excluded
    from the result entirely rather than reported present.
    """

    def _manifest(self, env_entries: str) -> str:
        return (
            "apiVersion: v1\n"
            "kind: Secret\n"
            "metadata:\n  name: db-secret\n"
            "data:\n  ACTUAL_KEY: dmFsdWU=\n  OTHER_KEY: dmFsdWUy\n"
            "---\n"
            "apiVersion: v1\n"
            "kind: ConfigMap\n"
            "metadata:\n  name: app-config\n"
            "data:\n  REAL_CM_KEY: hello\n"
            "---\n"
            "apiVersion: apps/v1\n"
            "kind: Deployment\n"
            "metadata:\n  name: api\n"
            "spec:\n"
            "  template:\n"
            "    spec:\n"
            "      containers:\n"
            "        - name: api\n"
            "          env:\n" + env_entries
        )

    def test_broken_in_file_secret_key_ref_excludes_the_variable(self, tmp_path):
        f = tmp_path / "deployment.yaml"
        f.write_text(
            self._manifest(
                "            - name: DATABASE_PASSWORD\n"
                "              valueFrom:\n"
                "                secretKeyRef:\n"
                "                  name: db-secret\n"
                "                  key: MISSING_KEY\n"
            )
        )

        variables = KubernetesParser().get_vars(str(f), get_values=True)

        assert "DATABASE_PASSWORD" not in variables

    def test_valid_in_file_secret_key_ref_is_reported_present(self, tmp_path):
        f = tmp_path / "deployment.yaml"
        f.write_text(
            self._manifest(
                "            - name: DATABASE_PASSWORD\n"
                "              valueFrom:\n"
                "                secretKeyRef:\n"
                "                  name: db-secret\n"
                "                  key: ACTUAL_KEY\n"
            )
        )

        variables = KubernetesParser().get_vars(str(f), get_values=True)

        assert variables["DATABASE_PASSWORD"] == KubernetesParser.UNRESOLVED_VALUE

    def test_broken_in_file_config_map_key_ref_excludes_the_variable(self, tmp_path):
        f = tmp_path / "deployment.yaml"
        f.write_text(
            self._manifest(
                "            - name: APP_SETTING\n"
                "              valueFrom:\n"
                "                configMapKeyRef:\n"
                "                  name: app-config\n"
                "                  key: MISSING_CM_KEY\n"
            )
        )

        variables = KubernetesParser().get_vars(str(f), get_values=True)

        assert "APP_SETTING" not in variables

    def test_valid_in_file_config_map_key_ref_is_reported_present(self, tmp_path):
        f = tmp_path / "deployment.yaml"
        f.write_text(
            self._manifest(
                "            - name: APP_SETTING\n"
                "              valueFrom:\n"
                "                configMapKeyRef:\n"
                "                  name: app-config\n"
                "                  key: REAL_CM_KEY\n"
            )
        )

        variables = KubernetesParser().get_vars(str(f), get_values=True)

        assert variables["APP_SETTING"] == KubernetesParser.UNRESOLVED_VALUE

    def test_multiple_env_vars_referencing_different_keys_of_the_same_secret(
        self, tmp_path
    ):
        f = tmp_path / "deployment.yaml"
        f.write_text(
            self._manifest(
                "            - name: FIRST_VAR\n"
                "              valueFrom:\n"
                "                secretKeyRef:\n"
                "                  name: db-secret\n"
                "                  key: ACTUAL_KEY\n"
                "            - name: SECOND_VAR\n"
                "              valueFrom:\n"
                "                secretKeyRef:\n"
                "                  name: db-secret\n"
                "                  key: OTHER_KEY\n"
                "            - name: THIRD_VAR\n"
                "              valueFrom:\n"
                "                secretKeyRef:\n"
                "                  name: db-secret\n"
                "                  key: MISSING_KEY\n"
            )
        )

        variables = KubernetesParser().get_vars(str(f), get_values=True)

        assert variables["FIRST_VAR"] == KubernetesParser.UNRESOLVED_VALUE
        assert variables["SECOND_VAR"] == KubernetesParser.UNRESOLVED_VALUE
        assert "THIRD_VAR" not in variables

    def test_external_secret_reference_keeps_existing_unresolved_behavior(
        self, tmp_path
    ):
        f = tmp_path / "deployment.yaml"
        f.write_text(
            self._manifest(
                "            - name: EXTERNAL_VAR\n"
                "              valueFrom:\n"
                "                secretKeyRef:\n"
                "                  name: not-in-this-file\n"
                "                  key: whatever\n"
            )
        )

        variables = KubernetesParser().get_vars(str(f), get_values=True)

        assert variables["EXTERNAL_VAR"] == KubernetesParser.UNRESOLVED_VALUE

    def test_external_config_map_reference_keeps_existing_unresolved_behavior(
        self, tmp_path
    ):
        f = tmp_path / "deployment.yaml"
        f.write_text(
            self._manifest(
                "            - name: EXTERNAL_VAR\n"
                "              valueFrom:\n"
                "                configMapKeyRef:\n"
                "                  name: not-in-this-file\n"
                "                  key: whatever\n"
            )
        )

        variables = KubernetesParser().get_vars(str(f), get_values=True)

        assert variables["EXTERNAL_VAR"] == KubernetesParser.UNRESOLVED_VALUE

    def test_field_ref_is_unaffected(self, tmp_path):
        f = tmp_path / "deployment.yaml"
        f.write_text(
            self._manifest(
                "            - name: POD_NAME\n"
                "              valueFrom:\n"
                "                fieldRef:\n"
                "                  fieldPath: metadata.name\n"
            )
        )

        variables = KubernetesParser().get_vars(str(f), get_values=True)

        assert variables["POD_NAME"] == KubernetesParser.UNRESOLVED_VALUE

    def test_resource_field_ref_is_unaffected(self, tmp_path):
        f = tmp_path / "deployment.yaml"
        f.write_text(
            self._manifest(
                "            - name: CPU_LIMIT\n"
                "              valueFrom:\n"
                "                resourceFieldRef:\n"
                "                  resource: limits.cpu\n"
            )
        )

        variables = KubernetesParser().get_vars(str(f), get_values=True)

        assert variables["CPU_LIMIT"] == KubernetesParser.UNRESOLVED_VALUE

    def test_literal_value_is_unaffected(self, tmp_path):
        f = tmp_path / "deployment.yaml"
        f.write_text(
            self._manifest("            - name: LOG_LEVEL\n              value: info\n")
        )

        variables = KubernetesParser().get_vars(str(f), get_values=True)

        assert variables["LOG_LEVEL"] == "info"

    def test_malformed_secret_key_ref_missing_its_own_name_falls_back_safely(
        self, tmp_path
    ):
        """
        No 'name:' on the secretKeyRef itself -- the existing lenient
        fallback (present/UNRESOLVED_VALUE) is preserved rather than
        crashing or misbehaving on malformed manifest content.
        """
        f = tmp_path / "deployment.yaml"
        f.write_text(
            self._manifest(
                "            - name: MALFORMED\n"
                "              valueFrom:\n"
                "                secretKeyRef:\n"
                "                  key: ACTUAL_KEY\n"
            )
        )

        variables = KubernetesParser().get_vars(str(f), get_values=True)

        assert variables["MALFORMED"] == KubernetesParser.UNRESOLVED_VALUE


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


class TestEnvFromPrefix:
    """
    Regression coverage for BL-011 #2: envFrom.prefix used to be ignored
    entirely -- a key pulled from a same-file ConfigMap/Secret was reported
    under its own unprefixed name, which the container never actually
    receives, while the real, prefixed name the container does receive was
    never reported at all. A compound, two-sided error: a false "present"
    for a name that doesn't exist in the container's environment, and a
    false "missing" for the one that does.
    """

    def _manifest(self, env_from_entries: str) -> str:
        return (
            "apiVersion: v1\n"
            "kind: ConfigMap\n"
            "metadata:\n  name: app-config\n"
            "data:\n  CM_KEY_A: valueA\n  CM_KEY_B: valueB\n"
            "---\n"
            "apiVersion: v1\n"
            "kind: Secret\n"
            "metadata:\n  name: app-secret\n"
            "data:\n  SECRET_KEY_A: c2VjcmV0YQ==\n  SECRET_KEY_B: c2VjcmV0Yg==\n"
            "---\n"
            "apiVersion: apps/v1\n"
            "kind: Deployment\n"
            "metadata:\n  name: api\n"
            "spec:\n"
            "  template:\n"
            "    spec:\n"
            "      containers:\n"
            "        - name: api\n"
            "          envFrom:\n" + env_from_entries
        )

    def test_config_map_with_prefix_is_reported_under_the_prefixed_name(
        self, tmp_path
    ):
        f = tmp_path / "deployment.yaml"
        f.write_text(
            self._manifest(
                "            - configMapRef:\n"
                "                name: app-config\n"
                "              prefix: CM_\n"
            )
        )

        variables = KubernetesParser().get_vars(str(f), get_values=True)

        assert variables["CM_CM_KEY_A"] == "valueA"
        assert "CM_KEY_A" not in variables

    def test_secret_with_prefix_is_reported_under_the_prefixed_name(self, tmp_path):
        f = tmp_path / "deployment.yaml"
        f.write_text(
            self._manifest(
                "            - secretRef:\n"
                "                name: app-secret\n"
                "              prefix: SEC_\n"
            )
        )

        variables = KubernetesParser().get_vars(str(f), get_values=True)

        assert variables["SEC_SECRET_KEY_A"] == KubernetesParser.UNRESOLVED_VALUE
        assert "SECRET_KEY_A" not in variables

    def test_prefix_applies_independently_to_every_key_under_the_same_source(
        self, tmp_path
    ):
        f = tmp_path / "deployment.yaml"
        f.write_text(
            self._manifest(
                "            - configMapRef:\n"
                "                name: app-config\n"
                "              prefix: CM_\n"
            )
        )

        variables = KubernetesParser().get_vars(str(f), get_values=True)

        assert variables["CM_CM_KEY_A"] == "valueA"
        assert variables["CM_CM_KEY_B"] == "valueB"
        assert "CM_KEY_A" not in variables
        assert "CM_KEY_B" not in variables

    def test_no_prefix_key_is_unchanged_from_existing_behavior(self, tmp_path):
        f = tmp_path / "deployment.yaml"
        f.write_text(
            self._manifest("            - configMapRef:\n                name: app-config\n")
        )

        variables = KubernetesParser().get_vars(str(f), get_values=True)

        assert variables["CM_KEY_A"] == "valueA"
        assert variables["CM_KEY_B"] == "valueB"

    def test_empty_string_prefix_is_a_no_op(self, tmp_path):
        """An empty prefix means no prefix at all -- not a distinct, literal empty-string key."""
        f = tmp_path / "deployment.yaml"
        f.write_text(
            self._manifest(
                "            - configMapRef:\n"
                "                name: app-config\n"
                "              prefix: \"\"\n"
            )
        )

        variables = KubernetesParser().get_vars(str(f), get_values=True)

        assert variables["CM_KEY_A"] == "valueA"

    def test_null_prefix_is_a_no_op(self, tmp_path):
        """An explicit YAML null ('prefix:' with no value) behaves the same as an absent key."""
        f = tmp_path / "deployment.yaml"
        f.write_text(
            self._manifest(
                "            - configMapRef:\n"
                "                name: app-config\n"
                "              prefix:\n"
            )
        )

        variables = KubernetesParser().get_vars(str(f), get_values=True)

        assert variables["CM_KEY_A"] == "valueA"

    def test_external_config_map_reference_with_prefix_stays_unresolved_not_ignored(
        self, tmp_path
    ):
        """
        A ConfigMap not defined in this file has unknowable keys, so there's
        nothing to prefix -- existing has_unresolved_source behavior is
        preserved exactly, not silently dropped because a prefix was given.
        """
        f = tmp_path / "deployment.yaml"
        f.write_text(
            self._manifest(
                "            - configMapRef:\n"
                "                name: not-in-this-file\n"
                "              prefix: EXT_\n"
            )
        )
        parser = KubernetesParser()

        variables = parser.get_vars(str(f), get_values=True)

        assert variables == {}
        assert parser.has_unresolved_source is True

    def test_external_secret_reference_with_prefix_stays_unresolved_not_ignored(
        self, tmp_path
    ):
        f = tmp_path / "deployment.yaml"
        f.write_text(
            self._manifest(
                "            - secretRef:\n"
                "                name: not-in-this-file\n"
                "              prefix: EXT_\n"
            )
        )
        parser = KubernetesParser()

        variables = parser.get_vars(str(f), get_values=True)

        assert variables == {}
        assert parser.has_unresolved_source is True

    def test_prefix_on_configmap_does_not_affect_a_sibling_unprefixed_secret(
        self, tmp_path
    ):
        f = tmp_path / "deployment.yaml"
        f.write_text(
            self._manifest(
                "            - configMapRef:\n"
                "                name: app-config\n"
                "              prefix: CM_\n"
                "            - secretRef:\n"
                "                name: app-secret\n"
            )
        )

        variables = KubernetesParser().get_vars(str(f), get_values=True)

        assert variables["CM_CM_KEY_A"] == "valueA"
        assert variables["SECRET_KEY_A"] == KubernetesParser.UNRESOLVED_VALUE


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


class TestInitContainers:
    """
    Regression coverage for BL-011 #4: initContainers were never inspected
    at all -- not even selectable via --container -- so a variable set
    only on an init container (a migration/setup job's own required
    config) was invisible no matter what. initContainers are now treated
    as additional, explicitly-selectable containers, exactly like a main
    container, but are never auto-selected by default -- a manifest with
    one main container plus any number of init containers must keep
    auto-selecting that main container with no flag required, unchanged
    from before this existed.
    """

    def _manifest(self, extra_containers: str = "") -> str:
        return (
            "apiVersion: apps/v1\n"
            "kind: Deployment\n"
            "metadata:\n  name: app\n"
            "spec:\n"
            "  template:\n"
            "    spec:\n"
            "      initContainers:\n"
            "        - name: init\n"
            "          env:\n"
            "            - name: MIGRATION_FLAG\n"
            "              value: \"true\"\n"
            "      containers:\n"
            "        - name: app\n"
            "          env:\n"
            "            - name: LOG_LEVEL\n"
            "              value: info\n" + extra_containers
        )

    def test_variable_only_in_an_init_container_is_invisible_by_default(
        self, tmp_path
    ):
        """
        Unchanged behavior: a schema check with no --container still
        validates against the main container, exactly as before
        initContainers were selectable at all.
        """
        f = tmp_path / "deployment.yaml"
        f.write_text(self._manifest())

        variables = KubernetesParser().get_vars(str(f), get_values=True)

        assert variables == {"LOG_LEVEL": "info"}
        assert "MIGRATION_FLAG" not in variables

    def test_variable_only_in_an_init_container_is_visible_when_explicitly_selected(
        self, tmp_path
    ):
        f = tmp_path / "deployment.yaml"
        f.write_text(self._manifest())

        variables = KubernetesParser(container="init").get_vars(
            str(f), get_values=True
        )

        assert variables == {"MIGRATION_FLAG": "true"}

    def test_a_variable_present_in_both_main_and_init_container_is_resolved_independently(
        self, tmp_path
    ):
        f = tmp_path / "deployment.yaml"
        f.write_text(
            "apiVersion: apps/v1\n"
            "kind: Deployment\n"
            "metadata:\n  name: app\n"
            "spec:\n"
            "  template:\n"
            "    spec:\n"
            "      initContainers:\n"
            "        - name: init\n"
            "          env:\n"
            "            - name: SHARED_VAR\n"
            "              value: init-value\n"
            "      containers:\n"
            "        - name: app\n"
            "          env:\n"
            "            - name: SHARED_VAR\n"
            "              value: main-value\n"
        )

        assert KubernetesParser().get_vars(str(f), get_values=True) == {
            "SHARED_VAR": "main-value"
        }
        assert KubernetesParser(container="init").get_vars(str(f), get_values=True) == {
            "SHARED_VAR": "init-value"
        }

    def test_multiple_init_containers_are_each_independently_selectable(
        self, tmp_path
    ):
        f = tmp_path / "deployment.yaml"
        f.write_text(
            "apiVersion: apps/v1\n"
            "kind: Deployment\n"
            "metadata:\n  name: app\n"
            "spec:\n"
            "  template:\n"
            "    spec:\n"
            "      initContainers:\n"
            "        - name: init-a\n"
            "          env:\n"
            "            - name: FLAG_A\n"
            "              value: a\n"
            "        - name: init-b\n"
            "          env:\n"
            "            - name: FLAG_B\n"
            "              value: b\n"
            "      containers:\n"
            "        - name: app\n"
            "          env:\n"
            "            - name: LOG_LEVEL\n"
            "              value: info\n"
        )

        assert KubernetesParser(container="init-a").get_vars(str(f), get_values=True) == {
            "FLAG_A": "a"
        }
        assert KubernetesParser(container="init-b").get_vars(str(f), get_values=True) == {
            "FLAG_B": "b"
        }
        # Default selection is still unaffected by however many init containers exist.
        assert KubernetesParser().get_vars(str(f), get_values=True) == {
            "LOG_LEVEL": "info"
        }

    def test_single_main_container_plus_init_containers_still_auto_selects_with_no_flag(
        self, tmp_path
    ):
        """
        The critical non-regression: this exact shape (one main container,
        one or more init containers) is an extremely common real manifest
        -- it must not newly become "ambiguous, pass --container."
        """
        f = tmp_path / "deployment.yaml"
        f.write_text(self._manifest())

        variables = KubernetesParser().get_vars(str(f), get_values=True)

        assert variables == {"LOG_LEVEL": "info"}

    def test_multiple_main_containers_plus_an_init_container_ambiguity_lists_only_main_containers(
        self, tmp_path
    ):
        """
        Ambiguity resolution for the default (no --container) case is
        unaffected by init containers -- they were never part of it and
        still aren't; the error must not mislead by listing a container
        that was never a candidate for auto-selection.
        """
        f = tmp_path / "deployment.yaml"
        f.write_text(
            self._manifest(
                "        - name: sidecar\n"
                "          env:\n"
                "            - name: SIDECAR_VAR\n"
                "              value: x\n"
            )
        )

        with pytest.raises(EnvShieldException, match="multiple containers") as exc_info:
            KubernetesParser().get_vars(str(f))

        assert "init" not in str(exc_info.value)

    def test_not_found_error_lists_both_main_and_init_containers(self, tmp_path):
        """
        Since an init container is now a legitimate explicit --container
        target, the error naming what's actually available must include
        it -- otherwise the message would be misleadingly incomplete.
        """
        f = tmp_path / "deployment.yaml"
        f.write_text(self._manifest())

        with pytest.raises(EnvShieldException, match="Available: app, init"):
            KubernetesParser(container="typo").get_vars(str(f))

    def test_prefer_hint_never_resolves_to_an_init_container(self, tmp_path):
        """
        --service's soft-match hint is scoped to main containers only --
        an init container coincidentally sharing a service's name must
        never be silently auto-selected by it.
        """
        f = tmp_path / "deployment.yaml"
        f.write_text(
            "apiVersion: apps/v1\n"
            "kind: Deployment\n"
            "metadata:\n  name: app\n"
            "spec:\n"
            "  template:\n"
            "    spec:\n"
            "      initContainers:\n"
            "        - name: web\n"
            "          env:\n"
            "            - name: INIT_ONLY_VAR\n"
            "              value: should-not-be-selected\n"
            "      containers:\n"
            "        - name: api\n"
            "          env:\n"
            "            - name: MAIN_VAR\n"
            "              value: main\n"
            "        - name: web\n"
            "          env:\n"
            "            - name: LOG_LEVEL\n"
            "              value: info\n"
        )

        variables = KubernetesParser(prefer="web").get_vars(str(f), get_values=True)

        assert variables == {"LOG_LEVEL": "info"}

    def test_no_main_containers_still_returns_empty_regardless_of_init_containers(
        self, tmp_path
    ):
        """
        Pre-existing behavior for a manifest with no main containers
        (BL-011 #5's own territory, untouched here) must not change just
        because init containers now participate in explicit selection.
        """
        f = tmp_path / "deployment.yaml"
        f.write_text(
            "apiVersion: apps/v1\n"
            "kind: Deployment\n"
            "metadata:\n  name: app\n"
            "spec:\n"
            "  template:\n"
            "    spec:\n"
            "      initContainers:\n"
            "        - name: init\n"
            "          env:\n"
            "            - name: MIGRATION_FLAG\n"
            "              value: \"true\"\n"
            "      containers: []\n"
        )

        variables = KubernetesParser().get_vars(str(f), get_values=True)

        assert variables == {}

    def test_env_from_on_an_init_container_still_works(self, tmp_path):
        """envFrom/env resolution logic itself is unchanged -- it operates
        identically on whichever container is selected."""
        f = tmp_path / "deployment.yaml"
        f.write_text(
            "apiVersion: v1\n"
            "kind: ConfigMap\n"
            "metadata:\n  name: app-config\n"
            "data:\n  MIGRATION_TARGET: v2\n"
            "---\n"
            "apiVersion: apps/v1\n"
            "kind: Deployment\n"
            "metadata:\n  name: app\n"
            "spec:\n"
            "  template:\n"
            "    spec:\n"
            "      initContainers:\n"
            "        - name: init\n"
            "          envFrom:\n"
            "            - configMapRef:\n"
            "                name: app-config\n"
            "      containers:\n"
            "        - name: app\n"
            "          env:\n"
            "            - name: LOG_LEVEL\n"
            "              value: info\n"
        )

        variables = KubernetesParser(container="init").get_vars(str(f), get_values=True)

        assert variables == {"MIGRATION_TARGET": "v2"}


def test_parser_handles_bare_pod_manifest(tmp_path):
    f = tmp_path / "pod.yaml"
    f.write_text(
        "apiVersion: v1\nkind: Pod\nmetadata:\n  name: debug-pod\nspec:\n  containers:\n    - name: shell\n      env:\n        - name: FOO\n          value: bar\n"
    )

    variables = KubernetesParser().get_vars(str(f), get_values=True)

    assert variables == {"FOO": "bar"}
