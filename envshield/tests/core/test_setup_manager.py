# envshield/tests/core/test_setup_manager.py
from typer.testing import CliRunner

from envshield.cli import app
from envshield.config.manager import SCHEMA_FILE_NAME
from envshield.core import setup_manager

runner = CliRunner()


def test_setup_uses_schema_secret_flag_over_heuristic(mocker, tmp_path):
    """
    Regression: setup previously re-derived secrecy from its own hardcoded
    keyword list, ignoring env.schema.toml's authoritative 'secret' flag
    entirely -- so the two could disagree in a tool whose whole premise is
    "one source of truth". 'AUTH_MODE' would be flagged secret by the
    keyword heuristic (it contains "auth"), but the schema says otherwise.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write('[AUTH_MODE]\ndescription="Login mode."\nsecret=false\n')
        with open(setup_manager.EXAMPLE_FILE, "w") as f:
            f.write("AUTH_MODE=\n")

        mock_prompt = mocker.patch("envshield.core.setup_manager.Prompt.ask")
        mock_prompt.return_value = "password_based"

        result = runner.invoke(app, ["setup"])

        assert result.exit_code == 0
        mock_prompt.assert_called_once()
        _, kwargs = mock_prompt.call_args
        assert kwargs["password"] is False


def test_setup_displays_schema_description_when_prompting(mocker, tmp_path):
    """The schema's description -- its whole documentation value-add -- must
    actually reach the person being onboarded, not just live in the TOML."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write(
                '[API_KEY]\ndescription="Third-party API key for widgets."\nsecret=true\n'
            )
        with open(setup_manager.EXAMPLE_FILE, "w") as f:
            f.write("API_KEY=\n")

        mock_prompt = mocker.patch("envshield.core.setup_manager.Prompt.ask")
        mock_prompt.return_value = "abc123"

        result = runner.invoke(app, ["setup"])

        assert "Third-party API key for widgets." in result.stdout


def test_setup_command_happy_path(mocker, tmp_path):
    """Tests the setup command with a mix of default and empty variables."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        example_content = "LOG_LEVEL=info\nDATABASE_URL=\nSECRET_KEY=\n"
        with open(setup_manager.EXAMPLE_FILE, "w") as f:
            f.write(example_content)

        mock_prompt = mocker.patch("envshield.core.setup_manager.Prompt.ask")
        mock_prompt.side_effect = [
            "postgres://user:pass@db/test?sslmode=require",
            "my-super-secret",
        ]

        # Prepare a fake datetime instance
        fake_now = mocker.Mock()
        fake_now.strftime.return_value = "2025-01-01"

        # Patch the entire datetime class in setup_manager
        mock_datetime = mocker.patch("envshield.core.setup_manager.datetime.datetime")
        mock_datetime.now.return_value = fake_now

        result = runner.invoke(app, ["setup"])

        assert result.exit_code == 0
        assert "Successfully created" in result.stdout

        with open(".env", "r") as f:
            content = f.read()
            assert "LOG_LEVEL=info" in content
            assert (
                'DATABASE_URL="postgres://user:pass@db/test?sslmode=require"' in content
            )
            assert "SECRET_KEY=my-super-secret" in content


def test_setup_command_no_example_file(tmp_path):
    """Tests that the command fails gracefully if .env.example is missing."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        result = runner.invoke(app, ["setup"])

        assert result.exit_code == 1
        assert "'.env.example' not found" in result.stdout


def test_setup_command_overwrite_declined(mocker, tmp_path):
    """Tests that the command exits if the user declines to overwrite an existing .env file."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        with open(setup_manager.EXAMPLE_FILE, "w") as f:
            f.write("KEY=VALUE\n")
        with open(".env", "w") as f:
            f.write("OLD_KEY=OLD_VALUE")

        mocker.patch(
            "questionary.confirm",
            return_value=mocker.Mock(ask=mocker.Mock(return_value=False)),
        )

        result = runner.invoke(app, ["setup"])

        assert result.exit_code == 0
        assert "Setup cancelled" in result.stdout

        with open(".env", "r") as f:
            content = f.read()
            assert content == "OLD_KEY=OLD_VALUE"
