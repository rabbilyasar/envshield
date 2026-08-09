# envshield/tests/test_hook_cli.py
import os
import subprocess

from typer.testing import CliRunner

from envshield.cli import app

runner = CliRunner()


def _init_git_repo():
    subprocess.run(["git", "init", "-q"], check=True)


def test_hook_install_writes_both_hooks(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _init_git_repo()

        result = runner.invoke(app, ["hook", "install", "--yes"])

        assert result.exit_code == 0
        assert os.path.exists(".git/hooks/pre-commit")
        assert os.path.exists(".git/hooks/post-merge")


def test_hook_status_reports_installed_hooks(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _init_git_repo()
        runner.invoke(app, ["hook", "install", "--yes"])

        result = runner.invoke(app, ["hook", "status"])

        assert result.exit_code == 0
        assert "pre-commit hook" in result.stdout
        assert "post-merge hook" in result.stdout


def test_hook_remove_deletes_envshield_installed_hooks(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _init_git_repo()
        runner.invoke(app, ["hook", "install", "--yes"])

        result = runner.invoke(app, ["hook", "remove", "--yes"])

        assert result.exit_code == 0
        assert not os.path.exists(".git/hooks/pre-commit")
        assert not os.path.exists(".git/hooks/post-merge")


def test_hook_remove_leaves_a_hook_envshield_did_not_install(tmp_path):
    """Safety: a pre-existing, unrelated hook (Husky, a hand-written script) must never be deleted."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _init_git_repo()
        os.makedirs(".git/hooks", exist_ok=True)
        with open(".git/hooks/pre-commit", "w") as f:
            f.write("#!/bin/sh\necho 'not envshield'\n")

        result = runner.invoke(app, ["hook", "remove", "--yes"])

        assert result.exit_code == 0
        assert os.path.exists(".git/hooks/pre-commit")
        assert "No EnvShield-installed hooks" in result.stdout


def test_hook_remove_outside_git_repo_errors_clearly(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        result = runner.invoke(app, ["hook", "remove", "--yes"])

        assert result.exit_code == 1
        assert "Not inside a Git repository" in result.stdout


def test_hook_install_declines_without_a_tty_and_without_yes(tmp_path):
    """
    Regression guard: 'hook install'/'hook remove' now confirm before
    acting (they mutate files outside the project), but that confirmation
    must never block a non-interactive invocation the way the old,
    already-fixed prompts used to -- it should decline safely instead.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _init_git_repo()

        result = runner.invoke(app, ["hook", "install"])

        assert result.exit_code == 0
        assert not os.path.exists(".git/hooks/pre-commit")
        assert "Cancelled" in result.stdout


def test_hook_remove_declines_without_a_tty_and_without_yes(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _init_git_repo()
        runner.invoke(app, ["hook", "install", "--yes"])

        result = runner.invoke(app, ["hook", "remove"])

        assert result.exit_code == 0
        assert os.path.exists(".git/hooks/pre-commit")
        assert "Cancelled" in result.stdout


def test_hook_install_yes_warns_and_skips_a_foreign_hook_instead_of_prompting(
    tmp_path,
):
    """
    Real bug: 'hook install --yes' still hit a second, un-gated
    confirmation when a pre-existing hook wasn't EnvShield's own --
    '--yes' only ever reached the first ("install hooks at all?") prompt.
    With no TTY, that left questionary's confirm with undefined input
    instead of the safe "warn and skip" path these install functions
    already implement for exactly this situation.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _init_git_repo()
        os.makedirs(".git/hooks", exist_ok=True)
        with open(".git/hooks/pre-commit", "w") as f:
            f.write("#!/bin/sh\necho 'not envshield'\n")

        result = runner.invoke(app, ["hook", "install", "--yes"])

        assert result.exit_code == 0
        assert "already exists" in result.stdout
        with open(".git/hooks/pre-commit") as f:
            assert "not envshield" in f.read()  # left completely untouched
        assert os.path.exists(".git/hooks/post-merge")  # unaffected, still installs


def test_hook_install_yes_silently_regenerates_its_own_existing_hook(tmp_path):
    """The foreign-hook skip must not regress the common re-run case: an already-EnvShield hook is always safe to regenerate, no warning needed."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _init_git_repo()
        runner.invoke(app, ["hook", "install", "--yes"])

        result = runner.invoke(app, ["hook", "install", "--yes"])

        assert result.exit_code == 0
        assert "already exists" not in result.stdout
        assert "installed successfully" in result.stdout


def test_hook_install_confirms_with_a_real_tty(mocker, tmp_path):
    """The confirmation gate must not silently disable itself for someone who actually has a terminal."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _init_git_repo()
        mocker.patch("envshield.core.hooks_manager._is_interactive", return_value=True)
        mocker.patch("questionary.confirm").return_value.ask.return_value = True

        result = runner.invoke(app, ["hook", "install"])

        assert result.exit_code == 0
        assert os.path.exists(".git/hooks/pre-commit")


def test_hook_install_cancelled_when_declined_at_a_real_tty(mocker, tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _init_git_repo()
        mocker.patch("envshield.core.hooks_manager._is_interactive", return_value=True)
        mocker.patch("questionary.confirm").return_value.ask.return_value = False

        result = runner.invoke(app, ["hook", "install"])

        assert result.exit_code == 0
        assert not os.path.exists(".git/hooks/pre-commit")
        assert "Cancelled" in result.stdout


def test_service_discover_does_not_abort_on_the_post_registration_hook_offer(tmp_path):
    """
    Regression: 'service discover --yes' (and, by the same code path, 'init'
    and 'setup') used to fully succeed -- register every service, seed every
    schema -- and then exit 1 with a bare 'Aborted.' anyway, because the
    hook-install offer that runs right after has its own confirm prompt with
    no TTY guard. '--yes' only ever covered which services to register, not
    this second, unrelated prompt.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _init_git_repo()
        os.makedirs("api")
        with open("api/.env", "w") as f:
            f.write("KEY=1\n")

        result = runner.invoke(app, ["service", "discover", "--yes"])

        assert result.exit_code == 0, result.stdout
        assert "Added 1 service" in result.stdout
        assert not os.path.exists(".git/hooks/pre-commit")
