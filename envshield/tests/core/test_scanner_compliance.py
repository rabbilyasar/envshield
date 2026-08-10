# envshield/tests/core/test_scanner_compliance.py
import json
import os
import re

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
