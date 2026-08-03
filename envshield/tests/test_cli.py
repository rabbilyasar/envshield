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
            # Regression: '.env' itself (the actual secrets file) must be
            # ignored, not just the '.local' override variants.
            assert ".env" in content.splitlines()
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
        assert result.exit_code == 1
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


def test_import_command_on_python_settings_file(tmp_path):
    """
    Regression test: `envshield import settings.py` used to raise a TypeError
    inside PythonParser.get_vars(get_values=True), which cli.py swallowed and
    misreported as 'Import cancelled by user.' with exit code 0. It must now
    succeed and actually write the schema file.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        with open("settings.py", "w") as f:
            f.write("SECRET_KEY = 'django-insecure-abc123'\nDEBUG = True\n")

        result = runner.invoke(app, ["import", "settings.py"])

        assert result.exit_code == 0
        assert "cancelled" not in result.stdout.lower()
        assert os.path.exists(SCHEMA_FILE_NAME)
        with open(SCHEMA_FILE_NAME, "r") as f:
            content = f.read()
            assert "SECRET_KEY" in content
            assert "DEBUG" in content


def test_generate_command_creates_typed_config_module(tmp_path):
    """Tests that `envshield generate` writes a pydantic-settings module from the schema."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write(
                '[DATABASE_URL]\ndescription = "DB URL"\nsecret = true\n\n'
                '[LOG_LEVEL]\ndescription = "Verbosity"\nsecret = false\ndefaultValue = "info"\n'
            )

        result = runner.invoke(app, ["generate"])

        assert result.exit_code == 0
        assert os.path.exists("config.py")
        with open("config.py", "r") as f:
            content = f.read()
            assert "class Settings(BaseSettings):" in content
            assert "database_url: SecretStr" in content
            assert "log_level: str" in content
            assert "settings = Settings()" in content


def test_generate_command_refuses_to_overwrite_without_force(tmp_path):
    """Tests that `envshield generate` won't clobber an existing file without --force."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write('[API_KEY]\ndescription = "Test"\nsecret = true\n')
        with open("config.py", "w") as f:
            f.write("# hand-written, should not be clobbered\n")

        result = runner.invoke(app, ["generate"])

        assert result.exit_code == 0
        assert "already exists" in result.stdout
        with open("config.py", "r") as f:
            assert "hand-written" in f.read()


def test_generate_command_with_explicit_typescript_lang(tmp_path):
    """Tests that `--lang typescript` produces a zod config module at config.ts."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write(
                '[DATABASE_URL]\ndescription = "DB URL"\nsecret = true\n\n'
                '[LOG_LEVEL]\ndescription = "Verbosity"\nsecret = false\ndefaultValue = "info"\n'
            )

        result = runner.invoke(app, ["generate", "--lang", "typescript"])

        assert result.exit_code == 0
        assert os.path.exists("config.ts")
        assert not os.path.exists("config.py")
        with open("config.ts", "r") as f:
            content = f.read()
            assert 'import { z } from "zod";' in content
            assert '"DATABASE_URL": new Secret(_parsed["DATABASE_URL"]),' in content
            assert "export const env = {" in content


def test_generate_command_auto_detects_typescript_for_nextjs(tmp_path):
    """Tests that `generate` defaults to TypeScript when the project is detected as Next.js."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        with open("package.json", "w") as f:
            f.write('{"dependencies": {"next": "^14.0.0"}}')
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write('[API_KEY]\ndescription = "Test"\nsecret = true\n')

        result = runner.invoke(app, ["generate"])

        assert result.exit_code == 0
        assert os.path.exists("config.ts")
        assert not os.path.exists("config.py")
        assert "detected 'typescript'" in result.stdout


def test_generate_command_rejects_unsupported_lang(tmp_path):
    """Tests that an unrecognized --lang value fails clearly instead of silently defaulting."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write('[API_KEY]\ndescription = "Test"\nsecret = true\n')

        result = runner.invoke(app, ["generate", "--lang", "rust"])

        assert result.exit_code == 1
        assert "Unsupported --lang" in result.stdout
        assert not os.path.exists("config.py")
        assert not os.path.exists("config.rs")


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


def _write_two_service_project():
    os.makedirs("athena")
    os.makedirs("hermes")
    with open(CONFIG_FILE_NAME, "w") as f:
        f.write(
            "services:\n"
            "  athena:\n"
            "    path: athena/env.schema.toml\n"
            "  hermes:\n"
            "    path: hermes/env.schema.toml\n"
        )
    with open("athena/env.schema.toml", "w") as f:
        f.write('[API_KEY]\ndescription="Athena key"\nsecret=true\n')
    with open("hermes/env.schema.toml", "w") as f:
        f.write('[DB_URL]\ndescription="Hermes DB"\nsecret=true\n')


def test_schema_sync_without_service_prompts_and_runs_for_all_services(mocker, tmp_path):
    """
    Regression: omitting --service on a multi-service project used to
    silently sync a single root-level '.env.example', ignoring every
    configured service. It must now offer 'All services' and, when chosen,
    sync each one into its own directory.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _write_two_service_project()
        mocker.patch(
            "questionary.select"
        ).return_value.ask.return_value = "All services"

        result = runner.invoke(app, ["schema", "sync"])

        assert result.exit_code == 0
        assert "── athena ──" in result.stdout
        assert "── hermes ──" in result.stdout
        assert os.path.exists("athena/.env.example")
        assert os.path.exists("hermes/.env.example")
        assert not os.path.exists(".env.example")


def test_schema_sync_without_service_auto_selects_the_only_service(mocker, tmp_path):
    """With only one service configured, there's nothing to choose -- it's used automatically, no prompt."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.makedirs("athena")
        with open(CONFIG_FILE_NAME, "w") as f:
            f.write("services:\n  athena:\n    path: athena/env.schema.toml\n")
        with open("athena/env.schema.toml", "w") as f:
            f.write('[API_KEY]\ndescription="x"\nsecret=true\n')
        mock_select = mocker.patch("questionary.select")

        result = runner.invoke(app, ["schema", "sync"])

        assert result.exit_code == 0
        mock_select.assert_not_called()
        assert os.path.exists("athena/.env.example")


def test_doctor_without_service_runs_for_all_services(mocker, tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _write_two_service_project()
        with open("athena/.env", "w") as f:
            f.write("API_KEY=abc\n")
        with open("hermes/.env", "w") as f:
            f.write("DB_URL=postgres://x\n")
        mocker.patch(
            "questionary.select"
        ).return_value.ask.return_value = "All services"

        result = runner.invoke(app, ["doctor"])

        assert "── athena ──" in result.stdout
        assert "── hermes ──" in result.stdout


def test_check_without_service_runs_for_all_services(mocker, tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _write_two_service_project()
        with open("athena/.env", "w") as f:
            f.write("API_KEY=abc\n")
        with open("hermes/.env", "w") as f:
            f.write("DB_URL=postgres://x\n")
        mocker.patch(
            "questionary.select"
        ).return_value.ask.return_value = "All services"

        result = runner.invoke(app, ["check"])

        assert result.exit_code == 0
        assert "── athena ──" in result.stdout
        assert "── hermes ──" in result.stdout
        assert "perfectly in sync" in result.stdout
