# envshield/tests/test_secret_manager.py
"""
V3-3 (#22), check half: working with the team's secret manager through its
own run command (docs/architecture/secret-managers.md). Fake run commands
(shell scripts that set variables and exec their arguments) stand in for
every provider.
"""

import json
import os
import stat
import subprocess

import pytest
from typer.testing import CliRunner

from envshield.cli import app
from envshield.core import secret_manager, verdicts
from envshield.core.exceptions import ConfigParseError

runner = CliRunner()

CANARIES = ("VAULT_CANARY_1", "VAULT_CANARY_2", "STDERR_CANARY", "STDOUT_CANARY")

SCHEMA = """
[DATABASE_URL]
type = "url"
secret = true
[API_KEY]
secret = true
[PORT]
type = "port"
[PERSONAL_TOKEN]
secret = true
source = "local"
required = false
[SENTRY_DSN]
type = "url"
source = "secret-manager"
"""

SUPPLIED = {
    "DATABASE_URL": "postgres://app:VAULT_CANARY_1@db/app",
    "API_KEY": "VAULT_CANARY_2",
    "SENTRY_DSN": "https://VAULT_CANARY_1@sentry.example/1",
}


def _config(run="./fakevault --", secrets=True):
    text = 'project_name: t\nschema: env.schema.toml\nservices:\n  app:\n    dir: "."\n'
    if secrets:
        text += "secrets:\n"
        if run is not None:
            text += f"  run: {json.dumps(run)}\n"
    return text


def _fake(path, supplied, exit_code=None, sleep=None):
    """A fake run command: marks that it ran, writes canaries to its own
    stdout and stderr, sets `supplied`, then execs its arguments (or exits
    with `exit_code` without running them)."""
    lines = [
        "#!/bin/sh",
        'touch "$(dirname "$0")/ran.marker"',
        '[ "$1" = "--" ] && shift',
        "echo 'signing in STDERR_CANARY' >&2",
        "echo 'banner STDOUT_CANARY'",
    ]
    if sleep:
        lines.append(f"sleep {sleep}")
    for name, value in supplied.items():
        lines.append(f"export {name}='{value}'")
    lines.append(f"exit {exit_code}" if exit_code is not None else 'exec "$@"')
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    os.chmod(path, 0o755)


def _project(supplied=SUPPLIED, env_file="PORT=8080\n", config=None, **fake):
    subprocess.run(["git", "init", "-q"], check=True)
    with open("env.schema.toml", "w") as f:
        f.write(SCHEMA)
    with open("envshield.yml", "w") as f:
        f.write(config if config is not None else _config())
    if env_file is not None:
        with open(".env", "w") as f:
            f.write(env_file)
    _fake("fakevault", supplied, **fake)


def _variables(payload):
    return {v["name"]: v for v in payload["reports"][0]["variables"]}


def _findings(payload):
    return {
        f["variable"]: f for f in payload["reports"][0]["findings"] if f["variable"]
    }


def _no_canary(*texts):
    for text in texts:
        for canary in CANARIES:
            assert canary not in text, canary


@pytest.fixture
def terminal(monkeypatch):
    monkeypatch.setattr(secret_manager, "stdin_is_terminal", lambda: True)


# --- Configuration ----------------------------------------------------------


class TestConfig:
    @pytest.mark.parametrize(
        "text,run",
        [
            ("secrets:\n", None),
            ("secrets: {}\n", None),
            (
                'secrets:\n  run: "bws run --project-id 1 --"\n',
                "bws run --project-id 1 --",
            ),
        ],
    )
    def test_valid(self, tmp_path, monkeypatch, text, run):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "envshield.yml").write_text("project_name: t\n" + text)
        assert secret_manager.get_config().run == run

    def test_absent(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "envshield.yml").write_text("project_name: t\n")
        assert secret_manager.get_config() is None

    @pytest.mark.parametrize(
        "text",
        [
            "secrets: bws run --\n",
            "secrets:\n  command: x\n",
            "secrets:\n  run: 3\n",
            'secrets:\n  run: ""\n',
            'secrets:\n  run: "bws \\u001b[2K run --"\n',
            'secrets:\n  run: "bws \'unterminated"\n',
        ],
    )
    def test_invalid(self, tmp_path, monkeypatch, text):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "envshield.yml").write_text("project_name: t\n" + text)
        with pytest.raises(ConfigParseError):
            secret_manager.get_config()

    @pytest.mark.parametrize(
        "field,enabled,expected",
        [
            ({"secret": True}, True, True),
            ({"secret": True}, False, False),
            ({}, True, False),
            ({"secret": True, "source": "local"}, True, False),
            ({"source": "secret-manager"}, False, True),
            ({"source": "secret-manager"}, True, True),
        ],
    )
    def test_vault_supplied(self, field, enabled, expected):
        assert secret_manager.is_vault_supplied(field, enabled) is expected

    def test_bad_source_is_a_schema_error(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _project()
            with open("env.schema.toml", "a") as f:
                f.write('[OTHER]\nsource = "vault"\n')
            result = runner.invoke(app, ["check", "--json"])
        assert result.exit_code == 1
        assert "source must be" in result.stdout


class TestApproval:
    def test_approve_revoke(self, tmp_path):
        root = str(tmp_path)
        assert not secret_manager.is_approved(root, "bws run --")
        secret_manager.approve(root, "bws run --")
        assert secret_manager.is_approved(root, "bws run --")
        # A changed command is a different approval.
        assert not secret_manager.is_approved(root, "bws run --project-id 2 --")
        # Another project isn't approved by this one.
        other = tmp_path / "other"
        other.mkdir()
        assert not secret_manager.is_approved(str(other), "bws run --")
        assert secret_manager.revoke(root)
        assert not secret_manager.is_approved(root, "bws run --")
        assert not secret_manager.revoke(root)

    def test_file_is_private_and_holds_no_command(self, tmp_path):
        secret_manager.approve(str(tmp_path), "bws run --project-id SECRETPROJ --")
        path = secret_manager._approvals_path()
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
        content = open(path).read()
        assert "SECRETPROJ" not in content
        assert os.path.realpath(str(tmp_path)) in content

    def test_corrupt_file_means_nothing_approved(self, tmp_path):
        path = secret_manager._approvals_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write("{not json")
        assert not secret_manager.is_approved(str(tmp_path), "x")
        secret_manager.approve(str(tmp_path), "x")
        assert secret_manager.is_approved(str(tmp_path), "x")


# --- The probe --------------------------------------------------------------


class TestProbe:
    @pytest.mark.parametrize(
        "field,text,status",
        [
            ({"type": "url"}, "https://a.example", "valid"),
            ({"type": "url"}, "not a url", "invalid"),
            ({"type": "url"}, "", "blank"),
            ({}, None, "missing"),
            ({}, "op://dev/x/y", "referenced"),
        ],
    )
    def test_status(self, field, text, status):
        result = secret_manager.probe_status(field, text)
        assert result["status"] == status
        if text:
            assert text not in json.dumps(result)

    def test_parse_ignores_other_nonces_and_rejects_bad_shapes(self):
        good = secret_manager.probe_line(
            "abc", {"services": {"app": {"K": {"status": "valid"}}}}
        )
        assert secret_manager._parse_probe(good, "abc", ["app"]) == {
            "app": {"K": {"status": "valid", "detail": None}}
        }
        assert secret_manager._parse_probe(good, "other", ["app"]) is None
        forged = secret_manager.probe_line(
            "abc", {"services": {"app": {"K": {"status": "sk_live_x"}}}}
        )
        assert secret_manager._parse_probe(forged, "abc", ["app"]) is None
        error = secret_manager.probe_line("abc", {"error": "schema broke"})
        assert secret_manager._parse_probe(error, "abc", ["app"]) == "schema broke"


# --- check ------------------------------------------------------------------


def test_without_secrets_section_nothing_changes(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project(config=_config(secrets=False))
        with open("env.schema.toml", "w") as f:
            f.write('[DATABASE_URL]\ntype = "url"\nsecret = true\n')
        result = runner.invoke(app, ["check", "--json"])
    payload = json.loads(result.stdout)
    assert result.exit_code == 1
    assert _variables(payload)["DATABASE_URL"]["reason_code"] == "missing"
    assert payload["gate"]["accepted"] == {}


def test_plain_check_doesnt_run_it_and_passes_locally(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project()
        result = runner.invoke(app, ["check", "--json"])
        ran = os.path.exists("ran.marker")
    assert not ran
    payload = json.loads(result.stdout)
    assert result.exit_code == 0, result.stdout
    variables = _variables(payload)
    for name in ("DATABASE_URL", "API_KEY", "SENTRY_DSN"):
        assert variables[name]["reason_code"] == "secret_manager", name
    assert variables["PERSONAL_TOKEN"]["verdict"] == "not_required"
    assert variables["PORT"]["verdict"] == "valid"
    assert payload["gate"] == {
        "result": "pass",
        "accepted": {"secret_manager": ["API_KEY", "DATABASE_URL", "SENTRY_DSN"]},
    }
    assert payload["success"] is False
    step = _findings(payload)["API_KEY"]["next_step"]["text"]
    assert "--with-secrets" in step


def test_wrapped_by_hand_reads_the_injected_values(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project()
        result = runner.invoke(app, ["check", "--json"], env=SUPPLIED)
    _no_canary(result.stdout)
    assert result.exit_code == 0, result.stdout
    variables = _variables(json.loads(result.stdout))
    for name in SUPPLIED:
        assert variables[name]["verdict"] == "valid"


def test_wrapped_by_hand_invalid_value(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project()
        result = runner.invoke(
            app, ["check", "--json"], env={**SUPPLIED, "DATABASE_URL": "VAULT_CANARY_1"}
        )
    _no_canary(result.stdout)
    assert result.exit_code == 1
    assert _variables(json.loads(result.stdout))["DATABASE_URL"]["verdict"] == "invalid"


def test_with_secrets_needs_a_terminal(tmp_path, monkeypatch):
    monkeypatch.setattr(secret_manager, "stdin_is_terminal", lambda: False)
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project()
        secret_manager.approve(os.getcwd(), "./fakevault --")
        result = runner.invoke(app, ["check", "--with-secrets", "--json"])
        ran = os.path.exists("ran.marker")
    assert not ran
    assert result.exit_code == 1  # asked for, not checked: never accepted
    finding = _findings(json.loads(result.stdout))["API_KEY"]
    assert finding["reason_code"] == "secret_manager"
    assert "no terminal" in finding["detail"]
    assert "./fakevault -- envshield check" in finding["detail"]


def test_with_secrets_asks_first_and_declining_runs_nothing(tmp_path, terminal):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project()
        result = runner.invoke(app, ["check", "--with-secrets"], input="n\n")
        ran = os.path.exists("ran.marker")
        approved = secret_manager.is_approved(os.getcwd(), "./fakevault --")
    assert not ran and not approved
    assert "./fakevault --" in result.stdout
    assert result.exit_code == 1


def test_with_secrets_approved_once_then_runs(tmp_path, terminal):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project(supplied={**SUPPLIED, "SENTRY_DSN": "https://x@s.example/1"})
        first = runner.invoke(app, ["check", "--with-secrets"], input="y\n")
        assert os.path.exists("ran.marker")
        os.remove("ran.marker")
        second = runner.invoke(app, ["check", "--with-secrets", "--json"])
        ran_again = os.path.exists("ran.marker")
        # A pull request changes the command: asked again, not run.
        with open("envshield.yml", "w") as f:
            f.write(_config(run="./fakevault --verbose --"))
        os.remove("ran.marker")
        changed = runner.invoke(app, ["check", "--with-secrets"], input="n\n")
        ran_changed = os.path.exists("ran.marker")
    _no_canary(first.stdout, second.stdout, changed.stdout)
    assert first.exit_code == 0, first.stdout
    assert "Allow EnvShield to run this command?" in first.stdout
    assert ran_again
    assert second.exit_code == 0, second.stdout
    variables = _variables(json.loads(second.stdout))
    for name in SUPPLIED:
        assert variables[name]["verdict"] == "valid"
    assert "Allow EnvShield" in changed.stdout and "--verbose" in changed.stdout
    assert not ran_changed


def test_with_secrets_json_never_prompts(tmp_path, terminal):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project()
        result = runner.invoke(app, ["check", "--with-secrets", "--json"])
        ran = os.path.exists("ran.marker")
    assert not ran
    finding = _findings(json.loads(result.stdout))["API_KEY"]
    assert "envshield secrets allow" in finding["detail"]


def _approved_run(tmp_path, args=("--json",), **project):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project(**project)
        secret_manager.approve(os.getcwd(), "./fakevault --")
        result = runner.invoke(app, ["check", "--with-secrets", *args])
    _no_canary(result.stdout, result.stderr or "")
    return result


def test_withheld_variable(tmp_path, terminal):
    supplied = {k: v for k, v in SUPPLIED.items() if k != "API_KEY"}
    result = _approved_run(tmp_path, supplied=supplied)
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert _variables(payload)["API_KEY"]["reason_code"] == "missing"
    assert _variables(payload)["DATABASE_URL"]["verdict"] == "valid"
    finding = _findings(payload)["API_KEY"]
    assert finding["detail"] == verdicts.NOT_SUPPLIED
    assert "Add API_KEY to your secret manager" in finding["next_step"]["text"]
    assert "API_KEY" in payload["results"][0]["missing"]


def test_invalid_supplied_value(tmp_path, terminal):
    result = _approved_run(
        tmp_path, supplied={**SUPPLIED, "DATABASE_URL": "VAULT_CANARY_1"}
    )
    assert result.exit_code == 1
    finding = _findings(json.loads(result.stdout))["DATABASE_URL"]
    assert finding["reason_code"] == "invalid_value"
    assert finding["detail"] == "the secret manager's value must be a valid URL"


def test_blank_supplied_value(tmp_path, terminal):
    result = _approved_run(tmp_path, supplied={**SUPPLIED, "API_KEY": ""})
    assert _variables(json.loads(result.stdout))["API_KEY"]["reason_code"] == "blank"


def test_supplied_value_overrides_the_file(tmp_path, terminal):
    result = _approved_run(tmp_path, env_file="PORT=8080\nDATABASE_URL=not-a-url\n")
    assert result.exit_code == 0, result.stdout


def test_not_supplied_keeps_the_files_value(tmp_path, terminal):
    supplied = {k: v for k, v in SUPPLIED.items() if k != "API_KEY"}
    result = _approved_run(
        tmp_path, supplied=supplied, env_file="PORT=8080\nAPI_KEY=local\n"
    )
    assert result.exit_code == 0, result.stdout


def _service_error(payload):
    errors = [f for f in payload["reports"][0]["findings"] if f["kind"] == "service"]
    assert len(errors) == 1
    assert errors[0]["verdict"] == "error"
    assert errors[0]["reason_code"] == "secret_manager_error"
    return errors[0]["detail"]


def test_command_fails(tmp_path, terminal):
    result = _approved_run(tmp_path, exit_code=3)
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["verdict"] == "error"
    assert "exit 3" in _service_error(payload)
    finding = _findings(payload)["API_KEY"]
    assert finding["reason_code"] == "secret_manager"
    assert "exit 3" in finding["detail"]
    (tmp_path / "t").mkdir()
    text = _approved_run(tmp_path / "t", args=(), exit_code=3)
    assert "couldn't check the values from your secret manager" in text.stdout


def test_command_times_out(tmp_path, terminal, monkeypatch):
    monkeypatch.setattr(secret_manager, "RUN_TIMEOUT", 0.5)
    result = _approved_run(tmp_path, sleep=5)
    assert "took over 0.5 s" in _service_error(json.loads(result.stdout))


def test_command_not_installed(tmp_path, terminal):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project(config=_config(run="no-such-vault-cli run --"))
        secret_manager.approve(os.getcwd(), "no-such-vault-cli run --")
        result = runner.invoke(app, ["check", "--with-secrets", "--json"])
    assert "isn't installed" in _service_error(json.loads(result.stdout))


def test_text_output(tmp_path, terminal):
    result = _approved_run(
        tmp_path,
        args=(),
        supplied={k: v for k, v in SUPPLIED.items() if k != "API_KEY"},
    )
    assert "not supplied by your secret manager" in result.stdout
    assert "Checking your secrets with" in result.stdout


@pytest.mark.parametrize(
    "args,config",
    [
        (["--with-secrets", "--runtime"], None),
        (["--with-secrets", "--code-only"], None),
        (["--with-secrets"], _config(secrets=False)),
        (["--with-secrets"], _config(run=None)),
    ],
)
def test_usage_errors(tmp_path, args, config):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project(config=config)
        result = runner.invoke(app, ["check", *args])
        ran = os.path.exists("ran.marker")
    assert result.exit_code != 0
    assert not ran


def test_other_environment_doesnt_accept_by_default(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project(config=_config() + "environments: [staging]\n")
        result = runner.invoke(app, ["check", ".env", "--env", "staging", "--json"])
    payload = json.loads(result.stdout)
    assert result.exit_code == 1
    assert payload["gate"]["accepted"] == {}


def test_runtime_unchanged(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project()
        valid = runner.invoke(
            app, ["check", "--runtime", "--json"], env={**SUPPLIED, "PORT": "80"}
        )
        missing = runner.invoke(
            app, ["check", "--runtime", "--json"], env={"PORT": "80"}
        )
    _no_canary(valid.stdout)
    assert valid.exit_code == 0, valid.stdout
    assert missing.exit_code == 1
    assert _variables(json.loads(missing.stdout))["API_KEY"]["reason_code"] == "missing"


# --- secrets allow / revoke, doctor -----------------------------------------


def test_allow_and_revoke_never_run_it(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project()
        allowed = runner.invoke(app, ["secrets", "allow"])
        approved = secret_manager.is_approved(os.getcwd(), "./fakevault --")
        revoked = runner.invoke(app, ["secrets", "revoke"])
        still = secret_manager.is_approved(os.getcwd(), "./fakevault --")
        ran = os.path.exists("ran.marker")
    assert allowed.exit_code == 0 and approved
    assert revoked.exit_code == 0 and not still
    assert not ran


def test_allow_without_a_command(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project(config=_config(run=None))
        result = runner.invoke(app, ["secrets", "allow"])
    assert result.exit_code == 1
    assert "secrets.run" in result.stdout


def test_doctor_reports_and_never_runs_it(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project()
        before = runner.invoke(app, ["doctor"], env={"COLUMNS": "200"})
        secret_manager.approve(os.getcwd(), "./fakevault --")
        after = runner.invoke(app, ["doctor"], env={"COLUMNS": "200"})
        with open("envshield.yml", "w") as f:
            f.write(_config(run="no-such-vault-cli --"))
        missing = runner.invoke(app, ["doctor"], env={"COLUMNS": "200"})
        ran = os.path.exists("ran.marker")
    assert not ran
    assert "isn't approved yet" in before.stdout
    assert "is approved" in after.stdout
    assert "isn't installed" in missing.stdout


def test_explain_shows_no_value(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project()
        result = runner.invoke(app, ["explain", "DATABASE_URL", "--json"], env=SUPPLIED)
    _no_canary(result.stdout)


# --- setup (part 2) ---------------------------------------------------------


def _outcomes(stdout):
    """{name: (category, detail)} from setup's report lines."""
    found = {}
    for line in stdout.splitlines():
        for category in ("READY", "NEEDS ACCESS", "SET YOURSELF", "OPTIONAL"):
            if line.strip().startswith(category):
                rest = line.strip()[len(category) :].split(None, 1)
                if rest:
                    found[rest[0]] = (category, rest[1] if len(rest) > 1 else "")
    return found


def _setup(tmp_path, args=(), input=None, approve=True, env=None, **project):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project(**project)
        if approve:
            secret_manager.approve(os.getcwd(), "./fakevault --")
        result = runner.invoke(
            app, ["setup", *args], input=input, env={"COLUMNS": "250", **(env or {})}
        )
        ran = os.path.exists("ran.marker")
        written = open(".env").read() if os.path.exists(".env") else ""
    _no_canary(result.stdout, written)
    return result, ran, written


def test_setup_reports_vault_values_and_never_writes_them(tmp_path, terminal):
    result, ran, written = _setup(tmp_path, input="8080\n", env_file=None)
    assert ran
    assert result.exit_code == 0, result.stdout
    outcomes = _outcomes(result.stdout)
    for name in ("DATABASE_URL", "API_KEY", "SENTRY_DSN"):
        assert outcomes[name] == ("READY", "from your secret manager"), name
        assert name not in written
    assert outcomes["PORT"][0] == "READY"
    assert "PORT=8080" in written
    # A personal secret stays the developer's own.
    assert outcomes["PERSONAL_TOKEN"] == ("OPTIONAL", "not required locally")


def test_setup_withheld_needs_access(tmp_path, terminal):
    supplied = {k: v for k, v in SUPPLIED.items() if k != "API_KEY"}
    result, _, written = _setup(tmp_path, supplied=supplied)
    assert result.exit_code == 1
    category, detail = _outcomes(result.stdout)["API_KEY"]
    assert category == "NEEDS ACCESS"
    assert "you may lack access" in detail
    assert "Ask for access, or add them to it" in result.stdout
    assert "API_KEY" not in written


def test_setup_invalid_vault_value(tmp_path, terminal):
    result, _, _ = _setup(
        tmp_path, supplied={**SUPPLIED, "DATABASE_URL": "VAULT_CANARY_1"}
    )
    assert result.exit_code == 1
    category, detail = _outcomes(result.stdout)["DATABASE_URL"]
    assert category == "SET YOURSELF"
    assert "must be a valid URL" in detail and "fix it there" in detail


def test_setup_asks_first_and_declining_runs_nothing(tmp_path, terminal):
    result, ran, _ = _setup(tmp_path, input="n\n", approve=False)
    assert not ran
    assert "Allow EnvShield to run this command?" in result.stdout
    category, detail = _outcomes(result.stdout)["API_KEY"]
    assert category == "NEEDS ACCESS"
    assert "./fakevault -- envshield setup" in detail
    assert result.exit_code == 1


def test_setup_without_a_terminal_never_runs_it(tmp_path, monkeypatch):
    # Scenario 10: whatever the approval, no terminal means no run.
    monkeypatch.setattr(secret_manager, "stdin_is_terminal", lambda: False)
    result, ran, _ = _setup(tmp_path, args=("--non-interactive",))
    assert not ran
    category, detail = _outcomes(result.stdout)["API_KEY"]
    assert category == "NEEDS ACCESS"
    assert "no terminal" in detail


def test_setup_changed_command_asks_again(tmp_path, terminal):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project(config=_config(run="./fakevault --verbose --"))
        secret_manager.approve(os.getcwd(), "./fakevault --")
        result = runner.invoke(app, ["setup"], input="n\n")
        ran = os.path.exists("ran.marker")
    assert not ran
    assert "--verbose" in result.stdout and "Allow EnvShield" in result.stdout


def test_setup_wrapped_by_hand(tmp_path, monkeypatch):
    monkeypatch.setattr(secret_manager, "stdin_is_terminal", lambda: False)
    result, ran, written = _setup(
        tmp_path, args=("--non-interactive",), approve=False, env=SUPPLIED
    )
    assert not ran
    assert result.exit_code == 0, result.stdout
    assert _outcomes(result.stdout)["API_KEY"] == ("READY", "from your secret manager")
    assert "API_KEY" not in written


def test_setup_keeps_a_local_value_the_vault_doesnt_supply(tmp_path, terminal):
    supplied = {k: v for k, v in SUPPLIED.items() if k != "API_KEY"}
    result, _, written = _setup(
        tmp_path, supplied=supplied, env_file="PORT=8080\nAPI_KEY=mine\n"
    )
    assert result.exit_code == 0, result.stdout
    assert _outcomes(result.stdout)["API_KEY"] == ("READY", "already set locally")
    assert written == "PORT=8080\nAPI_KEY=mine\n"


def test_setup_command_fails(tmp_path, terminal):
    result, _, _ = _setup(tmp_path, exit_code=3)
    category, detail = _outcomes(result.stdout)["API_KEY"]
    assert category == "NEEDS ACCESS"
    assert "couldn't run your secrets command" in detail and "exit 3" in detail


def test_setup_mounted_file(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project(env_file=None)
        os.mkfifo(".env")
        result = runner.invoke(app, ["setup"], env={"COLUMNS": "250"})
    assert result.exit_code == 1
    assert "mounted by your secret manager" in result.stdout
    assert "replace it" not in result.stdout


# --- No .env at all (BL-215) -------------------------------------------------


def test_check_without_env_file(tmp_path, terminal):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project(env_file=None)
        plain = runner.invoke(app, ["check", "--json"])
        secret_manager.approve(os.getcwd(), "./fakevault --")
        checked = runner.invoke(app, ["check", "--with-secrets", "--json"])
    _no_canary(plain.stdout, checked.stdout)
    for result in (plain, checked):
        variables = _variables(json.loads(result.stdout))
        # The one local variable is reported against the missing file...
        assert variables["PORT"]["reason_code"] == "file_not_found"
    # ...and the secret manager's are judged from it.
    variables = _variables(json.loads(checked.stdout))
    for name in SUPPLIED:
        assert variables[name]["verdict"] == "valid", name
    assert _variables(json.loads(plain.stdout))["API_KEY"]["reason_code"] == (
        "secret_manager"
    )


def test_check_without_env_file_all_from_the_vault(tmp_path, terminal):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project(env_file=None)
        with open("env.schema.toml", "w") as f:
            f.write('[DATABASE_URL]\ntype = "url"\nsecret = true\n')
        secret_manager.approve(os.getcwd(), "./fakevault --")
        result = runner.invoke(app, ["check", "--with-secrets", "--json"])
    assert result.exit_code == 0, result.stdout
    assert json.loads(result.stdout)["verdict"] == "valid"


def test_without_secrets_a_missing_file_is_unchanged(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project(env_file=None, config=_config(secrets=False))
        with open("env.schema.toml", "w") as f:
            f.write('[DATABASE_URL]\ntype = "url"\nsecret = true\n')
        result = runner.invoke(app, ["check", "--json"])
    payload = json.loads(result.stdout)
    assert result.exit_code == 1
    assert payload["results"][0].get("error")  # the source itself is missing


# --- A {command} placeholder (BL-216, #112) ---------------------------------


class TestPlaceholder:
    def test_appended_without_one(self):
        config = secret_manager.SecretsConfig(run="bws run --")
        assert config.wrap(["python", "-m", "x"]) == [
            "bws",
            "run",
            "--",
            "python",
            "-m",
            "x",
        ]
        assert config.display("envshield check") == "bws run -- envshield check"

    def test_one_argument(self):
        config = secret_manager.SecretsConfig(run="sops exec-env s.env '{command}'")
        assert config.wrap(
            ["/usr/bin/python3", "-m", "envshield", "--service", "a b"]
        ) == [
            "sops",
            "exec-env",
            "s.env",
            "/usr/bin/python3 -m envshield --service 'a b'",
        ]
        assert (
            config.display("envshield check") == "sops exec-env s.env 'envshield check'"
        )

    def test_inside_an_argument(self):
        config = secret_manager.SecretsConfig(run="tool --exec='{command} --flag'")
        assert config.wrap(["a", "b"]) == ["tool", "--exec=a b --flag"]

    def test_a_hostile_service_name_stays_one_word(self):
        config = secret_manager.SecretsConfig(run="sh -c '{command}'")
        line = config.wrap(["probe", "--service", "x; touch pwned"])[2]
        assert line == "probe --service 'x; touch pwned'"

    @pytest.mark.parametrize(
        "run", ["sops '{command}' '{command}'", "'{command}' sops"]
    )
    def test_invalid(self, tmp_path, monkeypatch, run):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "envshield.yml").write_text(
            f"project_name: t\nsecrets:\n  run: {json.dumps(run)}\n"
        )
        with pytest.raises(ConfigParseError):
            secret_manager.get_config()


def _one_argument_fake(path, supplied):
    """Like 'sops exec-env FILE CMD': the command is one argument, run by sh."""
    lines = ["#!/bin/sh", 'touch "$(dirname "$0")/ran.marker"']
    for name, value in supplied.items():
        lines.append(f"export {name}='{value}'")
    lines.append('exec sh -c "$2"')
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    os.chmod(path, 0o755)


def test_placeholder_end_to_end(tmp_path, terminal):
    run = "./onearg secrets.env '{command}'"
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project(config=_config(run=run))
        _one_argument_fake("onearg", SUPPLIED)
        plain = runner.invoke(app, ["check", "--json"])
        secret_manager.approve(os.getcwd(), run)
        result = runner.invoke(app, ["check", "--with-secrets", "--json"])
        ran = os.path.exists("ran.marker")
    _no_canary(plain.stdout, result.stdout)
    assert ran
    assert result.exit_code == 0, result.stdout
    for name in SUPPLIED:
        assert _variables(json.loads(result.stdout))[name]["verdict"] == "valid"
    detail = _findings(json.loads(plain.stdout))["API_KEY"]["detail"]
    assert "--with-secrets" in detail
