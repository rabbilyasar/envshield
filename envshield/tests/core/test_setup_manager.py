# envshield/tests/core/test_setup_manager.py
import os
import stat

import pytest
from typer.testing import CliRunner

from envshield.cli import app
from envshield.config import manager as config_manager
from envshield.config.manager import SCHEMA_FILE_NAME
from envshield.core import setup_manager
from envshield.core.exceptions import EnvShieldException

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
    """
    Regression: declining the overwrite prompt used to leave '.env'
    untouched (correct) but the CLI wrapper still unconditionally printed
    '✓ Configuration complete!' right after -- a false success report for
    a setup that did nothing. Also covers 'default' (pressing Enter, which
    questionary.confirm's default=False resolves the same way as an
    explicit decline).
    """
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
        assert "Configuration complete" not in result.stdout

        with open(".env", "r") as f:
            content = f.read()
            assert content == "OLD_KEY=OLD_VALUE"


def test_setup_manager_reports_cancellation_via_return_value(mocker, tmp_path):
    """
    run_setup's return value is the CLI's only signal that nothing was
    written -- a caller that ignores it (as the CLI used to) can't tell a
    cancelled setup apart from a completed one. `SetupResult.__bool__`
    mirrors `completed`, so a plain truthiness check still works.
    """
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

        result = setup_manager.run_setup(service_name="app")

        assert result.completed is False
        assert bool(result) is False


def test_setup_command_overwrite_accepted_still_reports_completion(mocker, tmp_path):
    """
    The accept path must keep reporting success -- this fix must not make
    every setup run silently withhold 'Configuration complete!'.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _write_root_service_config()
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write('[KEY]\ndescription="x"\n')
        with open(".env", "w") as f:
            f.write("OLD_KEY=OLD_VALUE")

        mocker.patch(
            "questionary.confirm",
            return_value=mocker.Mock(ask=mocker.Mock(return_value=True)),
        )
        mocker.patch(
            "envshield.core.setup_manager.Prompt.ask", return_value="new-value"
        )

        result = runner.invoke(app, ["setup"])

        assert result.exit_code == 0
        assert "Configuration complete" in result.stdout
        with open(".env", "r") as f:
            content = f.read()
            assert "KEY=new-value" in content
            assert "OLD_KEY=OLD_VALUE" in content


def test_setup_command_no_existing_env_still_reports_completion(mocker, tmp_path):
    """No pre-existing local file means no overwrite prompt at all -- setup
    should complete and report success exactly as before this fix."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _write_root_service_config()
        with open(setup_manager.EXAMPLE_FILE, "w") as f:
            f.write("KEY=\n")

        mock_confirm = mocker.patch("questionary.confirm")
        mocker.patch(
            "envshield.core.setup_manager.Prompt.ask", return_value="new-value"
        )

        result = runner.invoke(app, ["setup"])

        assert result.exit_code == 0
        mock_confirm.assert_not_called()
        assert "Configuration complete" in result.stdout
        with open(".env", "r") as f:
            assert "KEY=new-value" in f.read()


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


def test_setup_rejects_a_blank_answer_for_a_required_variable(mocker, tmp_path):
    """
    Real bug: pressing Enter with no input for a required variable (no
    default) was silently accepted as a valid answer -- 'setup' would
    report success and write a blank value, even though 'check'/'doctor'
    immediately flag a blank required variable as broken. The wizard whose
    whole job is to prevent that must not be the one creating it.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _write_root_service_config()
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write('[WEB_KEY]\ndescription="x"\n')
        with open(setup_manager.EXAMPLE_FILE, "w") as f:
            f.write("WEB_KEY=\n")

        mock_prompt = mocker.patch("envshield.core.setup_manager.Prompt.ask")
        mock_prompt.side_effect = ["", "real_value"]

        result = runner.invoke(app, ["setup"])

        assert result.exit_code == 0
        assert "required and cannot be left blank" in result.stdout
        with open(".env") as f:
            assert "WEB_KEY=real_value" in f.read()


def test_setup_still_accepts_blank_to_clear_an_invalid_but_optional_value(
    mocker, tmp_path
):
    """
    The only way a genuinely optional field (unmet 'requiredIf', no
    default) reaches the retry loop at all is an existing value that's
    invalid against the schema (e.g. a stale pattern mismatch) -- in that
    case, blank is a legitimate way to clear it, and must not be forced
    into a "required" error just because it needed re-prompting.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _write_root_service_config()
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write(
                '[X_ENABLED]\ndescription="x"\ndefaultValue="false"\n\n'
                '[OPTIONAL_CODE]\ndescription="x"\npattern="^[0-9]{4}$"\n'
                'requiredIf={var="X_ENABLED", equals="true"}\n'
            )
        with open(".env", "w") as f:
            f.write("X_ENABLED=false\nOPTIONAL_CODE=not-digits\n")

        mocker.patch("questionary.confirm").return_value.ask.return_value = True
        mock_prompt = mocker.patch("envshield.core.setup_manager.Prompt.ask")
        mock_prompt.return_value = ""

        result = runner.invoke(app, ["setup"])

        assert result.exit_code == 0, result.stdout
        assert "required and cannot be left blank" not in result.stdout
        with open(".env") as f:
            content = f.read()
            assert "OPTIONAL_CODE=" in content


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


def test_setup_retry_loop_never_prints_the_rejected_value(mocker, tmp_path):
    """
    Regression coverage for P0-3, and the one call site with no prior
    coverage at all: the free-text retry loop (setup_manager.py's
    Prompt.ask -> validate_value -> console.print(error) sequence) must
    never print the value it just rejected -- right after masking the same
    keystrokes on input (password=True for a secret field), printing the
    rejection error in plaintext would completely defeat that masking.
    Uses a distinctive sentinel so a leak is unambiguous.
    """
    sentinel = "SUPER_SECRET_TEST_VALUE_12345"
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _write_root_service_config()
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write('[API_PORT]\ndescription="x"\ntype="port"\nsecret=true\n')

        mock_prompt = mocker.patch("envshield.core.setup_manager.Prompt.ask")
        # First answer is rejected (not a valid port) -- the error for
        # *that* answer must be printed without the answer itself. Second
        # answer is valid, ending the retry loop.
        mock_prompt.side_effect = [sentinel, "8080"]

        result = runner.invoke(app, ["setup"])

        assert result.exit_code == 0
        assert sentinel not in result.stdout
        assert "must be a port number from 1-65535" in result.stdout
        with open(".env") as f:
            assert "API_PORT=8080" in f.read()


class TestLocalFileGenerationInjectionIsPrevented:
    """
    Regression coverage for P0-5: a schema key is repository-controlled
    (env.schema.toml is committed/PR-editable) and is about to become the
    left-hand side of a generated 'KEY=value' or 'KEY = value' assignment.
    Unlike a value, a key can't be escaped into a safe form without
    changing its identity, so an unsafe one must be rejected outright --
    and, critically, rejected before anything is written, so a bad key
    doesn't leave behind a truncated, half-written file.
    """

    def test_dotenv_writer_rejects_an_unsafe_key_and_writes_nothing(self, tmp_path):
        target = tmp_path / ".env"

        with pytest.raises(EnvShieldException):
            setup_manager._write_dotenv_local_file(
                str(target),
                {"GOOD_KEY": "1", "BAD\nENVSHIELD_P0_5_SENTINEL=injected": "2"},
            )

        assert not target.exists()

    def test_python_writer_rejects_an_unsafe_key_and_writes_nothing(self, tmp_path):
        target = tmp_path / "config.py"

        with pytest.raises(EnvShieldException):
            setup_manager._write_python_local_file(
                str(target),
                {"GOOD_KEY": "1", "BAD\nimport os": "2"},
                prompted_keys=[],
            )

        assert not target.exists()


class TestNewLocalFilesGetRestrictivePermissions:
    """
    Regression coverage for P1-1: a freshly-created local secrets file
    used to inherit whatever the process umask produced instead of being
    explicitly restricted -- landing world-readable (0644) on a
    permissive-umask or shared multi-user host. Both writers must
    guarantee 0600 for a genuinely new file, regardless of umask -- and,
    per the follow-up fix below, must also tighten a pre-existing file's
    permissions whenever they write into it, since a hand-created or
    otherwise loosely-permissioned '.env'/config module is exactly the
    common case in an existing-project onboarding.
    """

    def test_dotenv_writer_creates_a_fresh_file_as_0600(self, tmp_path):
        target = tmp_path / ".env"

        setup_manager._write_dotenv_local_file(str(target), {"API_KEY": "abc123"})

        assert stat.S_IMODE(os.stat(target).st_mode) == 0o600

    def test_python_writer_creates_a_fresh_file_as_0600(self, tmp_path):
        target = tmp_path / "config.py"

        setup_manager._write_python_local_file(
            str(target), {"API_KEY": "abc123"}, prompted_keys=["API_KEY"]
        )

        assert stat.S_IMODE(os.stat(target).st_mode) == 0o600

    def test_dotenv_writer_tightens_permissions_of_a_pre_existing_file(self, tmp_path):
        target = tmp_path / ".env"
        target.write_text("OLD=value\n")
        os.chmod(target, 0o644)

        setup_manager._write_dotenv_local_file(str(target), {"API_KEY": "abc123"})

        # open_new_secret_file's 0600 guarantee only applies to a file it
        # actually creates (POSIX open() semantics), which would otherwise
        # leave a pre-existing, loosely-permissioned '.env' (e.g. hand-
        # created before EnvShield was ever introduced to a project)
        # untouched even though this function fully regenerates its
        # content -- _write_dotenv_local_file takes explicit responsibility
        # for the permission outcome itself instead of relying on that.
        assert stat.S_IMODE(os.stat(target).st_mode) == 0o600

    def test_python_writer_tightens_permissions_when_patching_an_existing_file(
        self, tmp_path
    ):
        """
        Covers the *other* real writer path: _write_python_local_file's
        patch-an-existing-file branch (via file_updater.update_variables_in_file)
        is a separate code path from the fresh-file branch above and was not
        covered by open_new_secret_file's guarantee at all, since it never
        calls it.
        """
        target = tmp_path / "config.py"
        target.write_text("API_KEY = ''\n")
        os.chmod(target, 0o644)

        setup_manager._write_python_local_file(
            str(target), {"API_KEY": "abc123"}, prompted_keys=["API_KEY"]
        )

        assert stat.S_IMODE(os.stat(target).st_mode) == 0o600


def test_setup_shows_a_terse_target_line_not_a_decorative_banner(tmp_path):
    """
    P0-3: the old opening Panel ('Welcome to EnvShield Setup' / '✨ Local
    Setup ✨') is decorative and generic -- exactly the tone this round of
    work asked 'setup' to stop using. It's replaced with a plain line
    naming the file actually being configured.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _write_root_service_config()
        with open(setup_manager.EXAMPLE_FILE, "w") as f:
            f.write("KEY=value\n")

        result = runner.invoke(app, ["setup"])

        assert result.exit_code == 0
        assert "Welcome" not in result.stdout
        assert "✨" not in result.stdout
        assert ".env" in result.stdout


def test_setup_shows_upfront_drift_classification_before_prompting(mocker, tmp_path):
    """
    'setup' now shows the gap against the schema before asking anything --
    reusing schema_manager's own missing/blank/invalid classification
    (the same one 'check'/'doctor' render) instead of a second validation
    engine. A variable missing only because of a currently-active
    'requiredIf' is labeled distinctly from one that's unconditionally
    required, since the schema itself proves that distinction -- it's
    never phrased as "newly" required, which nothing here could prove.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _write_root_service_config()
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write(
                '[API_URL]\ndescription="x"\ntype="url"\n\n'
                '[LOG_LEVEL]\ndescription="x"\nenum=["debug","info"]\n\n'
                '[PAYMENTS_ENABLED]\ndescription="x"\ndefaultValue="true"\n\n'
                '[STRIPE_KEY]\ndescription="x"\nrequiredIf={var="PAYMENTS_ENABLED", equals="true"}\n'
            )
        with open(".env", "w") as f:
            f.write("API_URL=not-a-url\nPAYMENTS_ENABLED=true\n")

        mocker.patch("questionary.confirm").return_value.ask.return_value = True
        mocker.patch("questionary.select").return_value.ask.return_value = "debug"
        mocker.patch(
            "envshield.core.setup_manager.Prompt.ask",
            side_effect=["https://api.example.com", "sk_test"],
        )

        result = runner.invoke(app, ["setup"])

        assert result.exit_code == 0, result.stdout
        assert "missing: LOG_LEVEL" in result.stdout
        assert "missing (conditionally required): STRIPE_KEY" in result.stdout
        assert "invalid: API_URL (must be a valid URL)" in result.stdout


class TestDriftClassificationReflectsDefaultResolution:
    """
    Regression coverage for the second confirmed P0 defect: the upfront
    classification previously ran against raw local values, before
    'setup's own default-resolution step -- so a defaulted field missing
    or blank from the local file was labeled "missing"/"blank" (implying
    it needs input) right before being silently filled with zero prompt,
    contradicting the very next section of output. The classification
    must now be computed from the same resolved values Step 1 actually
    uses to decide what to prompt for (schema_types.is_required_now /
    should_be_present, fed by setup_manager._fill_schema_defaults),
    without introducing a second validation engine -- it's still
    schema_manager.diff_against_schema, just fed the resolved values.
    """

    def test_a_missing_defaulted_field_is_not_reported_as_missing(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _write_root_service_config()
            with open(SCHEMA_FILE_NAME, "w") as f:
                f.write('[LOG_LEVEL]\ndescription="x"\ndefaultValue="info"\n')
            with open(setup_manager.EXAMPLE_FILE, "w") as f:
                f.write("")  # LOG_LEVEL entirely absent from the template

            result = runner.invoke(app, ["setup"])

            assert result.exit_code == 0, result.stdout
            assert "missing" not in result.stdout
            assert "LOG_LEVEL=info" in open(".env").read()

    def test_a_blank_defaulted_field_is_not_reported_as_blank(self, mocker, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _write_root_service_config()
            with open(SCHEMA_FILE_NAME, "w") as f:
                f.write('[LOG_LEVEL]\ndescription="x"\ndefaultValue="info"\n')
            with open(".env", "w") as f:
                f.write("LOG_LEVEL=\n")

            mocker.patch("questionary.confirm").return_value.ask.return_value = True

            result = runner.invoke(app, ["setup"])

            assert result.exit_code == 0, result.stdout
            assert "blank" not in result.stdout
            assert "LOG_LEVEL=info" in open(".env").read()

    def test_a_missing_non_defaulted_required_field_is_still_reported_as_missing(
        self, mocker, tmp_path
    ):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _write_root_service_config()
            with open(SCHEMA_FILE_NAME, "w") as f:
                f.write('[API_KEY]\ndescription="x"\n')
            with open(setup_manager.EXAMPLE_FILE, "w") as f:
                f.write("")

            mocker.patch(
                "envshield.core.setup_manager.Prompt.ask", return_value="a-key"
            )

            result = runner.invoke(app, ["setup"])

            assert result.exit_code == 0, result.stdout
            assert "missing: API_KEY" in result.stdout

    def test_a_conditional_field_with_a_default_is_treated_as_defaulted_not_conditional(
        self, tmp_path
    ):
        """
        schema_types.is_required_now already gives 'defaultValue' precedence
        over 'requiredIf' -- a field with both is just a defaulted field,
        never actually "conditionally required". The classification must
        agree: it's silently resolved, not labeled either way.
        """
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _write_root_service_config()
            with open(SCHEMA_FILE_NAME, "w") as f:
                f.write(
                    '[FLAG]\ndescription="x"\ndefaultValue="false"\n\n'
                    '[DEPENDENT]\ndescription="x"\ndefaultValue="fallback"\n'
                    'requiredIf={var="FLAG", equals="true"}\n'
                )
            with open(setup_manager.EXAMPLE_FILE, "w") as f:
                f.write("")

            result = runner.invoke(app, ["setup"])

            assert result.exit_code == 0, result.stdout
            assert "missing" not in result.stdout
            content = open(".env").read()
            assert "FLAG=false" in content
            assert "DEPENDENT=fallback" in content

    def test_a_conditional_field_without_a_default_is_reported_as_conditionally_required(
        self, mocker, tmp_path
    ):
        """
        The trigger (FLAG) has its own default -- resolved before the
        condition is evaluated, so DEPENDENT correctly shows up as
        conditionally required even though FLAG never appears in the
        local file at all. Confirms requiredIf is now evaluated against
        post-default-resolution values, not raw local values.
        """
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _write_root_service_config()
            with open(SCHEMA_FILE_NAME, "w") as f:
                f.write(
                    '[FLAG]\ndescription="x"\ndefaultValue="true"\n\n'
                    '[DEPENDENT]\ndescription="x"\n'
                    'requiredIf={var="FLAG", equals="true"}\n'
                )
            with open(setup_manager.EXAMPLE_FILE, "w") as f:
                f.write("")

            mocker.patch(
                "envshield.core.setup_manager.Prompt.ask", return_value="dep-value"
            )

            result = runner.invoke(app, ["setup"])

            assert result.exit_code == 0, result.stdout
            assert "missing (conditionally required): DEPENDENT" in result.stdout
            assert "DEPENDENT=dep-value" in open(".env").read()

    def test_a_fully_defaulted_setup_shows_no_drift_and_needs_no_prompt(
        self, mocker, tmp_path
    ):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _write_root_service_config()
            with open(SCHEMA_FILE_NAME, "w") as f:
                f.write(
                    '[LOG_LEVEL]\ndescription="x"\ndefaultValue="info"\n\n'
                    '[PORT]\ndescription="x"\ndefaultValue="8080"\n'
                )
            with open(setup_manager.EXAMPLE_FILE, "w") as f:
                f.write("")

            mock_prompt = mocker.patch("envshield.core.setup_manager.Prompt.ask")

            result = runner.invoke(app, ["setup"])

            assert result.exit_code == 0, result.stdout
            assert "missing:" not in result.stdout
            assert "blank:" not in result.stdout
            assert "invalid:" not in result.stdout
            mock_prompt.assert_not_called()
            content = open(".env").read()
            assert "LOG_LEVEL=info" in content
            assert "PORT=8080" in content


def test_setup_explains_a_satisfied_requiredif_condition_for_a_non_secret_trigger(
    mocker, tmp_path
):
    """
    P0-4: a field that's only required right now because of a satisfied
    'requiredIf' condition says why, right where it's prompted for -- not
    just that it's required.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _write_root_service_config()
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write(
                '[PAYMENTS_ENABLED]\ndescription="x"\ndefaultValue="true"\n\n'
                '[STRIPE_KEY]\ndescription="x"\nrequiredIf={var="PAYMENTS_ENABLED", equals="true"}\n'
            )
        with open(setup_manager.EXAMPLE_FILE, "w") as f:
            f.write("PAYMENTS_ENABLED=true\nSTRIPE_KEY=\n")

        mocker.patch("envshield.core.setup_manager.Prompt.ask", return_value="sk_test")

        result = runner.invoke(app, ["setup"])

        assert result.exit_code == 0
        assert 'Required because PAYMENTS_ENABLED = "true".' in result.stdout


def test_setup_requiredif_explanation_never_leaks_a_secret_triggers_value(
    mocker, tmp_path
):
    """
    Security requirement for P0-4: if the variable that *triggers* a
    requiredIf condition is itself declared secret, 'setup' must never
    print its value -- or the schema's 'equals' comparison literal, which
    could itself coincide with the real secret -- while explaining why a
    dependent field is required. Uses a distinctive sentinel so a leak is
    unambiguous, the same way test_setup_retry_loop_never_prints_the_rejected_value
    does for the retry loop.
    """
    sentinel = "sk_live_super_secret_998877"
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _write_root_service_config()
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write(
                f'[STRIPE_TOKEN]\ndescription="x"\nsecret=true\ndefaultValue="{sentinel}"\n\n'
                f'[PAYMENTS_ENABLED]\ndescription="x"\nrequiredIf={{var="STRIPE_TOKEN", equals="{sentinel}"}}\n'
            )
        with open(setup_manager.EXAMPLE_FILE, "w") as f:
            f.write(f"STRIPE_TOKEN={sentinel}\nPAYMENTS_ENABLED=\n")

        mocker.patch("envshield.core.setup_manager.Prompt.ask", return_value="true")

        result = runner.invoke(app, ["setup"])

        assert result.exit_code == 0
        assert sentinel not in result.stdout
        assert "Required because STRIPE_TOKEN is set." in result.stdout


def test_setup_reports_configured_count_on_completion(mocker, tmp_path):
    """P0-2: the completion report states how many variables were actually set, instead of a bare '✓ Configuration complete!' with no detail."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _write_root_service_config()
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write('[A]\ndescription="x"\n\n[B]\ndescription="x"\n')
        with open(setup_manager.EXAMPLE_FILE, "w") as f:
            f.write("A=\nB=\n")

        mocker.patch(
            "envshield.core.setup_manager.Prompt.ask",
            side_effect=["a-value", "b-value"],
        )

        result = runner.invoke(app, ["setup"])

        assert result.exit_code == 0
        assert "2 variable(s) set" in result.stdout


def test_setup_reports_partial_completion_across_multiple_services(mocker, tmp_path):
    """
    Regression for P0-2: previously, one declined service silently blanked
    out the entire command's completion report -- even for other services
    that succeeded in the same run -- and installed no hooks even though
    real configuration had just been written. 'setup' must report what
    actually happened, per service and in aggregate, rather than staying
    silent the moment anything is less than a full sweep.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.makedirs("alpha")
        os.makedirs("beta")
        with open("envshield.yml", "w") as f:
            f.write(
                "services:\n  alpha:\n    schema: alpha/env.schema.toml\n  beta:\n    schema: beta/env.schema.toml\n"
            )
        with open("alpha/env.schema.toml", "w") as f:
            f.write('[API_KEY]\ndescription="x"\nsecret=true\n')
        with open("beta/env.schema.toml", "w") as f:
            f.write('[DB_URL]\ndescription="x"\nsecret=true\n')
        with open("beta/.env", "w") as f:
            f.write("OLD=value\n")

        mocker.patch(
            "envshield.core.service_manager._is_interactive", return_value=True
        )
        mock_select = mocker.patch("questionary.select")
        mock_select.return_value.ask.return_value = "All services"
        # beta already has a '.env' -- decline its overwrite prompt.
        mocker.patch("questionary.confirm").return_value.ask.return_value = False
        mocker.patch(
            "envshield.core.setup_manager.Prompt.ask", return_value="alpha-key"
        )

        result = runner.invoke(app, ["setup"])

        assert result.exit_code == 0, result.stdout
        assert "Configuration complete for 1 of 2 service(s)" in result.stdout
        assert "1 variable(s) set" in result.stdout
        assert "1 of 2 service(s) left unchanged" in result.stdout
        with open("alpha/.env") as f:
            assert "API_KEY=alpha-key" in f.read()
        with open("beta/.env") as f:
            assert f.read() == "OLD=value\n"


def test_setup_announces_an_inferred_service_but_not_an_explicit_one(tmp_path):
    """
    P0-5: silently picking one of several configured services based on
    which directory the command happened to run from is worth surfacing --
    the developer didn't say which service they meant, EnvShield guessed.
    An explicit '--service' needs no such explanation; the user already
    said so.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.makedirs("api")
        os.makedirs("web")
        with open("envshield.yml", "w") as f:
            f.write(
                "services:\n  api:\n    schema: api/env.schema.toml\n  web:\n    schema: web/env.schema.toml\n"
            )
        with open("api/env.schema.toml", "w") as f:
            f.write('[API_KEY]\ndescription="x"\ndefaultValue="k"\n')
        with open("web/env.schema.toml", "w") as f:
            f.write('[WEB_KEY]\ndescription="x"\ndefaultValue="k"\n')
        os.chdir("api")

        result = runner.invoke(app, ["setup"])

        assert result.exit_code == 0, result.stdout
        assert "inferred from the current directory" in result.stdout

    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.makedirs("api")
        os.makedirs("web")
        with open("envshield.yml", "w") as f:
            f.write(
                "services:\n  api:\n    schema: api/env.schema.toml\n  web:\n    schema: web/env.schema.toml\n"
            )
        with open("api/env.schema.toml", "w") as f:
            f.write('[API_KEY]\ndescription="x"\ndefaultValue="k"\n')
        with open("web/env.schema.toml", "w") as f:
            f.write('[WEB_KEY]\ndescription="x"\ndefaultValue="k"\n')

        result = runner.invoke(app, ["setup", "--service", "api"])

        assert result.exit_code == 0, result.stdout
        assert "inferred from the current directory" not in result.stdout


def test_setup_does_not_announce_inference_for_a_single_service_project(tmp_path):
    """
    Regression: a single-service project run from its own root directory
    used to be misreported as directory-inferred -- the root service's
    directory trivially equals the invocation directory, so a check that
    only asked "does this match what inference would produce" fired every
    time, even though resolution never even considered more than one
    service. This is the single most common 'setup' invocation in the
    whole product, so it must stay silent.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _write_root_service_config()
        with open(setup_manager.EXAMPLE_FILE, "w") as f:
            f.write("KEY=value\n")

        result = runner.invoke(app, ["setup"])

        assert result.exit_code == 0, result.stdout
        assert "inferred from the current directory" not in result.stdout


def test_setup_does_not_announce_inference_for_an_interactive_pick(mocker, tmp_path):
    """
    Regression: a human explicitly picking a service from the interactive
    prompt is not directory inference, even if that pick happens to be the
    same service cwd would have inferred (here: run from the project root,
    which matches neither service's own directory, so a real ambiguous
    prompt fires) -- the two must stay distinguishable.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.makedirs("api")
        os.makedirs("web")
        with open("envshield.yml", "w") as f:
            f.write(
                "services:\n  api:\n    schema: api/env.schema.toml\n  web:\n    schema: web/env.schema.toml\n"
            )
        with open("api/env.schema.toml", "w") as f:
            f.write('[API_KEY]\ndescription="x"\ndefaultValue="k"\n')
        with open("web/env.schema.toml", "w") as f:
            f.write('[WEB_KEY]\ndescription="x"\ndefaultValue="k"\n')

        mocker.patch(
            "envshield.core.service_manager._is_interactive", return_value=True
        )
        mocker.patch("questionary.select").return_value.ask.return_value = "api"

        result = runner.invoke(app, ["setup"])

        assert result.exit_code == 0, result.stdout
        assert "inferred from the current directory" not in result.stdout
