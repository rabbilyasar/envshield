# envshield/tests/test_cli.py
import json
import os

from typer.testing import CliRunner

from envshield.cli import app
from envshield.config import manager as config_manager
from envshield.config.manager import CONFIG_FILE_NAME, SCHEMA_FILE_NAME
from envshield.core.scanner import MAX_SCANNABLE_SIZE_BYTES

runner = CliRunner()


def _write_root_service(name="app", schema_path=SCHEMA_FILE_NAME):
    """
    Registers one service at the project root -- envshield.yml always has
    at least one entry, single-service or not (see
    config_manager.generate_default_config_content), so every command that
    resolves a target needs a real registration in this file's fixtures.
    """
    config_manager.add_service(name, schema_path)


def test_init_command_in_git_repo(tmp_path, mocker):
    """Tests the init command in a clean, git-initialized directory."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.system("git init")
        # Mock the hook installation prompt to answer "yes"
        mocker.patch("envshield.core.hooks_manager._is_interactive", return_value=True)
        mocker.patch("questionary.confirm").return_value.ask.return_value = True
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 0, (
            f"Exit code: {result.exit_code}, Output: {result.stdout}"
        )
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


def test_init_auto_registers_a_root_level_compose_file(tmp_path, mocker):
    """
    init's derived service name is the project directory's own basename --
    the compose file has to actually declare a container by that name for
    auto-registration to fire (see compose_declares_service), so this
    fixture names its one container after whatever that basename turns out
    to be, instead of an arbitrary unrelated name.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        service_name = os.path.basename(os.getcwd())
        with open("docker-compose.yml", "w") as f:
            f.write(f"services:\n  {service_name}:\n    image: x\n")
        mocker.patch("questionary.confirm").return_value.ask.return_value = False

        result = runner.invoke(app, ["init"])

        assert result.exit_code == 0
        manifests = config_manager.get_deployment_manifests(service_name)
        assert manifests == [{"path": "docker-compose.yml", "container": service_name}]


def test_init_command_in_non_git_repo(tmp_path):
    """Tests that init succeeds but doesn't install hooks if not in a git repo."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 0
        assert os.path.exists(CONFIG_FILE_NAME)
        assert os.path.exists(SCHEMA_FILE_NAME)
        # No git repo, so no hooks should be installed
        assert not os.path.exists(".git/hooks/pre-commit")


def test_check_command_happy_path(tmp_path):
    """Tests the check command when the .env file is in sync."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _write_root_service()
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
        _write_root_service()
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write('[API_KEY]\ndescription="Test"\n[SECRET]\ndescription="Secret"')
        with open(".env", "w") as f:
            f.write("API_KEY=12345")
        result = runner.invoke(app, ["check"])
        assert result.exit_code == 1
        assert "Missing in Local" in result.stdout
        assert "SECRET" in result.stdout


def test_check_auto_validates_a_registered_deployment_manifest_too(tmp_path):
    """
    A registered deployment manifest is checked automatically alongside the
    default local file, in the same 'envshield check' invocation -- no need
    to remember it's there, or to run 'check docker-compose.yml' separately.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _write_root_service()
        config_manager.add_manifest("docker-compose.yml", {"app": "app"})
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write('[API_KEY]\ndescription="Test"\n[MANIFEST_ONLY]\ndescription="x"\n')
        with open(".env", "w") as f:
            f.write("API_KEY=12345\nMANIFEST_ONLY=x\n")
        with open("docker-compose.yml", "w") as f:
            f.write("services:\n  app:\n    environment:\n      - API_KEY=12345\n")

        result = runner.invoke(app, ["check"])

        # The .env file is fully in sync; the compose file is missing MANIFEST_ONLY.
        assert result.exit_code == 1
        assert result.stdout.count("Validating") == 2
        assert "docker-compose.yml" in result.stdout
        assert "MANIFEST_ONLY" in result.stdout


def test_check_suggestion_for_a_deployment_manifest_does_not_recommend_setup(tmp_path):
    """
    Regression: a missing/blank/invalid finding against a deployment
    manifest used to print "Run 'envshield setup' to fill in..." -- setup
    only ever writes a service's local file, never a deployment manifest,
    so that suggestion was actively wrong advice for this target.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _write_root_service()
        config_manager.add_manifest("docker-compose.yml", {"app": "app"})
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write('[API_KEY]\ndescription="Test"\n[MANIFEST_ONLY]\ndescription="x"\n')
        with open(".env", "w") as f:
            f.write("API_KEY=12345\nMANIFEST_ONLY=x\n")
        with open("docker-compose.yml", "w") as f:
            f.write("services:\n  app:\n    environment:\n      - API_KEY=12345\n")

        result = runner.invoke(app, ["check"])

        assert result.exit_code == 1
        assert "Run 'envshield setup'" not in result.stdout
        assert "only writes your local config file" in result.stdout


def test_doctor_reports_unresolved_for_a_kubernetes_manifest_with_an_external_env_from(
    tmp_path,
):
    """
    End-to-end: doctor's 'Deployment Manifest' check must surface the same
    unresolved state check/check_result do, via the shared
    diff_against_schema computation -- not report a clean pass, and not
    claim the variable is definitely missing either.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _write_root_service()
        config_manager.add_manifest("deployment.yaml", {"app": "app"})
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write('[DATABASE_URL]\ndescription = "x"\n')
        with open(".env", "w") as f:
            f.write("")
        with open("deployment.yaml", "w") as f:
            f.write(
                "apiVersion: apps/v1\n"
                "kind: Deployment\n"
                "metadata:\n  name: app\n"
                "spec:\n"
                "  template:\n"
                "    spec:\n"
                "      containers:\n"
                "        - name: app\n"
                "          envFrom:\n"
                "            - secretRef:\n"
                "                name: externally-managed-secret\n"
            )

        result = runner.invoke(app, ["doctor", "--json"])

        payload = json.loads(result.stdout)
        checks = {c["name"]: c for c in payload["results"][0]["checks"]}
        manifest_check = checks["Deployment Manifest"]
        assert manifest_check["passed"] is False
        assert "Cannot confirm" in manifest_check["message"]
        assert "Missing variables" not in manifest_check["message"]


def test_check_with_explicit_file_does_not_also_check_the_registered_manifest(tmp_path):
    """An explicit file argument means 'check exactly this' -- the auto-check is only the default-file convenience."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _write_root_service()
        config_manager.add_manifest("docker-compose.yml", {"app": "app"})
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write('[API_KEY]\ndescription="Test"\n')
        with open(".env", "w") as f:
            f.write("API_KEY=12345\n")
        with open(".env.other", "w") as f:
            f.write("API_KEY=12345\n")
        with open("docker-compose.yml", "w") as f:
            f.write("services:\n  app:\n    image: x\n")

        result = runner.invoke(app, ["check", ".env.other"])

        assert result.exit_code == 0
        assert result.stdout.count("Validating") == 1


def test_schema_sync_command(tmp_path):
    """Tests that schema sync correctly generates a .env.example file."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _write_root_service()
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
        # Real gap: a successful sync said nothing about what to do with
        # the result -- commit it? does anything else need updating?
        assert "Next step" in result.stdout
        assert "envshield setup" in result.stdout


def test_schema_sync_reports_no_op_on_a_second_run_with_no_schema_change(tmp_path):
    """
    Real gap this reproduces: re-running 'schema sync' with nothing
    changed still claimed "Successfully created/updated" and told the
    developer to review and commit -- even though the file's actual
    content (ignoring the regenerated timestamp) is identical to before.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _write_root_service()
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write('[API_KEY]\ndescription="x"\ndefaultValue="abc"\n')

        first = runner.invoke(app, ["schema", "sync"])
        assert first.exit_code == 0
        assert "Successfully created/updated" in first.stdout

        second = runner.invoke(app, ["schema", "sync"])

        assert second.exit_code == 0
        assert "already up to date" in second.stdout
        assert "Successfully created/updated" not in second.stdout
        assert "Next step" not in second.stdout
        assert "Nothing to do" in second.stdout


def test_schema_sync_check_prints_no_next_step_hint_when_passing(tmp_path):
    """'--check' is a read-only verification -- unlike a real sync, passing cleanly needs no further action, matching 'check'/'doctor's own convention."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _write_root_service()
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write('[API_KEY]\ndescription="x"\ndefaultValue="abc"\n')
        with open(".env.example", "w") as f:
            f.write("# x\nAPI_KEY=abc\n")

        result = runner.invoke(app, ["schema", "sync", "--check"])

        assert result.exit_code == 0
        assert "Next step" not in result.stdout


def test_schema_sync_check_passes_and_writes_nothing_when_already_in_sync(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _write_root_service()
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write('[API_KEY]\ndescription="x"\ndefaultValue="abc"\n')
        with open(".env.example", "w") as f:
            f.write("# x\nAPI_KEY=abc\n")
        before = os.path.getmtime(".env.example")

        result = runner.invoke(app, ["schema", "sync", "--check"])

        assert result.exit_code == 0
        assert os.path.getmtime(".env.example") == before  # never written to


def test_schema_sync_check_fails_without_writing_when_template_is_stale(tmp_path):
    """
    Regression target: a schema edited by hand with no matching
    '.env.example' update must be caught, not silently accepted -- this is
    what the pre-commit hook now runs before allowing a commit that touches
    a schema file.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _write_root_service()
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write(
                '[API_KEY]\ndescription="x"\ndefaultValue="abc"\n\n[NEW_VAR]\ndescription="y"\n'
            )
        with open(".env.example", "w") as f:
            f.write("# x\nAPI_KEY=abc\n")  # NEW_VAR never synced in

        result = runner.invoke(app, ["schema", "sync", "--check"])

        assert result.exit_code == 1
        assert "NEW_VAR" in result.stdout
        with open(".env.example") as f:
            assert "NEW_VAR" not in f.read()  # --check never writes


def test_schema_sync_check_fails_for_a_stale_python_local_file(tmp_path):
    """
    BL-005 regression, at the CLI layer 'schema sync --check' actually
    runs (not just doctor._check_example_file_sync in isolation): a
    Python-module local_file used to short-circuit to an unconditional
    pass here regardless of its real contents, which is exactly the
    silent false-clean this command exists to prevent -- see this
    finding's own entry for the live-proven pre-commit-hook bypass this
    produced.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.makedirs("svc")
        with open(CONFIG_FILE_NAME, "w") as f:
            f.write(
                "services:\n  svc:\n    schema: svc/env.schema.toml\n    local_file: svc/config.py\n"
            )
        with open("svc/env.schema.toml", "w") as f:
            f.write(
                '[FIELD_PRESENT]\ndescription="p"\ndefaultValue="x"\n\n'
                '[FIELD_MISSING]\ndescription="m"\nrequired=true\n'
            )
        with open("svc/config.py", "w") as f:
            f.write('FIELD_PRESENT = "hello"\n')  # FIELD_MISSING never declared

        result = runner.invoke(app, ["schema", "sync", "--check", "--service", "svc"])

        assert result.exit_code == 1
        assert "FIELD_MISSING" in result.stdout


def test_schema_sync_check_passes_for_a_union_satisfied_python_local_file(tmp_path):
    """
    BL-030's interaction with BL-005, at the real CLI layer the installed
    pre-commit hook actually invokes (not doctor._check_example_file_sync
    in isolation): for a completeness: union service, this delegated
    unconditionally to the old single-file check, which would keep failing
    here for a variable a registered Compose manifest already covers --
    the exact BL-005 class of bug (a check disagreeing with what running
    'sync' would actually do), reachable through completeness: union
    rather than the original bare-Python-file bypass. Deliberately the
    mirror image of test_schema_sync_check_fails_for_a_stale_python_local_file
    directly above: same missing-from-the-Python-file shape, but this time
    a registered manifest genuinely covers it, so --check must pass.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.makedirs("svc")
        with open(CONFIG_FILE_NAME, "w") as f:
            f.write(
                "services:\n"
                "  svc:\n"
                "    schema: svc/env.schema.toml\n"
                "    local_file: svc/config.py\n"
                "    completeness: union\n"
                "manifests:\n"
                "  - file: docker-compose.yml\n"
                "    containers:\n"
                "      svc: svc\n"
            )
        with open("svc/env.schema.toml", "w") as f:
            f.write(
                '[FIELD_PRESENT]\ndescription="p"\ndefaultValue="x"\n\n'
                '[FIELD_FROM_COMPOSE]\ndescription="Compose-owned"\n'
            )
        with open("svc/config.py", "w") as f:
            f.write(
                'FIELD_PRESENT = "hello"\n'
            )  # FIELD_FROM_COMPOSE never declared here
        with open("docker-compose.yml", "w") as f:
            f.write(
                'services:\n  svc:\n    image: x\n    environment:\n      FIELD_FROM_COMPOSE: "on"\n'
            )

        result = runner.invoke(app, ["schema", "sync", "--check", "--service", "svc"])

        assert result.exit_code == 0, result.stdout
        # And running the real (non---check) sync must agree with --check's
        # verdict: nothing to add, since the union already covers it.
        with open("svc/config.py") as f:
            before = f.read()
        runner.invoke(app, ["schema", "sync", "--service", "svc"])
        with open("svc/config.py") as f:
            after = f.read()
        assert before == after


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


def test_import_command_warns_about_commented_out_variables(tmp_path):
    """
    End-to-end PDF finding 3.4 regression, through the actual 'import'
    command, not just the underlying importer.generate_schema_from_file
    helper: a real self-hosted-project '.env' commonly documents optional
    settings as commented-out examples, which must be warned about rather
    than silently dropped.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        with open("docker-compose.env", "w") as f:
            f.write(
                "PAPERLESS_REDIS=redis://broker:6379\n"
                "#PAPERLESS_OCR_LANGUAGE=eng\n"
                "#PAPERLESS_SECRET_KEY=change-me\n"
            )

        result = runner.invoke(app, ["import", "docker-compose.env"])

        assert result.exit_code == 0
        assert (
            "Found 2 commented-out variable assignment(s); these were not imported."
            in result.stdout
        )
        with open(SCHEMA_FILE_NAME, "r") as f:
            content = f.read()
            assert "PAPERLESS_REDIS" in content
            assert "PAPERLESS_OCR_LANGUAGE" not in content
            assert "PAPERLESS_SECRET_KEY" not in content


def test_import_command_skips_oversized_value_as_default(tmp_path):
    """
    End-to-end PDF finding 3.7 regression, through the actual 'import'
    command: a single oversized value (e.g. a misconfigured multi-MB
    variable) must not balloon the generated schema file, and the
    variable must still show up in it, just without that value as its
    suggested default.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        oversized_value = "A" * (MAX_SCANNABLE_SIZE_BYTES + 1)
        with open(".env", "w") as f:
            f.write(f"BIG_VALUE={oversized_value}\nNORMAL=fine\n")

        result = runner.invoke(app, ["import", ".env"])

        assert result.exit_code == 0
        assert "Skipped 1 value(s)" in result.stdout
        assert "BIG_VALUE" in result.stdout
        with open(SCHEMA_FILE_NAME, "r") as f:
            content = f.read()
        assert "[BIG_VALUE]" in content
        assert oversized_value[:1000] not in content
        assert 'defaultValue = "fine"' in content
        # The generated schema itself must stay small -- the whole point
        # of the guard, not just an absence-of-substring check.
        assert os.path.getsize(SCHEMA_FILE_NAME) < 10_000


def test_generate_command_creates_typed_config_module(tmp_path):
    """Tests that `envshield generate` writes a pydantic-settings module from the schema."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write(
                '[DATABASE_URL]\ndescription = "DB URL"\nsecret = true\n\n[LOG_LEVEL]\ndescription = "Verbosity"\nsecret = false\ndefaultValue = "info"\n'
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


def test_generate_command_refuses_to_shadow_an_existing_package_directory(tmp_path):
    """
    Real incident: a Flask/Django-style project with a 'config/settings.py'
    package, generating to the default 'config.py' output. CPython resolves
    a plain module over a same-named package in the same directory, so this
    would silently make 'import config' return the generated file instead
    of the real package -- breaking every 'from config.settings import ...'
    in the app, with nothing at write time to warn about it. Not
    overridable by --force: the output path itself has to change.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write('[API_KEY]\ndescription = "Test"\nsecret = true\n')
        os.makedirs("config")
        with open("config/settings.py", "w") as f:
            f.write("SECRET_KEY = 'x'\n")

        result = runner.invoke(app, ["generate"])

        assert result.exit_code == 1
        assert "shadow" in result.stdout
        assert not os.path.exists("config.py")

        # --force must not paper over this -- it's not an overwrite problem.
        result = runner.invoke(app, ["generate", "--force"])
        assert result.exit_code == 1
        assert not os.path.exists("config.py")


def test_generate_command_with_explicit_typescript_lang(tmp_path):
    """Tests that `--lang typescript` produces a zod config module at config.ts."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write(
                '[DATABASE_URL]\ndescription = "DB URL"\nsecret = true\n\n[LOG_LEVEL]\ndescription = "Verbosity"\nsecret = false\ndefaultValue = "info"\n'
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


def test_generate_command_on_a_go_project_requires_explicit_lang(tmp_path):
    """
    Regression: a detected-but-unmapped ecosystem (Go) used to silently fall
    back to Python codegen with no error and no warning -- a Go project has
    nothing to do with pydantic-settings, so that's a wrong file nobody
    asked for, not a helpful default. It must now ask for --lang explicitly.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        with open("go.mod", "w") as f:
            f.write("module example.com/testsvc\ngo 1.22\n")
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write('[API_KEY]\ndescription = "Test"\nsecret = true\n')

        result = runner.invoke(app, ["generate"])

        assert result.exit_code == 1
        assert "no default codegen target" in result.stdout
        assert not os.path.exists("config.py")

        # --lang still works explicitly, same as any other project.
        result = runner.invoke(app, ["generate", "--lang", "python"])
        assert result.exit_code == 0
        assert os.path.exists("config.py")


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
    os.makedirs("alpha")
    os.makedirs("beta")
    with open(CONFIG_FILE_NAME, "w") as f:
        f.write(
            "services:\n  alpha:\n    schema: alpha/env.schema.toml\n  beta:\n    schema: beta/env.schema.toml\n"
        )
    with open("alpha/env.schema.toml", "w") as f:
        f.write('[API_KEY]\ndescription="Alpha key"\nsecret=true\n')
    with open("beta/env.schema.toml", "w") as f:
        f.write('[DB_URL]\ndescription="Beta DB"\nsecret=true\n')


def test_schema_sync_without_service_prompts_and_runs_for_all_services(
    mocker, tmp_path
):
    """
    Regression: omitting --service on a multi-service project used to
    silently sync a single root-level '.env.example', ignoring every
    configured service. It must now offer 'All services' and, when chosen,
    sync each one into its own directory.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _write_two_service_project()
        mocker.patch(
            "envshield.core.service_manager._is_interactive", return_value=True
        )
        mock_select = mocker.patch("questionary.select")
        mock_select.return_value.ask.return_value = "All services"

        result = runner.invoke(app, ["schema", "sync"])

        mock_select.assert_called_once()
        assert result.exit_code == 0
        assert "── alpha ──" in result.stdout
        assert "── beta ──" in result.stdout
        assert os.path.exists("alpha/.env.example")
        assert os.path.exists("beta/.env.example")
        assert not os.path.exists(".env.example")


def test_schema_sync_without_service_auto_selects_the_only_service(mocker, tmp_path):
    """With only one service configured, there's nothing to choose -- it's used automatically, no prompt."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.makedirs("alpha")
        with open(CONFIG_FILE_NAME, "w") as f:
            f.write("services:\n  alpha:\n    schema: alpha/env.schema.toml\n")
        with open("alpha/env.schema.toml", "w") as f:
            f.write('[API_KEY]\ndescription="x"\nsecret=true\n')
        mock_select = mocker.patch("questionary.select")

        result = runner.invoke(app, ["schema", "sync"])

        assert result.exit_code == 0
        mock_select.assert_not_called()
        assert os.path.exists("alpha/.env.example")


def test_doctor_without_service_runs_for_all_services(mocker, tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _write_two_service_project()
        with open("alpha/.env", "w") as f:
            f.write("API_KEY=abc\n")
        with open("beta/.env", "w") as f:
            f.write("DB_URL=postgres://x\n")
        mocker.patch(
            "envshield.core.service_manager._is_interactive", return_value=True
        )
        mock_select = mocker.patch("questionary.select")
        mock_select.return_value.ask.return_value = "All services"

        result = runner.invoke(app, ["doctor"])

        mock_select.assert_called_once()
        assert "── alpha ──" in result.stdout
        assert "── beta ──" in result.stdout


def test_check_without_service_runs_for_all_services(mocker, tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _write_two_service_project()
        with open("alpha/.env", "w") as f:
            f.write("API_KEY=abc\n")
        with open("beta/.env", "w") as f:
            f.write("DB_URL=postgres://x\n")
        mocker.patch(
            "envshield.core.service_manager._is_interactive", return_value=True
        )
        mock_select = mocker.patch("questionary.select")
        mock_select.return_value.ask.return_value = "All services"

        result = runner.invoke(app, ["check"])

        mock_select.assert_called_once()
        assert result.exit_code == 0
        assert "── alpha ──" in result.stdout
        assert "── beta ──" in result.stdout
        assert "perfectly in sync" in result.stdout


def test_generate_scan_and_import_report_available_services_for_an_unknown_one(
    tmp_path,
):
    """
    Regression: 'check'/'doctor'/'setup' listed available services on an
    unknown --service, but 'generate'/'scan'/'import' each had their own
    inline check with no listing. All should behave the same now.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _write_two_service_project()

        for args in (
            ["generate", "--service", "bogus"],
            ["scan", ".", "--service", "bogus"],
            ["import", "alpha/env.schema.toml", "--service", "bogus"],
        ):
            result = runner.invoke(app, args)
            assert result.exit_code == 1, f"{args}: {result.stdout}"
            assert "not found" in result.stdout
            assert "alpha, beta" in result.stdout, f"{args}: {result.stdout}"


def test_import_command_syncs_the_env_example_template(tmp_path):
    """Regression: import used to leave .env.example stale after changing the schema it feeds."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        with open("settings.py", "w") as f:
            f.write("SECRET_KEY = 'x'\nAPI_PORT = 5000\n")

        result = runner.invoke(app, ["import", "settings.py"])

        assert result.exit_code == 0
        assert os.path.exists(".env.example")
        with open(".env.example", "r") as f:
            content = f.read()
            assert "SECRET_KEY" in content
            assert "API_PORT" in content


def test_import_force_preserves_existing_variables_not_in_the_new_source(tmp_path):
    """Same merge-safety as 'init --force': a re-import must only add to the schema, never silently drop what's already declared."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        with open("settings.py", "w") as f:
            f.write("SECRET_KEY = 'x'\nLOG_LEVEL = 'info'\n")
        runner.invoke(app, ["import", "settings.py"])

        with open("settings.py", "w") as f:
            f.write("SECRET_KEY = 'x'\n")  # LOG_LEVEL dropped from the source

        result = runner.invoke(app, ["import", "settings.py", "--force"])

        assert result.exit_code == 0, result.stdout
        with open(SCHEMA_FILE_NAME) as f:
            assert "LOG_LEVEL" in f.read()


def test_import_force_preserves_an_extends_directive(tmp_path):
    """'extends' is a schema-composition directive, not a variable -- a value-scan has no way to rediscover it if a re-import drops it."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        with open("base.schema.toml", "w") as f:
            f.write('[LOG_LEVEL]\ndescription="x"\ndefaultValue="info"\n')
        with open("settings.py", "w") as f:
            f.write("SECRET_KEY = 'x'\n")
        runner.invoke(app, ["import", "settings.py"])
        with open(SCHEMA_FILE_NAME) as f:
            content = f.read()
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write('extends = "base.schema.toml"\n\n' + content)

        result = runner.invoke(app, ["import", "settings.py", "--force"])

        assert result.exit_code == 0, result.stdout
        with open(SCHEMA_FILE_NAME) as f:
            assert 'extends = "base.schema.toml"' in f.read()


def test_init_builds_schema_from_a_real_config_source_instead_of_the_template(
    tmp_path, mocker
):
    """
    Regression: init used to always write the generic framework template even
    when the project already had real config (e.g. config/settings.py) sitting
    right there, forcing a separate 'envshield import ... --force' just to get
    an accurate schema.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.system("git init -q")
        mocker.patch("questionary.confirm").return_value.ask.return_value = False
        with open("requirements.txt", "w") as f:
            f.write("Flask\n")
        os.makedirs("config")
        with open("config/settings.py", "w") as f:
            f.write(
                "SECRET_KEY = 'x'\nDATABASE_URL = 'postgres://x'\nDEBUG = True\nAPI_PORT = 5000\nLOG_LEVEL = 'info'\n"
            )

        result = runner.invoke(app, ["init"])

        assert result.exit_code == 0, result.stdout
        with open(SCHEMA_FILE_NAME, "r") as f:
            content = f.read()
            # The fixed python-flask template only ever has these three --
            # DEBUG/API_PORT/LOG_LEVEL prove the real file was used instead.
            assert "DEBUG" in content
            assert "API_PORT" in content
            assert "LOG_LEVEL" in content


def test_init_records_the_config_source_it_used(tmp_path, mocker):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.system("git init -q")
        mocker.patch("questionary.confirm").return_value.ask.return_value = False
        os.makedirs("config")
        with open("config/settings.py", "w") as f:
            f.write("SECRET_KEY = 'x'\nDEBUG = True\nAPI_PORT = 5000\n")

        runner.invoke(app, ["init"])

        project_name = os.path.basename(os.getcwd())
        assert (
            config_manager.get_service_config_source(project_name)
            == "config/settings.py"
        )


def test_init_force_reuses_the_recorded_config_source_instead_of_redetecting(
    tmp_path, mocker
):
    """
    Real incident this reproduces: the first 'init' builds the schema from
    'config/settings.py' (5 variables, including LOG_LEVEL). Later, 'setup'
    creates a '.env' -- and a developer manually removes LOG_LEVEL from it.
    Re-running 'init --force' used to re-detect a config source from
    scratch, and a real '.env' always wins that detection over a Python
    module -- silently rebuilding the schema from the now-incomplete '.env'
    and losing LOG_LEVEL, even though 'config/settings.py' still declares
    it. It must keep using the originally-recorded source instead.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.system("git init -q")
        mocker.patch("questionary.confirm").return_value.ask.return_value = True
        os.makedirs("config")
        with open("config/settings.py", "w") as f:
            f.write(
                "SECRET_KEY = 'x'\nDATABASE_URL = 'postgres://x'\nDEBUG = True\n"
                "API_PORT = 5000\nLOG_LEVEL = 'info'\n"
            )
        runner.invoke(app, ["init"])

        # Simulate the drifted '.env': present, but missing LOG_LEVEL.
        with open(".env", "w") as f:
            f.write(
                "SECRET_KEY=x\nDATABASE_URL=postgres://x\nDEBUG=True\nAPI_PORT=5000\n"
            )

        result = runner.invoke(app, ["init", "--force"])

        assert result.exit_code == 0, result.stdout
        with open(SCHEMA_FILE_NAME) as f:
            assert "LOG_LEVEL" in f.read()


def test_init_force_preserves_a_variable_missing_from_a_drifted_source(
    tmp_path, mocker
):
    """
    Even if the recorded source itself is what changed (not just a rival
    '.env'), a variable already declared in the current schema must never
    be silently dropped just because this scan's source doesn't mention it
    -- only ever added to, never destructively replaced.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.system("git init -q")
        mocker.patch("questionary.confirm").return_value.ask.return_value = True
        os.makedirs("config")
        with open("config/settings.py", "w") as f:
            f.write(
                "SECRET_KEY = 'x'\nDEBUG = True\nAPI_PORT = 5000\nLOG_LEVEL = 'info'\n"
            )
        runner.invoke(app, ["init"])

        # The recorded source file itself loses a variable.
        with open("config/settings.py", "w") as f:
            f.write("SECRET_KEY = 'x'\n")

        result = runner.invoke(app, ["init", "--force"])

        assert result.exit_code == 0, result.stdout
        with open(SCHEMA_FILE_NAME) as f:
            assert "LOG_LEVEL" in f.read()


def test_init_force_preserves_a_hand_corrected_secret_classification(tmp_path, mocker):
    """A manual fix to the schema (e.g. correcting a wrong 'secret' guess) must survive a re-scan, not get silently reclassified back."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.system("git init -q")
        mocker.patch("questionary.confirm").return_value.ask.return_value = True
        os.makedirs("config")
        with open("config/settings.py", "w") as f:
            f.write(
                "DATABASE_URL = 'plain-placeholder'\nDEBUG = True\nAPI_PORT = 5000\n"
            )
        runner.invoke(app, ["init"])

        # Hand-correct the (wrongly guessed non-secret) classification for
        # DATABASE_URL only -- also dropping its inferred defaultValue,
        # since secret=true alongside a defaultValue is itself rejected at
        # schema-load time as of BL-001 (correctly: a hand-corrected secret
        # field's old placeholder default is exactly the kind of leftover
        # this should force the user to reconsider, not silently keep).
        with open(SCHEMA_FILE_NAME) as f:
            content = f.read()
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write(
                content.replace(
                    'secret = false\ndefaultValue = "plain-placeholder"',
                    "secret = true",
                )
            )

        result = runner.invoke(app, ["init", "--force"])

        assert result.exit_code == 0, result.stdout
        with open(SCHEMA_FILE_NAME) as f:
            content = f.read()
        assert "secret = true" in content


def test_init_merges_a_variable_only_present_in_another_config_source(tmp_path, mocker):
    """
    Real incident this reproduces: '.env' is (and stays) the pinned
    config_source, but 'config/settings.py' gains LOG_LEVEL that never
    makes it into '.env'. Since a pinned source is only ever re-scanned
    itself, nothing would otherwise notice -- 'init' has to check other
    real config sources sitting alongside it too, not just wait for a
    separate 'doctor' run to catch the drift.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.system("git init -q")
        mocker.patch("questionary.confirm").return_value.ask.return_value = True
        os.makedirs("config")
        with open("config/settings.py", "w") as f:
            f.write(
                "SECRET_KEY = 'x'\nDATABASE_URL = 'postgres://x'\nDEBUG = True\n"
                "API_PORT = 5000\n"
            )
        with open(".env", "w") as f:
            f.write(
                "SECRET_KEY=x\nDATABASE_URL=postgres://x\nDEBUG=True\nAPI_PORT=5000\n"
            )
        runner.invoke(app, ["init"])

        # LOG_LEVEL lands in the code but never in the pinned '.env'.
        with open("config/settings.py", "a") as f:
            f.write("LOG_LEVEL = 'info'\n")

        result = runner.invoke(app, ["init", "--force"])

        assert result.exit_code == 0, result.stdout
        assert "config/settings.py" in result.stdout
        assert "LOG_LEVEL" in result.stdout
        with open(SCHEMA_FILE_NAME) as f:
            content = f.read()
        assert "[LOG_LEVEL]" in content
        assert 'defaultValue = "info"' in content
        # The pinned source stays '.env' -- only the merge closed the gap.
        project_name = os.path.basename(os.getcwd())
        assert config_manager.get_service_config_source(project_name) == ".env"


def test_init_force_offers_to_repin_once_the_recorded_source_is_envshield_generated(
    tmp_path, mocker
):
    """
    Interactively confirming the repin prompt re-detects from scratch
    instead of reusing the recorded config_source -- needed once a
    project's '.env' has since become an EnvShield-generated artifact (via
    'setup'), which find_config_source now deliberately skips in favor of
    a genuine Python config module.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.system("git init -q")
        mocker.patch("questionary.confirm").return_value.ask.return_value = True
        mocker.patch("envshield.core.hooks_manager._is_interactive", return_value=True)
        with open(".env", "w") as f:
            f.write("SECRET_KEY=x\n")
        runner.invoke(app, ["init"])

        project_name = os.path.basename(os.getcwd())
        assert config_manager.get_service_config_source(project_name) == ".env"

        # The real source of truth shows up later; 'setup' regenerates
        # '.env' from the schema, making it a derivative, not independent.
        os.makedirs("config")
        with open("config/settings.py", "w") as f:
            f.write("SECRET_KEY = 'x'\nDEBUG = True\nAPI_PORT = 5000\n")
        with open(".env", "w") as f:
            f.write(
                "# Auto-generated by 'envshield setup' on 2026-01-01\n\nSECRET_KEY=x\n"
            )

        result = runner.invoke(app, ["init", "--force"])

        assert result.exit_code == 0, result.stdout
        assert (
            config_manager.get_service_config_source(project_name)
            == "config/settings.py"
        )


def test_init_force_keeps_the_recorded_source_when_repin_prompt_is_declined(
    tmp_path, mocker
):
    """Declining the repin prompt must keep reusing the recorded config_source -- no silent repin."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.system("git init -q")
        mocker.patch("envshield.core.hooks_manager._is_interactive", return_value=True)
        with open(".env", "w") as f:
            f.write("SECRET_KEY=x\n")
        confirm = mocker.patch("questionary.confirm")
        confirm.return_value.ask.return_value = True
        runner.invoke(app, ["init"])

        os.makedirs("config")
        with open("config/settings.py", "w") as f:
            f.write("SECRET_KEY = 'x'\nDEBUG = True\nAPI_PORT = 5000\n")
        with open(".env", "w") as f:
            f.write(
                "# Auto-generated by 'envshield setup' on 2026-01-01\n\nSECRET_KEY=x\n"
            )

        # Still confirm the destructive overwrite, but decline the repin.
        confirm.return_value.ask.side_effect = [True, False]
        result = runner.invoke(app, ["init", "--force"])

        assert result.exit_code == 0, result.stdout
        project_name = os.path.basename(os.getcwd())
        assert config_manager.get_service_config_source(project_name) == ".env"


def test_init_force_keeps_the_recorded_source_without_a_tty_to_ask(tmp_path, mocker):
    """
    No TTY to prompt (CI/scripting) must default to keeping the recorded
    config_source rather than either silently repinning or blocking --
    consistent with every other no-TTY-safe confirmation in this codebase.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.system("git init -q")
        mocker.patch("questionary.confirm").return_value.ask.return_value = True
        with open(".env", "w") as f:
            f.write("SECRET_KEY=x\n")
        runner.invoke(app, ["init"])

        os.makedirs("config")
        with open("config/settings.py", "w") as f:
            f.write("SECRET_KEY = 'x'\nDEBUG = True\nAPI_PORT = 5000\n")
        with open(".env", "w") as f:
            f.write(
                "# Auto-generated by 'envshield setup' on 2026-01-01\n\nSECRET_KEY=x\n"
            )

        result = runner.invoke(app, ["init", "--force"])

        assert result.exit_code == 0, result.stdout
        project_name = os.path.basename(os.getcwd())
        assert config_manager.get_service_config_source(project_name) == ".env"


def test_init_falls_back_to_the_framework_template_when_no_real_config_exists(
    tmp_path, mocker
):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.system("git init -q")
        mocker.patch("questionary.confirm").return_value.ask.return_value = False
        with open("requirements.txt", "w") as f:
            f.write("Flask\n")

        result = runner.invoke(app, ["init"])

        assert result.exit_code == 0, result.stdout
        with open(SCHEMA_FILE_NAME, "r") as f:
            content = f.read()
            assert "SECRET_KEY" in content
            assert "FLASK_ENV" in content


def test_init_proceeds_without_force_when_envshield_yml_has_zero_services(tmp_path):
    """
    Real bug this reproduces: 'service remove' can leave 'envshield.yml'
    with 'services: {}' (removing the last one) and then suggests running
    'envshield init' next -- but init's "already exists" gate checked raw
    file existence, not whether there was anything real registered, so its
    own suggested recovery command failed with "already exists, use
    --force." A config file with zero services must be treated the same
    as no config file at all, everywhere -- including here.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        with open(CONFIG_FILE_NAME, "w") as f:
            f.write("project_name: test\nservices: {}\n")

        result = runner.invoke(app, ["init"])

        assert result.exit_code == 0, result.stdout
        assert "already exists" not in result.stdout
        assert config_manager.get_services()


def test_init_still_blocks_without_force_when_a_real_service_exists(tmp_path, mocker):
    """Sanity check: the fix above must not weaken the normal 'already exists' gate when a real service is registered."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.makedirs("alpha")
        runner.invoke(app, ["service", "add", "alpha", "alpha"])
        with open("alpha/env.schema.toml", "w") as f:
            f.write('[API_KEY]\ndescription="x"\nsecret=true\n')

        result = runner.invoke(app, ["init"])

        assert result.exit_code == 0, result.stdout
        assert "already exists" in result.stdout


def test_init_warns_and_cancels_when_declining_a_detected_multi_service_layout(
    tmp_path, mocker
):
    """
    Real bug this reproduces: 'init' on a project shaped like a monorepo
    (service-like subdirectories, nothing real at the root) used to
    silently fabricate a generic, fictional single-service schema and
    declare success -- never mentioning the real services at all. It must
    instead warn and let the user back out in favor of 'service discover'.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.system("git init -q")
        mocker.patch("envshield.core.hooks_manager._is_interactive", return_value=True)
        os.makedirs("api")
        with open("api/.env", "w") as f:
            f.write("API_KEY=abc\n")
        os.makedirs("web")
        with open("web/.env", "w") as f:
            f.write("WEB_KEY=xyz\n")
        # Decline the "continue anyway?" prompt.
        mocker.patch("questionary.confirm").return_value.ask.return_value = False

        result = runner.invoke(app, ["init"])

        assert result.exit_code == 0, result.stdout
        assert "multi-service" in result.stdout
        assert "service discover" in result.stdout
        assert not os.path.exists(SCHEMA_FILE_NAME)


def test_init_proceeds_with_generic_template_when_confirming_anyway(tmp_path, mocker):
    """Confirming 'continue anyway' still produces the generic fallback template, same as before this check existed."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.system("git init -q")
        mocker.patch("envshield.core.hooks_manager._is_interactive", return_value=True)
        os.makedirs("api")
        with open("api/.env", "w") as f:
            f.write("API_KEY=abc\n")
        os.makedirs("web")
        with open("web/.env", "w") as f:
            f.write("WEB_KEY=xyz\n")
        mocker.patch("questionary.confirm").return_value.ask.return_value = True

        result = runner.invoke(app, ["init"])

        assert result.exit_code == 0, result.stdout
        assert os.path.exists(SCHEMA_FILE_NAME)


def test_init_warns_but_proceeds_without_a_tty_to_ask(tmp_path):
    """No TTY to ask (CI/scripting) must not silently block 'init' -- warn and keep going, same as every other no-TTY-safe prompt."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.system("git init -q")
        os.makedirs("api")
        with open("api/.env", "w") as f:
            f.write("API_KEY=abc\n")
        os.makedirs("web")
        with open("web/.env", "w") as f:
            f.write("WEB_KEY=xyz\n")

        result = runner.invoke(app, ["init"])

        assert result.exit_code == 0, result.stdout
        assert "multi-service" in result.stdout
        assert os.path.exists(SCHEMA_FILE_NAME)


def test_init_says_nothing_when_no_multi_service_layout_is_detected(tmp_path, mocker):
    """Sanity check: an ordinary single-service project must not see this warning at all."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.system("git init -q")
        mocker.patch("questionary.confirm").return_value.ask.return_value = False
        with open("requirements.txt", "w") as f:
            f.write("Flask\n")

        result = runner.invoke(app, ["init"])

        assert result.exit_code == 0, result.stdout
        assert "multi-service" not in result.stdout


def test_check_json_reports_clean_state(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _write_root_service()
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write('[API_KEY]\ndescription = "Test"\nsecret = true\n')
        with open(".env", "w") as f:
            f.write("API_KEY=abc123\n")

        result = runner.invoke(app, ["check", "--json"])

        assert result.exit_code == 0, result.stdout
        payload = json.loads(result.stdout)
        assert payload == {
            "success": True,
            "results": [
                {
                    "file": ".env",
                    "service": "app",
                    "clean": True,
                    "missing": [],
                    "blank": [],
                    "invalid": {},
                    "extra": [],
                    "unresolved": [],
                }
            ],
        }


def test_check_json_reports_drift_and_exits_nonzero(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _write_root_service()
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write('[API_KEY]\ndescription = "Test"\nsecret = true\n')
        with open(".env", "w") as f:
            f.write("API_KEY=\nEXTRA_VAR=x\n")

        result = runner.invoke(app, ["check", "--json"])

        assert result.exit_code == 1
        payload = json.loads(result.stdout)
        assert payload["success"] is False
        assert payload["results"][0]["blank"] == ["API_KEY"]
        assert payload["results"][0]["extra"] == ["EXTRA_VAR"]
        # No Rich table/markup leaked into the JSON stream.
        assert "[bold" not in result.stdout


def _write_generic_union_service(schema_toml, python_local_content, compose_content):
    """
    Shared CLI-level fixture for BL-030: one generic union-mode service
    ('app'), a Python local_file, one registered Compose manifest. No Zeus
    paths/names -- deliberately generic, per BL-030's testing requirements.
    """
    os.makedirs("config", exist_ok=True)
    with open("envshield.yml", "w") as f:
        f.write(
            "services:\n"
            "  app:\n"
            "    schema: env.schema.toml\n"
            "    local_file: config/settings.py\n"
            "    completeness: union\n"
            "manifests:\n"
            "  - file: docker-compose.yml\n"
            "    containers:\n"
            "      app: app\n"
        )
    with open(SCHEMA_FILE_NAME, "w") as f:
        f.write(schema_toml)
    with open("config/settings.py", "w") as f:
        f.write(python_local_content)
    with open("docker-compose.yml", "w") as f:
        f.write(compose_content)


def test_check_json_combined_key_is_absent_for_non_union_services(tmp_path):
    """
    Hard compatibility requirement (BL-030): a non-union invocation's JSON
    shape must be pixel-identical to before this feature existed --
    'combined' must not exist at all, not merely be empty. This is the same
    fixture as test_check_json_reports_clean_state, re-asserted here as an
    explicit BL-030 regression anchor in its own right.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _write_root_service()
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write('[API_KEY]\ndescription = "Test"\nsecret = true\n')
        with open(".env", "w") as f:
            f.write("API_KEY=abc123\n")

        result = runner.invoke(app, ["check", "--json"])

        payload = json.loads(result.stdout)
        assert "combined" not in payload


def test_check_json_combined_key_present_and_clean_for_a_satisfied_union(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _write_generic_union_service(
            schema_toml=(
                '[DB_HOST]\ndescription = "x"\n\n'
                '[FEATURE_MODE]\ndescription = "Compose-owned"\n'
            ),
            python_local_content='DB_HOST = "localhost"\n',
            compose_content=(
                'services:\n  app:\n    image: x\n    environment:\n      FEATURE_MODE: "on"\n'
            ),
        )

        result = runner.invoke(app, ["check", "--json"])

        assert result.exit_code == 0, result.stdout
        payload = json.loads(result.stdout)
        assert payload["success"] is True
        # Each source's own individual diff is still reported, unchanged --
        # completeness: union adds a verdict, it doesn't hide per-source
        # detail.
        assert len(payload["results"]) == 2
        assert payload["combined"]["app"] == {
            "clean": True,
            "missing": [],
            "blank": [],
            "invalid": {},
            "unresolved": [],
            "ambiguous_requiredif": {},
            "source_errors": [],
        }


def test_check_json_combined_key_reports_a_genuinely_missing_variable(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _write_generic_union_service(
            schema_toml='[GHOST]\ndescription = "satisfied nowhere"\n',
            python_local_content="",
            compose_content="services:\n  app:\n    image: x\n",
        )

        result = runner.invoke(app, ["check", "--json"])

        assert result.exit_code == 1
        payload = json.loads(result.stdout)
        assert payload["success"] is False
        assert payload["combined"]["app"]["missing"] == ["GHOST"]


def test_check_json_combined_key_reports_ambiguous_requiredif(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _write_generic_union_service(
            schema_toml=(
                '[TOGGLE]\ndescription = "x"\n\n'
                '[DEPENDENT]\ndescription = "x"\nsecret = true\n'
                'requiredIf = { var = "TOGGLE", equals = "true" }\n'
            ),
            python_local_content='TOGGLE = "false"\nDEPENDENT = "x"\n',
            compose_content=(
                'services:\n  app:\n    image: x\n    environment:\n      TOGGLE: "true"\n'
            ),
        )

        result = runner.invoke(app, ["check", "--json"])

        assert result.exit_code == 1
        payload = json.loads(result.stdout)
        assert payload["success"] is False
        assert "DEPENDENT" in payload["combined"]["app"]["ambiguous_requiredif"]


def test_check_json_combined_reports_false_clean_when_a_source_fails_to_load(tmp_path):
    """A malformed source must never be hidden by an otherwise-complete union (BL-030's explicit source-health requirement)."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _write_generic_union_service(
            schema_toml='[FEATURE_MODE]\ndescription = "fully covered by Compose alone"\n',
            python_local_content="this is not valid python (((\n",
            compose_content=(
                'services:\n  app:\n    image: x\n    environment:\n      FEATURE_MODE: "on"\n'
            ),
        )

        result = runner.invoke(app, ["check", "--json"])

        assert result.exit_code == 1
        payload = json.loads(result.stdout)
        assert payload["success"] is False
        combined = payload["combined"]["app"]
        assert combined["clean"] is False
        assert combined["source_errors"] != []


def test_check_rich_output_never_echoes_an_invalid_value(tmp_path):
    """
    Regression coverage for P0-3: 'Invalid Value' rows must describe the
    constraint, never the value that failed it -- for both a secret-flagged
    and an unflagged field, since the fix in schema_types.validate_value is
    unconditional, not secret-specific.
    """
    sentinel = "SUPER_SECRET_TEST_VALUE_12345"
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _write_root_service()
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write('[API_PORT]\ndescription = "x"\ntype = "port"\nsecret = true\n')
        with open(".env", "w") as f:
            f.write(f"API_PORT={sentinel}\n")

        result = runner.invoke(app, ["check"])

        assert result.exit_code == 1
        assert "Invalid Value" in result.stdout
        assert "must be a port number from 1-65535" in result.stdout
        assert sentinel not in result.stdout


def test_check_json_never_echoes_an_invalid_value(tmp_path):
    """Same invariant as the Rich-table test above, for the --json path."""
    sentinel = "SUPER_SECRET_TEST_VALUE_12345"
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _write_root_service()
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write('[API_PORT]\ndescription = "x"\ntype = "port"\nsecret = true\n')
        with open(".env", "w") as f:
            f.write(f"API_PORT={sentinel}\n")

        result = runner.invoke(app, ["check", "--json"])

        assert result.exit_code == 1
        # Assert on the raw serialized stdout, not just the parsed object --
        # proves the value is absent from the actual bytes written.
        assert sentinel not in result.stdout
        payload = json.loads(result.stdout)
        assert payload["results"][0]["invalid"] == {
            "API_PORT": "must be a port number from 1-65535"
        }


def test_check_json_is_valid_single_document_on_a_malformed_python_local_file(
    tmp_path,
):
    """
    BL-002 regression: a malformed Python local file used to print a
    'Warning: ...' line to stdout before the JSON document, corrupting it
    -- json.loads(stdout) must succeed regardless of schema shape.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _write_root_service()
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write('[API_KEY]\ndescription = "Test"\nsecret = true\n')
        with open("config.py", "w") as f:
            f.write("API_KEY = ")  # unterminated -- invalid syntax

        result = runner.invoke(app, ["check", "config.py", "--json"])

        assert result.exit_code == 1
        payload = json.loads(result.stdout)  # raises if stdout isn't one clean document
        assert payload["success"] is False
        assert payload["results"][0]["clean"] is False
        assert "error" in payload["results"][0]


def test_check_json_does_not_false_clean_on_a_malformed_python_local_file(tmp_path):
    """
    BL-002 regression, the false-clean half: with an all-optional/no-
    constraint schema, the old behavior silently treated the unparseable
    file as "declares nothing needed" and reported success. A parse
    failure must be reported as such regardless of whether the schema
    would otherwise have anything to flag as missing.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _write_root_service()
        # A genuinely empty schema -- the exact shape that previously
        # produced "success": true, "clean": true despite the local file
        # being unparseable.
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write("")
        with open("config.py", "w") as f:
            f.write("API_KEY = ")  # unterminated -- invalid syntax

        result = runner.invoke(app, ["check", "config.py", "--json"])

        assert result.exit_code == 1
        payload = json.loads(result.stdout)
        assert payload["success"] is False
        assert payload["results"][0]["clean"] is False


def test_doctor_json_is_valid_single_document_on_a_malformed_python_local_file(
    tmp_path,
):
    """BL-002 regression: same stdout-corruption bug, reproduced through 'doctor --json'."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.system("git init -q")
        config_manager.add_service("app", SCHEMA_FILE_NAME, local_file="config.py")
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write('[API_KEY]\ndescription = "Test"\nsecret = true\n')
        with open("config.py", "w") as f:
            f.write("API_KEY = ")  # unterminated -- invalid syntax

        result = runner.invoke(app, ["doctor", "--json"])

        payload = json.loads(result.stdout)  # raises if stdout isn't one clean document
        checks = payload["results"][0]["checks"]
        sync_check = next(c for c in checks if c["name"] == "Local Environment Sync")
        assert sync_check["passed"] is False


def test_check_json_with_multiple_services_runs_all_without_prompting(tmp_path):
    """Regression: --json must never fall into the interactive 'Which service?' picker."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _write_two_service_project()
        with open("alpha/.env", "w") as f:
            f.write("API_KEY=abc\n")
        with open("beta/.env", "w") as f:
            f.write("DB_URL=postgres://x\n")

        result = runner.invoke(app, ["check", "--json"])

        assert result.exit_code == 0, result.stdout
        payload = json.loads(result.stdout)
        services = {r["service"] for r in payload["results"]}
        assert services == {"alpha", "beta"}


def test_doctor_json_reports_structured_checks(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.system("git init -q")
        _write_root_service()
        with open("envshield.yml", "a") as f:
            f.write("project_name: test\n")
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write('[API_KEY]\ndescription = "Test"\nsecret = true\n')
        with open(".env", "w") as f:
            f.write("API_KEY=abc123\n")
        with open(".env.example", "w") as f:
            f.write("API_KEY=\n")

        result = runner.invoke(app, ["doctor", "--json"])

        payload = json.loads(result.stdout)
        assert payload["results"][0]["service"] == "app"
        names = {c["name"] for c in payload["results"][0]["checks"]}
        assert "Configuration Files" in names
        assert "Local Environment Sync" in names
        assert "[bold" not in result.stdout


def test_doctor_json_union_service_gets_source_health_and_aggregate_checks(tmp_path):
    """
    BL-030: a completeness: union service swaps "Local Environment Sync"/
    "Deployment Manifest" for narrower source-health checks plus one new
    aggregate "Registered-Source Completeness" check -- and, when the union
    is genuinely satisfied, every one of those checks (and the whole
    service) actually goes green, unlike today's per-source-only model.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.system("git init -q")
        os.makedirs("config", exist_ok=True)
        with open("envshield.yml", "w") as f:
            f.write(
                "services:\n"
                "  app:\n"
                "    schema: env.schema.toml\n"
                "    local_file: config/settings.py\n"
                "    completeness: union\n"
                "manifests:\n"
                "  - file: docker-compose.yml\n"
                "    containers:\n"
                "      app: app\n"
            )
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write(
                '[DB_HOST]\ndescription = "x"\n\n[FEATURE_MODE]\ndescription = "Compose-owned"\n'
            )
        with open("config/settings.py", "w") as f:
            f.write('DB_HOST = "localhost"\n')
        with open("docker-compose.yml", "w") as f:
            f.write(
                'services:\n  app:\n    image: x\n    environment:\n      FEATURE_MODE: "on"\n'
            )

        result = runner.invoke(app, ["doctor", "--json"])

        payload = json.loads(result.stdout)
        checks = {c["name"]: c for c in payload["results"][0]["checks"]}
        assert "Local Source Health" in checks
        assert "Local Environment Sync" not in checks
        assert "Deployment Manifest Source Health" in checks
        assert "Deployment Manifest" not in checks
        assert "Registered-Source Completeness" in checks
        assert checks["Local Source Health"]["passed"] is True
        assert checks["Deployment Manifest Source Health"]["passed"] is True
        assert checks["Registered-Source Completeness"]["passed"] is True


def test_doctor_json_non_union_service_keeps_todays_checks_unchanged(tmp_path):
    """
    Regression anchor (BL-030 compatibility requirement): a service without
    completeness: union must keep exactly today's checks -- same names,
    same per-source-must-be-self-sufficient semantics.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.system("git init -q")
        os.makedirs("config", exist_ok=True)
        with open("envshield.yml", "w") as f:
            f.write(
                "services:\n"
                "  app:\n"
                "    schema: env.schema.toml\n"
                "    local_file: config/settings.py\n"
                "manifests:\n"
                "  - file: docker-compose.yml\n"
                "    containers:\n"
                "      app: app\n"
            )
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write(
                '[DB_HOST]\ndescription = "x"\n\n[FEATURE_MODE]\ndescription = "Compose-owned"\n'
            )
        with open("config/settings.py", "w") as f:
            f.write('DB_HOST = "localhost"\n')
        with open("docker-compose.yml", "w") as f:
            f.write(
                'services:\n  app:\n    image: x\n    environment:\n      FEATURE_MODE: "on"\n'
            )

        result = runner.invoke(app, ["doctor", "--json"])

        payload = json.loads(result.stdout)
        checks = {c["name"]: c for c in payload["results"][0]["checks"]}
        assert "Local Environment Sync" in checks
        assert "Deployment Manifest" in checks
        assert "Registered-Source Completeness" not in checks
        # Neither source alone satisfies the whole schema -- today's model
        # correctly still reports both as failing, unaffected by BL-030.
        assert checks["Local Environment Sync"]["passed"] is False
        assert checks["Deployment Manifest"]["passed"] is False


def test_doctor_json_union_source_error_remains_visible_and_fails_completeness(
    tmp_path,
):
    """A malformed source must remain an independently visible failure in union mode, and must also fail the aggregate completeness check (BL-030's explicit source-health requirement)."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.system("git init -q")
        os.makedirs("config", exist_ok=True)
        with open("envshield.yml", "w") as f:
            f.write(
                "services:\n"
                "  app:\n"
                "    schema: env.schema.toml\n"
                "    local_file: config/settings.py\n"
                "    completeness: union\n"
                "manifests:\n"
                "  - file: docker-compose.yml\n"
                "    containers:\n"
                "      app: app\n"
            )
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write('[FEATURE_MODE]\ndescription = "fully covered by Compose alone"\n')
        with open("config/settings.py", "w") as f:
            f.write("this is not valid python (((\n")
        with open("docker-compose.yml", "w") as f:
            f.write(
                'services:\n  app:\n    image: x\n    environment:\n      FEATURE_MODE: "on"\n'
            )

        result = runner.invoke(app, ["doctor", "--json"])

        payload = json.loads(result.stdout)
        checks = {c["name"]: c for c in payload["results"][0]["checks"]}
        assert checks["Local Source Health"]["passed"] is False
        assert checks["Registered-Source Completeness"]["passed"] is False
        assert payload["results"][0]["passed"] is False


def test_doctor_reports_legacy_path_key(tmp_path):
    """
    A pre-4.5.0 'path:' key (renamed to 'schema:' in 4.5.0, no shim) must be
    flagged explicitly, not silently treated as if the service didn't exist.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.system("git init -q")
        with open(CONFIG_FILE_NAME, "w") as f:
            f.write("services:\n  api:\n    path: api/env.schema.toml\n")

        result = runner.invoke(app, ["doctor", "--json"])

        payload = json.loads(result.stdout)
        checks = {c["name"]: c for c in payload["results"][0]["checks"]}
        assert "Legacy Configuration Keys" in checks
        assert checks["Legacy Configuration Keys"]["passed"] is False
        assert "'path:' key" in checks["Legacy Configuration Keys"]["message"]


def test_doctor_reports_legacy_deployment_manifest_key(tmp_path):
    """
    A pre-4.2.0 per-service 'deployment_manifest:' key is never read by
    get_deployment_manifests (which only reads the top-level 'manifests:'
    list) -- manifest validation for that service silently never runs.
    doctor must surface this instead of staying silent.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.system("git init -q")
        os.makedirs("api", exist_ok=True)
        with open(CONFIG_FILE_NAME, "w") as f:
            f.write(
                "services:\n"
                "  api:\n"
                "    schema: api/env.schema.toml\n"
                "    deployment_manifest: docker-compose.yml\n"
            )
        with open("api/env.schema.toml", "w") as f:
            f.write('[API_KEY]\ndescription = "Test"\n')

        result = runner.invoke(app, ["doctor", "--json", "--service", "api"])

        payload = json.loads(result.stdout)
        checks = {c["name"]: c for c in payload["results"][0]["checks"]}
        assert "Legacy Configuration Keys" in checks
        assert checks["Legacy Configuration Keys"]["passed"] is False
        assert "deployment_manifest" in checks["Legacy Configuration Keys"]["message"]


def test_doctor_reports_a_manifests_key_nested_under_a_service(tmp_path):
    """
    Regression: 'manifests:' is only ever read as a TOP-LEVEL list
    (config_manager.get_deployment_manifests) -- nesting it under a
    service (a natural but wrong guess, since 'schema:'/'local_file:'
    genuinely are per-service keys) was silently never read: no error, no
    manifest validated, and 'explain'/'doctor' dropped the whole
    deployment-manifest section with no warning at all. Deployment-
    manifest correctness is the pillar the charter treats as carrying the
    most differentiation weight, so a silent failure here is exactly what
    doctor's legacy-key detection exists to prevent.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.system("git init -q")
        os.makedirs("api", exist_ok=True)
        with open(CONFIG_FILE_NAME, "w") as f:
            f.write(
                "services:\n"
                "  api:\n"
                "    schema: api/env.schema.toml\n"
                "    manifests:\n"
                "      - file: docker-compose.yml\n"
                "        container: api\n"
            )
        with open("api/env.schema.toml", "w") as f:
            f.write('[API_KEY]\ndescription = "Test"\n')

        result = runner.invoke(app, ["doctor", "--json", "--service", "api"])

        payload = json.loads(result.stdout)
        checks = {c["name"]: c for c in payload["results"][0]["checks"]}
        assert "Legacy Configuration Keys" in checks
        assert checks["Legacy Configuration Keys"]["passed"] is False
        assert "manifests" in checks["Legacy Configuration Keys"]["message"]
        assert "TOP-LEVEL" in checks["Legacy Configuration Keys"]["message"]


def test_doctor_omits_legacy_check_for_a_correctly_placed_top_level_manifest(tmp_path):
    """A correctly-placed top-level 'manifests:' list must not trip the same check."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.system("git init -q")
        os.makedirs("api", exist_ok=True)
        with open(CONFIG_FILE_NAME, "w") as f:
            f.write(
                "services:\n"
                "  api:\n"
                "    schema: api/env.schema.toml\n"
                "manifests:\n"
                "  - file: docker-compose.yml\n"
                "    containers:\n"
                "      api: api\n"
            )
        with open("api/env.schema.toml", "w") as f:
            f.write('[API_KEY]\ndescription = "Test"\n')
        with open("docker-compose.yml", "w") as f:
            f.write("services:\n  api:\n    environment:\n      - API_KEY=x\n")

        result = runner.invoke(app, ["doctor", "--json", "--service", "api"])

        payload = json.loads(result.stdout)
        checks = {c["name"]: c for c in payload["results"][0]["checks"]}
        assert "Legacy Configuration Keys" not in checks
        assert "Deployment Manifest" in checks


def test_doctor_omits_legacy_configuration_keys_check_when_not_applicable(tmp_path):
    """
    Regression: 'Legacy Configuration Keys' used to run unconditionally on
    every project, printing a permanent, always-green, identical line for
    the overwhelming majority of projects that never used the pre-4.2.0/
    4.5.0 key names at all. It should only appear when actually relevant --
    see test_doctor_reports_legacy_path_key/deployment_manifest_key above
    for the case where it does.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.system("git init -q")
        with open(CONFIG_FILE_NAME, "w") as f:
            f.write("services:\n  api:\n    schema: api/env.schema.toml\n")
        os.makedirs("api", exist_ok=True)
        with open("api/env.schema.toml", "w") as f:
            f.write('[API_KEY]\ndescription = "Test"\n')

        result = runner.invoke(app, ["doctor", "--json", "--service", "api"])

        payload = json.loads(result.stdout)
        checks = {c["name"]: c for c in payload["results"][0]["checks"]}
        assert "Legacy Configuration Keys" not in checks


def test_doctor_json_and_fix_together_is_rejected(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        result = runner.invoke(app, ["doctor", "--json", "--fix"])

        assert result.exit_code == 1
        assert "cannot be used together" in result.stdout


def test_scan_json_reports_findings(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        with open("leak.py", "w") as f:
            f.write('STRIPE_SECRET = "sk_live_123456789abcdefghijk"\n')

        result = runner.invoke(app, ["scan", ".", "--json"])

        assert result.exit_code == 1
        payload = json.loads(result.stdout)
        assert payload["clean"] is False
        assert len(payload["secrets"]) == 1
        assert payload["secrets"][0]["secret_type"] == "Generic API Key"
        assert "[bold" not in result.stdout


def test_scan_json_reports_clean_state(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        with open("clean.py", "w") as f:
            f.write("x = 1\n")

        result = runner.invoke(app, ["scan", ".", "--json"])

        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload == {
            "clean": True,
            "secrets": [],
            "undeclared_variables": [],
            "skipped_files": [],
            "complete": True,
        }
