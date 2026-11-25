# envshield/tests/test_v5_acceptance.py
"""
V7-1 (#27): the v5 lifecycle acceptance suite.

The 16 acceptance scenarios of the v5 plan (§15), one test each, through
the real CLI (`CliRunner`) in real git repositories with real commits, the
conventions of test_lifecycle_acceptance.py. Each test asserts every item
in its scenario's "must observe" column on #27. Scenarios 2, 7 and 10 follow
the run-command design of #22 (docs/architecture/secret-managers.md): a
fake run command, a shell script that sets variables and execs its
arguments, stands in for the team's secret manager.

The earlier issues test each scenario's parts in isolation; this module
runs every scenario end to end, from an empty repository.
"""

import json
import os
import re
import stat
import subprocess

import pytest
import toml
import yaml
from typer.testing import CliRunner

from envshield.cli import app
from envshield.core import hooks_manager, secret_manager, setup_manager

runner = CliRunner()

# Marks put in every secret value: none may appear in any output.
CANARY = "ZZCANARY"


# -- helpers -----------------------------------------------------------------


def write(path, content=""):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


def read(path):
    with open(path) as f:
        return f.read()


def flat(text):
    return " ".join(text.split())


def git(*args, cwd=None):
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True, cwd=cwd
    )


def commit(message, cwd=None):
    git("add", "-A", cwd=cwd)
    git("commit", "-q", "-m", message, cwd=cwd)


def cli(*args, code=0, env=None, input=None):
    result = runner.invoke(
        app, list(args), env={"COLUMNS": "250", **(env or {})}, input=input
    )
    if code is not None:
        assert result.exit_code == code, (args, result.stdout, result.exception)
    assert CANARY not in result.stdout, args
    return result


def check_json(*args, code=None, env=None):
    result = cli("check", *args, "--json", code=code, env=env)
    return result.exit_code, json.loads(result.stdout)


def verdicts(payload):
    """{(service, variable): (verdict, reason_code)} over every report."""
    return {
        (r["service"], v["name"]): (v["verdict"], v["reason_code"])
        for r in payload["reports"]
        for v in r["variables"]
    }


def one_service(payload):
    """{variable: (verdict, reason_code)} for a one-service report."""
    [report] = payload["reports"]
    return {v["name"]: (v["verdict"], v["reason_code"]) for v in report["variables"]}


def outcomes(stdout):
    """setup's report lines: {variable: (category, detail)}."""
    found = {}
    for line in stdout.splitlines():
        for category in ("READY", "NEEDS ACCESS", "SET YOURSELF", "OPTIONAL"):
            if line.strip().startswith(category):
                rest = line.strip()[len(category) :].split(None, 1)
                if rest:
                    found[rest[0]] = (category, rest[1] if len(rest) > 1 else "")
    return found


def project(schema, services=None, extra=""):
    """envshield.yml with the project contract and `services` {name: dir}."""
    lines = ["project_name: t", "schema: env.schema.toml", "services:"]
    for name, directory in (services or {"app": "."}).items():
        lines += [f"  {name}:", f'    dir: "{directory}"']
    write("envshield.yml", "\n".join(lines) + "\n" + extra)
    write("env.schema.toml", schema)


def unset(*names):
    """A CliRunner env that removes `names` from the process environment."""
    return {name: None for name in names}


def fake_vault(path, supplied):
    """A fake secrets run command: marks that it ran, sets `supplied`, then
    execs its arguments (the way `op run --`, `bws run --` do)."""
    lines = [
        "#!/bin/sh",
        'touch "$(dirname "$0")/ran.marker"',
        'while [ "$1" != "--" ] && [ $# -gt 0 ]; do shift; done',
        '[ "$1" = "--" ] && shift',
    ]
    lines += [f"export {name}='{value}'" for name, value in supplied.items()]
    lines.append('exec "$@"')
    write(path, "\n".join(lines) + "\n")
    os.chmod(path, 0o755)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    path = tmp_path / "repo"
    path.mkdir()
    monkeypatch.chdir(path)
    git("init", "-q")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    git("config", "commit.gpgsign", "false")
    return path


@pytest.fixture
def prompts(mocker):
    """setup's text prompts: recorded by variable name, answered from a dict
    (unanswered: "")."""
    answers = {}
    asked = []

    def ask(message, password=False, **kwargs):
        name = message.split("[bold cyan]")[1].split("[/bold cyan]")[0]
        asked.append(name)
        return answers.get(name, "")

    mocker.patch("envshield.core.setup_manager.Prompt.ask", side_effect=ask)
    return answers, asked


@pytest.fixture
def terminal(monkeypatch):
    """A developer at a terminal: setup prompts, and a secrets command may
    be approved and run."""
    monkeypatch.setattr(setup_manager, "stdin_is_terminal", lambda: True)
    monkeypatch.setattr(secret_manager, "stdin_is_terminal", lambda: True)


@pytest.fixture
def no_terminal(monkeypatch):
    """CI: nothing can prompt, and a secrets command never runs."""
    monkeypatch.setattr(setup_manager, "stdin_is_terminal", lambda: False)
    monkeypatch.setattr(secret_manager, "stdin_is_terminal", lambda: False)
    monkeypatch.setattr(hooks_manager, "_is_interactive", lambda: False)


# -- 1. New project -----------------------------------------------------------


def test_scenario_1_new_project(repo, prompts, terminal):
    """init with api/ and web/ -> one contract; both services with dir;
    secrets scoped; setup then check VALID."""
    write("api/.env.example", "API_TOKEN=\nAPI_PORT=8000\n")
    write("web/.env.example", "WEB_TOKEN=\nWEB_URL=http://localhost:3000\n")
    write("api/app.py", 'import os\nos.environ["API_TOKEN"]\n')
    write("web/index.ts", "process.env.WEB_TOKEN\n")
    write(".gitignore", ".env\n")
    commit("app")

    cli("init", "--yes")
    config = yaml.safe_load(read("envshield.yml"))
    assert config["schema"] == "env.schema.toml"
    assert {n: s["dir"] for n, s in config["services"].items()} == {
        "api": "api",
        "web": "web",
    }
    contract = toml.load("env.schema.toml")
    assert [p for p in os.listdir("api") if p.endswith(".toml")] == []
    assert contract["API_TOKEN"]["secret"] is True
    assert contract["API_TOKEN"]["services"] == ["api"]
    assert contract["WEB_TOKEN"]["secret"] is True
    assert contract["WEB_TOKEN"]["services"] == ["web"]
    commit("adopt envshield")

    answers, _ = prompts
    answers.update({"API_TOKEN": f"tok-{CANARY}", "WEB_TOKEN": f"tok-{CANARY}"})
    for service in ("api", "web"):
        cli("setup", "--service", service)
    code, payload = check_json()
    assert code == 0, payload
    assert payload["verdict"] == "valid"
    assert {r["service"] for r in payload["reports"]} == {"api", "web"}
    assert set(verdicts(payload).values()) == {("valid", None)}


# -- 2 and 7. A new developer with the team's secret manager -------------------

VAULT_SCHEMA = (
    '[DATABASE_URL]\ntype = "url"\nsecret = true\n\n'
    "[API_KEY]\nsecret = true\n\n"
    '[PORT]\ntype = "port"\n'
)
VAULT_VALUES = {
    "DATABASE_URL": f"postgres://app:{CANARY}@db/app",
    "API_KEY": f"key-{CANARY}",
}
SECRETS = 'secrets:\n  run: "./fakevault --"\n'


def _vault_project(supplied):
    project(VAULT_SCHEMA, extra=SECRETS)
    write(".gitignore", ".env\nran.marker\n")
    fake_vault("fakevault", supplied)
    commit("project with a secret manager")
    cli("secrets", "allow")


def test_scenario_2_new_developer_with_the_vault(repo, prompts, terminal):
    """The run command supplies every vault variable -> setup reports them
    READY; .env 0600 holds no vault value; check --with-secrets VALID; no
    value in any output."""
    _vault_project(VAULT_VALUES)
    answers, asked = prompts
    answers["PORT"] = "8080"

    result = cli("setup")
    assert os.path.exists("ran.marker")
    found = outcomes(result.stdout)
    for name in VAULT_VALUES:
        assert found[name] == ("READY", "from your secret manager"), name
    assert found["PORT"][0] == "READY"
    assert asked == ["PORT"]  # never asked for a vault value

    assert stat.S_IMODE(os.stat(".env").st_mode) == 0o600
    content = read(".env")
    assert "PORT=8080" in content
    assert CANARY not in content
    for name in VAULT_VALUES:
        assert name not in content

    code, payload = check_json("--with-secrets")
    assert code == 0, payload
    assert payload["verdict"] == "valid"
    assert one_service(payload) == {
        "DATABASE_URL": ("valid", None),
        "API_KEY": ("valid", None),
        "PORT": ("valid", None),
    }
    # Every output so far was checked for the values by cli(); the human
    # form too.
    cli("check", "--with-secrets")
    cli("doctor", code=None)
    cli("explain", "DATABASE_URL")


def test_scenario_7_the_vault_withholds_one_variable(repo, prompts, terminal):
    """The run command doesn't supply API_KEY -> NEEDS ACCESS for it, the
    others READY, exit 1."""
    _vault_project({k: v for k, v in VAULT_VALUES.items() if k != "API_KEY"})
    answers, _ = prompts
    answers["PORT"] = "8080"

    result = cli("setup", code=1)
    found = outcomes(result.stdout)
    category, detail = found["API_KEY"]
    assert category == "NEEDS ACCESS"
    assert "you may lack access" in detail
    assert found["DATABASE_URL"] == ("READY", "from your secret manager")
    assert found["PORT"][0] == "READY"
    assert "API_KEY" not in read(".env")


# -- 3, 4, 11, 15. The deploy-time check ---------------------------------------

RUNTIME_SCHEMA = (
    '[RT_PORT]\ntype = "port"\ndefaultValue = "8000"\n\n'
    '[RT_STRIPE_KEY]\nsecret = true\nrequiredIn = ["production"]\n'
)
ENVIRONMENTS = "environments: [staging, production]\n"
RT = unset("RT_PORT", "RT_STRIPE_KEY")


def _runtime_project():
    project(RUNTIME_SCHEMA, extra=ENVIRONMENTS)
    commit("runtime contract")
    assert not os.path.exists(".env")


def test_scenario_3_production_runtime_all_set(repo):
    """check --runtime --env production: VALID, exit 0, no .env."""
    _runtime_project()
    env = {**RT, "RT_PORT": "8000", "RT_STRIPE_KEY": f"sk_live_{CANARY}"}
    code, payload = check_json("--runtime", "--env", "production", env=env)
    assert code == 0, payload
    assert payload["verdict"] == "valid"
    assert payload["environment"] == "production"
    assert set(one_service(payload).values()) == {("valid", None)}
    assert not os.path.exists(".env")


def test_scenario_4_production_runtime_one_missing(repo):
    """One required variable missing -> INVALID naming it, exit 1."""
    _runtime_project()
    env = {**RT, "RT_PORT": "8000"}
    code, payload = check_json("--runtime", "--env", "production", env=env)
    assert code == 1
    assert payload["verdict"] == "invalid"
    assert one_service(payload)["RT_STRIPE_KEY"] == ("invalid", "missing")
    human = cli("check", "--runtime", "--env", "production", code=1, env=env)
    assert "RT_STRIPE_KEY" in human.stdout


def test_scenario_11_defaulted_variable_absent_at_runtime(repo):
    """A defaulted variable absent in the production runtime -> INVALID
    (missing); with requiredIn omitting production -> NOT REQUIRED."""
    _runtime_project()
    env = {**RT, "RT_STRIPE_KEY": f"sk_live_{CANARY}"}
    code, payload = check_json("--runtime", "--env", "production", env=env)
    assert one_service(payload)["RT_PORT"] == ("invalid", "missing")
    assert code == 1

    write(
        "env.schema.toml",
        RUNTIME_SCHEMA.replace(
            'defaultValue = "8000"\n',
            'defaultValue = "8000"\nrequiredIn = ["staging"]\n',
        ),
    )
    commit("RT_PORT: the app supplies its default in production")
    code, payload = check_json("--runtime", "--env", "production", env=env)
    assert one_service(payload)["RT_PORT"] == ("not_required", None)
    assert code == 0, payload


@pytest.mark.parametrize(
    "accept",
    ["--accept-unverified=external_reference", "--accept-unverified=host_forward"],
)
def test_scenario_15_accept_unverified_with_runtime(repo, accept):
    """--accept-unverified with --runtime: a usage error, exit 2."""
    _runtime_project()
    result = cli("check", "--runtime", "--env", "production", accept, code=2, env=RT)
    assert "--accept-unverified" in flat(re.sub(r"\x1b\[[0-9;]*m", "", result.output))


# -- 5. A Kubernetes manifest with an external Secret --------------------------

POD = (
    "apiVersion: v1\nkind: Pod\nmetadata: {name: p}\nspec:\n  containers:\n"
    "  - name: app\n    image: x\n    env:\n"
    "    - name: DB\n      valueFrom: {secretKeyRef: {name: ext, key: db}}\n"
)


def test_scenario_5_kubernetes_external_secret(repo):
    """COULD NOT VERIFY, exit 1, never VALID; accepted: exit 0, verdict still
    unverified, PASSED WITH UNVERIFIED VALUES, success false."""
    project('[DB]\ntype = "url"\n')
    write("k8s/pod.yaml", POD)
    commit("deploy")

    code, payload = check_json("k8s/pod.yaml")
    assert code == 1
    assert (payload["verdict"], payload["success"]) == ("unverified", False)
    assert one_service(payload)["DB"] == ("unverified", "external_reference")
    human = cli("check", "k8s/pod.yaml", code=1).stdout
    assert "COULD NOT VERIFY" in human and "VALID:" not in human

    accept = "--accept-unverified=external_reference"
    code, payload = check_json("k8s/pod.yaml", accept)
    assert code == 0
    assert payload["verdict"] == "unverified"
    assert payload["success"] is False
    assert payload["gate"] == {
        "result": "pass",
        "accepted": {"external_reference": ["DB"]},
    }
    human = flat(cli("check", "k8s/pod.yaml", accept).stdout)
    assert "PASSED WITH UNVERIFIED VALUES" in human and "VALID:" not in human


# -- 6. The contract adds a production-only requirement ------------------------


def test_scenario_6_contract_adds_required_in_production(repo):
    """schema diff names production; local is unaffected."""
    project("[APP_NAME]\n", extra=ENVIRONMENTS)
    write(".env", "APP_NAME=shop\n")
    write(".gitignore", ".env\n")
    commit("contract")
    local_before = check_json()

    with open("env.schema.toml", "a") as f:
        f.write('\n[SENTRY_DSN]\ntype = "url"\nrequiredIn = ["production"]\n')
    commit("SENTRY_DSN is required in production")

    result = cli("schema", "diff", "HEAD~1", "HEAD", "--json", code=None)
    [report] = json.loads(result.stdout)["results"]
    [change] = report["changes"]
    assert change["variable"] == "SENTRY_DSN"
    assert change["detail"]["environments"] == {
        "local": "non_breaking",
        "staging": "non_breaking",
        "production": "breaking",
    }
    assert "production" in cli("schema", "diff", "HEAD~1", "HEAD", code=None).stdout

    code, payload = check_json()
    assert code == local_before[0] == 0
    assert one_service(payload)["SENTRY_DSN"] == ("not_required", None)


# -- 8. A pull adds a required variable ----------------------------------------


def test_scenario_8_a_pull_adds_a_required_variable(repo, tmp_path, prompts, terminal):
    """The post-merge hook names it; setup asks only for it."""
    project('[EXISTING]\ndescription = "e"\n')
    write(".gitignore", ".env\n")
    commit("contract")

    clone = tmp_path / "clone"
    git("clone", "-q", str(repo), str(clone))
    for key, value in (
        ("user.email", "dev@example.com"),
        ("user.name", "Dev"),
        ("commit.gpgsign", "false"),
    ):
        git("config", key, value, cwd=clone)
    os.chdir(clone)
    cli("hook", "install", "--yes")
    answers, asked = prompts
    answers["EXISTING"] = "1"
    cli("setup")
    assert asked == ["EXISTING"]

    os.chdir(repo)
    with open("env.schema.toml", "a") as f:
        f.write('\n[NEW_REQUIRED]\ndescription = "n"\n')
    commit("NEW_REQUIRED")

    os.chdir(clone)
    pull = subprocess.run(
        ["git", "pull", "-q", "--no-rebase"], capture_output=True, text=True
    )
    assert pull.returncode == 0, pull.stderr
    assert "NEW_REQUIRED" in pull.stdout + pull.stderr

    asked.clear()
    answers["NEW_REQUIRED"] = "n"
    cli("setup")
    assert asked == ["NEW_REQUIRED"]
    # setup created .env (its header first); the pull only appended.
    assert read(".env").endswith("\nEXISTING=1\n\nNEW_REQUIRED=n\n")
    assert check_json()[0] == 0


# -- 9. A legacy project -------------------------------------------------------


def test_scenario_9_legacy_three_schemas_with_extends(repo):
    """Every command keeps its v4 behavior; schema merge is equivalent."""
    write(
        "shared/base.schema.toml",
        '[LOG_LEVEL]\ndescription = "Log level"\ndefaultValue = "info"\n',
    )
    bodies = {
        "api": '[API_KEY]\nsecret = true\n[PORT]\ntype = "port"\n',
        "web": '[PORT]\ntype = "port"\n[PUBLIC_URL]\ntype = "url"\n',
        "worker": '[QUEUE_URL]\ntype = "url"\nsecret = true\n',
    }
    lines = ["project_name: t", "services:"]
    for name, body in bodies.items():
        write(
            f"{name}/env.schema.toml", f'extends = "../shared/base.schema.toml"\n{body}'
        )
        lines += [f"  {name}:", f"    schema: {name}/env.schema.toml"]
    write("envshield.yml", "\n".join(lines) + "\n")
    write("api/.env", f"API_KEY=k-{CANARY}\nPORT=80\nLOG_LEVEL=info\n")
    write("web/.env", "PORT=not-a-port\nPUBLIC_URL=https://x.example\n")
    write("worker/.env", "QUEUE_URL=amqp://q\n")
    write(".gitignore", ".env\n")
    commit("legacy layout")
    schema_bytes = {
        p: read(p)
        for p in [f"{n}/env.schema.toml" for n in bodies] + ["shared/base.schema.toml"]
    }

    # v4 behavior: extends resolved per service, each judged on its own file.
    code, payload = check_json()
    before = verdicts(payload)
    assert code == 1
    assert before == {
        ("api", "API_KEY"): ("valid", None),
        ("api", "PORT"): ("valid", None),
        ("api", "LOG_LEVEL"): ("valid", None),
        ("web", "PORT"): ("invalid", "invalid_value"),
        ("web", "PUBLIC_URL"): ("valid", None),
        ("web", "LOG_LEVEL"): ("invalid", "missing"),
        ("worker", "QUEUE_URL"): ("valid", None),
        ("worker", "LOG_LEVEL"): ("invalid", "missing"),
    }
    doctor = cli("doctor", code=None)
    assert "schema merge" in flat(doctor.stdout)  # informational only
    cli("explain", "LOG_LEVEL", "--service", "worker")
    cli("schema", "diff", "HEAD", code=None)
    assert cli("scan", code=None).exit_code in (0, 1)

    result = cli("schema", "merge", "--yes")
    assert "not carried over" in flat(result.stdout)
    contract = toml.load("env.schema.toml")
    assert "extends" not in contract
    assert contract["API_KEY"]["services"] == ["api"]
    assert contract["QUEUE_URL"]["services"] == ["worker"]
    assert "services" not in contract["LOG_LEVEL"]
    assert {p: read(p) for p in schema_bytes} == schema_bytes
    assert verdicts(check_json()[1]) == before
    assert "schema merge" not in flat(cli("doctor", code=None).stdout)


# -- 10. A commit changes the secrets command ----------------------------------


def test_scenario_10_a_commit_changes_secrets_run(repo, prompts, monkeypatch):
    """setup shows the new command and asks again before running it;
    without a terminal it never runs it."""
    monkeypatch.setattr(setup_manager, "stdin_is_terminal", lambda: True)
    monkeypatch.setattr(secret_manager, "stdin_is_terminal", lambda: True)
    _vault_project(VAULT_VALUES)
    prompts[0]["PORT"] = "8080"
    cli("setup")
    assert os.path.exists("ran.marker")  # approved: it runs
    os.remove("ran.marker")

    write(
        "envshield.yml",
        read("envshield.yml").replace(
            '"./fakevault --"', '"./fakevault --account attacker --"'
        ),
    )
    commit("change the secrets command")

    result = cli("setup", code=1, input="n\n")
    out = flat(result.stdout)
    assert "./fakevault --account attacker --" in out
    assert "Allow EnvShield to run this command?" in out
    assert not os.path.exists("ran.marker")
    assert outcomes(result.stdout)["API_KEY"][0] == "NEEDS ACCESS"

    # No terminal: never run, even once the new command is approved.
    monkeypatch.setattr(setup_manager, "stdin_is_terminal", lambda: False)
    monkeypatch.setattr(secret_manager, "stdin_is_terminal", lambda: False)
    cli("secrets", "allow")
    result = cli("setup", "--non-interactive", code=1)
    assert not os.path.exists("ran.marker")
    category, detail = outcomes(result.stdout)["API_KEY"]
    assert category == "NEEDS ACCESS" and "no terminal" in detail


# -- 12. Optional locally, required in production ------------------------------


def test_scenario_12_optional_locally_required_in_production(repo, prompts, terminal):
    """Not prompted by setup; absent locally -> NOT REQUIRED; absent in
    production -> INVALID."""
    project(
        '[APP_NAME]\n\n[STRIPE_KEY]\nsecret = true\nrequiredIn = ["production"]\n',
        extra=ENVIRONMENTS,
    )
    write(".gitignore", ".env\n")
    commit("contract")

    answers, asked = prompts
    answers["APP_NAME"] = "shop"
    cli("setup")
    assert "STRIPE_KEY" not in asked
    assert "STRIPE_KEY" not in read(".env")

    code, payload = check_json()
    assert one_service(payload)["STRIPE_KEY"] == ("not_required", None)
    assert code == 0

    env = {**unset("STRIPE_KEY"), "APP_NAME": "shop"}
    code, payload = check_json("--runtime", "--env", "production", env=env)
    assert one_service(payload)["STRIPE_KEY"] == ("invalid", "missing")
    assert code == 1


# -- 13. CI with no terminal ---------------------------------------------------


def test_scenario_13_ci_without_a_terminal(repo, no_terminal, mocker):
    """No hang; setup exits 1 with a SET YOURSELF list; check's exit codes
    as specified."""
    ask = mocker.patch("envshield.core.setup_manager.Prompt.ask")
    confirm = mocker.patch("questionary.confirm")
    project(
        '[PORT]\ntype = "port"\ndefaultValue = "8000"\n\n'
        "[GITHUB_TOKEN]\nsecret = true\n\n"
        "[OPTIONAL_FLAG]\nrequired = false\n"
    )
    write(".gitignore", ".env\n")
    commit("contract")

    result = cli("setup", code=1)
    out = flat(result.stdout)
    assert "SET YOURSELF GITHUB_TOKEN" in out
    assert "1 required variable(s) still need a value" in out
    assert "PORT=8000" in read(".env")  # what it could resolve is written
    assert not ask.called and not confirm.called

    assert check_json()[0] == 1  # a required variable is missing
    assert cli("check", code=1).exit_code == 1
    with open(".env", "a") as f:
        f.write(f"GITHUB_TOKEN=ghp_{CANARY}\n")
    assert check_json()[0] == 0
    cli("check")
    cli("check", "--since", "HEAD", "--code-only")
    assert not ask.called and not confirm.called


# -- 14. A requiredIf trigger that Compose forwards ----------------------------


def test_scenario_14_required_if_trigger_is_a_compose_forward(repo):
    """The dependent variable is COULD NOT VERIFY (ambiguous_requirement),
    which can't be accepted; exit 1."""
    project(
        "[PAYMENTS]\nrequired = false\n\n"
        '[STRIPE_KEY]\nrequiredIf = { var = "PAYMENTS", equals = "true" }\n'
    )
    write(
        "docker-compose.yml",
        "services:\n  app:\n    image: x\n    environment:\n"
        "      PAYMENTS: ${PAYMENTS}\n",
    )
    commit("compose")

    code, payload = check_json("docker-compose.yml")
    assert one_service(payload)["STRIPE_KEY"] == ("unverified", "ambiguous_requirement")
    assert code == 1
    # Accepting everything else still fails on it, and it can't be named.
    code, payload = check_json("docker-compose.yml", "--accept-unverified=host_forward")
    assert one_service(payload)["STRIPE_KEY"] == ("unverified", "ambiguous_requirement")
    assert code == 1
    cli(
        "check",
        "docker-compose.yml",
        "--accept-unverified=ambiguous_requirement",
        code=2,
    )


# -- 16. Absent, empty and value: the PORT table -------------------------------

# Schema variants and, per state, the expected (verdict, reason_code).
PORT_SCHEMAS = {
    "required": '[PORT]\ntype = "port"\n',
    "defaulted": '[PORT]\ntype = "port"\ndefaultValue = "8000"\n',
    "optional": '[PORT]\ntype = "port"\nrequired = false\n',
    "optional-string": "[PORT]\nrequired = false\n",
    "optional-pattern": '[PORT]\nrequired = false\npattern = "^[0-9]+$"\n',
    "optional-pattern-empty-ok": '[PORT]\nrequired = false\npattern = "^[0-9]*$"\n',
}
REQUIRED_ROW = {
    "absent": ("invalid", "missing"),
    "empty": ("invalid", "blank"),
    "abc": ("invalid", "invalid_value"),
    "8000": ("valid", None),
}
PORT_TABLE = {
    "required": REQUIRED_ROW,
    "defaulted": REQUIRED_ROW,  # a default is never evidence
    "optional": {
        "absent": ("not_required", None),
        "empty": ("invalid", "invalid_value"),
        "abc": ("invalid", "invalid_value"),
        "8000": ("valid", None),
    },
    "optional-string": {
        "absent": ("not_required", None),
        "empty": ("valid", None),
        "abc": ("valid", None),
        "8000": ("valid", None),
    },
    "optional-pattern": {
        "absent": ("not_required", None),
        "empty": ("invalid", "invalid_value"),
        "abc": ("invalid", "invalid_value"),
        "8000": ("valid", None),
    },
    "optional-pattern-empty-ok": {
        "absent": ("not_required", None),
        "empty": ("valid", None),
        "abc": ("invalid", "invalid_value"),
        "8000": ("valid", None),
    },
}
# How each source writes each state ("absent" also as a bare `PORT` line,
# which dotenv loaders ignore, and empty also as `PORT=""`).
DOTENV = {"absent": ["", "PORT\n"], "empty": ["PORT=\n", 'PORT=""\n']}
PYTHON = {"absent": [""], "empty": ['PORT = ""\n']}


def _port(source, state, form):
    """Writes the local file / builds the env; returns (args, env)."""
    if source == "runtime":
        env = unset("PORT")
        if state != "absent":
            env["PORT"] = "" if state == "empty" else state
        return ["--runtime"], env
    if source == "dotenv":
        text = DOTENV[state][form] if state in DOTENV else f"PORT={state}\n"
        write(".env", text)
    else:
        text = PYTHON[state][form] if state in PYTHON else f'PORT = "{state}"\n'
        write("settings.py", text)
    return [], unset("PORT")


def test_scenario_16_the_port_table(repo):
    """Every cell of the absent / empty / value table matches the approved
    rules (v5-decisions.md, Q3), for .env, Python config and the process
    environment."""
    cells = 0
    for source in ("dotenv", "python", "runtime"):
        extra = "    local_file: settings.py\n" if source == "python" else ""
        for variant, row in PORT_TABLE.items():
            project(PORT_SCHEMAS[variant])
            if extra:
                write("envshield.yml", read("envshield.yml") + extra)
            for state, expected in row.items():
                forms = {"dotenv": DOTENV, "python": PYTHON}.get(source, {})
                for form in range(len(forms.get(state, [None]))):
                    args, env = _port(source, state, form)
                    code, payload = check_json(*args, env=env)
                    where = (source, variant, state, form)
                    assert one_service(payload)["PORT"] == expected, where
                    assert code == (
                        0 if expected[0] in ("valid", "not_required") else 1
                    ), where
                    assert "abc" not in json.dumps(payload["reports"][0]["findings"]), (
                        where
                    )
                    cells += 1
    # 6 variants x 4 states x 3 sources, plus .env's second absent and empty forms.
    assert cells == 6 * 4 * 3 + 6 * 2

    # With --process-env, an empty process value overrides the file (D-3).
    project(PORT_SCHEMAS["required"])
    write(".env", "PORT=8000\n")
    code, payload = check_json("--process-env", env={"PORT": ""})
    assert one_service(payload)["PORT"] == ("invalid", "blank")
    assert code == 1
