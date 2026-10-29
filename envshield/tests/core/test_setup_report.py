# envshield/tests/core/test_setup_report.py
"""V3-2 (#21): setup reports every variable's outcome, never prompts
without a terminal, and exits 1 while a required variable isn't READY.

Categories: READY (already set, default, from the template, entered),
SET YOURSELF (no shared source, skipped, set but empty, invalid),
OPTIONAL (not required locally). NEEDS ACCESS waits for providers (#22).
"""

import os
import subprocess
import sys

import pytest
from typer.testing import CliRunner

from envshield.cli import app
from envshield.core import setup_manager

runner = CliRunner()
SENTINEL = "zq9-report-sentinel-4417"

SCHEMA = """\
[ALREADY]
description = "a"

[DEFAULTED]
description = "d"
defaultValue = "8000"

[FROM_TEMPLATE]
description = "t"

[ENTERED]
description = "e"

[GITHUB_TOKEN]
description = "Create a personal token at github.com/settings/tokens"
secret = true

[EMPTY_ONE]
description = "x"

[BAD_PORT]
description = "p"
type = "port"

[SENTRY_DSN]
description = "s"
required = false
"""


def _write(path, content):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


def _project():
    _write("envshield.yml", "services:\n  app:\n    schema: env.schema.toml\n")
    _write("env.schema.toml", SCHEMA)
    _write(".env", f"ALREADY={SENTINEL}\nEMPTY_ONE=\nBAD_PORT=nope\n")
    _write(".env.example", "FROM_TEMPLATE=hello\nGITHUB_TOKEN=\n")


def _flat(result):
    return " ".join((result.stdout + (result.stderr or "")).split())


def _no_terminal(monkeypatch):
    monkeypatch.setattr(setup_manager, "stdin_is_terminal", lambda: False)


@pytest.fixture
def never_prompt(mocker):
    def boom(*a, **k):
        raise AssertionError("prompted without a terminal")

    mocker.patch("envshield.core.setup_manager.Prompt.ask", side_effect=boom)
    mocker.patch("questionary.select", side_effect=boom)
    mocker.patch("questionary.confirm", side_effect=boom)


# --- Every category, and the counts ----------------------------------------------


def test_every_category_and_the_summary_counts(tmp_path, mocker):
    answers = {"ENTERED": "typed"}

    def ask(message, **kwargs):
        name = message.split("[bold cyan]")[1].split("[/bold cyan]")[0]
        return answers.get(name, "")  # everything else skipped

    mocker.patch("envshield.core.setup_manager.Prompt.ask", side_effect=ask)
    mocker.patch("questionary.confirm").return_value.ask.return_value = False
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project()
        result = runner.invoke(app, ["setup"])
        flat = _flat(result)
        for expected in (
            "READY ALREADY already set",
            "READY DEFAULTED default",
            "READY FROM_TEMPLATE from .env.example",
            "READY ENTERED entered",
            "SET YOURSELF GITHUB_TOKEN skipped; Create a personal token at github.com/settings/tokens",
            "SET YOURSELF EMPTY_ONE set but empty",
            "SET YOURSELF BAD_PORT invalid (must be a port number from 1-65535)",
            "OPTIONAL SENTRY_DSN not required locally",
        ):
            assert expected in flat, expected
        # 7 required, 4 READY.
        assert "4 of 7 required variables ready" in flat
        assert ".env updated (3 added, 0 changed)" in flat
        assert result.exit_code == 1
        assert SENTINEL not in flat


def test_all_ready_exits_0(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _write("envshield.yml", "services:\n  app:\n    schema: env.schema.toml\n")
        _write("env.schema.toml", '[PORT]\ndescription = "p"\ndefaultValue = "8000"\n')
        result = runner.invoke(app, ["setup"])
        assert result.exit_code == 0, result.stdout
        assert "1 of 1 required variables ready" in result.stdout
        assert ".env created (1 added)" in result.stdout
        assert "Configuration complete" in result.stdout


# --- Without a terminal ----------------------------------------------------------


def test_scenario_13_ci_without_a_terminal(tmp_path, monkeypatch, never_prompt):
    """No hang: setup writes what it can, lists the rest, exits 1; check's
    exit codes don't change."""
    _no_terminal(monkeypatch)
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project()
        check_before = runner.invoke(app, ["check"]).exit_code
        result = runner.invoke(app, ["setup"])
        flat = _flat(result)
        assert result.exit_code == 1, flat
        for name in ("ENTERED", "GITHUB_TOKEN", "EMPTY_ONE", "BAD_PORT"):
            assert f"SET YOURSELF {name}" in flat
        assert "SET YOURSELF ENTERED no shared source" in flat
        assert "4 required variable(s) still need a value" in flat
        # Partial: what could be resolved was written; lines kept as they were.
        content = open(".env").read()
        assert "DEFAULTED=8000" in content and "FROM_TEMPLATE=hello" in content
        assert "EMPTY_ONE=\n" in content and "BAD_PORT=nope\n" in content
        assert SENTINEL not in flat
        check_after = runner.invoke(app, ["check"]).exit_code
        assert check_before == check_after == 1


def test_non_interactive_flag_never_prompts_even_on_a_terminal(tmp_path, never_prompt):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project()
        result = runner.invoke(app, ["setup", "--non-interactive"])
        assert result.exit_code == 1
        assert "SET YOURSELF GITHUB_TOKEN no shared source" in _flat(result)


def test_without_a_terminal_an_invalid_optional_empty_is_reported_and_kept(
    tmp_path, monkeypatch, never_prompt
):
    _no_terminal(monkeypatch)
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _write("envshield.yml", "services:\n  app:\n    schema: env.schema.toml\n")
        _write(
            "env.schema.toml",
            '[PORT]\ndescription = "p"\ntype = "port"\nrequired = false\n',
        )
        _write(".env", "PORT=\n")
        result = runner.invoke(app, ["setup"])
        assert result.exit_code == 0  # nothing required
        assert "OPTIONAL PORT set but empty, and empty isn't valid" in _flat(result)
        assert open(".env").read() == "PORT=\n"


def test_a_real_process_without_a_terminal_never_hangs(tmp_path):
    """The fixture that simulates a terminal doesn't reach a subprocess:
    this is the real no-terminal path (B5)."""
    os.chdir(tmp_path)
    _project()
    proc = subprocess.run(
        [sys.executable, "-m", "envshield", "setup"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
    )
    output = proc.stdout + proc.stderr
    assert proc.returncode == 1, output
    assert "SET YOURSELF" in output
    assert "Input is not a terminal" not in output
    assert SENTINEL not in output
    assert "DEFAULTED=8000" in open(tmp_path / ".env").read()


def test_a_known_secret_never_reaches_stdout_or_stderr(tmp_path, monkeypatch):
    _no_terminal(monkeypatch)
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _write("envshield.yml", "services:\n  app:\n    schema: env.schema.toml\n")
        _write(
            "env.schema.toml",
            '[API_KEY]\ndescription = "k"\nsecret = true\n\n'
            '[OTHER]\ndescription = "o"\ntype = "port"\n',
        )
        _write(".env", f"API_KEY={SENTINEL}\nOTHER={SENTINEL}\n")
        _write(".env.example", f"API_KEY={SENTINEL}x\n")
        result = runner.invoke(app, ["setup"])
        assert SENTINEL not in result.stdout
        assert SENTINEL not in (result.stderr or "")
        assert "SET YOURSELF OTHER invalid" in _flat(result)
