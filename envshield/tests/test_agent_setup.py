# envshield/tests/test_agent_setup.py
"""V6-3 (#63, BL-192): agent setup.

- 'agent guide' prints version-matched instructions for running the check
  and maintaining env.schema.toml.
- 'agent install' writes a marked AGENTS.md (and CLAUDE.md) section and
  Claude Code/Codex PostToolUse hooks: idempotent, keeps the user's own
  content, never follows a symlink (BL-154).
- 'agent hook' (what those hooks run) is silent on a pass and reports a
  failure on stderr with exit code 2, never a value.
"""

import json
import os
import subprocess

import pytest
import typer
from typer.testing import CliRunner

from envshield import __version__
from envshield.cli import app
from envshield.core import agent_setup
from envshield.core.exceptions import EnvShieldException

runner = CliRunner()

SECRET = "sk_live_canary_4a7e1c9b2d"
START = agent_setup.SECTION_START
END = agent_setup.SECTION_END


def _git(*args):
    subprocess.run(["git", *args], check=True, capture_output=True)


def _write(path, content):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


def _read(path):
    with open(path) as f:
        return f.read()


def _project(commit=True):
    _git("init", "-q")
    _git("config", "user.email", "t@example.com")
    _git("config", "user.name", "T")
    _git("config", "commit.gpgsign", "false")
    _write("envshield.yml", "services:\n  api:\n    schema: env.schema.toml\n")
    _write(
        "env.schema.toml",
        '[PORT]\ndescription = "p"\ntype = "port"\n\n'
        '[STRIPE_KEY]\ndescription = "s"\nsecret = true\n',
    )
    _write(".env", f"PORT=8000\nSTRIPE_KEY={SECRET}\n")
    _write(".gitignore", ".env\n")
    _write(
        "app.py",
        'import os\nPORT = os.environ["PORT"]\nKEY = os.environ["STRIPE_KEY"]\n',
    )
    if commit:
        _git("add", "-A")
        _git("commit", "-q", "-m", "base")


def _install(*args):
    return runner.invoke(app, ["agent", "install", *args])


def _hook():
    return runner.invoke(app, ["agent", "hook"], input="{}")


def _hook_entries(path):
    groups = json.loads(_read(path))["hooks"]["PostToolUse"]
    return [
        h
        for g in groups
        for h in g["hooks"]
        if h.get("command") == agent_setup.HOOK_COMMAND
    ]


# --- agent guide ------------------------------------------------------------


def test_guide_names_version_check_and_maintenance_rules():
    result = runner.invoke(app, ["agent", "guide"])
    assert result.exit_code == 0
    out = result.stdout
    assert f"envshield {__version__}" in out
    assert agent_setup.POST_CHANGE_CHECK in out
    for rule in (
        "secret = true",
        "requiredIn",
        "defaultValue",
        "envshield schema sync",
        '"human"',
        "Never invent",
    ):
        assert rule in out


def test_guide_names_only_commands_that_exist():
    # The guide must not go stale: every 'envshield X' it names is a command.
    names = set(typer.main.get_command(app).commands)
    for line in agent_setup.guide().splitlines():
        for part in line.split("envshield ")[1:]:
            word = part.split()[0].strip("`'.,)")
            if word[:1].isalpha():
                assert word in names, word


# --- agent install ----------------------------------------------------------


def test_install_writes_section_and_both_hooks(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project()
        result = _install()
        assert result.exit_code == 0, result.stdout
        agents = _read("AGENTS.md")
        assert agents.startswith(START) and agents.rstrip().endswith(END)
        assert "envshield agent guide" in agents
        assert agent_setup.POST_CHANGE_CHECK in agents
        claude = json.loads(_read(".claude/settings.json"))
        assert claude["hooks"]["PostToolUse"][0]["matcher"].startswith("Edit")
        assert len(_hook_entries(".claude/settings.json")) == 1
        codex = json.loads(_read(".codex/hooks.json"))
        assert "apply_patch" in codex["hooks"]["PostToolUse"][0]["matcher"]
        assert len(_hook_entries(".codex/hooks.json")) == 1


def test_install_is_idempotent(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project()
        _install()
        files = ["AGENTS.md", ".claude/settings.json", ".codex/hooks.json"]
        before = {f: _read(f) for f in files}
        result = _install()
        assert result.exit_code == 0
        assert result.stdout.count("already up to date") == 3
        assert {f: _read(f) for f in files} == before


def test_install_keeps_users_agents_md_and_updates_section_in_place(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project()
        _write("AGENTS.md", "# Our rules\n\nUse tabs.\n")
        _install()
        text = _read("AGENTS.md")
        assert text.startswith("# Our rules\n\nUse tabs.\n\n" + START)
        # Hand-edit our section and add the user's content after it.
        stale = text.replace("Never\ninvent", "OLD TEXT")
        _write("AGENTS.md", stale + "\n## Later\n\nKeep me.\n")
        _install()
        text = _read("AGENTS.md")
        assert "OLD TEXT" not in text
        assert text.startswith("# Our rules\n\nUse tabs.\n\n" + START)
        assert text.endswith(END + "\n\n## Later\n\nKeep me.\n")
        assert text.count(START) == 1


def test_install_refuses_damaged_markers_and_leaves_file(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project()
        damaged = f"# Rules\n{START}\nhalf a section\n"
        _write("AGENTS.md", damaged)
        result = _install()
        assert result.exit_code == 1
        assert "markers are damaged" in result.stdout
        assert _read("AGENTS.md") == damaged
        # The hooks are still installed: one bad file doesn't stop the rest.
        assert os.path.exists(".claude/settings.json")


def test_claude_md_gets_section_unless_it_imports_agents_md(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project()
        _write("CLAUDE.md", "# Claude notes\n")
        _install()
        assert START in _read("CLAUDE.md")
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project()
        _write("CLAUDE.md", "See @AGENTS.md\n")
        result = _install()
        assert "it imports AGENTS.md" in result.stdout
        assert _read("CLAUDE.md") == "See @AGENTS.md\n"


def test_install_keeps_existing_settings_and_hooks(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project()
        existing = {
            "permissions": {"allow": ["Bash(ls)"]},
            "hooks": {
                "PostToolUse": [
                    {
                        "matcher": "Write",
                        "hooks": [{"type": "command", "command": "prettier -w"}],
                    }
                ],
                "Stop": [{"hooks": [{"type": "command", "command": "make lint"}]}],
            },
        }
        _write(".claude/settings.json", json.dumps(existing))
        _install("--agent", "claude")
        data = json.loads(_read(".claude/settings.json"))
        assert data["permissions"]["allow"] == ["Bash(ls)", *agent_setup.CLAUDE_ALLOW]
        assert data["hooks"]["Stop"] == existing["hooks"]["Stop"]
        assert data["hooks"]["PostToolUse"][0] == existing["hooks"]["PostToolUse"][0]
        assert len(_hook_entries(".claude/settings.json")) == 1
        assert not os.path.exists(".codex")


def test_changed_envshield_hook_is_replaced_only_when_confirmed(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project()
        old = {
            "hooks": {
                "PostToolUse": [
                    {
                        "matcher": "Write",
                        "hooks": [
                            {"type": "command", "command": "envshield agent hook --x"}
                        ],
                    }
                ]
            }
        }
        _write(".claude/settings.json", json.dumps(old))
        result = _install("--agent", "claude")  # no terminal, no --yes
        assert result.exit_code == 0
        assert "kept the existing EnvShield hook" in result.stdout
        # The old hook is kept; the allow rules are still added (#89).
        data = json.loads(_read(".claude/settings.json"))
        assert data["hooks"] == old["hooks"]
        assert data["permissions"]["allow"] == list(agent_setup.CLAUDE_ALLOW)
        result = _install("--agent", "claude", "--yes")
        groups = json.loads(_read(".claude/settings.json"))["hooks"]["PostToolUse"]
        assert len(groups) == 1
        assert groups[0]["hooks"][0]["command"] == agent_setup.HOOK_COMMAND


def test_invalid_json_config_is_left_unchanged(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project()
        _write(".codex/hooks.json", "{not json")
        result = _install("--agent", "codex")
        assert result.exit_code == 1
        assert "isn't valid JSON" in result.stdout
        assert _read(".codex/hooks.json") == "{not json"


@pytest.mark.parametrize("target", ["AGENTS.md", "CLAUDE.md"])
def test_symlinked_instructions_file_outside_project_is_refused(tmp_path, target):
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / "victim.md"
    victim.write_text("outside content\n")
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project()
        os.symlink(victim, target)
        result = _install()
        assert result.exit_code == 1
        assert "symlink" in result.stdout
        assert victim.read_text() == "outside content\n"


def test_symlinked_hook_config_dir_outside_project_is_refused(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project()
        os.symlink(outside, ".claude")
        result = _install("--agent", "claude")
        assert result.exit_code == 1
        assert "symlink" in result.stdout
        assert list(outside.iterdir()) == []


def test_install_needs_a_project(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        result = _install()
        assert result.exit_code == 1
        assert "envshield init" in result.stdout
        assert not os.path.exists("AGENTS.md")


def test_install_rejects_unknown_agent(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project()
        result = _install("--agent", "cursor")
        assert result.exit_code != 0
        assert not os.path.exists("AGENTS.md")


def test_init_without_terminal_names_agent_install(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _write(".env", "PORT=8000\n")
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 0, result.stdout
        assert "envshield agent install" in result.stdout
        assert not os.path.exists("AGENTS.md")


# --- agent hook -------------------------------------------------------------


def test_hook_is_silent_when_the_check_passes(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project()
        _write("app.py", _read("app.py") + "# edited\n")
        result = _hook()
        assert result.exit_code == 0
        assert result.stdout == "" and result.stderr == ""


def test_hook_reports_new_undeclared_read_with_exit_2_and_no_value(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project()
        _write(
            "app.py",
            _read("app.py")
            + 'NEW = os.getenv("NEW_FLAG")\n'
            + f'HARDCODED = "{SECRET}"\n',
        )
        result = _hook()
        assert result.exit_code == 2
        assert "NEW_FLAG (api) at app.py:4" in result.stderr
        assert "Declare NEW_FLAG in env.schema.toml" in result.stderr
        assert "envshield agent guide" in result.stderr
        assert SECRET not in result.stderr + result.stdout


def test_hook_ignores_undeclared_read_already_there_at_head(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project(commit=False)
        _write("app.py", _read("app.py") + 'OLD = os.getenv("OLD_FLAG")\n')
        _git("add", "-A")
        _git("commit", "-q", "-m", "base")
        _write("app.py", _read("app.py") + "# edited\n")
        assert _hook().exit_code == 0


def test_hook_reports_a_check_that_cannot_run(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project()
        _write("env.schema.toml", "[PORT\n")
        result = _hook()
        assert result.exit_code == 2
        assert "Schema parse error" in result.stderr


def test_hook_without_a_commit_checks_every_read(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project(commit=False)
        _write("app.py", _read("app.py") + 'NEW = os.getenv("NEW_FLAG")\n')
        result = _hook()
        assert result.exit_code == 2
        assert "NEW_FLAG" in result.stderr


def test_hook_outside_a_project_does_nothing(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        result = _hook()
        assert result.exit_code == 0
        assert result.stderr == ""


# --- upsert_section ---------------------------------------------------------


@pytest.mark.parametrize(
    "text,prefix",
    [
        (None, ""),
        ("", ""),
        ("# R", "# R\n\n"),
        ("# R\n", "# R\n\n"),
        ("# R\n\n", "# R\n\n"),
    ],
)
def test_upsert_section_separates_from_existing_text(text, prefix):
    out = agent_setup.upsert_section(text)
    assert out.startswith(prefix + START)
    assert agent_setup.upsert_section(out) == out


def test_upsert_section_rejects_end_before_start():
    with pytest.raises(EnvShieldException):
        agent_setup.upsert_section(f"{END}\n{START}\n")


def test_init_offers_agent_setup_once(tmp_path, mocker):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _git("init", "-q")
        mocker.patch("envshield.core.hooks_manager._is_interactive", return_value=True)
        confirm = mocker.patch("questionary.confirm")
        confirm.return_value.ask.return_value = True
        _write(".env", "PORT=8000\n")
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 0, result.stdout
        assert START in _read("AGENTS.md")
        assert len(_hook_entries(".claude/settings.json")) == 1
        assert len(_hook_entries(".codex/hooks.json")) == 1
        prompts = [c.args[0] for c in confirm.call_args_list]
        assert any("coding agents" in p for p in prompts)
        confirm.reset_mock()
        runner.invoke(app, ["init", "--force"])
        prompts = [c.args[0] for c in confirm.call_args_list]
        assert not any("coding agents" in p for p in prompts)


def test_hook_config_keeps_non_ascii_text(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project()
        _write(".claude/settings.json", '{"note": "café ✓"}')
        _install("--agent", "claude")
        assert '"note": "café ✓"' in _read(".claude/settings.json")


# Real projects link the two files (BL-192 trial: cal.com and PostHog link
# CLAUDE.md -> AGENTS.md, Twenty AGENTS.md -> CLAUDE.md, Langfuse
# AGENTS.md -> .agents/AGENTS.md). The real file is written once.
@pytest.mark.parametrize(
    "real,link",
    [
        ("AGENTS.md", "CLAUDE.md"),
        ("CLAUDE.md", "AGENTS.md"),
        (".agents/AGENTS.md", "AGENTS.md"),
    ],
)
def test_in_project_link_between_instruction_files_is_written_once(
    tmp_path, real, link
):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project()
        _write(real, "# Team rules\n")
        os.symlink(os.path.relpath(real, os.path.dirname(link) or "."), link)
        result = _install()
        assert result.exit_code == 0, result.stdout
        assert os.path.islink(link)
        text = _read(real)
        assert text.startswith("# Team rules\n\n" + START)
        assert text.count(START) == 1
        assert f"{link} links to it" in result.stdout
        real_name = os.path.basename(real)
        if real_name != link:
            assert f"{real_name} links to it" not in result.stdout
        before = _read(real)
        assert _install().exit_code == 0
        assert _read(real) == before


def test_link_to_another_in_project_file_is_refused(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project()
        before = _read("app.py")
        os.symlink("app.py", "AGENTS.md")
        result = _install()
        assert result.exit_code == 1
        assert "symlink" in result.stdout
        assert _read("app.py") == before


def test_non_utf8_file_is_reported_and_left_unchanged(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project()
        with open("AGENTS.md", "wb") as f:
            f.write(b"\xff\xfe rules\n")
        result = _install()
        assert result.exit_code == 1
        assert "isn't UTF-8 text" in result.stdout
        with open("AGENTS.md", "rb") as f:
            assert f.read() == b"\xff\xfe rules\n"


# --- Claude Code allow rules (#89, BL-206) -----------------------------------


def test_allow_rules_are_exactly_the_commands_the_instructions_name():
    # The AGENTS.md section names 'agent guide'; the guide names the rest.
    guide = agent_setup.guide() + agent_setup._instructions_section()
    assert len(agent_setup.CLAUDE_ALLOW) == 4
    for rule in agent_setup.CLAUDE_ALLOW:
        assert rule.startswith("Bash(") and rule.endswith(")")
        command = rule[len("Bash(") : -1]
        # Exact commands, never a prefix rule.
        assert ":*" not in command and "*" not in command
        assert command in guide, command


def test_install_adds_allow_rules_once_and_keeps_users_permissions(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project()
        existing = {
            "permissions": {
                "allow": ["Bash(npm test)", "Bash(envshield check --json)"],
                "deny": ["Bash(envshield agent guide)"],
                "ask": ["Bash(git push)"],
                "defaultMode": "acceptEdits",
            }
        }
        _write(".claude/settings.json", json.dumps(existing))
        assert _install("--agent", "claude").exit_code == 0
        perms = json.loads(_read(".claude/settings.json"))["permissions"]
        assert perms["deny"] == existing["permissions"]["deny"]
        assert perms["ask"] == existing["permissions"]["ask"]
        assert perms["defaultMode"] == "acceptEdits"
        assert perms["allow"][:2] == existing["permissions"]["allow"]
        for rule in agent_setup.CLAUDE_ALLOW:
            assert perms["allow"].count(rule) == 1
        before = _read(".claude/settings.json")
        result = _install("--agent", "claude")
        assert "already up to date" in result.stdout
        assert _read(".claude/settings.json") == before


def test_allow_rules_added_to_an_install_that_predates_them(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project()
        _install("--agent", "claude")
        data = json.loads(_read(".claude/settings.json"))
        del data["permissions"]
        _write(".claude/settings.json", json.dumps(data))
        result = _install("--agent", "claude")
        assert ".claude/settings.json: updated" in result.stdout
        perms = json.loads(_read(".claude/settings.json"))["permissions"]
        assert perms["allow"] == list(agent_setup.CLAUDE_ALLOW)


def test_codex_config_gets_no_permissions(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project()
        _install("--agent", "codex")
        assert "permissions" not in json.loads(_read(".codex/hooks.json"))
        assert not os.path.exists(".claude")


@pytest.mark.parametrize(
    "settings",
    ['{"permissions": []}', '{"permissions": {"allow": "Bash(ls)"}}'],
)
def test_malformed_permissions_leave_settings_unchanged(tmp_path, settings):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project()
        _write(".claude/settings.json", settings)
        result = _install("--agent", "claude")
        assert result.exit_code == 1
        assert "left unchanged" in " ".join(result.stdout.split())
        assert _read(".claude/settings.json") == settings


def test_install_names_the_flag_for_unattended_claude_runs(tmp_path):
    # Project allow rules need folder trust, which 'claude -p' never gives.
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project()
        out = _install("--agent", "claude").stdout
        flag = agent_setup.claude_allowed_tools_args()
        assert flag in out
        for rule in agent_setup.CLAUDE_ALLOW:
            assert f'"{rule}"' in flag
        assert "allowedTools" not in _install("--agent", "codex").stdout


# --- Guide stages (#90, BL-207) ----------------------------------------------


def _section(guide, heading):
    start = guide.index(heading)
    nxt = guide.find("\n## ", start + 1)
    # Wrapping-independent: one space between words.
    return " ".join(guide[start : nxt if nxt != -1 else None].split())


def test_guide_covers_all_three_stages():
    guide = agent_setup.guide()
    dev = _section(guide, "## Development")
    assert agent_setup.POST_CHANGE_CHECK in dev
    deploy = _section(guide, "## Deployment")
    for text in (
        "envshield check --env NAME --json",
        "envshield check --runtime --env NAME",
        "last step before the app",
        "COULD NOT VERIFY",
        "never inline a value",
        "envshield schema diff",
        "Don't run it on a developer",
    ):
        assert text in deploy, text
    onboarding = _section(guide, "## Onboarding")
    for text in (
        agent_setup.FULL_CHECK,
        agent_setup.SETUP_NON_INTERACTIVE,
        "SET YOURSELF",
        "their own terminal",
        "never writes a secret",
    ):
        assert text in onboarding, text


def test_guide_contains_nothing_that_looks_like_a_secret_value():
    import re

    guide = agent_setup.guide()
    assert not re.search(r"\b(sk|pk|rk)_(live|test)_|ghp_|xox[abp]-|AKIA", guide)
    # No NAME=value assignment with a value.
    assert not re.search(r"\b[A-Z][A-Z0-9_]+=\S", guide)


def test_guide_deployment_advice_matches_check_output(tmp_path):
    # What the guide says a manifest finding looks like is what check says.
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _git("init", "-q")
        _write(
            "envshield.yml",
            "environments:\n  - production\nservices:\n  api:\n"
            "    schema: env.schema.toml\nmanifests:\n"
            "  - file: compose.prod.yml\n    environment: production\n"
            "    containers:\n      api: api\n",
        )
        _write(
            "env.schema.toml",
            '[STRIPE_KEY]\ndescription = "s"\nsecret = true\n'
            'requiredIn = ["production"]\n\n'
            '[SENTRY_DSN]\ndescription = "d"\nrequiredIn = ["production"]\n',
        )
        _write(
            "compose.prod.yml",
            "services:\n  api:\n    image: x\n    environment:\n"
            "      STRIPE_KEY: ${STRIPE_KEY}\n",
        )
        result = runner.invoke(app, ["check", "--env", "production", "--json"])
        findings = {
            f["variable"]: f
            for r in json.loads(result.stdout)["reports"]
            for f in r["findings"]
        }
        assert findings["SENTRY_DSN"]["next_step"]["by"] == "human"
        assert (
            "supply it in this deployment manifest"
            in (findings["SENTRY_DSN"]["next_step"]["text"])
        )
        assert findings["STRIPE_KEY"]["verdict"] == "unverified"
        deploy = _section(agent_setup.guide(), "## Deployment")
        assert "supply it in this deployment manifest" in deploy


def test_guide_onboarding_matches_checks_agent_step(tmp_path):
    # check gives a missing non-secret with a default to the agent; the
    # guide must allow exactly that step, not contradict it.
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _git("init", "-q")
        _write("envshield.yml", "services:\n  api:\n    schema: env.schema.toml\n")
        _write(
            "env.schema.toml",
            '[PORT]\ndescription = "p"\ntype = "port"\ndefaultValue = "8000"\n',
        )
        _write(".env", "")
        result = runner.invoke(app, ["check", "--json"])
        step = json.loads(result.stdout)["reports"][0]["findings"][0]["next_step"]
        assert step["by"] == "agent"
        assert "defaultValue" in step["text"]
        onboarding = _section(agent_setup.guide(), "## Onboarding")
        # The guide routes check's agent step through setup, which does it.
        assert agent_setup.SETUP_NON_INTERACTIVE in onboarding
        assert "defaultValue" in onboarding
