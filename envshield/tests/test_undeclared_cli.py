# envshield/tests/test_undeclared_cli.py
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

            result = runner.invoke(app, ["undeclared", "HEAD"])

            assert result.exit_code == 1
            assert "both revisions, or neither" in result.stdout


class TestDefaultComparisonIsHeadVsWorkingTree:
    def test_uncommitted_new_dependency_is_caught_before_commit(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _single_service_repo()
            _write("app.py", "import os\nx = os.environ.get('FOO')\n")  # uncommitted

            result = runner.invoke(app, ["undeclared"])

            assert result.exit_code == 1
            assert "FOO" in result.stdout
            assert "missing declaration" in result.stdout

    def test_untracked_new_file_is_caught_before_commit(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _single_service_repo()
            _write("brand_new.py", "import os\nx = os.environ.get('UNTRACKED')\n")

            result = runner.invoke(app, ["undeclared"])

            assert result.exit_code == 1
            assert "UNTRACKED" in result.stdout

    def test_a_dependency_already_declared_exits_zero(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _init_repo()
            _write("envshield.yml", "services:\n  api:\n    schema: env.schema.toml\n")
            _write("env.schema.toml", '[FOO]\ndescription = "x"\n')
            _commit("init")
            _write("app.py", "import os\nx = os.environ.get('FOO')\n")

            result = runner.invoke(app, ["undeclared"])

            assert result.exit_code == 0
            assert "FOO" in result.stdout
            assert "declared" in result.stdout

    def test_no_changes_at_all_exits_zero(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _single_service_repo()

            result = runner.invoke(app, ["undeclared"])

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

            result = runner.invoke(app, ["undeclared", "HEAD~1", "HEAD"])

            assert result.exit_code == 1
            assert "FOO" in result.stdout

    def test_no_changes_between_head_and_itself_exits_zero(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _single_service_repo()
            _write("app.py", "import os\nx = os.environ.get('FOO')\n")
            _commit("has FOO")

            result = runner.invoke(app, ["undeclared", "HEAD", "HEAD"])

            assert result.exit_code == 0

    def test_a_file_move_does_not_falsely_report_its_usages_as_new(self, tmp_path):
        """
        Regression: git_utils.list_changed_files passes --no-renames, so a
        moved file shows up as its old path deleted and its new path
        added. Before the variable-only identity fix, every usage in the
        moved file was reported as newly introduced even though nothing
        about the dependency itself changed.
        """
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _single_service_repo()
            _write("old_location/app.py", "import os\nx = os.environ.get('FOO')\n")
            _commit("v1: FOO used in old_location/app.py")
            os.makedirs("new_location", exist_ok=True)
            os.rename("old_location/app.py", "new_location/app.py")
            os.rmdir("old_location")
            _commit("v2: moved to new_location/app.py")

            result = runner.invoke(app, ["undeclared", "HEAD~1", "HEAD"])

            assert result.exit_code == 0
            assert "No new source dependencies" in result.stdout

    def test_unresolvable_revision_exits_nonzero_with_clear_error(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _single_service_repo()

            result = runner.invoke(app, ["undeclared", "not-a-real-revision", "HEAD"])

            assert result.exit_code == 1


class TestJsonOutput:
    def test_json_shape_matches_schema_diff_convention(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _single_service_repo()
            _write("app.py", "import os\n")
            _commit("v1: no usages")
            _write("app.py", "import os\nx = os.environ.get('FOO')\n")
            _commit("v2: adds FOO")

            result = runner.invoke(app, ["undeclared", "HEAD~1", "HEAD", "--json"])

            payload = json.loads(result.stdout)
            # A single-service project resolves to exactly one target, so
            # this stays Milestone 1's exact flat shape -- no 'results' key.
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

            result = runner.invoke(app, ["undeclared", "--json"])

            json.loads(result.stdout)

    def test_json_never_contains_a_value_shaped_key(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _single_service_repo()
            _write("app.py", "import os\n")
            _commit("v1")
            _write("app.py", "import os\nx = os.environ.get('SECRET_KEY')\n")
            _commit("v2")

            result = runner.invoke(app, ["undeclared", "HEAD~1", "HEAD", "--json"])

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

            result = runner.invoke(app, ["undeclared", "--service", "api"])

            assert result.exit_code == 0
            assert "WEB_ONLY" not in result.stdout

    def test_a_change_in_the_targeted_services_directory_is_reported(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            self._multi_service_repo()
            _write(
                "services/api/app.py",
                "import os\nx = os.environ.get('API_ONLY')\n",
            )

            result = runner.invoke(app, ["undeclared", "--service", "api"])

            assert result.exit_code == 1
            assert "API_ONLY" in result.stdout

    def test_explicit_service_in_multi_service_project_keeps_flat_json_shape(
        self, tmp_path
    ):
        """
        The JSON shape is decided by how many targets actually resolve
        (len(targets) == 1), not by project topology -- --service pins a
        multi-service project down to one target, so it must keep
        Milestone 1's flat contract exactly like a single-service project
        does, never the multi-service 'results' wrapper.
        """
        with runner.isolated_filesystem(temp_dir=tmp_path):
            self._multi_service_repo()
            _write(
                "services/api/app.py",
                "import os\nx = os.environ.get('API_ONLY')\n",
            )

            result = runner.invoke(app, ["undeclared", "--service", "api", "--json"])

            payload = json.loads(result.stdout)
            assert "results" not in payload
            assert payload["service"] == "api"
            assert payload["has_missing_declarations"] is True
            assert {c["variable"] for c in payload["changes"]} == {"API_ONLY"}

    def test_single_target_failure_keeps_the_exact_milestone_one_error_shape(
        self, tmp_path
    ):
        """
        A single resolved target that fails during discovery/schema
        loading (here: a malformed schema TOML) must reproduce Milestone
        1's bare two-key error contract exactly -- no 'service' key, no
        'results' wrapper -- distinct from a multi-service error entry,
        which is keyed by service inside 'results'.
        """
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _init_repo()
            _write("envshield.yml", "services:\n  api:\n    schema: env.schema.toml\n")
            _write("env.schema.toml", "this is not valid toml [[[")
            _commit("init")

            result = runner.invoke(app, ["undeclared", "--json"])

            assert result.exit_code == 1
            payload = json.loads(result.stdout)
            assert payload == {
                "has_missing_declarations": False,
                "error": payload["error"],
            }
            assert payload["error"]

    def test_no_service_in_a_multi_service_project_now_checks_every_service(
        self, tmp_path
    ):
        """
        Milestone 2 reversal of the Milestone 1 lock (which this test used
        to assert the opposite of): 'schema diff' already loops over every
        configured service via service_manager.resolve_targets when
        --service is omitted. The Milestone 1 restriction existed because
        file-ownership filtering (service_dir_contains) didn't exist yet;
        it does now, so looping no longer risks checking one service's
        files against another's schema -- see
        test_a_missing_declaration_in_one_service_is_not_attributed_to_another
        for direct proof of that. This is a deliberate behavior change, not
        a bug fix: omitting --service in a multi-service project used to be
        a hard error, and now runs every service instead.
        """
        with runner.isolated_filesystem(temp_dir=tmp_path):
            self._multi_service_repo()
            _write(
                "services/api/app.py",
                "import os\nx = os.environ.get('API_ONLY')\n",
            )

            result = runner.invoke(app, ["undeclared"])

            assert result.exit_code == 1
            assert "API_ONLY" in result.stdout
            # Both services rendered under their own header -- proof this
            # looped over every service rather than picking just one.
            assert "── api ──" in result.stdout
            assert "── web ──" in result.stdout

    def test_a_missing_declaration_in_one_service_is_not_attributed_to_another(
        self, tmp_path
    ):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            self._multi_service_repo()
            _write(
                "services/api/app.py",
                "import os\nx = os.environ.get('API_ONLY')\n",
            )
            _write(
                "services/web/env.schema.toml",
                '[WEB_DECLARED]\ndescription = "x"\n',
            )
            _write(
                "services/web/app.py",
                "import os\nx = os.environ.get('WEB_DECLARED')\n",
            )

            result = runner.invoke(app, ["undeclared", "--json"])

            payload = json.loads(result.stdout)
            api_result = next(r for r in payload["results"] if r["service"] == "api")
            web_result = next(r for r in payload["results"] if r["service"] == "web")

            assert {c["variable"] for c in api_result["changes"]} == {"API_ONLY"}
            assert {c["variable"] for c in web_result["changes"]} == {"WEB_DECLARED"}
            assert api_result["has_missing_declarations"] is True
            assert web_result["has_missing_declarations"] is False

    def test_multi_service_human_readable_output_labels_each_service(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            self._multi_service_repo()
            _write(
                "services/api/app.py",
                "import os\nx = os.environ.get('API_ONLY')\n",
            )

            result = runner.invoke(app, ["undeclared"])

            assert "── api ──" in result.stdout
            assert "── web ──" in result.stdout

    def test_has_missing_declarations_false_when_no_service_has_one(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            self._multi_service_repo()

            result = runner.invoke(app, ["undeclared", "--json"])

            payload = json.loads(result.stdout)
            assert result.exit_code == 0
            assert payload["has_missing_declarations"] is False
            assert payload["results"] == [
                {"service": "api", "has_missing_declarations": False, "changes": []},
                {"service": "web", "has_missing_declarations": False, "changes": []},
            ]

    def test_a_broken_service_does_not_silence_a_healthy_ones_finding(self, tmp_path):
        """
        Locks down the per-service error isolation Milestone 2 introduced
        (mirroring schema_diff's own had_error policy): one service's
        schema failing to load must not hide another service's real
        finding, must not be silently converted into a clean result, and
        must still force a non-zero exit even though the other service is
        otherwise fine.
        """
        with runner.isolated_filesystem(temp_dir=tmp_path):
            self._multi_service_repo()
            _write("services/web/env.schema.toml", "this is not valid toml [[[")
            _write(
                "services/api/app.py",
                "import os\nx = os.environ.get('API_ONLY')\n",
            )

            result = runner.invoke(app, ["undeclared", "--json"])

            assert result.exit_code == 1
            payload = json.loads(result.stdout)
            assert payload["has_missing_declarations"] is True

            api_result = next(r for r in payload["results"] if r["service"] == "api")
            web_result = next(r for r in payload["results"] if r["service"] == "web")

            # The healthy service is still checked, finding intact.
            assert api_result["has_missing_declarations"] is True
            assert {c["variable"] for c in api_result["changes"]} == {"API_ONLY"}

            # The broken service is represented explicitly, not swallowed
            # into a clean result and not merged into api's entry.
            assert "error" in web_result
            assert web_result["error"]
            assert "has_missing_declarations" not in web_result
            assert "changes" not in web_result

    def test_single_service_project_shows_no_per_service_header(self, tmp_path):
        """
        Single-service projects must behave exactly as before Milestone 2:
        resolve_targets degrades to the one configured service, so the
        multi-service header (only printed when more than one target is
        resolved) never appears.
        """
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _single_service_repo()
            _write("app.py", "import os\nx = os.environ.get('FOO')\n")

            result = runner.invoke(app, ["undeclared"])

            # Rich's own table borders also use "──", so check for the
            # specific service-header text rather than the character.
            assert "── api ──" not in result.stdout


class TestUnknownService:
    def test_unknown_service_is_a_clean_error(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _single_service_repo()

            result = runner.invoke(app, ["undeclared", "--service", "does-not-exist"])

            assert result.exit_code == 1


class TestSymlinkHardening:
    """
    End-to-end coverage for the reproduced trust-boundary fix: a symlink
    sitting in the working tree must never cause 'undeclared' to
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

            result = runner.invoke(app, ["undeclared"])

            assert result.exit_code == 0
            assert self.OUTSIDE_VARIABLE not in result.stdout

    def test_json_output_stays_pure_even_when_a_symlink_is_skipped(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path) as td:
            _single_service_repo()
            target = self._make_outside_source_file(tmp_path)
            os.symlink(target, os.path.join(td, "evil.py"))

            result = runner.invoke(app, ["undeclared", "--json"])

            payload = json.loads(result.stdout)
            assert payload["has_missing_declarations"] is False
            assert self.OUTSIDE_VARIABLE not in result.stdout
