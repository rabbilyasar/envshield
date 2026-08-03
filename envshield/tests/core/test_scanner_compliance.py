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

        python_code = (
            "import os\n\n"
            "SECRET = 'sk_live_123456789abcdefghijklmnopqrstuv'\n"
            "UNDECLARED = os.environ.get('UNDECLARED_KEY')\n"
        )
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
            "The staged (committed) content still has the secret, even "
            "though the working-tree copy was cleaned up"
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
