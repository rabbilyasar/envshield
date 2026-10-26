# envshield/tests/test_consistent_defaults.py
"""V6-2 (#45): the same answer in a terminal and in CI, output that stays
readable at CI's 80 columns, an adoption PR that doesn't fail its own
contract check, and no unverifiable claims (BL-177, BL-183, BL-184,
BL-186, BL-188, BL-203)."""

import json
import os
import re
import subprocess

import pytest
from typer.testing import CliRunner

from envshield import cli
from envshield.cli import app

runner = CliRunner()

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _plain(text):
    return " ".join(_ANSI.sub("", text).replace("│", " ").split())


def _git(*args):
    subprocess.run(["git", *args], check=True, capture_output=True)


def _write(path, content):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


def _repo():
    _git("init", "-q")
    _git("config", "user.email", "t@example.com")
    _git("config", "user.name", "T")
    _git("config", "commit.gpgsign", "false")


def _commit(message="c"):
    _git("add", "-A")
    _git("commit", "-q", "-m", message)


def _nested_project():
    """BL-203's shape: a root service at '.' and a nested 'frontend'."""
    _repo()
    _write(
        "envshield.yml",
        "services:\n  root:\n    schema: env.schema.toml\n"
        "  frontend:\n    schema: frontend/env.schema.toml\n"
        "    local_file: frontend/.env\n",
    )
    _write("env.schema.toml", '[PORT]\ndescription = "p"\n')
    _write(".env", "PORT=8000\n")
    _write("frontend/env.schema.toml", '[API_URL]\ndescription = "a"\n')
    _write("frontend/.env", "API_URL=http://localhost\n")
    _write("frontend/src/api.ts", "const f = process.env.FRONTEND_ONLY;\n")
    _commit("base")


def _check_services(args, interactive, monkeypatch):
    monkeypatch.setattr(
        "envshield.core.service_manager._is_interactive", lambda: interactive
    )
    result = runner.invoke(app, args)
    return json.loads(result.stdout)


# --- 1. every service by default, in every mode ------------------------------


@pytest.mark.parametrize("interactive", [True, False])
def test_check_covers_every_service_from_the_root(tmp_path, monkeypatch, interactive):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _nested_project()

        payload = _check_services(["check", "--json"], interactive, monkeypatch)

        assert [r["service"] for r in payload["reports"]] == ["frontend", "root"]
        # BL-203: the nested service's undeclared read is reported.
        frontend = payload["reports"][0]
        assert frontend["code_references"]["undeclared"] == ["FRONTEND_ONLY"]


@pytest.mark.parametrize("interactive", [True, False])
def test_since_covers_every_service_with_and_without_a_terminal(
    tmp_path, monkeypatch, interactive
):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _nested_project()

        payload = _check_services(
            ["check", "--since", "HEAD", "--code-only", "--json"],
            interactive,
            monkeypatch,
        )

        assert [r["service"] for r in payload["reports"]] == ["frontend", "root"]


@pytest.mark.parametrize("interactive", [True, False])
def test_undeclared_covers_every_service_with_and_without_a_terminal(
    tmp_path, monkeypatch, interactive
):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _nested_project()
        _write("frontend/src/new.ts", "const n = process.env.NEW_ONE;\n")

        payload = _check_services(["undeclared", "--json"], interactive, monkeypatch)

        assert [r["service"] for r in payload["results"]] == ["frontend", "root"]


def test_the_working_directory_no_longer_narrows(tmp_path, monkeypatch):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _nested_project()
        monkeypatch.setattr(cli, "INVOCATION_DIR", os.path.abspath("frontend"))

        payload = json.loads(runner.invoke(app, ["check", "--json"]).stdout)

        assert [r["service"] for r in payload["reports"]] == ["frontend", "root"]


def test_service_still_narrows(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _nested_project()

        payload = json.loads(
            runner.invoke(app, ["check", "--service", "frontend", "--json"]).stdout
        )

        assert [r["service"] for r in payload["reports"]] == ["frontend"]


def test_no_picker_is_shown_in_a_terminal(tmp_path, mocker):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _nested_project()
        mocker.patch(
            "envshield.core.service_manager._is_interactive", return_value=True
        )
        select = mocker.patch("questionary.select")

        for args in (["check"], ["doctor"], ["schema", "diff"], ["schema", "sync"]):
            runner.invoke(app, args)

        select.assert_not_called()


def test_setup_still_asks_with_all_services_first_and_preselected(tmp_path, mocker):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _repo()
        _write(
            "envshield.yml",
            "services:\n  api:\n    schema: api/env.schema.toml\n    dir: api\n"
            "  web:\n    schema: web/env.schema.toml\n    dir: web\n",
        )
        _write("api/env.schema.toml", '[A]\ndescription = "a"\n')
        _write("web/env.schema.toml", '[W]\ndescription = "w"\n')
        mocker.patch(
            "envshield.core.service_manager._is_interactive", return_value=True
        )
        select = mocker.patch("questionary.select")
        select.return_value.ask.return_value = None  # cancel at the picker

        runner.invoke(app, ["setup"])

        select.assert_called_once()
        kwargs = select.call_args.kwargs
        assert kwargs["choices"][0] == "All services"
        assert kwargs["default"] == "All services"


# --- 2. nothing truncated at 80 columns ---------------------------------------

LONG_DIR = "frontend/src/components/dashboard/recovery/timing"
LONG_VAR = "RECOVERY_TIMING_WINDOW_SECONDS_FOR_DASHBOARD"


@pytest.fixture
def at_80_columns(monkeypatch):
    from envshield.core import scanner, schema_manager

    # The private field, not the 'width' property: undoing a property patch
    # writes back the width it read, pinning it for every later test.
    for console in (cli.console, scanner.console, schema_manager.console):
        monkeypatch.setattr(console, "_width", 80)


def _columns(output):
    """Each table column's text, read top to bottom and joined: a folded
    cell continues on the next line in the same column."""
    columns = {}
    for line in _ANSI.sub("", output).splitlines():
        if "│" not in line:
            continue
        for i, cell in enumerate(line.split("│")):
            columns[i] = columns.get(i, "") + cell.strip()
    return list(columns.values())


def _assert_whole(output, *strings):
    """Nothing truncated: no ellipsis, and each string appears whole in one
    column (wrapped across lines at most)."""
    assert "…" not in output
    columns = _columns(output)
    for s in strings:
        assert any(s in c for c in columns), (s, columns)


def test_undeclared_never_truncates_at_80_columns(tmp_path, at_80_columns):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _nested_project()
        _write(f"{LONG_DIR}/window.ts", f"const w = process.env.{LONG_VAR};\n")

        result = runner.invoke(app, ["undeclared", "--service", "frontend"])

        _assert_whole(result.stdout, f"{LONG_DIR}/window.ts", LONG_VAR)


def test_check_never_truncates_at_80_columns(tmp_path, at_80_columns):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _nested_project()
        _write(f"{LONG_DIR}/window.ts", f"const w = process.env.{LONG_VAR};\n")

        result = runner.invoke(app, ["check", "--service", "frontend"])

        _assert_whole(result.stdout, f"{LONG_DIR}/window.ts", LONG_VAR)


def test_schema_diff_never_truncates_at_80_columns(tmp_path, at_80_columns):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _nested_project()
        _write(
            "frontend/env.schema.toml",
            f'[API_URL]\ndescription = "a"\n\n[{LONG_VAR}]\ndescription = "w"\n',
        )

        result = runner.invoke(app, ["schema", "diff", "--service", "frontend"])

        _assert_whole(result.stdout, LONG_VAR)


# --- 3. introducing the contract isn't breaking ------------------------------


def _adoption_repo():
    _repo()
    _write("app.py", "print('hi')\n")
    _commit("before envshield")
    _write("envshield.yml", "services:\n  api:\n    schema: env.schema.toml\n")
    _write("env.schema.toml", '[DATABASE_URL]\ndescription = "d"\nsecret = true\n')


def test_schema_diff_reports_an_uncommitted_contract_as_introduced(tmp_path):
    """The default form: HEAD predates the contract (the adoption PR,
    before its first commit). This used to be an error."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _adoption_repo()

        result = runner.invoke(app, ["schema", "diff", "--json"])

        assert result.exit_code == 0, result.stdout
        payload = json.loads(result.stdout)
        assert payload["has_breaking_changes"] is False
        assert payload["has_blocking_changes"] is False
        [entry] = payload["results"]
        assert entry["contract_introduced"] is True
        assert [(c["variable"], c["category"]) for c in entry["changes"]] == [
            ("DATABASE_URL", "informational")
        ]
        assert entry["changes"][0]["detail"] == {"introduced": True}


def test_schema_diff_reports_a_committed_contract_as_introduced(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _adoption_repo()
        _commit("adopt envshield")

        result = runner.invoke(app, ["schema", "diff", "HEAD~1", "HEAD"])

        assert result.exit_code == 0
        assert "Contract introduced" in _plain(result.stdout)
        assert "DATABASE_URL" in result.stdout
        assert "BREAKING" not in result.stdout


def test_fail_on_breaking_doesnt_block_the_adoption_pr(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _adoption_repo()
        _commit("adopt envshield")

        result = runner.invoke(
            app,
            ["schema", "diff", "HEAD~1", "HEAD", "--fail-on", "breaking,informational"],
        )

        # Introduced changes are informational; --fail-on can still opt in.
        assert result.exit_code == 1


def test_a_contract_added_after_adoption_is_still_breaking(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _adoption_repo()
        _commit("adopt envshield")
        _write(
            "env.schema.toml",
            '[DATABASE_URL]\ndescription = "d"\nsecret = true\n\n'
            '[STRIPE_KEY]\ndescription = "s"\nsecret = true\n',
        )

        result = runner.invoke(app, ["schema", "diff", "--json"])

        assert result.exit_code == 1
        [entry] = json.loads(result.stdout)["results"]
        assert entry["contract_introduced"] is False
        assert [c["category"] for c in entry["changes"]] == ["breaking"]


# --- 4. informational references are one line --------------------------------


def test_medium_confidence_reads_are_one_summary_line(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _repo()
        _write("envshield.yml", "services:\n  api:\n    schema: env.schema.toml\n")
        _write("env.schema.toml", '[PORT]\ndescription = "p"\n')
        _write(".env", "PORT=8000\n")
        reads = "".join(f'x{i} = current_app.config["KEY_{i}"]\n' for i in range(5))
        _write("app.py", "from flask import current_app\n" + reads)

        result = runner.invoke(app, ["check"])

        out = _plain(result.stdout)
        assert "5 more names may be read from a config object" in out
        assert "KEY_0" not in out
        assert "may be read from configuration at" not in out


# --- 5. no unverifiable or wrong text (BL-177) --------------------------------


def test_init_makes_no_unverifiable_claims(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _repo()
        _write(".env", "PORT=8000\nAPI_KEY=abc\n")

        result = runner.invoke(app, ["init"], input="n\n")

        assert result.exit_code == 0, result.stdout
        assert "protected" not in result.stdout
        assert "Contract created" in result.stdout
        config = open("envshield.yml").read()
        assert "security settings" not in config
        schema = open("env.schema.toml").read()
        assert "auto-generated by 'envshield import'" not in schema
        assert "Generated by EnvShield" in schema
        # The placeholder stays in the schema as a nudge, never in the template.
        assert "TODO: Add description." in schema
        assert "TODO" not in open(".env.example").read()


def test_a_real_description_is_still_copied_into_the_template(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _write("envshield.yml", "services:\n  api:\n    schema: env.schema.toml\n")
        _write("env.schema.toml", '[PORT]\ndescription = "HTTP port"\n')

        runner.invoke(app, ["schema", "sync"])

        assert "# HTTP port" in open(".env.example").read()


def test_scan_says_what_it_checked(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _repo()
        _write("envshield.yml", "services:\n  api:\n    schema: env.schema.toml\n")
        _write("env.schema.toml", '[PORT]\ndescription = "p"\n')
        _write("app.py", "print('hi')\n")

        result = runner.invoke(app, ["scan"])

        assert result.exit_code == 0
        assert "✓ No secrets found." in result.stdout
        assert "secure and compliant" not in result.stdout


def test_check_schema_labels_name_no_location(tmp_path):
    """The legacy table says 'Missing'/'Not in Schema'; the file is named
    in its header, so a manifest row never says 'in Local'."""
    from envshield.core import schema_manager

    with runner.isolated_filesystem(temp_dir=tmp_path):
        _write("envshield.yml", "services:\n  api:\n    schema: env.schema.toml\n")
        _write("env.schema.toml", '[PORT]\ndescription = "p"\n')
        _write(
            "docker-compose.yml",
            "services:\n  api:\n    image: x\n    environment:\n      EXTRA: '1'\n",
        )
        with schema_manager.console.capture() as captured:
            schema_manager.check_schema("docker-compose.yml", service_name="api")

        out = captured.get()
        assert "in Local" not in out
        assert "Missing" in out and "Not in Schema" in out


def test_no_message_points_at_a_deprecated_command():
    """#42 deprecated 'import' and 'undeclared'; every other command's
    advice names the replacement. Source check: these strings are only
    reachable through error paths."""
    import pathlib

    import envshield

    root = pathlib.Path(envshield.__file__).parent
    offenders = []
    for path in root.rglob("*.py"):
        if "tests" in path.parts:
            continue
        for n, line in enumerate(path.read_text().splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "Run 'envshield import" in line or "running 'envshield import'" in line:
                offenders.append(f"{path.name}:{n}")
    assert offenders == []
