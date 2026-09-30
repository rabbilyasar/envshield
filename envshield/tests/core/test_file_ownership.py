# envshield/tests/core/test_file_ownership.py
"""
D-001: config_manager.resolve_file_owner is the one canonical answer to
"which registered service owns this physical file" -- these tests pin its
own rules directly, then prove every real consumer (scanner, explain,
dependency_snapshot) gets the same answer through their normal public
entry points, closing BL-146 (a nested service's files were silently
misattributed to a shorter-matching parent/root directory by explain/
undeclared, while scan already routed them correctly).
"""

import json
import os
import subprocess

import pytest
from typer.testing import CliRunner

from envshield.cli import app
from envshield.config import manager as config_manager
from envshield.core import dependency_snapshot
from envshield.core.exceptions import DuplicateServiceDirError

runner = CliRunner()


def _write(path, content=""):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


def _git(*args):
    subprocess.run(["git", *args], check=True, capture_output=True, text=True)


def _init_repo():
    _git("init", "-q")
    _git("config", "user.email", "test@example.com")
    _git("config", "user.name", "Test")


def _commit(message):
    _git("add", "-A")
    _git("commit", "-q", "-m", message)


def cli_json(*args, code=0):
    result = runner.invoke(app, [*args, "--json"])
    assert result.exit_code == code, (args, result.stdout, result.exception)
    return json.loads(result.stdout)


# -- 1. resolve_file_owner itself: the ten behaviors D-001 requires. --------


class TestResolveFileOwner:
    def _nested_topology(self, tmp_path, monkeypatch):
        """root service (dir '.') + nested 'frontend' service -- the exact
        JossJobs shape BL-146 was reproduced against."""
        monkeypatch.chdir(tmp_path)
        _write(
            "envshield.yml",
            "services:\n"
            "  jossjobs:\n    schema: env.schema.toml\n"
            "  frontend:\n    schema: frontend/env.schema.toml\n",
        )
        _write("env.schema.toml", '[FRONTEND_ORIGIN]\ndefaultValue = "x"\n')
        _write("frontend/env.schema.toml", '[BACKEND_ORIGIN]\ndefaultValue = "y"\n')
        _write("frontend/src/api.ts", "const b = process.env.BACKEND_ORIGIN;\n")

    def test_nested_service_file_resolves_to_the_nested_service(
        self, tmp_path, monkeypatch
    ):
        self._nested_topology(tmp_path, monkeypatch)
        assert config_manager.resolve_file_owner("frontend/src/api.ts") == "frontend"

    def test_root_service_does_not_claim_the_nested_files(self, tmp_path, monkeypatch):
        self._nested_topology(tmp_path, monkeypatch)
        assert config_manager.resolve_file_owner("frontend/src/api.ts") != "jossjobs"

    def test_root_level_file_resolves_to_the_root_service(self, tmp_path, monkeypatch):
        self._nested_topology(tmp_path, monkeypatch)
        _write("app.py", "import os\n")
        assert config_manager.resolve_file_owner("app.py") == "jossjobs"

    def test_sibling_non_nested_services_resolve_independently(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        _write(
            "envshield.yml",
            "services:\n"
            "  api:\n    schema: services/api/env.schema.toml\n"
            "  web:\n    schema: services/web/env.schema.toml\n",
        )
        _write("services/api/env.schema.toml", "")
        _write("services/web/env.schema.toml", "")
        _write("services/api/app.py", "")
        _write("services/web/app.py", "")

        assert config_manager.resolve_file_owner("services/api/app.py") == "api"
        assert config_manager.resolve_file_owner("services/web/app.py") == "web"

    def test_deepest_match_wins_among_three_nesting_levels(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write(
            "envshield.yml",
            "services:\n"
            "  root:\n    schema: env.schema.toml\n"
            "  frontend:\n    schema: frontend/env.schema.toml\n"
            "  frontend-admin:\n"
            "    schema: frontend/admin/env.schema.toml\n"
            "    dir: frontend/admin\n",
        )
        _write("env.schema.toml", "")
        _write("frontend/env.schema.toml", "")
        _write("frontend/admin/env.schema.toml", "")
        _write("frontend/admin/x.ts", "")

        assert (
            config_manager.resolve_file_owner("frontend/admin/x.ts") == "frontend-admin"
        )
        assert config_manager.resolve_file_owner("frontend/other.ts") == "frontend"

    def test_file_outside_every_registered_directory_is_unowned(
        self, tmp_path, monkeypatch
    ):
        """No root ('.') service registered -- a file outside every
        service's own directory has no owner at all, and that must be a
        safe `None`, not a crash or a guessed attribution."""
        monkeypatch.chdir(tmp_path)
        _write(
            "envshield.yml",
            "services:\n  api:\n    schema: services/api/env.schema.toml\n",
        )
        _write("services/api/env.schema.toml", "")
        _write("unrelated/thing.py", "")

        assert config_manager.resolve_file_owner("unrelated/thing.py") is None

    def test_duplicate_service_directories_fail_closed(self, tmp_path, monkeypatch):
        """BL-137: two services sharing one schema and declaring the same
        directory is an invalid topology -- resolve_file_owner must raise,
        never silently pick one."""
        monkeypatch.chdir(tmp_path)
        _write(
            "envshield.yml",
            "services:\n"
            "  a:\n    schema: shared/env.schema.toml\n    dir: shared\n"
            "  b:\n    schema: shared/env.schema.toml\n    dir: shared\n",
        )
        _write("shared/env.schema.toml", "")

        with pytest.raises(DuplicateServiceDirError):
            config_manager.resolve_file_owner("shared/app.py")


# -- 2. Consumers agree: scan / explain / undeclared, through their real ----
# -- public entry points, on the exact JossJobs-shaped nested topology. -----


@pytest.fixture
def jossjobs_repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _init_repo()
    _write(
        "envshield.yml",
        "services:\n"
        "  jossjobs:\n    schema: env.schema.toml\n"
        "  frontend:\n    schema: frontend/env.schema.toml\n",
    )
    _write("env.schema.toml", '[FRONTEND_ORIGIN]\ndefaultValue = "x"\n')
    _write("frontend/env.schema.toml", '[BACKEND_ORIGIN]\ndefaultValue = "y"\n')
    _write("frontend/src/api.ts", "const b = process.env.BACKEND_ORIGIN;\n")
    _commit("init")
    return tmp_path


class TestConsumersAgreeOnJossJobsShape:
    def test_scan_attributes_the_nested_file_to_the_nested_service(self, jossjobs_repo):
        _write(
            "frontend/src/api.ts",
            "const b = process.env.BACKEND_ORIGIN;\n"
            "const u = process.env.UNIQUE_FRONTEND_VAR;\n",
        )
        result = cli_json("scan", ".", code=1)
        undeclared = {
            (f["file_path"], f["variable_name"]): f.get("scope")
            for f in result["undeclared_variables"]
        }
        # UNIQUE_FRONTEND_VAR isn't declared in *either* schema -- scan
        # must attribute the file to 'frontend' (its real owner), not
        # silently drop it or attribute it to root.
        assert ("frontend/src/api.ts", "UNIQUE_FRONTEND_VAR") in undeclared

    def test_explain_does_not_attribute_a_nested_services_file_to_root(
        self, jossjobs_repo
    ):
        _write(
            "frontend/src/api.ts",
            "const b = process.env.BACKEND_ORIGIN;\n"
            "const u = process.env.UNIQUE_FRONTEND_VAR;\n",
        )
        # UNIQUE_FRONTEND_VAR isn't declared for 'jossjobs' either, so the
        # CLI's graceful-degradation path (build_undeclared_report) runs.
        result = cli_json(
            "explain", "UNIQUE_FRONTEND_VAR", "--service", "jossjobs", code=1
        )
        source_files = {u["file_path"] for u in result["source_usages"]}
        # Before D-001: root's service_dir '.' contained frontend/, so this
        # file's read was misattributed to jossjobs (BL-146). After: it
        # must not appear in root's report at all -- it belongs to frontend.
        assert "frontend/src/api.ts" not in source_files

    def test_explain_attributes_it_to_the_nested_service_instead(self, jossjobs_repo):
        _write(
            "frontend/src/api.ts",
            "const b = process.env.BACKEND_ORIGIN;\n"
            "const u = process.env.UNIQUE_FRONTEND_VAR;\n",
        )
        result = cli_json(
            "explain", "UNIQUE_FRONTEND_VAR", "--service", "frontend", code=1
        )
        source_files = {u["file_path"] for u in result["source_usages"]}
        assert "frontend/src/api.ts" in source_files

    def test_undeclared_does_not_attribute_a_nested_services_file_to_root(
        self, jossjobs_repo
    ):
        """
        The BL-146 false-positive reproduction: a newly introduced read in
        frontend's own file must never be classified against root's
        schema, even though root's directory ('.') lexically contains it.
        """
        _write(
            "frontend/src/api.ts",
            "const b = process.env.BACKEND_ORIGIN;\n"
            "const u = process.env.UNIQUE_FRONTEND_VAR;\n",
        )
        result = cli_json("undeclared", "--service", "jossjobs")
        assert result["has_missing_declarations"] is False
        assert result["changes"] == []

    def test_undeclared_attributes_it_to_the_nested_service_instead(
        self, jossjobs_repo
    ):
        _write(
            "frontend/src/api.ts",
            "const b = process.env.BACKEND_ORIGIN;\n"
            "const u = process.env.UNIQUE_FRONTEND_VAR;\n",
        )
        result = cli_json("undeclared", "--service", "frontend", code=1)
        missing = {
            c["variable"]
            for c in result["changes"]
            if c["category"] == "missing_declaration"
        }
        assert "UNIQUE_FRONTEND_VAR" in missing

    def test_dependency_snapshot_agrees_at_the_module_level_too(self, jossjobs_repo):
        """Same reproduction, one layer down -- dependency_snapshot itself
        (not just the CLI wrapping it) must exclude the nested service's
        file from root's changed-file set."""
        _write(
            "frontend/src/api.ts",
            "const b = process.env.BACKEND_ORIGIN;\n"
            "const u = process.env.UNIQUE_FRONTEND_VAR;\n",
        )
        _, root_usages_b = dependency_snapshot.discover_usages_for_service("jossjobs")
        assert root_usages_b == []

        _, frontend_usages_b = dependency_snapshot.discover_usages_for_service(
            "frontend"
        )
        assert "UNIQUE_FRONTEND_VAR" in {u.variable for u in frontend_usages_b}


# -- 3. A broken/missing schema must never redirect ownership elsewhere. ----
# -- resolve_file_owner is topology-only; the scanner separately decides ----
# -- eligibility (does the topologically-correct owner have anything loaded --
# -- to check the file against) *after* ownership is resolved, never before.


class TestScannerSeparatesOwnershipFromSchemaEligibility:
    def test_ownership_is_correct_even_when_the_owners_schema_is_broken(
        self, tmp_path, monkeypatch
    ):
        """resolve_file_owner is pure topology -- it must still name the
        real owner even though that owner's schema can't be parsed."""
        monkeypatch.chdir(tmp_path)
        _write(
            "envshield.yml",
            "services:\n"
            "  jossjobs:\n    schema: env.schema.toml\n"
            "  frontend:\n    schema: frontend/env.schema.toml\n",
        )
        _write("env.schema.toml", '[FRONTEND_ORIGIN]\ndefaultValue = "x"\n')
        _write("frontend/env.schema.toml", '[UNTERMINATED\ndescription = "x\n')
        _write("frontend/src/api.ts", "")

        assert config_manager.resolve_file_owner("frontend/src/api.ts") == "frontend"

    def test_scan_does_not_check_a_nested_files_reads_against_roots_schema(
        self, jossjobs_repo
    ):
        """
        The critical regression this refactor exists to eliminate: frontend's
        schema is broken -> frontend/src/api.ts must be skipped for
        undeclared-variable purposes, never silently checked against root's
        schema (which would either wrongly flag BACKEND_ORIGIN as
        undeclared-to-root, or wrongly clear a genuinely undeclared var that
        happens to coincide with something root does declare).
        """
        _write("frontend/env.schema.toml", '[UNTERMINATED\ndescription = "x\n')
        _write(
            "frontend/src/api.ts",
            "const b = process.env.BACKEND_ORIGIN;\n"  # declared only in frontend's (broken) schema
            "const f = process.env.FRONTEND_ORIGIN;\n",  # declared in root's schema -- must NOT clear this
        )
        result = runner.invoke(app, ["scan", "."])
        assert "Could not load schema for service 'frontend'" in result.stdout
        assert result.exit_code == 0, result.stdout
        assert "BACKEND_ORIGIN" not in result.stdout
        assert "FRONTEND_ORIGIN" not in result.stdout

    def test_scan_still_detects_secrets_in_a_file_whose_owner_has_no_schema(
        self, jossjobs_repo
    ):
        """Secret detection is schema-independent by design (see
        _scan_single_file's skip_undeclared docstring) -- a broken schema
        must never suppress it."""
        _write("frontend/env.schema.toml", '[UNTERMINATED\ndescription = "x\n')
        _write(
            "frontend/src/config.py",
            'AWS_KEY = "AKIAIOSFODNN7EXAMPLE"\n',
        )
        result = runner.invoke(app, ["scan", "."])
        assert result.exit_code == 1, result.stdout
        assert "AWS" in result.stdout or "Secret" in result.stdout

    def test_scan_still_checks_other_services_normally_when_one_is_broken(
        self, jossjobs_repo
    ):
        """The pre-existing flat-sibling regression, reproduced on the
        nested shape too: frontend's broken schema must not degrade
        checking for root's own files."""
        _write("frontend/env.schema.toml", '[UNTERMINATED\ndescription = "x\n')
        _write("app.py", "import os\nx = os.environ.get('ROOT_UNDECLARED')\n")
        result = cli_json("scan", ".", code=1)
        undeclared = {
            (f["file_path"], f["variable_name"]) for f in result["undeclared_variables"]
        }
        assert ("app.py", "ROOT_UNDECLARED") in undeclared

    def test_scan_checks_a_root_level_file_normally_when_root_schema_is_valid(
        self, jossjobs_repo
    ):
        """Baseline: with every schema healthy, root-level files are still
        checked against root's own schema exactly as before."""
        _write("app.py", "import os\nx = os.environ.get('ROOT_UNDECLARED')\n")
        result = cli_json("scan", ".", code=1)
        undeclared = {
            (f["file_path"], f["variable_name"]) for f in result["undeclared_variables"]
        }
        assert ("app.py", "ROOT_UNDECLARED") in undeclared

    def test_scan_still_flags_everything_in_a_genuinely_unowned_file(
        self, tmp_path, monkeypatch
    ):
        """Distinguish 'owned but nothing loaded to check against' (skip)
        from 'no registered service owns this file at all' (existing
        semantics: still flag everything found) -- these are different
        facts and must stay different outcomes."""
        monkeypatch.chdir(tmp_path)
        _init_repo()
        _write(
            "envshield.yml",
            "services:\n  api:\n    schema: services/api/env.schema.toml\n",
        )
        _write("services/api/env.schema.toml", "")
        _write(
            "unrelated/thing.py",
            "import os\nx = os.environ.get('SOME_VAR')\n",
        )
        _commit("init")

        result = cli_json("scan", ".", code=1)
        undeclared = {
            (f["file_path"], f["variable_name"]) for f in result["undeclared_variables"]
        }
        assert ("unrelated/thing.py", "SOME_VAR") in undeclared
