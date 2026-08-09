# envshield/tests/test_service_cli.py
import json
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


def test_service_add_rejects_a_nonexistent_directory(tmp_path):
    """
    Real bug: 'service add foo foo' with no 'foo/' directory anywhere
    silently registered the service and reported success -- indistinguishable
    from a real registration, right up until every other command against
    'foo' failed. A typo'd directory must fail loudly instead, the same
    principle as 'scan' rejecting a nonexistent path.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        result = runner.invoke(app, ["service", "add", "foo", "foo"])

        assert result.exit_code == 1
        assert "does not exist" in result.stdout
        assert config_manager.get_services() == {}


def test_service_remove_points_at_leftover_files_and_next_step_when_last(tmp_path):
    """
    'service remove' never deletes a service's own files -- without saying
    so, and without saying what to do once no services are left at all,
    the bare "Removed" checkmark leaves a developer with no idea their
    schema/local file are still sitting on disk, or what command comes
    next for an otherwise now-uninitialized project.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.makedirs("api")
        runner.invoke(app, ["service", "add", "api", "api"])
        with open("api/env.schema.toml", "w") as f:
            f.write('[API_KEY]\ndescription="x"\nsecret=true\n')
        with open("api/.env", "w") as f:
            f.write("API_KEY=x\n")

        result = runner.invoke(app, ["service", "remove", "api"])

        assert result.exit_code == 0
        assert "api/env.schema.toml" in result.stdout
        assert "api/.env" in result.stdout
        assert os.path.exists("api/env.schema.toml")  # files are untouched
        assert "envshield init" in result.stdout
        assert config_manager.get_services() == {}


def test_service_remove_omits_next_step_hint_when_other_services_remain(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.makedirs("api")
        os.makedirs("web")
        runner.invoke(app, ["service", "add", "api", "api"])
        runner.invoke(app, ["service", "add", "web", "web"])

        result = runner.invoke(app, ["service", "remove", "api"])

        assert result.exit_code == 0
        assert "Next step" not in result.stdout
        assert config_manager.get_services() == {"web": {"schema": "web/env.schema.toml"}}


def test_service_add_warns_when_no_schema_was_created(tmp_path):
    """
    Real gap: 'service add' without '--import' only registers the path in
    envshield.yml -- it never creates a schema file, since there's nothing
    to build one from. Without a warning, the checkmark output reads as
    "done" even though every other command (check/doctor/setup) would
    immediately fail against this service with "schema not found."
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.makedirs("api")

        result = runner.invoke(app, ["service", "add", "api", "api"])

        assert result.exit_code == 0
        assert not os.path.exists("api/env.schema.toml")
        assert "doesn't exist yet" in result.stdout
        assert "import <file> --service api" in result.stdout.replace("\n", " ")


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


def test_commands_find_envshield_yml_from_a_subdirectory(tmp_path):
    """
    envshield.yml lookup now walks upward like Git finds '.git' -- a command
    run from inside a service's own directory shouldn't report an
    uninitialized project just because envshield.yml lives at the repo root.

    Plain os.chdir, not monkeypatch.chdir: isolated_filesystem already
    restores the real pre-test cwd in its own finally block regardless of
    what happens inside it, and stacking monkeypatch's chdir-restore on top
    fires afterwards, once isolated_filesystem has already moved out of
    tmp_path -- leaving the process cwd pointed at a now-orphaned directory.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.makedirs("api/nested")
        runner.invoke(app, ["service", "add", "api", "api"])
        os.chdir("api/nested")

        result = runner.invoke(app, ["service", "list"])

        assert result.exit_code == 0
        assert "api" in result.stdout


def test_doctor_infers_service_from_invocation_directory(tmp_path):
    """
    The other half of directory-context inference: with more than one
    service configured and no --service given, running from inside one
    service's own directory should target that service -- not prompt or
    (worse, with no TTY) crash.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.makedirs("api")
        os.makedirs("web")
        runner.invoke(app, ["service", "add", "api", "api"])
        runner.invoke(app, ["service", "add", "web", "web"])
        os.chdir("api")

        result = runner.invoke(app, ["doctor", "--json"])

        assert result.exit_code in (0, 1)  # health failures are fine; a crash isn't
        payload = json.loads(result.stdout)
        assert [r["service"] for r in payload["results"]] == ["api"]
