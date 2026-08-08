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
        assert "Run 'envshield init' first" in result.stdout


def test_service_add_registers_and_creates_envshield_yml(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.makedirs("alpha")

        result = runner.invoke(app, ["service", "add", "alpha", "alpha"])

        assert result.exit_code == 0
        assert (
            config_manager.get_services()["alpha"]["schema"] == "alpha/env.schema.toml"
        )


def test_service_add_seeds_schema_from_import_file(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.makedirs("alpha/config")
        with open("alpha/config/env_config.local.py", "w") as f:
            f.write('DB_NAME = "alpha"\nAPI_KEY = ""\n')

        result = runner.invoke(
            app,
            [
                "service",
                "add",
                "alpha",
                "alpha",
                "--local-file",
                "alpha/config/env_config.local.py",
                "--import",
                "alpha/config/env_config.local.py",
            ],
        )

        assert result.exit_code == 0
        assert os.path.exists("alpha/env.schema.toml")
        with open("alpha/env.schema.toml") as f:
            content = f.read()
        assert "API_KEY" in content
        assert 'defaultValue = "alpha"' in content


def test_service_discover_end_to_end_bootstraps_acme_shaped_project(mocker, tmp_path):
    """
    The full flow this command exists for: point it at a fresh multi-service
    repo with no envshield.yml at all, and it should find every real
    service, register it, and seed its schema from its real current config
    -- without ever touching a shared library package that merely has a
    project marker.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.makedirs("alpha/config")
        with open("alpha/config/env_config.local.py", "w") as f:
            f.write('DB_HOST = ""\nDB_NAME = "alpha"\nCACHE_PORT = 6379\n')
        os.makedirs("beta/config")
        with open("beta/config/env_config.local.py", "w") as f:
            f.write('DB_HOST = ""\nDB_NAME = "beta"\nCACHE_PORT = 6379\n')
        os.makedirs("modules/sharedlib")
        with open("modules/sharedlib/pyproject.toml", "w") as f:
            f.write("[project]\nname = 'sharedlib'\n")

        result = runner.invoke(app, ["service", "discover", "--yes"])

        assert result.exit_code == 0
        services = config_manager.get_services()
        assert set(services.keys()) == {"alpha", "beta"}
        assert services["alpha"]["local_file"] == "alpha/config/env_config.local.py"
        assert os.path.exists("alpha/env.schema.toml")
        assert os.path.exists("beta/env.schema.toml")
        with open("alpha/env.schema.toml") as f:
            assert 'defaultValue = "alpha"' in f.read()


def test_service_discover_extend_only_adds_the_new_service(mocker, tmp_path):
    """Re-running discover after adding a new service must register only the new one, leaving existing ones untouched."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.makedirs("alpha")
        with open("alpha/.env", "w") as f:
            f.write("API_KEY=abc\n")
        runner.invoke(app, ["service", "discover", "--yes"])

        os.makedirs("beta")
        with open("beta/.env", "w") as f:
            f.write("DB_URL=postgres://x\n")
        result = runner.invoke(app, ["service", "discover", "--yes"])

        assert result.exit_code == 0
        assert "Registered beta" in result.stdout
        assert "Registered alpha" not in result.stdout  # already-known, not re-offered
        services = config_manager.get_services()
        assert set(services.keys()) == {"alpha", "beta"}


def test_service_discover_finds_multiple_services(tmp_path):
    """Test that service discover detects multiple services correctly."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.makedirs("alpha")
        with open("alpha/.env", "w") as f:
            f.write("API_KEY=abc\n")
        os.makedirs("beta")
        with open("beta/.env", "w") as f:
            f.write("DB_URL=postgres://x\n")

        # Test discovery by directly testing service discovery logic
        from envshield.core.service_discovery import discover_candidates

        candidates = discover_candidates(".")

        # Should find both services
        assert len(candidates) >= 2
        candidate_names = {c["name"] for c in candidates}
        assert "alpha" in candidate_names
        assert "beta" in candidate_names


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


def test_service_discover_auto_registers_a_found_compose_file(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.makedirs("api")
        with open("api/.env", "w") as f:
            f.write("KEY=1\n")
        with open("docker-compose.yml", "w") as f:
            f.write("services:\n  api:\n    image: x\n")

        result = runner.invoke(app, ["service", "discover", "--yes"])

        assert result.exit_code == 0
        manifests = config_manager.get_deployment_manifests("api")
        assert manifests == [{"path": "docker-compose.yml", "container": "api"}]
        assert "docker-compose.yml" in result.stdout


def test_service_add_auto_detects_compose_file_in_service_directory(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.makedirs("api")
        with open("api/docker-compose.yml", "w") as f:
            f.write("services:\n  api:\n    image: x\n")

        result = runner.invoke(app, ["service", "add", "api", "api"])

        assert result.exit_code == 0
        manifests = config_manager.get_deployment_manifests("api")
        assert manifests == [{"path": "api/docker-compose.yml", "container": "api"}]


def test_service_add_does_not_auto_attach_a_manifest_that_does_not_name_it(tmp_path):
    """
    Regression: a shared root compose file used to get auto-attached to
    any service directory regardless of whether it's actually declared in
    it -- silently validating against the wrong container. An explicit
    --deployment-manifest still always works (see the test right below).
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.makedirs("docs")
        with open("docker-compose.yml", "w") as f:
            f.write("services:\n  api:\n    image: x\n")

        result = runner.invoke(app, ["service", "add", "docs", "docs"])

        assert result.exit_code == 0
        assert config_manager.get_deployment_manifests("docs") == []
        assert "docker-compose.yml" not in result.stdout


def test_service_add_explicit_deployment_manifest_and_container(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.makedirs("api")
        with open("docker-compose.yml", "w") as f:
            f.write("services:\n  backend:\n    image: x\n")

        result = runner.invoke(
            app,
            [
                "service",
                "add",
                "api",
                "api",
                "--deployment-manifest",
                "docker-compose.yml",
                "--container",
                "backend",
            ],
        )

        assert result.exit_code == 0
        manifests = config_manager.get_deployment_manifests("api")
        assert manifests == [{"path": "docker-compose.yml", "container": "backend"}]
