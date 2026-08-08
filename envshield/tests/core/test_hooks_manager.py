# envshield/tests/core/test_hooks_manager.py
import subprocess

from envshield.core import hooks_manager


def test_prompt_install_hooks_skips_without_a_tty(mocker, tmp_path, monkeypatch):
    """
    No TTY to answer a confirm prompt with -- must not call questionary at
    all, just decline, so the caller (init/setup/service discover) proceeds
    to exit 0 instead of aborting on an unanswerable prompt.
    """
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "-q"], check=True)
    mocker.patch("envshield.core.hooks_manager._is_interactive", return_value=False)
    mock_confirm = mocker.patch("questionary.confirm")

    result = hooks_manager.HooksManager().prompt_install_hooks()

    assert result is False
    mock_confirm.assert_not_called()


def test_prompt_install_hooks_still_prompts_with_a_real_tty(
    mocker, tmp_path, monkeypatch
):
    """The fix must not silently disable the offer for people who actually have a terminal."""
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "-q"], check=True)
    mocker.patch("envshield.core.hooks_manager._is_interactive", return_value=True)
    mock_confirm = mocker.patch("questionary.confirm")
    mock_confirm.return_value.ask.return_value = True

    result = hooks_manager.HooksManager().prompt_install_hooks()

    assert result is True
    mock_confirm.assert_called_once()
