# envshield/tests/core/test_doctor.py
from typer.testing import CliRunner

from envshield.cli import app
from envshield.config.manager import CONFIG_FILE_NAME, SCHEMA_FILE_NAME
from envshield.core import doctor

runner = CliRunner()


def test_check_example_file_sync_detects_drift(tmp_path, monkeypatch):
    """
    Regression: this check previously only verified that '.env.example'
    *exists*, so it reported success even when someone added a variable to
    the schema and forgot to run 'schema sync' -- exactly the drift scenario
    doctor exists to catch.
    """
    monkeypatch.chdir(tmp_path)
    with open(SCHEMA_FILE_NAME, "w") as f:
        f.write(
            '[FOO]\ndescription="x"\nsecret=false\n\n'
            '[BAR]\ndescription="y"\nsecret=false\n'
        )
    with open(".env.example", "w") as f:
        f.write("FOO=\n")  # BAR is missing

    passed, message = doctor._check_example_file_sync()

    assert passed is False
    assert "BAR" in message


def test_check_example_file_sync_passes_when_in_sync(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with open(SCHEMA_FILE_NAME, "w") as f:
        f.write('[FOO]\ndescription="x"\nsecret=false\n')
    with open(".env.example", "w") as f:
        f.write("FOO=\n")

    passed, message = doctor._check_example_file_sync()

    assert passed is True


def test_doctor_all_ok(mocker, tmp_path):
    """Tests the doctor command when all checks pass."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        mocker.patch(
            "envshield.core.doctor._check_config_files", return_value=(True, "OK")
        )
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


def test_doctor_with_issues(mocker, tmp_path):
    """Tests the doctor command when checks fail."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        mocker.patch(
            "envshield.core.doctor._check_config_files",
            return_value=(False, "Config missing"),
        )
        mocker.patch(
            "envshield.core.doctor._check_example_file_sync",
            return_value=(False, "Example out of sync"),
        )
        # Mock other checks to pass so we only test the failures
        mocker.patch(
            "envshield.core.doctor._check_local_env_sync", return_value=(True, "OK")
        )
        mocker.patch("envshield.core.doctor._check_git_hook", return_value=(True, "OK"))

        result = runner.invoke(app, ["doctor"])

        assert result.exit_code == 1
        assert "Config missing" in result.stdout
        assert "Example out of sync" in result.stdout
        assert "Some issues were found" in result.stdout


def test_doctor_fix_flow(mocker, tmp_path):
    """Tests the interactive --fix flag for a single, isolated issue."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        # 1. Create a valid config so that only the hook check fails.
        with open(CONFIG_FILE_NAME, "w") as f:
            f.write("project_name: test\nschema: env.schema.toml")
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write("[API_KEY]\n")

        # 2. Mock the other checks to pass, isolating the git hook check.
        mocker.patch(
            "envshield.core.doctor._check_local_env_sync", return_value=(True, "OK")
        )
        mocker.patch(
            "envshield.core.doctor._check_example_file_sync", return_value=(True, "OK")
        )
        # Mock the git hook check to fail initially, then pass after the fix
        mocker.patch(
            "envshield.core.doctor._check_git_hook",
            side_effect=[(False, "Not installed"), (True, "OK")],
        )

        # Mock the fix function itself
        mock_install_hook = mocker.patch(
            "envshield.core.scanner.install_pre_commit_hook"
        )

        # Correctly mock the chained call for questionary
        mocker.patch(
            "questionary.confirm",
            return_value=mocker.Mock(ask=mocker.Mock(return_value=True)),
        )

        result = runner.invoke(app, ["doctor", "--fix"])

        assert result.exit_code == 0
        assert "Git Pre-commit Hook" in result.stdout
        assert "Not installed" in result.stdout
        assert "Fixed!" in result.stdout
        mock_install_hook.assert_called_once()
