# envshield/tests/test_import_credentials.py
"""BL-208 (#94, P0): a value that holds a credential never becomes a schema
defaultValue.

Invariant (AGENTS.md section 4): a secret value must never enter an output
representation -- here env.schema.toml, which is committed, and the
.env.example generated from it. Tested for the class: every credential
form, through every path that writes imported values into a schema.
"""

import os
import subprocess

import pytest
import toml
from typer.testing import CliRunner

from envshield.cli import app
from envshield.core import importer

runner = CliRunner()

PASSWORD = "zq9pw4BL208"

# (variable, value) pairs whose value carries PASSWORD, under names with no
# secret keyword: only the value can tell.
CREDENTIAL_VALUES = [
    ("REDIS_URL", f"redis://:{PASSWORD}@cache:6379/0"),
    ("DATABASE_URL", f"postgres://app:{PASSWORD}@db:5432/app"),
    ("MONGO_URI", f"mongodb+srv://app:{PASSWORD}%40x@cluster.example.net/db"),
    ("BROKER_URL", f"amqp://guest:{PASSWORD}@rabbit:5672//"),
    ("SQL_CONN", f"Server=db;User Id=app;Password={PASSWORD};"),
    ("PG_CONNINFO", f"host=db user=app password={PASSWORD} dbname=app"),
    ("ODBC_CONN", f"Driver=x;Uid=app;Pwd={PASSWORD}"),
]

# Names that hold credentials in real projects (BL-208's trial evidence).
CREDENTIAL_NAMES = [
    "NEXT_PRIVATE_SIGNING_PASSPHRASE",
    "GOOGLE_API_CREDENTIALS",
    "GCLOUD_APPLICATION_CREDENTIALS_CONTENTS",
    "DB_PASSWD",
    "API_KEYS",
    "AUTH_TOKENS",
]

# These keep their default: nothing secret in them.
SAFE_VALUES = [
    ("LOCAL_DB_URL", "postgres://app@localhost:5432/app"),
    ("API_BASE", "https://api.example.com/v1"),
    ("SUPPORT_EMAIL", "help@example.com"),
    ("LOG_LEVEL", "info"),
]


def _git(*args):
    subprocess.run(["git", *args], check=True, capture_output=True)


def _write(path, content):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


def _read(path):
    with open(path) as f:
        return f.read()


def _env_lines(pairs):
    return "".join(f"{k}={v}\n" for k, v in pairs)


def _assert_no_password_written():
    for path in ("env.schema.toml", ".env.example"):
        if os.path.exists(path):
            assert PASSWORD not in _read(path), path


@pytest.mark.parametrize("key,value", CREDENTIAL_VALUES)
def test_value_with_a_credential_is_secret(key, value):
    assert importer.value_embeds_credential(value)
    assert importer._classify_variable(key, value) == (True, None)


@pytest.mark.parametrize("key", CREDENTIAL_NAMES)
def test_credential_names_are_secret(key):
    assert importer._classify_variable(key, "opaque-value-123") == (True, None)


@pytest.mark.parametrize("key,value", SAFE_VALUES)
def test_values_without_a_credential_keep_their_default(key, value):
    assert not importer.value_embeds_credential(value)
    assert importer._classify_variable(key, value) == (False, value)


@pytest.mark.parametrize(
    "key", ["MONKEY_PATCH", "MONKEYS", "AUTHORS", "KEYBOARD_LAYOUT", "PASSES_ENABLED"]
)
def test_plural_matching_stays_whole_token(key):
    assert not importer.key_contains_secret_keyword(key)


def test_init_writes_no_credential_into_schema_or_template(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _git("init", "-q")
        pairs = CREDENTIAL_VALUES + [(k, PASSWORD) for k in CREDENTIAL_NAMES]
        _write(".env", _env_lines(pairs + SAFE_VALUES))
        result = runner.invoke(app, ["init"], input="")
        assert result.exit_code == 0, result.stdout
        _assert_no_password_written()
        assert PASSWORD not in result.stdout
        schema = toml.load("env.schema.toml")
        for key, _ in pairs:
            assert schema[key]["secret"] is True, key
            assert "defaultValue" not in schema[key], key
        for key, value in SAFE_VALUES:
            assert schema[key]["defaultValue"] == value
        # The user is told which values held a password.
        flat = " ".join(result.stdout.split())
        assert "holds a password" in flat
        for key, _ in CREDENTIAL_VALUES:
            assert key in flat


def test_init_from_a_compose_manifest(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _git("init", "-q")
        env = "".join(f'      {k}: "{v}"\n' for k, v in CREDENTIAL_VALUES)
        _write(
            "docker-compose.yml",
            "services:\n  app:\n    image: x\n    environment:\n" + env,
        )
        result = runner.invoke(app, ["init", "--from", "docker-compose.yml"])
        assert result.exit_code == 0, result.stdout
        _assert_no_password_written()
        assert PASSWORD not in result.stdout


def test_init_from_a_python_config_module_as_local_values(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _git("init", "-q")
        body = "".join(f'{k} = "{v}"\n' for k, v in CREDENTIAL_VALUES)
        _write("settings.py", body)
        result = runner.invoke(
            app, ["init", "--from", "settings.py", "--as-local-values"]
        )
        assert result.exit_code == 0, result.stdout
        _assert_no_password_written()


def test_refresh_with_init_from_force(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _git("init", "-q")
        _write(".env", "LOG_LEVEL=info\n")
        assert runner.invoke(app, ["init"], input="").exit_code == 0
        _write("prod.env", _env_lines(CREDENTIAL_VALUES))
        result = runner.invoke(app, ["init", "--from", "prod.env", "--force"])
        assert result.exit_code == 0, result.stdout
        _assert_no_password_written()


def test_merge_from_other_sources(tmp_path):
    # init's "also found in another config file" path.
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _write("other.env", _env_lines(CREDENTIAL_VALUES))
        schema = {}
        added = importer.merge_variables_from_other_sources(schema, ["other.env"])
        assert set(added["other.env"]) == {k for k, _ in CREDENTIAL_VALUES}
        for key, _ in CREDENTIAL_VALUES:
            assert schema[key]["secret"] is True
            assert "defaultValue" not in schema[key]
        assert PASSWORD not in toml.dumps(schema)
