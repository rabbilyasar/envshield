# envshield/tests/core/test_scanner_compliance.py
import os

from typer.testing import CliRunner

from envshield.cli import app
from envshield.config.manager import SCHEMA_FILE_NAME

runner = CliRunner()


def test_scan_with_undeclared_variable(mocker, tmp_path):
    """
    Tests that the scan command correctly identifies a variable used in code
    but not declared in the schema.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write('[DECLARED_KEY]\ndescription="This one is okay"\n')
        mocker.patch(
            "envshield.config.manager.load_schema", return_value={"DECLARED_KEY": {}}
        )

        python_code = "import os\n\nAPI_KEY = os.environ.get('UNDECLARED_KEY')\n"
        with open("app.py", "w") as f:
            f.write(python_code)

        result = runner.invoke(app, ["scan"])

        assert result.exit_code == 1, (
            "Scan should fail if undeclared variables are found"
        )
        assert "Found 1 undeclared variable(s)!" in result.stdout
        assert "UNDECLARED_KEY" in result.stdout


def test_scan_with_only_declared_variables(mocker, tmp_path):
    """Tests that the scan command passes when all variables are declared in the schema."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write('[DECLARED_KEY]\ndescription="This one is okay"\n')
        mocker.patch(
            "envshield.config.manager.load_schema", return_value={"DECLARED_KEY": {}}
        )

        python_code = "import os\n\nAPI_KEY = os.environ.get('DECLARED_KEY')\n"
        with open("app.py", "w") as f:
            f.write(python_code)

        result = runner.invoke(app, ["scan"])

        assert result.exit_code == 0, "Scan should pass when code is compliant"
        assert "No issues found" in result.stdout


def test_scan_with_both_secret_and_undeclared_variable(mocker, tmp_path):
    """
    Edge Case: Tests that the scanner correctly reports both hardcoded secrets
    and undeclared variables in a single run.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write('[DECLARED_KEY]\ndescription="This one is okay"\n')
        mocker.patch(
            "envshield.config.manager.load_schema", return_value={"DECLARED_KEY": {}}
        )

        python_code = "import os\n\nSECRET = 'sk_live_123456789abcdefghijklmnopqrstuv'\nUNDECLARED = os.environ.get('UNDECLARED_KEY')\n"
        with open("app.py", "w") as f:
            f.write(python_code)

        result = runner.invoke(app, ["scan"])

        assert result.exit_code == 1, "Scan should fail if any issue is found"
        assert "DANGER: Found 1 potential secret(s)!" in result.stdout
        assert "WARNING: Found 1 undeclared variable(s)!" in result.stdout
        # Fix: Assert on the key content, not the full table rendering, which is brittle.
        assert "sk_live_123456789" in result.stdout
        assert "UNDECLARED_KEY" in result.stdout


def test_scan_ignores_dependency_and_vcs_dirs_by_default(tmp_path):
    """
    Regression test: `scan` used to walk into node_modules/.venv/.git with no
    default excludes, producing false-positive noise on any real project.
    These dirs must now be pruned even with no envshield.yml exclusions configured.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.makedirs("node_modules/some-pkg")
        with open("node_modules/some-pkg/config.js", "w") as f:
            f.write("const key = 'sk_live_123456789abcdefghijklmnopqrstuv';\n")

        os.makedirs(".venv/lib")
        with open(".venv/lib/leftover.py", "w") as f:
            f.write("TOKEN = 'ghp_123456789012345678901234567890123456'\n")

        result = runner.invoke(app, ["scan"])

        assert result.exit_code == 0
        assert "No issues found" in result.stdout


def test_scan_staged_scans_index_content_not_working_tree(tmp_path):
    """
    Regression (critical): the pre-commit hook (`scan --staged`) must scan
    what's actually staged in the Git index, not the working-tree copy on
    disk. Previously it read the file straight off disk, so staging a secret
    and then editing it out *without re-staging* would let the commit
    through, even though the secret is still exactly what gets committed.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.system("git init -q")
        os.system('git config user.email "test@example.com"')
        os.system('git config user.name "Test"')

        with open("config.py", "w") as f:
            f.write("STRIPE_KEY = 'sk_live_123456789abcdefghijklmnopqrstuv'\n")
        os.system("git add config.py")

        # Clean up the secret on disk *without* re-staging the change.
        with open("config.py", "w") as f:
            f.write("STRIPE_KEY = os.environ['STRIPE_KEY']\n")

        result = runner.invoke(app, ["scan", "--staged"])

        assert result.exit_code == 1, (
            "The staged (committed) content still has the secret, even though the working-tree copy was cleaned up"
        )
        assert "DANGER" in result.stdout


def test_scan_staged_does_not_flag_secret_removed_before_staging(tmp_path):
    """Sanity check: a secret staged and then genuinely fixed *and re-staged* must not be flagged."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.system("git init -q")
        os.system('git config user.email "test@example.com"')
        os.system('git config user.name "Test"')

        with open("config.py", "w") as f:
            f.write("STRIPE_KEY = 'sk_live_123456789abcdefghijklmnopqrstuv'\n")
        os.system("git add config.py")

        with open("config.py", "w") as f:
            f.write("STRIPE_KEY = os.environ['STRIPE_KEY']\n")
        os.system("git add config.py")

        result = runner.invoke(app, ["scan", "--staged"])

        assert result.exit_code == 0
        assert "No issues found" in result.stdout


def test_scan_without_service_resolves_schema_per_service_directory(tmp_path):
    """
    Regression: on a multi-service project, `scan` without --service used to
    look for a single root 'env.schema.toml' that was never there (each
    service has its own), so it silently gave up on the undeclared-variable
    check entirely -- flagging nothing, including genuinely undeclared vars.
    This is exactly the mode the pre-commit hook runs in (`scan --staged`,
    no --service), so it was doing nothing useful for a project like this
    at all. Each service's own directory must now be checked against that
    service's own schema.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.makedirs("athena")
        os.makedirs("hermes")
        with open("envshield.yml", "w") as f:
            f.write(
                "services:\n  athena:\n    path: athena/env.schema.toml\n  hermes:\n    path: hermes/env.schema.toml\n"
            )
        with open("athena/env.schema.toml", "w") as f:
            f.write('[ATHENA_VAR]\ndescription="x"\n')
        with open("hermes/env.schema.toml", "w") as f:
            f.write('[HERMES_VAR]\ndescription="x"\n')

        with open("athena/app.py", "w") as f:
            f.write(
                "import os\n\n"
                "a = os.environ.get('ATHENA_VAR')\n"  # declared in athena's own schema
                "b = os.environ.get('ATHENA_UNDECLARED')\n"  # genuinely undeclared
            )
        with open("hermes/app.py", "w") as f:
            f.write(
                "import os\n\nc = os.environ.get('HERMES_VAR')\n"  # declared in hermes's own schema
            )

        result = runner.invoke(app, ["scan"])

        assert result.exit_code == 1
        assert "Found 1 undeclared variable(s)!" in result.stdout
        assert "ATHENA_UNDECLARED" in result.stdout
        # Declared-in-its-own-service vars must not be flagged just because
        # they aren't in the *other* service's schema.
        assert "ATHENA_VAR" not in result.stdout.split("Undeclared Variable Usage")[-1]
        assert "HERMES_VAR" not in result.stdout.split("Undeclared Variable Usage")[-1]


def test_scan_with_explicit_service_still_checks_a_single_schema_for_every_file(
    tmp_path,
):
    """Passing --service explicitly keeps the original single-target behavior, even on a multi-service project."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.makedirs("athena")
        os.makedirs("hermes")
        with open("envshield.yml", "w") as f:
            f.write(
                "services:\n  athena:\n    path: athena/env.schema.toml\n  hermes:\n    path: hermes/env.schema.toml\n"
            )
        with open("athena/env.schema.toml", "w") as f:
            f.write('[ATHENA_VAR]\ndescription="x"\n')
        with open("hermes/env.schema.toml", "w") as f:
            f.write('[HERMES_VAR]\ndescription="x"\n')
        with open("hermes/app.py", "w") as f:
            f.write("import os\n\nc = os.environ.get('HERMES_VAR')\n")

        result = runner.invoke(app, ["scan", "hermes", "--service", "athena"])

        assert result.exit_code == 1
        assert "HERMES_VAR" in result.stdout


def test_scan_detects_unquoted_dotenv_style_secret(tmp_path):
    """
    Regression: the generic secret pattern used to require quotes around the
    value (Python/JSON style), so it was blind to plain, unquoted
    'KEY=value' assignments -- the conventional .env format this tool exists
    to protect, and the format every one of these values would actually be
    committed in.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        with open(".env", "w") as f:
            f.write("DATABASE_PASSWORD=SuperSecretProdPassw0rd\n")

        result = runner.invoke(app, ["scan"])

        assert result.exit_code == 1
        assert "DANGER: Found 1 potential secret(s)!" in result.stdout


def test_scan_detects_unquoted_aws_secret_key(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        with open(".env", "w") as f:
            f.write("AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY\n")

        result = runner.invoke(app, ["scan"])

        assert result.exit_code == 1
        assert "DANGER: Found 1 potential secret(s)!" in result.stdout


def test_scan_reports_skipped_large_files(tmp_path):
    """
    Regression: files over 1MB were silently skipped with zero warning --
    coverage was incomplete and nobody was told, so a real secret padded
    past the size threshold would sail through unnoticed.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        with open("big.env", "w") as f:
            f.write("PADDING=" + ("a" * 1_000_010) + "\n")

        result = runner.invoke(app, ["scan"])

        assert result.exit_code == 0
        assert "Skipped 1 file(s) over 1MB" in result.stdout
        assert "big.env" in result.stdout


def test_scan_gracefully_handles_missing_schema_file(tmp_path):
    """
    Edge Case: Tests that the scanner finds undeclared variables and fails,
    even if the schema is missing.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        python_code = "import os\n\nAPI_KEY = os.environ.get('SOME_KEY')\n"
        with open("app.py", "w") as f:
            f.write(python_code)

        result = runner.invoke(app, ["scan"])

        # When no schema exists, ALL variables are considered undeclared.
        assert result.exit_code == 1, (
            "Scan should fail if undeclared variables are found"
        )
        assert "Warning: Schema not found" in result.stdout
        assert "Found 1 undeclared variable(s)!" in result.stdout
        assert "SOME_KEY" in result.stdout
