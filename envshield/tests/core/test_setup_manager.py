# envshield/tests/core/test_setup_manager.py
import pytest
from typer.testing import CliRunner

from envshield.cli import app
from envshield.core import setup_manager

runner = CliRunner()

def test_setup_command_happy_path(mocker, tmp_path):
    """Tests the setup command with a mix of default and empty variables."""
    with runner.isolated_filesystem(temp_dir=tmp_path) as td:
        # Setup .env.example
        example_content = "LOG_LEVEL=info\nDATABASE_URL=\nSECRET_KEY=\n"
        with open(setup_manager.EXAMPLE_FILE, "w") as f:
            f.write(example_content)

        # Mock user input
        mock_prompt = mocker.patch("envshield.core.setup_manager.Prompt.ask")
        mock_prompt.side_effect = ["postgres://user:pass@db/test", "my-super-secret"]

        result = runner.invoke(app, ["setup"])

        assert result.exit_code == 0
        assert "Successfully created" in result.stdout

        # Verify the content of the created .env file
        with open(setup_manager.OUTPUT_FILE, "r") as f:
            content = f.read()
            assert "LOG_LEVEL=info" in content
            assert "DATABASE_URL=postgres://user:pass@db/test" in content
            assert "SECRET_KEY=my-super-secret" in content

def test_setup_command_no_example_file(tmp_path):
    """Tests that the command fails gracefully if .env.example is missing."""
    with runner.isolated_filesystem(temp_dir=tmp_path) as td:
        result = runner.invoke(app, ["setup"])

        assert result.exit_code == 1
        assert "'.env.example' not found" in result.stdout

def test_setup_command_overwrite_declined(mocker, tmp_path):
    """Tests that the command exits if the user declines to overwrite an existing .env file."""
    with runner.isolated_filesystem(temp_dir=tmp_path) as td:
        # Setup existing files
        with open(setup_manager.EXAMPLE_FILE, "w") as f:
            f.write("KEY=VALUE\n")
        with open(setup_manager.OUTPUT_FILE, "w") as f:
            f.write("OLD_KEY=OLD_VALUE")

        # Mock user input to say "no"
        mocker.patch("questionary.confirm.ask", return_value=False)

        result = runner.invoke(app, ["setup"])

        assert result.exit_code == 0
        assert "Setup cancelled" in result.stdout

        # Ensure the old file was not modified
        with open(setup_manager.OUTPUT_FILE, "r") as f:
            content = f.read()
            assert content == "OLD_KEY=OLD_VALUE"