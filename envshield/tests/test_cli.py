# envshield/tests/test_cli.py
import os
from typer.testing import CliRunner

from envshield.cli import app
from envshield.config.manager import CONFIG_FILE_NAME, SCHEMA_FILE_NAME

runner = CliRunner()


def test_init_command_in_git_repo(tmp_path):
    """Tests the init command in a clean, git-initialized directory."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.system("git init")
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 0
        assert "Setup Complete!" in result.stdout
        assert os.path.exists(CONFIG_FILE_NAME)
        assert os.path.exists(SCHEMA_FILE_NAME)
        assert os.path.exists(".env.example")
        with open(".gitignore", "r") as f:
            content = f.read()
            assert ".env.local" in content
            assert ".envshield/" in content
        assert os.path.exists(".git/hooks/pre-commit")


def test_init_command_in_non_git_repo(tmp_path):
    """Tests that init succeeds but warns if not in a git repo."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 0
        assert "Setup Complete!" in result.stdout
        assert "Warning: Could not install Git hook" in result.stdout
        assert not os.path.exists(".git/hooks/pre-commit")


def test_check_command_happy_path(tmp_path):
    """Tests the check command when the .env file is in sync."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write('[API_KEY]\ndescription="Test"\n')
        with open(".env", "w") as f:
            f.write("API_KEY=12345")
        result = runner.invoke(app, ["check"])
        assert result.exit_code == 0
        assert "perfectly in sync" in result.stdout


def test_check_command_with_missing_variable(tmp_path):
    """Tests the check command when the .env file has a missing variable."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write('[API_KEY]\ndescription="Test"\n[SECRET]\ndescription="Secret"')
        with open(".env", "w") as f:
            f.write("API_KEY=12345")
        result = runner.invoke(app, ["check"])
        assert result.exit_code == 0
        assert "Missing in Local" in result.stdout
        assert "SECRET" in result.stdout


def test_schema_sync_command(tmp_path):
    """Tests that schema sync correctly generates a .env.example file."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        schema_content = '[API_KEY]\ndescription="My test key"\ndefaultValue="abc"'
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write(schema_content)
        result = runner.invoke(app, ["schema", "sync"])
        assert result.exit_code == 0
        assert os.path.exists(".env.example")
        with open(".env.example", "r") as f:
            content = f.read()
            assert "# My test key" in content
            assert "API_KEY=abc" in content


def test_init_force_flag_with_confirmation(mocker, tmp_path):
    """Tests that 'init --force' prompts for confirmation and overwrites existing files."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        with open(CONFIG_FILE_NAME, "w") as f:
            f.write("project_name: old_project")

        # Correctly mock the chained call
        mocker.patch(
            "questionary.confirm",
            return_value=mocker.Mock(ask=mocker.Mock(return_value=True)),
        )

        result = runner.invoke(app, ["init", "--force"])
        assert result.exit_code == 0
        assert "Setup Complete!" in result.stdout
        with open(CONFIG_FILE_NAME, "r") as f:
            content = f.read()
            # The default name is the directory name, which is a random temp dir name
            assert "project_name: old_project" not in content
