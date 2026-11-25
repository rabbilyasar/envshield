# envshield/tests/core/test_schema_merge.py
"""V6-1 (#26): 'envshield schema merge' and doctor's legacy-layout notices."""

import json
import os
import subprocess

import pytest
import toml
from typer.testing import CliRunner

from envshield.cli import app
from envshield.core import schema_merge

runner = CliRunner()
ENVS = ["local"]


def _write(path, text):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def _read(path):
    with open(path) as f:
        return f.read()


def _legacy(schemas, config_extra="", env_files=None):
    """A legacy project: each service in its own directory with its own schema."""
    subprocess.run(["git", "init", "-q"], check=True)
    lines = ["project_name: t", "services:"]
    for name, text in schemas.items():
        lines += [f"  {name}:", f"    schema: {name}/env.schema.toml"]
        _write(f"{name}/env.schema.toml", text)
    _write("envshield.yml", config_extra + "\n".join(lines) + "\n")
    for path, text in (env_files or {}).items():
        _write(path, text)


def _merge(*args, **kwargs):
    return runner.invoke(app, ["schema", "merge", *args], **kwargs)


def _runs(command):
    """Runs to completion (any exit code), never a crash."""
    result = runner.invoke(app, command)
    assert result.exception is None or isinstance(result.exception, SystemExit), (
        command,
        result.exception,
    )
    return result


def _verdicts():
    payload = json.loads(runner.invoke(app, ["check", "--json"]).stdout)
    return {
        r["service"]: {v["name"]: v["verdict"] for v in r["variables"]}
        for r in payload["reports"]
    }


# --- merge() ------------------------------------------------------------------


def test_identical_tables_become_one_and_a_shared_non_secret_is_unscoped():
    result = schema_merge.merge(
        {
            "a": {"PORT": {"type": "port", "description": "p"}, "A_ONLY": {}},
            "b": {"PORT": {"type": "port", "description": "p"}},
        },
        ENVS,
    )
    assert result.conflicts == []
    assert result.contract["PORT"] == {"type": "port", "description": "p"}
    # Only some services had it: granted to them alone.
    assert result.contract["A_ONLY"] == {"services": ["a"]}


def test_semantically_equal_tables_are_not_a_conflict():
    result = schema_merge.merge(
        {
            "a": {"X": {"type": "string", "required": True, "defaultValue": 8080}},
            "b": {"X": {"defaultValue": "8080"}},
        },
        ENVS,
    )
    assert result.conflicts == []
    assert "services" not in result.contract["X"]


def test_differing_description_and_default_become_overrides():
    result = schema_merge.merge(
        {
            "a": {
                "PORT": {"type": "port", "description": "api", "defaultValue": "8000"}
            },
            "b": {
                "PORT": {
                    "type": "port",
                    "description": "worker",
                    "defaultValue": "9000",
                }
            },
        },
        ENVS,
    )
    assert result.contract["PORT"] == {
        "type": "port",
        "services": {
            "a": {"description": "api", "defaultValue": "8000"},
            "b": {"description": "worker", "defaultValue": "9000"},
        },
    }


@pytest.mark.parametrize(
    "attribute, a, b",
    [
        ("type", {"type": "int"}, {"type": "url"}),
        ("secret", {"secret": True}, {}),
        ("enum", {"type": "enum", "enum": ["x"]}, {"type": "enum", "enum": ["y"]}),
        ("pattern", {"pattern": "^a$"}, {"pattern": "^b$"}),
        ("required", {"required": False}, {}),
        (
            "requiredIf",
            {"requiredIf": {"var": "F", "equals": "1"}},
            {"requiredIf": {"var": "F", "equals": "2"}},
        ),
    ],
)
def test_each_conflicting_attribute_is_refused(attribute, a, b):
    result = schema_merge.merge({"a": {"X": a, "Y": a}, "b": {"X": b, "Y": b}}, ENVS)
    assert result.contract == {}
    assert {(c.variable, c.attribute) for c in result.conflicts} == {
        ("X", attribute),
        ("Y", attribute),
    }


def test_a_secret_is_never_broadened():
    result = schema_merge.merge(
        {
            "a": {"KEY": {"secret": True}, "ALL": {"secret": True}},
            "b": {"ALL": {"secret": True}},
        },
        ENVS,
    )
    assert result.contract["KEY"]["services"] == ["a"]
    # Every service had it, and it's still granted by name, never unscoped.
    assert result.contract["ALL"]["services"] == ["a", "b"]


def test_a_single_service_needs_no_grants():
    result = schema_merge.merge({"a": {"KEY": {"secret": True}, "P": {}}}, ENVS)
    assert result.contract == {"KEY": {"secret": True}, "P": {}}


def test_render_round_trips():
    contract = {
        "PORT": {"type": "port", "services": {"a": {"defaultValue": "1"}, "b": {}}},
        "COND": {"requiredIf": {"var": "PORT", "equals": "1"}, "services": ["a"]},
    }
    text = schema_merge.render(contract, ["a/env.schema.toml"])
    assert toml.loads(text) == contract
    assert "comments in those files are not carried over" in text.lower()


# --- The command ----------------------------------------------------------------

A = """\
[DATABASE_URL]
description = "Main database"
type = "url"
secret = true

[PORT]
description = "Port"
type = "port"
defaultValue = "8000"

[LOG_LEVEL]
description = "Log level"
"""
B = """\
[PORT]
description = "Port"
type = "port"
defaultValue = "9000"

[LOG_LEVEL]
description = "Log level"
"""
ENV = {
    "a/.env": "DATABASE_URL=postgres://db/app\nPORT=8000\nLOG_LEVEL=info\n",
    "b/.env": "PORT=9000\n",
}


def test_merge_writes_the_contract_and_applies_with_yes(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _legacy({"a": A, "b": B}, env_files=ENV)
        before = _verdicts()
        old = {p: _read(p) for p in ("a/env.schema.toml", "b/env.schema.toml")}
        result = _merge("--yes")
        assert result.exit_code == 0, result.stdout
        contract = toml.loads(_read("env.schema.toml"))
        assert contract["DATABASE_URL"]["services"] == ["a"]
        assert contract["PORT"]["services"] == {
            "a": {"defaultValue": "8000"},
            "b": {"defaultValue": "9000"},
        }
        assert "services" not in contract["LOG_LEVEL"]
        config = _read("envshield.yml")
        assert "schema: env.schema.toml" in config
        assert "dir: a" in config and "dir: b" in config
        # Old schema files are byte-identical, and verdicts didn't move.
        for path, text in old.items():
            assert _read(path) == text
        assert _verdicts() == before
        assert "not carried over" in " ".join(result.stdout.split())


def test_without_confirmation_envshield_yml_is_unchanged(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _legacy({"a": A, "b": B}, env_files=ENV)
        config = _read("envshield.yml")
        result = _merge()
        assert result.exit_code == 0, result.stdout
        assert _read("envshield.yml") == config
        assert "+schema: env.schema.toml" in result.stdout
        assert "--yes" in result.stdout
        # Running again with --yes applies it: the contract already there
        # is exactly the merge, so it isn't "overwriting".
        assert _merge("--yes").exit_code == 0
        assert "schema: env.schema.toml" in _read("envshield.yml")


def test_an_existing_output_is_refused_unless_another_path_is_given(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _legacy({"a": A, "b": B}, env_files=ENV)
        _write("env.schema.toml", "# mine\n")
        result = _merge("--yes")
        assert result.exit_code == 1
        assert "--output" in result.stdout
        assert _read("env.schema.toml") == "# mine\n"
        result = _merge("--output", "contract.toml", "--yes")
        assert result.exit_code == 0, result.stdout
        assert "PORT" in toml.loads(_read("contract.toml"))
        assert "schema: contract.toml" in _read("envshield.yml")
        assert _read("env.schema.toml") == "# mine\n"


def test_conflicts_are_all_listed_and_nothing_is_written(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _legacy(
            {
                "a": '[PORT]\ntype = "port"\n[KEY]\nsecret = true\n',
                "b": '[PORT]\ntype = "int"\n[KEY]\n',
            }
        )
        config = _read("envshield.yml")
        result = _merge("--yes")
        out = " ".join(result.stdout.split())
        assert result.exit_code == 1
        assert "PORT: 'type' differs" in out
        assert "KEY: 'secret' differs" in out
        assert "Nothing was written" in out
        assert not os.path.exists("env.schema.toml")
        assert _read("envshield.yml") == config


def test_a_verdict_change_stops_the_merge(tmp_path):
    # COND is conditional on FLAG, which only b defines: merged, COND (for a)
    # would depend on a variable a isn't granted, which doesn't load.
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _legacy(
            {
                "a": '[COND]\nrequiredIf = { var = "FLAG", equals = "on" }\n',
                "b": "[FLAG]\n",
            },
            env_files={"a/.env": "COND=1\n", "b/.env": "FLAG=on\n"},
        )
        config = _read("envshield.yml")
        result = _merge("--yes")
        out = " ".join(result.stdout.split())
        assert result.exit_code == 1, result.stdout
        assert "Not merged" in out
        assert "Nothing was written" in out
        assert not os.path.exists("env.schema.toml")
        assert not any(n.endswith(".merge-tmp") for n in os.listdir("."))
        assert _read("envshield.yml") == config


def test_scenario_9_three_schemas_with_extends(tmp_path):
    """A legacy project with three schemas and 'extends': every command
    keeps working, and the merge gives every service the same verdicts."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _write(
            "shared/base.schema.toml",
            '[LOG_LEVEL]\ndescription = "Log level"\ndefaultValue = "info"\n',
        )
        schemas = {
            name: f'extends = "../shared/base.schema.toml"\n{body}'
            for name, body in {
                "api": '[API_KEY]\nsecret = true\n[PORT]\ntype = "port"\n',
                "web": '[PORT]\ntype = "port"\n[PUBLIC_URL]\ntype = "url"\n',
                "worker": '[QUEUE_URL]\ntype = "url"\nsecret = true\n',
            }.items()
        }
        _legacy(
            schemas,
            env_files={
                "api/.env": "API_KEY=k\nPORT=80\nLOG_LEVEL=info\n",
                "web/.env": "PORT=not-a-port\nPUBLIC_URL=https://x.example\n",
                "worker/.env": "QUEUE_URL=amqp://q\n",
            },
        )
        before = _verdicts()
        for command in (["doctor"], ["explain", "PORT", "--service", "api"]):
            _runs(command)
        result = _merge("--yes")
        assert result.exit_code == 0, result.stdout
        contract = toml.loads(_read("env.schema.toml"))
        assert "extends" not in contract
        assert "services" not in contract["LOG_LEVEL"]  # every service had it
        assert contract["API_KEY"]["services"] == ["api"]
        assert contract["QUEUE_URL"]["services"] == ["worker"]
        assert contract["PORT"]["services"] == ["api", "web"]
        assert _verdicts() == before
        for command in (
            ["doctor"],
            ["explain", "PORT", "--service", "api"],
            ["schema", "sync"],
        ):
            _runs(command)


# --- doctor notices ---------------------------------------------------------------


def _layout_check():
    result = runner.invoke(app, ["doctor", "--json"])
    checks = json.loads(result.stdout)["results"][0]["checks"]
    found = [c for c in checks if c["name"] == "Schema Layout"]
    return result.exit_code, (found[0] if found else None)


def test_doctor_suggests_merge_for_several_schemas(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _legacy({"a": A, "b": B}, env_files=ENV)
        code_before = runner.invoke(app, ["doctor"]).exit_code
        code, check = _layout_check()
        assert check["passed"] is True
        assert "envshield schema merge" in check["message"]
        assert runner.invoke(app, ["doctor"]).exit_code == code_before


def test_doctor_suggests_the_top_level_schema_for_one_named_per_service(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        subprocess.run(["git", "init", "-q"], check=True)
        _write(
            "envshield.yml",
            "services:\n  a:\n    dir: a\n    schema: env.schema.toml\n"
            "  b:\n    dir: b\n    schema: env.schema.toml\n",
        )
        _write("env.schema.toml", "[PORT]\n")
        _, check = _layout_check()
        assert check["passed"] is True
        assert "top-level 'schema:'" in check["message"]


def test_doctor_has_no_notice_for_the_project_contract_or_one_service(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _legacy({"a": A}, env_files=ENV)
        assert _layout_check()[1] is None
        _legacy({"a": A, "b": B}, env_files=ENV)
        assert _merge("--yes").exit_code == 0
        assert _layout_check()[1] is None


# --- Reading each service's code once for both layouts -------------------------


def test_the_gate_reads_each_services_code_once(tmp_path, monkeypatch):
    from envshield.core import discovery

    with runner.isolated_filesystem(temp_dir=tmp_path):
        _legacy({"a": A, "b": B}, env_files=ENV)
        _write("a/app.py", 'import os\nos.environ["PORT"]\n')
        _write("b/app.py", 'import os\nos.getenv("UNDECLARED_B")\n')
        reads = []
        real = discovery.discover_references

        def counting(content, path):
            reads.append(path)
            return real(content, path)

        monkeypatch.setattr(discovery, "discover_references", counting)
        result = _merge("--yes")
        assert result.exit_code == 0, result.stdout
        # Once for both layouts, not once per layout.
        assert sorted(reads) == ["a/app.py", "b/app.py"]


def test_reused_code_references_are_keyed_on_the_files_read(tmp_path, monkeypatch):
    from envshield.core import discovery, evaluator

    with runner.isolated_filesystem(temp_dir=tmp_path):
        _legacy({"a": A})
        _write("a/one.py", 'import os\nos.environ["ONE"]\n')
        reads = []
        real = discovery.discover_references

        def counting(content, path):
            reads.append(path)
            return real(content, path)

        monkeypatch.setattr(discovery, "discover_references", counting)
        with evaluator.reuse_code_references():
            first = evaluator.discover_code_references("a")
            again = evaluator.discover_code_references("a")
            assert reads == ["a/one.py"]
            assert [u.variable for u in again.usages] == ["ONE"]
            again.usages.clear()  # a caller's change never reaches the cache
            assert evaluator.discover_code_references("a").usages
            _write("a/two.py", 'import os\nos.environ["TWO"]\n')
            changed = evaluator.discover_code_references("a")
        assert sorted(reads) == ["a/one.py", "a/one.py", "a/two.py"]
        assert {u.variable for u in changed.usages} == {"ONE", "TWO"}
        assert first.files_scanned == 1
        # Outside the block nothing is reused.
        evaluator.discover_code_references("a")
        assert len(reads) == 5
