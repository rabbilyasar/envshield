# envshield/tests/test_schema_check_usages_cli.py
import json
import os
import subprocess

from typer.testing import CliRunner

from envshield.cli import app

runner = CliRunner()


def _init_repo():
    subprocess.run(["git", "init", "-q"], check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "config", "user.name", "Test"], check=True)


def _commit(message):
    subprocess.run(["git", "add", "-A"], check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], check=True)


def _write(relative, content):
    os.makedirs(os.path.dirname(relative) or ".", exist_ok=True)
    with open(relative, "w") as f:
        f.write(content)


def _single_service_repo():
    _init_repo()
    _write("envshield.yml", "services:\n  api:\n    schema: env.schema.toml\n")
    _write("env.schema.toml", "")
    _commit("init")


class TestRevisionPairing:
    def test_one_argument_is_a_clean_error_not_a_guess(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _single_service_repo()

            result = runner.invoke(app, ["schema", "check-usages", "HEAD"])

            assert result.exit_code == 1
            assert "both revisions, or neither" in result.stdout


class TestDefaultComparisonIsHeadVsWorkingTree:
    def test_uncommitted_new_dependency_is_caught_before_commit(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _single_service_repo()
            _write("app.py", "import os\nx = os.environ.get('FOO')\n")  # uncommitted

            result = runner.invoke(app, ["schema", "check-usages"])

            assert result.exit_code == 1
            assert "FOO" in result.stdout
            assert "missing declaration" in result.stdout

    def test_untracked_new_file_is_caught_before_commit(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _single_service_repo()
            _write("brand_new.py", "import os\nx = os.environ.get('UNTRACKED')\n")

            result = runner.invoke(app, ["schema", "check-usages"])

            assert result.exit_code == 1
            assert "UNTRACKED" in result.stdout

    def test_a_dependency_already_declared_exits_zero(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _init_repo()
            _write("envshield.yml", "services:\n  api:\n    schema: env.schema.toml\n")
            _write("env.schema.toml", '[FOO]\ndescription = "x"\n')
            _commit("init")
            _write("app.py", "import os\nx = os.environ.get('FOO')\n")

            result = runner.invoke(app, ["schema", "check-usages"])

            assert result.exit_code == 0
            assert "FOO" in result.stdout
            assert "declared" in result.stdout

    def test_no_changes_at_all_exits_zero(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _single_service_repo()

            result = runner.invoke(app, ["schema", "check-usages"])

            assert result.exit_code == 0
            assert "No new source dependencies" in result.stdout


class TestExplicitTwoRevisionForm:
    def test_a_dependency_added_between_two_commits_is_caught(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _single_service_repo()
            _write("app.py", "import os\n")
            _commit("v1: no usages")
            _write("app.py", "import os\nx = os.environ.get('FOO')\n")
            _commit("v2: adds FOO")

            result = runner.invoke(app, ["schema", "check-usages", "HEAD~1", "HEAD"])

            assert result.exit_code == 1
            assert "FOO" in result.stdout

    def test_no_changes_between_head_and_itself_exits_zero(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _single_service_repo()
            _write("app.py", "import os\nx = os.environ.get('FOO')\n")
            _commit("has FOO")

            result = runner.invoke(app, ["schema", "check-usages", "HEAD", "HEAD"])

            assert result.exit_code == 0

    def test_unresolvable_revision_exits_nonzero_with_clear_error(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _single_service_repo()

            result = runner.invoke(
                app, ["schema", "check-usages", "not-a-real-revision", "HEAD"]
            )

            assert result.exit_code == 1


class TestJsonOutput:
    def test_json_shape_matches_schema_diff_convention(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _single_service_repo()
            _write("app.py", "import os\n")
            _commit("v1: no usages")
            _write("app.py", "import os\nx = os.environ.get('FOO')\n")
            _commit("v2: adds FOO")

            result = runner.invoke(
                app, ["schema", "check-usages", "HEAD~1", "HEAD", "--json"]
            )

            payload = json.loads(result.stdout)
            assert payload["has_missing_declarations"] is True
            assert payload["service"] == "api"
            assert payload["revision_a"] == "HEAD~1"
            assert payload["revision_b"] == "HEAD"
            change = next(c for c in payload["changes"] if c["variable"] == "FOO")
            assert change["category"] == "missing_declaration"
            assert change["access_type"] == "os.environ.get"

    def test_json_output_has_no_rich_table_noise(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _single_service_repo()

            result = runner.invoke(app, ["schema", "check-usages", "--json"])

            json.loads(result.stdout)

    def test_json_never_contains_a_value_shaped_key(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _single_service_repo()
            _write("app.py", "import os\n")
            _commit("v1")
            _write("app.py", "import os\nx = os.environ.get('SECRET_KEY')\n")
            _commit("v2")

            result = runner.invoke(
                app, ["schema", "check-usages", "HEAD~1", "HEAD", "--json"]
            )

            payload = json.loads(result.stdout)
            change = payload["changes"][0]
            assert not any(
                "value" in k.lower() or "default" in k.lower() for k in change
            )


class TestMultiServiceFileOwnership:
    def _multi_service_repo(self):
        _init_repo()
        _write(
            "envshield.yml",
            "services:\n"
            "  api:\n    schema: services/api/env.schema.toml\n"
            "  web:\n    schema: services/web/env.schema.toml\n",
        )
        _write("services/api/env.schema.toml", "")
        _write("services/web/env.schema.toml", "")
        _commit("init")

    def test_explicit_service_only_checks_that_services_own_files(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            self._multi_service_repo()
            _write(
                "services/web/app.py",
                "import os\nx = os.environ.get('WEB_ONLY')\n",
            )

            result = runner.invoke(app, ["schema", "check-usages", "--service", "api"])

            assert result.exit_code == 0
            assert "WEB_ONLY" not in result.stdout

    def test_a_change_in_the_targeted_services_directory_is_reported(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            self._multi_service_repo()
            _write(
                "services/api/app.py",
                "import os\nx = os.environ.get('API_ONLY')\n",
            )

            result = runner.invoke(app, ["schema", "check-usages", "--service", "api"])

            assert result.exit_code == 1
            assert "API_ONLY" in result.stdout

    def test_no_service_and_no_tty_fails_clearly_instead_of_scanning_everything(
        self, tmp_path
    ):
        """
        Locked decision: never silently loop over every service. Without
        --service, in a non-interactive run (no TTY, e.g. CI), this must
        fail with a clear error rather than pick a service (or, worse,
        check every service's files against one arbitrary schema).
        """
        with runner.isolated_filesystem(temp_dir=tmp_path):
            self._multi_service_repo()

            result = runner.invoke(app, ["schema", "check-usages"])

            assert result.exit_code == 1
            assert "--service" in result.stdout


class TestUnknownService:
    def test_unknown_service_is_a_clean_error(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _single_service_repo()

            result = runner.invoke(
                app, ["schema", "check-usages", "--service", "does-not-exist"]
            )

            assert result.exit_code == 1


class TestSymlinkHardening:
    """
    End-to-end coverage for the reproduced trust-boundary fix: a symlink
    sitting in the working tree must never cause 'schema check-usages' to
    read (or report a finding sourced from) content outside the project,
    and the '--json' contract (stdout is exactly one JSON document) must
    hold even when a symlink is skipped and warned about.
    """

    OUTSIDE_VARIABLE = "SUPER_SECRET_OUTSIDE_VAR"

    def _make_outside_source_file(self, tmp_path):
        outside_dir = tmp_path.parent / f"{tmp_path.name}_outside"
        outside_dir.mkdir(exist_ok=True)
        target = outside_dir / "leak.py"
        target.write_text(
            f"import os\n{self.OUTSIDE_VARIABLE} = os.environ.get('{self.OUTSIDE_VARIABLE}')\n"
        )
        return target

    def test_a_symlink_never_produces_a_finding_from_outside_the_project(
        self, tmp_path
    ):
        with runner.isolated_filesystem(temp_dir=tmp_path) as td:
            _single_service_repo()
            target = self._make_outside_source_file(tmp_path)
            os.symlink(target, os.path.join(td, "evil.py"))

            result = runner.invoke(app, ["schema", "check-usages"])

            assert result.exit_code == 0
            assert self.OUTSIDE_VARIABLE not in result.stdout

    def test_json_output_stays_pure_even_when_a_symlink_is_skipped(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path) as td:
            _single_service_repo()
            target = self._make_outside_source_file(tmp_path)
            os.symlink(target, os.path.join(td, "evil.py"))

            result = runner.invoke(app, ["schema", "check-usages", "--json"])

            payload = json.loads(result.stdout)
            assert payload["has_missing_declarations"] is False
            assert self.OUTSIDE_VARIABLE not in result.stdout
