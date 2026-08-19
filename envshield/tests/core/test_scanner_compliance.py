# envshield/tests/core/test_scanner_compliance.py
import json
import os
import re
import subprocess

import pytest
from typer.testing import CliRunner

from envshield.cli import app
from envshield.config import manager as config_manager
from envshield.config.manager import SCHEMA_FILE_NAME
from envshield.core import scanner

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


def test_scan_reports_consistent_relative_paths_regardless_of_argument_form(
    mocker, tmp_path
):
    """
    Regression: scanning '.' walked directories and reported a './'-
    prefixed relative path, while scanning the single file 'app.py'
    directly reported an absolute path -- two different-looking paths for
    the same file, in the same tool. Both invocations should report the
    same plain, cwd-relative path ('app.py'), matching 'undeclared's own
    already-consistent style.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write('[DECLARED_KEY]\ndescription="ok"\n')
        mocker.patch(
            "envshield.config.manager.load_schema", return_value={"DECLARED_KEY": {}}
        )
        with open("app.py", "w") as f:
            f.write("import os\n\nAPI_KEY = os.environ.get('UNDECLARED_KEY')\n")

        result_dir = runner.invoke(app, ["scan", "."])
        assert "app.py" in result_dir.stdout
        assert "./app.py" not in result_dir.stdout
        assert os.getcwd() not in result_dir.stdout

        result_file = runner.invoke(app, ["scan", "app.py"])
        assert "app.py" in result_file.stdout
        assert os.getcwd() not in result_file.stdout


def test_scan_with_only_declared_variables(mocker, tmp_path):
    """Tests that the scan command passes when all variables are declared in the schema."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        config_manager.add_service("app", SCHEMA_FILE_NAME)
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
        # Security invariant: the finding identifies itself (a redacted
        # preview), but the secret's own value must never reach output. See
        # TestSecretValueNeverLeaksToOutput for the dedicated regression
        # coverage of this invariant.
        assert "sk_live_123456789abcdefghijklmnopqrstuv" not in result.stdout
        assert "redacted" in result.stdout.lower()
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


def test_scan_skips_a_gitignored_env_file(tmp_path):
    """
    Real friction: a plain `envshield scan` flagged the developer's own
    '.env' as "DANGER" -- but '.env' is gitignored by convention (and by
    'scan's own suggestion text, which tells you to move secrets INTO it),
    so it will never actually be committed. Flagging it is pure noise
    against what 'scan' exists to prevent. Git-ignored files must be
    skipped in the default (non-'--staged') scan.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.system("git init -q")
        with open(".gitignore", "w") as f:
            f.write(".env\n")
        with open(".env", "w") as f:
            f.write("SECRET_KEY=sk_live_123456789abcdefghijklmnopqrstuv\n")

        result = runner.invoke(app, ["scan"])

        assert result.exit_code == 0, result.stdout
        assert "No issues found" in result.stdout
        assert "Skipping" in result.stdout
        assert ".env" in result.stdout


def test_scan_staged_still_flags_a_gitignored_file_that_got_force_staged(tmp_path):
    """
    The gitignore skip is only for the default (working-tree) scan, where
    an ignored file genuinely won't be committed. '--staged' scans exactly
    what's in the index -- if an ignored file got force-added anyway,
    that's a real, imminent risk and must still be flagged, not silenced.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.system("git init -q")
        os.system('git config user.email "test@example.com"')
        os.system('git config user.name "Test"')
        with open(".gitignore", "w") as f:
            f.write(".env\n")
        with open(".env", "w") as f:
            f.write("SECRET_KEY=sk_live_123456789abcdefghijklmnopqrstuv\n")
        os.system("git add -f .env")

        result = runner.invoke(app, ["scan", "--staged"])

        assert result.exit_code == 1
        assert "DANGER" in result.stdout


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
            # Not an os.environ/os.getenv read on purpose -- Phase 2B's AST
            # discovery would (correctly) flag that as a genuinely
            # undeclared variable in this schema-less test, which isn't
            # what this test is about.
            f.write("# secret removed\n")
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
        os.makedirs("alpha")
        os.makedirs("beta")
        with open("envshield.yml", "w") as f:
            f.write(
                "services:\n  alpha:\n    schema: alpha/env.schema.toml\n  beta:\n    schema: beta/env.schema.toml\n"
            )
        with open("alpha/env.schema.toml", "w") as f:
            f.write('[ALPHA_VAR]\ndescription="x"\n')
        with open("beta/env.schema.toml", "w") as f:
            f.write('[BETA_VAR]\ndescription="x"\n')

        with open("alpha/app.py", "w") as f:
            f.write(
                "import os\n\n"
                "a = os.environ.get('ALPHA_VAR')\n"  # declared in alpha's own schema
                "b = os.environ.get('ALPHA_UNDECLARED')\n"  # genuinely undeclared
            )
        with open("beta/app.py", "w") as f:
            f.write(
                "import os\n\nc = os.environ.get('BETA_VAR')\n"  # declared in beta's own schema
            )

        result = runner.invoke(app, ["scan"])

        assert result.exit_code == 1
        assert "Found 1 undeclared variable(s)!" in result.stdout
        assert "ALPHA_UNDECLARED" in result.stdout
        # Declared-in-its-own-service vars must not be flagged just because
        # they aren't in the *other* service's schema.
        assert "ALPHA_VAR" not in result.stdout.split("Undeclared Variable Usage")[-1]
        assert "BETA_VAR" not in result.stdout.split("Undeclared Variable Usage")[-1]


def test_scan_without_service_skips_a_broken_schema_instead_of_crashing_entirely(
    tmp_path,
):
    """
    Real incident this reproduces: a malformed env.schema.toml in one
    service (mid-edit, completely unrelated to what's staged) crashed
    'scan --staged' outright with an uncaught parse error -- blocking
    every commit in the whole project, including ones that never touched
    the broken service at all. A broken schema in one service must only
    degrade that service's own undeclared-variable check, not take down
    scanning for every other service.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.makedirs("alpha")
        os.makedirs("beta")
        with open("envshield.yml", "w") as f:
            f.write(
                "services:\n  alpha:\n    schema: alpha/env.schema.toml\n  beta:\n    schema: beta/env.schema.toml\n"
            )
        with open("alpha/env.schema.toml", "w") as f:
            f.write('[ALPHA_VAR]\ndescription = "unterminated\n')  # malformed TOML
        with open("beta/env.schema.toml", "w") as f:
            f.write('[BETA_VAR]\ndescription="x"\n')
        with open("beta/app.py", "w") as f:
            f.write("import os\n\nc = os.environ.get('BETA_VAR')\n")

        result = runner.invoke(app, ["scan"])

        assert result.exit_code == 0, result.stdout
        assert "Could not load schema for service 'alpha'" in result.stdout
        assert "No issues found" in result.stdout


def test_scan_with_explicit_service_still_checks_a_single_schema_for_every_file(
    tmp_path,
):
    """Passing --service explicitly keeps the original single-target behavior, even on a multi-service project."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.makedirs("alpha")
        os.makedirs("beta")
        with open("envshield.yml", "w") as f:
            f.write(
                "services:\n  alpha:\n    schema: alpha/env.schema.toml\n  beta:\n    schema: beta/env.schema.toml\n"
            )
        with open("alpha/env.schema.toml", "w") as f:
            f.write('[ALPHA_VAR]\ndescription="x"\n')
        with open("beta/env.schema.toml", "w") as f:
            f.write('[BETA_VAR]\ndescription="x"\n')
        with open("beta/app.py", "w") as f:
            f.write("import os\n\nc = os.environ.get('BETA_VAR')\n")

        result = runner.invoke(app, ["scan", "beta", "--service", "alpha"])

        assert result.exit_code == 1
        assert "BETA_VAR" in result.stdout


def test_scan_with_service_and_no_path_defaults_to_that_services_own_directory(
    tmp_path,
):
    """
    Regression: 'scan --service X' with no path argument defaulted to
    scanning the whole project (paths or ["."]) while still checking every
    file against only X's schema -- so on a multi-service project, every
    OTHER service's genuinely-declared variables got flagged as
    "undeclared" against X's schema. This is the natural, documented
    invocation ("scan just my service"), not an edge case: the default
    path must now be scoped to X's own directory, not the whole project.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.makedirs("alpha")
        os.makedirs("beta")
        with open("envshield.yml", "w") as f:
            f.write(
                "services:\n  alpha:\n    schema: alpha/env.schema.toml\n  beta:\n    schema: beta/env.schema.toml\n"
            )
        with open("alpha/env.schema.toml", "w") as f:
            f.write('[ALPHA_VAR]\ndescription="x"\n')
        with open("beta/env.schema.toml", "w") as f:
            f.write('[BETA_VAR]\ndescription="x"\n')
        with open("alpha/app.py", "w") as f:
            f.write("import os\n\na = os.environ.get('ALPHA_VAR')\n")
        with open("beta/app.py", "w") as f:
            # Declared in beta's OWN schema -- must never be flagged just
            # because a bare '--service alpha' run (no path) used to sweep
            # in every file project-wide.
            f.write("import os\n\nb = os.environ.get('BETA_VAR')\n")

        result = runner.invoke(app, ["scan", "--service", "alpha"])

        assert result.exit_code == 0, result.stdout
        assert "No issues found" in result.stdout
        assert "BETA_VAR" not in result.stdout


def test_scan_with_service_and_explicit_path_outside_its_directory_is_unchanged(
    tmp_path,
):
    """
    The default-path scoping above must not override a caller's own
    explicit path/file argument -- deliberately checking one specific file
    against a named service's schema (e.g. a shared file, on purpose) is
    existing, intentional behavior and must keep working exactly as before.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.makedirs("alpha")
        os.makedirs("beta")
        with open("envshield.yml", "w") as f:
            f.write(
                "services:\n  alpha:\n    schema: alpha/env.schema.toml\n  beta:\n    schema: beta/env.schema.toml\n"
            )
        with open("alpha/env.schema.toml", "w") as f:
            f.write('[ALPHA_VAR]\ndescription="x"\n')
        with open("beta/env.schema.toml", "w") as f:
            f.write('[BETA_VAR]\ndescription="x"\n')
        with open("beta/app.py", "w") as f:
            f.write("import os\n\nc = os.environ.get('BETA_VAR')\n")

        result = runner.invoke(app, ["scan", "beta", "--service", "alpha"])

        assert result.exit_code == 1
        assert "BETA_VAR" in result.stdout


def test_scan_staged_with_service_does_not_leak_another_services_staged_file(
    tmp_path,
):
    """
    Same root cause, '--staged' variant: '--staged' always collects every
    staged file project-wide (paths is never consulted for it), so
    '--staged --service X' used to check every OTHER service's staged
    files against X's schema too. A staged change belonging to a different
    service must not be attributed to X.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        subprocess.run(["git", "init", "-q"], check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], check=True)
        subprocess.run(["git", "config", "user.name", "Test"], check=True)
        os.makedirs("alpha")
        os.makedirs("beta")
        with open("envshield.yml", "w") as f:
            f.write(
                "services:\n  alpha:\n    schema: alpha/env.schema.toml\n  beta:\n    schema: beta/env.schema.toml\n"
            )
        with open("alpha/env.schema.toml", "w") as f:
            f.write('[ALPHA_VAR]\ndescription="x"\n')
        with open("beta/env.schema.toml", "w") as f:
            f.write('[BETA_VAR]\ndescription="x"\n')
        with open("beta/app.py", "w") as f:
            f.write("import os\n\nb = os.environ.get('BETA_VAR')\n")
        subprocess.run(["git", "add", "-A"], check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], check=True)

        # Stage a genuinely undeclared var in alpha's own file...
        with open("alpha/app.py", "w") as f:
            f.write("import os\n\na = os.environ.get('ALPHA_UNDECLARED')\n")
        # ...and an unrelated, already-declared edit in beta's file.
        with open("beta/app.py", "w") as f:
            f.write("import os\n\nb = os.environ.get('BETA_VAR')\n# unrelated edit\n")
        subprocess.run(["git", "add", "-A"], check=True)

        result_beta = runner.invoke(app, ["scan", "--staged", "--service", "beta"])
        assert result_beta.exit_code == 0, result_beta.stdout
        assert "ALPHA_UNDECLARED" not in result_beta.stdout

        result_alpha = runner.invoke(app, ["scan", "--staged", "--service", "alpha"])
        assert result_alpha.exit_code == 1
        assert "ALPHA_UNDECLARED" in result_alpha.stdout


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
    even if the project hasn't been initialized (no envshield.yml, so no
    schema to check against) at all.
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
        assert "Warning: No services configured" in result.stdout
        assert "Found 1 undeclared variable(s)!" in result.stdout
        assert "SOME_KEY" in result.stdout


def test_scan_rejects_a_nonexistent_path_instead_of_reporting_clean(tmp_path):
    """
    Real bug: 'scan nonexi.py' (a typo'd path) silently scanned zero files
    and reported "No issues found -- your configuration is secure and
    compliant!" -- indistinguishable from an actual clean scan of real
    files. A typo'd path must fail loudly instead.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        result = runner.invoke(app, ["scan", "nonexistent.py"])

        assert result.exit_code == 1
        assert "not found" in result.stdout.lower()
        assert "No issues found" not in result.stdout


def test_scan_json_rejects_a_nonexistent_path_instead_of_reporting_clean(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        result = runner.invoke(app, ["scan", "nonexistent.py", "--json"])

        assert result.exit_code == 1
        payload = json.loads(result.stdout)
        assert payload["clean"] is False
        assert "not found" in payload["error"].lower()


class TestSymlinkNeverFollowed:
    """
    Regression coverage for the security invariant behind P0-2: a scan must
    never read content from outside the directory tree it was asked to
    scan. A symlink is the mechanism that can violate this, and it can be
    reached three ways -- all three are covered here rather than only the
    single case the original report named, since they're the same
    vulnerability via three entry points into the same file-collection
    code: a symlinked file discovered while walking a real directory, a
    symlinked directory passed directly as the scan path, and a symlinked
    file passed directly as the scan path.
    """

    OUTSIDE_SECRET = "AKIAABCDEFGHIJKLMNOP"

    def _make_outside_secret_file(self, tmp_path, name="outside_secret.env"):
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir(exist_ok=True)
        target = outside_dir / name
        target.write_text(f"AWS_ACCESS_KEY_ID={self.OUTSIDE_SECRET}\n")
        return target

    def test_symlinked_file_inside_a_walked_directory_is_not_read(self, tmp_path):
        target = self._make_outside_secret_file(tmp_path)
        with runner.isolated_filesystem(temp_dir=tmp_path):
            os.makedirs("project/subdir")
            os.symlink(target, "project/subdir/.env")

            result = runner.invoke(app, ["scan", "project"])

            assert result.exit_code == 0, result.stdout
            assert "No issues found" in result.stdout
            assert self.OUTSIDE_SECRET not in result.stdout
            assert "Skipping 1 symlink" in result.stdout
            assert "subdir" in result.stdout

    def test_symlinked_directory_passed_directly_is_not_walked(self, tmp_path):
        outside_dir = tmp_path / "outside_dir"
        outside_dir.mkdir()
        (outside_dir / "a.env").write_text(f"AWS_ACCESS_KEY_ID={self.OUTSIDE_SECRET}\n")
        with runner.isolated_filesystem(temp_dir=tmp_path):
            os.symlink(outside_dir, "linked_dir")

            result = runner.invoke(app, ["scan", "linked_dir"])

            assert result.exit_code == 0, result.stdout
            assert "No issues found" in result.stdout
            assert self.OUTSIDE_SECRET not in result.stdout
            assert "Skipping 1 symlink" in result.stdout

    def test_symlinked_file_passed_directly_is_not_read(self, tmp_path):
        target = self._make_outside_secret_file(tmp_path)
        with runner.isolated_filesystem(temp_dir=tmp_path):
            os.symlink(target, "linked.env")

            result = runner.invoke(app, ["scan", "linked.env"])

            assert result.exit_code == 0, result.stdout
            assert "No issues found" in result.stdout
            assert self.OUTSIDE_SECRET not in result.stdout
            assert "Skipping 1 symlink" in result.stdout

    def test_skip_message_names_the_skipped_path(self, tmp_path):
        """
        Matches this file's own existing convention for the git-ignored-file
        skip: a skip must be visible and identify what was skipped, not a
        silent, unexplained drop in coverage that reads identically to
        "nothing was there to find."
        """
        target = self._make_outside_secret_file(tmp_path)
        with runner.isolated_filesystem(temp_dir=tmp_path):
            os.symlink(target, "linked.env")

            result = runner.invoke(app, ["scan", "linked.env"])

            assert result.exit_code == 0
            assert "Skipping 1 symlink(s)" in result.stdout
            assert "linked.env" in result.stdout

    def test_staged_scan_of_a_symlink_is_unaffected(self, tmp_path):
        """
        Confirms the stated contract directly, rather than only in prose:
        '--staged' reads a symlink's Git-index entry, which is the literal
        target path *string*, not the target file's bytes -- so it was
        never exposed to this vulnerability and needs no skip logic. A
        secret sitting in the real target file must not surface via
        '--staged' either (proving nothing reads through it), and the scan
        must not crash on a staged symlink entry.
        """
        target = self._make_outside_secret_file(tmp_path)
        with runner.isolated_filesystem(temp_dir=tmp_path):
            os.system("git init -q")
            os.system('git config user.email "test@example.com"')
            os.system('git config user.name "Test"')
            os.symlink(target, "linked.env")
            os.system("git add linked.env")

            result = runner.invoke(app, ["scan", "--staged"])

            assert result.exit_code == 0, result.stdout
            assert "No issues found" in result.stdout
            assert self.OUTSIDE_SECRET not in result.stdout


class TestAtomicOpenBoundary:
    """
    Regression coverage for the P0-2 hardening: _open_for_scan is the
    actual security boundary, not _collect_files_to_scan's islink()
    pre-filter -- because that pre-filter and the eventual read are
    separate syscalls with a real (if narrow) window between them (see the
    P0-2 TOCTOU analysis in the session that added this). These tests
    exercise the read-time boundary directly and deterministically --
    calling _scan_single_file/_open_for_scan on a path that *is* a symlink
    right now, which is exactly the state the read-time boundary must
    defend against regardless of what an earlier check decided -- rather
    than trying to literally win a timing race, which would be flaky by
    construction.
    """

    OUTSIDE_SECRET = "AKIAABCDEFGHIJKLMNOP"

    def _make_outside_secret_file(self, tmp_path, name="outside_secret.env"):
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir(exist_ok=True)
        target = outside_dir / name
        target.write_text(f"AWS_ACCESS_KEY_ID={self.OUTSIDE_SECRET}\n")
        return target

    def test_regular_file_still_scans_via_open_for_scan(self, tmp_path):
        real_file = tmp_path / "real.env"
        real_file.write_text(f"AWS_ACCESS_KEY_ID={self.OUTSIDE_SECRET}\n")

        secrets, _ = scanner._scan_single_file(str(real_file), set())

        assert len(secrets) == 1
        assert self.OUTSIDE_SECRET not in secrets[0]["redacted_preview"]

    def test_scan_single_file_refuses_a_symlink_independent_of_collection(
        self, tmp_path
    ):
        """
        Models the moment after collection where the filesystem object at
        this path is (or has become) a symlink -- calling the read-time
        function directly, with no collection step involved at all, proves
        it refuses the read on its own rather than trusting an earlier
        check that may be stale by the time this runs.
        """
        target = self._make_outside_secret_file(tmp_path)
        symlink_path = tmp_path / "linked.env"
        os.symlink(target, symlink_path)

        secrets, undeclared = scanner._scan_single_file(str(symlink_path), set())

        assert secrets == []
        assert undeclared == []

    def test_open_for_scan_raises_on_a_symlink(self, tmp_path):
        """Unit-level proof of the mechanism itself, at the smallest
        granularity: the open call raises, it doesn't silently succeed."""
        target = self._make_outside_secret_file(tmp_path)
        symlink_path = tmp_path / "linked.env"
        os.symlink(target, symlink_path)

        assert scanner._CAN_USE_O_NOFOLLOW, (
            "this test environment is expected to support O_NOFOLLOW; "
            "see TestONofollowFallback for the platform without it"
        )
        with pytest.raises(OSError):
            scanner._open_for_scan(str(symlink_path))

    def test_open_for_scan_reads_a_regular_file_normally(self, tmp_path):
        real_file = tmp_path / "real.txt"
        real_file.write_text("hello world\nsecond line\n")

        with scanner._open_for_scan(str(real_file)) as f:
            content = f.read()

        assert content == "hello world\nsecond line\n"

    def test_staged_scan_never_calls_open_for_scan(self, mocker, tmp_path):
        """
        Proves requirement 8 directly: staged scanning must never reach the
        disk-open boundary at all, since it reads content from the Git
        index (see _scan_single_file's `content` parameter), not from disk.
        """
        spy = mocker.patch(
            "envshield.core.scanner._open_for_scan",
            side_effect=AssertionError("a staged scan must never call _open_for_scan"),
        )
        with runner.isolated_filesystem(temp_dir=tmp_path):
            os.system("git init -q")
            os.system('git config user.email "test@example.com"')
            os.system('git config user.name "Test"')
            with open("app.py", "w") as f:
                f.write("import os\n\nx = os.getenv('SOME_KEY')\n")
            os.system("git add app.py")

            result = runner.invoke(app, ["scan", "--staged"])

            assert result.exit_code == 1  # SOME_KEY is undeclared -- expected
        spy.assert_not_called()


class TestONofollowFallback:
    """
    Coverage for platforms without os.O_NOFOLLOW (Windows). Patches the
    module-level capability flag rather than mocking any stdlib function,
    so this stays a narrow, local, deterministic test of _open_for_scan's
    own branch -- not a global patch of os.path/os.open that could affect
    unrelated code running in the same test.
    """

    def test_fallback_still_reads_regular_files_normally(self, tmp_path, mocker):
        mocker.patch.object(scanner, "_CAN_USE_O_NOFOLLOW", False)
        real_file = tmp_path / "real.txt"
        real_file.write_text("hello world\n")

        with scanner._open_for_scan(str(real_file)) as f:
            content = f.read()

        assert content == "hello world\n"

    def test_fallback_has_the_documented_residual_symlink_gap(self, tmp_path, mocker):
        """
        Proves the accepted limitation explicitly rather than leaving it
        asserted only in a comment: without O_NOFOLLOW, a symlink IS
        followed. This is the documented, disclosed gap on such platforms
        (see _open_for_scan's docstring and the P0-2 TOCTOU analysis) -- a
        test failure here would mean the fallback became *more* dangerous
        than documented, not less.
        """
        mocker.patch.object(scanner, "_CAN_USE_O_NOFOLLOW", False)
        outside = tmp_path / "outside.txt"
        outside.write_text("target content\n")
        symlink_path = tmp_path / "linked.txt"
        os.symlink(outside, symlink_path)

        with scanner._open_for_scan(str(symlink_path)) as f:
            content = f.read()

        assert content == "target content\n"


class TestRedactMatch:
    """
    Unit coverage for scanner._redact_match -- the single function every
    output path (CLI table, --json) now depends on to never emit a secret
    value. Exercised directly, independent of which detection regex
    happened to match, so these hold regardless of how SECRET_PATTERNS
    changes in the future.
    """

    def test_never_returns_the_input_text(self):
        secret = "AKIAABCDEFGHIJKLMNOP"
        preview = scanner._redact_match(secret)
        assert secret not in preview

    def test_reports_the_length(self):
        assert scanner._redact_match("a" * 40) == "<redacted, 40 chars>"

    def test_very_short_secret_reveals_nothing(self):
        """
        A 1-2 character 'secret' is the case where any boundary-character
        reveal would disclose most or all of the value -- confirm the
        redaction is still fully opaque, not just 'safe enough'.
        """
        preview = scanner._redact_match("ab")
        assert preview == "<redacted, 2 chars>"
        assert "a" not in preview.replace("chars", "").replace("redacted", "")
        assert "b" not in preview.replace("chars", "").replace("redacted", "")

    def test_secret_containing_quotes_is_not_leaked(self):
        secret = '"AKIAABCDEFGHIJKLMNOP"'
        preview = scanner._redact_match(secret)
        assert "AKIAABCDEFGHIJKLMNOP" not in preview
        assert '"' not in preview

    def test_secret_containing_whitespace_is_not_leaked(self):
        secret = "sk_live_ 123 456 789abcdefghijklmnop"
        preview = scanner._redact_match(secret)
        assert "123" not in preview
        assert "456" not in preview

    def test_unicode_secret_is_not_leaked(self):
        secret = "tökén_日本語_secretvalue1234567890"
        preview = scanner._redact_match(secret)
        assert "tökén" not in preview
        assert "日本語" not in preview
        # len() on a Python str counts codepoints, so this must match the
        # actual character count, not a byte count that would silently
        # differ for multi-byte characters.
        assert preview == f"<redacted, {len(secret)} chars>"


_REDACTED_PREVIEW_RE = re.compile(r"^<redacted, \d+ chars>$")


def _assert_well_formed_finding(finding):
    """
    A finding must still be a usable lead (file, line, a classification,
    and a preview whose *shape* is right) without the preview ever being
    anything other than the fixed, contentless redaction format -- not
    just "doesn't happen to contain this one test's secret".
    """
    assert finding["file_path"]
    assert isinstance(finding["line_num"], int) and finding["line_num"] > 0
    assert finding["secret_type"]
    assert _REDACTED_PREVIEW_RE.match(finding["redacted_preview"]), finding[
        "redacted_preview"
    ]
    assert "line_content" not in finding, (
        "the old raw-line field must be gone entirely, not just emptied"
    )


class TestSecretValueNeverLeaksToOutput:
    """
    Regression coverage for the security invariant behind P0-1: a secret
    value must never enter an output representation, in any form the
    scanner produces -- not the normal Rich-rendered CLI output, not
    `--json`. These tests prove the invariant directly (the exact secret
    string is provably absent from stdout), rather than only re-running the
    original reported case.

    Deliberately does not assert *which* named pattern in SECRET_PATTERNS
    fires for a given line (several overlap by design, e.g. "Generic API
    Key" matches most vendor-specific shapes too) -- that's a detection
    coverage question, not part of this invariant.
    """

    SECRET_VALUE = "AKIAABCDEFGHIJKLMNOP"

    def _write_secret_file(self, path="app.py"):
        with open(path, "w") as f:
            f.write(f'AWS_ACCESS_KEY_ID = "{self.SECRET_VALUE}"\n')

    def test_normal_cli_output_never_contains_the_secret_value(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            self._write_secret_file()

            result = runner.invoke(app, ["scan"])

            assert result.exit_code == 1
            assert "DANGER: Found 1 potential secret(s)!" in result.stdout
            assert self.SECRET_VALUE not in result.stdout
            # The finding must still be identifiable without the value.
            assert "app.py" in result.stdout
            assert "redacted" in result.stdout.lower()

    def test_json_output_never_contains_the_secret_value(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            self._write_secret_file()

            result = runner.invoke(app, ["scan", "--json"])

            assert result.exit_code == 1
            # Assert on the raw, already-serialized stdout -- proves the
            # value is absent from the actual bytes written, not just from
            # a re-parsed/re-rendered view of them (proves redaction
            # survives JSON serialization, not just Python-object identity).
            assert self.SECRET_VALUE not in result.stdout

            payload = json.loads(result.stdout)
            assert payload["clean"] is False
            assert len(payload["secrets"]) == 1
            finding = payload["secrets"][0]
            assert finding["file_path"].endswith("app.py")
            assert finding["line_num"] == 1
            assert self.SECRET_VALUE not in finding["redacted_preview"]
            _assert_well_formed_finding(finding)

    def test_secret_embedded_in_surrounding_source_text_is_not_leaked(self, tmp_path):
        """A secret sitting mid-line, surrounded by ordinary code, must not
        leak even though the rest of the line is harmless and could
        reasonably appear in output."""
        with runner.isolated_filesystem(temp_dir=tmp_path):
            secret_value = "sk_live_" + "a" * 30
            with open("app.py", "w") as f:
                f.write(
                    "def configure():\n"
                    f'    token = "{secret_value}"  # noqa: hardcoded for local dev\n'
                )

            result = runner.invoke(app, ["scan", "--json"])

            assert result.exit_code == 1
            assert secret_value not in result.stdout
            payload = json.loads(result.stdout)
            finding = payload["secrets"][0]
            _assert_well_formed_finding(finding)

    def test_multiple_findings_each_redacted_independently(self, tmp_path):
        """Two distinct secrets across two lines must each be individually
        redacted -- one leaking must not be masked by the other passing."""
        with runner.isolated_filesystem(temp_dir=tmp_path):
            with open(".env", "w") as f:
                f.write(
                    "AWS_ACCESS_KEY_ID=AKIAABCDEFGHIJKLMNOP\n"
                    "STRIPE_KEY=sk_live_123456789abcdefghijklmnopqrstuv\n"
                )

            result = runner.invoke(app, ["scan", "--json"])

            assert result.exit_code == 1
            payload = json.loads(result.stdout)
            assert len(payload["secrets"]) == 2
            for finding in payload["secrets"]:
                _assert_well_formed_finding(finding)
            assert "AKIAABCDEFGHIJKLMNOP" not in result.stdout
            assert "sk_live_123456789abcdefghijklmnopqrstuv" not in result.stdout


class TestPythonAstDiscoveryViaScanner:
    """
    Phase 2B Milestone 1: AST-based discovery closes two confirmed regex
    misses (bracket access, multi-line calls) and preserves every existing
    behavior (finding shape, diff-aware filtering, resilience to a
    malformed file) end to end through the actual `_scan_single_file`
    entry point -- not just at the discovery.py unit level.
    """

    def test_bracket_access_is_now_caught(self):
        secrets, undeclared = scanner._scan_single_file(
            "app.py", set(), content="x = os.environ['UNDECLARED']\n"
        )
        assert secrets == []
        assert len(undeclared) == 1
        assert undeclared[0]["variable_name"] == "UNDECLARED"
        assert undeclared[0]["file_path"] == "app.py"
        assert undeclared[0]["line_num"] == 1

    def test_multiline_call_is_now_caught(self):
        content = "x = os.environ.get(\n    'UNDECLARED',\n    'fallback',\n)\n"

        _, undeclared = scanner._scan_single_file("app.py", set(), content=content)

        assert len(undeclared) == 1
        assert undeclared[0]["variable_name"] == "UNDECLARED"
        assert undeclared[0]["line_num"] == 1

    def test_declared_variable_via_bracket_access_is_not_flagged(self):
        _, undeclared = scanner._scan_single_file(
            "app.py", {"DECLARED"}, content="x = os.environ['DECLARED']\n"
        )
        assert undeclared == []

    def test_new_lines_only_filters_ast_discovered_usages_too(self):
        content = "a = os.environ['OLD']\nb = os.environ['NEW']\n"

        _, undeclared = scanner._scan_single_file(
            "app.py", set(), content=content, new_lines_only={2}
        )

        assert len(undeclared) == 1
        assert undeclared[0]["variable_name"] == "NEW"

    def test_a_syntax_error_does_not_abort_the_scan_or_hide_secrets(self):
        """
        A malformed .py file must not crash the scan or suppress secret
        detection in that same file -- discovery just contributes nothing
        for it.
        """
        content = "def f(:\nAWS_ACCESS_KEY_ID=AKIAABCDEFGHIJKLMNOP\n"

        secrets, undeclared = scanner._scan_single_file(
            "broken.py", set(), content=content
        )

        assert undeclared == []
        assert len(secrets) == 1

    def test_a_non_py_file_with_python_looking_text_is_no_longer_flagged(self):
        """
        Intentional narrowing (approved as part of the Phase 2B Milestone 1
        plan): AST discovery is gated to .py files, and the old regex that
        used to catch this in *any* file was removed globally, not just
        replaced for .py files.
        """
        _, undeclared = scanner._scan_single_file(
            "notes.md", set(), content="Example: `os.environ.get('X')`\n"
        )
        assert undeclared == []


class TestJsTsDiscoveryViaScanner:
    """
    Phase 2B Milestone 2: JS/TS discovery (regex + bounded brace-matching,
    not tree-sitter -- see discovery.py) closes the confirmed gaps
    (bracket access, import.meta.env, destructuring including multi-line)
    end to end through the actual `_scan_single_file` entry point.
    """

    def test_dot_access_is_still_caught(self):
        _, undeclared = scanner._scan_single_file(
            "app.js", set(), content="const x = process.env.UNDECLARED;\n"
        )
        assert len(undeclared) == 1
        assert undeclared[0]["variable_name"] == "UNDECLARED"
        assert undeclared[0]["file_path"] == "app.js"
        assert undeclared[0]["line_num"] == 1

    def test_bracket_access_is_now_caught(self):
        _, undeclared = scanner._scan_single_file(
            "app.js", set(), content="const x = process.env['UNDECLARED'];\n"
        )
        assert len(undeclared) == 1
        assert undeclared[0]["variable_name"] == "UNDECLARED"

    def test_import_meta_env_is_now_caught(self):
        _, undeclared = scanner._scan_single_file(
            "app.ts", set(), content="const x = import.meta.env.VITE_UNDECLARED;\n"
        )
        assert len(undeclared) == 1
        assert undeclared[0]["variable_name"] == "VITE_UNDECLARED"

    def test_multiline_destructuring_is_now_caught(self):
        content = "const {\n  UNDECLARED,\n  ALSO_UNDECLARED,\n} = process.env;\n"

        _, undeclared = scanner._scan_single_file("app.ts", set(), content=content)

        assert {f["variable_name"] for f in undeclared} == {
            "UNDECLARED",
            "ALSO_UNDECLARED",
        }
        assert all(f["line_num"] == 1 for f in undeclared)

    def test_declared_variable_via_destructuring_is_not_flagged(self):
        _, undeclared = scanner._scan_single_file(
            "app.js", {"DECLARED"}, content="const { DECLARED } = process.env;\n"
        )
        assert undeclared == []

    def test_new_lines_only_filters_js_discovered_usages_too(self):
        content = "const a = process.env['OLD'];\nconst b = process.env['NEW'];\n"

        _, undeclared = scanner._scan_single_file(
            "app.js", set(), content=content, new_lines_only={2}
        )

        assert len(undeclared) == 1
        assert undeclared[0]["variable_name"] == "NEW"

    def test_jsx_and_tsx_files_are_also_scanned(self):
        _, undeclared_jsx = scanner._scan_single_file(
            "app.jsx", set(), content="const x = process.env.UNDECLARED;\n"
        )
        _, undeclared_tsx = scanner._scan_single_file(
            "app.tsx", set(), content="const x = process.env.UNDECLARED;\n"
        )
        assert len(undeclared_jsx) == 1
        assert len(undeclared_tsx) == 1

    def test_a_non_js_file_with_js_looking_text_is_no_longer_flagged(self):
        """
        Intentional narrowing, mirroring the Python one above: JS/TS
        discovery is gated to .js/.jsx/.ts/.tsx files, and the old regex
        that used to catch this in *any* file was removed globally.
        """
        _, undeclared = scanner._scan_single_file(
            "notes.md", set(), content="Example: `process.env.UNDECLARED`\n"
        )
        assert undeclared == []

    def test_malformed_js_does_not_crash_the_scan_or_hide_secrets(self):
        content = "const { FOO = process.env;\nAWS_ACCESS_KEY_ID=AKIAABCDEFGHIJKLMNOP\n"

        secrets, undeclared = scanner._scan_single_file(
            "broken.js", set(), content=content
        )

        assert undeclared == []
        assert len(secrets) == 1


class TestDatabaseConnectionStringPatternCoversPostgresql:
    """
    Regression coverage for a P0 finding: the pattern used to match only
    the bare 'postgres://' scheme. 'postgresql://' -- the scheme
    SQLAlchemy, Django, and psycopg all require -- silently fell through to
    no match at all, meaning a real embedded-credential connection string
    using the single most common real-world scheme was never recognized as
    secret-shaped by value. This matters beyond scan() itself: import/init's
    _classify_variable (envshield/core/importer.py) checks the exact same
    SECRET_PATTERNS list before falling back to a name-keyword heuristic,
    so this same gap let a real password get written as a schema
    defaultValue -- see test_importer.py's companion regression test.
    """

    def test_postgresql_scheme_with_embedded_credentials_is_secret_shaped(self):
        value = "postgresql://appuser:sup3rsecret@db.internal.prod:5432/appdb"
        matched = any(re.search(p["pattern"], value) for p in scanner.SECRET_PATTERNS)
        assert matched

    def test_bare_postgres_scheme_still_matches(self):
        """Guards against a fix that narrows the pattern instead of widening it."""
        value = "postgres://appuser:sup3rsecret@db.internal.prod:5432/appdb"
        matched = any(re.search(p["pattern"], value) for p in scanner.SECRET_PATTERNS)
        assert matched

    def test_postgresql_url_with_no_credentials_does_not_false_positive(self):
        """A connection string with no embedded user:pass is not secret-shaped by this pattern."""
        value = "postgresql://db.internal.prod:5432/appdb"
        matched = any(re.search(p["pattern"], value) for p in scanner.SECRET_PATTERNS)
        assert not matched


class TestDsnStyleUrlWithEmbeddedApiKeyIsCaught:
    """
    Regression: a single-token DSN-style URL (a Sentry DSN's real shape --
    a long generated key directly before '@', no ':pass' pair) fell through
    both the Database Connection String pattern above (which requires a
    user:pass PAIR) and importer.py's DATABASE_URL/SENTRY_DSN name-keyword
    heuristic, confirmed via real onboarding testing to let a real key get
    written as a schema defaultValue into both env.schema.toml AND the
    committed .env.example template.
    """

    def test_realistic_sentry_dsn_shape_is_secret_shaped(self):
        value = (
            "https://a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6@o123456.ingest.sentry.io/7890123"
        )
        matched = any(re.search(p["pattern"], value) for p in scanner.SECRET_PATTERNS)
        assert matched

    def test_ordinary_url_with_a_short_username_does_not_false_positive(self):
        """A short, human-readable username (e.g. a git remote's 'user@host') is not secret-shaped."""
        value = "https://git@github.com/example/repo.git"
        matched = any(re.search(p["pattern"], value) for p in scanner.SECRET_PATTERNS)
        assert not matched

    def test_url_with_no_userinfo_segment_does_not_false_positive(self):
        value = "https://o123456.ingest.sentry.io/7890123"
        matched = any(re.search(p["pattern"], value) for p in scanner.SECRET_PATTERNS)
        assert not matched
