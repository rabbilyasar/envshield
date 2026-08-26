# envshield/tests/core/test_doctor.py
import os
import sys

import pytest
from typer.testing import CliRunner

from envshield.cli import app
from envshield.config import manager as config_manager
from envshield.config.manager import CONFIG_FILE_NAME, SCHEMA_FILE_NAME
from envshield.core import doctor
from envshield.core.exceptions import EnvShieldException

runner = CliRunner()


def _write_root_service(name="app", schema_path=SCHEMA_FILE_NAME):
    """Registers one service at the project root -- envshield.yml always has at least one entry (see config_manager.generate_default_config_content)."""
    config_manager.add_service(name, schema_path)


def test_healthcheck_prints_its_message_on_success_too(mocker):
    """
    Regression: a passing check's message was silently dropped in the plain
    Rich rendering -- only 'doctor --json' included it. A check can carry
    real information even while passing (e.g. "using schema default:
    LOG_LEVEL"), and a bare checkmark must not hide it -- the whole point
    of this tool is not letting a green check mean less than it looks like.
    """
    mock_console = mocker.patch("envshield.core.doctor.console")
    check = doctor.HealthCheck(
        "Local Environment Sync",
        lambda: (
            True,
            "'.env' is in sync with schema. Using schema default: LOG_LEVEL.",
        ),
    )

    check.run()

    mock_console.print.assert_any_call(
        "  [dim]'.env' is in sync with schema. Using schema default: LOG_LEVEL.[/dim]"
    )


def test_healthcheck_prints_no_extra_line_when_message_is_empty(mocker):
    """A passing check with nothing further to say shouldn't grow a blank dim line."""
    mock_console = mocker.patch("envshield.core.doctor.console")
    check = doctor.HealthCheck("Git Pre-commit Hook", lambda: (True, ""))

    check.run()

    assert mock_console.print.call_count == 1


def test_run_init_fix_uses_current_interpreter_not_bare_path_lookup(mocker):
    """
    Regression: the 'Configuration Files' fix used to shell out to a bare
    'envshield' command via os.system, which silently did nothing (no error
    surfaced) if the console script wasn't on PATH in whatever shell/venv
    'doctor' happened to be run from. It must invoke the same interpreter
    that's already running -- guaranteed to have envshield importable.
    """
    mock_run = mocker.patch("envshield.core.doctor.subprocess.run")
    mock_run.return_value.returncode = 0

    doctor._run_init_fix()

    mock_run.assert_called_once_with(
        [sys.executable, "-m", "envshield", "init"], check=False
    )


def test_run_init_fix_surfaces_a_non_zero_exit_instead_of_swallowing_it(mocker):
    """The previous os.system call never checked its return code at all -- a failed init looked identical to a successful one."""
    mock_run = mocker.patch("envshield.core.doctor.subprocess.run")
    mock_run.return_value.returncode = 1

    with pytest.raises(EnvShieldException, match="exited with code 1"):
        doctor._run_init_fix()


def test_check_example_file_sync_detects_drift(tmp_path, monkeypatch):
    """
    Regression: this check previously only verified that '.env.example'
    *exists*, so it reported success even when someone added a variable to
    the schema and forgot to run 'schema sync' -- exactly the drift scenario
    doctor exists to catch.
    """
    monkeypatch.chdir(tmp_path)
    _write_root_service()
    with open(SCHEMA_FILE_NAME, "w") as f:
        f.write(
            '[FOO]\ndescription="x"\nsecret=false\n\n[BAR]\ndescription="y"\nsecret=false\n'
        )
    with open(".env.example", "w") as f:
        f.write("FOO=\n")  # BAR is missing

    passed, message = doctor._check_example_file_sync(service_name="app")

    assert passed is False
    assert "BAR" in message


def test_check_example_file_sync_passes_when_in_sync(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_root_service()
    with open(SCHEMA_FILE_NAME, "w") as f:
        f.write('[FOO]\ndescription="x"\nsecret=false\n')
    with open(".env.example", "w") as f:
        f.write("FOO=\n")

    passed, message = doctor._check_example_file_sync(service_name="app")

    assert passed is True


def test_doctor_all_ok(mocker, tmp_path):
    """Tests the doctor command when all checks pass."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _write_root_service()
        mocker.patch(
            "envshield.core.doctor._check_config_files", return_value=(True, "OK")
        )
        mocker.patch(
            "envshield.core.doctor._check_local_env_sync", return_value=(True, "OK")
        )
        mocker.patch(
            "envshield.core.doctor._check_example_file_sync", return_value=(True, "OK")
        )
        mocker.patch(
            "envshield.core.doctor._check_git_hooks", return_value=(True, "OK")
        )

        result = runner.invoke(app, ["doctor"])

        assert result.exit_code == 0
        assert "Everything looks great!" in result.stdout


def test_doctor_with_issues(mocker, tmp_path):
    """Tests the doctor command when checks fail."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _write_root_service()
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
        mocker.patch(
            "envshield.core.doctor._check_git_hooks", return_value=(True, "OK")
        )

        result = runner.invoke(app, ["doctor"])

        assert result.exit_code == 1
        assert "Config missing" in result.stdout
        assert "Example out of sync" in result.stdout
        assert "Some issues were found" in result.stdout


def test_doctor_fix_flow(mocker, tmp_path):
    """Tests the interactive --fix flag for a single, isolated issue."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        # 1. Create a valid config so that only the hook check fails.
        _write_root_service()
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
            "envshield.core.doctor._check_git_hooks",
            side_effect=[(False, "Not installed"), (True, "OK")],
        )

        # Mock the fix function itself -- the fix now installs both hooks,
        # not just pre-commit.
        mock_install_hook = mocker.patch(
            "envshield.core.scanner.install_pre_commit_hook"
        )
        mock_install_post_merge = mocker.patch(
            "envshield.core.scanner.install_post_merge_hook"
        )

        # Correctly mock the chained call for questionary
        mocker.patch(
            "questionary.confirm",
            return_value=mocker.Mock(ask=mocker.Mock(return_value=True)),
        )

        result = runner.invoke(app, ["doctor", "--fix"])

        assert result.exit_code == 0
        assert "Git Hooks" in result.stdout
        assert "Not installed" in result.stdout
        assert "Fixed!" in result.stdout
        mock_install_hook.assert_called_once()
        mock_install_post_merge.assert_called_once()


def test_check_config_files_looks_up_the_services_own_schema_path(
    tmp_path, monkeypatch
):
    """
    Regression: doctor --service used to always check for the ROOT
    'env.schema.toml', even though a multi-service project's schemas live at
    each service's own path -- so a perfectly healthy service was reported
    as missing its schema entirely.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "services" / "api").mkdir(parents=True)
    with open(CONFIG_FILE_NAME, "w") as f:
        f.write("services:\n  api:\n    schema: services/api/env.schema.toml\n")
    with open("services/api/env.schema.toml", "w") as f:
        f.write("[API_KEY]\n")

    passed, message = doctor._check_config_files(service_name="api")

    assert passed is True, message


def test_check_config_files_reports_missing_service_schema(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with open(CONFIG_FILE_NAME, "w") as f:
        f.write("services:\n  api:\n    schema: services/api/env.schema.toml\n")

    passed, message = doctor._check_config_files(service_name="api")

    assert passed is False
    assert "services/api/env.schema.toml" in message


def test_check_local_env_sync_scopes_to_service_directory(tmp_path, monkeypatch):
    """The 'Local Environment Sync' check must look at the service's own '.env', not the root one."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "services" / "api").mkdir(parents=True)
    with open(CONFIG_FILE_NAME, "w") as f:
        f.write("services:\n  api:\n    schema: services/api/env.schema.toml\n")
    with open("services/api/env.schema.toml", "w") as f:
        f.write('[API_KEY]\ndescription="x"\n')
    with open("services/api/.env", "w") as f:
        f.write("API_KEY=abc\n")

    passed, message = doctor._check_local_env_sync(service_name="api")

    assert passed is True, message


def test_check_local_env_sync_flags_a_required_var_declared_but_left_blank(
    tmp_path, monkeypatch
):
    """
    Regression, found via a real incident: 'doctor' reported a service as
    healthy even though a required secret (no schema default) was checked
    into the local Python config module as a blank placeholder
    (`SECRETS_ENCRYPTION_KEY = ""`) -- because only the key's presence was
    checked, never its value. This is exactly the gap that let a developer
    hit a runtime error instead of 'doctor' catching it first.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "beta").mkdir()
    with open(CONFIG_FILE_NAME, "w") as f:
        f.write(
            "services:\n  beta:\n    schema: beta/env.schema.toml\n    local_file: beta/env_config.local.py\n"
        )
    with open("beta/env.schema.toml", "w") as f:
        f.write('[SECRETS_ENCRYPTION_KEY]\ndescription="x"\nsecret=true\n')
    with open("beta/env_config.local.py", "w") as f:
        f.write('SECRETS_ENCRYPTION_KEY = ""\n')

    passed, message = doctor._check_local_env_sync(service_name="beta")

    assert passed is False
    assert "SECRETS_ENCRYPTION_KEY" in message


def test_check_local_env_sync_flags_a_defaulted_var_missing_from_env(
    tmp_path, monkeypatch
):
    """
    A schema variable with a defaultValue, absent from '.env' entirely,
    must fail the check -- nothing guarantees whatever reads '.env'
    actually falls back to the schema's documented default.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "api").mkdir()
    with open(CONFIG_FILE_NAME, "w") as f:
        f.write("services:\n  api:\n    schema: api/env.schema.toml\n")
    with open("api/env.schema.toml", "w") as f:
        f.write(
            '[API_KEY]\ndescription="x"\nsecret=true\n\n[LOG_LEVEL]\ndescription="x"\ndefaultValue="info"\n'
        )
    with open("api/.env", "w") as f:
        f.write("API_KEY=abc\n")

    passed, message = doctor._check_local_env_sync(service_name="api")

    assert passed is False
    assert "LOG_LEVEL" in message
    assert "LOG_LEVEL" in message


def test_check_config_source_drift_flags_a_var_added_to_another_source(
    tmp_path, monkeypatch
):
    """
    Real scenario: '.env' is the pinned config_source, but a developer later
    added LOG_LEVEL straight to config/settings.py and never touched '.env'
    or the schema. Since a pinned source is never re-scanned, nothing else
    would ever catch this -- 'doctor' has to compare against other sources
    directly.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir()
    with open(CONFIG_FILE_NAME, "w") as f:
        f.write(
            "services:\n  api:\n    schema: env.schema.toml\n    config_source: .env\n"
        )
    with open("env.schema.toml", "w") as f:
        f.write('[API_KEY]\ndescription="x"\nsecret=true\n')
    with open(".env", "w") as f:
        f.write("API_KEY=abc\n")
    with open("config/settings.py", "w") as f:
        f.write('API_KEY = "abc"\nDEBUG = True\nLOG_LEVEL = "info"\n')

    passed, message = doctor._check_config_source_drift(service_name="api")

    assert passed is False
    assert "LOG_LEVEL" in message
    assert "config/settings.py" in message
    assert "envshield import config/settings.py" in message


def test_check_config_source_drift_passes_when_no_other_source_exists(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    with open(CONFIG_FILE_NAME, "w") as f:
        f.write(
            "services:\n  api:\n    schema: env.schema.toml\n    config_source: .env\n"
        )
    with open("env.schema.toml", "w") as f:
        f.write('[API_KEY]\ndescription="x"\nsecret=true\n')
    with open(".env", "w") as f:
        f.write("API_KEY=abc\n")

    passed, message = doctor._check_config_source_drift(service_name="api")

    assert passed is True, message


def test_check_config_source_drift_skips_a_malformed_other_source(
    tmp_path, monkeypatch, mocker
):
    """
    BL-002 regression: a malformed 'other source' must be skipped, not
    abort the whole health check via HealthCheck.run()'s coarser outer
    EnvShieldException catch. In practice find_other_config_sources already
    filters a malformed candidate out via _looks_like_python_config_module
    before this loop ever sees it (see BL-002's BACKLOG entry) -- this test
    forces the defense-in-depth path directly by mocking that discovery
    step to return a genuinely malformed file, the same way it could reach
    _check_config_source_drift's loop through some other future caller.
    """
    monkeypatch.chdir(tmp_path)
    with open(CONFIG_FILE_NAME, "w") as f:
        f.write(
            "services:\n  api:\n    schema: env.schema.toml\n    config_source: .env\n"
        )
    with open("env.schema.toml", "w") as f:
        f.write('[API_KEY]\ndescription="x"\nsecret=true\n')
    with open(".env", "w") as f:
        f.write("API_KEY=abc\n")
    with open("broken.py", "w") as f:
        f.write("API_KEY =")  # unterminated -- invalid syntax

    mocker.patch(
        "envshield.core.doctor.service_discovery.find_other_config_sources",
        return_value=[str(tmp_path / "broken.py")],
    )

    passed, message = doctor._check_config_source_drift(service_name="api")

    assert passed is True, message


def test_check_config_source_drift_passes_when_no_config_source_recorded(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    with open(CONFIG_FILE_NAME, "w") as f:
        f.write("services:\n  api:\n    schema: env.schema.toml\n")
    with open("env.schema.toml", "w") as f:
        f.write('[API_KEY]\ndescription="x"\nsecret=true\n')

    passed, message = doctor._check_config_source_drift(service_name="api")

    assert passed is True, message


def test_doctor_omits_config_source_drift_check_when_none_recorded(tmp_path):
    """A project with no recorded config_source (an older envshield.yml) shouldn't see this check at all."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _write_root_service()
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write('[API_KEY]\ndescription="x"\nsecret=true\n')

        checks = doctor._build_checks("app")

        assert not any(c.description == "Config Source Drift" for c in checks)


def test_check_config_source_reads_environment_flags_hardcoded_literals(
    tmp_path, monkeypatch
):
    """
    Real scenario: config_source is a Python module built entirely of
    hardcoded literals, no 'os.environ'/'os.getenv' anywhere. 'check'/
    'doctor' comparing '.env' against the schema can never notice that
    nothing actually wires '.env's values into the running app.
    """
    monkeypatch.chdir(tmp_path)
    with open(CONFIG_FILE_NAME, "w") as f:
        f.write(
            "services:\n  api:\n    schema: env.schema.toml\n    config_source: config/settings.py\n"
        )
    with open("env.schema.toml", "w") as f:
        f.write('[SECRET_KEY]\ndescription="x"\nsecret=true\n')
    os.makedirs("config")
    with open("config/settings.py", "w") as f:
        f.write("SECRET_KEY = 'sk_live_x'\n")

    passed, message = doctor._check_config_source_reads_environment(service_name="api")

    assert passed is False
    assert "config/settings.py" in message
    assert "envshield generate" in message


def test_check_config_source_reads_environment_passes_when_it_does(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    with open(CONFIG_FILE_NAME, "w") as f:
        f.write(
            "services:\n  api:\n    schema: env.schema.toml\n    config_source: config/settings.py\n"
        )
    with open("env.schema.toml", "w") as f:
        f.write('[SECRET_KEY]\ndescription="x"\nsecret=true\n')
    os.makedirs("config")
    with open("config/settings.py", "w") as f:
        f.write("import os\nSECRET_KEY = os.environ['SECRET_KEY']\n")

    passed, message = doctor._check_config_source_reads_environment(service_name="api")

    assert passed is True, message


def test_check_config_source_reads_environment_passes_for_a_dotenv_source(
    tmp_path, monkeypatch
):
    """A dotenv config_source IS the environment values -- nothing for it to 'read'."""
    monkeypatch.chdir(tmp_path)
    with open(CONFIG_FILE_NAME, "w") as f:
        f.write(
            "services:\n  api:\n    schema: env.schema.toml\n    config_source: .env\n"
        )
    with open("env.schema.toml", "w") as f:
        f.write('[SECRET_KEY]\ndescription="x"\nsecret=true\n')
    with open(".env", "w") as f:
        f.write("SECRET_KEY=x\n")

    passed, message = doctor._check_config_source_reads_environment(service_name="api")

    assert passed is True, message


def test_doctor_omits_config_source_reads_environment_check_for_a_dotenv_source(
    tmp_path,
):
    """The check shouldn't even show up for a dotenv config_source, not just pass silently."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        config_manager.add_service("app", SCHEMA_FILE_NAME, config_source=".env")
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write('[API_KEY]\ndescription="x"\nsecret=true\n')

        checks = doctor._build_checks("app")

        assert not any(
            c.description == "Config Source Reads Environment" for c in checks
        )


def test_check_example_file_sync_skips_python_format_local_file(tmp_path, monkeypatch):
    """
    A Python-module local file has no separate '.env.example' to drift out
    of sync -- it IS the contract. This check should pass through with an
    informational message instead of reporting a missing template.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "alpha").mkdir(parents=True)
    with open(CONFIG_FILE_NAME, "w") as f:
        f.write(
            "services:\n  alpha:\n    schema: alpha/env.schema.toml\n    local_file: alpha/env_config.local.py\n"
        )
    with open("alpha/env.schema.toml", "w") as f:
        f.write('[DB_HOST]\ndescription="x"\n')

    passed, message = doctor._check_example_file_sync(service_name="alpha")

    assert passed is True
    assert "no separate template file" in message


def test_check_deployment_manifest_passes_when_none_registered(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_root_service()

    passed, message = doctor._check_deployment_manifest(service_name="app")

    assert passed is True
    assert "nothing to check" in message


def test_check_deployment_manifest_flags_drift(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_root_service()
    config_manager.add_manifest("docker-compose.yml", {"app": "app"})
    with open(SCHEMA_FILE_NAME, "w") as f:
        f.write('[API_KEY]\ndescription="x"\n')
    with open("docker-compose.yml", "w") as f:
        f.write("services:\n  app:\n    image: x\n")

    passed, message = doctor._check_deployment_manifest(service_name="app")

    assert passed is False
    assert "API_KEY" in message


def test_check_deployment_manifest_passes_when_in_sync(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_root_service()
    config_manager.add_manifest("docker-compose.yml", {"app": "app"})
    with open(SCHEMA_FILE_NAME, "w") as f:
        f.write('[API_KEY]\ndescription="x"\n')
    with open("docker-compose.yml", "w") as f:
        f.write("services:\n  app:\n    environment:\n      - API_KEY=secret\n")

    passed, _ = doctor._check_deployment_manifest(service_name="app")

    assert passed is True


def test_doctor_omits_deployment_manifest_check_when_none_registered(tmp_path):
    """A project that doesn't use a deployment manifest shouldn't see a check for it at all."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _write_root_service()
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write('[API_KEY]\ndescription="x"\ndefaultValue="x"\n')
        with open(".env.example", "w") as f:
            f.write("API_KEY=x\n")
        with open(".env", "w") as f:
            f.write("API_KEY=x\n")

        result = runner.invoke(app, ["doctor"])

        assert "Deployment Manifest" not in result.stdout


def test_doctor_includes_deployment_manifest_check_when_registered(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _write_root_service()
        config_manager.add_manifest("docker-compose.yml", {"app": "app"})
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write('[API_KEY]\ndescription="x"\ndefaultValue="x"\n')
        with open(".env.example", "w") as f:
            f.write("API_KEY=x\n")
        with open(".env", "w") as f:
            f.write("API_KEY=x\n")
        with open("docker-compose.yml", "w") as f:
            f.write("services:\n  app:\n    environment:\n      - API_KEY=x\n")

        result = runner.invoke(app, ["doctor"])

        assert "Deployment Manifest" in result.stdout


def test_doctor_fix_for_local_env_sync_delegates_to_setup_wizard(mocker, tmp_path):
    """
    The 'Local Environment Sync' fix must reuse 'setup' rather than
    reimplementing its own prompting -- it already knows how to re-validate
    existing values and leave correct ones untouched.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _write_root_service()
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write('[API_KEY]\ndescription="x"\n')
        # No local .env at all -- 'Local Environment Sync' will fail.
        mocker.patch(
            "envshield.core.doctor._check_example_file_sync", return_value=(True, "OK")
        )
        mocker.patch(
            "envshield.core.doctor._check_git_hooks", return_value=(True, "OK")
        )
        mock_run_setup = mocker.patch("envshield.core.setup_manager.run_setup")
        mocker.patch(
            "questionary.confirm",
            return_value=mocker.Mock(ask=mocker.Mock(return_value=True)),
        )

        runner.invoke(app, ["doctor", "--fix"])

        mock_run_setup.assert_called_once_with(service_name="app")
