# envshield/tests/test_git_utils.py
import os
import subprocess

from envshield.utils import git_utils


def _init_repo(path):
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=path, check=True
    )
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
    subprocess.run(
        ["git", "config", "core.hooksPath", ".husky"], cwd=tmp_path, check=True
    )

    hooks_dir = git_utils.get_hooks_dir()

    assert hooks_dir == os.path.join(str(tmp_path), ".husky")


def test_get_hooks_dir_honors_absolute_core_hooks_path(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    hooks_dir_abs = tmp_path / "custom-hooks"
    hooks_dir_abs.mkdir()
    subprocess.run(
        ["git", "config", "core.hooksPath", str(hooks_dir_abs)],
        cwd=tmp_path,
        check=True,
    )

    hooks_dir = git_utils.get_hooks_dir()

    assert hooks_dir == str(hooks_dir_abs)


def test_install_pre_commit_hook_writes_into_configured_hooks_path(
    tmp_path, monkeypatch
):
    from envshield.core import scanner

    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".husky").mkdir()
    subprocess.run(
        ["git", "config", "core.hooksPath", ".husky"], cwd=tmp_path, check=True
    )

    scanner.install_pre_commit_hook(force=True)

    assert os.path.exists(tmp_path / ".husky" / "pre-commit")
    assert not os.path.exists(tmp_path / ".git" / "hooks" / "pre-commit")


def _commit(path, message):
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=path, check=True)


class TestGetFileContentAtRevision:
    """Regression coverage for Phase 2A: generalizes get_head_file_content's
    'git show <ref>:<path>' pattern to an arbitrary caller-supplied revision."""

    def test_reads_content_at_an_older_revision(self, tmp_path, monkeypatch):
        _init_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        (tmp_path / "file.txt").write_text("v1\n")
        _commit(tmp_path, "v1")
        (tmp_path / "file.txt").write_text("v2\n")
        _commit(tmp_path, "v2")

        content = git_utils.get_file_content_at_revision(
            str(tmp_path / "file.txt"), "HEAD~1"
        )

        assert content == "v1\n"

    def test_reads_content_at_head(self, tmp_path, monkeypatch):
        _init_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        (tmp_path / "file.txt").write_text("current\n")
        _commit(tmp_path, "current")

        content = git_utils.get_file_content_at_revision(
            str(tmp_path / "file.txt"), "HEAD"
        )

        assert content == "current\n"

    def test_returns_none_for_a_path_that_does_not_exist_at_that_revision(
        self, tmp_path, monkeypatch
    ):
        _init_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        (tmp_path / "a.txt").write_text("a\n")
        _commit(tmp_path, "only a")

        content = git_utils.get_file_content_at_revision(
            str(tmp_path / "b.txt"), "HEAD"
        )

        assert content is None

    def test_returns_none_for_an_unresolvable_revision(self, tmp_path, monkeypatch):
        _init_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        (tmp_path / "a.txt").write_text("a\n")
        _commit(tmp_path, "only a")

        content = git_utils.get_file_content_at_revision(
            str(tmp_path / "a.txt"), "not-a-real-revision"
        )

        assert content is None

    def test_returns_none_outside_a_git_repository(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "a.txt").write_text("a\n")

        content = git_utils.get_file_content_at_revision(
            str(tmp_path / "a.txt"), "HEAD"
        )

        assert content is None


class TestRevisionExists:
    def test_head_exists(self, tmp_path, monkeypatch):
        _init_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        (tmp_path / "a.txt").write_text("a\n")
        _commit(tmp_path, "v1")

        assert git_utils.revision_exists("HEAD") is True

    def test_an_older_revision_exists(self, tmp_path, monkeypatch):
        _init_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        (tmp_path / "a.txt").write_text("a\n")
        _commit(tmp_path, "v1")
        (tmp_path / "a.txt").write_text("b\n")
        _commit(tmp_path, "v2")

        assert git_utils.revision_exists("HEAD~1") is True

    def test_a_bogus_revision_does_not_exist(self, tmp_path, monkeypatch):
        _init_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        (tmp_path / "a.txt").write_text("a\n")
        _commit(tmp_path, "v1")

        assert git_utils.revision_exists("not-a-real-revision") is False

    def test_returns_false_outside_a_git_repository(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        assert git_utils.revision_exists("HEAD") is False


class TestListChangedFiles:
    """Phase 2C: the changed-file primitive dependency_snapshot.py builds on."""

    def test_modified_tracked_file_is_reported(self, tmp_path, monkeypatch):
        _init_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        (tmp_path / "app.py").write_text("x = 1\n")
        _commit(tmp_path, "v1")
        (tmp_path / "app.py").write_text("x = 2\n")

        changed = git_utils.list_changed_files("HEAD", None)

        assert str(tmp_path / "app.py") in changed

    def test_staged_new_file_is_reported_against_head(self, tmp_path, monkeypatch):
        _init_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        (tmp_path / "a.py").write_text("a = 1\n")
        _commit(tmp_path, "v1")
        (tmp_path / "new.py").write_text("b = 2\n")
        subprocess.run(["git", "add", "new.py"], cwd=tmp_path, check=True)

        changed = git_utils.list_changed_files("HEAD", None)

        assert str(tmp_path / "new.py") in changed

    def test_untracked_file_is_not_reported_by_diff_alone(self, tmp_path, monkeypatch):
        _init_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        (tmp_path / "a.py").write_text("a = 1\n")
        _commit(tmp_path, "v1")
        (tmp_path / "untracked.py").write_text("c = 3\n")

        changed = git_utils.list_changed_files("HEAD", None)

        assert str(tmp_path / "untracked.py") not in changed

    def test_deleted_file_is_reported(self, tmp_path, monkeypatch):
        _init_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        (tmp_path / "gone.py").write_text("a = 1\n")
        _commit(tmp_path, "v1")
        (tmp_path / "gone.py").unlink()

        changed = git_utils.list_changed_files("HEAD", None)

        assert str(tmp_path / "gone.py") in changed

    def test_two_explicit_revisions_are_compared(self, tmp_path, monkeypatch):
        _init_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        (tmp_path / "a.py").write_text("a = 1\n")
        _commit(tmp_path, "v1")
        (tmp_path / "a.py").write_text("a = 2\n")
        _commit(tmp_path, "v2")

        changed = git_utils.list_changed_files("HEAD~1", "HEAD")

        assert str(tmp_path / "a.py") in changed

    def test_rename_is_reported_as_remove_and_add_not_a_rename(
        self, tmp_path, monkeypatch
    ):
        """
        Regression: without an explicit '--no-renames', rename detection
        depends on the user's 'diff.renames' git config, making the
        changed-file list non-deterministic across environments. Pinning
        '--no-renames' means a rename always shows as its old path
        removed and its new path added -- a disclosed limitation, not a
        flaky one.
        """
        _init_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        (tmp_path / "old_name.py").write_text("x = os.environ.get('FOO')\n" * 5)
        _commit(tmp_path, "v1")
        subprocess.run(
            ["git", "mv", "old_name.py", "new_name.py"], cwd=tmp_path, check=True
        )
        _commit(tmp_path, "rename")

        changed = git_utils.list_changed_files("HEAD~1", "HEAD")

        assert str(tmp_path / "new_name.py") in changed
        assert str(tmp_path / "old_name.py") in changed

    def test_both_revisions_none_returns_empty_without_shelling_out(
        self, tmp_path, monkeypatch
    ):
        _init_repo(tmp_path)
        monkeypatch.chdir(tmp_path)

        assert git_utils.list_changed_files(None, None) == []

    def test_returns_empty_outside_a_git_repository(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        assert git_utils.list_changed_files("HEAD", None) == []

    def test_unresolvable_revision_returns_empty(self, tmp_path, monkeypatch):
        _init_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        (tmp_path / "a.py").write_text("a = 1\n")
        _commit(tmp_path, "v1")

        assert git_utils.list_changed_files("not-a-real-revision", None) == []


class TestListUntrackedFiles:
    def test_untracked_file_is_reported(self, tmp_path, monkeypatch):
        _init_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        (tmp_path / "a.py").write_text("a = 1\n")
        _commit(tmp_path, "v1")
        (tmp_path / "untracked.py").write_text("b = 2\n")

        untracked = git_utils.list_untracked_files()

        assert str(tmp_path / "untracked.py") in untracked

    def test_tracked_file_is_not_reported(self, tmp_path, monkeypatch):
        _init_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        (tmp_path / "a.py").write_text("a = 1\n")
        _commit(tmp_path, "v1")

        untracked = git_utils.list_untracked_files()

        assert str(tmp_path / "a.py") not in untracked

    def test_gitignored_file_is_not_reported(self, tmp_path, monkeypatch):
        _init_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        (tmp_path / "a.py").write_text("a = 1\n")
        (tmp_path / ".gitignore").write_text("ignored.py\n")
        _commit(tmp_path, "v1")
        (tmp_path / "ignored.py").write_text("c = 3\n")

        untracked = git_utils.list_untracked_files()

        assert str(tmp_path / "ignored.py") not in untracked

    def test_returns_empty_outside_a_git_repository(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        assert git_utils.list_untracked_files() == []
