# envshield/tests/core/test_setup_manager.py
import os

from typer.testing import CliRunner

from envshield.cli import app
from envshield.config import manager as config_manager
from envshield.config.manager import SCHEMA_FILE_NAME
from envshield.core import setup_manager

runner = CliRunner()


def _write_root_service_config(name="app", schema_path=SCHEMA_FILE_NAME):
    """
    Registers one service at the project root -- envshield.yml always has
    at least one entry, single-service or not (see
    config_manager.generate_default_config_content), so every 'setup'
    invocation in this file needs a real registration to resolve against.
    """
    config_manager.add_service(name, schema_path)


def test_setup_uses_schema_secret_flag_over_heuristic(mocker, tmp_path):
    """
    Regression: setup previously re-derived secrecy from its own hardcoded
    keyword list, ignoring env.schema.toml's authoritative 'secret' flag
    entirely -- so the two could disagree in a tool whose whole premise is
    "one source of truth". 'AUTH_MODE' would be flagged secret by the
    keyword heuristic (it contains "auth"), but the schema says otherwise.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _write_root_service_config()
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
        _write_root_service_config()
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
        _write_root_service_config()
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
    """Tests that the command fails gracefully if there's no schema and no template to work from."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _write_root_service_config()  # schema path registered, but the file itself is never created

        result = runner.invoke(app, ["setup"])

        assert result.exit_code == 1
        assert "'.env.example' not found" in result.stdout


def test_setup_command_overwrite_declined(mocker, tmp_path):
    """Tests that the command exits if the user declines to overwrite an existing .env file."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _write_root_service_config()
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


def _write_multiservice_config(local_file="alpha/config/env_config.local.py"):
    with open("envshield.yml", "w") as f:
        f.write(
            f"services:\n  alpha:\n    schema: alpha/env.schema.toml\n    local_file: {local_file}\n"
        )


def test_setup_scopes_dotenv_target_to_service_directory(mocker, tmp_path):
    """A service with no 'local_file' override still gets its own '.env' inside its own directory."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.makedirs("services/api")
        with open("envshield.yml", "w") as f:
            f.write("services:\n  api:\n    schema: services/api/env.schema.toml\n")
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
    For a service whose local config is a Python module (e.g. acme's
    env_config.local.py) with no file yet, setup creates one straight from
    the schema -- prompting for anything without a default, writing plain
    Python assignments for the rest.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.makedirs("alpha/config")
        _write_multiservice_config()
        with open("alpha/env.schema.toml", "w") as f:
            f.write(
                '[DB_HOST]\ndescription="Database host"\ndefaultValue="db"\n\n[INTREPID_KEY]\ndescription="Intrepid API key"\nsecret=true\n'
            )

        mock_prompt = mocker.patch(
            "envshield.core.setup_manager.Prompt.ask", return_value="real-key-value"
        )

        result = runner.invoke(app, ["setup", "--service", "alpha"])

        assert result.exit_code == 0
        # Only the var with no schema default should have been prompted for.
        mock_prompt.assert_called_once()
        assert "INTREPID_KEY" in mock_prompt.call_args[0][0]

        with open("alpha/config/env_config.local.py") as f:
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
        os.makedirs("alpha/config")
        _write_multiservice_config()
        with open("alpha/env.schema.toml", "w") as f:
            f.write(
                '[DB_HOST]\ndescription="Database host"\ndefaultValue="db"\n\n'
                '[INTREPID_KEY]\ndescription="Intrepid API key"\nsecret=true\n\n'
                '[NEW_FEATURE_FLAG]\ndescription="Newly added var"\ndefaultValue="no"\n'
            )
        existing_content = 'import os\n\nDB_HOST = "db"\nINTREPID_KEY = ""\n\nif os.environ.get("USE_LOCAL_DB") == "yes":\n    DB_HOST = "db"\n'
        with open("alpha/config/env_config.local.py", "w") as f:
            f.write(existing_content)

        mock_prompt = mocker.patch(
            "envshield.core.setup_manager.Prompt.ask", return_value="a-real-secret"
        )
        # No overwrite confirmation should ever be needed for a Python target.
        mock_confirm = mocker.patch("questionary.confirm")

        result = runner.invoke(app, ["setup", "--service", "alpha"])

        assert result.exit_code == 0
        mock_confirm.assert_not_called()
        mock_prompt.assert_called_once()  # only INTREPID_KEY has no default and was still blank

        with open("alpha/config/env_config.local.py") as f:
            content = f.read()

        assert "INTREPID_KEY = 'a-real-secret'" in content
        # The conditional logic around DB_HOST survives untouched.
        assert 'if os.environ.get("USE_LOCAL_DB") == "yes":' in content
        assert '    DB_HOST = "db"' in content
        # The newly-declared schema var got appended even though it was never prompted for.
        assert "NEW_FEATURE_FLAG = 'no'" in content


def test_setup_python_target_noop_when_everything_already_set(mocker, tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.makedirs("alpha/config")
        _write_multiservice_config()
        with open("alpha/env.schema.toml", "w") as f:
            f.write('[DB_HOST]\ndescription="x"\ndefaultValue="db"\n')
        with open("alpha/config/env_config.local.py", "w") as f:
            f.write('DB_HOST = "already-set"\n')

        mock_prompt = mocker.patch("envshield.core.setup_manager.Prompt.ask")

        result = runner.invoke(app, ["setup", "--service", "alpha"])

        assert result.exit_code == 0
        mock_prompt.assert_not_called()
        with open("alpha/config/env_config.local.py") as f:
            assert f.read() == 'DB_HOST = "already-set"\n'


def test_setup_all_services_runs_each_wizard_sequentially(mocker, tmp_path):
    """
    Regression: omitting --service on a multi-service project used to
    silently set up a single root-level '.env', ignoring every configured
    service. Picking 'All services' must now walk through each service's
    own wizard in turn, in the same run.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.makedirs("alpha")
        os.makedirs("beta")
        with open("envshield.yml", "w") as f:
            f.write(
                "services:\n  alpha:\n    schema: alpha/env.schema.toml\n  beta:\n    schema: beta/env.schema.toml\n"
            )
        with open("alpha/env.schema.toml", "w") as f:
            f.write('[API_KEY]\ndescription="Alpha key"\nsecret=true\n')
        with open("beta/env.schema.toml", "w") as f:
            f.write('[DB_URL]\ndescription="Beta DB"\nsecret=true\n')

        mocker.patch(
            "envshield.core.service_manager._is_interactive", return_value=True
        )
        mock_select = mocker.patch("questionary.select")
        mock_select.return_value.ask.return_value = "All services"
        mock_prompt = mocker.patch(
            "envshield.core.setup_manager.Prompt.ask",
            side_effect=["alpha-key-value", "beta-db-url"],
        )

        result = runner.invoke(app, ["setup"])

        mock_select.assert_called_once()
        assert result.exit_code == 0
        assert mock_prompt.call_count == 2
        assert "── alpha ──" in result.stdout
        assert "── beta ──" in result.stdout
        with open("alpha/.env") as f:
            assert "API_KEY=alpha-key-value" in f.read()
        with open("beta/.env") as f:
            assert "DB_URL=beta-db-url" in f.read()
        assert not os.path.exists(".env")


def test_setup_re_prompts_for_an_existing_but_invalid_value(mocker, tmp_path):
    """
    Regression target for this change: setup previously only checked
    *presence* ('is there any non-blank value already?'), so a value that
    got hand-edited into something the schema no longer allows (e.g. an
    enum typo) was silently accepted as "already configured" and never
    re-surfaced.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _write_root_service_config()
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write(
                '[LOG_LEVEL]\ndescription="x"\nenum=["debug","info","warn","error"]\n'
            )
        with open(".env", "w") as f:
            f.write("LOG_LEVEL=verbose\n")

        mocker.patch("questionary.confirm").return_value.ask.return_value = True
        mocker.patch("questionary.select").return_value.ask.return_value = "warn"

        result = runner.invoke(app, ["setup"])

        assert result.exit_code == 0
        with open(".env") as f:
            assert "LOG_LEVEL=warn" in f.read()


def test_setup_does_not_re_prompt_for_an_existing_valid_value(mocker, tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _write_root_service_config()
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write(
                '[LOG_LEVEL]\ndescription="x"\nenum=["debug","info","warn","error"]\n'
            )
        with open(".env", "w") as f:
            f.write("LOG_LEVEL=info\n")
        mocker.patch("questionary.confirm").return_value.ask.return_value = True
        mock_select = mocker.patch("questionary.select")

        result = runner.invoke(app, ["setup"])

        assert result.exit_code == 0
        mock_select.assert_not_called()
        with open(".env") as f:
            assert "LOG_LEVEL=info" in f.read()


def test_setup_uses_a_picker_for_enum_fields(mocker, tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _write_root_service_config()
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write(
                '[LOG_LEVEL]\ndescription="x"\nenum=["debug","info","warn","error"]\n'
            )

        mock_select = mocker.patch("questionary.select")
        mock_select.return_value.ask.return_value = "debug"

        result = runner.invoke(app, ["setup"])

        assert result.exit_code == 0
        mock_select.assert_called_once()
        _, kwargs = mock_select.call_args
        assert kwargs["choices"] == ["debug", "info", "warn", "error"]
        with open(".env") as f:
            assert "LOG_LEVEL=debug" in f.read()
