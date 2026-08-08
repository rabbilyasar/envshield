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

        result = runner.invoke(app, ["hook", "install"])

        assert result.exit_code == 0
        assert os.path.exists(".git/hooks/pre-commit")
        assert os.path.exists(".git/hooks/post-merge")


def test_hook_status_reports_installed_hooks(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _init_git_repo()
        runner.invoke(app, ["hook", "install"])

        result = runner.invoke(app, ["hook", "status"])

        assert result.exit_code == 0
        assert "pre-commit hook" in result.stdout
        assert "post-merge hook" in result.stdout


def test_hook_remove_deletes_envshield_installed_hooks(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _init_git_repo()
        runner.invoke(app, ["hook", "install"])

        result = runner.invoke(app, ["hook", "remove"])

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

        result = runner.invoke(app, ["hook", "remove"])

        assert result.exit_code == 0
        assert os.path.exists(".git/hooks/pre-commit")
        assert "No EnvShield-installed hooks" in result.stdout


def test_hook_remove_outside_git_repo_errors_clearly(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        result = runner.invoke(app, ["hook", "remove"])

        assert result.exit_code == 1
        assert "Not inside a Git repository" in result.stdout


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
