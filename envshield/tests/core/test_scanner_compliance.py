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


def test_scan_undeclared_suggestion_gives_a_concrete_next_step(mocker, tmp_path):
    """
    Regression: the old suggestion ("Please add these variables to your
    'env.schema.toml' to maintain your configuration contract.") told the
    user the desired end state but not how to get there. It must now point
    at the actual next action -- and must not imply 'schema sync' does this,
    since that command only ever propagates an *already-declared* schema
    variable into '.env.example'/a local config module; it never adds a
    newly-discovered undeclared read into the schema itself.
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

        assert result.exit_code == 1
        assert "env.schema.toml" in result.stdout
        assert "hand-edited" in result.stdout
        assert "envshield scan" in result.stdout
        assert "schema sync" not in result.stdout
        assert "Please add these variables" not in result.stdout


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


class TestVendorPathExclusion:
    """
    BL-127: real-world scanning found FPs only in third-party vendored code
    (a minified Private Key stub, a minified plugin bundle, a vendored
    TypeScript .d.ts's type-signature parameters) with zero confirmed real
    credentials ever found under a vendor/ path. 'vendor' was added to
    DEFAULT_EXCLUDED_DIRS -- same mechanism as node_modules/.venv, not a
    new exclusion system.
    """

    def test_top_level_vendor_dir_is_skipped(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            os.makedirs("vendor")
            with open("vendor/jsencrypt.min.js", "w") as f:
                f.write("-----BEGIN RSA PRIVATE KEY-----\n")

            result = runner.invoke(app, ["scan"])

            assert result.exit_code == 0, result.stdout
            assert "No issues found" in result.stdout

    def test_nested_vendor_dir_is_skipped(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            os.makedirs("hermes/app/static/vendor/tinymce")
            with open("hermes/app/static/vendor/tinymce/plugin.min.js", "w") as f:
                f.write("const key = 'sk_live_123456789abcdefghijklmnopqrstuv';\n")

            result = runner.invoke(app, ["scan"])

            assert result.exit_code == 0, result.stdout
            assert "No issues found" in result.stdout

    def test_same_secret_material_outside_vendor_is_still_scanned(self, tmp_path):
        """Confirms exclusion is path-based, not content-based."""
        with runner.isolated_filesystem(temp_dir=tmp_path):
            with open("app.js", "w") as f:
                f.write("const key = 'sk_live_123456789abcdefghijklmnopqrstuv';\n")

            result = runner.invoke(app, ["scan"])

            assert result.exit_code == 1
            assert "DANGER: Found 1 potential secret(s)!" in result.stdout

    def test_vendor_as_substring_is_not_excluded(self, tmp_path):
        """
        'my_vendor' and 'vendored' must not match -- only a path component
        that is exactly 'vendor' is pruned.
        """
        with runner.isolated_filesystem(temp_dir=tmp_path):
            os.makedirs("my_vendor")
            with open("my_vendor/config.js", "w") as f:
                f.write("const key = 'sk_live_123456789abcdefghijklmnopqrstuv';\n")
            os.makedirs("vendored")
            with open("vendored/other.js", "w") as f:
                f.write("const token = 'ghp_123456789012345678901234567890123456';\n")

            result = runner.invoke(app, ["scan"])

            assert result.exit_code == 1
            assert "DANGER: Found 2 potential secret(s)!" in result.stdout

    def test_user_configured_exclusion_still_works_alongside_vendor_default(
        self, tmp_path
    ):
        """
        The pre-existing envshield.yml exclude_files mechanism is untouched.
        Scans an explicit path rather than the default '.' -- see BL-128:
        _filter_files's glob matching against a '.'-prefixed path (produced
        by os.walk(".")) has a pre-existing, unrelated normalization gap
        that a bare 'scan' with no path argument would otherwise hit.
        """
        with runner.isolated_filesystem(temp_dir=tmp_path):
            with open("envshield.yml", "w") as f:
                f.write("secret_scanning:\n  exclude_files:\n    - 'legacy/*.js'\n")
            os.makedirs("legacy")
            with open("legacy/old.js", "w") as f:
                f.write("const key = 'sk_live_123456789abcdefghijklmnopqrstuv';\n")

            result = runner.invoke(app, ["scan", "legacy"])

            assert result.exit_code == 0, result.stdout
            assert "No issues found" in result.stdout

    def test_generic_api_key_and_provider_patterns_unaffected_outside_vendor(self):
        """Direct pattern-level check: nothing about SECRET_PATTERNS itself changed."""
        line = 'AWS_ACCESS_KEY_ID = "AKIA3X9QK2LP7MB4"'
        matched = any(re.search(p["pattern"], line) for p in scanner.SECRET_PATTERNS)
        assert matched


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


def test_scan_staged_enforce_undeclared_gives_a_concrete_next_step(tmp_path):
    """
    Regression: 'scan --staged --enforce' (what the installed pre-commit
    hook runs) blocks on an undeclared variable unconditionally -- there's
    no interactive override for it, unlike a high-confidence secret finding
    (see enforcement.enforce_findings). The commit-abort message on this
    path previously named the problem ('Commit aborted. Please fix the
    issues above...') without ever saying how -- the same gap as the plain
    'scan' suggestion, just on a different code path.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.system("git init -q")
        os.system('git config user.email "test@example.com"')
        os.system('git config user.name "Test"')
        with open(SCHEMA_FILE_NAME, "w") as f:
            f.write('[DECLARED_KEY]\ndescription="This one is okay"\n')

        with open("app.py", "w") as f:
            f.write("import os\n\nAPI_KEY = os.environ.get('UNDECLARED_KEY')\n")
        os.system("git add .")

        result = runner.invoke(app, ["scan", "--staged", "--enforce"])

        assert result.exit_code == 1
        assert "Commit aborted" in result.stdout
        assert "env.schema.toml" in result.stdout
        assert "hand-edited" in result.stdout
        assert "schema sync" not in result.stdout


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

    A bare, non-staged interactive `scan` deliberately keeps exit 0 here
    (see BL-004's design: incompleteness is informational, not fatal,
    outside staged/CI-facing modes) -- but it must never claim the scan
    was fully clean when it wasn't, so the old "secure and compliant"
    message must be gone in favor of an honest "coverage was incomplete"
    one.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        with open("big.env", "w") as f:
            f.write("PADDING=" + ("a" * 1_000_010) + "\n")

        result = runner.invoke(app, ["scan"])

        assert result.exit_code == 0
        assert "Skipped 1 file(s) over 1MB" in result.stdout
        assert "big.env" in result.stdout
        assert "coverage was incomplete" in result.stdout
        assert "secure and compliant" not in result.stdout


class TestFlaskConfidenceIsExcludedFromUndeclaredDetection:
    """
    Consumer-boundary regression for BL-113: 'scan's undeclared-variable
    detection is a binary completeness signal, and a medium-confidence
    usage (currently, a Flask current_app.config[...] read) must never
    contribute to it -- found and fixed during BL-113's own implementation
    (a live Zeus reproduction showed the undeclared-variable count change
    from 206 to 348 before this filter existed). These tests exercise the
    real CLI path, not just discovery.py's own unit tests, so a future
    change that reintroduces the leak fails here first.
    """

    def test_flask_only_read_is_not_reported_as_undeclared(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            with open(SCHEMA_FILE_NAME, "w") as f:
                f.write("")
            python_code = (
                "from flask import current_app as app\n"
                "x = app.config['FLASK_ONLY_VAR']\n"
            )
            with open("app.py", "w") as f:
                f.write(python_code)

            result = runner.invoke(app, ["scan"])

            assert result.exit_code == 0
            assert "FLASK_ONLY_VAR" not in result.stdout

    def test_mixed_os_and_flask_reads_only_reports_the_os_one(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            with open(SCHEMA_FILE_NAME, "w") as f:
                f.write("")
            python_code = (
                "import os\n"
                "from flask import current_app as app\n"
                "a = os.getenv('OS_VAR')\n"
                "b = app.config['FLASK_VAR']\n"
            )
            with open("app.py", "w") as f:
                f.write(python_code)

            result = runner.invoke(app, ["scan"])

            assert result.exit_code == 1
            assert "OS_VAR" in result.stdout
            assert "FLASK_VAR" not in result.stdout

    def test_os_read_is_still_reported_as_undeclared(self, tmp_path):
        """Preservation of existing high-confidence behavior -- BL-113
        must not change what was already correctly detected."""
        with runner.isolated_filesystem(temp_dir=tmp_path):
            with open(SCHEMA_FILE_NAME, "w") as f:
                f.write("")
            with open("app.py", "w") as f:
                f.write("import os\nx = os.getenv('OS_ONLY_VAR')\n")

            result = runner.invoke(app, ["scan"])

            assert result.exit_code == 1
            assert "OS_ONLY_VAR" in result.stdout


class TestScanCompletenessContract:
    """
    Regression coverage for BL-004: a skipped file (over MAX_SCANNABLE_SIZE_BYTES)
    used to have zero effect on `clean`/exit code in any mode, including
    `--staged` -- the exact mode EnvShield's own generated pre-commit hook
    invokes -- so padding a secret-bearing file past 1MB fully bypassed the
    scanner. The fix adds a `complete` field (True iff nothing was skipped)
    alongside the unchanged `clean` field, and makes incompleteness fatal
    for `--staged` and/or `--json` (machine/automation-facing modes) while
    a bare interactive `scan` keeps its existing exit-0 UX.
    """

    SECRET_LINE = "AWS_SECRET_ACCESS_KEY=SYNTHETIC0000FAKEKEYNOTAREALSECRET1234\n"

    def _git_init(self):
        os.system("git init -q")
        os.system('git config user.email "test@example.com"')
        os.system('git config user.name "Test"')

    def _write_oversized_secret_file(self, name="big.env"):
        with open(name, "w") as f:
            f.write(self.SECRET_LINE)
            f.write("PADDING=" + ("a" * (1_000_010 - len(self.SECRET_LINE))) + "\n")

    def test_complete_is_true_for_a_fully_scanned_result(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            with open("app.env", "w") as f:
                f.write("SOME_VALUE=fine\n")

            result = runner.invoke(app, ["scan", "--json"])

            assert result.exit_code == 0
            payload = json.loads(result.stdout)
            assert payload["clean"] is True
            assert payload["complete"] is True
            assert payload["skipped_files"] == []

    def test_complete_is_false_for_an_eligible_oversized_file(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            self._write_oversized_secret_file()

            result = runner.invoke(app, ["scan", "--json"])

            payload = json.loads(result.stdout)
            assert payload["complete"] is False
            assert payload["skipped_files"] == ["./big.env"]

    def test_bare_staged_scan_exits_nonzero_when_an_eligible_file_is_skipped(
        self, tmp_path
    ):
        """The exact shape the installed pre-commit hook invokes
        (`envshield scan --staged`, no `--json`) -- this is BL-004's own
        primary evidenced bypass, and must be fixed without any change to
        the hook script itself."""
        with runner.isolated_filesystem(temp_dir=tmp_path):
            self._git_init()
            self._write_oversized_secret_file()
            os.system("git add big.env")

            result = runner.invoke(app, ["scan", "--staged"])

            assert result.exit_code == 1
            assert "Commit aborted" in result.stdout
            assert "coverage is incomplete" in result.stdout

    def test_staged_json_scan_exits_nonzero_and_reports_complete_false(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            self._git_init()
            self._write_oversized_secret_file()
            os.system("git add big.env")

            result = runner.invoke(app, ["scan", "--staged", "--json"])

            assert result.exit_code == 1
            payload = json.loads(result.stdout)
            assert payload["complete"] is False
            assert payload["clean"] is True  # nothing found in what *was* scanned

    def test_nonstaged_json_scan_exits_nonzero_on_incompleteness(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            self._write_oversized_secret_file()

            result = runner.invoke(app, ["scan", "--json"])

            assert result.exit_code == 1
            payload = json.loads(result.stdout)
            assert payload["complete"] is False

    def test_ordinary_nonstaged_interactive_scan_retains_exit_zero(self, tmp_path):
        """The one mode BL-004's design deliberately leaves unaffected --
        see `test_scan_reports_skipped_large_files` for the full case."""
        with runner.isolated_filesystem(temp_dir=tmp_path):
            self._write_oversized_secret_file()

            result = runner.invoke(app, ["scan"])

            assert result.exit_code == 0

    def test_excluded_oversized_file_with_a_small_diff_is_not_flagged_incomplete(
        self, tmp_path
    ):
        """The corrected exclusion ordering: an excluded file's *total*
        size must not matter when only a small, already-bounded diff is
        actually being scanned."""
        with runner.isolated_filesystem(temp_dir=tmp_path):
            self._git_init()
            with open("envshield.yml", "w") as f:
                f.write("secret_scanning:\n  exclude_files:\n    - vendor.bin\n")
            with open("vendor.bin", "w") as f:
                f.write("A" * 1_000_010)
            os.system("git add -A")
            os.system('git commit -q -m "baseline"')

            # A small, genuinely new line appended to the already-committed,
            # oversized, excluded file.
            with open("vendor.bin", "a") as f:
                f.write("NEW_LINE\n")
            os.system("git add vendor.bin")

            result = runner.invoke(app, ["scan", "--staged", "--json"])

            payload = json.loads(result.stdout)
            assert payload["complete"] is True
            assert payload["skipped_files"] == []

    def test_excluded_but_brand_new_oversized_file_is_marked_incomplete(self, tmp_path):
        """The other half of the corrected ordering: an excluded file that's
        brand new (not in HEAD) is scanned in full *despite* the exclusion
        -- so its size still legitimately matters."""
        with runner.isolated_filesystem(temp_dir=tmp_path):
            self._git_init()
            with open("envshield.yml", "w") as f:
                f.write("secret_scanning:\n  exclude_files:\n    - vendor.bin\n")
            os.system("git add envshield.yml")
            os.system('git commit -q -m "baseline"')

            with open("vendor.bin", "w") as f:
                f.write("A" * 1_000_010)
            os.system("git add vendor.bin")

            result = runner.invoke(app, ["scan", "--staged", "--json"])

            payload = json.loads(result.stdout)
            assert payload["complete"] is False
            # --staged reports the absolute path (see get_staged_file_content);
            # only its basename is asserted, matching that existing convention.
            assert len(payload["skipped_files"]) == 1
            assert payload["skipped_files"][0].endswith("vendor.bin")

    def test_excluded_oversized_file_no_longer_incorrectly_marked_skipped_when_staged(
        self, tmp_path
    ):
        """Parity with non-staged mode: an excluded oversized file with a
        staged change that adds no *new* lines (a pure deletion) must not
        appear in `skipped_files` under `--staged` either -- only a
        bounded, already-small diff is actually being scanned."""
        with runner.isolated_filesystem(temp_dir=tmp_path):
            self._git_init()
            with open("envshield.yml", "w") as f:
                f.write("secret_scanning:\n  exclude_files:\n    - vendor.bin\n")
            with open("vendor.bin", "w") as f:
                f.write("A" * 1_000_010)
            os.system("git add -A")
            os.system('git commit -q -m "baseline"')

            # A pure deletion (no line inserted/replaced) -- _get_diff_lines
            # returns an empty set for this, distinct from "identical
            # content re-added", which git wouldn't even list as staged.
            with open("vendor.bin") as f:
                content = f.read()
            with open("vendor.bin", "w") as f:
                f.write(content[:-100])
            os.system("git add vendor.bin")

            result = runner.invoke(app, ["scan", "--staged", "--json"])

            payload = json.loads(result.stdout)
            assert payload["complete"] is True
            assert payload["skipped_files"] == []

    def test_multiple_skipped_files_are_all_reported(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            self._write_oversized_secret_file("big1.env")
            self._write_oversized_secret_file("big2.env")

            result = runner.invoke(app, ["scan", "--json"])

            payload = json.loads(result.stdout)
            assert payload["complete"] is False
            assert sorted(payload["skipped_files"]) == ["./big1.env", "./big2.env"]

    def test_json_output_is_a_single_valid_document_when_incomplete(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            self._write_oversized_secret_file()

            result = runner.invoke(app, ["scan", "--json"])

            # json.loads succeeding on the whole of stdout is itself the
            # proof there's exactly one well-formed document -- any stray
            # print mixed in (e.g. a leaked Rich progress bar) would break
            # this the same way it would for a real machine consumer.
            payload = json.loads(result.stdout)
            assert payload["complete"] is False

    def test_skipped_file_reporting_contains_no_secret_or_file_content(self, tmp_path):
        """Security requirement: skipped-file entries are a bare path
        string -- the secret-shaped content that made this file worth
        scanning in the first place must never appear anywhere in output
        just because the file itself couldn't be inspected."""
        with runner.isolated_filesystem(temp_dir=tmp_path):
            self._write_oversized_secret_file()

            result = runner.invoke(app, ["scan", "--json"])

            assert "SYNTHETIC0000FAKEKEYNOTAREALSECRET1234" not in result.stdout
            payload = json.loads(result.stdout)
            assert payload["skipped_files"] == ["./big.env"]
            assert isinstance(payload["skipped_files"][0], str)


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


class TestDatabaseConnectionStringIgnoresFStringPlaceholders:
    """
    Regression, found on real Zeus/issuebear/issuebear-management codebases
    (22 confirmed instances): SQLAlchemy's "dialect+driver://" scheme syntax
    (e.g. "mysql+pymysql://", "mariadb+pymysql://") was never modeled, so the
    pattern only ever matched these forms by accident -- "mysql" happens to
    match as a substring of "pymysql" immediately before "://". That same
    accidental substring match fires just as easily when the "credentials"
    are actually unresolved Python f-string placeholders, producing a false
    positive on ordinary code like
    f"mariadb+pymysql://{db_user}:{db_pass}@{db_host}/{db_name}".
    """

    def test_mariadb_pymysql_fstring_placeholders_is_not_a_secret(self):
        line = 'app.config["SQLALCHEMY_DATABASE_URI"] = f"mariadb+pymysql://{db_user}:{db_pass}@{db_host}/{db_name}"'
        matched = any(re.search(p["pattern"], line) for p in scanner.SECRET_PATTERNS)
        assert not matched

    def test_mysql_pymysql_fstring_placeholders_is_not_a_secret(self):
        line = 'f"mysql+pymysql://{db_user}:{db_pass}@{db_host}/{db_name}"'
        matched = any(re.search(p["pattern"], line) for p in scanner.SECRET_PATTERNS)
        assert not matched

    def test_generic_fstring_user_and_password_placeholders_is_not_a_secret(self):
        line = 'url = f"postgresql://{user}:{password}@{host}:{port}/{name}"'
        matched = any(re.search(p["pattern"], line) for p in scanner.SECRET_PATTERNS)
        assert not matched

    def test_whitespace_inside_fstring_expression_braces_is_still_not_a_secret(self):
        """f"{ db_pass }" (spaces inside the braces) is valid Python and must be treated the same."""
        line = 'f"postgresql://{ db_user }:{ db_pass }@{ db_host }/db"'
        matched = any(re.search(p["pattern"], line) for p in scanner.SECRET_PATTERNS)
        assert not matched

    def test_mysql_pymysql_with_literal_credentials_is_still_detected(self):
        """The dialect+driver scheme form itself must remain detectable when credentials are real."""
        line = "mysql+pymysql://appuser:sup3rsecret@db.internal.prod:3306/appdb"
        matched = any(re.search(p["pattern"], line) for p in scanner.SECRET_PATTERNS)
        assert matched

    def test_mariadb_pymysql_with_literal_credentials_is_still_detected(self):
        line = "mariadb+pymysql://appuser:sup3rsecret@db.internal.prod:3306/appdb"
        matched = any(re.search(p["pattern"], line) for p in scanner.SECRET_PATTERNS)
        assert matched

    def test_postgresql_psycopg2_with_literal_credentials_is_detected(self):
        """Dialect+driver support is general, not special-cased to mysql/mariadb."""
        line = "postgresql+psycopg2://appuser:sup3rsecret@db.internal.prod:5432/appdb"
        matched = any(re.search(p["pattern"], line) for p in scanner.SECRET_PATTERNS)
        assert matched

    def test_mongodb_srv_with_literal_credentials_still_detected(self):
        """Guards against the dialect+driver change breaking mongodb's existing '+srv' handling."""
        line = "mongodb+srv://appuser:sup3rsecret@cluster0.mongodb.net/appdb"
        matched = any(re.search(p["pattern"], line) for p in scanner.SECRET_PATTERNS)
        assert matched

    def test_placeholder_username_with_literal_password_is_still_detected(self):
        """
        A templated username next to a genuinely hardcoded password must still be
        flagged -- the actual secret (the password) is real, so the templated
        username must not suppress detection.
        """
        line = 'f"postgresql://{db_user}:hardcodedpass123@host/db"'
        matched = any(re.search(p["pattern"], line) for p in scanner.SECRET_PATTERNS)
        assert matched

    def test_literal_username_with_placeholder_password_is_not_detected(self):
        """
        The mirror image of the case above: a literal (non-secret) username next
        to a templated password has no real secret present and must not match.
        """
        line = 'f"postgresql://appuser:{db_pass}@host/db"'
        matched = any(re.search(p["pattern"], line) for p in scanner.SECRET_PATTERNS)
        assert not matched

    def test_bare_pymysql_scheme_with_no_dialect_prefix_does_not_match(self):
        """'pymysql://' alone is not a real SQLAlchemy scheme; nothing legitimate uses it."""
        line = "pymysql://user:pass@host/db"
        matched = any(re.search(p["pattern"], line) for p in scanner.SECRET_PATTERNS)
        assert not matched

    def test_end_to_end_scan_does_not_flag_fstring_database_uri_template(self):
        content = (
            "def make_engine(db_user, db_pass, db_host, db_name):\n"
            '    uri = f"mariadb+pymysql://{db_user}:{db_pass}@{db_host}/{db_name}"\n'
            "    return create_engine(uri)\n"
        )
        secrets, _ = scanner._scan_single_file("db.py", set(), content=content)
        assert secrets == []

    def test_end_to_end_scan_still_flags_literal_hardcoded_connection_string(self):
        content = 'DATABASE_URL = "mysql+pymysql://appuser:sup3rsecret@db.internal.prod:3306/appdb"\n'
        secrets, _ = scanner._scan_single_file("settings.py", set(), content=content)
        assert len(secrets) == 1
        assert secrets[0]["secret_type"] == "Database Connection String"


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


class TestGenericApiKeyPatternIgnoresOrdinaryPythonSyntax:
    """
    Regression, found on a real site codebase: the "Generic API Key"
    pattern's keyword group had no boundary at all, so it matched as a
    mid-word substring ("auth" inside "authorization"), and its unquoted-
    value branch had no check that the matched span wasn't actually a
    function call -- so an ordinary Python type annotation and an ordinary
    "compute a cache key" function call both false-positived as secrets.
    Every one of site' 40 findings traced to exactly these two shapes.
    """

    def test_type_annotation_is_not_a_secret(self):
        line = "    work_authorization: WorkAuthorization"
        matched = any(re.search(p["pattern"], line) for p in scanner.SECRET_PATTERNS)
        assert not matched

    def test_constructor_call_is_not_a_secret(self):
        line = (
            '        work_authorization=WorkAuthorization(value="not_stated", '
            'confidence="not_stated"),'
        )
        matched = any(re.search(p["pattern"], line) for p in scanner.SECRET_PATTERNS)
        assert not matched

    def test_cache_key_function_call_assignment_is_not_a_secret(self):
        line = (
            "    cache_key = compute_fit_cache_key(facts_json, "
            "profile_fingerprint(profile))"
        )
        matched = any(re.search(p["pattern"], line) for p in scanner.SECRET_PATTERNS)
        assert not matched

    def test_dedup_key_function_call_assignment_is_not_a_secret(self):
        line = "    dedup_key = compute_dedup_key(job)"
        matched = any(re.search(p["pattern"], line) for p in scanner.SECRET_PATTERNS)
        assert not matched

    def test_extraction_cache_key_function_call_assignment_is_not_a_secret(self):
        """The third distinct call site site' scan flagged, same shape."""
        line = (
            '    cache_key = compute_extraction_cache_key(job_row["title"], '
            'job_row["location_raw"], job_row["description_text"])'
        )
        matched = any(re.search(p["pattern"], line) for p in scanner.SECRET_PATTERNS)
        assert not matched

    def test_full_scan_of_the_site_shaped_file_reports_zero_findings(self):
        """
        End-to-end, not just the raw pattern: the same shapes run through
        the actual file-scanning path, not only re.search in isolation.
        """
        content = (
            "class JobFacts(BaseModel):\n"
            "    work_authorization: WorkAuthorization\n"
            "\n"
            "def extract_job(conn, client, job_row):\n"
            "    cache_key = compute_extraction_cache_key(\n"
            '        job_row["title"], job_row["location_raw"]\n'
            "    )\n"
            "    dedup_key = compute_dedup_key(job_row)\n"
        )
        secrets, _ = scanner._scan_single_file(
            "job_facts.py", schema_vars=set(), content=content
        )
        assert secrets == []

    def test_legitimate_generic_assignments_still_match(self):
        """The fix must not weaken real, quoted or unquoted, generic secrets."""
        lines = [
            'API_KEY = "abcdefghijklmnop1234"',
            "API_TOKEN=abcdefghijklmnop1234",
            'AUTH_TOKEN = "abcdefghijklmnop1234"',
            'PASSWORD = "abcdefghijklmnop1234"',
            'SECRET = "abcdefghijklmnop1234"',
            'CREDENTIAL = "abcdefghijklmnop1234"',
        ]
        for line in lines:
            matched = any(
                re.search(p["pattern"], line) for p in scanner.SECRET_PATTERNS
            )
            assert matched, f"expected a match for: {line!r}"

    def test_provider_specific_patterns_are_unaffected(self):
        """Only the Generic API Key pattern changed; provider formats didn't."""
        lines = [
            "AKIAABCDEFGHIJKLMNOP",  # AWS Access Key ID
            "ghp_" + "a" * 36,  # GitHub PAT (classic)
            "sk_live_" + "a" * 24,  # Stripe secret key
        ]
        for line in lines:
            matched = any(
                re.search(p["pattern"], line) for p in scanner.SECRET_PATTERNS
            )
            assert matched, f"expected a match for: {line!r}"

    def test_compound_no_underscore_keywords_still_match(self):
        """
        The scanner must keep recognizing the same no-underscore compounds
        importer.py's SECRET_KEY_KEYWORDS already special-cases (APIKEY,
        ACCESSKEY, SECRETKEY, AUTHTOKEN) -- adding a keyword boundary must
        not regress this, only reject a keyword continuing into unrelated
        letters like "authorization" or "monkey".
        """
        lines = [
            'AWS_APIKEY = "abcdefghijklmnop1234"',
            'AWS_ACCESSKEY = "abcdefghijklmnop1234"',
            'STRIPE_SECRETKEY = "abcdefghijklmnop1234"',
            'GITHUB_AUTHTOKEN = "abcdefghijklmnop1234"',
        ]
        for line in lines:
            matched = any(
                re.search(p["pattern"], line) for p in scanner.SECRET_PATTERNS
            )
            assert matched, f"expected a match for: {line!r}"

    def test_compound_word_false_positives_are_not_flagged(self):
        """
        Regression: a keyword must not match as a substring of an unrelated
        word -- "auth" inside "author"/"authorization", "key" inside
        "monkey"/"keyboard" -- even with a plausible long RHS value.
        Mirrors importer.py's existing MONKEY_PATCH/AUTHOR_NAME coverage,
        now enforced at the scanner layer too.
        """
        lines = [
            "MONKEY_PATCH_ENABLED: bool = True",
            'AUTHOR_NAME = "Jane Doe Long Display Name Value"',
            'KEYBOARD_LAYOUT = "some_long_layout_identifier_value"',
        ]
        for line in lines:
            matched = any(
                re.search(p["pattern"], line) for p in scanner.SECRET_PATTERNS
            )
            assert not matched, f"expected no match for: {line!r}"


class TestGenericApiKeyPatternIgnoresCodeReferenceShapes:
    """
    Milestone 2 regression: broader real-world validation (phineas, Zeus)
    found the same underlying class Milestone 1 fixed for a bare function
    call (`x = foo(...)`) recurring through three more syntactic shapes --
    an enclosing call's own closing paren, a subscript, and a TypeScript
    generic parameter list -- each an equally strong signal that the
    matched text is code, not a literal. A fourth candidate, '.', is
    deliberately handled more narrowly: excluded only when it continues
    into another identifier (attribute access), not when it plausibly ends
    a sentence in prose.
    """

    def test_enclosing_call_closing_paren_is_not_a_secret(self):
        """token=value where ')' closes an ALREADY-OPEN enclosing call."""
        lines = [
            "        s.loads(token, max_age=max_age_in_seconds)",
            "call_something(token=some_identifier_value)",
            "call_something(  token = some_identifier_value  )",
        ]
        for line in lines:
            matched = any(
                re.search(p["pattern"], line) for p in scanner.SECRET_PATTERNS
            )
            assert not matched, f"expected no match for: {line!r}"

    def test_subscript_access_is_not_a_secret(self):
        lines = [
            '    api_key = settings["postmark_api_key"]',
            "api_key = settings['postmark_api_key']",
            "api_key=settings[postmark_api_key_name]",
        ]
        for line in lines:
            matched = any(
                re.search(p["pattern"], line) for p in scanner.SECRET_PATTERNS
            )
            assert not matched, f"expected no match for: {line!r}"

    def test_attribute_access_is_not_a_secret(self):
        lines = [
            "    api_key = settings.API_KEY",
            "api_key=settings.API_KEY",
            "value = self._get(key=key, vtype=SessionValueType.BLOB)",
        ]
        for line in lines:
            matched = any(
                re.search(p["pattern"], line) for p in scanner.SECRET_PATTERNS
            )
            assert not matched, f"expected no match for: {line!r}"

    def test_typescript_generic_type_parameter_is_not_a_secret(self):
        """The real validation finding: a vendored .d.ts typings file."""
        lines = [
            "    api: DialogInstanceApi<T>",
            "declare type Handler<T> = (api: DialogInstanceApi<T>) => void;",
            "api: DialogInstanceApi<T, U>",
        ]
        for line in lines:
            matched = any(
                re.search(p["pattern"], line) for p in scanner.SECRET_PATTERNS
            )
            assert not matched, f"expected no match for: {line!r}"

    def test_full_scan_of_the_phineas_shaped_file_reports_zero_findings(self):
        """End-to-end, not just the raw pattern -- mirrors real phineas code."""
        content = (
            "def send(postmark_settings):\n"
            '    server_token = postmark_settings["postmark_api_key"]\n'
            "\n"
            "def get_blob(self, key: str):\n"
            "    value = self._get(key=key, vtype=SessionValueType.BLOB)\n"
            "\n"
            "def verify(token, salt):\n"
            "    s = URLSafeTimedSerializer(secret_key, salt=salt)\n"
            "    s.loads(token, max_age=max_age_in_seconds)\n"
        )
        secrets, _ = scanner._scan_single_file(
            "session.py", schema_vars=set(), content=content
        )
        assert secrets == []

    def test_a_bare_secret_ending_a_sentence_still_matches(self):
        """
        A real, unquoted secret can legitimately precede a literal '.' --
        an ordinary sentence-ending period in prose -- and must not be
        suppressed just because '.' is now excluded when it continues into
        an identifier. Only the continuing case (attribute access) is
        excluded, not this one.
        """
        lines = [
            "API_KEY=abcdefghijklmnop1234uvwx.",
            "The API_KEY=abcdefghijklmnop1234uvwx. Please rotate it soon.",
        ]
        for line in lines:
            matched = any(
                re.search(p["pattern"], line) for p in scanner.SECRET_PATTERNS
            )
            assert matched, f"expected a match for: {line!r}"

    def test_a_bare_secret_immediately_continuing_into_an_identifier_is_suppressed(
        self,
    ):
        """The counterpart to the above: '.' followed by more letters IS attribute-access-shaped."""
        line = "API_KEY=abcdefghijklmnop1234uvwx.something"
        matched = any(re.search(p["pattern"], line) for p in scanner.SECRET_PATTERNS)
        assert not matched

    def test_legitimate_generic_assignments_still_match(self):
        """The fix must not weaken real, quoted or unquoted, generic secrets."""
        lines = [
            'API_KEY = "abcdefghijklmnop1234"',
            "API_TOKEN=abcdefghijklmnop1234",
            'AUTH_TOKEN = "abcdefghijklmnop1234"',
            'PASSWORD = "abcdefghijklmnop1234"',
            "DATABASE_PASSWORD=SuperSecretProdPassw0rd",
        ]
        for line in lines:
            matched = any(
                re.search(p["pattern"], line) for p in scanner.SECRET_PATTERNS
            )
            assert matched, f"expected a match for: {line!r}"

    def test_provider_specific_patterns_are_unaffected(self):
        lines = [
            "AKIAABCDEFGHIJKLMNOP",  # AWS Access Key ID
            "ghp_" + "a" * 36,  # GitHub PAT (classic)
            "sk_live_" + "a" * 24,  # Stripe secret key
        ]
        for line in lines:
            matched = any(
                re.search(p["pattern"], line) for p in scanner.SECRET_PATTERNS
            )
            assert matched, f"expected a match for: {line!r}"


class TestGenericApiKeyQuotedMinimumLoweredToEight:
    """
    Milestone 4 recall fix, real-world validation (Zeus, Issuebear, Issuebear
    Management): the quoted branch's 16-char minimum missed real, hardcoded
    short passwords -- confirmed via literal MYSQL_PASSWORD/MYSQL_ROOT_PASSWORD
    values (8 and 13 chars) in committed docker-compose files, identically in
    three separate repositories. Lowered to 8, the smallest minimum that
    catches both confirmed cases. The unquoted branch is deliberately
    untouched -- see TestGenericApiKeyUnquotedMinimumUnaffectedByQuotedFix.
    """

    def test_eight_char_quoted_password_is_now_detected(self):
        line = 'MYSQL_PASSWORD: "8charpw1"'
        matched = any(re.search(p["pattern"], line) for p in scanner.SECRET_PATTERNS)
        assert matched

    def test_thirteen_char_quoted_password_is_now_detected(self):
        line = 'MYSQL_ROOT_PASSWORD: "root13charpw"'
        matched = any(re.search(p["pattern"], line) for p in scanner.SECRET_PATTERNS)
        assert matched

    def test_representative_quoted_lengths_between_new_and_old_minimum(self):
        """9, 12, and 15 characters -- all below the old 16-char floor."""
        lines = [
            'API_KEY = "aZ3x9Qk2p"',
            'TOKEN = "aZ3x9Qk2Lp7m"',
            'SECRET = "aZ3x9Qk2Lp7mB4v"',
        ]
        for line in lines:
            matched = any(
                re.search(p["pattern"], line) for p in scanner.SECRET_PATTERNS
            )
            assert matched, f"expected a match for: {line!r}"

    def test_existing_long_quoted_credentials_still_match(self):
        """Guards against a fix that narrows instead of only lowering the floor."""
        line = 'API_KEY = "sk_live_51H8xJ2aZ9Qk2LpN7mB4vC6"'
        matched = any(re.search(p["pattern"], line) for p in scanner.SECRET_PATTERNS)
        assert matched

    def test_seven_char_quoted_value_below_new_minimum_does_not_match(self):
        line = 'PASSWORD = "abc123z"'
        matched = any(re.search(p["pattern"], line) for p in scanner.SECRET_PATTERNS)
        assert not matched

    def test_short_ordinary_word_does_not_newly_match(self):
        line = 'AUTH_ENABLED = "local"'
        matched = any(re.search(p["pattern"], line) for p in scanner.SECRET_PATTERNS)
        assert not matched

    def test_short_lowercase_configuration_name_does_not_newly_match(self):
        """8 characters, within the new floor, but identifier-shaped -- see the _KEY filter below."""
        line = 'STATE_KEY = "oauth_st"'
        matched = any(re.search(p["pattern"], line) for p in scanner.SECRET_PATTERNS)
        assert not matched

    def test_short_uppercase_configuration_name_does_not_newly_match(self):
        line = 'TOKEN_TYPE = "BEARER_X"'
        matched = any(re.search(p["pattern"], line) for p in scanner.SECRET_PATTERNS)
        assert not matched


class TestGenericApiKeyUnquotedMinimumUnaffectedByQuotedFix:
    """
    The unquoted branch's 16-char minimum and existing exclusions (Milestones
    1-2) must be completely unaffected by lowering the quoted branch's
    minimum -- the two branches are independent alternatives in the same
    non-capturing group.
    """

    def test_short_unquoted_value_still_does_not_match(self):
        line = "MYSQL_PASSWORD=8charpw1"
        matched = any(re.search(p["pattern"], line) for p in scanner.SECRET_PATTERNS)
        assert not matched

    def test_type_annotation_still_not_a_secret(self):
        line = "    work_authorization: WorkAuthorization"
        matched = any(re.search(p["pattern"], line) for p in scanner.SECRET_PATTERNS)
        assert not matched

    def test_function_call_rhs_still_not_a_secret(self):
        line = "cache_key = compute_fit_cache_key(x, y)"
        matched = any(re.search(p["pattern"], line) for p in scanner.SECRET_PATTERNS)
        assert not matched

    def test_bare_identifier_kwarg_still_not_a_secret(self):
        """Same shape as the existing Milestone 2 regression: value ends in ')', not ','."""
        line = "        s.loads(token, max_age=max_age_in_seconds)"
        matched = any(re.search(p["pattern"], line) for p in scanner.SECRET_PATTERNS)
        assert not matched

    def test_typescript_generic_still_not_a_secret(self):
        line = "declare type Handler = (api: DialogInstanceApi<T>) => void;"
        matched = any(re.search(p["pattern"], line) for p in scanner.SECRET_PATTERNS)
        assert not matched

    def test_existing_long_unquoted_credential_still_matches(self):
        line = "API_TOKEN=abcdefghijklmnop1234"
        matched = any(re.search(p["pattern"], line) for p in scanner.SECRET_PATTERNS)
        assert matched


class TestGenericApiKeyExcludesConfigurationLookupKeyNames:
    """
    Milestone 4 precision fix, real-world validation (Zeus): 12 confirmed
    false positives from a '*_KEY'-named constant or keyword argument whose
    quoted value is itself a configuration/lookup NAME, not a credential
    (`SESSION_STATE_KEY = "xero_oauth_state"`,
    `get_config(key="tourradar_username")`). Every one of those 12 values was
    pure ASCII letters/underscores with consistent casing (all-lower or
    all-UPPER) and contained no digit; every real secret checked alongside
    them contained at least one digit, with no exceptions. The filter
    therefore excludes a quoted value ONLY when it is entirely
    lowercase-letters-and-underscores or entirely UPPERCASE-LETTERS-AND-
    UNDERSCORES end to end -- a value with even one digit, mixed case, or any
    other character is unaffected.
    """

    def test_lowercase_lookup_key_name_is_not_a_secret(self):
        line = 'SESSION_STATE_KEY = "xero_oauth_state"'
        matched = any(re.search(p["pattern"], line) for p in scanner.SECRET_PATTERNS)
        assert not matched

    def test_uppercase_lookup_key_name_is_not_a_secret(self):
        line = 'COLUMN_DB_CURRENCY_NAME_KEY = "SOME_CONFIG_NAME_STR"'
        matched = any(re.search(p["pattern"], line) for p in scanner.SECRET_PATTERNS)
        assert not matched

    def test_get_config_keyword_argument_snake_case_name_is_not_a_secret(self):
        line = 'get_config(key="tourradar_username")'
        matched = any(re.search(p["pattern"], line) for p in scanner.SECRET_PATTERNS)
        assert not matched

    def test_get_config_keyword_argument_upper_case_name_is_not_a_secret(self):
        line = 'get_config(key="TOURRADAR_PASSWORD")'
        matched = any(re.search(p["pattern"], line) for p in scanner.SECRET_PATTERNS)
        assert not matched

    def test_single_word_lowercase_name_with_no_underscore_is_not_a_secret(self):
        line = 'SESSION_STATE_KEY = "oauthstate"'
        matched = any(re.search(p["pattern"], line) for p in scanner.SECRET_PATTERNS)
        assert not matched

    def test_real_secret_containing_a_digit_still_matches(self):
        """The one-digit distinction is the entire filter -- confirm it still lets real secrets through."""
        line = 'API_ADMIN_TOKEN = "aZ9x3Qk2Lp7mB4vC6dE1fG8hJ0kL5nP2rS7tU9wX1yZ3aB5c"'
        matched = any(re.search(p["pattern"], line) for p in scanner.SECRET_PATTERNS)
        assert matched

    def test_real_secret_with_digits_and_underscores_still_matches(self):
        line = 'APP_SECRET = "aZ3x9_Qk2Lp7_mB4vC6"'
        matched = any(re.search(p["pattern"], line) for p in scanner.SECRET_PATTERNS)
        assert matched

    def test_aws_style_key_with_digits_still_matches(self):
        line = 'AWS_ACCESS_KEY_ID = "AKIA3X9QK2LP7MB4"'
        matched = any(re.search(p["pattern"], line) for p in scanner.SECRET_PATTERNS)
        assert matched

    def test_mixed_case_no_digit_value_is_not_excluded(self):
        """
        Deliberately narrower than "no digits" alone: a mixed-case, no-digit
        string is NOT one of the two specific all-one-case shapes the filter
        targets, so it is left unaffected (matches, as before this fix).
        """
        line = 'API_KEY = "aZxQkLpmBvCdEfGhJk"'
        matched = any(re.search(p["pattern"], line) for p in scanner.SECRET_PATTERNS)
        assert matched

    def test_end_to_end_scan_does_not_flag_lookup_key_name(self):
        content = 'def f():\n    value = get_config(key="tourradar_username", partner_id=partner_id).value\n'
        secrets, _ = scanner._scan_single_file("tour.py", set(), content=content)
        assert secrets == []

    def test_end_to_end_scan_still_flags_real_secret_assignment(self):
        content = (
            'API_ADMIN_TOKEN = "aZ9x3Qk2Lp7mB4vC6dE1fG8hJ0kL5nP2rS7tU9wX1yZ3aB5c"\n'
        )
        secrets, _ = scanner._scan_single_file("config.py", set(), content=content)
        assert len(secrets) == 1
        assert secrets[0]["secret_type"] == "Generic API Key"


class TestDockerComposeEnvironmentListShortCredentials:
    """
    Milestone 5 recall fix, real-world validation (Zeus, Issuebear, Issuebear
    Management, identically): Docker Compose's own list-style 'environment:'
    syntax (`- MYSQL_PASSWORD=password`) writes literal credentials unquoted,
    so they never reach Generic API Key's quoted branch, and the unquoted
    branch's 16-char floor misses real, short (8/13-char) hardcoded
    passwords. Broadening the generic unquoted floor was rejected -- a real
    counter-example (a CodeBuild buildspec's 'parameter-store:' mapping,
    where the value is an SSM path, not a credential) would become a false
    positive under a general rule. This is narrowly scoped instead: only
    Docker-Compose-named files, only a complete `- KEY=value` line, only
    when KEY contains the same credential keyword Generic API Key already
    requires.
    """

    def test_mysql_password_confirmed_real_world_miss_is_now_detected(self):
        line = "            - MYSQL_PASSWORD=password\n"
        secrets, _ = scanner._scan_single_file(
            "docker-compose.yml", set(), content=line
        )
        assert len(secrets) == 1
        assert secrets[0]["secret_type"] == "Docker Compose Environment Credential"

    def test_mysql_root_password_confirmed_real_world_miss_is_now_detected(self):
        line = "            - MYSQL_ROOT_PASSWORD=root_password\n"
        secrets, _ = scanner._scan_single_file(
            "docker-compose.override.yml", set(), content=line
        )
        assert len(secrets) == 1
        assert secrets[0]["secret_type"] == "Docker Compose Environment Credential"

    def test_short_api_key_value_is_detected(self):
        line = "- API_KEY=short123\n"
        secrets, _ = scanner._scan_single_file(
            "docker-compose.yml", set(), content=line
        )
        assert len(secrets) == 1

    def test_value_lengths_between_four_and_fifteen_are_detected(self):
        lines = [
            "- API_KEY=ab12\n",  # 4 chars, the floor
            "- API_KEY=abcdefghijklm12\n",  # 15 chars
        ]
        for line in lines:
            secrets, _ = scanner._scan_single_file(
                "docker-compose.yml", set(), content=line
            )
            assert len(secrets) == 1, f"expected a match for: {line!r}"

    def test_quoted_whole_entry_is_not_matched_by_this_rule(self):
        """A quoted entire entry is a different, deliberately-out-of-scope shape."""
        line = '            - "MYSQL_PASSWORD=password"\n'
        secrets, _ = scanner._scan_single_file(
            "docker-compose.yml", set(), content=line
        )
        assert secrets == []

    def test_env_var_reference_placeholder_is_not_matched(self):
        line = "            - MYSQL_PASSWORD=${MYSQL_PASSWORD}\n"
        secrets, _ = scanner._scan_single_file(
            "docker-compose.yml", set(), content=line
        )
        assert secrets == []

    def test_yaml_mapping_style_colon_is_not_matched(self):
        """Only the dash-prefixed list form is in scope, not 'KEY: value'."""
        line = "MYSQL_PASSWORD: password\n"
        secrets, _ = scanner._scan_single_file(
            "docker-compose.yml", set(), content=line
        )
        assert secrets == []

    def test_non_credential_environment_entries_are_not_matched(self):
        """
        The exact same environment blocks that contain the real misses also
        contain these two -- neither is a credential, and must not become new
        noise from a keyword-free rule.
        """
        lines = [
            "            - MYSQL_USER=local\n",
            "            - REDIS_REPLICATION_MODE=master\n",
        ]
        for line in lines:
            secrets, _ = scanner._scan_single_file(
                "docker-compose.yml", set(), content=line
            )
            assert secrets == [], f"expected no match for: {line!r}"

    def test_docker_label_entry_is_not_matched(self):
        """A quoted Compose label uses the same list-item syntax but is not in scope."""
        line = '            - "traefik.enable=true"\n'
        secrets, _ = scanner._scan_single_file(
            "docker-compose.yml", set(), content=line
        )
        assert secrets == []

    def test_ordinary_python_source_equivalent_is_unaffected(self):
        """The same KEY=value text, as ordinary Python source, is untouched by this rule."""
        line = 'API_KEY = "short123"\n'
        secrets, _ = scanner._scan_single_file("settings.py", set(), content=line)
        # Still detected -- but via Generic API Key (quoted branch), not this rule.
        assert len(secrets) == 1
        assert secrets[0]["secret_type"] == "Generic API Key"

    def test_lowercase_variable_name_is_not_matched(self):
        line = "            - mysql_password=password\n"
        secrets, _ = scanner._scan_single_file(
            "docker-compose.yml", set(), content=line
        )
        assert secrets == []

    def test_rule_only_applies_to_docker_compose_named_files(self):
        """The identical line in a non-Compose YAML file must not match this rule."""
        line = "            - MYSQL_PASSWORD=password\n"
        secrets, _ = scanner._scan_single_file(
            "buildspec-issuebear.yml", set(), content=line
        )
        assert secrets == []

    def test_buildspec_parameter_store_path_is_not_matched(self):
        """
        The real counter-example that ruled out a general YAML/short-value
        rule: a CodeBuild buildspec's 'parameter-store:' mapping's value is an
        SSM parameter path, not a credential.
        """
        line = "    GEMFURY_TOKEN: /issuebearapp/gemfury_token\n"
        secrets, _ = scanner._scan_single_file(
            "buildspec-issuebear.yml", set(), content=line
        )
        assert secrets == []

    def test_longer_compose_credential_still_caught_by_generic_api_key(self):
        """A value long enough for the existing unquoted floor must still be attributed there, not double-counted."""
        line = (
            "            - GARAGE_DEFAULT_SECRET_KEY=aVeryLongRealisticGarageKey1234\n"
        )
        secrets, _ = scanner._scan_single_file(
            "docker-compose.override.yml", set(), content=line
        )
        assert len(secrets) == 1
        assert secrets[0]["secret_type"] == "Generic API Key"

    def test_value_below_four_char_floor_is_not_matched(self):
        line = "- API_KEY=ab1\n"
        secrets, _ = scanner._scan_single_file(
            "docker-compose.yml", set(), content=line
        )
        assert secrets == []

    def test_end_to_end_scan_matches_the_real_zeus_shape(self):
        content = (
            "    mysql:\n"
            "        environment:\n"
            "            - MYSQL_USER=local\n"
            "            - MYSQL_PASSWORD=password\n"
            "            - MYSQL_DATABASE=\n"
            "            - MYSQL_ROOT_PASSWORD=root_password\n"
        )
        secrets, _ = scanner._scan_single_file(
            "docker-compose.yml", set(), content=content
        )
        assert len(secrets) == 2
        assert {s["line_num"] for s in secrets} == {4, 6}
        assert all(
            s["secret_type"] == "Docker Compose Environment Credential" for s in secrets
        )


class TestGenericApiKeyExcludesWordSegmentedIdentifierValues:
    """
    Milestone 6 precision fix, real-world validation (Zeus, all 5): a
    quoted value that is digit-free and clearly word-segmented --
    camelCase/PascalCase or hyphen-separated words -- is a configuration
    name/UI constant/non-secret salt argument, not a credential. This is
    additive to the existing all-one-case rule (`SESSION_STATE_KEY =
    "xero_oauth_state"` still excluded, unchanged): it targets the same
    "identifier, not token" distinction for values that also happen to mix
    case or use hyphens. Every real secret in the corpus (17 checked)
    contains at least one digit -- since the new lookaheads require the
    ENTIRE value to be letters-only and cleanly segmented, none can match a
    value containing a digit, regardless of casing.
    """

    # --- the 5 confirmed false positives, reconstructed with the same
    # segment-length shape as the real values (never real secret content) ---

    def test_camelcase_config_lookup_value_is_not_a_secret(self):
        """Same shape as common.py:21 -- a dict-key config name, 2 segments of 4-5 letters."""
        line = 'REPORT_KEY = "abcdEfghi"'
        matched = any(re.search(p["pattern"], line) for p in scanner.SECRET_PATTERNS)
        assert not matched

    def test_camelcase_ui_constant_value_is_not_a_secret(self):
        """Same shape as backpack.js:1 -- a UI/layout constant, 2 segments of 8+5 letters."""
        line = 'const layoutKey = "abcdefghEfghi";'
        matched = any(re.search(p["pattern"], line) for p in scanner.SECRET_PATTERNS)
        assert not matched

    def test_kebab_case_config_name_value_is_not_a_secret(self):
        """Same shape as quote_export.py:42 -- a cookie/session name, hyphen-separated."""
        line = 'SESSION_KEY = "session-cookie"'
        matched = any(re.search(p["pattern"], line) for p in scanner.SECRET_PATTERNS)
        assert not matched

    def test_non_secret_salt_argument_hyphenated_is_not_a_secret(self):
        """Same shape as form.py:2359/validate.py:995 -- itsdangerous serializer salt, not a secret."""
        lines = [
            's = Serializer(x, salt="user-login")',
            's = Serializer(x, salt="email-confirm")',
        ]
        for line in lines:
            matched = any(
                re.search(p["pattern"], line) for p in scanner.SECRET_PATTERNS
            )
            assert not matched, f"expected no match for: {line!r}"

    # --- existing BL-124 all-one-case cases, unaffected by this addition ---

    def test_existing_lowercase_lookup_key_name_still_not_a_secret(self):
        line = 'SESSION_STATE_KEY = "xero_oauth_state"'
        matched = any(re.search(p["pattern"], line) for p in scanner.SECRET_PATTERNS)
        assert not matched

    def test_existing_get_config_keyword_lookup_still_not_a_secret(self):
        line = 'get_config(key="TOURRADAR_PASSWORD")'
        matched = any(re.search(p["pattern"], line) for p in scanner.SECRET_PATTERNS)
        assert not matched

    # --- true-positive protection: realistic credential shapes that must
    # NOT be suppressed, proving the filter targets word-segmented
    # identifiers specifically, not mixed-case or hyphenation in general ---

    def test_digit_bearing_mixed_case_credential_still_matches(self):
        line = 'API_KEY = "aZ3x9Qk2Lp7mB4vC6"'
        matched = any(re.search(p["pattern"], line) for p in scanner.SECRET_PATTERNS)
        assert matched

    def test_punctuation_bearing_credential_still_matches(self):
        line = 'SECRET_KEY = "aZ3x9Qk2Lp==mB4vC6"'
        matched = any(re.search(p["pattern"], line) for p in scanner.SECRET_PATTERNS)
        assert matched

    def test_high_entropy_mixed_case_credential_still_matches(self):
        """Synthetic, but shaped like a real Google API key -- 20+ internal case transitions."""
        line = 'GOOGLE_API_KEY = "AIzaSyD8xJ2aZ9Qk2LpN7mB4vC6dE1fG8hJ0k"'
        matched = any(re.search(p["pattern"], line) for p in scanner.SECRET_PATTERNS)
        assert matched

    def test_hyphenated_credential_with_a_digit_still_matches(self):
        """A hyphen alone does not exclude -- only hyphen-separated words with NO digit."""
        line = 'SERVICE_KEY = "svc-key-9f2a1"'
        matched = any(re.search(p["pattern"], line) for p in scanner.SECRET_PATTERNS)
        assert matched

    def test_api_key_style_value_still_matches(self):
        line = 'API_KEY = "sk_live_51H8xJ2aZ9Qk2LpN7mB4v"'
        matched = any(re.search(p["pattern"], line) for p in scanner.SECRET_PATTERNS)
        assert matched

    def test_secret_key_style_value_still_matches(self):
        line = 'SECRET_KEY = "aZ3x9Qk2Lp7mB4vC6dE1fG8h"'
        matched = any(re.search(p["pattern"], line) for p in scanner.SECRET_PATTERNS)
        assert matched

    def test_service_key_style_value_still_matches(self):
        line = 'SERVICE_KEY = "aZ9x3Qk2Lp7mB4vC6"'
        matched = any(re.search(p["pattern"], line) for p in scanner.SECRET_PATTERNS)
        assert matched

    def test_short_credential_in_range_still_matches(self):
        line = 'API_KEY = "aZ3x9Qk2p"'
        matched = any(re.search(p["pattern"], line) for p in scanner.SECRET_PATTERNS)
        assert matched

    def test_aws_style_key_with_digits_still_matches(self):
        line = 'AWS_ACCESS_KEY_ID = "AKIA3X9QK2LP7MB4"'
        matched = any(re.search(p["pattern"], line) for p in scanner.SECRET_PATTERNS)
        assert matched

    def test_compose_credential_unaffected_by_this_quoted_branch_change(self):
        """Docker Compose's unquoted branch is a separate code path -- confirm it's untouched."""
        line = "            - MYSQL_PASSWORD=password\n"
        secrets, _ = scanner._scan_single_file(
            "docker-compose.yml", set(), content=line
        )
        assert len(secrets) == 1
        assert secrets[0]["secret_type"] == "Docker Compose Environment Credential"

    def test_adversarial_single_letter_segment_mixed_case_still_matches(self):
        """
        The exact case that exposed a first-draft grammar bug: alternating
        single-letter case flips (no real word boundary) must not be treated
        as camelCase, or a real high-entropy secret with this shape would be
        suppressed.
        """
        line = 'API_KEY = "aZxQkLpmBvCdEfGhJk"'
        matched = any(re.search(p["pattern"], line) for p in scanner.SECRET_PATTERNS)
        assert matched

    def test_end_to_end_scan_suppresses_camelcase_lookup_and_keeps_real_secret(self):
        content = (
            'REPORT_KEY = "abcdEfghi"\n'
            'API_ADMIN_TOKEN = "aZ9x3Qk2Lp7mB4vC6dE1fG8hJ0kL5nP2rS7tU9wX1yZ3aB5c"\n'
        )
        secrets, _ = scanner._scan_single_file("config.py", set(), content=content)
        assert len(secrets) == 1
        assert secrets[0]["line_num"] == 2


class TestGenericApiKeyFileLocalBareIdentifierSuppression:
    """
    BL-129 (Milestone B): Generic API Key's unquoted branch, on a bare
    identifier value, is suppressed only when THIS file's own enclosing
    function positively shows the identifier is a non-literal reference --
    never on shape, naming convention, or absence of evidence.
    """

    # --- suppress: positive local evidence ---

    def test_local_assignment_from_function_call_is_suppressed(self):
        content = (
            "def f():\n"
            "    cache_key = compute_fit_cache_key()\n"
            "    do_thing(key=cache_key)\n"
        )
        secrets, _ = scanner._scan_single_file("app.py", set(), content=content)
        assert secrets == []

    def test_local_assignment_from_attribute_access_is_suppressed(self):
        content = (
            "def f():\n"
            "    original_password = mfa_user.password\n"
            "    mfa_user.password = original_password\n"
        )
        secrets, _ = scanner._scan_single_file("app.py", set(), content=content)
        assert secrets == []

    def test_local_assignment_from_subscript_is_suppressed(self):
        content = "def f():\n    token = settings['token']\n    call(x=token)\n"
        secrets, _ = scanner._scan_single_file("app.py", set(), content=content)
        assert secrets == []

    def test_local_assignment_from_another_identifier_is_suppressed(self):
        content = (
            "def f():\n"
            "    existing_client = get_client()\n"
            "    api = existing_client\n"
            "    call(x=api)\n"
        )
        secrets, _ = scanner._scan_single_file("app.py", set(), content=content)
        assert secrets == []

    def test_enclosing_function_parameter_is_suppressed(self):
        content = (
            "def test_flask_doc_prefix(test_client_and_api, prefix):\n"
            "    client, api = test_client_and_api\n"
        )
        secrets, _ = scanner._scan_single_file("test_x.py", set(), content=content)
        assert secrets == []

    # --- remain detectable: no positive evidence, or evidence is itself a literal ---

    def test_quoted_literal_assignment_remains_detectable(self):
        content = "def f():\n    api = 'sk_live_123456789abcdefghijklmnopqrstuv'\n"
        secrets, _ = scanner._scan_single_file("app.py", set(), content=content)
        assert len(secrets) == 1
        assert secrets[0]["secret_type"] == "Generic API Key"

    def test_identifier_with_no_local_definition_remains_detectable(self):
        content = "def f():\n    token = external_value_placeholder\n"
        secrets, _ = scanner._scan_single_file("app.py", set(), content=content)
        assert len(secrets) == 1

    def test_imported_identifier_with_no_local_definition_remains_detectable(self):
        content = (
            "from somewhere import imported_value_placeholder\n"
            "def f():\n"
            "    api = imported_value_placeholder\n"
        )
        secrets, _ = scanner._scan_single_file("app.py", set(), content=content)
        assert len(secrets) == 1

    def test_quoted_real_looking_secret_remains_detectable(self):
        content = 'API_SECRET_KEY = "aZ9x3Qk2Lp7mB4vC6dE1fG8hJ0kL5nP2rS7tU9wX1"\n'
        secrets, _ = scanner._scan_single_file("app.py", set(), content=content)
        assert len(secrets) == 1

    def test_provider_specific_detector_unchanged(self):
        line = 'AWS_ACCESS_KEY_ID = "AKIA3X9QK2LP7MB4"'
        matched = any(re.search(p["pattern"], line) for p in scanner.SECRET_PATTERNS)
        assert matched

    def test_compose_detector_unchanged(self):
        content = "            - MYSQL_PASSWORD=password\n"
        secrets, _ = scanner._scan_single_file(
            "docker-compose.yml", set(), content=content
        )
        assert len(secrets) == 1
        assert secrets[0]["secret_type"] == "Docker Compose Environment Credential"

    def test_unrelated_identifier_same_spelling_different_scope_not_suppressed(self):
        """
        A same-named identifier defined non-literally in an UNRELATED,
        non-enclosing function must not supply evidence for a candidate in
        a different function -- evidence must come only from the
        candidate's own enclosing scope.
        """
        content = (
            "def other_func():\n"
            "    helper_name_identifier = get_thing()\n"
            "\n"
            "def target_func():\n"
            "    token = helper_name_identifier\n"
        )
        secrets, _ = scanner._scan_single_file("app.py", set(), content=content)
        assert len(secrets) == 1

    # --- adversarial safety boundary ---

    def test_identifier_whose_only_local_definition_is_itself_a_literal_not_suppressed(
        self,
    ):
        """
        The scanner must NOT treat `password` as safe merely because it is
        locally defined -- its only local definition IS a quoted literal,
        so referencing it elsewhere must still be flagged.
        """
        content = (
            "def f():\n"
            "    password = 'a-real-looking-secret-value-12345'\n"
            "    api = password\n"
        )
        secrets, _ = scanner._scan_single_file("app.py", set(), content=content)
        assert len(secrets) == 1

    def test_external_value_with_no_local_definition_remains_detectable(self):
        content = "def f():\n    api = external_value_with_no_definition_anywhere\n"
        secrets, _ = scanner._scan_single_file("app.py", set(), content=content)
        assert len(secrets) == 1

    def test_module_level_bare_identifier_with_no_enclosing_function_not_suppressed(
        self,
    ):
        """No enclosing function at all -- no scope to draw evidence from."""
        content = "some_existing_module_variable = get_value()\napi = some_existing_module_variable\n"
        secrets, _ = scanner._scan_single_file("app.py", set(), content=content)
        assert len(secrets) == 1

    def test_suppression_does_not_apply_outside_python_files(self):
        """
        Scope restriction: Python Class A/C cases only. Same shape as the
        function-call-assignment case that IS suppressed in a .py file --
        in a .js file, it must remain detected.
        """
        content = (
            "function f() {\n"
            "  const existingLongIdentifierValue = getIt();\n"
            "  doThing({ apikey: existingLongIdentifierValue });\n"
            "}\n"
        )
        secrets, _ = scanner._scan_single_file("app.js", set(), content=content)
        assert len(secrets) == 1

    # --- exact regressions for the investigated Class A/C findings ---
    # Reconstructed with matching structural shape, never using real secret
    # or proprietary content -- consistent with this project's established
    # test-authoring practice for prior scanner milestones.

    def test_regression_password_reassigned_from_local_variable(self):
        """test_mfa_trusted_device.py:82 shape."""
        content = (
            "def test_mfa_flow():\n"
            "    original_password = mfa_user.password\n"
            "    mfa_user.password = original_password\n"
        )
        secrets, _ = scanner._scan_single_file("test_mfa.py", set(), content=content)
        assert secrets == []

    def test_regression_session_key_kwarg_from_local_variable(self):
        """personal_data.py:1016 shape."""
        content = (
            "def render_page():\n"
            "    safe_session_key = session_key or client_search_data.session_id\n"
            "    return render_template(\n"
            "        'page.html.j2',\n"
            "        session_key=safe_session_key,\n"
            "    )\n"
        )
        secrets, _ = scanner._scan_single_file("views.py", set(), content=content)
        assert secrets == []

    def test_regression_url_for_kwarg_from_attribute_access(self):
        """email.py:64 shape (owning_partner_id)."""
        content = (
            "def send_reset_email():\n"
            "    owning_partner_id = user_detail.partner.owning_partner_id\n"
            "    action_url = url_for(\n"
            "        'login.resetpassword', unique_key=unique_key, partnerid=owning_partner_id\n"
            "    )\n"
        )
        secrets, _ = scanner._scan_single_file("email.py", set(), content=content)
        assert secrets == []

    def test_regression_unique_payment_code_from_request_args(self):
        """unique_payment.py:101 shape."""
        content = (
            "def multipay():\n"
            "    unique_payment_code = request.args.get('unique_key')\n"
            "    return url_for(\n"
            "        'athena.tour_journey.unique_payment.unique_payment_multipay',\n"
            "        unique_key=unique_payment_code,\n"
            "    )\n"
        )
        secrets, _ = scanner._scan_single_file(
            "unique_payment.py", set(), content=content
        )
        assert secrets == []

    def test_regression_tourradar_password_from_config_lookup(self):
        """tour.py:755/964 shape."""
        content = (
            "def upload():\n"
            "    tourradar_password = str(hermes_admin.get_config(key='tourradar_password').value)\n"
            "    exporter.upload_to_api(\n"
            "        username=tourradar_username,\n"
            "        password=tourradar_password,\n"
            "    )\n"
        )
        secrets, _ = scanner._scan_single_file("tour.py", set(), content=content)
        assert secrets == []

    def test_regression_ticket_feedback_secret_key_from_local_call(self):
        """notification_builder.py:108 shape."""
        content = (
            "class NotificationBuilder:\n"
            "    def build(self):\n"
            "        ticket_feedback_secret_key = generate_unique_key('ticket_feedback')\n"
            "        url = url_for(\n"
            "            'customer.ticketfeedback', unique_key=ticket_feedback_secret_key\n"
            "        )\n"
        )
        secrets, _ = scanner._scan_single_file(
            "notification_builder.py", set(), content=content
        )
        assert secrets == []

    def test_regression_login_session_salt_from_config_get(self):
        """user.py:616 shape."""
        content = (
            "def resolve_session():\n"
            "    login_session_salt = app.config.get('LOGIN_SESSION_SALT', 'login-session')\n"
            "    login_session_id = app.serializer.loads(\n"
            "        login_session_token, salt=login_session_salt, max_age=None\n"
            "    )\n"
        )
        secrets, _ = scanner._scan_single_file("user.py", set(), content=content)
        assert secrets == []

    def test_regression_pytest_fixture_tuple_unpack(self):
        """test_plugin_flask_blueprint.py:237 / dry_plugin_flask.py:86 shape."""
        content = (
            "def test_flask_doc_prefix(test_client_and_api, prefix):\n"
            "    client, api = test_client_and_api\n"
            "    resp = client.get(prefix + '/apidoc/openapi.json')\n"
        )
        secrets, _ = scanner._scan_single_file(
            "test_plugin_flask_blueprint.py", set(), content=content
        )
        assert secrets == []
