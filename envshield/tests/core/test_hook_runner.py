# envshield/tests/core/test_hook_runner.py
"""
Shared system schema, Phase 5: hooks. The installed hook is a constant
shim; which schemas and services it covers is resolved from the current
envshield.yml each time it runs (hooks_manager.run_pre_commit /
run_post_merge), from the schema-file closure config_manager.get_schema_files
returns.
"""

import os
import subprocess

import pytest
from typer.testing import CliRunner

from envshield.cli import app
from envshield.config import manager as config_manager
from envshield.core import hooks_manager, scanner
from envshield.core.exceptions import (
    SchemaNotFoundError,
    SchemaParseError,
    ServiceConfigError,
    UnsafePathError,
)

runner = CliRunner()

SHARED = (
    "services:\n"
    "  api:\n    schema: env.schema.toml\n    dir: api\n"
    "  worker:\n    schema: env.schema.toml\n    dir: worker\n"
)
SHARED_SCHEMA = '[LOG_LEVEL]\ndefaultValue = "info"\n'


def _write(path, content=""):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


def _git(*args):
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True)


def _init_repo():
    _git("init", "-q")
    _git("config", "user.email", "test@example.com")
    _git("config", "user.name", "Test")


def _abs(*paths):
    return [os.path.abspath(p) for p in paths]


def _real(path):
    return os.path.realpath(path)


class TestGetSchemaFiles:
    def test_a_schema_without_extends_is_just_itself(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write("env.schema.toml", "[A]\n")
        assert config_manager.get_schema_files("env.schema.toml") == [
            _real("env.schema.toml")
        ]

    def test_direct_extends(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write("base.toml", "[A]\n")
        _write("env.schema.toml", 'extends = "base.toml"\n[B]\n')
        assert config_manager.get_schema_files("env.schema.toml") == [
            _real("env.schema.toml"),
            _real("base.toml"),
        ]

    def test_multi_level_extends(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write("shared/root.toml", "[A]\n")
        _write("shared/mid.toml", 'extends = "root.toml"\n[B]\n')
        _write("svc/env.schema.toml", 'extends = "../shared/mid.toml"\n[C]\n')
        assert config_manager.get_schema_files("svc/env.schema.toml") == [
            _real("svc/env.schema.toml"),
            _real("shared/root.toml"),
            _real("shared/mid.toml"),
        ]

    def test_list_form_extends_and_a_shared_base_listed_once(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        _write("common.toml", "[A]\n")
        _write("a.toml", 'extends = "common.toml"\n[B]\n')
        _write("b.toml", 'extends = "common.toml"\n[C]\n')
        _write("env.schema.toml", 'extends = ["a.toml", "b.toml"]\n[D]\n')
        assert config_manager.get_schema_files("env.schema.toml") == [
            _real("env.schema.toml"),
            _real("common.toml"),
            _real("a.toml"),
            _real("b.toml"),
        ]

    def test_symlinked_base_resolves_to_its_real_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write("real/base.toml", "[A]\n")
        os.symlink("real/base.toml", "link.toml")
        _write("env.schema.toml", 'extends = "link.toml"\n[B]\n')
        assert config_manager.get_schema_files("env.schema.toml") == [
            _real("env.schema.toml"),
            str(tmp_path.resolve() / "real" / "base.toml"),
        ]

    def test_symlinked_base_outside_the_project_is_rejected(
        self, tmp_path, monkeypatch
    ):
        outside = tmp_path / "outside.toml"
        outside.write_text("[A]\n")
        project = tmp_path / "project"
        project.mkdir()
        monkeypatch.chdir(project)
        os.symlink(outside, "link.toml")
        _write("env.schema.toml", 'extends = "link.toml"\n')
        with pytest.raises(UnsafePathError):
            config_manager.get_schema_files("env.schema.toml")

    def test_cycle_is_rejected(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write("a.toml", 'extends = "b.toml"\n')
        _write("b.toml", 'extends = "a.toml"\n')
        with pytest.raises(SchemaParseError, match="circular"):
            config_manager.get_schema_files("a.toml")

    def test_missing_base_is_an_error(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write("env.schema.toml", 'extends = "gone.toml"\n')
        with pytest.raises(SchemaNotFoundError):
            config_manager.get_schema_files("env.schema.toml")


class TestAffectedServices:
    def test_both_users_of_a_shared_schema_are_affected(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write("envshield.yml", SHARED)
        _write("env.schema.toml", SHARED_SCHEMA)
        assert hooks_manager.affected_services(_abs("env.schema.toml")) == [
            "api",
            "worker",
        ]

    def test_changing_a_base_affects_users_of_the_child(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write(
            "envshield.yml",
            "services:\n"
            "  api:\n    schema: api/env.schema.toml\n"
            "  web:\n    schema: web/env.schema.toml\n",
        )
        _write("shared/base.toml", "[A]\n")
        _write("api/env.schema.toml", 'extends = "../shared/base.toml"\n[B]\n')
        _write("web/env.schema.toml", "[C]\n")
        assert hooks_manager.affected_services(_abs("shared/base.toml")) == ["api"]

    def test_multi_level_base_change_reaches_the_leaf_schemas_users(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        _write("envshield.yml", SHARED)
        _write("root.toml", "[A]\n")
        _write("mid.toml", 'extends = "root.toml"\n')
        _write("env.schema.toml", 'extends = "mid.toml"\n' + SHARED_SCHEMA)
        assert hooks_manager.affected_services(_abs("root.toml")) == [
            "api",
            "worker",
        ]

    def test_list_form_extends_each_base_counts(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write("envshield.yml", "services:\n  app:\n    schema: env.schema.toml\n")
        _write("a.toml", "[A]\n")
        _write("b.toml", "[B]\n")
        _write("env.schema.toml", 'extends = ["a.toml", "b.toml"]\n')
        assert hooks_manager.affected_services(_abs("a.toml")) == ["app"]
        assert hooks_manager.affected_services(_abs("b.toml")) == ["app"]

    def test_changed_envshield_yml_affects_every_service(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write(
            "envshield.yml",
            "services:\n"
            "  web:\n    schema: web/env.schema.toml\n"
            "  api:\n    schema: api/env.schema.toml\n",
        )
        assert hooks_manager.affected_services(_abs("envshield.yml")) == [
            "web",
            "api",
        ]

    def test_an_unrelated_file_affects_nothing(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write("envshield.yml", SHARED)
        _write("env.schema.toml", SHARED_SCHEMA)
        assert hooks_manager.affected_services(_abs("api/main.py", "README.md")) == []

    def test_a_schema_path_that_is_a_substring_of_another_does_not_cross_trigger(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        _write(
            "envshield.yml",
            "services:\n"
            "  api:\n    schema: api/env.schema.toml\n"
            "  internal-api:\n    schema: internal-api/env.schema.toml\n",
        )
        _write("api/env.schema.toml", "[A]\n")
        _write("internal-api/env.schema.toml", "[B]\n")
        assert hooks_manager.affected_services(
            _abs("internal-api/env.schema.toml")
        ) == ["internal-api"]

    def test_order_follows_envshield_yml_not_the_changed_paths(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        _write(
            "envshield.yml",
            "services:\n"
            "  zeta:\n    schema: z/env.schema.toml\n"
            "  alpha:\n    schema: a/env.schema.toml\n",
        )
        _write("z/env.schema.toml", "[A]\n")
        _write("a/env.schema.toml", "[B]\n")
        changed = _abs("a/env.schema.toml", "z/env.schema.toml")
        assert hooks_manager.affected_services(changed) == ["zeta", "alpha"]

    def test_a_schema_that_does_not_exist_yet_still_covers_its_own_path(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        _write("envshield.yml", "services:\n  app:\n    schema: env.schema.toml\n")
        assert hooks_manager.affected_services(_abs("env.schema.toml")) == ["app"]

    def test_git_relative_paths_under_a_subdirectory_match(self, tmp_path, monkeypatch):
        """Changed paths come from git (repo-root relative, joined to the
        root); schema paths and extends come from envshield.yml and the
        schema. Both must land on the same real path."""
        monkeypatch.chdir(tmp_path)
        _write(
            "envshield.yml",
            "services:\n  api:\n    schema: ./services/api/../api/env.schema.toml\n",
        )
        _write("services/shared/base.toml", "[A]\n")
        _write(
            "services/api/env.schema.toml",
            'extends = "../shared/base.toml"\n[B]\n',
        )
        root = str(tmp_path)
        assert hooks_manager.affected_services(
            [os.path.join(root, "services/shared/base.toml")]
        ) == ["api"]
        assert hooks_manager.affected_services(
            [os.path.join(root, "services/api/env.schema.toml")]
        ) == ["api"]

    def test_a_service_whose_schema_cannot_be_resolved_fails_closed(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        _write(
            "envshield.yml",
            "services:\n  app:\n    schema: env.schema.toml\n  broken:\n    description: x\n",
        )
        _write("env.schema.toml", "[A]\n")
        with pytest.raises(ServiceConfigError):
            hooks_manager.affected_services(_abs("env.schema.toml"))

    def test_a_broken_extends_chain_fails_closed(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write("envshield.yml", "services:\n  app:\n    schema: env.schema.toml\n")
        _write("env.schema.toml", 'extends = "gone.toml"\n')
        with pytest.raises(SchemaNotFoundError):
            hooks_manager.affected_services(_abs("README.md"))


class TestConstantShim:
    def test_hook_bytes_do_not_change_when_a_service_is_added(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        before = (
            scanner._generate_pre_commit_hook_content(),
            scanner._generate_post_merge_hook_content(),
        )
        _write("envshield.yml", SHARED)
        _write("env.schema.toml", SHARED_SCHEMA)
        after = (
            scanner._generate_pre_commit_hook_content(),
            scanner._generate_post_merge_hook_content(),
        )
        assert before == after

    def test_no_project_topology_appears_in_the_hooks(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write(
            "envshield.yml",
            "services:\n"
            "  payments-svc:\n    schema: deep/payments/env.schema.toml\n"
            "    dir: deep/payments\n    example_file: deep/payments/tmpl.env\n",
        )
        for content in (
            scanner._generate_pre_commit_hook_content(),
            scanner._generate_post_merge_hook_content(),
        ):
            for value in ("payments", "deep/", "tmpl.env", "env.schema.toml"):
                assert value not in content
            assert content.startswith("#!/bin/sh\n")
            assert scanner.ENVSHIELD_HOOK_MARKER in content

    def test_shims_invoke_the_runner_for_their_event(self):
        assert (
            "envshield hook run pre-commit"
            in scanner._generate_pre_commit_hook_content()
        )
        assert (
            "envshield hook run post-merge"
            in scanner._generate_post_merge_hook_content()
        )


class TestRunPreCommit:
    """Orchestration, with EnvShield's own sub-commands recorded instead of run."""

    @pytest.fixture
    def calls(self, mocker):
        recorded = []

        def fake(*args, **kwargs):
            recorded.append(args)
            return 0

        mocker.patch.object(hooks_manager, "_envshield", side_effect=fake)
        return recorded

    def _synced(self, *dirs):
        for d in dirs:
            _write(f"{d}/.env.example", "LOG_LEVEL=\n")

    def test_scan_always_runs_even_with_nothing_registered(
        self, tmp_path, monkeypatch, calls
    ):
        monkeypatch.chdir(tmp_path)
        _init_repo()
        assert hooks_manager.run_pre_commit() == 0
        assert calls == [("scan", "--staged", "--enforce")]

    def test_a_staged_shared_schema_checks_every_user(
        self, tmp_path, monkeypatch, calls
    ):
        monkeypatch.chdir(tmp_path)
        _init_repo()
        _write("envshield.yml", SHARED)
        _write("env.schema.toml", SHARED_SCHEMA)
        self._synced("api", "worker")
        _git("add", "-A")

        assert hooks_manager.run_pre_commit() == 0
        assert calls[1:] == [
            ("schema", "sync", "--service", "api", "--check"),
            ("schema", "sync", "--service", "worker", "--check"),
        ]

    def test_a_staged_base_schema_checks_the_childs_users(
        self, tmp_path, monkeypatch, calls
    ):
        monkeypatch.chdir(tmp_path)
        _init_repo()
        _write(
            "envshield.yml",
            "services:\n"
            "  api:\n    schema: api/env.schema.toml\n"
            "  web:\n    schema: web/env.schema.toml\n",
        )
        _write("shared/base.toml", "[A]\n")
        _write("api/env.schema.toml", 'extends = "../shared/base.toml"\n')
        _write("web/env.schema.toml", "[B]\n")
        _git("add", "-A")
        _git("commit", "-q", "-m", "base")

        _write("shared/base.toml", "[A]\n[NEW]\n")
        _git("add", "shared/base.toml")

        assert hooks_manager.run_pre_commit() == 0
        assert calls[1:] == [("schema", "sync", "--service", "api", "--check")]

    def test_nothing_schema_related_staged_runs_only_the_scan(
        self, tmp_path, monkeypatch, calls
    ):
        monkeypatch.chdir(tmp_path)
        _init_repo()
        _write("envshield.yml", SHARED)
        _write("env.schema.toml", SHARED_SCHEMA)
        _git("add", "-A")
        _git("commit", "-q", "-m", "base")
        _write("api/main.py", "print(1)\n")
        _git("add", "api/main.py")

        assert hooks_manager.run_pre_commit() == 0
        assert calls == [("scan", "--staged", "--enforce")]

    def test_single_service_behaves_as_before(self, tmp_path, monkeypatch, calls):
        monkeypatch.chdir(tmp_path)
        _init_repo()
        _write("envshield.yml", "services:\n  app:\n    schema: env.schema.toml\n")
        _write("env.schema.toml", SHARED_SCHEMA)
        _write(".env.example", "LOG_LEVEL=\n")
        _git("add", "-A")

        assert hooks_manager.run_pre_commit() == 0
        assert calls == [
            ("scan", "--staged", "--enforce"),
            ("schema", "sync", "--service", "app", "--check"),
        ]

    def test_a_failing_check_blocks(self, tmp_path, monkeypatch, mocker):
        monkeypatch.chdir(tmp_path)
        _init_repo()
        _write("envshield.yml", SHARED)
        _write("env.schema.toml", SHARED_SCHEMA)
        self._synced("api", "worker")
        _git("add", "-A")
        mocker.patch.object(
            hooks_manager,
            "_envshield",
            side_effect=lambda *a, **k: 1 if "worker" in a else 0,
        )
        assert hooks_manager.run_pre_commit() == 1

    def test_an_unaffected_services_unstaged_template_does_not_block(
        self, tmp_path, monkeypatch, capsys
    ):
        """
        Only web's schema is staged; api's template has unrelated unstaged
        edits. Nothing staged affects api, so its template must neither
        block the commit nor be reported. Real subcommands run here.
        """
        monkeypatch.chdir(tmp_path)
        _init_repo()
        _write(
            "envshield.yml",
            "services:\n"
            "  api:\n    schema: api/env.schema.toml\n"
            "  web:\n    schema: web/env.schema.toml\n",
        )
        _write("api/env.schema.toml", '[API_URL]\ndefaultValue = "x"\n')
        _write("web/env.schema.toml", '[WEB_URL]\ndefaultValue = "x"\n')
        _write("api/.env.example", "API_URL=\n")
        _write("web/.env.example", "WEB_URL=\n")
        _git("add", "-A")
        _git("commit", "-q", "-m", "base")

        _write("web/env.schema.toml", '[WEB_URL]\ndefaultValue = "y"\n')
        _git("add", "web/env.schema.toml")
        _write("api/.env.example", "API_URL=\n# local note\n")

        assert hooks_manager.run_pre_commit() == 0
        out = capsys.readouterr().out
        assert "unstaged changes" not in out
        assert "api/.env.example" not in out

    def test_an_unstaged_template_blocks(self, tmp_path, monkeypatch, calls, capsys):
        monkeypatch.chdir(tmp_path)
        _init_repo()
        _write("envshield.yml", "services:\n  app:\n    schema: env.schema.toml\n")
        _write("env.schema.toml", SHARED_SCHEMA)
        _write(".env.example", "LOG_LEVEL=\n")
        _git("add", "-A")
        _write(".env.example", "LOG_LEVEL=\nOTHER=\n")  # synced, not staged

        assert hooks_manager.run_pre_commit() == 1
        assert "unstaged changes" in capsys.readouterr().out

    def test_a_shared_template_is_checked_for_unstaged_changes_once(
        self, tmp_path, monkeypatch, calls, capsys
    ):
        """Two services on one physical .env.example: one file, one
        unstaged-template check -- but each service's own sync check still
        runs, since each checks its own projection's variables. Nothing
        here writes."""
        monkeypatch.chdir(tmp_path)
        _init_repo()
        _write(
            "envshield.yml",
            "services:\n"
            "  api:\n    schema: env.schema.toml\n    dir: api\n"
            "    example_file: app/.env.example\n"
            "  worker:\n    schema: env.schema.toml\n    dir: worker\n"
            "    example_file: app/.env.example\n",
        )
        _write("env.schema.toml", SHARED_SCHEMA)
        _write("app/.env.example", "LOG_LEVEL=\n")
        _git("add", "-A")
        _write("app/.env.example", "LOG_LEVEL=\nX=\n")
        before = open("app/.env.example").read()

        assert hooks_manager.run_pre_commit() == 1
        assert capsys.readouterr().out.count("unstaged changes") == 1
        assert calls[1:] == [
            ("schema", "sync", "--service", "api", "--check"),
            ("schema", "sync", "--service", "worker", "--check"),
        ]
        assert open("app/.env.example").read() == before

    def test_unresolvable_topology_blocks_the_commit(
        self, tmp_path, monkeypatch, calls, capsys
    ):
        monkeypatch.chdir(tmp_path)
        _init_repo()
        _write("envshield.yml", "services:\n  app:\n    schema: env.schema.toml\n")
        _write("env.schema.toml", 'extends = "gone.toml"\n')
        _git("add", "-A")

        assert hooks_manager.run_pre_commit() == 1
        assert "couldn't work out which services" in capsys.readouterr().out

    def test_service_names_reach_subcommands_as_arguments_not_shell(
        self, tmp_path, monkeypatch
    ):
        """No shell is involved anywhere: a hostile service name is one argv
        element, so it can't run anything."""
        monkeypatch.chdir(tmp_path)
        _init_repo()
        sentinel = tmp_path / "pwned"
        name = f"x'; touch {sentinel}; echo '$(touch {sentinel})"
        _write(
            "envshield.yml",
            f'services:\n  "{name}":\n    schema: env.schema.toml\n',
        )
        _write("env.schema.toml", SHARED_SCHEMA)
        _git("add", "-A")

        hooks_manager.run_pre_commit()

        assert not sentinel.exists()


class TestRunPostMerge:
    def test_doctor_runs_for_every_user_of_a_changed_schema_and_never_blocks(
        self, tmp_path, monkeypatch, mocker
    ):
        monkeypatch.chdir(tmp_path)
        _init_repo()
        _write(
            "envshield.yml",
            SHARED + "  web:\n    schema: web/env.schema.toml\n",
        )
        _write("env.schema.toml", SHARED_SCHEMA)
        _write("web/env.schema.toml", "[B]\n")
        _git("add", "-A")
        _git("commit", "-q", "-m", "one")
        _write("env.schema.toml", SHARED_SCHEMA + "[NEW]\n")
        _git("commit", "-q", "-am", "two")

        calls = []
        mocker.patch.object(
            hooks_manager,
            "_envshield",
            side_effect=lambda *a, **k: calls.append(a) or 1,
        )

        assert hooks_manager.run_post_merge() == 0
        assert calls == [
            ("doctor", "--service", "api"),
            ("doctor", "--service", "worker"),
        ]

    def test_resolution_failure_warns_without_blocking(
        self, tmp_path, monkeypatch, mocker, capsys
    ):
        monkeypatch.chdir(tmp_path)
        _init_repo()
        _write("envshield.yml", "services:\n  app:\n    schema: env.schema.toml\n")
        _write("env.schema.toml", 'extends = "gone.toml"\n')
        _write("README.md", "x\n")
        _git("add", "-A")
        _git("commit", "-q", "-m", "one")
        _write("README.md", "y\n")
        _git("commit", "-q", "-am", "two")
        doctor = mocker.patch.object(hooks_manager, "_envshield")

        assert hooks_manager.run_post_merge() == 0
        doctor.assert_not_called()
        assert "couldn't check" in capsys.readouterr().out


class TestLifecycle:
    def test_hook_installed_before_a_service_was_added_is_still_current_and_removable(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        _init_repo()
        _write("envshield.yml", "services:\n  app:\n    schema: env.schema.toml\n")
        assert runner.invoke(app, ["hook", "install", "--yes"]).exit_code == 0

        _write("envshield.yml", SHARED)
        _write("env.schema.toml", SHARED_SCHEMA)

        status = runner.invoke(app, ["hook", "status"])
        assert "older EnvShield" not in status.stdout
        assert "modified" not in status.stdout
        assert "env.schema.toml → api, worker" in status.stdout

        result = runner.invoke(app, ["hook", "remove", "--yes"])
        assert result.exit_code == 0
        assert not os.path.exists(".git/hooks/pre-commit")
        assert not os.path.exists(".git/hooks/post-merge")

    def test_status_lists_extends_bases(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _init_repo()
        _write("envshield.yml", "services:\n  app:\n    schema: env.schema.toml\n")
        _write("base.toml", "[A]\n")
        _write("env.schema.toml", 'extends = "base.toml"\n')

        result = runner.invoke(app, ["hook", "status"])

        assert "env.schema.toml → app" in result.stdout
        assert "extends base.toml" in result.stdout

    def test_foreign_hook_is_not_silently_overwritten(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _init_repo()
        foreign = "#!/bin/sh\nnpm test\n"
        _write(".git/hooks/pre-commit", foreign)

        runner.invoke(app, ["hook", "install", "--yes"])
        status = runner.invoke(app, ["hook", "status"])

        assert open(".git/hooks/pre-commit").read() == foreign
        assert "not installed by EnvShield" in status.stdout

    def test_legacy_hook_is_reported_and_not_silently_upgraded(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        _init_repo()
        legacy = (
            "#!/bin/sh\n\n# Hook installed by EnvShield\n"
            "# Scans staged files for hardcoded secrets AND undeclared environment variables.\n"
            "# M7: --enforce enables interactive override for high-confidence secret findings.\n"
            "envshield scan --staged --enforce\nSTATUS=$?\nexit $STATUS\n"
        )
        _write(".git/hooks/pre-commit", legacy)

        status = runner.invoke(app, ["hook", "status"])
        assert "older EnvShield hook" in status.stdout

        install = runner.invoke(app, ["hook", "install", "--yes"])
        assert "was not installed automatically" in " ".join(install.stdout.split())
        assert open(".git/hooks/pre-commit").read() == legacy

        remove = runner.invoke(app, ["hook", "remove", "--yes"])
        assert remove.exit_code == 0
        assert open(".git/hooks/pre-commit").read() == legacy

    def test_legacy_hook_upgrades_after_an_explicit_confirmation(
        self, tmp_path, monkeypatch, mocker
    ):
        monkeypatch.chdir(tmp_path)
        _init_repo()
        legacy = (
            "#!/bin/sh\n\n# Hook installed by EnvShield\n"
            "# Smart: only runs for a service whose own schema actually changed in this merge.\n"
            "exit 0\n"
        )
        _write(".git/hooks/post-merge", legacy)
        confirm = mocker.patch("questionary.confirm")
        confirm.return_value.ask.return_value = True

        scanner.install_post_merge_hook()

        confirm.assert_called_once()
        assert (
            open(".git/hooks/post-merge").read()
            == scanner._generate_post_merge_hook_content()
        )

    def test_unknown_event_is_rejected(self):
        result = runner.invoke(app, ["hook", "run", "pre-push"])
        assert result.exit_code == 2


class TestRealCommitWithSharedSchema:
    """End to end, through a real installed hook and a real 'git commit'."""

    def test_a_shared_schema_change_is_blocked_when_any_user_is_stale(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        _init_repo()
        _write("envshield.yml", SHARED)
        _write("env.schema.toml", SHARED_SCHEMA)
        _write("api/.env.example", "LOG_LEVEL=\n")
        _write("worker/.env.example", "LOG_LEVEL=\n")
        _git("add", "-A")
        _git("commit", "-q", "-m", "base")
        install = subprocess.run(
            ["envshield", "hook", "install", "--yes"], capture_output=True, text=True
        )
        assert install.returncode == 0, install.stdout + install.stderr

        # A new variable for worker only; api's template stays correct.
        _write(
            "env.schema.toml",
            SHARED_SCHEMA + '\n[QUEUE_URL]\nservices = ["worker"]\n',
        )
        _git("add", "env.schema.toml")
        commit = subprocess.run(
            ["git", "commit", "-m", "add QUEUE_URL"], capture_output=True, text=True
        )

        assert commit.returncode != 0, commit.stdout + commit.stderr
        assert "QUEUE_URL" in commit.stdout + commit.stderr

        _write("worker/.env.example", "LOG_LEVEL=\nQUEUE_URL=\n")
        _git("add", "worker/.env.example")
        commit = subprocess.run(
            ["git", "commit", "-m", "add QUEUE_URL"], capture_output=True, text=True
        )
        assert commit.returncode == 0, commit.stdout + commit.stderr
