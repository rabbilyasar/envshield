"""BL-222 (#123): with --yes, 'init' and 'service discover' never wait on a
terminal. The git-hook and agent-setup offers are skipped and the command is
named instead, as without a terminal; without --yes a terminal still gets
the offers."""

import os
import subprocess

import pytest
import yaml
from typer.testing import CliRunner

from envshield.cli import app

runner = CliRunner()

HOOK_HINT = "Run 'envshield hook install' to add the git hooks."
AGENT_HINT = "envshield agent install"

CONTRACT_PROJECT = """\
schema: env.schema.toml
services:
  root:
    dir: .
"""


def _write(path, content):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


def _git_init():
    subprocess.run(["git", "init", "-q"], check=True)


def _flat(result):
    return " ".join((result.stdout + (result.stderr or "")).split())


@pytest.fixture
def terminal_never_prompted(mocker):
    """A terminal is present, and any prompt fails the test."""

    def boom(*a, **k):
        raise AssertionError("prompted despite --yes")

    mocker.patch("envshield.core.hooks_manager._is_interactive", return_value=True)
    mocker.patch("envshield.core.service_manager._is_interactive", return_value=True)
    mocker.patch("questionary.confirm", side_effect=boom)
    mocker.patch("questionary.select", side_effect=boom)
    mocker.patch("questionary.checkbox", side_effect=boom)


def _assert_skipped_and_named(result, agent=True):
    flat = _flat(result)
    assert result.exit_code == 0, flat
    assert HOOK_HINT in flat
    if agent:
        assert AGENT_HINT in flat
    assert not os.path.exists(".git/hooks/pre-commit")
    assert not os.path.exists(".git/hooks/post-merge")
    assert not os.path.exists("AGENTS.md")


def test_init_yes_single_directory(tmp_path, terminal_never_prompted):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _git_init()
        _write(".env", "PORT=8000\n")
        result = runner.invoke(app, ["init", "--yes"])
        _assert_skipped_and_named(result)


def test_init_yes_service_directories(tmp_path, terminal_never_prompted):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _git_init()
        _write("api/.env", "API_PORT=8000\n")
        _write("web/.env", "WEB_PORT=3000\n")
        result = runner.invoke(app, ["init", "--yes"])
        _assert_skipped_and_named(result)
        services = yaml.safe_load(open("envshield.yml"))["services"]
        assert {"api", "web"} <= set(services)


def test_service_discover_yes_onto_the_contract(tmp_path, terminal_never_prompted):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _git_init()
        _write("envshield.yml", CONTRACT_PROJECT)
        _write("env.schema.toml", '[ROOT]\ndescription = "r"\n')
        _write("api/.env", "API_TOKEN=abc\n")
        result = runner.invoke(app, ["service", "discover", "--yes"])
        _assert_skipped_and_named(result, agent=False)
        assert "Added 1 service" in _flat(result)


def test_service_discover_yes_legacy(tmp_path, terminal_never_prompted):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _git_init()
        _write("envshield.yml", "services:\n  api:\n    schema: api/env.schema.toml\n")
        _write("api/env.schema.toml", '[API]\ndescription = "a"\n')
        _write("web/.env", "WEB_TOKEN=x\n")
        result = runner.invoke(app, ["service", "discover", "--yes"])
        _assert_skipped_and_named(result, agent=False)


def test_no_hook_hint_once_hooks_exist(tmp_path, terminal_never_prompted):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _git_init()
        for name in ("pre-commit", "post-merge"):
            _write(f".git/hooks/{name}", "#!/bin/sh\n")
        _write(".env", "PORT=8000\n")
        result = runner.invoke(app, ["init", "--yes"])
        assert result.exit_code == 0, _flat(result)
        assert HOOK_HINT not in _flat(result)


def test_init_without_yes_still_offers_hooks_and_agents(tmp_path, mocker):
    mocker.patch("envshield.core.hooks_manager._is_interactive", return_value=True)
    confirm = mocker.patch("questionary.confirm")
    confirm.return_value.ask.return_value = False
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _git_init()
        _write(".env", "PORT=8000\n")
        result = runner.invoke(app, ["init"])
        asked = [c.args[0] for c in confirm.call_args_list if c.args]
        assert any(a.startswith("Install git hooks?") for a in asked), asked
        assert any(a.startswith("Set up coding agents?") for a in asked), asked
        assert HOOK_HINT not in _flat(result)
