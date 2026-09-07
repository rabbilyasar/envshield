# envshield/tests/test_uninstall_cli.py
"""
Tests for 'envshield uninstall' -- a project-level command that only ever
deletes a Git hook file it can prove, by exact content match, is its own
current generated output. It never deletes or modifies project
configuration (envshield.yml, schemas, .env.example, any registered local
configuration file) -- see the command's own docstring in cli.py.
"""

import os
import subprocess

from typer.testing import CliRunner

from envshield.cli import app

runner = CliRunner()


def _init_git_repo():
    subprocess.run(["git", "init", "-q"], check=True)


# --- Basic ---


def test_uninstall_command_is_registered():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "uninstall" in result.stdout


def test_bare_uninstall_removes_untouched_hooks_at_a_real_tty(mocker, tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _init_git_repo()
        runner.invoke(app, ["hook", "install", "--yes"])
        mocker.patch("envshield.core.hooks_manager._is_interactive", return_value=True)
        mocker.patch("questionary.confirm").return_value.ask.return_value = True

        result = runner.invoke(app, ["uninstall"])

        assert result.exit_code == 0
        assert not os.path.exists(".git/hooks/pre-commit")
        assert not os.path.exists(".git/hooks/post-merge")


def test_uninstall_yes_removes_untouched_hooks_without_prompting(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _init_git_repo()
        runner.invoke(app, ["hook", "install", "--yes"])

        result = runner.invoke(app, ["uninstall", "--yes"])

        assert result.exit_code == 0
        assert not os.path.exists(".git/hooks/pre-commit")
        assert not os.path.exists(".git/hooks/post-merge")
        assert "Removed" in result.stdout
        assert ".git/hooks/pre-commit" in result.stdout
        assert ".git/hooks/post-merge" in result.stdout


# --- Hooks ---


def test_uninstall_removes_a_current_untouched_pre_commit_hook_only(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _init_git_repo()
        os.makedirs(".git/hooks", exist_ok=True)
        runner.invoke(app, ["hook", "install", "--yes"])
        os.remove(".git/hooks/post-merge")

        result = runner.invoke(app, ["uninstall", "--yes"])

        assert result.exit_code == 0
        assert not os.path.exists(".git/hooks/pre-commit")


def test_uninstall_removes_a_current_untouched_post_merge_hook_only(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _init_git_repo()
        runner.invoke(app, ["hook", "install", "--yes"])
        os.remove(".git/hooks/pre-commit")

        result = runner.invoke(app, ["uninstall", "--yes"])

        assert result.exit_code == 0
        assert not os.path.exists(".git/hooks/post-merge")


def test_uninstall_removes_both_hooks_when_both_are_current_and_untouched(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _init_git_repo()
        runner.invoke(app, ["hook", "install", "--yes"])

        result = runner.invoke(app, ["uninstall", "--yes"])

        assert result.exit_code == 0
        assert not os.path.exists(".git/hooks/pre-commit")
        assert not os.path.exists(".git/hooks/post-merge")


def test_uninstall_is_harmless_when_no_hooks_are_installed(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _init_git_repo()

        result = runner.invoke(app, ["uninstall", "--yes"])

        assert result.exit_code == 0
        assert "Nothing to remove" in result.stdout


def test_uninstall_preserves_a_modified_pre_commit_hook(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _init_git_repo()
        runner.invoke(app, ["hook", "install", "--yes"])
        with open(".git/hooks/pre-commit") as f:
            generated = f.read()
        modified = generated + "\necho 'developer added this'\n"
        with open(".git/hooks/pre-commit", "w") as f:
            f.write(modified)

        result = runner.invoke(app, ["uninstall", "--yes"])

        assert result.exit_code == 0
        assert os.path.exists(".git/hooks/pre-commit")
        with open(".git/hooks/pre-commit") as f:
            assert f.read() == modified
        assert "Preserved" in result.stdout
        assert ".git/hooks/pre-commit" in result.stdout
        # the other, untouched hook is still safely removed
        assert not os.path.exists(".git/hooks/post-merge")


def test_uninstall_preserves_a_modified_post_merge_hook(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _init_git_repo()
        runner.invoke(app, ["hook", "install", "--yes"])
        with open(".git/hooks/post-merge") as f:
            generated = f.read()
        modified = generated + "\necho 'developer added this'\n"
        with open(".git/hooks/post-merge", "w") as f:
            f.write(modified)

        result = runner.invoke(app, ["uninstall", "--yes"])

        assert result.exit_code == 0
        assert os.path.exists(".git/hooks/post-merge")
        with open(".git/hooks/post-merge") as f:
            assert f.read() == modified


def test_uninstall_preserves_a_foreign_pre_commit_hook(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _init_git_repo()
        os.makedirs(".git/hooks", exist_ok=True)
        foreign_content = "#!/bin/sh\necho 'not envshield'\n"
        with open(".git/hooks/pre-commit", "w") as f:
            f.write(foreign_content)

        result = runner.invoke(app, ["uninstall", "--yes"])

        assert result.exit_code == 0
        with open(".git/hooks/pre-commit") as f:
            assert f.read() == foreign_content


def test_uninstall_preserves_a_foreign_post_merge_hook(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _init_git_repo()
        os.makedirs(".git/hooks", exist_ok=True)
        foreign_content = "#!/bin/sh\necho 'not envshield'\n"
        with open(".git/hooks/post-merge", "w") as f:
            f.write(foreign_content)

        result = runner.invoke(app, ["uninstall", "--yes"])

        assert result.exit_code == 0
        with open(".git/hooks/post-merge") as f:
            assert f.read() == foreign_content


def test_uninstall_does_not_trust_a_coincidental_marker_in_a_foreign_hook(tmp_path):
    """
    The exact safety invariant this whole feature depends on: a hand-
    written hook that happens to contain the literal
    '# Hook installed by EnvShield' marker comment, but was never actually
    generated by EnvShield, must not be deleted merely because that
    string is present -- only an exact content match proves ownership.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _init_git_repo()
        foreign_content = (
            "#!/bin/sh\n"
            "# Hook installed by EnvShield\n"
            "# (hand-written, not actually generated by EnvShield)\n"
            "echo 'CUSTOM_LOGIC_MARKER'\n"
            "./run_custom_lint.sh || exit 1\n"
        )
        os.makedirs(".git/hooks", exist_ok=True)
        with open(".git/hooks/pre-commit", "w") as f:
            f.write(foreign_content)

        result = runner.invoke(app, ["uninstall", "--yes"])

        assert result.exit_code == 0
        with open(".git/hooks/pre-commit") as f:
            assert f.read() == foreign_content
        assert "Preserved" in result.stdout


def test_uninstall_preserves_a_marker_free_hook_that_happens_to_resemble_envshields(
    tmp_path,
):
    """
    The mirror case of the marker-collision test above: absence of the
    marker must not matter either way -- only exact content match against
    the current generated output decides ownership, in both directions.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _init_git_repo()
        runner.invoke(app, ["hook", "install", "--yes"])
        with open(".git/hooks/pre-commit") as f:
            generated = f.read()
        # Strip the marker line but otherwise keep the content identical --
        # this must still be preserved, since it no longer exactly matches
        # what EnvShield would generate now.
        stripped = generated.replace("# Hook installed by EnvShield", "")
        with open(".git/hooks/pre-commit", "w") as f:
            f.write(stripped)

        result = runner.invoke(app, ["uninstall", "--yes"])

        assert result.exit_code == 0
        with open(".git/hooks/pre-commit") as f:
            assert f.read() == stripped


def test_uninstall_preserves_a_stale_envshield_hook_after_a_service_is_added(tmp_path):
    """
    A hook generated before a service was registered no longer exactly
    matches what EnvShield would generate now that the service exists --
    it must be preserved, not silently deleted or regenerated, exactly as
    'hook remove'/'hook install --yes' already behave for this case.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _init_git_repo()
        runner.invoke(app, ["hook", "install", "--yes"])
        with open(".git/hooks/pre-commit") as f:
            before = f.read()

        os.makedirs("api")
        with open("api/.env", "w") as f:
            f.write("FOO=bar\n")
        add_result = runner.invoke(app, ["service", "add", "api", "api"])
        assert add_result.exit_code == 0

        result = runner.invoke(app, ["uninstall", "--yes"])

        assert result.exit_code == 0
        assert os.path.exists(".git/hooks/pre-commit")
        with open(".git/hooks/pre-commit") as f:
            assert f.read() == before
        assert "Preserved" in result.stdout


def test_uninstall_respects_a_custom_hooks_path(tmp_path):
    """
    Git can be configured with 'core.hooksPath' pointing anywhere (e.g.
    Husky). Both the actual removal and the printed report must use that
    real location -- never a hardcoded '.git/hooks/...' that would be
    wrong here.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _init_git_repo()
        os.makedirs("custom-hooks", exist_ok=True)
        subprocess.run(["git", "config", "core.hooksPath", "custom-hooks"], check=True)
        try:
            runner.invoke(app, ["hook", "install", "--yes"])
            assert os.path.exists("custom-hooks/pre-commit")
            assert os.path.exists("custom-hooks/post-merge")
            assert not os.path.exists(".git/hooks/pre-commit")

            result = runner.invoke(app, ["uninstall", "--yes"])

            assert result.exit_code == 0
            # Actually removed from the real, configured location.
            assert not os.path.exists("custom-hooks/pre-commit")
            assert not os.path.exists("custom-hooks/post-merge")
            # The report names the real location...
            assert "custom-hooks/pre-commit" in result.stdout
            assert "custom-hooks/post-merge" in result.stdout
            # ...and never falsely claims the default location.
            assert ".git/hooks/pre-commit" not in result.stdout
            assert ".git/hooks/post-merge" not in result.stdout
        finally:
            subprocess.run(["git", "config", "--unset", "core.hooksPath"], check=False)


def test_uninstall_yes_does_not_bypass_ownership_checks(tmp_path):
    """
    Critical safety invariant: '--yes' only skips the confirmation
    prompt. It must never force deletion of a hook that fails the
    ownership check.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _init_git_repo()
        os.makedirs(".git/hooks", exist_ok=True)
        foreign_content = "#!/bin/sh\necho 'not envshield'\n"
        with open(".git/hooks/pre-commit", "w") as f:
            f.write(foreign_content)

        result = runner.invoke(app, ["uninstall", "--yes"])

        assert result.exit_code == 0
        with open(".git/hooks/pre-commit") as f:
            assert f.read() == foreign_content


# --- Configuration preservation ---


def test_uninstall_never_deletes_envshield_yml(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _init_git_repo()
        with open("envshield.yml", "w") as f:
            f.write("services:\n  demo:\n    schema: env.schema.toml\n")
        with open("env.schema.toml", "w") as f:
            f.write("[FOO]\ndescription = 'x'\n")
        runner.invoke(app, ["hook", "install", "--yes"])

        result = runner.invoke(app, ["uninstall", "--yes"])

        assert result.exit_code == 0
        assert os.path.exists("envshield.yml")
        assert "envshield.yml" in result.stdout
        assert "Preserved" in result.stdout


def test_uninstall_never_deletes_schema_files(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _init_git_repo()
        with open("envshield.yml", "w") as f:
            f.write("services:\n  demo:\n    schema: env.schema.toml\n")
        with open("env.schema.toml", "w") as f:
            f.write("[FOO]\ndescription = 'x'\n")

        result = runner.invoke(app, ["uninstall", "--yes"])

        assert result.exit_code == 0
        assert os.path.exists("env.schema.toml")
        assert "env.schema.toml" in result.stdout


def test_uninstall_preserves_every_schema_in_a_multi_service_project(tmp_path):
    """
    Modeled on Zeus's own real shape (athena + hermes, each with its own
    schema): a multi-service project must have EVERY registered service's
    schema reported as preserved, independently -- not just the first
    one, and not collapsed into a single combined entry.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _init_git_repo()
        os.makedirs("athena", exist_ok=True)
        os.makedirs("hermes", exist_ok=True)
        with open("envshield.yml", "w") as f:
            f.write(
                "services:\n"
                "  athena:\n"
                "    schema: athena/env.schema.toml\n"
                "  hermes:\n"
                "    schema: hermes/env.schema.toml\n"
            )
        with open("athena/env.schema.toml", "w") as f:
            f.write("[ATHENA_VAR]\ndescription = 'x'\n")
        with open("hermes/env.schema.toml", "w") as f:
            f.write("[HERMES_VAR]\ndescription = 'x'\n")
        runner.invoke(app, ["hook", "install", "--yes"])

        result = runner.invoke(app, ["uninstall", "--yes"])

        assert result.exit_code == 0
        # Neither schema was deleted...
        assert os.path.exists("athena/env.schema.toml")
        assert os.path.exists("hermes/env.schema.toml")
        # ...and both are reported, independently -- not just one, and
        # not merged into a single line.
        assert result.stdout.count("athena/env.schema.toml") == 1
        assert result.stdout.count("hermes/env.schema.toml") == 1
        # The safely-owned hooks are still removed regardless.
        assert not os.path.exists(".git/hooks/pre-commit")
        assert not os.path.exists(".git/hooks/post-merge")


def test_uninstall_never_deletes_env_example(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _init_git_repo()
        with open("envshield.yml", "w") as f:
            f.write("services:\n  demo:\n    schema: env.schema.toml\n")
        with open("env.schema.toml", "w") as f:
            f.write("[FOO]\ndescription = 'x'\n")
        with open(".env.example", "w") as f:
            f.write("# DO NOT EDIT THIS FILE MANUALLY.\n\nFOO=\n")

        result = runner.invoke(app, ["uninstall", "--yes"])

        assert result.exit_code == 0
        assert os.path.exists(".env.example")
        assert ".env.example" in result.stdout


def test_uninstall_never_deletes_registered_local_configuration(tmp_path):
    """
    Directly modeled on Zeus's own registered local_file
    (athena/config/env_config.local.py) -- a real application
    configuration file that may hold real secret values, never
    EnvShield's own integration artifact to reclaim.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _init_git_repo()
        with open("envshield.yml", "w") as f:
            f.write(
                "services:\n"
                "  demo:\n"
                "    schema: env.schema.toml\n"
                "    local_file: env_config.local.py\n"
            )
        with open("env.schema.toml", "w") as f:
            f.write("[FOO]\ndescription = 'x'\n")
        with open("env_config.local.py", "w") as f:
            f.write("FOO = 'a-real-local-value'\n")

        result = runner.invoke(app, ["uninstall", "--yes"])

        assert result.exit_code == 0
        assert os.path.exists("env_config.local.py")
        with open("env_config.local.py") as f:
            assert f.read() == "FOO = 'a-real-local-value'\n"
        assert "env_config.local.py" in result.stdout


def test_uninstall_never_modifies_gitignore(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _init_git_repo()
        with open(".gitignore", "w") as f:
            f.write("node_modules/\n")
        runner.invoke(app, ["hook", "install", "--yes"])

        result = runner.invoke(app, ["uninstall", "--yes"])

        assert result.exit_code == 0
        with open(".gitignore") as f:
            assert f.read() == "node_modules/\n"


def test_uninstall_never_deletes_generate_output(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _init_git_repo()
        with open("config.py", "w") as f:
            f.write("# hand-written application code\nDATABASE_URL = 'sqlite://'\n")
        runner.invoke(app, ["hook", "install", "--yes"])

        result = runner.invoke(app, ["uninstall", "--yes"])

        assert result.exit_code == 0
        assert os.path.exists("config.py")
        with open("config.py") as f:
            assert "DATABASE_URL" in f.read()


# --- Environment ---


def test_uninstall_handles_no_git_repository_safely(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        with open("envshield.yml", "w") as f:
            f.write("services:\n  demo:\n    schema: env.schema.toml\n")
        with open("env.schema.toml", "w") as f:
            f.write("[FOO]\ndescription = 'x'\n")

        result = runner.invoke(app, ["uninstall", "--yes"])

        assert result.exit_code == 0
        assert "Nothing to remove" in result.stdout
        assert os.path.exists("envshield.yml")
        assert "envshield.yml" in result.stdout


def test_uninstall_surfaces_a_genuine_filesystem_error(tmp_path):
    """
    A real operational failure (e.g. no permission to delete the hook
    file) must propagate as an error, not be silently swallowed or
    misreported as a successful removal.
    """
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _init_git_repo()
        runner.invoke(app, ["hook", "install", "--yes"])
        os.chmod(".git/hooks", 0o555)
        try:
            result = runner.invoke(app, ["uninstall", "--yes"])
            assert result.exit_code != 0
        finally:
            os.chmod(".git/hooks", 0o755)


def test_uninstall_declines_without_a_tty_and_without_yes(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _init_git_repo()
        runner.invoke(app, ["hook", "install", "--yes"])

        result = runner.invoke(app, ["uninstall"])

        assert result.exit_code == 0
        assert os.path.exists(".git/hooks/pre-commit")
        assert os.path.exists(".git/hooks/post-merge")
        assert "Cancelled" in result.stdout


# --- Interaction ---


def test_uninstall_cancelled_when_declined_at_a_real_tty(mocker, tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _init_git_repo()
        runner.invoke(app, ["hook", "install", "--yes"])
        mocker.patch("envshield.core.hooks_manager._is_interactive", return_value=True)
        mocker.patch("questionary.confirm").return_value.ask.return_value = False

        result = runner.invoke(app, ["uninstall"])

        assert result.exit_code == 0
        assert os.path.exists(".git/hooks/pre-commit")
        assert os.path.exists(".git/hooks/post-merge")
        assert "Cancelled" in result.stdout


def test_uninstall_confirmed_at_a_real_tty_removes_safely_owned_hooks(mocker, tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _init_git_repo()
        runner.invoke(app, ["hook", "install", "--yes"])
        mocker.patch("envshield.core.hooks_manager._is_interactive", return_value=True)
        mocker.patch("questionary.confirm").return_value.ask.return_value = True

        result = runner.invoke(app, ["uninstall"])

        assert result.exit_code == 0
        assert not os.path.exists(".git/hooks/pre-commit")
        assert not os.path.exists(".git/hooks/post-merge")
