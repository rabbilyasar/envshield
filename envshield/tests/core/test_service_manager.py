# envshield/tests/core/test_service_manager.py
import os

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
    mocker.patch("envshield.core.service_manager._is_interactive", return_value=True)
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
    mocker.patch("envshield.core.service_manager._is_interactive", return_value=True)
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
    mocker.patch("envshield.core.service_manager._is_interactive", return_value=True)
    mock_select = mocker.patch("questionary.select")
    mock_select.return_value.ask.return_value = "alpha"

    service_manager.resolve_service(allow_multiple=False)

    _, kwargs = mock_select.call_args
    assert service_manager.ALL_SERVICES_CHOICE not in kwargs["choices"]


def test_resolve_service_raises_on_cancelled_prompt(mocker, tmp_path, monkeypatch):
    """
    Real gap: cancelling the picker (Ctrl+C/Esc) used to raise a bare "No
    service selected." -- no indication of what to actually do next. The
    error must name the available services and the --service flag, the
    same "tell them the next command" standard every other diagnostic in
    this codebase already holds to.
    """
    _write_two_services(monkeypatch, tmp_path)
    mocker.patch("envshield.core.service_manager._is_interactive", return_value=True)
    mocker.patch("questionary.select").return_value.ask.return_value = None

    with pytest.raises(EnvShieldException) as exc_info:
        service_manager.resolve_service(allow_multiple=True)

    message = str(exc_info.value)
    assert "--service" in message
    assert "alpha" in message and "beta" in message
    assert service_manager.ALL_SERVICES_CHOICE in message


def test_resolve_service_defaults_to_all_when_no_tty_and_multiple_allowed(
    mocker, tmp_path, monkeypatch
):
    """
    CI-safety: a command that can run against every service (check, doctor,
    setup, schema sync) must never block on a prompt nothing can answer --
    it should behave like --json already does and just run against every
    registered service instead of hanging or crashing with a raw EOFError.
    """
    _write_two_services(monkeypatch, tmp_path)
    mocker.patch("envshield.core.service_manager._is_interactive", return_value=False)
    mock_select = mocker.patch("questionary.select")

    assert service_manager.resolve_service(allow_multiple=True) == ["alpha", "beta"]
    assert service_manager.resolve_targets() == ["alpha", "beta"]
    mock_select.assert_not_called()


def test_resolve_service_raises_clearly_when_no_tty_and_single_target_required(
    mocker, tmp_path, monkeypatch
):
    """
    A command that writes one output (generate, import) can't fall back to
    "every service" -- it must fail with an actionable message instead of
    hanging on an unanswerable prompt.
    """
    _write_two_services(monkeypatch, tmp_path)
    mocker.patch("envshield.core.service_manager._is_interactive", return_value=False)
    mock_select = mocker.patch("questionary.select")

    with pytest.raises(EnvShieldException, match="--service"):
        service_manager.resolve_service(allow_multiple=False)
    mock_select.assert_not_called()


class TestResolutionProvenance:
    """
    Regression coverage for the P0 fix: a caller that wants to explain
    *why* a target was picked (currently: 'setup's directory-inference
    announcement) previously had to guess by checking whether directory
    inference *would* produce the same result as whatever was resolved --
    which a single-service project (the resolution never even calls the
    inference helper) or an interactive pick (a human chose it) can both
    match by pure coincidence. `resolve_service_with_provenance` /
    `resolve_targets_with_provenance` instead report which branch of
    resolution actually ran, so a caller never has to reverse-engineer it.
    """

    def test_explicit_service_has_explicit_provenance(self, tmp_path, monkeypatch):
        _write_two_services(monkeypatch, tmp_path)

        resolved, provenance = service_manager.resolve_service_with_provenance("beta")

        assert resolved == "beta"
        assert provenance == service_manager.PROVENANCE_EXPLICIT

    def test_the_only_configured_service_has_single_service_provenance(
        self, tmp_path, monkeypatch
    ):
        """
        The trivial case this fix must not treat as inference: a project
        with exactly one registered service never even reaches the
        directory-inference check, regardless of where it's invoked from.
        """
        monkeypatch.chdir(tmp_path)
        with open("envshield.yml", "w") as f:
            f.write("services:\n  alpha:\n    schema: alpha/env.schema.toml\n")

        resolved, provenance = service_manager.resolve_service_with_provenance()

        assert resolved == "alpha"
        assert provenance == service_manager.PROVENANCE_SINGLE_SERVICE

    def test_cwd_match_among_multiple_services_has_inferred_provenance(
        self, tmp_path, monkeypatch
    ):
        _write_two_services(monkeypatch, tmp_path)
        os.makedirs("alpha", exist_ok=True)

        resolved, provenance = service_manager.resolve_service_with_provenance(
            invocation_dir=os.path.join(str(tmp_path), "alpha")
        )

        assert resolved == "alpha"
        assert provenance == service_manager.PROVENANCE_INFERRED

    def test_interactive_pick_has_interactive_provenance_even_if_it_matches_cwd(
        self, mocker, tmp_path, monkeypatch
    ):
        """
        A human explicitly picking a service from the prompt is not
        directory inference, even if that pick happens to be the same
        service cwd would have inferred -- the two must stay distinguishable.
        """
        _write_two_services(monkeypatch, tmp_path)
        mocker.patch(
            "envshield.core.service_manager._is_interactive", return_value=True
        )
        mocker.patch("questionary.select").return_value.ask.return_value = "alpha"

        resolved, provenance = service_manager.resolve_service_with_provenance(
            invocation_dir=str(tmp_path)
        )

        assert resolved == "alpha"
        assert provenance == service_manager.PROVENANCE_INTERACTIVE

    def test_resolve_targets_with_provenance_matches_resolve_targets(
        self, tmp_path, monkeypatch
    ):
        """resolve_targets/resolve_service keep their exact prior behavior -- the provenance-aware variants are additive, not a replacement."""
        _write_two_services(monkeypatch, tmp_path)
        os.makedirs("alpha", exist_ok=True)

        targets, provenance = service_manager.resolve_targets_with_provenance(
            invocation_dir=os.path.join(str(tmp_path), "alpha")
        )

        assert targets == ["alpha"]
        assert provenance == service_manager.PROVENANCE_INFERRED
        assert service_manager.resolve_targets(
            invocation_dir=os.path.join(str(tmp_path), "alpha")
        ) == ["alpha"]
