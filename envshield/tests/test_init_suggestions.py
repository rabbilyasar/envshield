# envshield/tests/test_init_suggestions.py
"""V2-3 (#44): init's first contract matches the project.

- BL-175: the variables the code reads are offered (accepted with --yes or
  on a terminal; listed, not added, otherwise).
- BL-180: no framework template; a read is required only when the code
  fails without it; the framework is detected by weighing languages.
- BL-176/BL-181: Compose and Kubernetes manifests are proposed with a
  containers: map; infrastructure containers stay unmapped; the ad-hoc
  check error names the registration.
- BL-185: doctor fails on a git-ignored template; init offers '!' to fix it.
"""

import json
import os
import subprocess

import pytest
import toml
import yaml
from typer.testing import CliRunner

from envshield.cli import app
from envshield.core import inspector

runner = CliRunner()


def _git(*args):
    subprocess.run(["git", *args], check=True, capture_output=True)


def _write(path, content):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


def _schema():
    return toml.load("env.schema.toml")


def _config():
    with open("envshield.yml") as f:
        return yaml.safe_load(f)


def _flat(result):
    return " ".join(result.stdout.split())


def _single_service_project():
    _git("init", "-q")
    _write(".env", "PORT=8000\n")
    _write(
        "app.py",
        "import os\n"
        'DB = os.environ["DB_PASSWORD"]\n'
        'FLAG = os.getenv("FEATURE_X")\n'
        'PORT = os.environ.get("PORT")\n',
    )
    _write("web/index.ts", "export const u = process.env.PUBLIC_URL;\n")


def _check_json():
    result = runner.invoke(app, ["check", "--json"])
    return json.loads(result.stdout)


# --- Code reads (BL-175) -----------------------------------------------------------


def test_yes_adds_every_code_read_and_check_has_no_undeclared_read(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _single_service_project()
        result = runner.invoke(app, ["init", "--yes"])
        assert result.exit_code == 0, result.stdout
        schema = _schema()
        name = os.path.basename(os.getcwd())
        # Required only when the code fails without it.
        assert schema["DB_PASSWORD"]["secret"] is True
        assert schema["DB_PASSWORD"]["services"] == [name]
        assert "required" not in schema["DB_PASSWORD"]
        assert schema["FEATURE_X"]["required"] is False
        assert schema["PUBLIC_URL"]["required"] is False
        # PORT came from .env; it isn't offered again.
        assert open("env.schema.toml").read().count("[PORT]") == 1
        report = _check_json()["reports"][0]
        assert report["code_references"]["undeclared"] == []
        assert "__from_code__" not in open("env.schema.toml").read()


def test_without_a_terminal_reads_are_listed_not_added(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _single_service_project()
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 0, result.stdout
        assert "DB_PASSWORD" in result.stdout
        assert "envshield init --force --yes" in _flat(result)
        assert "DB_PASSWORD" not in _schema()


@pytest.mark.parametrize("answer", [True, False])
def test_on_a_terminal_reads_are_offered(tmp_path, mocker, answer):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _single_service_project()
        mocker.patch("envshield.core.hooks_manager._is_interactive", return_value=True)
        confirm = mocker.patch("questionary.confirm")
        confirm.return_value.ask.return_value = answer
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 0, result.stdout
        prompts = [c.args[0] for c in confirm.call_args_list]
        assert any("variable(s) to the contract" in p for p in prompts)
        assert ("DB_PASSWORD" in _schema()) is answer


def test_init_force_yes_adds_reads_later(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _single_service_project()
        runner.invoke(app, ["init"])
        before = open("env.schema.toml", "rb").read()
        result = runner.invoke(app, ["init", "--force", "--yes"])
        assert result.exit_code == 0, result.stdout
        after = open("env.schema.toml", "rb").read()
        assert after.startswith(before)
        assert "DB_PASSWORD" in _schema()


def test_reads_are_granted_to_the_service_that_reads_them(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _git("init", "-q")
        _write("api/.env", "API_TOKEN=a\n")
        _write("web/.env", "WEB_URL=http://x\n")
        _write(
            "api/app.py", 'import os\nos.getenv("SHARED_FLAG")\nos.getenv("API_ONLY")\n'
        )
        _write("web/index.ts", "process.env.SHARED_FLAG; process.env.WEB_ONLY;\n")
        result = runner.invoke(app, ["init", "--yes"])
        assert result.exit_code == 0, result.stdout
        schema = _schema()
        assert schema["API_ONLY"]["services"] == ["api"]
        assert schema["WEB_ONLY"]["services"] == ["web"]
        assert schema["SHARED_FLAG"]["services"] == ["api", "web"]
        for report in _check_json()["reports"]:
            assert report["code_references"]["undeclared"] == [], report["service"]


# --- No invented requirements (BL-180) ----------------------------------------------


def test_every_fresh_variable_has_a_source(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _git("init", "-q")
        _write("requirements.txt", "Django\n")
        _write("settings.py", 'import os\nKEY = os.getenv("ONLY_THIS")\n')
        result = runner.invoke(app, ["init", "--yes"])
        assert result.exit_code == 0, result.stdout
        assert set(_schema()) == {"ONLY_THIS"}


@pytest.mark.parametrize(
    "py,js,expected",
    [(5, 1, "python-flask"), (1, 5, "nodejs"), (0, 0, "nodejs")],
)
def test_framework_is_detected_by_weighing_languages(tmp_path, py, js, expected):
    _write(str(tmp_path / "package.json"), "{}")
    _write(str(tmp_path / "requirements.txt"), "flask\n")
    for i in range(py):
        _write(str(tmp_path / f"app/m{i}.py"), "")
    for i in range(js):
        _write(str(tmp_path / f"web/m{i}.ts"), "")
    assert inspector.detect_project_type(str(tmp_path)) == expected


# --- Manifests (BL-176, BL-181) ------------------------------------------------------


COMPOSE = """\
services:
  api:
    build: ./api
    environment:
      API_TOKEN: ${API_TOKEN}
  frontend:
    build:
      context: ./web
    environment:
      WEB_URL: http://web
  postgres:
    image: postgres:16
"""


def test_two_app_services_are_both_validated(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _git("init", "-q")
        _write("api/.env", "API_TOKEN=a\n")
        _write("web/.env", "WEB_URL=http://x\n")
        _write("docker-compose.yml", COMPOSE)
        result = runner.invoke(app, ["init", "--yes"])
        assert result.exit_code == 0, result.stdout
        (manifest,) = _config()["manifests"]
        assert manifest == {
            "file": "docker-compose.yml",
            "containers": {"api": "api", "frontend": "web"},
        }
        assert "postgres" in _flat(result)
        sources = {
            r["service"]: [
                s["container"] for s in r["sources"] if s["kind"] == "manifest"
            ]
            for r in _check_json()["reports"]
        }
        assert sources == {"api": ["api"], "web": ["frontend"]}


def test_single_service_project_maps_its_own_build(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _git("init", "-q")
        _write(".env", "PORT=8000\n")
        _write(
            "docker-compose.yml",
            "services:\n  app:\n    build: .\n  cache:\n    image: redis\n",
        )
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 0, result.stdout
        name = os.path.basename(os.getcwd())
        assert _config()["manifests"] == [
            {"file": "docker-compose.yml", "containers": {"app": name}}
        ]


def test_declining_a_manifest_registers_nothing(tmp_path, mocker):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _git("init", "-q")
        _write(".env", "PORT=8000\n")
        _write("docker-compose.yml", "services:\n  app:\n    build: .\n")
        mocker.patch("envshield.core.hooks_manager._is_interactive", return_value=True)
        mocker.patch("questionary.confirm").return_value.ask.return_value = False
        assert runner.invoke(app, ["init"]).exit_code == 0
        assert "manifests" not in _config()


def test_a_kubernetes_manifest_is_proposed_by_container_name(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _git("init", "-q")
        _write("api/.env", "API_TOKEN=a\n")
        _write("web/.env", "WEB_URL=http://x\n")
        _write(
            "k8s/api.yaml",
            "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: api\n"
            "spec:\n  template:\n    spec:\n      containers:\n"
            "      - name: api\n        image: x\n      - name: sidecar\n"
            "        image: y\n",
        )
        result = runner.invoke(app, ["init", "--yes"])
        assert result.exit_code == 0, result.stdout
        assert _config()["manifests"] == [
            {"file": "k8s/api.yaml", "containers": {"api": "api"}}
        ]


def test_adhoc_check_error_names_the_registration(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _git("init", "-q")
        # A service named like no container, so check can't pick one.
        _write("envshield.yml", "services:\n  app:\n    schema: env.schema.toml\n")
        _write("env.schema.toml", '[A]\ndescription = "a"\n')
        _write("docker-compose.yml", COMPOSE)
        result = runner.invoke(app, ["check", "docker-compose.yml"])
        flat = _flat(result)
        assert "manifests:" in flat and "containers:" in flat


# --- A git-ignored template (BL-185) ------------------------------------------------


def _nextjs_ignore():
    _write(".gitignore", "node_modules\n.env*\n")


def test_doctor_fails_template_sync_on_an_ignored_template(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _git("init", "-q")
        _nextjs_ignore()
        _write("envshield.yml", "services:\n  app:\n    schema: env.schema.toml\n")
        _write("env.schema.toml", '[A]\ndescription = "a"\n')
        assert runner.invoke(app, ["schema", "sync"]).exit_code == 0
        result = runner.invoke(app, ["doctor", "--json"])
        checks = {
            c["name"]: c
            for s in json.loads(result.stdout)["results"]
            for c in s["checks"]
        }
        assert checks["Template Sync"]["passed"] is False
        assert ".gitignore:2 .env*" in checks["Template Sync"]["message"]
        # The pre-commit hook's check only asks whether it's in sync.
        assert runner.invoke(app, ["schema", "sync", "--check"]).exit_code == 0


def test_init_yes_unignores_the_template(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _git("init", "-q")
        _nextjs_ignore()
        _write(".env", "PORT=8000\n")
        result = runner.invoke(app, ["init", "--yes"])
        assert result.exit_code == 0, result.stdout
        assert "!.env.example" in open(".gitignore").read()
        ignored = subprocess.run(
            ["git", "check-ignore", "-q", ".env.example"], capture_output=True
        )
        assert ignored.returncode == 1  # not ignored
        assert (
            subprocess.run(
                ["git", "check-ignore", "-q", ".env"], capture_output=True
            ).returncode
            == 0
        )  # .env itself still is


def test_init_without_a_terminal_only_warns_about_the_template(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _git("init", "-q")
        _nextjs_ignore()
        _write(".env", "PORT=8000\n")
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 0, result.stdout
        assert "git-ignored" in _flat(result)
        assert "!.env.example" not in open(".gitignore").read().replace(
            "'!.env.example'", ""
        )


def test_unignore_works_when_the_project_is_a_subdirectory_of_the_repo(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _git("init", "-q")
        _write(".gitignore", ".env*\n")  # the rule is in the repository root
        os.makedirs("app")
        os.chdir("app")
        _write(".env", "PORT=8000\n")
        result = runner.invoke(app, ["init", "--yes"])
        assert result.exit_code == 0, result.stdout
        assert "!.env.example" in open(".gitignore").read()
        assert (
            subprocess.run(
                ["git", "check-ignore", "-q", ".env.example"], capture_output=True
            ).returncode
            == 1
        )


def test_a_name_one_config_declares_is_granted_to_services_whose_code_reads_it(
    tmp_path,
):
    # Twenty (#44 trial): NODE_ENV came from one service's config file and
    # was read by two more services' code; all three get it.
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _git("init", "-q")
        _write("server/.env", "NODE_ENV=production\nSERVER_TOKEN=x\n")
        _write("front/.env", "FRONT_URL=http://x\n")
        _write("front/index.ts", "process.env.NODE_ENV;\n")
        result = runner.invoke(app, ["init", "--yes"])
        assert result.exit_code == 0, result.stdout
        schema = _schema()
        assert schema["NODE_ENV"]["services"] == ["server", "front"]
        # The config file's fields win over the code read's.
        assert schema["NODE_ENV"]["defaultValue"] == "production"
        assert "required" not in schema["NODE_ENV"]
        assert open("env.schema.toml").read().count("[NODE_ENV]") == 1
        for report in _check_json()["reports"]:
            assert report["code_references"]["undeclared"] == [], report["service"]


def test_several_apps_building_from_the_root_are_not_all_mapped_to_it(tmp_path):
    # Plane (#44 trial): web, admin and space all build from '.'; mapping
    # them to the one root service checked each against the whole contract.
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _git("init", "-q")
        _write(".env", "PORT=8000\n")
        _write(
            "docker-compose.yml",
            "services:\n"
            "  web:\n    build:\n      context: .\n"
            "  admin:\n    build:\n      context: .\n"
            "  db:\n    image: postgres\n",
        )
        result = runner.invoke(app, ["init", "--yes"])
        assert result.exit_code == 0, result.stdout
        assert "manifests" not in _config()


def test_containers_building_from_one_service_directory_all_map_to_it(tmp_path):
    # An app and its worker share a build context: both are that service.
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _git("init", "-q")
        _write("api/.env", "API_TOKEN=a\n")
        _write("web/.env", "WEB_URL=http://x\n")
        _write(
            "docker-compose.yml",
            "services:\n"
            "  server:\n    build: ./api\n"
            "  worker:\n    build: ./api\n"
            "  web:\n    build: ./web\n",
        )
        result = runner.invoke(app, ["init", "--yes"])
        assert result.exit_code == 0, result.stdout
        (manifest,) = _config()["manifests"]
        assert manifest["containers"] == {
            "server": "api",
            "worker": "api",
            "web": "web",
        }
