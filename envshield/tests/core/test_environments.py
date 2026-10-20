# envshield/tests/core/test_environments.py
"""
V4-1 (#23): environments, requiredIn and --env (plan §7). The requiredness
table itself is in test_schema_types.py.
"""

import json
import os

import pytest
from typer.testing import CliRunner

from envshield.cli import app
from envshield.config import manager as config_manager
from envshield.core.exceptions import (
    ConfigParseError,
    InvalidManifestDefinitionError,
    SchemaValidationError,
    UnknownEnvironmentError,
)

runner = CliRunner()

SERVICE = "services:\n  app:\n    schema: env.schema.toml\n"
ENVS = "environments: [staging, production]\n"


def _manifest(path, environment=None):
    tag = f"    environment: {environment}\n" if environment else ""
    return f"  - file: {path}\n{tag}    containers:\n      app: app\n"


def _compose(environment):
    return f"services:\n  app:\n    image: x\n    environment:\n{environment}"


def _write(files):
    for path, content in files.items():
        with open(path, "w") as f:
            f.write(content)


def _check(tmp_path, files, *args, env=None):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _write(files)
        result = runner.invoke(app, ["check", *args, "--json"], env=env)
        human = runner.invoke(
            app, ["check", *args], env={"COLUMNS": "200", **(env or {})}
        )
    payload = json.loads(result.stdout) if result.exit_code != 2 else None
    return result.exit_code, payload, human.stdout + (human.stderr or "")


def _verdicts(payload):
    return {
        v["name"]: (v["verdict"], v["reason_code"])
        for r in payload["reports"]
        for v in r["variables"]
    }


# --- envshield.yml: environments ------------------------------------------


def _environments(tmp_path, config):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _write({"envshield.yml": SERVICE + config})
        return config_manager.get_environments()


def test_local_is_built_in(tmp_path):
    assert _environments(tmp_path, "") == ["local"]
    assert _environments(tmp_path, ENVS) == ["local", "staging", "production"]


@pytest.mark.parametrize(
    "config, fragment",
    [
        ("environments: production\n", "must be a list"),
        ("environments: [Production]\n", "isn't a valid name"),
        ("environments: [prod env]\n", "isn't a valid name"),
        ("environments: [local]\n", "'local' is built in"),
        ("environments: [staging, staging]\n", "declared twice"),
    ],
)
def test_an_invalid_declaration_is_a_load_error(tmp_path, config, fragment):
    with pytest.raises(ConfigParseError, match=fragment):
        _environments(tmp_path, config)


def test_env_precedence(tmp_path, monkeypatch):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _write({"envshield.yml": SERVICE + ENVS})
        monkeypatch.delenv("ENVSHIELD_ENV", raising=False)
        assert config_manager.resolve_environment() == "local"
        monkeypatch.setenv("ENVSHIELD_ENV", "staging")
        assert config_manager.resolve_environment() == "staging"
        assert config_manager.resolve_environment("production") == "production"
        with pytest.raises(
            UnknownEnvironmentError, match="Declared: local, staging, production"
        ):
            config_manager.resolve_environment("prod")


# --- The contract: requiredIn ---------------------------------------------


@pytest.mark.parametrize(
    "schema, error",
    [
        (
            '[A]\nrequiredIn = ["prod"]\n',
            "names 'prod', which envshield.yml doesn't declare",
        ),
        (
            '[A]\nrequired = true\nrequiredIn = ["production"]\n',
            "sets 'required' and 'requiredIn'",
        ),
        (
            '[A]\nrequiredIn = ["production"]\nrequiredIf = {var = "B"}\n',
            "sets 'requiredIn' and 'requiredIf'",
        ),
    ],
)
def test_contract_load_errors(tmp_path, schema, error):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _write({"envshield.yml": SERVICE + ENVS, "env.schema.toml": schema})
        with pytest.raises(SchemaValidationError, match=error):
            config_manager.load_schema("app")


def test_requiredin_local_needs_no_declaration(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _write(
            {
                "envshield.yml": SERVICE,
                "env.schema.toml": '[A]\nrequiredIn = ["local"]\n',
            }
        )
        assert config_manager.load_schema("app")["A"]["requiredIn"] == ["local"]


def test_a_manifest_tagged_with_an_undeclared_environment_is_an_error(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _write(
            {
                "envshield.yml": SERVICE
                + ENVS
                + "manifests:\n"
                + _manifest("c.yml", "prod")
            }
        )
        with pytest.raises(InvalidManifestDefinitionError, match="'prod'"):
            config_manager.get_deployment_manifests("app")


# --- check --env --------------------------------------------------------------

PROD = {
    "envshield.yml": SERVICE
    + ENVS
    + "manifests:\n"
    + _manifest("local.yml")
    + _manifest("prod.yml", "production"),
    ".env": "",
    "local.yml": _compose("      OTHER: x\n"),
    "prod.yml": _compose("      OTHER: x\n"),
}


def test_scenario_11_a_defaulted_variable_absent_from_production(tmp_path):
    files = {**PROD, "env.schema.toml": '[OTHER]\n\n[LEVEL]\ndefaultValue = "info"\n'}
    code, payload, _ = _check(tmp_path, files, "--env", "production")
    assert _verdicts(payload)["LEVEL"] == ("invalid", "missing")
    assert payload["environment"] == "production"
    assert code == 1

    files["env.schema.toml"] = (
        '[OTHER]\n\n[LEVEL]\ndefaultValue = "info"\nrequiredIn = ["staging"]\n'
    )
    code, payload, _ = _check(tmp_path, files, "--env", "production")
    assert _verdicts(payload)["LEVEL"] == ("not_required", None)
    assert code == 0


def test_scenario_12_required_only_in_production(tmp_path, mocker):
    schema = '[OTHER]\n\n[STRIPE_KEY]\nsecret = true\nrequiredIn = ["production"]\n'
    files = {**PROD, ".env": "OTHER=x\n", "env.schema.toml": schema}

    # Absent locally: NOT REQUIRED, and the local check passes.
    code, payload, _ = _check(tmp_path, files)
    assert _verdicts(payload)["STRIPE_KEY"] == ("not_required", None)
    assert code == 0
    # Absent from the production manifest: INVALID.
    code, payload, _ = _check(tmp_path, files, "--env", "production")
    assert _verdicts(payload)["STRIPE_KEY"] == ("invalid", "missing")
    assert code == 1

    # setup never asks for it.
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _write(files)
        prompt = mocker.patch("envshield.core.setup_manager.Prompt.ask")
        mocker.patch("questionary.confirm").return_value.ask.return_value = False
        result = runner.invoke(app, ["setup"])
    assert result.exit_code == 0, result.stdout
    assert all("STRIPE_KEY" not in str(c) for c in prompt.call_args_list)


def test_each_environment_checks_only_its_own_manifests(tmp_path):
    files = {
        **PROD,
        "env.schema.toml": "[A]\n",
        ".env": "A=1\n",
        "local.yml": _compose("      A: '1'\n"),
        "prod.yml": _compose("      B: '1'\n"),
    }
    code, payload, _ = _check(tmp_path, files)
    paths = [s["path"] for r in payload["reports"] for s in r["sources"]]
    assert paths == [".env", "local.yml"]
    assert code == 0

    code, payload, _ = _check(tmp_path, files, "--env", "production")
    paths = [s["path"] for r in payload["reports"] for s in r["sources"]]
    assert paths == ["prod.yml"]
    assert code == 1


def test_an_environment_with_no_tagged_manifest_is_an_error_not_a_pass(tmp_path):
    files = {
        "envshield.yml": SERVICE + ENVS,
        "env.schema.toml": "[A]\n",
        ".env": "A=1\n",
    }
    code, payload, human = _check(tmp_path, files, "--env", "staging")
    assert payload["verdict"] == "error"
    assert payload["reports"][0]["errors"][0]["reason_code"] == "topology_error"
    assert "tagged 'staging'" in human
    assert code == 1


def test_an_explicit_file_is_judged_by_the_chosen_environment(tmp_path):
    """No manifest needed: what a deploy script's env file is checked with."""
    files = {
        "envshield.yml": SERVICE + ENVS,
        "env.schema.toml": '[A]\n\n[GIT_SHA]\nrequiredIn = ["production"]\n',
        ".env": "A=1\n",
        "prod.env": "A=1\n",
    }
    code, payload, _ = _check(tmp_path, files, "prod.env", "--env", "production")
    assert _verdicts(payload)["GIT_SHA"] == ("invalid", "missing")
    assert code == 1


@pytest.mark.parametrize("how", ["flag", "variable"])
def test_an_undeclared_environment_is_a_usage_error(tmp_path, how):
    files = {
        "envshield.yml": SERVICE + ENVS,
        "env.schema.toml": "[A]\n",
        ".env": "A=1\n",
    }
    args, env = (
        (["--env", "prod"], None) if how == "flag" else ([], {"ENVSHIELD_ENV": "prod"})
    )
    code, _, output = _check(tmp_path, files, *args, env=env)
    assert code == 2
    assert "Unknown environment 'prod'" in output and "production" in output


def test_environment_variable_selects_the_environment(tmp_path):
    files = {**PROD, "env.schema.toml": '[OTHER]\n\n[K]\nrequiredIn = ["production"]\n'}
    code, payload, _ = _check(tmp_path, files, env={"ENVSHIELD_ENV": "production"})
    assert payload["environment"] == "production"
    assert _verdicts(payload)["K"] == ("invalid", "missing")


def test_declaring_environments_changes_no_verdict(tmp_path):
    """
    A schema without requiredIn judges the same in every environment as in
    'local' (plan §7), given the same source in each.
    """
    schema = (
        '[REQ]\n\n[OPT]\nrequired = false\ntype = "int"\n\n[DEF]\ndefaultValue = "1"\n\n'
        '[FLAG]\nenum = ["on", "off"]\n\n[IF]\nrequiredIf = {var = "FLAG", equals = "on"}\n'
    )
    compose = _compose("      FLAG: 'on'\n      OPT: x\n")
    config = (
        SERVICE
        + ENVS
        + "manifests:\n"
        + "".join(
            _manifest(f"{e}.yml", None if e == "local" else e)
            for e in ("local", "staging", "production")
        )
    )
    files = {"envshield.yml": config, "env.schema.toml": schema, ".env": ""}
    files.update({f"{e}.yml": compose for e in ("local", "staging", "production")})
    per_env = {}
    for env in ("local", "staging", "production"):
        _, payload, _ = _check(tmp_path, files, "--env", env)
        report = payload["reports"][0]
        per_env[env] = {
            v["name"]: [s["verdict"] for s in v["sources"] if s["path"] == f"{env}.yml"]
            for v in report["variables"]
        }
    assert per_env["staging"] == per_env["local"] == per_env["production"]


# --- KemonChilo (BL-187, BL-201): deploy-only variables -------------------------

KEMONCHILO_SCHEMA = """
[DATABASE_URL]
type = "url"
secret = true

[GIT_SHA]
description = "Set by scripts/deploy.sh at build time. Never in .env."
requiredIn = ["staging", "production"]

[BUILD_TIME]
requiredIn = ["staging", "production"]

[KEMONCHILO_ENV]
enum = ["staging", "production"]
requiredIn = ["staging", "production"]

[WORKER_HEARTBEAT_URL]
type = "url"
secret = true
requiredIn = ["production"]

[NODE_ENV]
description = "Set by the run command, never by hand."
required = false

[RECOVERY_TIMING_SCALE]
description = "Local testing only."
required = false
"""


def test_kemonchilo_is_green_locally_and_still_strict_in_production(tmp_path):
    files = {
        "envshield.yml": SERVICE + ENVS,
        "env.schema.toml": KEMONCHILO_SCHEMA,
        ".env": "DATABASE_URL=postgres://localhost:5432/dev\n",
        "production.env": "DATABASE_URL=postgres://db:5432/prod\nKEMONCHILO_ENV=production\n",
    }
    code, payload, _ = _check(tmp_path, files)
    assert payload["verdict"] == "valid", _verdicts(payload)
    assert code == 0

    code, payload, _ = _check(tmp_path, files, "production.env", "--env", "production")
    missing = sorted(n for n, (v, r) in _verdicts(payload).items() if r == "missing")
    assert missing == ["BUILD_TIME", "GIT_SHA", "WORKER_HEARTBEAT_URL"]
    assert code == 1


# --- doctor, explain, schema sync ----------------------------------------------


def _doctor(tmp_path, files, *args):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _write(files)
        result = runner.invoke(app, ["doctor", *args, "--json"])
    return result.exit_code, json.loads(result.stdout)


def test_doctor_warns_when_no_manifest_is_tagged(tmp_path):
    files = {
        "envshield.yml": SERVICE + ENVS + "manifests:\n" + _manifest("c.yml"),
        "env.schema.toml": "[A]\n",
        ".env": "A=1\n",
        "c.yml": _compose("      A: '1'\n"),
    }
    _, payload = _doctor(tmp_path, files)
    checks = {c["name"]: c for c in payload["results"][0]["checks"]}
    assert checks["Deployment Environments"]["passed"] is True
    assert "Warning" in checks["Deployment Environments"]["message"]

    files["envshield.yml"] = (
        SERVICE + ENVS + "manifests:\n" + _manifest("c.yml", "production")
    )
    _, payload = _doctor(tmp_path, files)
    names = [c["name"] for c in payload["results"][0]["checks"]]
    assert "Deployment Environments" not in names


def test_doctor_does_not_warn_a_project_without_manifests(tmp_path):
    files = {
        "envshield.yml": SERVICE + ENVS,
        "env.schema.toml": "[A]\n",
        ".env": "A=1\n",
    }
    _, payload = _doctor(tmp_path, files)
    assert "Deployment Environments" not in [
        c["name"] for c in payload["results"][0]["checks"]
    ]


def test_doctor_env_checks_that_environments_manifests(tmp_path):
    files = {
        "envshield.yml": SERVICE
        + ENVS
        + "manifests:\n"
        + _manifest("prod.yml", "production"),
        "env.schema.toml": '[A]\n\n[P]\nrequiredIn = ["production"]\n',
        ".env": "A=1\n",
        "prod.yml": _compose("      A: '1'\n"),
    }
    _, payload = _doctor(tmp_path, files, "--env", "production")
    checks = {c["name"]: c for c in payload["results"][0]["checks"]}
    assert "Local Environment Sync" not in checks
    assert checks["Deployment Manifest"]["passed"] is False
    assert "P" in checks["Deployment Manifest"]["message"]


def test_explain_env(tmp_path):
    files = {
        "envshield.yml": SERVICE + ENVS,
        "env.schema.toml": '[GIT_SHA]\nrequiredIn = ["staging", "production"]\n',
    }
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _write(files)
        local = json.loads(runner.invoke(app, ["explain", "GIT_SHA", "--json"]).stdout)
        prod = json.loads(
            runner.invoke(
                app, ["explain", "GIT_SHA", "--env", "production", "--json"]
            ).stdout
        )
        human = runner.invoke(
            app, ["explain", "GIT_SHA", "--env", "production"], env={"COLUMNS": "200"}
        ).stdout
    assert local["schema"]["requiredness"] == "optional" and "environment" not in local
    assert (
        prod["schema"]["requiredness"] == "required"
        and prod["environment"] == "production"
    )
    assert prod["schema"]["requiredIn"] == ["staging", "production"]
    assert "Yes in production (requiredIn: staging, production)" in human


def test_env_example_names_where_it_is_required(tmp_path):
    files = {
        "envshield.yml": SERVICE + ENVS,
        "env.schema.toml": '[GIT_SHA]\nrequiredIn = ["staging", "production"]\n',
    }
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _write(files)
        assert runner.invoke(app, ["schema", "sync"]).exit_code == 0
        with open(".env.example") as f:
            assert "# required in staging, production" in f.read()
        assert not os.path.exists(".env")
