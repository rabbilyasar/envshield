# envshield/tests/parsers/test_docker_compose_parser.py
import pytest

from envshield.core.exceptions import EnvShieldException
from envshield.parsers._deployment import detect_deployment_format
from envshield.parsers._docker_compose import DockerComposeParser


def test_detect_deployment_format_recognizes_docker_compose(tmp_path):
    f = tmp_path / "docker-compose.yml"
    f.write_text("services:\n  api:\n    image: x\n")

    assert detect_deployment_format(str(f)) == "docker-compose"


def test_detect_deployment_format_returns_none_for_unrelated_yaml(tmp_path):
    f = tmp_path / "other.yml"
    f.write_text("foo: bar\nbaz: 1\n")

    assert detect_deployment_format(str(f)) is None


def test_parser_auto_selects_the_sole_service(tmp_path):
    f = tmp_path / "docker-compose.yml"
    f.write_text("services:\n  api:\n    environment:\n      - FOO=bar\n")

    variables = DockerComposeParser().get_vars(str(f), get_values=True)

    assert variables == {"FOO": "bar"}


def test_parser_raises_when_container_ambiguous(tmp_path):
    f = tmp_path / "docker-compose.yml"
    f.write_text("services:\n  api:\n    image: x\n  worker:\n    image: y\n")

    with pytest.raises(EnvShieldException, match="multiple services"):
        DockerComposeParser().get_vars(str(f))


def test_parser_uses_prefer_hint_to_resolve_ambiguity(tmp_path):
    """A soft --service-name hint resolves an otherwise-ambiguous file without needing an explicit --container."""
    f = tmp_path / "docker-compose.yml"
    f.write_text(
        "services:\n  api:\n    environment:\n      - FOO=api-value\n  worker:\n    environment:\n      - FOO=worker-value\n"
    )

    variables = DockerComposeParser(prefer="worker").get_vars(str(f), get_values=True)

    assert variables == {"FOO": "worker-value"}


def test_parser_ignores_prefer_hint_that_does_not_match_any_service(tmp_path):
    """A hint that doesn't match anything still raises the normal ambiguity error, not a silent wrong guess."""
    f = tmp_path / "docker-compose.yml"
    f.write_text("services:\n  api:\n    image: x\n  worker:\n    image: y\n")

    with pytest.raises(EnvShieldException, match="multiple services"):
        DockerComposeParser(prefer="does-not-exist").get_vars(str(f))


def test_parser_explicit_container_wins_over_prefer_hint(tmp_path):
    f = tmp_path / "docker-compose.yml"
    f.write_text(
        "services:\n  api:\n    environment:\n      - FOO=api-value\n  worker:\n    environment:\n      - FOO=worker-value\n"
    )

    variables = DockerComposeParser(container="api", prefer="worker").get_vars(
        str(f), get_values=True
    )

    assert variables == {"FOO": "api-value"}


def test_parser_raises_for_unknown_container(tmp_path):
    f = tmp_path / "docker-compose.yml"
    f.write_text("services:\n  api:\n    image: x\n")

    with pytest.raises(EnvShieldException, match="not found"):
        DockerComposeParser(container="worker").get_vars(str(f))


def test_parser_merges_env_file_with_environment_block(tmp_path):
    """'environment:' wins over 'env_file:' on a key conflict, matching docker-compose's own precedence."""
    (tmp_path / ".env").write_text(
        "DATABASE_URL=from-env-file\nREDIS_URL=redis://cache\n"
    )
    f = tmp_path / "docker-compose.yml"
    f.write_text(
        "services:\n  api:\n    env_file:\n      - .env\n    environment:\n      - DATABASE_URL=from-environment-block\n"
    )

    variables = DockerComposeParser().get_vars(str(f), get_values=True)

    assert variables["DATABASE_URL"] == "from-environment-block"
    assert variables["REDIS_URL"] == "redis://cache"


def test_parser_treats_bare_environment_entry_as_present_not_blank(tmp_path):
    """A bare 'KEY' entry (no '=') passes through from the host shell -- it's present, just not visible here."""
    f = tmp_path / "docker-compose.yml"
    f.write_text("services:\n  api:\n    environment:\n      - HOST_SECRET\n")

    variables = DockerComposeParser().get_vars(str(f), get_values=True)

    assert variables["HOST_SECRET"] == DockerComposeParser.UNRESOLVED_VALUE


def test_parser_supports_mapping_style_environment(tmp_path):
    f = tmp_path / "docker-compose.yml"
    f.write_text(
        "services:\n  api:\n    environment:\n      FOO: bar\n      COUNT: 3\n"
    )

    variables = DockerComposeParser().get_vars(str(f), get_values=True)

    assert variables == {"FOO": "bar", "COUNT": "3"}


def test_parser_raises_clean_error_for_multi_document_yaml(tmp_path):
    """
    yaml.safe_load only ever handles a single YAML document and raises
    ComposerError (uncaught) if the file contains more than one
    '---'-separated document -- must surface as a clean EnvShieldException,
    not a raw YAML library crash.
    """
    f = tmp_path / "docker-compose.yml"
    f.write_text(
        "services:\n  api:\n    image: x\n---\nservices:\n  worker:\n    image: y\n"
    )

    with pytest.raises(EnvShieldException, match="multiple YAML documents"):
        DockerComposeParser().get_vars(str(f))


class TestEnvFileLongForm:
    """
    Regression coverage for the Compose Spec long-form 'env_file:' entry
    ({path: ..., required: ...}), which used to crash with a TypeError from
    os.path.join(base_dir, {"path": ...}) -- the loop assumed every entry
    was a plain string.
    """

    def test_dict_entry_with_existing_file_loads_variables(self, tmp_path):
        (tmp_path / ".env").write_text("FOO=bar\n")
        f = tmp_path / "docker-compose.yml"
        f.write_text(
            "services:\n  api:\n    env_file:\n      - path: .env\n        required: true\n"
        )

        variables = DockerComposeParser().get_vars(str(f), get_values=True)

        assert variables == {"FOO": "bar"}

    def test_dict_entry_with_missing_optional_file_is_skipped_not_crashed(
        self, tmp_path
    ):
        f = tmp_path / "docker-compose.yml"
        f.write_text(
            "services:\n  api:\n    env_file:\n      - path: .env.missing\n        required: false\n"
        )

        variables = DockerComposeParser().get_vars(str(f), get_values=True)

        assert variables == {}


class TestVariableInterpolation:
    """
    Regression coverage for DI-2: a value using Compose's own
    '${VAR}'/'${VAR:-default}' shell-style interpolation syntax used to be
    stored and validated as the literal, un-interpolated template text
    (e.g. the string '${API_BASE_URL}' itself), which fails any declared
    schema type -- a false positive in doctor/check's manifest validation
    every time a real Compose file used this extremely common syntax.
    """

    def test_reference_with_a_default_resolves_to_the_default(self, tmp_path):
        f = tmp_path / "docker-compose.yml"
        f.write_text(
            "services:\n  api:\n    environment:\n      DB_PORT: ${DB_PORT:-3307}\n"
        )

        variables = DockerComposeParser().get_vars(str(f), get_values=True)

        assert variables["DB_PORT"] == "3307"

    def test_reference_with_no_default_is_reported_unresolved_not_literal(
        self, tmp_path
    ):
        f = tmp_path / "docker-compose.yml"
        f.write_text(
            "services:\n"
            "  frontend:\n"
            "    environment:\n"
            "      API_BASE_URL: ${API_BASE_URL}\n"
        )

        variables = DockerComposeParser().get_vars(str(f), get_values=True)

        assert variables["API_BASE_URL"] == DockerComposeParser.UNRESOLVED_VALUE
        assert variables["API_BASE_URL"] != "${API_BASE_URL}"

    def test_list_style_environment_also_resolves_interpolation(self, tmp_path):
        f = tmp_path / "docker-compose.yml"
        f.write_text(
            "services:\n"
            "  api:\n"
            "    environment:\n"
            "      - FLASK_APP=${FLASK_APP:-app}\n"
            "      - QUEUE_URL=${QUEUE_URL}\n"
        )

        variables = DockerComposeParser().get_vars(str(f), get_values=True)

        assert variables["FLASK_APP"] == "app"
        assert variables["QUEUE_URL"] == DockerComposeParser.UNRESOLVED_VALUE

    def test_embedded_reference_inside_a_larger_string_is_left_literal(self, tmp_path):
        """
        Out of scope by design: resolving a partial reference embedded in a
        larger string would require modeling Compose's full shell-style
        expansion grammar. Only a value that is entirely one reference is
        resolved.
        """
        f = tmp_path / "docker-compose.yml"
        f.write_text(
            "services:\n"
            "  api:\n"
            "    environment:\n"
            "      URL: https://${HOST:-localhost}/api\n"
        )

        variables = DockerComposeParser().get_vars(str(f), get_values=True)

        assert variables["URL"] == "https://${HOST:-localhost}/api"


class TestInterpolationComparesAgainstHostVariableName:
    """
    Regression coverage for PDF finding 3.6: a whole-value '${VAR}' /
    '${VAR:-default}' reference used to be stored under the
    container-facing key it's assigned to, not the host-facing name it
    actually references -- e.g. 'AUTHENTIK_POSTGRESQL__PASSWORD: ${PG_PASS}'
    contributed an AUTHENTIK_POSTGRESQL__PASSWORD entry instead of a
    PG_PASS one, which made schema comparison check the wrong name
    entirely (PG_PASS reported missing; the container-internal rename
    reported as a spurious "extra" variable). DI-2 already fixed the
    *value* side of interpolation (see TestVariableInterpolation above);
    this class covers the *key* side.
    """

    def test_renamed_variable_with_no_default_is_keyed_by_host_name(self, tmp_path):
        f = tmp_path / "docker-compose.yml"
        f.write_text(
            "services:\n"
            "  server:\n"
            "    environment:\n"
            "      AUTHENTIK_POSTGRESQL__PASSWORD: ${PG_PASS}\n"
        )

        variables = DockerComposeParser().get_vars(str(f), get_values=True)

        assert variables == {"PG_PASS": DockerComposeParser.UNRESOLVED_VALUE}
        assert "AUTHENTIK_POSTGRESQL__PASSWORD" not in variables

    def test_renamed_variable_with_default_is_keyed_by_host_name(self, tmp_path):
        f = tmp_path / "docker-compose.yml"
        f.write_text(
            "services:\n"
            "  server:\n"
            "    environment:\n"
            "      AUTHENTIK_POSTGRESQL__HOST: ${PG_HOST:-localhost}\n"
        )

        variables = DockerComposeParser().get_vars(str(f), get_values=True)

        assert variables == {"PG_HOST": "localhost"}
        assert "AUTHENTIK_POSTGRESQL__HOST" not in variables

    def test_same_name_interpolation_is_unaffected(self, tmp_path):
        """Non-regression: the container key already equals the host name in the common case."""
        f = tmp_path / "docker-compose.yml"
        f.write_text(
            "services:\n  api:\n    environment:\n      DB_PORT: ${DB_PORT:-3307}\n"
        )

        variables = DockerComposeParser().get_vars(str(f), get_values=True)

        assert variables == {"DB_PORT": "3307"}

    def test_explicit_empty_default_is_keyed_by_host_name_and_reported_blank_ready(
        self, tmp_path
    ):
        f = tmp_path / "docker-compose.yml"
        f.write_text(
            "services:\n"
            "  server:\n"
            "    environment:\n"
            "      AUTHENTIK_POSTGRESQL__PASSWORD: ${PG_PASS:-}\n"
        )

        variables = DockerComposeParser().get_vars(str(f), get_values=True)

        assert variables == {"PG_PASS": ""}

    def test_multiple_renamed_variables_each_keyed_by_their_own_host_name(
        self, tmp_path
    ):
        f = tmp_path / "docker-compose.yml"
        f.write_text(
            "services:\n"
            "  server:\n"
            "    environment:\n"
            "      AUTHENTIK_POSTGRESQL__PASSWORD: ${PG_PASS}\n"
            "      AUTHENTIK_POSTGRESQL__HOST: ${PG_HOST:-localhost}\n"
        )

        variables = DockerComposeParser().get_vars(str(f), get_values=True)

        assert variables == {
            "PG_PASS": DockerComposeParser.UNRESOLVED_VALUE,
            "PG_HOST": "localhost",
        }

    def test_multiple_container_keys_referencing_the_same_host_variable_collapse(
        self, tmp_path
    ):
        """
        Two container-facing names both interpolating the same host
        variable collapse to one schema-comparison entry -- the schema's
        contract is about the host variable, not about how many container
        keys happen to consume it.
        """
        f = tmp_path / "docker-compose.yml"
        f.write_text(
            "services:\n"
            "  server:\n"
            "    environment:\n"
            "      FIRST_CONSUMER: ${SHARED_SECRET}\n"
            "      SECOND_CONSUMER: ${SHARED_SECRET}\n"
        )

        variables = DockerComposeParser().get_vars(str(f), get_values=True)

        assert variables == {"SHARED_SECRET": DockerComposeParser.UNRESOLVED_VALUE}

    def test_list_style_renamed_variable_is_keyed_by_host_name(self, tmp_path):
        f = tmp_path / "docker-compose.yml"
        f.write_text(
            "services:\n"
            "  server:\n"
            "    environment:\n"
            "      - AUTHENTIK_POSTGRESQL__PASSWORD=${PG_PASS}\n"
        )

        variables = DockerComposeParser().get_vars(str(f), get_values=True)

        assert variables == {"PG_PASS": DockerComposeParser.UNRESOLVED_VALUE}

    def test_embedded_reference_is_still_keyed_by_container_name(self, tmp_path):
        """Non-regression: an embedded (not whole-value) reference never triggers the rename -- no host name to extract."""
        f = tmp_path / "docker-compose.yml"
        f.write_text(
            "services:\n"
            "  api:\n"
            "    environment:\n"
            "      URL: https://${HOST:-localhost}/api\n"
        )

        variables = DockerComposeParser().get_vars(str(f), get_values=True)

        assert variables == {"URL": "https://${HOST:-localhost}/api"}

    def test_environment_block_rename_still_overrides_env_file(self, tmp_path):
        """The existing 'environment: wins over env_file:' precedence still applies when the winning entry is a rename."""
        (tmp_path / ".env").write_text("PG_PASS=from-env-file\n")
        f = tmp_path / "docker-compose.yml"
        f.write_text(
            "services:\n"
            "  server:\n"
            "    env_file:\n"
            "      - .env\n"
            "    environment:\n"
            "      AUTHENTIK_POSTGRESQL__PASSWORD: ${PG_PASS:-from-default}\n"
        )

        variables = DockerComposeParser().get_vars(str(f), get_values=True)

        assert variables["PG_PASS"] == "from-default"
