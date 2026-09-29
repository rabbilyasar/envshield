# envshield/tests/core/test_scope_reporting.py
"""
Shared system schema, Phase 4: reporting distinguishes `undefined` (not in
the system schema) from `out_of_scope` (defined, not granted to this
service). Reporting only -- an out-of-scope variable is never accepted as
part of the service's schema, and every existing failure stays a failure.
"""

import json
import subprocess

from typer.testing import CliRunner

from envshield.cli import app
from envshield.config import manager as config_manager
from envshield.core import contract_diff, doctor, explain, scanner, schema_manager

runner = CliRunner()

SCHEMA = """
[LOG_LEVEL]
defaultValue = "info"

[API_TOKEN]
secret = true
services = ["api"]

[QUEUE_URL]
services = ["worker"]
"""

SEPARATE = (
    "services:\n"
    "  api:\n    schema: env.schema.toml\n    dir: api\n"
    "  worker:\n    schema: env.schema.toml\n    dir: worker\n"
)
SHARED_LOCAL = (
    "services:\n"
    "  api:\n    schema: env.schema.toml\n    dir: api\n    local_file: .env\n"
    "  worker:\n    schema: env.schema.toml\n    dir: worker\n    local_file: .env\n"
)
SINGLE = "services:\n  app:\n    schema: env.schema.toml\n"


def _git(tmp_path, *args):
    subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)


def _repo(tmp_path, monkeypatch, yml=SEPARATE, schema=SCHEMA):
    monkeypatch.chdir(tmp_path)
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "T")
    (tmp_path / "envshield.yml").write_text(yml)
    (tmp_path / "env.schema.toml").write_text(schema)
    for d in ("api", "worker"):
        (tmp_path / d).mkdir(exist_ok=True)
        (tmp_path / d / ".keep").write_text("")
    _commit(tmp_path)


def _commit(tmp_path, message="c"):
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", message)


class TestStatus:
    def test_three_states(self, tmp_path, monkeypatch):
        _repo(tmp_path, monkeypatch)
        view = config_manager.load_schema_view("worker")
        assert view.status("QUEUE_URL") == "in_scope"
        assert view.status("LOG_LEVEL") == "in_scope"
        assert view.status("API_TOKEN") == "out_of_scope"
        assert view.status("NOPE") == "undefined"

    def test_out_of_scope_is_not_part_of_the_service_schema(
        self, tmp_path, monkeypatch
    ):
        _repo(tmp_path, monkeypatch)
        assert "API_TOKEN" not in config_manager.load_schema("worker")


class TestUndeclared:
    def _run(self, tmp_path, source):
        (tmp_path / "worker" / "app.py").write_text(source)
        return runner.invoke(app, ["undeclared", "--service", "worker", "--json"])

    def test_out_of_scope_read_still_fails_and_is_labelled(self, tmp_path, monkeypatch):
        _repo(tmp_path, monkeypatch)
        result = self._run(
            tmp_path, "import os\nos.environ['API_TOKEN']\nos.environ['NOPE']\n"
        )
        assert result.exit_code == 1
        payload = json.loads(result.stdout)
        by_var = {c["variable"]: c for c in payload["changes"]}
        assert by_var["API_TOKEN"]["category"] == "missing_declaration"
        assert by_var["API_TOKEN"]["scope"] == "out_of_scope"
        assert by_var["NOPE"]["category"] == "missing_declaration"
        assert "scope" not in by_var["NOPE"]

    def test_in_scope_read_is_declared(self, tmp_path, monkeypatch):
        _repo(tmp_path, monkeypatch)
        result = self._run(tmp_path, "import os\nos.environ['QUEUE_URL']\n")
        assert result.exit_code == 0
        change = json.loads(result.stdout)["changes"][0]
        assert change["category"] == "declared"
        assert "scope" not in change

    def test_human_output_names_out_of_scope(self, tmp_path, monkeypatch):
        _repo(tmp_path, monkeypatch)
        (tmp_path / "worker" / "app.py").write_text(
            "import os\nos.environ['API_TOKEN']\n"
        )
        result = runner.invoke(app, ["undeclared", "--service", "worker"])
        assert result.exit_code == 1
        assert "Out of scope:" in result.stdout

    def test_sarif_marks_out_of_scope(self, tmp_path, monkeypatch):
        _repo(tmp_path, monkeypatch)
        (tmp_path / "worker" / "app.py").write_text(
            "import os\nos.environ['API_TOKEN']\n"
        )
        result = runner.invoke(app, ["undeclared", "--service", "worker", "--sarif"])
        assert result.exit_code == 1
        sarif_result = json.loads(result.stdout)["runs"][0]["results"][0]
        assert sarif_result["properties"]["scope"] == "out_of_scope"

    def test_revision_uses_historical_grants(self, tmp_path, monkeypatch):
        """API_TOKEN was granted to worker at HEAD~1 and revoked at HEAD: a new
        read judged at HEAD is out of scope, at HEAD~1 it would be declared."""
        _repo(
            tmp_path,
            monkeypatch,
            schema=SCHEMA.replace('services = ["api"]', 'services = ["api", "worker"]'),
        )
        (tmp_path / "env.schema.toml").write_text(SCHEMA)
        (tmp_path / "worker" / "app.py").write_text(
            "import os\nos.environ['API_TOKEN']\n"
        )
        _commit(tmp_path)
        result = runner.invoke(
            app, ["undeclared", "HEAD~1", "HEAD", "--service", "worker", "--json"]
        )
        assert result.exit_code == 1
        change = json.loads(result.stdout)["changes"][0]
        assert change["scope"] == "out_of_scope"

    def test_single_service_output_unchanged(self, tmp_path, monkeypatch):
        _repo(
            tmp_path,
            monkeypatch,
            yml=SINGLE,
            schema='[LOG_LEVEL]\ndefaultValue = "x"\n',
        )
        (tmp_path / "app.py").write_text("import os\nos.environ['NOPE']\n")
        result = runner.invoke(app, ["undeclared", "--json"])
        assert result.exit_code == 1
        change = json.loads(result.stdout)["changes"][0]
        assert change["category"] == "missing_declaration"
        assert "scope" not in change


class TestScanner:
    def test_scan_labels_out_of_scope_and_still_reports_it(self, tmp_path, monkeypatch):
        _repo(tmp_path, monkeypatch)
        (tmp_path / "worker" / "app.py").write_text(
            "import os\nos.environ['API_TOKEN']\nos.environ['NOPE']\nos.environ['QUEUE_URL']\n"
        )
        result = scanner.scan_result(["worker"], False, None, None, "worker")
        assert result["clean"] is False
        by_var = {f["variable_name"]: f for f in result["undeclared_variables"]}
        assert set(by_var) == {"API_TOKEN", "NOPE"}
        assert by_var["API_TOKEN"]["scope"] == "out_of_scope"
        assert "scope" not in by_var["NOPE"]

    def test_scan_per_service_routing_labels_out_of_scope(self, tmp_path, monkeypatch):
        _repo(tmp_path, monkeypatch)
        (tmp_path / "worker" / "app.py").write_text(
            "import os\nos.environ['API_TOKEN']\n"
        )
        result = scanner.scan_result(["."], False, None, None)
        (finding,) = result["undeclared_variables"]
        assert finding["scope"] == "out_of_scope"


class TestExplain:
    def test_out_of_scope_names_the_granted_services(self, tmp_path, monkeypatch):
        _repo(tmp_path, monkeypatch)
        result = runner.invoke(
            app, ["explain", "API_TOKEN", "--service", "worker", "--json"]
        )
        assert result.exit_code == 1
        payload = json.loads(result.stdout)
        assert payload["found"] is False
        assert payload["scope"] == "out_of_scope"
        assert payload["granted_to"] == ["api"]

    def test_undefined_has_no_scope(self, tmp_path, monkeypatch):
        _repo(tmp_path, monkeypatch)
        report = explain.build_undeclared_report("NOPE", "worker")
        assert "scope" not in report.to_dict()

    def test_human_output(self, tmp_path, monkeypatch):
        _repo(tmp_path, monkeypatch)
        result = runner.invoke(app, ["explain", "API_TOKEN", "--service", "worker"])
        assert result.exit_code == 1
        assert "Out of scope" in result.stdout
        assert "api" in result.stdout


class TestCheckAndDoctor:
    def test_unshared_env_with_other_services_var_is_out_of_scope(
        self, tmp_path, monkeypatch
    ):
        _repo(tmp_path, monkeypatch)
        (tmp_path / "worker" / ".env").write_text(
            "LOG_LEVEL=info\nQUEUE_URL=q\nAPI_TOKEN=x\nNOPE=1\n"
        )
        result = schema_manager.check_result("worker/.env", "worker")
        assert result["clean"] is False
        assert result["extra"] == ["API_TOKEN", "NOPE"]
        assert result["out_of_scope"] == ["API_TOKEN"]

        ok, message = doctor._check_local_env_sync("worker")
        assert ok is False
        assert "Out of scope: API_TOKEN" in message
        assert "Extra variables: NOPE" in message

    def test_shared_env_holding_a_peers_var_is_not_extra(self, tmp_path, monkeypatch):
        _repo(tmp_path, monkeypatch, yml=SHARED_LOCAL)
        (tmp_path / ".env").write_text("LOG_LEVEL=info\nQUEUE_URL=q\nAPI_TOKEN=x\n")
        # Out of scope for worker, but legitimately in the shared file.
        assert config_manager.load_schema_view("worker").status("API_TOKEN") == (
            "out_of_scope"
        )
        result = schema_manager.check_result(".env", "worker")
        assert result["clean"] is True
        assert "out_of_scope" not in result

    def test_single_service_check_shape_unchanged(self, tmp_path, monkeypatch):
        _repo(
            tmp_path,
            monkeypatch,
            yml=SINGLE,
            schema='[LOG_LEVEL]\ndefaultValue = "x"\n',
        )
        (tmp_path / ".env").write_text("LOG_LEVEL=x\nNOPE=1\n")
        result = schema_manager.check_result(".env", "app")
        assert result["extra"] == ["NOPE"]
        assert "out_of_scope" not in result

    def test_example_file_out_of_scope(self, tmp_path, monkeypatch):
        _repo(tmp_path, monkeypatch)
        (tmp_path / "worker" / ".env.example").write_text(
            "LOG_LEVEL=\nQUEUE_URL=\nAPI_TOKEN=\n"
        )
        ok, message = doctor._check_example_file_sync("worker")
        assert ok is False
        assert "Out of scope in" in message and "API_TOKEN" in message


class TestContractDiff:
    def test_revoked_grant_is_out_of_scope_not_deleted(self):
        result = contract_diff.diff_schemas(
            {"A": {}, "B": {}}, {"A": {}}, {"A", "B"}, {"A", "B"}
        )
        (change,) = result.changes
        assert change.detail == {"removed": True, "scope": "out_of_scope"}
        assert change.category == "informational"

    def test_new_grant_keeps_its_category(self):
        plain = contract_diff.diff_schemas({}, {"B": {}}).changes[0]
        granted = contract_diff.diff_schemas({}, {"B": {}}, {"B"}, {"B"}).changes[0]
        assert granted.category == plain.category == "breaking"
        assert granted.detail["scope_before"] == "out_of_scope"

    def test_real_removal_unchanged(self):
        (change,) = contract_diff.diff_schemas({"B": {}}, {}, {"B"}, set()).changes
        assert change.detail == {"removed": True}

    def test_schema_diff_cli_across_revisions(self, tmp_path, monkeypatch):
        _repo(
            tmp_path,
            monkeypatch,
            schema=SCHEMA.replace('services = ["api"]', 'services = ["api", "worker"]'),
        )
        (tmp_path / "env.schema.toml").write_text(SCHEMA)
        _commit(tmp_path)
        result = runner.invoke(
            app, ["schema", "diff", "HEAD~1", "HEAD", "--service", "worker", "--json"]
        )
        change = json.loads(result.stdout)["results"][0]["changes"][0]
        assert change["variable"] == "API_TOKEN"
        assert change["detail"] == {"removed": True, "scope": "out_of_scope"}
