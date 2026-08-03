# envshield/tests/core/test_setup_manager.py
import os

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


def _write_multiservice_config(local_file="athena/config/env_config.local.py"):
    with open("envshield.yml", "w") as f:
        f.write(
            "services:\n"
            "  athena:\n"
            "    path: athena/env.schema.toml\n"
            f"    local_file: {local_file}\n"
        )


def test_setup_scopes_dotenv_target_to_service_directory(mocker, tmp_path):
    """A service with no 'local_file' override still gets its own '.env' inside its own directory."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.makedirs("services/api")
        with open("envshield.yml", "w") as f:
            f.write("services:\n  api:\n    path: services/api/env.schema.toml\n")
        with open("services/api/env.schema.toml", "w") as f:
            f.write('[API_KEY]\ndescription="Key"\nsecret=true\n')
        with open("services/api/.env.example", "w") as f:
            f.write("API_KEY=\n")

        mocker.patch("envshield.core.setup_manager.Prompt.ask", return_value="abc123")

        result = runner.invoke(app, ["setup", "--service", "api"])

        assert result.exit_code == 0
        assert not os.path.exists(".env")
        with open("services/api/.env") as f:
            assert "API_KEY=abc123" in f.read()


def test_setup_creates_python_local_file_from_schema_when_missing(mocker, tmp_path):
    """
    For a service whose local config is a Python module (e.g. zeus's
    env_config.local.py) with no file yet, setup creates one straight from
    the schema -- prompting for anything without a default, writing plain
    Python assignments for the rest.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.makedirs("athena/config")
        _write_multiservice_config()
        with open("athena/env.schema.toml", "w") as f:
            f.write(
                '[DB_HOST]\ndescription="Database host"\ndefaultValue="db"\n\n'
                '[INTREPID_KEY]\ndescription="Intrepid API key"\nsecret=true\n'
            )

        mock_prompt = mocker.patch(
            "envshield.core.setup_manager.Prompt.ask", return_value="real-key-value"
        )

        result = runner.invoke(app, ["setup", "--service", "athena"])

        assert result.exit_code == 0
        # Only the var with no schema default should have been prompted for.
        mock_prompt.assert_called_once()
        assert "INTREPID_KEY" in mock_prompt.call_args[0][0]

        with open("athena/config/env_config.local.py") as f:
            content = f.read()
        assert "DB_HOST = 'db'" in content
        assert "INTREPID_KEY = 'real-key-value'" in content


def test_setup_patches_existing_python_local_file_in_place(mocker, tmp_path):
    """
    Regression: setup must never fully rewrite a Python config module the
    way it does '.env' -- that would destroy any real logic in the file
    beyond simple assignments. Only variables that are missing or still
    blank get written; a value the developer already set (even one that
    looks like an intentional placeholder) is left alone.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.makedirs("athena/config")
        _write_multiservice_config()
        with open("athena/env.schema.toml", "w") as f:
            f.write(
                '[DB_HOST]\ndescription="Database host"\ndefaultValue="db"\n\n'
                '[INTREPID_KEY]\ndescription="Intrepid API key"\nsecret=true\n\n'
                '[NEW_FEATURE_FLAG]\ndescription="Newly added var"\ndefaultValue="no"\n'
            )
        existing_content = (
            "import os\n\n"
            'DB_HOST = "db"\n'
            'INTREPID_KEY = ""\n\n'
            'if os.environ.get("USE_LOCAL_DB") == "yes":\n'
            '    DB_HOST = "db"\n'
        )
        with open("athena/config/env_config.local.py", "w") as f:
            f.write(existing_content)

        mock_prompt = mocker.patch(
            "envshield.core.setup_manager.Prompt.ask", return_value="a-real-secret"
        )
        # No overwrite confirmation should ever be needed for a Python target.
        mock_confirm = mocker.patch("questionary.confirm")

        result = runner.invoke(app, ["setup", "--service", "athena"])

        assert result.exit_code == 0
        mock_confirm.assert_not_called()
        mock_prompt.assert_called_once()  # only INTREPID_KEY has no default and was still blank

        with open("athena/config/env_config.local.py") as f:
            content = f.read()

        assert "INTREPID_KEY = 'a-real-secret'" in content
        # The conditional logic around DB_HOST survives untouched.
        assert 'if os.environ.get("USE_LOCAL_DB") == "yes":' in content
        assert '    DB_HOST = "db"' in content
        # The newly-declared schema var got appended even though it was never prompted for.
        assert "NEW_FEATURE_FLAG = 'no'" in content


def test_setup_python_target_noop_when_everything_already_set(mocker, tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.makedirs("athena/config")
        _write_multiservice_config()
        with open("athena/env.schema.toml", "w") as f:
            f.write('[DB_HOST]\ndescription="x"\ndefaultValue="db"\n')
        with open("athena/config/env_config.local.py", "w") as f:
            f.write('DB_HOST = "already-set"\n')

        mock_prompt = mocker.patch("envshield.core.setup_manager.Prompt.ask")

        result = runner.invoke(app, ["setup", "--service", "athena"])

        assert result.exit_code == 0
        mock_prompt.assert_not_called()
        with open("athena/config/env_config.local.py") as f:
            assert f.read() == 'DB_HOST = "already-set"\n'


def test_setup_all_services_runs_each_wizard_sequentially(mocker, tmp_path):
    """
    Regression: omitting --service on a multi-service project used to
    silently set up a single root-level '.env', ignoring every configured
    service. Picking 'All services' must now walk through each service's
    own wizard in turn, in the same run.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.makedirs("athena")
        os.makedirs("hermes")
        with open("envshield.yml", "w") as f:
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

        mocker.patch(
            "questionary.select"
        ).return_value.ask.return_value = "All services"
        mock_prompt = mocker.patch(
            "envshield.core.setup_manager.Prompt.ask",
            side_effect=["athena-key-value", "hermes-db-url"],
        )

        result = runner.invoke(app, ["setup"])

        assert result.exit_code == 0
        assert mock_prompt.call_count == 2
        assert "── athena ──" in result.stdout
        assert "── hermes ──" in result.stdout
        with open("athena/.env") as f:
            assert "API_KEY=athena-key-value" in f.read()
        with open("hermes/.env") as f:
            assert "DB_URL=hermes-db-url" in f.read()
        assert not os.path.exists(".env")
