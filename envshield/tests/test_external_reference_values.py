# envshield/tests/test_external_reference_values.py
"""
BL-213 (#103): a value that is a secret manager's reference or an encrypted
value ('op://...', SOPS 'ENC[...]', ...) is COULD NOT VERIFY
`external_reference`, whatever the field's type: never VALID (EnvShield
never saw the real value) and never INVALID (the text isn't what the
application gets). The reference text never reaches any output.
"""

import json
import os
import subprocess

import pytest
from typer.testing import CliRunner

from envshield.cli import app
from envshield.core import verdicts
from envshield.parsers._base import (
    Concrete,
    Referenced,
    ValueStates,
    read_value,
    reference_form,
)

runner = CliRunner()

# (value, form). Each value carries a canary, so a leak is easy to spot.
REFERENCES = [
    ("op://dev/postgres/CANARY1", "a 1Password reference"),
    ("sm://my-project/CANARY2", "a Berglas (Google Cloud) reference"),
    ("berglas://bucket/CANARY3", "a Berglas (Google Cloud) reference"),
    ("vault:secret/data/db#CANARY4", "a Vault reference"),
    ("encrypted:BDqR3x7lK9pQzWm4vN8tCANARY5", "a dotenvx-encrypted value"),
    (
        "ENC[AES256_GCM,data:CANARY6==,iv:abc=,tag:def=,type:str]",
        "a SOPS-encrypted value",
    ),
]
CANARIES = ["CANARY1", "CANARY2", "CANARY3", "CANARY4", "CANARY5", "CANARY6"]

# Field types a reference must never be judged against.
FIELDS = {
    "string": {},
    "url": {"type": "url"},
    "port": {"type": "port"},
    "enum": {"type": "enum", "enum": ["a", "b"]},
    "secret": {"secret": True},
}


class TestReferenceForm:
    @pytest.mark.parametrize("value,form", REFERENCES)
    def test_recognised(self, value, form):
        assert reference_form(value) == form
        assert read_value(value) == Referenced(form)

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "https://example.com/op://x",
            "https://user:pass@db.example.com/app",
            "postgres://localhost/db",
            "op:/missing-slash",
            "op://",
            "vault",
            "vault: spaced",
            "vault:1.15",  # a Docker image tag
            "vault:latest",
            "encrypted:short",
            "encrypted",
            "ENC[not sops]",
            "see op://dev/x",  # not the whole value
            "op://dev/x trailing words",
            "plain-value",
        ],
    )
    def test_ordinary_values_stay_concrete(self, value):
        assert reference_form(value) is None
        assert read_value(value) == Concrete(value)

    def test_repr_never_holds_the_text(self):
        state = read_value("op://dev/postgres/CANARY1")
        assert "CANARY1" not in repr(state)


class TestVerdict:
    @pytest.mark.parametrize("value,form", REFERENCES)
    @pytest.mark.parametrize("field", sorted(FIELDS))
    @pytest.mark.parametrize("need", [verdicts.REQUIRED, verdicts.OPTIONAL])
    def test_never_valid_or_invalid(self, value, form, field, need):
        verdict = verdicts.judge_variable(FIELDS[field], read_value(value), need)
        assert verdict.verdict == verdicts.UNVERIFIED
        assert verdict.reason == "external_reference"
        assert verdict.detail.startswith(form)
        assert value not in verdict.detail

    def test_accepted_reason(self):
        assert "external_reference" in verdicts.ACCEPTABLE

    def test_a_reference_trigger_makes_requiredif_ambiguous(self):
        states = ValueStates({"MODE": read_value("op://dev/app/mode")})
        field = {"requiredIf": {"var": "MODE", "equals": "prod"}}
        assert verdicts.requirement(field, states) == verdicts.AMBIGUOUS

    def test_next_step_local_and_runtime(self):
        detail = verdicts.judge_variable(
            {}, read_value("op://dev/x/y"), verdicts.REQUIRED
        ).detail
        local = verdicts.next_step(
            "external_reference", variable="K", file=".env", detail=detail
        )
        assert local["by"] == "human"
        assert "a 1Password reference in .env" in local["text"]
        assert "--process-env" in local["text"]
        runtime = verdicts.next_step(
            "external_reference", variable="K", detail=detail, runtime_env="prod"
        )
        assert "prod deploy environment" in runtime["text"]
        assert "resolves it" in runtime["text"]

    def test_kubernetes_next_step_unchanged(self):
        step = verdicts.next_step(
            "external_reference",
            variable="K",
            detail="Secret 'db' is defined outside this manifest",
        )
        assert "supplied by the cluster" in step["text"]


# --- Through the CLI --------------------------------------------------------

SCHEMA = """
[STRING_VAR]
[URL_VAR]
type = "url"
[PORT_VAR]
type = "port"
[ENUM_VAR]
type = "enum"
enum = ["a", "b"]
[SECRET_VAR]
secret = true
[PLAIN_URL]
type = "url"
"""

CONFIG = """project_name: t
schema: env.schema.toml
services:
  app:
    dir: "."
"""

NAMES = ["STRING_VAR", "URL_VAR", "PORT_VAR", "ENUM_VAR", "SECRET_VAR"]


def _project(files):
    subprocess.run(["git", "init", "-q"], check=True)
    for path, content in files.items():
        if os.path.dirname(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(content)


def _dotenv(values):
    return "".join(f"{k}={v}\n" for k, v in values.items())


def _variables(payload):
    report = payload["reports"][0]
    return {v["name"]: v for v in report["variables"]}


def _assert_no_canary(*texts):
    for text in texts:
        for canary in CANARIES:
            assert canary not in text


@pytest.mark.parametrize("value,form", REFERENCES)
def test_check_local_file(tmp_path, value, form):
    env = {name: value for name in NAMES}
    env["PLAIN_URL"] = "https://example.com/path"
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project(
            {"env.schema.toml": SCHEMA, "envshield.yml": CONFIG, ".env": _dotenv(env)}
        )
        as_json = runner.invoke(app, ["check", "--json"], env={"COLUMNS": "200"})
        as_text = runner.invoke(app, ["check"], env={"COLUMNS": "200"})
        accepted = runner.invoke(
            app, ["check", "--accept-unverified", "external_reference"]
        )
    _assert_no_canary(as_json.stdout, as_text.stdout, accepted.stdout)
    assert as_json.exit_code == 1, as_json.stdout
    assert as_text.exit_code == 1
    assert accepted.exit_code == 0, accepted.stdout
    payload = json.loads(as_json.stdout)
    variables = _variables(payload)
    for name in NAMES:
        assert variables[name]["verdict"] == "unverified", name
        assert variables[name]["reason_code"] == "external_reference"
    # A real URL beside them is still judged.
    assert variables["PLAIN_URL"]["verdict"] == "valid"
    # The v1 fields don't call them invalid either.
    assert payload["results"][0]["invalid"] == {}
    assert form in as_text.stdout


def test_an_ordinary_value_is_still_judged(tmp_path):
    env = {
        "STRING_VAR": "https://example.com/op://x",
        "URL_VAR": "not a url",
        "PORT_VAR": "99999",
        "ENUM_VAR": "c",
        "SECRET_VAR": "s",
        "PLAIN_URL": "https://example.com",
    }
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project(
            {"env.schema.toml": SCHEMA, "envshield.yml": CONFIG, ".env": _dotenv(env)}
        )
        result = runner.invoke(app, ["check", "--json"])
    variables = _variables(json.loads(result.stdout))
    assert variables["STRING_VAR"]["verdict"] == "valid"
    for name in ("URL_VAR", "PORT_VAR", "ENUM_VAR"):
        assert variables[name]["verdict"] == "invalid", name


def test_runtime(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project({"env.schema.toml": SCHEMA, "envshield.yml": CONFIG})
        environment = {
            "STRING_VAR": "op://dev/app/CANARY1",
            "URL_VAR": "https://example.com",
            "PORT_VAR": "80",
            "ENUM_VAR": "a",
            "SECRET_VAR": "vault:secret/data/CANARY4",
            "PLAIN_URL": "https://example.com",
        }
        result = runner.invoke(
            app, ["check", "--runtime", "--env", "local", "--json"], env=environment
        )
    _assert_no_canary(result.stdout)
    assert result.exit_code == 1
    variables = _variables(json.loads(result.stdout))
    assert variables["STRING_VAR"]["reason_code"] == "external_reference"
    assert variables["SECRET_VAR"]["reason_code"] == "external_reference"
    assert variables["URL_VAR"]["verdict"] == "valid"


def test_process_env_with_the_resolved_value_is_valid(tmp_path):
    """What the next step suggests: run through the secret manager with
    --process-env, and the injected value is judged."""
    env = {name: "op://dev/app/CANARY1" for name in NAMES}
    env["PLAIN_URL"] = "https://example.com"
    resolved = {
        "STRING_VAR": "s",
        "URL_VAR": "https://example.com",
        "PORT_VAR": "80",
        "ENUM_VAR": "a",
        "SECRET_VAR": "sk_live_resolved",
    }
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project(
            {"env.schema.toml": SCHEMA, "envshield.yml": CONFIG, ".env": _dotenv(env)}
        )
        result = runner.invoke(app, ["check", "--process-env", "--json"], env=resolved)
    assert result.exit_code == 0, result.stdout
    assert "sk_live_resolved" not in result.stdout
    _assert_no_canary(result.stdout)


def test_compose_literal_and_env_file(tmp_path):
    from envshield.parsers._docker_compose import DockerComposeParser

    (tmp_path / "docker-compose.yml").write_text(
        """services:
  app:
    env_file: app.env
    environment:
      STRING_VAR: "op://dev/app/CANARY1"
      URL_VAR: https://example.com
      FORWARDED: ${HOST_VALUE}
"""
    )
    (tmp_path / "app.env").write_text(
        "SECRET_VAR=ENC[AES256_GCM,data:CANARY6==,type:str]\n"
    )
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        states = DockerComposeParser().get_states("docker-compose.yml")
    finally:
        os.chdir(cwd)
    assert states["STRING_VAR"] == Referenced("a 1Password reference")
    assert states["SECRET_VAR"] == Referenced("a SOPS-encrypted value")
    assert states["URL_VAR"] == Concrete("https://example.com")
    assert "HOST_VALUE" in states  # interpolation is unchanged


def test_kubernetes_literal_value(tmp_path):
    from envshield.parsers._kubernetes import KubernetesParser

    manifest = tmp_path / "deploy.yaml"
    manifest.write_text(
        """apiVersion: apps/v1
kind: Deployment
metadata: {name: app}
spec:
  template:
    spec:
      containers:
        - name: app
          env:
            - name: DB_URL
              value: "vault:secret/data/db#CANARY4"
            - name: PLAIN
              value: "x"
"""
    )
    states = KubernetesParser().get_states(str(manifest))
    assert states["DB_URL"] == Referenced("a Vault reference")
    assert states["PLAIN"] == Concrete("x")


def test_python_config_module(tmp_path):
    from envshield.parsers.factory import get_parser

    module = tmp_path / "settings.py"
    module.write_text('API_KEY = "op://dev/stripe/CANARY1"\nDEBUG = "true"\n')
    states = get_parser(str(module)).get_states(str(module))
    assert states["API_KEY"] == Referenced("a 1Password reference")
    assert states["DEBUG"] == Concrete("true")
