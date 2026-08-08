# envshield/tests/core/test_service_manager.py
import pytest

from envshield.core import service_manager
from envshield.core.exceptions import EnvShieldException


def _write_two_services(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    with open("envshield.yml", "w") as f:
        f.write(
            "services:\n  alpha:\n    schema: alpha/env.schema.toml\n  beta:\n    schema: beta/env.schema.toml\n"
        )


def test_resolve_service_raises_when_nothing_is_configured_yet(tmp_path, monkeypatch):
    """
    No envshield.yml services declared at all -- an uninitialized project,
    not a valid "root" target to silently fall back to (envshield.yml
    always has at least one entry once a project's been initialized; see
    config_manager.generate_default_config_content).
    """
    monkeypatch.chdir(tmp_path)

    with pytest.raises(EnvShieldException, match="Run 'envshield init' first"):
        service_manager.resolve_service()
    with pytest.raises(EnvShieldException, match="Run 'envshield init' first"):
        service_manager.resolve_targets()


def test_resolve_service_auto_selects_the_only_configured_service(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    with open("envshield.yml", "w") as f:
        f.write("services:\n  alpha:\n    schema: alpha/env.schema.toml\n")

    assert service_manager.resolve_service() == "alpha"
    assert service_manager.resolve_targets() == ["alpha"]


def test_resolve_service_returns_explicit_service_without_prompting(
    mocker, tmp_path, monkeypatch
):
    _write_two_services(monkeypatch, tmp_path)
    mock_select = mocker.patch("questionary.select")

    assert service_manager.resolve_service("beta") == "beta"
    mock_select.assert_not_called()


def test_resolve_service_rejects_unknown_explicit_service(tmp_path, monkeypatch):
    _write_two_services(monkeypatch, tmp_path)

    with pytest.raises(EnvShieldException, match="not found"):
        service_manager.resolve_service("does-not-exist")


def test_resolve_service_prompts_when_multiple_and_none_given(
    mocker, tmp_path, monkeypatch
):
    """
    Regression: this is the behaviour the README already advertised
    ("Which service? (api / web / all)") but that was never wired into any
    command -- omitting --service on a multi-service project silently
    defaulted to a root-only project instead of asking.
    """
    _write_two_services(monkeypatch, tmp_path)
    mock_select = mocker.patch("questionary.select")
    mock_select.return_value.ask.return_value = "beta"

    result = service_manager.resolve_service()

    assert result == "beta"
    _, kwargs = mock_select.call_args
    assert "alpha" in kwargs["choices"]


def test_resolve_service_all_services_choice_returns_every_service(
    mocker, tmp_path, monkeypatch
):
    _write_two_services(monkeypatch, tmp_path)
    mocker.patch(
        "questionary.select"
    ).return_value.ask.return_value = service_manager.ALL_SERVICES_CHOICE

    result = service_manager.resolve_service(allow_multiple=True)

    assert result == ["alpha", "beta"]
    assert service_manager.resolve_targets() == ["alpha", "beta"]


def test_resolve_service_does_not_offer_all_when_disallowed(
    mocker, tmp_path, monkeypatch
):
    _write_two_services(monkeypatch, tmp_path)
    mock_select = mocker.patch("questionary.select")
    mock_select.return_value.ask.return_value = "alpha"

    service_manager.resolve_service(allow_multiple=False)

    _, kwargs = mock_select.call_args
    assert service_manager.ALL_SERVICES_CHOICE not in kwargs["choices"]


def test_resolve_service_raises_on_cancelled_prompt(mocker, tmp_path, monkeypatch):
    _write_two_services(monkeypatch, tmp_path)
    mocker.patch("questionary.select").return_value.ask.return_value = None

    with pytest.raises(EnvShieldException):
        service_manager.resolve_service()
