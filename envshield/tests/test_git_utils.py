# envshield/tests/test_git_utils.py
import os
import subprocess

from envshield.utils import git_utils


def _init_repo(path):
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)


def test_get_hooks_dir_defaults_to_dot_git_hooks(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)

    hooks_dir = git_utils.get_hooks_dir()

    assert hooks_dir == os.path.join(str(tmp_path), ".git", "hooks")


def test_get_hooks_dir_honors_core_hooks_path(tmp_path, monkeypatch):
    """
    Regression: hook install/checks used to hardcode '.git/hooks' unconditionally,
    ignoring a configured 'core.hooksPath' (as set by Husky and similar tools) --
    silently installing a hook Git never actually runs, and letting 'doctor'
    falsely report it as active.
    """
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".husky").mkdir()
    subprocess.run(["git", "config", "core.hooksPath", ".husky"], cwd=tmp_path, check=True)

    hooks_dir = git_utils.get_hooks_dir()

    assert hooks_dir == os.path.join(str(tmp_path), ".husky")


def test_get_hooks_dir_honors_absolute_core_hooks_path(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    hooks_dir_abs = tmp_path / "custom-hooks"
    hooks_dir_abs.mkdir()
    subprocess.run(
        ["git", "config", "core.hooksPath", str(hooks_dir_abs)], cwd=tmp_path, check=True
    )

    hooks_dir = git_utils.get_hooks_dir()

    assert hooks_dir == str(hooks_dir_abs)


def test_install_pre_commit_hook_writes_into_configured_hooks_path(tmp_path, monkeypatch):
    from envshield.core import scanner

    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".husky").mkdir()
    subprocess.run(["git", "config", "core.hooksPath", ".husky"], cwd=tmp_path, check=True)

    scanner.install_pre_commit_hook(force=True)

    assert os.path.exists(tmp_path / ".husky" / "pre-commit")
    assert not os.path.exists(tmp_path / ".git" / "hooks" / "pre-commit")
