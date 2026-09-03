# envshield/tests/parsers/test_docker_compose_parser.py
import pytest

from envshield.core.exceptions import EnvShieldException, UnsafePathError
from envshield.parsers._deployment import (
    detect_deployment_format,
    looks_like_unrendered_helm_template,
)
from envshield.parsers._docker_compose import DockerComposeParser


def test_detect_deployment_format_recognizes_docker_compose(tmp_path):
    f = tmp_path / "docker-compose.yml"
    f.write_text("services:\n  api:\n    image: x\n")

    assert detect_deployment_format(str(f)) == "docker-compose"


def test_detect_deployment_format_returns_none_for_unrelated_yaml(tmp_path):
    f = tmp_path / "other.yml"
    f.write_text("foo: bar\nbaz: 1\n")

    assert detect_deployment_format(str(f)) is None


class TestLooksLikeUnrenderedHelmTemplate:
    """
    Regression coverage for PDF finding 3.9: a '.yml'/'.yaml' file whose
    YAML parse fails because of unrendered '{{ ... }}' Go-template syntax
    is a distinct, common cause from genuinely invalid/unrelated YAML, and
    schema_manager's error message should be able to tell them apart.
    """

    def test_true_for_a_helm_style_template_expression(self, tmp_path):
        f = tmp_path / "deployment.yaml"
        f.write_text(
            "apiVersion: apps/v1\n"
            "kind: Deployment\n"
            "metadata:\n"
            "  name: {{ .Release.Name }}-server\n"
        )

        assert looks_like_unrendered_helm_template(str(f)) is True

    def test_false_for_ordinary_invalid_yaml(self, tmp_path):
        f = tmp_path / "broken.yaml"
        f.write_text(
            "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: [unterminated\n"
        )

        assert looks_like_unrendered_helm_template(str(f)) is False

    def test_false_for_ordinary_valid_yaml(self, tmp_path):
        f = tmp_path / "normal.yaml"
        f.write_text("apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: x\n")

        assert looks_like_unrendered_helm_template(str(f)) is False

    def test_false_for_a_bare_unmatched_brace(self, tmp_path):
        """A single stray '{' (no closing '}}') isn't a template expression -- must not be mistaken for one."""
        f = tmp_path / "weird.yaml"
        f.write_text("apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: {\n")

        assert looks_like_unrendered_helm_template(str(f)) is False

    def test_false_for_missing_file(self, tmp_path):
        assert looks_like_unrendered_helm_template(str(tmp_path / "nope.yaml")) is False


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


def test_parser_merges_env_file_with_environment_block(tmp_path, monkeypatch):
    """'environment:' wins over 'env_file:' on a key conflict, matching docker-compose's own precedence."""
    monkeypatch.chdir(tmp_path)
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


def test_yaml_parse_error_never_echoes_the_offending_lines_content(tmp_path):
    """
    Regression for BL-031/BL-115: yaml.YAMLError's own __str__ renders a
    code-context snippet containing the actual offending line's text --
    verified directly (a secret-shaped string on a malformed line partially
    leaked into str(e)) -- so building the EnvShieldException message from
    str(e) directly would leak file content through a parse-error message,
    the same class of leak BL-001's security invariant exists to prevent.
    Only structural info (problem description, line/column numbers) may
    appear in the message.
    """
    f = tmp_path / "docker-compose.yml"
    f.write_text(
        'services:\n  api:\n    environment:\n      API_KEY: "AKIA-NOT-A-REAL-AWS-KEY_SECRET-PLACEHOLDER-VALUE\n      DB_HOST: localhost\n'
    )

    with pytest.raises(EnvShieldException) as exc_info:
        DockerComposeParser().get_vars(str(f))

    message = str(exc_info.value)
    assert "AKIA" not in message
    assert "SECRET" not in message
    assert "line" in message and "column" in message


class TestEnvFileLongForm:
    """
    Regression coverage for the Compose Spec long-form 'env_file:' entry
    ({path: ..., required: ...}), which used to crash with a TypeError from
    os.path.join(base_dir, {"path": ...}) -- the loop assumed every entry
    was a plain string.
    """

    def test_dict_entry_with_existing_file_loads_variables(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text("FOO=bar\n")
        f = tmp_path / "docker-compose.yml"
        f.write_text(
            "services:\n  api:\n    env_file:\n      - path: .env\n        required: true\n"
        )

        variables = DockerComposeParser().get_vars(str(f), get_values=True)

        assert variables == {"FOO": "bar"}

    def test_dict_entry_with_missing_optional_file_is_skipped_not_crashed(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        f = tmp_path / "docker-compose.yml"
        f.write_text(
            "services:\n  api:\n    env_file:\n      - path: .env.missing\n        required: false\n"
        )

        variables = DockerComposeParser().get_vars(str(f), get_values=True)

        assert variables == {}


class TestEnvFileProjectBoundary:
    """
    Regression coverage for BL-008: a docker-compose file is committed,
    untrusted content -- an 'env_file:' entry pointing outside the project
    (directly via '../', or indirectly via a symlink) must never be read,
    the same trust boundary schema/local_file/example_file paths in
    envshield.yml already have (see config/manager.py's
    _ensure_within_project, which this mirrors via
    parsers/_deployment.ensure_within_project).
    """

    def test_valid_sibling_env_file_still_loads(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text("FOO=bar\n")
        f = tmp_path / "docker-compose.yml"
        f.write_text("services:\n  api:\n    env_file:\n      - .env\n")

        variables = DockerComposeParser().get_vars(str(f), get_values=True)

        assert variables == {"FOO": "bar"}

    def test_valid_nested_relative_env_file_still_loads(self, tmp_path, monkeypatch):
        """Existing relative-path behavior (a subdirectory reference, not
        just a same-directory sibling) is unaffected."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "config").mkdir()
        (tmp_path / "config" / ".env").write_text("NESTED=ok\n")
        f = tmp_path / "docker-compose.yml"
        f.write_text("services:\n  api:\n    env_file:\n      - config/.env\n")

        variables = DockerComposeParser().get_vars(str(f), get_values=True)

        assert variables == {"NESTED": "ok"}

    def test_direct_dotdot_escape_is_refused(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        outside = tmp_path.parent / f"{tmp_path.name}_outside"
        outside.mkdir(exist_ok=True)
        (outside / "secrets.env").write_text("SUPER_SECRET_OUTSIDE=leaked\n")
        f = tmp_path / "docker-compose.yml"
        f.write_text(
            f"services:\n  api:\n    env_file:\n      - ../{outside.name}/secrets.env\n"
        )

        with pytest.raises(UnsafePathError):
            DockerComposeParser().get_vars(str(f), get_values=True)

    def test_symlink_escape_is_refused(self, tmp_path, monkeypatch):
        """
        A lexical containment check alone would be satisfied by a symlink
        living inside the project directory -- the security-relevant
        question is where it *resolves*, not where it lexically sits.
        """
        monkeypatch.chdir(tmp_path)
        outside = tmp_path.parent / f"{tmp_path.name}_outside"
        outside.mkdir(exist_ok=True)
        (outside / "secrets.env").write_text("SUPER_SECRET_OUTSIDE=leaked\n")
        (tmp_path / "linked.env").symlink_to(outside / "secrets.env")
        f = tmp_path / "docker-compose.yml"
        f.write_text("services:\n  api:\n    env_file:\n      - linked.env\n")

        with pytest.raises(UnsafePathError):
            DockerComposeParser().get_vars(str(f), get_values=True)

    def test_error_message_matches_the_existing_unsafe_path_convention(
        self, tmp_path, monkeypatch
    ):
        """Same exception type and message shape as every other
        envshield.yml-sourced path (schema/local_file/example_file/
        extends) -- callers that already handle UnsafePathError generically
        (e.g. explain.py's ManifestReference error status) need no new
        handling for this case."""
        monkeypatch.chdir(tmp_path)
        f = tmp_path / "docker-compose.yml"
        f.write_text(
            "services:\n  api:\n    env_file:\n      - ../../../etc/passwd\n"
        )

        with pytest.raises(UnsafePathError, match="resolves outside the project"):
            DockerComposeParser().get_vars(str(f), get_values=True)


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


class TestNoColonDefaultInterpolation:
    """
    Regression coverage for BL-011 #1: Compose's colon in '${VAR:-default}'
    is optional -- '${VAR-default}' is equally valid syntax, differing only
    in whether an empty *runtime* value also falls back to the default, a
    distinction this static parser can't observe. The old regex required
    the literal ':-' sequence, so '${VAR-default}' failed to match entirely
    and was stored as the literal, un-interpolated template text instead of
    being resolved.
    """

    def test_reference_with_no_colon_default_resolves_to_the_default(self, tmp_path):
        f = tmp_path / "docker-compose.yml"
        f.write_text(
            "services:\n  api:\n    environment:\n      DB_PORT: ${DB_PORT-3307}\n"
        )

        variables = DockerComposeParser().get_vars(str(f), get_values=True)

        assert variables["DB_PORT"] == "3307"
        assert variables["DB_PORT"] != "${DB_PORT-3307}"

    def test_list_style_environment_also_resolves_no_colon_default(self, tmp_path):
        f = tmp_path / "docker-compose.yml"
        f.write_text(
            "services:\n"
            "  api:\n"
            "    environment:\n"
            "      - FLASK_APP=${FLASK_APP-app}\n"
        )

        variables = DockerComposeParser().get_vars(str(f), get_values=True)

        assert variables["FLASK_APP"] == "app"

    def test_no_colon_empty_default_resolves_to_empty_string(self, tmp_path):
        f = tmp_path / "docker-compose.yml"
        f.write_text(
            "services:\n  api:\n    environment:\n      OPTIONAL_FLAG: ${OPTIONAL_FLAG-}\n"
        )

        variables = DockerComposeParser().get_vars(str(f), get_values=True)

        assert variables["OPTIONAL_FLAG"] == ""

    def test_colon_empty_default_resolves_to_empty_string(self, tmp_path):
        f = tmp_path / "docker-compose.yml"
        f.write_text(
            "services:\n  api:\n    environment:\n      OPTIONAL_FLAG: ${OPTIONAL_FLAG:-}\n"
        )

        variables = DockerComposeParser().get_vars(str(f), get_values=True)

        assert variables["OPTIONAL_FLAG"] == ""

    def test_default_value_itself_starting_with_a_dash_is_captured_whole(
        self, tmp_path
    ):
        """Only the first '-' after the (optional) colon is the separator."""
        f = tmp_path / "docker-compose.yml"
        f.write_text(
            "services:\n"
            "  api:\n"
            "    environment:\n"
            "      FLAGS: ${FLAGS--leading-dash-value}\n"
        )

        variables = DockerComposeParser().get_vars(str(f), get_values=True)

        assert variables["FLAGS"] == "-leading-dash-value"

    def test_quoted_no_colon_default_resolves_the_same_as_unquoted(self, tmp_path):
        f = tmp_path / "docker-compose.yml"
        f.write_text(
            'services:\n  api:\n    environment:\n      DB_PORT: "${DB_PORT-3307}"\n'
        )

        variables = DockerComposeParser().get_vars(str(f), get_values=True)

        assert variables["DB_PORT"] == "3307"

    def test_required_and_alternative_operators_remain_out_of_scope_and_literal(
        self, tmp_path
    ):
        """
        '${VAR:?err}'/'${VAR?err}' (required-or-error) and '${VAR:+alt}'/
        '${VAR+alt}' (alternative-value) are different Compose operators,
        deliberately unsupported -- the widened regex must not accidentally
        start matching them.
        """
        f = tmp_path / "docker-compose.yml"
        f.write_text(
            "services:\n"
            "  api:\n"
            "    environment:\n"
            "      A: ${A:?missing}\n"
            "      B: ${B?missing}\n"
            "      C: ${C:+alt}\n"
            "      D: ${D+alt}\n"
        )

        variables = DockerComposeParser().get_vars(str(f), get_values=True)

        assert variables["A"] == "${A:?missing}"
        assert variables["B"] == "${B?missing}"
        assert variables["C"] == "${C:+alt}"
        assert variables["D"] == "${D+alt}"


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

    def test_renamed_variable_with_no_colon_default_is_keyed_by_host_name(
        self, tmp_path
    ):
        """BL-011 #1: the no-colon default form must also resolve the rename."""
        f = tmp_path / "docker-compose.yml"
        f.write_text(
            "services:\n"
            "  server:\n"
            "    environment:\n"
            "      AUTHENTIK_POSTGRESQL__HOST: ${PG_HOST-localhost}\n"
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

    def test_environment_block_rename_still_overrides_env_file(
        self, tmp_path, monkeypatch
    ):
        """The existing 'environment: wins over env_file:' precedence still applies when the winning entry is a rename."""
        monkeypatch.chdir(tmp_path)
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
