# envshield/tests/core/test_evaluator.py
import json
import os

import pytest
from typer.testing import CliRunner

from envshield.core import evaluator, service_manager
from envshield.tests.check_golden_scenarios import SCENARIOS
from envshield.tests.test_check_golden import GOLDEN_PATH, _materialize

runner = CliRunner()


def _evaluate_scenario(name, tmp_path, environment=None):
    scenario = SCENARIOS[name]
    args = list(scenario.get("args", []))
    service = None
    if "--service" in args:
        i = args.index("--service")
        service = args[i + 1]
        del args[i : i + 2]
    file = args[0] if args else None
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _materialize(scenario["files"])
        try:
            targets = service_manager.resolve_targets(
                service, invocation_dir=os.getcwd()
            )
        except Exception as e:  # mirrors check's own target-resolution error path
            return {"success": False, "error": str(e)}, []
        if len(targets) > 1:
            file = None
        evaluations = [
            evaluator.evaluate_service(t, file=file, environment=environment)
            for t in targets
        ]
        return evaluator.legacy_check_payload(evaluations), evaluations


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_evaluator_reproduces_check_json(name, tmp_path):
    """Step 4 parity: the evaluator alone reproduces pre-evaluator 'check --json'."""
    with open(GOLDEN_PATH) as f:
        expected = json.load(f)[name]["json"]

    payload, _ = _evaluate_scenario(name, tmp_path)

    assert payload == expected


SYNTHETIC = {
    "OK_PLAIN": "SYNTHVAL_OK_PLAIN_7f3a",
    "OK_SECRET": "SYNTHVAL_OK_SECRET_91bc",
    "BAD_INT": "SYNTHVAL_BAD_INT_22de",
    "BAD_SECRET_URL": "SYNTHVAL_BAD_SECRET_URL_5e10",
    "BAD_PATTERN": "SYNTHVAL_BAD_PATTERN_0a77",
    "EXTRA_VAR": "SYNTHVAL_EXTRA_c4d2",
}
SCHEMA = (
    '[OK_PLAIN]\ndescription="p"\n\n'
    "[OK_SECRET]\nsecret=true\n\n"
    '[BAD_INT]\ntype="int"\n\n'
    '[BAD_SECRET_URL]\nsecret=true\ntype="url"\n\n'
    '[BAD_PATTERN]\npattern="^v[0-9]+$"\n\n'
    '[FROM_SHELL]\ndescription="s"\n\n'
    '[MISSING]\ndescription="m"\n'
)


class TestReportNeverContainsValues:
    """
    D-5's invariant, as a class: no value from any source -- valid,
    invalid, secret or not, extra, a manifest's, or the process
    environment's -- appears anywhere in the report or the legacy payload.
    """

    def _project(self):
        subprocess_env = "\n".join(f"{k}={v}" for k, v in SYNTHETIC.items())
        _materialize(
            {
                "envshield.yml": (
                    "services:\n  app:\n    schema: env.schema.toml\n"
                    "manifests:\n  - file: docker-compose.yml\n"
                    "    containers:\n      web: app\n"
                ),
                "env.schema.toml": SCHEMA,
                ".env": subprocess_env + "\n",
                "docker-compose.yml": (
                    "services:\n  web:\n    image: x\n    environment:\n"
                    "      OK_PLAIN: SYNTHVAL_MANIFEST_PLAIN_33aa\n"
                    "      BAD_INT: SYNTHVAL_MANIFEST_BAD_INT_44bb\n"
                ),
            }
        )

    def test_no_value_in_report_or_payload(self, tmp_path):
        environment = {
            "FROM_SHELL": "SYNTHVAL_SHELL_OVERLAY_8e8e",
            "OK_SECRET": "SYNTHVAL_SHELL_SECRET_6f6f",
            "UNRELATED_SHELL_VAR": "SYNTHVAL_UNRELATED_9d9d",
        }
        with runner.isolated_filesystem(temp_dir=tmp_path):
            self._project()
            evaluation = evaluator.evaluate_service("app", environment=environment)
            text = json.dumps(
                [
                    evaluator.build_report(evaluation),
                    evaluator.legacy_check_payload([evaluation]),
                ]
            )

        assert "SYNTHVAL" not in text
        # The unrelated shell variable isn't even named.
        assert "UNRELATED_SHELL_VAR" not in text


class TestProcessEnvironmentOverlay:
    """D-3: opt-in, local file only, environment wins, only contract names."""

    def _project(self, env_content):
        _materialize(
            {
                "envshield.yml": (
                    "services:\n  app:\n    schema: env.schema.toml\n"
                    "manifests:\n  - file: docker-compose.yml\n"
                    "    containers:\n      web: app\n"
                ),
                "env.schema.toml": (
                    '[NODE_ENV]\nenum=["development","production"]\n\n'
                    "[FLAG]\nrequired=false\n\n"
                    '[NEEDED]\nrequiredIf={var="TRIGGER", equals="on"}\n'
                ),
                ".env": env_content,
                "docker-compose.yml": (
                    "services:\n  web:\n    image: x\n    environment:\n"
                    "      FLAG: '1'\n"
                ),
            }
        )

    def _local(self, evaluation):
        return evaluation.sources[0]

    def test_off_by_default(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NODE_ENV", "development")
        with runner.isolated_filesystem(temp_dir=tmp_path):
            self._project("")
            evaluation = evaluator.evaluate_service("app")
        assert "NODE_ENV" in self._local(evaluation).diff.missing

    def test_environment_supplies_a_missing_variable(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            self._project("")
            evaluation = evaluator.evaluate_service(
                "app", environment={"NODE_ENV": "development"}
            )
        local = self._local(evaluation)
        assert local.diff.is_clean
        assert local.overlaid == ("NODE_ENV",)

    def test_environment_wins_over_the_file_even_when_empty(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            self._project("NODE_ENV=development\n")
            invalid = evaluator.evaluate_service(
                "app", environment={"NODE_ENV": "staging"}
            )
            empty = evaluator.evaluate_service("app", environment={"NODE_ENV": ""})
        assert "NODE_ENV" in self._local(invalid).diff.invalid
        assert "NODE_ENV" in self._local(empty).diff.blank

    def test_requiredif_trigger_is_read_from_the_environment(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            self._project("NODE_ENV=development\n")
            evaluation = evaluator.evaluate_service(
                "app", environment={"TRIGGER": "on"}
            )
        local = self._local(evaluation)
        assert "NEEDED" in local.diff.missing
        # A trigger isn't declared, so it's never reported as an extra.
        assert "TRIGGER" not in local.diff.extra

    def test_manifests_are_never_overlaid(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            self._project("NODE_ENV=development\n")
            evaluation = evaluator.evaluate_service(
                "app", environment={"NODE_ENV": "development"}
            )
        manifest = evaluation.sources[1]
        assert manifest.kind == "manifest"
        assert manifest.overlaid == ()
        assert "NODE_ENV" in manifest.diff.missing

    def test_report_names_overlaid_variables_only(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            self._project("")
            report = evaluator.build_report(
                evaluator.evaluate_service(
                    "app", environment={"NODE_ENV": "development", "HOME": "/x"}
                )
            )
        (process,) = [
            s for s in report["sources"] if s["kind"] == "process_environment"
        ]
        assert process["variables"] == ["NODE_ENV"]


class TestReportShape:
    def test_statuses_and_summary(self, tmp_path):
        _, evaluations = _evaluate_scenario("missing_blank_invalid_extra", tmp_path)
        report = evaluator.build_report(evaluations[0])

        assert report["report_version"] == 1
        assert report["summary"] == {"clean": False, "complete": True}
        by_name = {v["name"]: v for v in report["variables"]}
        assert by_name["A"]["present"] is False
        assert by_name["A"]["sources"] == [{"path": ".env", "status": "missing"}]
        assert by_name["B"]["sources"][0]["status"] == "blank"
        assert by_name["C"]["valid"] is False
        assert by_name["C"]["reason"] == "must be a port number from 1-65535"
        assert by_name["S"]["secret"] is True
        assert by_name["EXTRA"]["declared"] is False
        assert by_name["EXTRA"]["scope"] == "undefined"

    def test_a_missing_source_is_incomplete_and_never_clean(self, tmp_path):
        _, evaluations = _evaluate_scenario("local_file_missing", tmp_path)
        report = evaluator.build_report(evaluations[0])

        assert report["sources"][0]["status"] == "missing"
        assert report["summary"] == {"clean": False, "complete": False}
        assert report["variables"][0]["present"] == "unknown"

    def test_an_unresolved_manifest_is_incomplete(self, tmp_path):
        _, evaluations = _evaluate_scenario("kubernetes_unresolved_envfrom", tmp_path)
        report = evaluator.build_report(evaluations[0])

        assert [s["status"] for s in report["sources"]] == ["checked", "unresolved"]
        assert report["summary"]["complete"] is False
        by_name = {v["name"]: v for v in report["variables"]}
        assert by_name["B"]["present"] == "unknown"

    def test_a_schema_that_fails_to_load_fails_every_source(self, tmp_path):
        _, evaluations = _evaluate_scenario("malformed_schema", tmp_path)
        report = evaluator.build_report(evaluations[0])

        assert report["sources"][0]["status"] == "error"
        assert report["variables"] == []
        assert report["summary"] == {"clean": False, "complete": False}

    def test_union_decides_presence(self, tmp_path):
        _, evaluations = _evaluate_scenario("union_satisfied_across_sources", tmp_path)
        report = evaluator.build_report(evaluations[0])

        assert report["summary"] == {"clean": True, "complete": True}
        by_name = {v["name"]: v for v in report["variables"]}
        # Missing from .env on its own, but the union has it.
        assert by_name["FROM_COMPOSE"]["present"] is True
        assert report["union"]["clean"] is True

    def test_out_of_scope_extra(self, tmp_path):
        _, evaluations = _evaluate_scenario(
            "shared_schema_out_of_scope_extra", tmp_path
        )
        api = {e.service: e for e in evaluations}["api"]
        report = evaluator.build_report(api)

        by_name = {v["name"]: v for v in report["variables"]}
        assert by_name["WORKER_ONLY"]["scope"] == "out_of_scope"
        assert by_name["WORKER_ONLY"]["sources"][0]["status"] == "out_of_scope"

    def test_required_false_absent_is_not_required(self, tmp_path):
        _, evaluations = _evaluate_scenario("required_false_absent", tmp_path)
        report = evaluator.build_report(evaluations[0])

        by_name = {v["name"]: v for v in report["variables"]}
        assert by_name["OPT"]["requiredness"] == "optional"
        assert by_name["OPT"]["sources"][0]["status"] == "not_required"
        assert by_name["OPT"]["present"] is False
        # Optional, but a value that's present must still be valid.
        assert by_name["BAD"]["valid"] is False


class TestCodeReferences:
    """D-7: 'check' evaluates the service's code references."""

    def _project(self, extra_files=None, config_extra=""):
        files = {
            "envshield.yml": (
                "services:\n  app:\n    schema: env.schema.toml\n" + config_extra
            ),
            "env.schema.toml": '[DECLARED]\ndescription="d"\n',
            ".env": "DECLARED=1\n",
            ".gitignore": "ignored.py\n",
            "app/main.py": (
                "import os\nfrom flask import current_app\n\n"
                'a = os.environ["DECLARED"]\n'
                'b = os.getenv("NEW_KEY")\n'
                'c = current_app.config["MAYBE_CONFIG"]\n'
            ),
            "web/index.ts": "const u = process.env.API_URL;\n",
            "tests/test_x.py": 'import os\nos.getenv("ONLY_IN_TESTS")\n',
            "ignored.py": 'import os\nos.getenv("ONLY_IN_IGNORED")\n',
            "node_modules/dep/index.js": "process.env.FROM_DEPENDENCY\n",
        }
        files.update(extra_files or {})
        _materialize(files)

    def test_undeclared_high_confidence_reads_fail_the_service(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            self._project(
                config_extra="secret_scanning:\n  exclude_files:\n    - '**/tests/*'\n"
            )
            evaluation = evaluator.evaluate_service("app", code=True)
            report = evaluator.build_report(evaluation)

        assert sorted(evaluation.undeclared_references) == ["API_URL", "NEW_KEY"]
        assert evaluation.passed is False
        assert report["summary"] == {"clean": False, "complete": True}
        by_name = {v["name"]: v for v in report["variables"]}
        assert by_name["DECLARED"]["referenced"] is True
        assert by_name["NEW_KEY"]["declared"] is False
        assert by_name["NEW_KEY"]["references"][0]["file"] == os.path.join(
            "app", "main.py"
        )
        assert by_name["NEW_KEY"]["references"][0]["line"] == 5
        # Medium confidence: reported, never a failure.
        assert "MAYBE_CONFIG" not in evaluation.undeclared_references
        assert by_name["MAYBE_CONFIG"]["references"][0]["confidence"] == "medium"
        # Excluded, git-ignored, and dependency-tree files are never read.
        for name in ("ONLY_IN_TESTS", "ONLY_IN_IGNORED", "FROM_DEPENDENCY"):
            assert name not in by_name

    def test_same_file_set_as_scan(self, tmp_path):
        """Migration parity: the undeclared names 'check' finds are exactly
        the ones the pre-commit 'scan' flags, for the same project."""
        from envshield.cli import app

        with runner.isolated_filesystem(temp_dir=tmp_path):
            self._project(
                config_extra="secret_scanning:\n  exclude_files:\n    - '**/tests/*'\n"
            )
            scan = json.loads(runner.invoke(app, ["scan", "--json"]).stdout)
            evaluation = evaluator.evaluate_service("app", code=True)

        scanned = {f["variable_name"] for f in scan["undeclared_variables"]}
        assert scanned == set(evaluation.undeclared_references)

    def test_declaring_them_passes(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            self._project(
                extra_files={
                    "env.schema.toml": (
                        '[DECLARED]\ndescription="d"\n\n[NEW_KEY]\nrequired=false\n\n'
                        "[API_URL]\nrequired=false\n\n[ONLY_IN_TESTS]\nrequired=false\n"
                    )
                }
            )
            evaluation = evaluator.evaluate_service("app", code=True)
        assert evaluation.undeclared_references == {}
        assert evaluation.passed is True

    def test_explicit_file_and_default_skip_code(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            self._project()
            explicit = evaluator.evaluate_service("app", file=".env", code=True)
            default = evaluator.evaluate_service("app")
        assert explicit.code is None and explicit.passed is True
        assert default.code is None
        assert evaluator.build_report(explicit)["code_references"] == {
            "status": "not_checked"
        }

    def test_each_service_sees_only_its_own_files(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _materialize(
                {
                    "envshield.yml": (
                        "services:\n  api:\n    schema: api/env.schema.toml\n"
                        "  web:\n    schema: web/env.schema.toml\n"
                    ),
                    "api/env.schema.toml": '[API_KEY]\ndescription="a"\n',
                    "api/.env": "API_KEY=1\n",
                    "api/main.py": 'import os\nos.getenv("API_KEY")\n',
                    "web/env.schema.toml": '[WEB_URL]\ndescription="w"\n',
                    "web/.env": "WEB_URL=1\n",
                    "web/app.js": "process.env.WEB_URL; process.env.API_KEY;\n",
                }
            )
            api = evaluator.evaluate_service("api", code=True)
            web = evaluator.evaluate_service("web", code=True)
        assert api.undeclared_references == {}
        assert list(web.undeclared_references) == ["API_KEY"]

    def test_out_of_scope_reference_in_a_shared_schema(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _materialize(
                {
                    "envshield.yml": (
                        "services:\n  api:\n    schema: env.schema.toml\n    dir: api\n"
                        "  worker:\n    schema: env.schema.toml\n    dir: worker\n"
                    ),
                    "env.schema.toml": '[WORKER_ONLY]\nservices=["worker"]\n',
                    "api/.env": "",
                    "api/main.py": 'import os\nos.getenv("WORKER_ONLY")\n',
                    "worker/.env": "WORKER_ONLY=1\n",
                }
            )
            report = evaluator.build_report(
                evaluator.evaluate_service("api", code=True)
            )
        (entry,) = [v for v in report["variables"] if v["name"] == "WORKER_ONLY"]
        assert entry["scope"] == "out_of_scope"
        assert entry["undeclared_reference"] is True


def test_check_sees_basesettings_fields_and_reports_dynamic_reads(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _materialize(
            {
                "envshield.yml": "services:\n  app:\n    schema: env.schema.toml\n",
                "env.schema.toml": '[DATABASE_URL]\ndescription="d"\n',
                ".env": "DATABASE_URL=x\n",
                "settings.py": (
                    "import os\nfrom pydantic_settings import BaseSettings\n\n"
                    "class Settings(BaseSettings):\n"
                    "    database_url: str\n    sentry_dsn: str = ''\n\n"
                    "flag = os.getenv(NAME)\n"
                ),
            }
        )
        evaluation = evaluator.evaluate_service("app", code=True)
        report = evaluator.build_report(evaluation)

    assert list(evaluation.undeclared_references) == ["SENTRY_DSN"]
    assert report["code_references"]["dynamic_references"] == [
        {
            "file_path": "settings.py",
            "line": 8,
            "language": "python",
            "access_type": "os.getenv",
        }
    ]
    # Informational only.
    assert report["code_references"]["undeclared"] == ["SENTRY_DSN"]


def test_union_success_and_report_summary_agree_with_an_unresolved_manifest(tmp_path):
    """A manifest that can't confirm a name the local file supplies must not
    make a passing union evaluation 'incomplete'."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _materialize(
            {
                "envshield.yml": (
                    "services:\n  app:\n    schema: env.schema.toml\n"
                    "    completeness: union\n"
                    "manifests:\n  - file: k8s.yml\n    containers:\n      app: app\n"
                ),
                "env.schema.toml": '[A]\ndescription="a"\n\n[B]\ndescription="b"\n',
                ".env": "A=1\nB=2\n",
                "k8s.yml": SCENARIOS["kubernetes_unresolved_envfrom"]["files"][
                    "k8s.yml"
                ],
            }
        )
        evaluation = evaluator.evaluate_service("app")
        report = evaluator.build_report(evaluation)
        payload = evaluator.legacy_check_payload([evaluation])

    assert payload["success"] is True
    assert report["summary"] == {"clean": True, "complete": True}
    assert report["sources"][1]["status"] == "unresolved"


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_report_summary_agrees_with_the_verdict(name, tmp_path):
    """For every golden scenario: the report is clean exactly when 'check'
    passes, and a passing evaluation is never incomplete."""
    _, evaluations = _evaluate_scenario(name, tmp_path)
    for evaluation in evaluations:
        summary = evaluator.build_report(evaluation)["summary"]
        assert summary["clean"] == evaluation.passed
        if evaluation.passed:
            assert summary["complete"] is True
