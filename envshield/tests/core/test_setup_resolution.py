# envshield/tests/core/test_setup_resolution.py
"""V3-1 (#20): setup resolves every variable first, then writes each file
once, atomically, in place.

Precedence per variable: (1) the local file's value, an explicit empty one
included; (3) the schema default (absent, non-secret); (4) the template
value (non-secret, non-empty, valid); (5) a prompt (required now; may be
skipped); (6) unset. The explicit-empty table, the write rules and the
acceptance scenarios from the issue, each tested.
"""

import os
import stat
import subprocess

import pytest
from typer.testing import CliRunner

from envshield.cli import app
from envshield.core import file_updater, hooks_manager

runner = CliRunner()
SENTINEL = "zq9-setup-sentinel-value"


def _write(path, content):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


def _read(path=".env"):
    with open(path) as f:
        return f.read()


def _project(schema, env=None, example=None):
    _write("envshield.yml", "services:\n  app:\n    schema: env.schema.toml\n")
    _write("env.schema.toml", schema)
    if env is not None:
        _write(".env", env)
    if example is not None:
        _write(".env.example", example)


def _flat(result):
    return " ".join(result.stdout.split())


@pytest.fixture
def prompts(mocker):
    """Records every text prompt by variable name; answers from a dict."""
    answers = {}
    asked = []

    def ask(message, password=False, **kwargs):
        name = message.split("[bold cyan]")[1].split("[/bold cyan]")[0]
        asked.append((name, password))
        return answers.get(name, "")

    mocker.patch("envshield.core.setup_manager.Prompt.ask", side_effect=ask)
    return answers, asked


@pytest.fixture
def selects(mocker):
    """Answers questionary.select by its first matching choice prefix."""
    plan = []
    seen = []

    def select(message, choices, **kwargs):
        seen.append((message, list(choices)))
        prefix = plan.pop(0) if plan else None
        chosen = next((c for c in choices if prefix and c.startswith(prefix)), None)
        return mocker.Mock(ask=mocker.Mock(return_value=chosen))

    mocker.patch("questionary.select", side_effect=select)
    return plan, seen


# --- Precedence -------------------------------------------------------------------


def test_local_value_wins_over_default_template_and_prompt(tmp_path, prompts):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project(
            '[PORT]\ndescription = "p"\ntype = "port"\ndefaultValue = "8000"\n',
            env="PORT=9000\n",
            example="PORT=7000\n",
        )
        assert runner.invoke(app, ["setup"]).exit_code == 0
        assert _read() == "PORT=9000\n"
        assert prompts[1] == []


def test_default_wins_over_template_and_prompt(tmp_path, prompts):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project(
            '[PORT]\ndescription = "p"\ntype = "port"\ndefaultValue = "8000"\n',
            example="PORT=7000\n",
        )
        assert runner.invoke(app, ["setup"]).exit_code == 0
        assert "PORT=8000" in _read()
        assert prompts[1] == []


def test_valid_template_value_wins_over_prompt(tmp_path, prompts):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project('[HOST]\ndescription = "h"\n', example="HOST=localhost\n")
        assert runner.invoke(app, ["setup"]).exit_code == 0
        assert "HOST=localhost" in _read()
        assert prompts[1] == []


def test_an_invalid_template_value_is_not_used(tmp_path, prompts):
    answers, asked = prompts
    answers["PORT"] = "8080"
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project('[PORT]\ndescription = "p"\ntype = "port"\n', example="PORT=abc\n")
        assert runner.invoke(app, ["setup"]).exit_code == 0
        assert "PORT=8080" in _read()
        assert asked == [("PORT", False)]


def test_an_empty_template_slot_counts_as_absent(tmp_path, prompts):
    # A template 'KEY=' blocks neither the default nor the prompt.
    answers, asked = prompts
    answers["TOKEN_NAME"] = "x"
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project(
            '[PORT]\ndescription = "p"\ndefaultValue = "8000"\n\n'
            '[TOKEN_NAME]\ndescription = "t"\n',
            example="PORT=\nTOKEN_NAME=\n",
        )
        assert runner.invoke(app, ["setup"]).exit_code == 0
        content = _read()
        assert "PORT=8000" in content and "TOKEN_NAME=x" in content
        assert asked == [("TOKEN_NAME", False)]


def test_a_secret_in_the_template_is_never_copied(tmp_path, prompts):
    answers, asked = prompts
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project(
            '[API_KEY]\ndescription = "k"\nsecret = true\n',
            example=f"API_KEY={SENTINEL}\n",
        )
        result = runner.invoke(app, ["setup"])
        assert result.exit_code == 1, result.stdout  # skipped, left to set
        assert SENTINEL not in _read()
        assert SENTINEL not in result.stdout
        assert asked == [("API_KEY", True)]  # masked


def test_an_absent_optional_variable_is_never_prompted(tmp_path, prompts):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project('[DEBUG_FLAG]\ndescription = "d"\nrequired = false\n')
        assert runner.invoke(app, ["setup"]).exit_code == 0
        assert prompts[1] == []
        assert "DEBUG_FLAG" not in _read()


# --- The explicit-empty table --------------------------------------------------------


def test_required_empty_is_reported_then_prompted(tmp_path, prompts):
    answers, asked = prompts
    answers["NAME"] = "given"
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project('[NAME]\ndescription = "n"\n', env="NAME=\n")
        result = runner.invoke(app, ["setup"])
        assert "NAME is set but empty" in result.stdout
        assert _read() == "NAME=given\n"


def test_required_empty_with_a_default_is_kept_when_declined(tmp_path, selects):
    # Acceptance: PORT= with defaultValue 8000 is never replaced without
    # confirmation, and declining keeps PORT=.
    plan, seen = selects
    plan.append("Leave it as it is")
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project('[PORT]\ndescription = "p"\ndefaultValue = "8000"\n', env="PORT=\n")
        result = runner.invoke(app, ["setup"])
        assert result.exit_code == 1, result.stdout  # required, left empty
        assert _read() == "PORT=\n"
        assert any("Use the default (8000)" in c for _, cs in seen for c in cs)


def test_required_empty_with_a_default_takes_it_when_chosen(tmp_path, selects):
    plan, _ = selects
    plan.append("Use the default")
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project('[PORT]\ndescription = "p"\ndefaultValue = "8000"\n', env="PORT=\n")
        assert runner.invoke(app, ["setup"]).exit_code == 0
        assert _read() == "PORT=8000\n"


def test_optional_empty_that_is_valid_is_kept(tmp_path, prompts, selects):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project('[NOTE]\ndescription = "n"\nrequired = false\n', env="NOTE=\n")
        assert runner.invoke(app, ["setup"]).exit_code == 0
        assert _read() == "NOTE=\n"
        assert prompts[1] == [] and selects[1] == []


@pytest.mark.parametrize(
    "choice,expected",
    [
        ("Enter a value", "PORT=8080\n"),
        ("Remove the line", ""),
        ("Keep it empty", "PORT=\n"),
    ],
)
def test_optional_empty_that_is_invalid_offers_three_choices(
    tmp_path, prompts, selects, choice, expected
):
    answers, _ = prompts
    answers["PORT"] = "8080"
    plan, seen = selects
    plan.append(choice)
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project(
            '[PORT]\ndescription = "p"\ntype = "port"\nrequired = false\n',
            env="PORT=\n",
        )
        result = runner.invoke(app, ["setup"])
        assert result.exit_code == 0, result.stdout
        assert "PORT is set but empty, and empty isn't valid" in _flat(result)
        assert _read() == expected


# --- Invalid values and declining --------------------------------------------------


def test_an_invalid_value_is_replaced_only_after_confirmation(
    tmp_path, mocker, prompts
):
    answers, _ = prompts
    answers["B_PORT"] = "8081"
    confirm = mocker.patch("questionary.confirm")
    confirm.return_value.ask.side_effect = [False, True]  # decline A, accept B
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project(
            '[A_PORT]\ndescription = "a"\ntype = "port"\n\n'
            '[B_PORT]\ndescription = "b"\ntype = "port"\n',
            env="A_PORT=nope\nB_PORT=bad\n",
        )
        result = runner.invoke(app, ["setup"])
        # Declining one replacement doesn't cancel setup (B4); A_PORT is left
        # invalid, so the exit code is 1 (#21).
        assert result.exit_code == 1, result.stdout
        assert _read() == "A_PORT=nope\nB_PORT=8081\n"
        assert "nope" not in result.stdout and "bad" not in result.stdout


# --- Writing ---------------------------------------------------------------------


def test_unknown_lines_comments_and_export_are_kept(tmp_path, prompts):
    answers, _ = prompts
    answers["NEW_VAR"] = "v"
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project(
            '[PORT]\ndescription = "p"\n\n[NEW_VAR]\ndescription = "n"\n',
            env="# my notes\nexport PORT=9000\nLOCAL_ONLY=1\n",
        )
        result = runner.invoke(app, ["setup"])
        assert result.exit_code == 0, result.stdout
        assert _read() == "# my notes\nexport PORT=9000\nLOCAL_ONLY=1\n\nNEW_VAR=v\n"
        assert ".env updated (1 added, 0 changed)" in _flat(result)


def test_ctrl_c_while_prompting_leaves_the_file_byte_identical(tmp_path, mocker):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project(
            '[A]\ndescription = "a"\n\n[B]\ndescription = "b"\n',
            env="# keep\nOTHER=1\n",
        )
        os.chmod(".env", 0o640)
        before = open(".env", "rb").read()
        mocker.patch(
            "envshield.core.setup_manager.Prompt.ask",
            side_effect=["a-value", KeyboardInterrupt],
        )
        runner.invoke(app, ["setup"])
        assert open(".env", "rb").read() == before
        assert stat.S_IMODE(os.stat(".env").st_mode) == 0o640
        assert not [f for f in os.listdir(".") if "envshield-tmp" in f]


def test_one_atomic_write_and_a_0600_result(tmp_path, mocker, prompts):
    answers, _ = prompts
    answers.update(A="1", B="2")
    spy = mocker.spy(file_updater, "atomic_write")
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project('[A]\ndescription = "a"\n\n[B]\ndescription = "b"\n', env="X=1\n")
        os.chmod(".env", 0o644)
        assert runner.invoke(app, ["setup"]).exit_code == 0
        assert spy.call_count == 1
        assert stat.S_IMODE(os.stat(".env").st_mode) == 0o600
        assert not [f for f in os.listdir(".") if "envshield-tmp" in f]


def test_nothing_to_change_writes_nothing(tmp_path, mocker):
    spy = mocker.spy(file_updater, "atomic_write")
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project('[A]\ndescription = "a"\n', env="A=1\n")
        result = runner.invoke(app, ["setup"])
        assert result.exit_code == 0
        assert spy.call_count == 0
        assert ".env unchanged" in result.stdout


def test_a_shared_file_is_written_once_with_the_union_contract(
    tmp_path, mocker, prompts
):
    answers, _ = prompts
    answers.update(API_ONLY="a", WORKER_ONLY="w")
    spy = mocker.spy(file_updater, "atomic_write")
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _write(
            "envshield.yml",
            "schema: env.schema.toml\nservices:\n"
            "  api:\n    dir: api\n    local_file: .env\n"
            "  worker:\n    dir: worker\n    local_file: .env\n",
        )
        _write(
            "env.schema.toml",
            '[API_ONLY]\ndescription = "a"\nservices = ["api"]\n\n'
            '[WORKER_ONLY]\ndescription = "w"\nservices = ["worker"]\n',
        )
        result = runner.invoke(app, ["setup", "--service", "api"])
        assert result.exit_code == 0, result.stdout
        assert spy.call_count == 1
        content = _read()
        assert "API_ONLY=a" in content and "WORKER_ONLY=w" in content


def test_a_symlinked_local_file_is_refused_before_any_prompt(tmp_path, prompts):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "victim").write_text("ORIGINAL=1\n")
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project('[A]\ndescription = "a"\n')
        os.symlink(outside / "victim", ".env")
        result = runner.invoke(app, ["setup"])
        assert result.exit_code != 0
        assert prompts[1] == []
        assert (outside / "victim").read_text() == "ORIGINAL=1\n"


def test_a_contract_load_error_aborts_before_anything_is_resolved(tmp_path, prompts):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project("[BROKEN\n", env="A=1\n")
        result = runner.invoke(app, ["setup"])
        assert result.exit_code != 0
        assert prompts[1] == []
        assert _read() == "A=1\n"


# --- Scenario 8 ------------------------------------------------------------------


def _git(*args):
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        check=True,
        capture_output=True,
    )


def test_scenario_8_a_pull_adds_a_required_variable(tmp_path, mocker, prompts):
    """A pull adds a required variable -> the post-merge hook names it ->
    setup asks only for that variable."""
    answers, asked = prompts
    answers["NEW_REQUIRED"] = "n"
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _git("init", "-q")
        _project('[EXISTING]\ndescription = "e"\n', env="EXISTING=1\n")
        _write(".gitignore", ".env\n")
        _git("add", "-A")
        _git("commit", "-qm", "one")
        with open("env.schema.toml", "a") as f:
            f.write('\n[NEW_REQUIRED]\ndescription = "n"\n')
        _git("commit", "-qam", "two")

        calls = []
        mocker.patch.object(
            hooks_manager,
            "_envshield",
            side_effect=lambda *a, **k: calls.append(a) or 0,
        )
        hooks_manager.run_post_merge()
        assert any("doctor" in a for a in calls), calls

        doctor = runner.invoke(app, ["doctor", "--service", "app"])
        assert "NEW_REQUIRED" in doctor.stdout

        result = runner.invoke(app, ["setup"])
        assert result.exit_code == 0, result.stdout
        assert asked == [("NEW_REQUIRED", False)]
        assert _read() == "EXISTING=1\n\nNEW_REQUIRED=n\n"
