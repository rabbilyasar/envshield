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
