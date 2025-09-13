# envshield/tests/core/test_doctor.py
import pytest
from typer.testing import CliRunner

from envshield.cli import app

runner = CliRunner()


def test_doctor_all_ok(mocker):
    """Tests the doctor command when all checks pass."""
    mocker.patch("envshield.core.doctor._check_config_files", return_value=(True, "OK"))
    mocker.patch(
        "envshield.core.doctor._check_local_env_sync", return_value=(True, "OK")
    )
    mocker.patch(
        "envshield.core.doctor._check_example_file_sync", return_value=(True, "OK")
    )
    mocker.patch("envshield.core.doctor._check_git_hook", return_value=(True, "OK"))

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert "Everything looks great!" in result.stdout


def test_doctor_with_issues(mocker):
    """Tests the doctor command when checks fail."""
    mocker.patch(
        "envshield.core.doctor._check_config_files",
        return_value=(False, "Config missing"),
    )
    mocker.patch(
        "envshield.core.doctor._check_local_env_sync", return_value=(True, "OK")
    )
    mocker.patch(
        "envshield.core.doctor._check_example_file_sync",
        return_value=(False, "Example out of sync"),
    )
    mocker.patch("envshield.core.doctor._check_git_hook", return_value=(True, "OK"))

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0  # Doctor reports, it doesn't fail
    assert "Config missing" in result.stdout
    assert "Example out of sync" in result.stdout
    assert "Some issues were found" in result.stdout


def test_doctor_fix_flow(mocker):
    """Tests the interactive --fix flag."""
    # Mock checks to fail initially
    mocker.patch(
        "envshield.core.doctor._check_git_hook",
        return_value=(False, "Hook not installed"),
    )

    # Mock the fix function
    mock_install_hook = mocker.patch("envshield.core.scanner.install_pre_commit_hook")

    # Mock user input to say "yes" to the fix
    mocker.patch("questionary.confirm.ask", return_value=True)

    # Re-check should pass after fix
    mocker.patch(
        "envshield.core.doctor._check_git_hook",
        side_effect=[(False, "Not installed"), (True, "OK")],
    )

    result = runner.invoke(app, ["doctor", "--fix"])

    assert result.exit_code == 0
    assert "Hook not installed" in result.stdout
    assert "Fixed!" in result.stdout
    mock_install_hook.assert_called_once()
