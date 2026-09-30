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
        assert config_manager.get_services() == {
            "web": {"schema": "web/env.schema.toml"}
        }


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
        assert manifests == [
            {
                "path": "docker-compose.yml",
                "paths": ["docker-compose.yml"],
                "container": "api",
            }
        ]
        assert "docker-compose.yml" in result.stdout


def test_service_add_auto_detects_compose_file_in_service_directory(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.makedirs("api")
        with open("api/docker-compose.yml", "w") as f:
            f.write("services:\n  api:\n    image: x\n")

        result = runner.invoke(app, ["service", "add", "api", "api"])

        assert result.exit_code == 0
        manifests = config_manager.get_deployment_manifests("api")
        assert manifests == [
            {
                "path": "api/docker-compose.yml",
                "paths": ["api/docker-compose.yml"],
                "container": "api",
            }
        ]


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
        assert manifests == [
            {
                "path": "docker-compose.yml",
                "paths": ["docker-compose.yml"],
                "container": "backend",
            }
        ]


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


class TestGitBoundaryPreventsFalseCleanAcrossNestedRepos:
    """
    P0 regression, direct end-to-end reproduction of the reported failure
    mode: a nested, independent Git repository (its own '.git', its own
    unrelated '.env') sitting inside an EnvShield-managed project must
    never let 'check'/'doctor' silently resolve and validate the OUTER
    project's file while the invocation is actually standing inside the
    inner, unrelated repository. Before the fix, the everyday no-argument
    form of both commands -- not the contrived explicit-path form -- did
    exactly that: confidently reported the outer project's file as clean,
    the worst possible failure mode for a validation tool.
    """

    def _write_outer_project_with_nested_repo(self, root):
        """
        outer/ (root, its own '.git')
          envshield.yml -- registers service 'edge'
          edge/
            env.schema.toml, .env  -- the outer project's own, genuinely-clean file
            reserved3/  -- an INDEPENDENT nested repo, own '.git', own unrelated .env
        Returns reserved3's absolute path.
        """
        os.makedirs(os.path.join(root, "edge"))
        os.makedirs(os.path.join(root, ".git"))
        with open(os.path.join(root, "envshield.yml"), "w") as f:
            f.write("services:\n  edge:\n    schema: edge/env.schema.toml\n")
        with open(os.path.join(root, "edge", "env.schema.toml"), "w") as f:
            f.write('[OUTER_REQUIRED_VAR]\ndescription="x"\n')
        with open(os.path.join(root, "edge", ".env"), "w") as f:
            f.write("OUTER_REQUIRED_VAR=value\n")

        nested_repo = os.path.join(root, "edge", "reserved3")
        os.makedirs(os.path.join(nested_repo, ".git"))
        with open(os.path.join(nested_repo, ".env"), "w") as f:
            f.write("TOTALLY_UNRELATED_VAR=foo\n")
        return nested_repo

    def test_check_no_args_does_not_report_the_outer_projects_env_as_clean(
        self, tmp_path
    ):
        with runner.isolated_filesystem(temp_dir=tmp_path) as root:
            nested_repo = self._write_outer_project_with_nested_repo(root)
            os.chdir(nested_repo)

            result = runner.invoke(app, ["check", "--json"])

            payload = json.loads(result.stdout)
            # The exact pre-fix symptom: never claim the outer service's
            # file was checked and found clean from inside the nested repo.
            for entry in payload.get("results", []):
                assert not (
                    entry.get("service") == "edge" and entry.get("clean") is True
                )
            assert payload.get("success") is not True

    def test_doctor_no_args_does_not_report_the_outer_projects_service_healthy(
        self, tmp_path
    ):
        with runner.isolated_filesystem(temp_dir=tmp_path) as root:
            nested_repo = self._write_outer_project_with_nested_repo(root)
            os.chdir(nested_repo)

            result = runner.invoke(app, ["doctor", "--json"])

            payload = json.loads(result.stdout)
            assert not any(
                entry.get("service") == "edge" for entry in payload.get("results", [])
            )

    def test_check_and_doctor_still_work_normally_from_the_outer_project_itself(
        self, tmp_path
    ):
        """Sanity check: the fix must not break the legitimate case -- running from the outer project itself still finds and validates it."""
        with runner.isolated_filesystem(temp_dir=tmp_path) as root:
            self._write_outer_project_with_nested_repo(root)

            check_result = runner.invoke(app, ["check", "--json"])
            check_payload = json.loads(check_result.stdout)
            assert check_payload["success"] is True
            assert check_payload["results"][0]["service"] == "edge"
            assert check_payload["results"][0]["clean"] is True

            doctor_result = runner.invoke(app, ["doctor", "--json"])
            doctor_payload = json.loads(doctor_result.stdout)
            assert doctor_payload["results"][0]["service"] == "edge"


class TestPythonLocalFileSurfaceIsDefinedOnce:
    """
    Regression: a Python config module registered as a service's
    'local_file' had its schema seeded by 'import' using one definition
    of "this file's configuration surface" (environment reads discovered
    anywhere in the file) while 'check' subsequently validated that same
    file using a different, disjoint one (top-level assignments -- the
    format PythonParser exists for, and the format
    service_discovery._looks_like_python_config_module used to recognise
    the file as a config module in the first place).

    The two rules only ever agree by luck. On a real 'config as code'
    module -- literal assignments, plus a couple of `os.environ` reads
    guarding local overrides -- they disagree completely: the schema was
    seeded with just the handful of read names, and 'check' then
    reported every genuine assignment as "Extra in Local" and the seeded
    names as "Missing in Local". A first run that is wrong in both
    directions at once.

    The rule is now chosen by the file's ROLE, not guessed from its
    contents: a file being registered as this service's local values
    file is read exactly the way every command that consumes a local
    values file reads it. 'import' on some other Python file (a settings
    module that genuinely resolves its config from the environment)
    keeps its existing discovery behaviour -- see
    test_importer.py for that side.
    """

    LOCAL_CONFIG_MODULE = (
        "import os\n"
        "\n"
        'API_ADMIN_TOKEN = "abc123"\n'
        'DB_NAME = "app"\n'
        'DB_USER = ""\n'
        'EMAIL_FROM_ADDRESS = "support@example.com"\n'
        "\n"
        "# Local overrides -- the only os.environ reads in the whole file.\n"
        'if os.environ.get("USE_LOCAL_DB") == "yes":\n'
        '    DB_HOST = "db"\n'
    )

    def _service_with_python_local_file(self):
        os.makedirs("api/config")
        with open("api/config/env_config.local.py", "w") as f:
            f.write(self.LOCAL_CONFIG_MODULE)
        return runner.invoke(
            app,
            [
                "service",
                "add",
                "api",
                "api",
                "--local-file",
                "api/config/env_config.local.py",
                "--import",
                "api/config/env_config.local.py",
            ],
        )

    def test_seeded_schema_covers_the_modules_real_assignments(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            result = self._service_with_python_local_file()

            assert result.exit_code == 0
            schema = config_manager.load_schema("api")
            assert {
                "API_ADMIN_TOKEN",
                "DB_NAME",
                "DB_USER",
                "EMAIL_FROM_ADDRESS",
            } <= set(schema)

    def test_check_reports_no_drift_against_the_file_it_was_seeded_from(self, tmp_path):
        """
        The property that actually matters: seeding a schema from a file
        and immediately validating that same, unmodified file must not
        invent drift in either direction.
        """
        with runner.isolated_filesystem(temp_dir=tmp_path):
            self._service_with_python_local_file()

            result = runner.invoke(app, ["check", "--service", "api", "--json"])
            payload = json.loads(result.stdout)
            local = next(
                r
                for r in payload["results"]
                if r["file"] == "api/config/env_config.local.py"
            )

            assert local["extra"] == []
            assert local["missing"] == []

    def test_a_secret_looking_assignment_is_still_classified_as_secret(self, tmp_path):
        """Seeding by role must not bypass the importer's secret classification."""
        with runner.isolated_filesystem(temp_dir=tmp_path):
            self._service_with_python_local_file()

            schema = config_manager.load_schema("api")

            assert schema["API_ADMIN_TOKEN"]["secret"] is True
            assert "defaultValue" not in schema["API_ADMIN_TOKEN"]


# --- Shared system schema, Phase 2: 'service add' must persist DIRECTORY.


def test_service_add_with_shared_schema_persists_the_directory(tmp_path):
    """Real bug: DIRECTORY was only used for an existence check and the
    default schema path -- with --schema it was silently discarded."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.makedirs("api")
        os.makedirs("worker")
        with open("env.schema.toml", "w") as f:
            f.write("[LOG_LEVEL]\n")

        for name in ("api", "worker"):
            result = runner.invoke(
                app, ["service", "add", name, name, "--schema", "env.schema.toml"]
            )
            assert result.exit_code == 0, result.stdout

        services = config_manager.get_services()
        assert services["api"]["dir"] == "api"
        assert services["worker"]["dir"] == "worker"
        assert config_manager.get_service_dir("worker") == "worker"
        assert config_manager.get_env_paths("worker")["local_file"] == os.path.join(
            "worker", ".env"
        )


def test_service_add_default_schema_path_writes_no_dir_key(tmp_path):
    """Legacy shape stays byte-for-byte: DIRECTORY == schema parent."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.makedirs("alpha")
        runner.invoke(app, ["service", "add", "alpha", "alpha"])
        runner.invoke(
            app,
            ["service", "add", "beta", "alpha", "--schema", "alpha/other.schema.toml"],
        )
        services = config_manager.get_services()
        assert services["alpha"] == {"schema": "alpha/env.schema.toml"}
        assert "dir" not in services["beta"]


def test_service_add_joining_a_schema_persists_dir_even_when_it_equals_the_parent(
    tmp_path,
):
    """Joining an existing schema makes it shared -- the new service's dir
    must be explicit even if it happens to match the schema's parent."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.makedirs("api")
        with open("env.schema.toml", "w") as f:
            f.write("[LOG_LEVEL]\n")
        runner.invoke(
            app, ["service", "add", "api", "api", "--schema", "env.schema.toml"]
        )
        result = runner.invoke(
            app, ["service", "add", "web", ".", "--schema", "env.schema.toml"]
        )
        assert result.exit_code == 0, result.stdout
        assert config_manager.get_services()["web"]["dir"] == "."


def test_service_add_re_add_replaces_a_stale_dir_with_the_schema_parent(tmp_path):
    """Re-adding with DIRECTORY equal to the schema's parent must not keep
    a previously persisted, different 'dir' (add_service merges)."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.makedirs("api")
        os.makedirs("old")
        with open("api/env.schema.toml", "w") as f:
            f.write("[LOG_LEVEL]\n")
        args = ["service", "add", "api"]
        schema = ["--schema", "api/env.schema.toml"]
        assert runner.invoke(app, [*args, "old", *schema]).exit_code == 0
        assert config_manager.get_services()["api"]["dir"] == "old"

        result = runner.invoke(app, [*args, "api", *schema])
        assert result.exit_code == 0, result.stdout
        assert config_manager.get_services()["api"].get("dir") != "old"
        assert config_manager.get_service_dir("api") == "api"


def test_service_add_warns_when_another_user_of_the_schema_lacks_a_dir(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.makedirs("worker")
        with open("env.schema.toml", "w") as f:
            f.write("[LOG_LEVEL]\n")
        runner.invoke(
            app, ["service", "add", "api", ".", "--schema", "env.schema.toml"]
        )
        assert "dir" not in config_manager.get_services()["api"]

        result = runner.invoke(
            app, ["service", "add", "worker", "worker", "--schema", "env.schema.toml"]
        )
        assert result.exit_code == 0, result.stdout
        assert "api" in result.stdout and "dir" in result.stdout
        # Not silently backfilled -- the user must choose api's directory.
        assert "dir" not in config_manager.get_services()["api"]


# BL-143: 'service remove' on shared resources. Removal proceeds with a
# warning; it never offers a file another registered service still uses for
# deletion, and the resulting topology still fails closed where invalid.
SHARED_SCHEMA_YML = (
    "services:\n"
    "  api:\n    schema: env.schema.toml\n    dir: api\n"
    "  worker:\n    schema: env.schema.toml\n    dir: worker\n"
)
SCOPED_SCHEMA = (
    '[LOG_LEVEL]\ndefaultValue = "info"\n'
    '[DATABASE_URL]\nsecret = true\nservices = ["api", "worker"]\n'
    '[QUEUE_URL]\nsecret = true\nservices = ["worker"]\n'
)


def _flat(text):
    return " ".join(text.split())


def test_service_remove_never_offers_a_schema_another_service_uses(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.makedirs("api")
        os.makedirs("worker")
        with open("envshield.yml", "w") as f:
            f.write(SHARED_SCHEMA_YML)
        with open("env.schema.toml", "w") as f:
            f.write('[LOG_LEVEL]\ndefaultValue = "info"\n')

        result = runner.invoke(app, ["service", "remove", "worker"])

        out = _flat(result.stdout)
        assert result.exit_code == 0, out
        assert "Delete them by hand" not in out
        assert "env.schema.toml" in out and "still used by api" in out
        assert os.path.exists("env.schema.toml")
        assert list(config_manager.get_services()) == ["api"]


def test_service_remove_warns_about_grants_naming_the_removed_service(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.makedirs("api")
        os.makedirs("worker")
        with open("envshield.yml", "w") as f:
            f.write(SHARED_SCHEMA_YML)
        with open("env.schema.toml", "w") as f:
            f.write(SCOPED_SCHEMA)

        result = runner.invoke(app, ["service", "remove", "worker"])

        out = _flat(result.stdout)
        assert result.exit_code == 0, out  # warn and proceed
        assert "Warning" in out and "worker" in out and "api" in out
        assert "services" in out  # points at the grants to fix
        assert list(config_manager.get_services()) == ["api"]
        with open("env.schema.toml") as f:
            assert f.read() == SCOPED_SCHEMA  # never rewritten

        # The resulting topology is invalid, so api fails closed.
        check = runner.invoke(app, ["check", "--service", "api"])
        assert check.exit_code != 0
        assert "worker" in _flat(check.stdout)


def test_service_remove_with_valid_remaining_topology_does_not_warn(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.makedirs("api")
        os.makedirs("worker")
        with open("envshield.yml", "w") as f:
            f.write(SHARED_SCHEMA_YML)
        with open("env.schema.toml", "w") as f:
            f.write('[LOG_LEVEL]\ndefaultValue = "info"\n')

        result = runner.invoke(app, ["service", "remove", "worker"])

        assert "Warning" not in result.stdout


def test_service_remove_never_offers_a_shared_local_file(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        for d in ("api", "worker"):
            os.makedirs(d)
            with open(f"{d}/env.schema.toml", "w") as f:
                f.write("[LOG_LEVEL]\n")
        with open("envshield.yml", "w") as f:
            f.write(
                "services:\n"
                "  api:\n    schema: api/env.schema.toml\n    local_file: .env\n"
                "  worker:\n    schema: worker/env.schema.toml\n    local_file: .env\n"
            )
        with open(".env", "w") as f:
            f.write("LOG_LEVEL=info\n")

        result = runner.invoke(app, ["service", "remove", "worker"])

        out = _flat(result.stdout)
        assert result.exit_code == 0, out
        # worker's own schema is still offered; the shared .env is not.
        assert "Its files are untouched: worker/env.schema.toml." in out
        assert ".env still used by api" in out


def test_service_remove_never_offers_a_schema_another_service_extends(tmp_path):
    # A root service whose schema is the base a nested service extends: the
    # file is part of api's contract even though api doesn't register it.
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.makedirs("api")
        with open("env.schema.toml", "w") as f:
            f.write("[LOG_LEVEL]\n")
        with open("api/env.schema.toml", "w") as f:
            f.write('extends = "../env.schema.toml"\n[API_KEY]\n')
        with open("envshield.yml", "w") as f:
            f.write(
                "services:\n"
                "  root:\n    schema: env.schema.toml\n"
                "  api:\n    schema: api/env.schema.toml\n"
            )

        result = runner.invoke(app, ["service", "remove", "root"])

        out = _flat(result.stdout)
        assert result.exit_code == 0, out
        assert "Delete them by hand" not in out
        assert "env.schema.toml still used by api" in out


# BL-137 (option a): two users of one shared schema can't declare the same
# directory. 'service add' rejects it at registration.
def test_service_add_rejects_joining_a_shared_schema_at_a_taken_directory(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.makedirs("app")
        with open("env.schema.toml", "w") as f:
            f.write('[LOG_LEVEL]\ndefaultValue = "info"\n')
        assert (
            runner.invoke(
                app, ["service", "add", "web", "app", "--schema", "env.schema.toml"]
            ).exit_code
            == 0
        )
        before = open("envshield.yml").read()

        result = runner.invoke(
            app, ["service", "add", "worker", "./app/", "--schema", "env.schema.toml"]
        )

        out = _flat(result.stdout)
        assert result.exit_code == 1, out
        assert "web" in out and "app" in out
        assert open("envshield.yml").read() == before


def test_service_add_re_adding_the_same_service_at_its_own_directory_is_fine(
    tmp_path,
):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.makedirs("app")
        os.makedirs("worker")
        with open("env.schema.toml", "w") as f:
            f.write('[LOG_LEVEL]\ndefaultValue = "info"\n')
        for name, d in (("web", "app"), ("worker", "worker"), ("web", "app")):
            result = runner.invoke(
                app, ["service", "add", name, d, "--schema", "env.schema.toml"]
            )
            assert result.exit_code == 0, result.stdout


def test_service_add_rejects_joining_a_legacy_services_schema_at_its_directory(
    tmp_path,
):
    """KemonChilo shape: a legacy root service (no 'dir', resolved to the
    schema's parent) and a second process registered as its own service in
    the same directory. The legacy user's directory is the schema parent,
    so the join claims the same code and is rejected."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        with open("env.schema.toml", "w") as f:
            f.write('[LOG_LEVEL]\ndefaultValue = "info"\n')
        assert runner.invoke(app, ["service", "add", "app", "."]).exit_code == 0
        assert "dir" not in config_manager.get_services()["app"]
        before = open("envshield.yml").read()

        result = runner.invoke(
            app, ["service", "add", "worker", ".", "--schema", "env.schema.toml"]
        )

        assert result.exit_code == 1, result.stdout
        assert "app" in _flat(result.stdout) and "worker" in _flat(result.stdout)
        assert open("envshield.yml").read() == before
        assert config_manager.get_service_dir("app") == "."  # still valid
