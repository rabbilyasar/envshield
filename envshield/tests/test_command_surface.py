# envshield/tests/test_command_surface.py
"""V0-2a (#50): duplicate commands are deprecated and hidden, and --help is
grouped by stage. Deprecated commands must keep working exactly as before."""

import os
import subprocess

from typer.testing import CliRunner

from envshield.cli import app

runner = CliRunner()


def _init_git_repo():
    subprocess.run(["git", "init", "-q"], check=True)


def _write_project():
    with open("envshield.yml", "w") as f:
        f.write("services:\n  app:\n    schema: env.schema.toml\n")
    with open("env.schema.toml", "w") as f:
        f.write('[DB_URL]\ndescription = "x"\n')


# --- --help ---


def test_help_hides_the_deprecated_commands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "install-hook" not in result.stdout
    assert "uninstall" not in result.stdout


def test_help_groups_commands_by_stage():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for panel in ("Onboarding", "Development & deployment", "Supporting"):
        assert panel in result.stdout
    # Each stage's front-door commands are listed under it, in story order.
    out = result.stdout
    assert out.index("Onboarding") < out.index("init") < out.index("setup")
    assert out.index("Development & deployment") < out.index("check")


def test_hidden_commands_still_show_their_own_help():
    for command in ("install-hook", "uninstall"):
        result = runner.invoke(app, [command, "--help"])
        assert result.exit_code == 0, command


# --- install-hook ---


def test_install_hook_still_installs_and_names_its_replacement(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _init_git_repo()

        result = runner.invoke(app, ["install-hook", "--yes"])

        assert result.exit_code == 0
        assert os.path.exists(".git/hooks/pre-commit")
        assert os.path.exists(".git/hooks/post-merge")
        assert "deprecated" in result.stderr
        assert "envshield hook install" in result.stderr
        assert "deprecated" not in result.stdout


# --- uninstall ---


def test_uninstall_still_removes_and_names_its_replacement(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _init_git_repo()
        runner.invoke(app, ["hook", "install", "--yes"])

        result = runner.invoke(app, ["uninstall", "--yes"])

        assert result.exit_code == 0
        assert not os.path.exists(".git/hooks/pre-commit")
        assert "deprecated" in result.stderr
        assert "envshield hook remove" in result.stderr
        assert "deprecated" not in result.stdout


# --- hook remove gains uninstall's report ---


def test_hook_remove_reports_removed_hook_paths_and_preserved_files(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _init_git_repo()
        _write_project()
        runner.invoke(app, ["hook", "install", "--yes"])

        result = runner.invoke(app, ["hook", "remove", "--yes"])

        assert result.exit_code == 0
        assert ".git/hooks/pre-commit" in result.stdout
        assert "Preserved:" in result.stdout
        assert "envshield.yml" in result.stdout
        assert "env.schema.toml" in result.stdout
        assert os.path.exists("envshield.yml")
        assert os.path.exists("env.schema.toml")


def test_hook_remove_reports_a_hook_it_left_alone(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _init_git_repo()
        runner.invoke(app, ["hook", "install", "--yes"])
        with open(".git/hooks/pre-commit", "a") as f:
            f.write("\n# local edit\n")

        result = runner.invoke(app, ["hook", "remove", "--yes"])

        assert result.exit_code == 0
        assert os.path.exists(".git/hooks/pre-commit")
        assert "Preserved:" in result.stdout
        assert ".git/hooks/pre-commit" in result.stdout


def test_hook_remove_outside_a_git_repository_is_still_an_error(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        result = runner.invoke(app, ["hook", "remove", "--yes"])

        assert result.exit_code == 1
        assert "Error" in result.stdout
