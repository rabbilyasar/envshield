# envshield/tests/core/test_verdicts.py
"""
V1-2 (#16): the five verdicts, their reason codes, aggregation, and the
invariant that VALID only ever comes from a validated Concrete value.
"""

import json
import os

import pytest
from typer.testing import CliRunner

from envshield.cli import app
from envshield.config import manager as config_manager
from envshield.core import evaluator, schema_types, service_manager, verdicts
from envshield.core.exceptions import EnvShieldException
from envshield.core.verdicts import Verdict
from envshield.parsers._base import Concrete
from envshield.parsers.factory import get_manifest_parser_and_states
from envshield.tests.check_golden_scenarios import SCENARIOS
from envshield.tests.test_check_golden import _materialize

runner = CliRunner()

ONE = "services:\n  app:\n    schema: env.schema.toml\n"
UNION = "services:\n  app:\n    schema: env.schema.toml\n    completeness: union\n"


def _manifest(*paths):
    return "manifests:\n" + "".join(
        f"  - file: {p}\n    containers:\n      app: app\n" for p in paths
    )


def _compose(environment, extra=""):
    return f"services:\n  app:\n    image: x\n{extra}    environment:\n{environment}"


SECRET = "apiVersion: v1\nkind: Secret\nmetadata: {name: db}\nstringData: {url: x, port: '1'}\n---\n"


def _pod(env):
    return (
        "apiVersion: v1\nkind: Pod\nmetadata: {name: p}\nspec:\n  containers:\n"
        "    - name: app\n      env:\n" + env
    )


def _run(tmp_path, files, *args):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _materialize(files)
        result = runner.invoke(app, ["check", *args, "--json"])
        human = runner.invoke(app, ["check", *args], env={"COLUMNS": "200"})
    return result.exit_code, json.loads(result.stdout), human.stdout


def _variable(payload, name):
    report = payload["reports"][0]
    return next(v for v in report["variables"] if v["name"] == name)


def _verdict(payload, name):
    v = _variable(payload, name)
    return v["verdict"], v["reason_code"]


# --- Source matrix (plan §10) ------------------------------------------------

# (schema, file name, file content, variable, expected (verdict, reason))
MATRIX = {
    "dotenv value": ('[A]\ntype="int"\n', ".env", "A=1\n", "A", ("valid", None)),
    "dotenv invalid": (
        '[A]\ntype="int"\n',
        ".env",
        "A=x\n",
        "A",
        ("invalid", "invalid_value"),
    ),
    "dotenv empty required": ("[A]\n", ".env", "A=\n", "A", ("invalid", "blank")),
    "dotenv empty optional typed": (
        '[A]\nrequired=false\ntype="int"\n',
        ".env",
        "A=\n",
        "A",
        ("invalid", "invalid_value"),
    ),
    "dotenv empty optional string": (
        "[A]\nrequired=false\n",
        ".env",
        "A=\n",
        "A",
        ("valid", None),
    ),
    "dotenv absent required": ("[A]\n", ".env", "", "A", ("invalid", "missing")),
    "dotenv absent optional": (
        "[A]\nrequired=false\n",
        ".env",
        "",
        "A",
        ("not_required", None),
    ),
    "python config value": (
        '[A]\ntype="int"\n',
        "config.py",
        "A = '1'\n",
        "A",
        ("valid", None),
    ),
    "k8s literal": (
        '[A]\ntype="int"\n',
        "k8s.yml",
        _pod("        - {name: A, value: '1'}\n"),
        "A",
        ("valid", None),
    ),
    "k8s literal invalid": (
        '[A]\ntype="int"\n',
        "k8s.yml",
        _pod("        - {name: A, value: x}\n"),
        "A",
        ("invalid", "invalid_value"),
    ),
    "k8s in-file secret, key exists": (
        "[A]\n",
        "k8s.yml",
        SECRET
        + _pod(
            "        - name: A\n          valueFrom: {secretKeyRef: {name: db, key: url}}\n"
        ),
        "A",
        ("unverified", "value_not_inspected"),
    ),
    "k8s in-file secret, key missing": (
        "[A]\n",
        "k8s.yml",
        SECRET
        + _pod(
            "        - name: A\n          valueFrom: {secretKeyRef: {name: db, key: nope}}\n"
        ),
        "A",
        ("invalid", "missing"),
    ),
    "k8s secret outside the file": (
        "[A]\n",
        "k8s.yml",
        _pod(
            "        - name: A\n          valueFrom: {secretKeyRef: {name: elsewhere, key: k}}\n"
        ),
        "A",
        ("unverified", "external_reference"),
    ),
    "k8s configmap outside the file": (
        "[A]\n",
        "k8s.yml",
        _pod(
            "        - name: A\n          valueFrom: {configMapKeyRef: {name: elsewhere, key: k}}\n"
        ),
        "A",
        ("unverified", "external_reference"),
    ),
    "k8s envFrom outside the file, required": (
        "[A]\n",
        "k8s.yml",
        _pod("        - {name: B, value: x}\n").replace(
            "      env:\n",
            "      envFrom:\n        - configMapRef: {name: elsewhere}\n      env:\n",
        ),
        "A",
        ("unverified", "external_reference"),
    ),
    "k8s envFrom outside the file, optional": (
        "[A]\nrequired=false\n",
        "k8s.yml",
        _pod("        - {name: B, value: x}\n").replace(
            "      env:\n",
            "      envFrom:\n        - configMapRef: {name: elsewhere}\n      env:\n",
        ),
        "A",
        ("not_required", None),
    ),
    "compose env_file exists": (
        "[A]\n",
        "docker-compose.yml",
        _compose("      B: x\n", extra="    env_file: app.env\n"),
        "A",
        ("valid", None),
    ),
    "compose env_file missing": (
        "[A]\n",
        "docker-compose.yml",
        _compose("      B: x\n", extra="    env_file: nope.env\n"),
        "A",
        ("invalid", "file_not_found"),
    ),
    "compose optional env_file missing": (
        "[A]\n",
        "docker-compose.yml",
        _compose(
            "      B: x\n",
            extra="    env_file:\n      - {path: nope.env, required: false}\n",
        ),
        "A",
        ("invalid", "missing"),
    ),
}

# Compose interpolation (plan §10), PORT typed "port".
COMPOSE = {
    "${VAR}": ("${PORT}", ("unverified", "host_forward")),
    "bare KEY": (None, ("unverified", "host_forward")),
    "${VAR-x} valid": ("${PORT-8000}", ("unverified", "host_forward")),
    "${VAR:-x} valid": ("${PORT:-8000}", ("unverified", "host_forward")),
    "${VAR-x} invalid": ("${PORT-abc}", ("invalid", "invalid_fallback")),
    "${VAR:-x} invalid": ("${PORT:-abc}", ("invalid", "invalid_fallback")),
    "${VAR:-} empty, required": ("${PORT:-}", ("invalid", "invalid_fallback")),
    "concrete valid": ("'8000'", ("valid", None)),
    "concrete invalid": ("abc", ("invalid", "invalid_value")),
    "${VAR:?err}": ("${PORT:?set it}", ("unverified", "host_forward")),
    "${VAR+alt}": ("${PORT+1}", ("unverified", "host_forward")),
    "${VAR?err}": ("${PORT?set it}", ("unverified", "host_forward")),
    "${VAR:+alt}": ("${PORT:+1}", ("unverified", "host_forward")),
}


@pytest.mark.parametrize("case", sorted(MATRIX))
def test_source_matrix(case, tmp_path):
    schema, path, content, name, expected = MATRIX[case]
    files = {"envshield.yml": ONE, "env.schema.toml": schema, path: content}
    if "env_file" in content:
        files["app.env"] = "A=1\n"
    if path == "config.py":
        files["envshield.yml"] = ONE + "    local_file: config.py\n"
    args = [] if path in (".env", "config.py") else [path]
    _, payload, _ = _run(tmp_path, files, *args)
    assert _verdict(payload, name) == expected


@pytest.mark.parametrize("case", sorted(COMPOSE))
def test_compose_interpolation(case, tmp_path):
    expression, expected = COMPOSE[case]
    line = "      PORT:\n" if expression is None else f"      PORT: {expression}\n"
    files = {
        "envshield.yml": ONE,
        "env.schema.toml": '[PORT]\ntype="port"\n',
        "docker-compose.yml": _compose(line),
    }
    _, payload, _ = _run(tmp_path, files, "docker-compose.yml")
    assert _verdict(payload, "PORT") == expected


def test_a_configured_local_file_that_does_not_exist(tmp_path):
    files = {"envshield.yml": ONE, "env.schema.toml": "[A]\n\n[B]\nrequired=false\n"}
    code, payload, _ = _run(tmp_path, files)
    assert _verdict(payload, "A") == ("invalid", "file_not_found")
    assert _verdict(payload, "B") == ("not_required", None)
    assert code == 1  # unchanged


def test_scenario_14_a_forwarded_requiredif_trigger_is_ambiguous(tmp_path):
    files = {
        "envshield.yml": ONE,
        "env.schema.toml": '[PAY]\nrequired=false\n\n[KEY]\nrequiredIf={var="PAY", equals="true"}\n',
        "docker-compose.yml": _compose("      PAY: ${PAY}\n"),
    }
    _, payload, _ = _run(tmp_path, files, "docker-compose.yml")
    assert _verdict(payload, "KEY") == ("unverified", "ambiguous_requirement")


def test_an_empty_requiredif_trigger_compares_normally(tmp_path):
    files = {
        "envshield.yml": ONE,
        "env.schema.toml": '[PAY]\nrequired=false\n\n[KEY]\nrequiredIf={var="PAY", equals="true"}\n',
        ".env": "PAY=\n",
    }
    _, payload, _ = _run(tmp_path, files)
    assert _verdict(payload, "KEY") == ("not_required", None)


def test_a_present_dependent_of_an_ambiguous_trigger_is_judged_on_its_value(tmp_path):
    files = {
        "envshield.yml": ONE,
        "env.schema.toml": '[PAY]\nrequired=false\n\n[KEY]\ntype="int"\nrequiredIf={var="PAY", equals="true"}\n',
        "docker-compose.yml": _compose("      PAY: ${PAY}\n      KEY: '5'\n"),
    }
    _, payload, _ = _run(tmp_path, files, "docker-compose.yml")
    assert _verdict(payload, "KEY") == ("valid", None)


# --- ERROR is never COULD NOT VERIFY ----------------------------------------


def test_malformed_manifest_is_error(tmp_path):
    files = {
        "envshield.yml": ONE,
        "env.schema.toml": "[A]\n",
        "docker-compose.yml": "services: [unclosed\n",
    }
    _, payload, _ = _run(tmp_path, files, "docker-compose.yml")
    report = payload["reports"][0]
    assert report["summary"]["verdict"] == "error"
    assert [f["reason_code"] for f in report["errors"]] == ["source_parse_error"]
    assert report["errors"][0]["variables"] == ["A"]
    assert report["unverified"] == []
    assert _verdict(payload, "A") == ("error", "source_parse_error")


def test_malformed_contract_is_error(tmp_path):
    files = {"envshield.yml": ONE, "env.schema.toml": "[A\n", ".env": "A=1\n"}
    _, payload, _ = _run(tmp_path, files)
    report = payload["reports"][0]
    assert report["summary"]["verdict"] == "error"
    assert report["errors"][0]["reason_code"] == "contract_error"


def test_topology_error_is_error(tmp_path):
    files = {
        "envshield.yml": ONE
        + "manifests:\n  - files: [a.yml, b.yml]\n    containers:\n      app: app\n",
        "env.schema.toml": "[A]\n",
        ".env": "A=1\n",
        "a.yml": "kind: Pod\n",
        "b.yml": "kind: Pod\n",
    }
    _, payload, _ = _run(tmp_path, files)
    report = payload["reports"][0]
    assert report["summary"]["verdict"] == "error"
    assert report["unverified"] == []


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads any file")
def test_an_unreadable_file_is_error_not_a_crash(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _materialize(
            {"envshield.yml": ONE, "env.schema.toml": "[A]\n", ".env": "A=1\n"}
        )
        os.chmod(".env", 0)
        try:
            result = runner.invoke(app, ["check", "--json"])
        finally:
            os.chmod(".env", 0o644)
    payload = json.loads(result.stdout)
    report = payload["reports"][0]
    assert result.exit_code == 1
    assert report["errors"][0]["reason_code"] == "source_unreadable"
    assert report["summary"]["verdict"] == "error"


# --- Aggregation --------------------------------------------------------------

OK, BAD, UNSEEN, NONE, ERR = (
    Verdict("valid"),
    Verdict("invalid", "missing"),
    Verdict("unverified", "host_forward"),
    Verdict("not_required"),
    Verdict("error", "source_parse_error"),
)


@pytest.mark.parametrize(
    "mix, expected",
    [
        ([OK, OK, NONE], "valid"),
        ([OK] * 8 + [UNSEEN, BAD], "invalid"),
        ([OK, UNSEEN], "unverified"),
        ([UNSEEN, UNSEEN, NONE], "unverified"),
        ([BAD, UNSEEN], "invalid"),
        ([BAD, ERR], "error"),
        ([ERR, OK, OK], "error"),
        ([NONE, NONE], "not_required"),
        ([], "not_required"),
    ],
)
def test_precedence(mix, expected):
    assert verdicts.worst(mix).verdict == expected


def test_mixed_service_lists_every_section(tmp_path):
    """VALID + COULD NOT VERIFY + INVALID: INVALID, with all three listed (BL-178)."""
    files = {
        "envshield.yml": ONE,
        "env.schema.toml": "[DATABASE_URL]\n\n[API_URL]\n\n[LOG]\n",
        "docker-compose.yml": _compose(
            "      DATABASE_URL: ${DATABASE_URL}\n      LOG: info\n"
        ),
    }
    _, payload, human = _run(tmp_path, files, "docker-compose.yml")
    report = payload["reports"][0]
    assert report["summary"]["verdict"] == "invalid"
    assert report["summary"]["counts"] == {
        "valid": 1,
        "invalid": 1,
        "unverified": 1,
        "not_required": 0,
        "error": 0,
    }
    assert [f["variable"] for f in report["invalid"]] == ["API_URL"]
    assert [f["variable"] for f in report["unverified"]] == ["DATABASE_URL"]
    # Human output lists the unverified name too, in v5's order.
    assert (
        human.index("Invalid")
        < human.index("API_URL")
        < human.index("Could not verify")
    )
    assert (
        human.index("Could not verify")
        < human.index("DATABASE_URL")
        < human.index("LOG")
    )
    assert "INVALID: 1 invalid, 1 could not be verified, 1 valid." in human


def test_no_required_variables_is_valid(tmp_path):
    files = {
        "envshield.yml": ONE,
        "env.schema.toml": "[A]\nrequired=false\n",
        ".env": "",
    }
    code, payload, human = _run(tmp_path, files)
    assert payload["reports"][0]["summary"]["verdict"] == "valid"
    assert payload["verdict"] == "valid"  # plan §10: "0 required variables" is VALID
    assert payload["success"] is True
    assert "VALID: 0 required variables." in human
    assert code == 0


def test_union_valid_anywhere_and_non_concrete_never_ok(tmp_path):
    files = {
        "envshield.yml": UNION + _manifest("docker-compose.yml"),
        "env.schema.toml": '[A]\n\n[B]\ntype="int"\n',
        ".env": "A=1\n",
        "docker-compose.yml": _compose("      A: ${A}\n      B: ${B}\n"),
    }
    _, payload, _ = _run(tmp_path, files)
    assert _verdict(payload, "A") == ("valid", None)
    assert _verdict(payload, "B") == ("unverified", "host_forward")


# --- Findings (BL-191) ----------------------------------------------------------


def test_findings_are_value_free_and_actionable(tmp_path):
    files = {
        "envshield.yml": ONE,
        "env.schema.toml": '[SECRET_KEY]\nsecret=true\ntype="url"\n\n[PORT]\ntype="port"\ndefaultValue="8000"\n',
        ".env": "SECRET_KEY=hunter2-not-a-url\n",
    }
    _, payload, human = _run(tmp_path, files)
    findings = {f["variable"]: f for f in payload["reports"][0]["findings"]}
    assert set(findings["SECRET_KEY"]) >= {
        "kind",
        "variable",
        "service",
        "location",
        "secret",
        "reason_code",
        "next_step",
    }
    assert findings["SECRET_KEY"]["secret"] is True
    assert findings["SECRET_KEY"]["location"]["file"] == ".env"
    assert findings["SECRET_KEY"]["next_step"]["by"] == "human"
    assert findings["PORT"]["next_step"]["by"] == "agent"  # has a default
    for f in findings.values():
        assert "setup" not in f["next_step"]["text"]
    assert "hunter2" not in json.dumps(payload) + human


# --- The invariant: VALID implies a validated Concrete state -----------------

INVARIANT_PROJECTS = {
    **{f"golden:{k}": v["files"] for k, v in SCENARIOS.items()},
    **{
        f"matrix:{k}": {
            "envshield.yml": ONE
            + (_manifest(p) if p not in (".env", "config.py") else ""),
            "env.schema.toml": s,
            p: c,
            "app.env": "A=1\n",
        }
        for k, (s, p, c, _, _) in MATRIX.items()
        if p != "config.py"
    },
    **{
        f"compose:{k}": {
            "envshield.yml": ONE + _manifest("docker-compose.yml"),
            "env.schema.toml": '[PORT]\ntype="port"\n',
            ".env": "PORT=1\n",
            "docker-compose.yml": _compose(
                "      PORT:\n" if e is None else f"      PORT: {e}\n"
            ),
        }
        for k, (e, _) in COMPOSE.items()
    },
    "union": {
        "envshield.yml": UNION + _manifest("docker-compose.yml", "k8s.yml"),
        "env.schema.toml": '[A]\n\n[B]\ntype="int"\n\n[C]\n',
        ".env": "C=1\n",
        "docker-compose.yml": _compose("      A: ${A:-x}\n      B: '2'\n"),
        "k8s.yml": SECRET
        + _pod(
            "        - name: A\n          valueFrom: {secretKeyRef: {name: db, key: url}}\n"
        ),
    },
}


@pytest.mark.parametrize("name", sorted(INVARIANT_PROJECTS))
def test_valid_only_from_a_validated_concrete_value(name, tmp_path):
    """
    Every per-source VALID is a Concrete state that passes validation, and
    a variable is VALID only if some source is (union mode included).
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _materialize(INVARIANT_PROJECTS[name])
        try:
            targets = service_manager.resolve_targets(None, invocation_dir=os.getcwd())
        except EnvShieldException:
            return  # the scenario fails before anything is evaluated
        for target in targets:
            evaluation = evaluator.evaluate_service(target)
            fields = evaluation.view.fields if evaluation.view else {}
            layers = {
                m["path"]: m["paths"]
                for m in config_manager.get_deployment_manifests(target)
            }
            for var, (overall, per_source) in evaluation.judged().items():
                for verdict, source in zip(per_source, evaluation.sources):
                    if verdict is None or verdict.verdict != "valid":
                        continue
                    _, states = get_manifest_parser_and_states(
                        layers.get(source.path, [source.path]),
                        container=source.container,
                        prefer=target,
                    )
                    state = states.get(var)
                    assert isinstance(state, Concrete), (var, state)
                    assert schema_types.validate_value(state.value, fields[var]) is None
                if overall.verdict == "valid":
                    assert any(
                        v is not None and v.verdict == "valid" for v in per_source
                    )


def test_a_secret_fallback_never_reaches_output(tmp_path):
    """A Compose fallback is manifest text and may be a secret literal."""
    files = {
        "envshield.yml": ONE,
        "env.schema.toml": '[PASS]\nsecret=true\ntype="url"\n',
        "docker-compose.yml": _compose("      PASS: ${PASS:-hunter2}\n"),
    }
    _, payload, human = _run(tmp_path, files, "docker-compose.yml")
    assert _verdict(payload, "PASS") == ("invalid", "invalid_fallback")
    assert "hunter2" not in json.dumps(payload) + human


def test_gate_and_accepted_reasons():
    f = lambda var, reason: {"variable": var, "reason_code": reason}  # noqa: E731
    unverified = [f("A", "host_forward"), f("B", "external_reference")]
    both = ["host_forward", "external_reference"]
    assert verdicts.gate("valid", [], []) == {"result": "pass", "accepted": {}}
    assert verdicts.gate("unverified", unverified, both)["result"] == "pass"
    assert verdicts.gate("unverified", unverified, ["host_forward"]) == {
        "result": "fail",
        "accepted": {"host_forward": ["A"]},
    }
    assert verdicts.gate("invalid", unverified, both)["result"] == "fail"
    assert verdicts.gate("error", [], both)["result"] == "fail"
    # COULD NOT VERIFY with nothing to accept fails closed.
    assert verdicts.gate("unverified", [], both)["result"] == "fail"
    ambiguous = [f("C", "ambiguous_requirement")]
    assert (
        verdicts.gate("unverified", ambiguous, list(verdicts.ACCEPTABLE))["result"]
        == "fail"
    )

    assert verdicts.parse_accepted(
        ["host_forward,", "value_not_inspected", "host_forward"]
    ) == [
        "host_forward",
        "value_not_inspected",
    ]
    for bad in (
        ["all"],
        ["ambiguous_requirement"],
        ["source_unreadable"],
        ["blank"],
        ["x"],
        [""],
        [","],
    ):
        with pytest.raises(ValueError):
            verdicts.parse_accepted(bad)
