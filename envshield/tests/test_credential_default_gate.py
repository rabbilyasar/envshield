# envshield/tests/test_credential_default_gate.py
"""BL-209 (#96): a schema whose defaultValue holds a credential is refused.

Invariant (AGENTS.md section 4): a secret value must never enter an output
representation. A default is copied into .env.example and generated code,
so a password in one is committed with them. #94 stopped the importer from
writing one; this refuses one already in a schema (written before that fix,
or by hand), names the field and never the value, and keeps `schema diff`
from printing it from history. Tested for the class: every credential form,
every way a default reaches a schema, every command that loads one.
"""

import json
import os
import subprocess

import pytest
from typer.testing import CliRunner

from envshield.cli import app
from envshield.config import manager as config_manager
from envshield.core import contract_diff, explain, generator, schema_types
from envshield.core.exceptions import CredentialDefaultError

from .test_import_credentials import CREDENTIAL_VALUES, PASSWORD, SAFE_VALUES

runner = CliRunner()


def _git(*args):
    subprocess.run(["git", *args], check=True, capture_output=True)


def _write(path, content):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


def _toml_str(value):
    return json.dumps(value)  # a TOML basic string is JSON-compatible here


def _project(schema, config="services:\n  api:\n    schema: env.schema.toml\n"):
    _git("init", "-q")
    _git("config", "user.email", "t@example.com")
    _git("config", "user.name", "T")
    _git("config", "commit.gpgsign", "false")
    _write("envshield.yml", config)
    _write("env.schema.toml", schema)
    _write(".env", "")


def _field(key, value, secret_line="secret = false\n"):
    return (
        f'[{key}]\ndescription = "d"\n{secret_line}defaultValue = {_toml_str(value)}\n'
    )


# --- The predicate -----------------------------------------------------------


@pytest.mark.parametrize("key,value", CREDENTIAL_VALUES)
def test_default_holding_a_credential_must_not_render(key, value):
    field = {"defaultValue": value, "secret": False}
    assert schema_types.default_holds_credential(field)
    assert schema_types.default_must_not_render(field)


@pytest.mark.parametrize("key,value", SAFE_VALUES)
def test_safe_default_renders(key, value):
    field = {"defaultValue": value}
    assert not schema_types.default_must_not_render(field)


@pytest.mark.parametrize("value", [8000, True, None, ["a"], ""])
def test_non_string_or_empty_default_is_not_a_credential(value):
    assert not schema_types.default_holds_credential({"defaultValue": value})


# --- Schema loading refuses it -------------------------------------------------


@pytest.mark.parametrize("key,value", CREDENTIAL_VALUES)
@pytest.mark.parametrize("secret_line", ["secret = false\n", ""])
def test_load_refuses_and_names_only_the_field(tmp_path, key, value, secret_line):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project(_field(key, value, secret_line) + _field("LOG_LEVEL", "info"))
        with pytest.raises(CredentialDefaultError) as exc:
            config_manager.load_schema("api")
        assert key in str(exc.value)
        assert "LOG_LEVEL" not in str(exc.value)
        assert PASSWORD not in str(exc.value)


def test_load_refuses_an_inherited_default(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project('extends = "base.schema.toml"\n' + _field("LOG_LEVEL", "info"))
        _write("base.schema.toml", _field(*CREDENTIAL_VALUES[0]))
        with pytest.raises(CredentialDefaultError, match=CREDENTIAL_VALUES[0][0]):
            config_manager.load_schema("api")


def test_load_refuses_a_per_service_override(tmp_path):
    key, value = CREDENTIAL_VALUES[0]
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project(
            f'[{key}]\ndescription = "d"\n\n[{key}.services.api]\n\n'
            f"[{key}.services.worker]\ndefaultValue = {_toml_str(value)}\n",
            config=(
                "schema: env.schema.toml\nservices:\n"
                "  api:\n    dir: api\n  worker:\n    dir: worker\n"
            ),
        )
        for service in ("api", "worker"):
            with pytest.raises(CredentialDefaultError) as exc:
                config_manager.load_schema(service)
            assert f"{key} (services.worker)" in str(exc.value)
            assert PASSWORD not in str(exc.value)


def test_safe_defaults_still_load(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project("".join(_field(k, v) for k, v in SAFE_VALUES))
        schema = config_manager.load_schema("api")
        for key, value in SAFE_VALUES:
            assert schema[key]["defaultValue"] == value


# --- Every command that loads the schema ----------------------------------------


@pytest.mark.parametrize(
    "args",
    [
        ["check"],
        ["check", "--json"],
        ["check", "--code-only", "--json"],
        ["doctor", "--json"],
        ["schema", "sync"],
        ["explain", "REDIS_URL"],
        ["generate"],
    ],
)
def test_commands_fail_without_printing_the_password(tmp_path, args):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project(_field(*CREDENTIAL_VALUES[0]))
        result = runner.invoke(app, args)
        output = result.stdout + (result.stderr or "")
        assert result.exit_code != 0, output
        assert "REDIS_URL" in output
        assert PASSWORD not in output
        if args == ["schema", "sync"]:
            assert not os.path.exists(".env.example")


def test_pre_commit_check_blocks_the_schema(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project(_field(*CREDENTIAL_VALUES[0]))
        _git("add", "env.schema.toml", "envshield.yml")
        result = runner.invoke(app, ["scan", "--staged"])
        output = result.stdout + (result.stderr or "")
        assert result.exit_code != 0, output
        assert PASSWORD not in output


# --- Backstops where a default is rendered ---------------------------------------


@pytest.mark.parametrize("key,value", CREDENTIAL_VALUES)
def test_renderers_never_emit_it(key, value):
    field = {"description": "d", "secret": False, "defaultValue": value}
    python_src, _ = generator._render_python_field(key, field)
    assert PASSWORD not in python_src
    assert PASSWORD not in generator._render_ts_field(key, field)
    assert PASSWORD not in json.dumps(explain._describe_field(field))


# --- schema diff never prints it from history -------------------------------------


@pytest.mark.parametrize("key,value", CREDENTIAL_VALUES)
def test_diff_hides_a_credential_default(key, value):
    before = {key: {"description": "d", "defaultValue": value}}
    after = {key: {"description": "d", "defaultValue": value + "x"}}
    changes = [c for c in contract_diff.diff_schemas(before, after).changes]
    changed = [c for c in changes if c.category == "default_changed"]
    assert changed
    for change in changed:
        assert PASSWORD not in change.description
        assert PASSWORD not in json.dumps(change.detail)
        assert change.detail["redacted"] is True


def test_diff_hides_a_secret_default_from_history():
    # A pre-BL-001 schema could hold one; a revision isn't load-gated.
    before = {"K": {"secret": True, "defaultValue": "old-" + PASSWORD}}
    after = {"K": {"secret": True, "defaultValue": "new-" + PASSWORD}}
    for change in contract_diff.diff_schemas(before, after).changes:
        assert PASSWORD not in change.description
        assert PASSWORD not in json.dumps(change.detail)


def test_diff_still_shows_a_plain_default_change():
    before = {"LOG_LEVEL": {"defaultValue": "info"}}
    after = {"LOG_LEVEL": {"defaultValue": "debug"}}
    (change,) = contract_diff.diff_schemas(before, after).changes
    assert "'info'" in change.description and "'debug'" in change.description
    assert change.detail == {"before": "info", "after": "debug"}


def test_schema_diff_cli_on_the_fixing_commit(tmp_path):
    # The commit that removes a leaked default must still diff cleanly,
    # without printing the password from the old revision.
    key, value = CREDENTIAL_VALUES[0]
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project(_field(key, value))
        _git("add", "-A")
        _git("commit", "-q", "-m", "leak")
        _write("env.schema.toml", f'[{key}]\ndescription = "d"\nsecret = true\n')
        result = runner.invoke(app, ["schema", "diff", "--json"])
        assert PASSWORD not in result.stdout
        payload = json.loads(result.stdout)
        assert "error" not in payload, payload
