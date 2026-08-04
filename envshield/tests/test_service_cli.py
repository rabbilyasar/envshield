# envshield/tests/test_service_cli.py
import os

from typer.testing import CliRunner

from envshield.cli import app
from envshield.config import manager as config_manager

runner = CliRunner()


def test_service_list_reports_single_service_project(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        result = runner.invoke(app, ["service", "list"])

        assert result.exit_code == 0
        assert "single-service/root project" in result.stdout


def test_service_add_registers_and_creates_envshield_yml(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.makedirs("athena")

        result = runner.invoke(app, ["service", "add", "athena", "athena"])

        assert result.exit_code == 0
        assert config_manager.get_services()["athena"]["path"] == "athena/env.schema.toml"


def test_service_add_seeds_schema_from_import_file(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.makedirs("athena/config")
        with open("athena/config/env_config.local.py", "w") as f:
            f.write('DB_NAME = "athena"\nAPI_KEY = ""\n')

        result = runner.invoke(
            app,
            [
                "service",
                "add",
                "athena",
                "athena",
                "--local-file",
                "athena/config/env_config.local.py",
                "--import",
                "athena/config/env_config.local.py",
            ],
        )

        assert result.exit_code == 0
        assert os.path.exists("athena/env.schema.toml")
        with open("athena/env.schema.toml") as f:
            content = f.read()
        assert "API_KEY" in content
        assert 'defaultValue = "athena"' in content


def test_service_discover_end_to_end_bootstraps_zeus_shaped_project(mocker, tmp_path):
    """
    The full flow this command exists for: point it at a fresh multi-service
    repo with no envshield.yml at all, and it should find every real
    service, register it, and seed its schema from its real current config
    -- without ever touching a shared library package that merely has a
    project marker.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.makedirs("athena/config")
        with open("athena/config/env_config.local.py", "w") as f:
            f.write('DB_HOST = ""\nDB_NAME = "athena"\nCACHE_PORT = 6379\n')
        os.makedirs("hermes/config")
        with open("hermes/config/env_config.local.py", "w") as f:
            f.write('DB_HOST = ""\nDB_NAME = "hermes"\nCACHE_PORT = 6379\n')
        os.makedirs("modules/phineas")
        with open("modules/phineas/pyproject.toml", "w") as f:
            f.write("[project]\nname = 'phineas'\n")

        result = runner.invoke(app, ["service", "discover", "--yes"])

        assert result.exit_code == 0
        services = config_manager.get_services()
        assert set(services.keys()) == {"athena", "hermes"}
        assert services["athena"]["local_file"] == "athena/config/env_config.local.py"
        assert os.path.exists("athena/env.schema.toml")
        assert os.path.exists("hermes/env.schema.toml")
        with open("athena/env.schema.toml") as f:
            assert 'defaultValue = "athena"' in f.read()


def test_service_discover_extend_only_adds_the_new_service(mocker, tmp_path):
    """Re-running discover after adding a new service must register only the new one, leaving existing ones untouched."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.makedirs("athena")
        with open("athena/.env", "w") as f:
            f.write("API_KEY=abc\n")
        runner.invoke(app, ["service", "discover", "--yes"])

        os.makedirs("hermes")
        with open("hermes/.env", "w") as f:
            f.write("DB_URL=postgres://x\n")
        result = runner.invoke(app, ["service", "discover", "--yes"])

        assert result.exit_code == 0
        assert "Registered hermes" in result.stdout
        assert "Registered athena" not in result.stdout  # already-known, not re-offered
        services = config_manager.get_services()
        assert set(services.keys()) == {"athena", "hermes"}


def test_service_discover_interactive_selection_skips_unchecked_candidates(mocker, tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.makedirs("athena")
        with open("athena/.env", "w") as f:
            f.write("API_KEY=abc\n")
        os.makedirs("hermes")
        with open("hermes/.env", "w") as f:
            f.write("DB_URL=postgres://x\n")

        # Initialize git repo for hook installation prompt
        os.system("git init")

        # Mock questionary prompts: checkbox for service selection, confirm for hooks
        checkbox_mock = mocker.MagicMock()
        checkbox_mock.ask.return_value = ["athena"]
        mocker.patch("questionary.checkbox", return_value=checkbox_mock)

        confirm_mock = mocker.MagicMock()
        confirm_mock.ask.return_value = False  # Don't install hooks
        mocker.patch("questionary.confirm", return_value=confirm_mock)

        result = runner.invoke(app, ["service", "discover"])

        assert result.exit_code == 0, f"Exit code: {result.exit_code}, Output: {result.stdout}"
        assert set(config_manager.get_services().keys()) == {"athena"}


def test_service_discover_finds_mastodon_style_and_nx_style_env_files(tmp_path):
    """
    End-to-end regression for the two real projects whose actual env-file
    naming (Mastodon's '.env.production', Nx's per-target
    '.env.<target>.<configuration>') was previously invisible to discovery.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.makedirs("mastodon-app")
        with open("mastodon-app/.env.production", "w") as f:
            f.write("DB_HOST=localhost\n")
        os.makedirs("apps/api")
        with open("apps/api/.env.serve.development", "w") as f:
            f.write("DB_URL=postgres://x\n")

        result = runner.invoke(app, ["service", "discover", "--yes"])

        assert result.exit_code == 0
        services = config_manager.get_services()
        assert set(services.keys()) == {"mastodon-app", "api"}
        assert services["api"]["local_file"] == "apps/api/.env.serve.development"


def test_service_discover_reports_nothing_new(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        result = runner.invoke(app, ["service", "discover", "--yes"])

        assert result.exit_code == 0
        assert "No new service-like directories found" in result.stdout
