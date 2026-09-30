# envshield/tests/test_lifecycle_acceptance.py
"""
Shared system schema, Phase 6: lifecycle acceptance.

Each test class drives one project topology through the whole EnvShield
lifecycle -- registration, sync, setup, check/doctor, scan, installed Git
hooks with real commits, topology changes, continued development -- through
the real CLI. Five models mirror the shapes of real projects (Zeus,
KemonChilo, JossJobs, IssueBear, the rabbilyasar.com portfolio); their
file layout is modelled on those projects, their contents are synthetic.
A sixth, synthetic fixture covers what none of them uses today: several
services sharing one schema (A3), sharing one physical file (A4), and
sharing a schema without sharing a file (A5).

Acceptance criteria A1-A12 are defined in progress.md (Phase 6).
"""

import json
import os
import subprocess

import pytest
from typer.testing import CliRunner

from envshield.cli import app
from envshield.config import manager as config_manager

runner = CliRunner()

HOOKS = (".git/hooks/pre-commit", ".git/hooks/post-merge")


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


def git(*args):
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True)


def cli(*args, code=0):
    result = runner.invoke(app, list(args))
    if code is not None:
        assert result.exit_code == code, (args, result.stdout, result.exception)
    return result


def cli_json(*args, code=0):
    return json.loads(cli(*args, "--json", code=code).stdout)


def commit(message):
    """A real 'git commit' through whatever hooks are installed."""
    git("add", "-A")
    result = subprocess.run(
        ["git", "commit", "-q", "-m", message], capture_output=True, text=True
    )
    result.output = flat(result.stdout + result.stderr)
    return result


def hook_bytes():
    return [read(path) for path in HOOKS]


def undeclared(scan):
    return {
        (f["file_path"], f["variable_name"]): f.get("scope")
        for f in scan["undeclared_variables"]
    }


@pytest.fixture
def repo(tmp_path, monkeypatch, mocker):
    monkeypatch.chdir(tmp_path)
    git("init", "-q")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    # setup's prompts: every value asked for is answered "v"; overwrite
    # confirmations are accepted.
    mocker.patch(
        "envshield.core.setup_manager.Prompt.ask", side_effect=lambda *a, **k: "v"
    )
    mocker.patch("questionary.confirm").return_value.ask.return_value = True
    return tmp_path


def install_hooks():
    cli("hook", "install", "--yes")
    assert all(os.path.exists(path) for path in HOOKS)
    return hook_bytes()


# -- 1. Zeus: two services, separate schemas, one shared `extends` base, ------
# -- Python local files, several containers per logical service. ------------

ZEUS_BASE = '[DATABASE_URL]\nsecret = true\n\n[REDIS_HOST]\ndefaultValue = "cache"\n'
ZEUS_COMPOSE = """services:
  athena:
    image: zeus
    environment:
      DATABASE_URL: ${DATABASE_URL}
      REDIS_HOST: cache
      ATHENA_BOOKING_URL: http://athena
  athenalambda:
    image: zeus
    environment:
      DATABASE_URL: ${DATABASE_URL}
      REDIS_HOST: cache
      ATHENA_BOOKING_URL: http://athena
  hermes:
    image: zeus
    environment:
      DATABASE_URL: ${DATABASE_URL}
      REDIS_HOST: cache
      HERMES_ADMIN_EMAIL: ops@example.com
"""


class TestZeusModel:
    def _project(self):
        write("modules/zeus-shared/base.schema.toml", ZEUS_BASE)
        write(
            "athena/env.schema.toml",
            'extends = "../modules/zeus-shared/base.schema.toml"\n\n'
            '[ATHENA_BOOKING_URL]\ndefaultValue = "http://athena"\n',
        )
        write(
            "hermes/env.schema.toml",
            'extends = "../modules/zeus-shared/base.schema.toml"\n\n'
            '[HERMES_ADMIN_EMAIL]\ndefaultValue = "ops@example.com"\n',
        )
        write(
            "athena/config/env_config.local.py",
            'DATABASE_URL = "mysql://db/athena"\nREDIS_HOST = "cache"\n'
            'ATHENA_BOOKING_URL = "http://athena"\n',
        )
        write(
            "hermes/config/env_config.local.py",
            'DATABASE_URL = "mysql://db/hermes"\nREDIS_HOST = "cache"\n'
            'HERMES_ADMIN_EMAIL = "ops@example.com"\n',
        )
        write(
            "athena/app/main.py",
            'import os\nos.environ["DATABASE_URL"]\nos.getenv("ATHENA_BOOKING_URL")\n',
        )
        write("hermes/app/main.py", 'import os\nos.getenv("HERMES_ADMIN_EMAIL")\n')
        write("docker-compose.yml", ZEUS_COMPOSE)
        write(".gitignore", "*.local.py\n")
        for name, containers in (
            ("athena", ("athena", "athenalambda")),
            ("hermes", ("hermes",)),
        ):
            for container in containers:
                cli(
                    "service", "add", name, name,
                    "--local-file", f"{name}/config/env_config.local.py",
                    "--deployment-manifest", "docker-compose.yml",
                    "--container", container,
                )  # fmt: skip

    def test_lifecycle(self, repo):
        self._project()
        services = config_manager.get_services()
        # Legacy, single-user schemas: no `dir` written, schema parent used.
        assert all("dir" not in services[s] for s in ("athena", "hermes"))
        assert config_manager.get_service_dir("athena") == "athena"
        # Several containers, one logical service.
        manifests = config_manager.get_deployment_manifests("athena")
        assert sorted(m["container"] for m in manifests) == ["athena", "athenalambda"]

        for name in ("athena", "hermes"):
            assert cli_json("check", "--service", name)["success"]
            checks = cli_json("doctor", "--service", name, code=None)["results"][0]
            failing = [c["name"] for c in checks["checks"] if not c["passed"]]
            assert failing == ["Git Hooks"], checks
        scan = cli_json("scan", ".")
        assert scan["undeclared_variables"] == []

        before = install_hooks()
        assert commit("baseline").returncode == 0

        # A6: a change to the shared base only reaches both leaf schemas'
        # users -- in the hook, and in schema diff.
        write(
            "modules/zeus-shared/base.schema.toml",
            ZEUS_BASE + "\n[SENTRY_DSN]\nsecret = true\n",
        )
        blocked = commit("add SENTRY_DSN")
        assert blocked.returncode != 0
        # One check per leaf schema's user. (The lines don't name the
        # service -- a recorded reporting follow-up, not a coverage gap.)
        assert blocked.output.count("Missing variables: SENTRY_DSN") == 2
        for name in ("athena", "hermes"):
            path = f"{name}/config/env_config.local.py"
            write(path, read(path) + 'SENTRY_DSN = "https://sentry.invalid/1"\n')
        assert commit("add SENTRY_DSN").returncode == 0, blocked.output
        diff = cli_json("schema", "diff", "HEAD~1", "HEAD", code=None)
        changed = {
            r["service"]
            for r in diff["results"]
            for c in r["changes"]
            if c["variable"] == "SENTRY_DSN"
        }
        assert changed == {"athena", "hermes"}

        # A7: a new container mapping, by hand; hooks unchanged and current.
        write(
            "envshield.yml",
            read("envshield.yml").replace(
                "    hermes: hermes\n",
                "    hermes: hermes\n    hermeslambda: hermes\n",
            ),
        )
        assert "hermeslambda" in read("envshield.yml")
        write(
            "docker-compose.yml",
            ZEUS_COMPOSE + "  hermeslambda:\n    image: zeus\n    environment:\n"
            "      DATABASE_URL: ${DATABASE_URL}\n      REDIS_HOST: cache\n"
            "      HERMES_ADMIN_EMAIL: ops@example.com\n      SENTRY_DSN: x\n",
        )
        assert commit("hermeslambda").returncode == 0
        assert hook_bytes() == before

        # A6 limitation, fail-closed: `services` in a base shared by two
        # single-user schemas can't be honoured for the other schema.
        write(
            "modules/zeus-shared/base.schema.toml",
            ZEUS_BASE + '\n[SENTRY_DSN]\nsecret = true\nservices = ["athena"]\n',
        )
        hermes = cli("check", "--service", "hermes", code=None)
        assert hermes.exit_code != 0
        assert "uses a different schema file" in flat(hermes.stdout)
        assert commit("scope in base").returncode != 0


# -- 2. KemonChilo: one logical service, two processes (web + worker). --------

KEMON_SCHEMA = (
    "[DATABASE_URL]\nsecret = true\n\n"
    '[ADMIN_USERNAME]\ndefaultValue = "admin"\n\n'
    "[TELEGRAM_BOT_TOKEN]\nsecret = true\n"
)


class TestKemonChiloModel:
    def _project(self):
        write("env.schema.toml", KEMON_SCHEMA)
        write(
            "src/app/route.ts",
            "const db = process.env.DATABASE_URL;\n"
            "const user = process.env.ADMIN_USERNAME;\n",
        )
        write("scripts/worker.ts", "const t = process.env.TELEGRAM_BOT_TOKEN;\n")
        write(".gitignore", ".env\n")
        cli("service", "add", "kemonchilo", ".")

    def test_lifecycle(self, repo):
        self._project()
        # A1/A11: the legacy shape, nothing added.
        assert config_manager.get_services() == {
            "kemonchilo": {"schema": "./env.schema.toml"}
        }
        cli("schema", "sync", "--service", "kemonchilo")
        cli("setup", "--service", "kemonchilo")
        assert cli_json("check", "--service", "kemonchilo") == {
            "success": True,
            "results": [
                {
                    "file": ".env",
                    "service": "kemonchilo",
                    "clean": True,
                    "missing": [],
                    "blank": [],
                    "invalid": {},
                    "extra": [],
                    "unresolved": [],
                }
            ],
        }
        # Both processes' reads are judged by the one service's schema.
        assert cli_json("scan", ".")["undeclared_variables"] == []

        before = install_hooks()
        assert commit("baseline").returncode == 0

        # Continued development: the worker process gains a dependency.
        write(
            "scripts/worker.ts",
            read("scripts/worker.ts") + "const i = process.env.SWEEP_INTERVAL;\n",
        )
        blocked = commit("sweep interval")
        assert blocked.returncode != 0 and "SWEEP_INTERVAL" in blocked.output
        write(
            "env.schema.toml",
            KEMON_SCHEMA + '\n[SWEEP_INTERVAL]\ndefaultValue = "60"\n',
        )
        cli("schema", "sync", "--service", "kemonchilo")
        assert commit("sweep interval").returncode == 0
        assert hook_bytes() == before

        # A12 counter-scenario: the worker process registered as a second
        # logical service in the same directory is rejected at registration...
        rejected = cli(
            "service", "add", "worker", ".", "--schema", "env.schema.toml", code=1
        )
        assert "one logical service" in flat(rejected.stdout)
        assert list(config_manager.get_services()) == ["kemonchilo"]

        # ...and a hand-written equivalent fails closed everywhere.
        good_yml = read("envshield.yml")
        write(
            "envshield.yml",
            "services:\n"
            "  kemonchilo:\n    schema: env.schema.toml\n    dir: .\n"
            "  worker:\n    schema: env.schema.toml\n    dir: .\n",
        )
        assert cli("scan", ".", code=1)
        assert cli("check", "--service", "worker", code=None).exit_code != 0
        blocked = commit("same dir")
        assert blocked.returncode != 0 and "one logical service" in blocked.output
        write("envshield.yml", good_yml)
        assert cli_json("scan", ".")["undeclared_variables"] == []


# -- 3. JossJobs: two services, separate schemas, nested directories, --------
# -- non-default file names; the second service added after hook install. ---


class TestJossJobsModel:
    def test_lifecycle(self, repo):
        write(
            "env.schema.toml",
            '[DATABASE_URL]\nsecret = true\n\n[LOG_LEVEL]\ndefaultValue = "info"\n\n'
            '[FRONTEND_ORIGIN]\ndefaultValue = "http://localhost:3000"\n',
        )
        write("src/app.py", 'import os\nos.getenv("FRONTEND_ORIGIN")\n')
        write(".gitignore", ".env\n.env.local\n")
        cli("service", "add", "jossjobs", ".")
        cli("schema", "sync", "--service", "jossjobs")
        cli("setup", "--service", "jossjobs")
        before = install_hooks()
        assert commit("backend").returncode == 0

        # A7: the frontend arrives later; the installed hooks don't change.
        write(
            "frontend/env.schema.toml",
            '[BACKEND_ORIGIN]\ndefaultValue = "http://localhost:8000"\n',
        )
        write("frontend/src/api.ts", "const b = process.env.BACKEND_ORIGIN;\n")
        cli(
            "service", "add", "frontend", "frontend",
            "--local-file", "frontend/.env.local",
            "--example-file", "frontend/.env.local.example",
        )  # fmt: skip
        assert "dir" not in config_manager.get_services()["frontend"]
        status = flat(cli("hook", "status").stdout)
        assert "frontend/env.schema.toml → frontend" in status
        # The frontend template doesn't exist yet: the commit is blocked.
        blocked = commit("frontend")
        assert blocked.returncode != 0 and "frontend" in blocked.output
        cli("schema", "sync", "--service", "frontend")
        cli("setup", "--service", "frontend")
        assert commit("frontend").returncode == 0
        assert hook_bytes() == before

        # A2: each service independently scoped; nested files route to the
        # deepest service.
        for name in ("jossjobs", "frontend"):
            assert cli_json("check", "--service", name)["success"]
        assert read("frontend/.env.local.example").count("=") == 1
        assert "BACKEND_ORIGIN" not in read(".env.example")
        assert cli_json("scan", ".")["undeclared_variables"] == []
        write(
            "frontend/src/api.ts",
            read("frontend/src/api.ts") + "const f = process.env.FRONTEND_ORIGIN;\n",
        )
        # Separate schemas: the backend's variable is undefined for the
        # frontend, not out of scope.
        found = undeclared(cli_json("scan", ".", code=1))
        assert found == {("frontend/src/api.ts", "FRONTEND_ORIGIN"): None}
        assert commit("cross read").returncode != 0

        # A10: syncing one service never touches the other's files.
        backend = read(".env.example")
        cli("schema", "sync", "--service", "frontend")
        assert read(".env.example") == backend


# -- 4. IssueBear: one service, Python local file, three containers. ---------

ISSUEBEAR_COMPOSE = """services:
  issuebear:
    image: issuebear
    environment:
      SECRET_KEY: ${SECRET_KEY}
      LOCAL_MODE: "yes"
  issuebear-task-worker:
    image: issuebear
    environment:
      SECRET_KEY: ${SECRET_KEY}
      LOCAL_MODE: "yes"
  issuebear-util:
    image: issuebear
    environment:
      SECRET_KEY: ${SECRET_KEY}
      LOCAL_MODE: "yes"
"""


class TestIssueBearModel:
    def test_lifecycle(self, repo):
        write(
            "env.schema.toml",
            '[SECRET_KEY]\nsecret = true\n\n[LOCAL_MODE]\nenum = ["yes", "no"]\n'
            'defaultValue = "yes"\n',
        )
        write(
            "config/env_config.local.py",
            'SECRET_KEY = "dev-only"\nLOCAL_MODE = "yes"\n',
        )
        write("app/main.py", 'import os\nos.getenv("LOCAL_MODE")\n')
        write("docker-compose.yml", ISSUEBEAR_COMPOSE)
        write(".gitignore", "*.local.py\n")
        for container in ("issuebear", "issuebear-task-worker", "issuebear-util"):
            cli(
                "service", "add", "issuebear", ".",
                "--local-file", "config/env_config.local.py",
                "--deployment-manifest", "docker-compose.yml",
                "--container", container,
            )  # fmt: skip
        entry = config_manager.get_services()["issuebear"]
        assert "dir" not in entry  # A1/A11: legacy shape
        manifests = config_manager.get_deployment_manifests("issuebear")
        assert sorted(m["container"] for m in manifests) == [
            "issuebear",
            "issuebear-task-worker",
            "issuebear-util",
        ]

        assert cli_json("check", "--service", "issuebear")["success"]
        cli("setup", "--service", "issuebear")  # patch-in-place, nothing asked
        assert read("config/env_config.local.py").startswith('SECRET_KEY = "dev-only"')
        before = install_hooks()
        assert commit("baseline").returncode == 0

        # One of the three containers drifts: reported for that container.
        write(
            "docker-compose.yml",
            ISSUEBEAR_COMPOSE.replace(
                '      LOCAL_MODE: "yes"\n  issuebear-util:',
                "  issuebear-util:",
            ),
        )
        doctor = cli_json("doctor", "--service", "issuebear", code=None)
        failing = [c for c in doctor["results"][0]["checks"] if not c["passed"]]
        # Detected for the drifting container. (Which container isn't named
        # in the message -- a recorded reporting follow-up.)
        assert [c["name"] for c in failing] == ["Deployment Manifest"], failing
        assert failing[0]["message"].count("Missing variables: LOCAL_MODE") == 1
        assert hook_bytes() == before


# -- 5. Portfolio (rabbilyasar.com): almost no EnvShield-managed config. ------

PORTFOLIO_YML = """# EnvShield Configuration File
project_name: rabbil-portfolio
services:
  rabbil-portfolio:
    schema: env.schema.toml
    config_source: .env.example
secret_scanning:
  exclude_files:
  - '**/tests/*'
"""


class TestPortfolioModel:
    def test_stays_outside_the_shared_model(self, repo):
        write("envshield.yml", PORTFOLIO_YML)
        write("env.schema.toml", '[NEXT_PUBLIC_API_URL]\ndefaultValue = "/api"\n')
        # Worker bindings: read from the handler's `env`, not process.env.
        write(
            "app/api/contact/route.ts",
            "export async function POST(req, env) { return env.CONTACT_EMAIL; }\n",
        )
        write("wrangler.jsonc", '{ "name": "rabbil-portfolio" }\n')

        # A11: every lifecycle step works on the legacy shape as-is.
        assert config_manager.get_service_dir("rabbil-portfolio") == "."
        cli("schema", "sync", "--service", "rabbil-portfolio")
        assert cli_json("scan", ".")["undeclared_variables"] == []
        status = flat(cli("hook", "status").stdout)
        assert "env.schema.toml → rabbil-portfolio" in status
        before = install_hooks()
        assert commit("baseline").returncode == 0
        write(
            "app/api/contact/route.ts",
            read("app/api/contact/route.ts") + "// rate limit\n",
        )
        assert commit("feature").returncode == 0
        assert hook_bytes() == before
        # Nothing in the lifecycle added dir/services/manifests.
        assert read("envshield.yml") == PORTFOLIO_YML
        assert "services" not in read("env.schema.toml")


# -- 6. Synthetic: one shared schema (A3), a shared physical file (A4), and --
# -- a schema user with its own files (A5). ---------------------------------

SHARED_SCHEMA = """[LOG_LEVEL]
defaultValue = "info"

[DATABASE_URL]
secret = true
services = ["api", "worker"]

[QUEUE_URL]
secret = true
services = ["worker"]

[SESSION_SECRET]
secret = true
services = ["web"]

[PORT]
[PORT.services.api]
defaultValue = "8000"
[PORT.services.web]
defaultValue = "3000"
"""


class TestSharedSchemaFixture:
    def _project(self):
        write("env.schema.toml", SHARED_SCHEMA)
        write(
            "api/main.py", 'import os\nos.getenv("DATABASE_URL")\nos.getenv("PORT")\n'
        )
        write("worker/main.py", 'import os\nos.getenv("QUEUE_URL")\n')
        write("web/main.py", 'import os\nos.getenv("SESSION_SECRET")\n')
        write(".gitignore", ".env\nweb/.env\n")
        for name in ("api", "worker"):
            cli(
                "service", "add", name, name, "--schema", "env.schema.toml",
                "--local-file", ".env", "--example-file", ".env.example",
            )  # fmt: skip
        cli("service", "add", "web", "web", "--schema", "env.schema.toml")

    def test_registration_sync_setup_and_scope(self, repo):
        self._project()
        # A3: every user discovered, each with its own directory.
        assert config_manager.get_schema_users("env.schema.toml") == [
            "api",
            "worker",
            "web",
        ]
        assert [
            config_manager.get_service_dir(s) for s in ("api", "worker", "web")
        ] == [
            "api",
            "worker",
            "web",
        ]

        # A4: the shared template is the union; A5: web's is its projection.
        cli("schema", "sync", "--service", "api")
        assert cli("schema", "sync", "--service", "worker", "--check").exit_code == 0
        shared = read(".env.example")
        for var in ("LOG_LEVEL", "DATABASE_URL", "QUEUE_URL", "PORT=8000"):
            assert var in shared
        assert "SESSION_SECRET" not in shared
        cli("schema", "sync", "--service", "web")
        web = read("web/.env.example")
        assert "SESSION_SECRET" in web and "PORT=3000" in web
        assert "DATABASE_URL" not in web and "QUEUE_URL" not in web

        # A4/A10: setup of one sharer keeps the other's values.
        cli("setup", "--service", "worker")
        write(".env", read(".env").replace("QUEUE_URL=v", "QUEUE_URL=amqp://w"))
        cli("setup", "--service", "api")
        assert "QUEUE_URL=amqp://w" in read(".env")
        cli("setup", "--service", "web")

        for name in ("api", "worker", "web"):
            result = cli_json("check", "--service", name)
            assert result["success"], result

        # A4: sharing the file grants nothing.
        assert "QUEUE_URL" not in config_manager.load_schema("api")
        explain = cli_json("explain", "QUEUE_URL", "--service", "api", code=1)
        assert explain["found"] is False
        assert explain["scope"] == "out_of_scope"
        assert explain["granted_to"] == ["worker"]

        # A8: out of scope and undefined stay distinct, and both fail.
        assert cli_json("scan", ".")["undeclared_variables"] == []
        write(
            "api/main.py",
            read("api/main.py") + 'os.getenv("QUEUE_URL")\nos.getenv("NOPE")\n',
        )
        assert undeclared(cli_json("scan", ".", code=1)) == {
            ("api/main.py", "QUEUE_URL"): "out_of_scope",
            ("api/main.py", "NOPE"): None,
        }

    def test_hooks_follow_topology_changes(self, repo):
        self._project()
        for name in ("api", "web"):
            cli("schema", "sync", "--service", name)
        before = install_hooks()
        assert commit("baseline").returncode == 0

        # A9: a shared-schema change is checked for every user.
        write("env.schema.toml", SHARED_SCHEMA + '\n[TRACE]\ndefaultValue = "0"\n')
        blocked = commit("trace")
        assert blocked.returncode != 0 and "TRACE" in blocked.output
        for name in ("api", "web"):
            cli("schema", "sync", "--service", name)
        assert commit("trace").returncode == 0

        # A7 (explicit): envshield.yml edited by hand after hook install --
        # a new service joins the shared schema and is granted QUEUE_URL.
        write(
            "envshield.yml",
            read("envshield.yml")
            + "  jobs:\n    schema: env.schema.toml\n    dir: jobs\n",
        )
        write(
            "env.schema.toml",
            read("env.schema.toml").replace(
                'services = ["worker"]', 'services = ["worker", "jobs"]'
            ),
        )
        write("jobs/main.py", 'import os\nos.getenv("QUEUE_URL")\n')
        status = flat(cli("hook", "status").stdout)
        assert "env.schema.toml → api, worker, web, jobs" in status
        blocked = commit("jobs")
        assert blocked.returncode != 0 and "jobs" in blocked.output
        cli("schema", "sync", "--service", "jobs")
        cli("schema", "sync", "--service", "api")  # shared template: grant changed
        ok = commit("jobs")
        assert ok.returncode == 0, ok.output
        assert hook_bytes() == before

        # A12: a hand-edit giving jobs worker's directory fails closed.
        good_yml = read("envshield.yml")
        write("envshield.yml", good_yml.replace("dir: jobs", "dir: worker"))
        blocked = commit("same dir")
        assert blocked.returncode != 0 and "one logical service" in blocked.output
        assert cli("scan", ".", code=1)
        write("envshield.yml", good_yml)

        # A9: post-merge warns for every affected user and never blocks.
        git("checkout", "-q", "-b", "feature")
        write(
            "env.schema.toml",
            read("env.schema.toml") + '\n[MERGED]\ndefaultValue = "1"\n',
        )
        git("commit", "-q", "--no-verify", "-am", "merged var")
        git("checkout", "-q", "-")
        merge = subprocess.run(
            ["git", "merge", "-q", "--no-edit", "feature"],
            capture_output=True,
            text=True,
        )
        assert merge.returncode == 0, merge.stdout + merge.stderr
        assert "MERGED" in flat(merge.stdout + merge.stderr)
        assert hook_bytes() == before

    def test_removing_a_sharer(self, repo):
        self._project()
        cli("schema", "sync", "--service", "api")
        cli("setup", "--service", "worker")

        # A10: warn and proceed; nothing another service uses is offered for
        # deletion, and the schema is never rewritten.
        result = cli("service", "remove", "worker")
        out = flat(result.stdout)
        assert "env.schema.toml still used by api, web -- kept." in out
        assert ".env still used by api -- kept." in out
        assert "Delete them by hand" not in out
        assert "Warning" in out and "'worker'" in out
        assert read("env.schema.toml") == SHARED_SCHEMA
        assert os.path.exists(".env")

        # The resulting topology is invalid until fixed by hand: fail closed.
        for name in ("api", "web"):
            assert cli("check", "--service", name, code=None).exit_code != 0

        # Fixed by hand: QUEUE_URL (worker's) is still in api's now-unshared
        # .env, and is reported as out of scope -- never silently accepted.
        write(
            "env.schema.toml",
            SHARED_SCHEMA.replace('["api", "worker"]', '["api"]').replace(
                'services = ["worker"]', 'services = ["web"]'
            ),
        )
        check = cli_json("check", "--service", "api", code=1)["results"][0]
        assert check["extra"] == ["QUEUE_URL"]
        assert check["out_of_scope"] == ["QUEUE_URL"]
