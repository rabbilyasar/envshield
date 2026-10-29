# envshield/tests/test_contract_cli.py
"""V2-2 (#19): init, service add, service discover and init --from keep one
project contract, appending to it and never rewriting it."""

import json
import os
import subprocess

import pytest
import toml
import yaml
from typer.testing import CliRunner

from envshield.cli import app
from envshield.config import manager as config_manager
from envshield.core import contract_writer

runner = CliRunner()

CONTRACT_PROJECT = """\
schema: env.schema.toml
services:
  root:
    dir: .
"""

HAND_WRITTEN = """\
# Hand-maintained. Keep me.
[LOG_LEVEL]
description = "Log level"   # why: ops asked
defaultValue = "info"
"""


def _git_init():
    subprocess.run(["git", "init", "-q"], check=True)


def _write(path, content):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


def _read_bytes(path="env.schema.toml"):
    with open(path, "rb") as f:
        return f.read()


def _config():
    with open("envshield.yml") as f:
        return yaml.safe_load(f)


def _contract_project():
    _git_init()
    _write("envshield.yml", CONTRACT_PROJECT)
    _write("env.schema.toml", HAND_WRITTEN)


def _flat(result):
    return " ".join(result.stdout.split())


# --- service add -----------------------------------------------------------------


def test_service_add_registers_on_the_contract(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _contract_project()
        os.makedirs("api")
        before = _read_bytes()
        result = runner.invoke(app, ["service", "add", "api", "api"])
        assert result.exit_code == 0, result.stdout
        assert _config()["services"]["api"] == {"dir": "api"}
        assert not os.path.exists("api/env.schema.toml")
        assert _read_bytes() == before
        assert config_manager.get_service_schema_path("api") == "env.schema.toml"
        assert "project contract" in _flat(result)


def test_service_add_import_appends_with_grants(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _contract_project()
        _write("api/.env", "API_TOKEN=abc\nAPI_TIMEOUT=30\n")
        before = _read_bytes()
        result = runner.invoke(
            app, ["service", "add", "api", "api", "--import", "api/.env"]
        )
        assert result.exit_code == 0, result.stdout
        after = _read_bytes()
        assert after.startswith(before)
        schema = toml.loads(after.decode())
        assert schema["API_TOKEN"]["secret"] is True
        assert schema["API_TOKEN"]["services"] == ["api"]
        # Two services now: a non-secret is scoped too.
        assert schema["API_TIMEOUT"]["services"] == ["api"]
        # No terminal to confirm a default observed in a local file.
        assert "defaultValue" not in schema["API_TIMEOUT"]
        assert "API_TIMEOUT" in _flat(result) and "no terminal" in _flat(result)


def test_service_add_rejects_a_directory_another_service_has(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _contract_project()
        os.makedirs("api")
        assert runner.invoke(app, ["service", "add", "api", "api"]).exit_code == 0
        result = runner.invoke(app, ["service", "add", "api2", "api"])
        assert result.exit_code == 1
        assert "api2" not in _config()["services"]


def test_service_add_with_explicit_schema_keeps_its_own(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _contract_project()
        os.makedirs("legacy")
        result = runner.invoke(
            app,
            [
                "service",
                "add",
                "legacy",
                "legacy",
                "--schema",
                "legacy/env.schema.toml",
            ],
        )
        assert result.exit_code == 0, result.stdout
        assert _config()["services"]["legacy"]["schema"] == "legacy/env.schema.toml"


# --- service discover ----------------------------------------------------------------


def test_service_discover_appends_every_directory(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _contract_project()
        _write("api/.env", "API_TOKEN=abc\nPORT=8000\n")
        _write("web/.env", "WEB_TOKEN=xyz\nPORT=8000\n")
        before = _read_bytes()
        result = runner.invoke(app, ["service", "discover", "--yes"])
        assert result.exit_code == 0, result.stdout
        after = _read_bytes()
        assert after.startswith(before)
        config = _config()
        assert config["services"]["api"]["dir"] == "api"
        assert config["services"]["web"]["dir"] == "web"
        assert "schema" not in config["services"]["api"]
        schema = toml.loads(after.decode())
        assert schema["API_TOKEN"]["services"] == ["api"]
        assert schema["WEB_TOKEN"]["services"] == ["web"]
        assert schema["PORT"]["services"] == ["api", "web"]
        assert schema["PORT"]["defaultValue"] == "8000"


# --- init --from FILE on a project with a contract -------------------------------------


def test_init_from_appends_and_never_rewrites(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _contract_project()
        _write("extra.env", "NEW_SECRET_TOKEN=abc\nLOG_LEVEL=debug\n")
        before = _read_bytes()
        result = runner.invoke(
            app, ["init", "--from", "extra.env", "--service", "root", "--force"]
        )
        assert result.exit_code == 0, result.stdout
        after = _read_bytes()
        assert after.startswith(before)
        schema = toml.loads(after.decode())
        assert schema["LOG_LEVEL"]["defaultValue"] == "info"  # untouched
        assert schema["NEW_SECRET_TOKEN"]["services"] == ["root"]


@pytest.mark.parametrize("answer", [True, False])
def test_init_from_asks_before_committing_an_observed_default(tmp_path, mocker, answer):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _contract_project()
        _write("extra.env", "CACHE_TTL=300\n")
        mocker.patch("envshield.core.hooks_manager._is_interactive", return_value=True)
        confirm = mocker.patch("questionary.confirm")
        confirm.return_value.ask.return_value = answer
        result = runner.invoke(
            app, ["init", "--from", "extra.env", "--service", "root"]
        )
        assert result.exit_code == 0, result.stdout
        prompts = [c.args[0] for c in confirm.call_args_list]
        assert any("CACHE_TTL" in p and "default" in p for p in prompts)
        entry = toml.load("env.schema.toml")["CACHE_TTL"]
        assert ("defaultValue" in entry) is answer


# --- init --force on a contract: re-scan and append -------------------------------------


def test_init_force_appends_new_variables_and_keeps_hand_edits(tmp_path, mocker):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _git_init()
        mocker.patch("questionary.confirm").return_value.ask.return_value = True
        _write(".env", "PORT=8000\nAPI_SECRET=abc\n")
        assert runner.invoke(app, ["init"]).exit_code == 0
        name = os.path.basename(os.getcwd())
        assert toml.load("env.schema.toml")["API_SECRET"]["services"] == [name]
        # A hand edit, then the source gains a variable.
        with open("env.schema.toml", "a") as f:
            f.write('\n# hand-added\n[MANUAL]\ndescription = "m"\n')
        before = _read_bytes()
        _write(".env", "PORT=8000\nAPI_SECRET=abc\nNEW_FLAG=on\n")
        result = runner.invoke(app, ["init", "--force"])
        assert result.exit_code == 0, result.stdout
        after = _read_bytes()
        assert after.startswith(before)
        schema = toml.loads(after.decode())
        assert "NEW_FLAG" in schema and "MANUAL" in schema


def test_init_then_service_add_keeps_everything_loading(tmp_path):
    # Secrets are scoped from the start, so a second service never turns
    # them into unscoped secrets in a shared schema.
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _git_init()
        _write(".env", "DATABASE_PASSWORD=x\nPORT=8000\n")
        assert runner.invoke(app, ["init"]).exit_code == 0
        os.makedirs("worker")
        assert runner.invoke(app, ["service", "add", "worker", "worker"]).exit_code == 0
        name = os.path.basename(os.getcwd())
        assert "DATABASE_PASSWORD" in config_manager.load_schema(name)
        assert "DATABASE_PASSWORD" not in config_manager.load_schema("worker")


# --- Legacy projects: per-service schemas, one tip line -----------------------------------


def test_legacy_service_add_is_unchanged_but_for_the_tip(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _git_init()
        _write("envshield.yml", "services:\n  api:\n    schema: api/env.schema.toml\n")
        _write("api/env.schema.toml", HAND_WRITTEN)
        os.makedirs("web")
        result = runner.invoke(app, ["service", "add", "web", "web"])
        assert result.exit_code == 0, result.stdout
        assert _config()["services"]["web"] == {"schema": "web/env.schema.toml"}
        assert "schema" not in _config()
        flat = _flat(result)
        assert " ".join(contract_writer.LEGACY_TIP.split()) in flat
        assert flat.count("Tip:") == 1


def test_legacy_discover_is_unchanged_but_for_the_tip(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _git_init()
        _write("envshield.yml", "services:\n  api:\n    schema: api/env.schema.toml\n")
        _write("api/env.schema.toml", HAND_WRITTEN)
        _write("web/.env", "WEB_TOKEN=x\n")
        result = runner.invoke(app, ["service", "discover", "--yes"])
        assert result.exit_code == 0, result.stdout
        assert os.path.exists("web/env.schema.toml")
        assert _flat(result).count("Tip:") == 1


# --- Scenario 1 (#19 acceptance) ---------------------------------------------------------


def test_scenario_1_init_setup_check(tmp_path):
    """init in a repo with api/ and web/ -> one contract, both services
    registered with dir, secrets scoped; then setup and check -> VALID."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _git_init()
        _write("api/.env.example", "API_TOKEN=\nAPI_PORT=8000\n")
        _write("web/.env.example", "WEB_TOKEN=\nWEB_URL=http://localhost:3000\n")
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 0, result.stdout
        config = _config()
        assert config["schema"] == "env.schema.toml"
        assert config["services"]["api"]["dir"] == "api"
        assert config["services"]["web"]["dir"] == "web"
        schema = toml.load("env.schema.toml")
        assert schema["API_TOKEN"]["secret"] is True
        assert schema["API_TOKEN"]["services"] == ["api"]
        assert schema["WEB_TOKEN"]["services"] == ["web"]

        for service, answers in (("api", "tok-api\n"), ("web", "tok-web\n")):
            result = runner.invoke(app, ["setup", "--service", service], input=answers)
            assert result.exit_code == 0, result.stdout

        result = runner.invoke(app, ["check", "--json"])
        payload = json.loads(result.stdout)
        assert result.exit_code == 0, payload
        assert payload["verdict"] == "valid"
        assert {r["service"] for r in payload["reports"]} == {"api", "web"}
