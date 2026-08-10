# envshield/tests/core/test_file_updater.py
import ast
import os
import stat

import pytest

from envshield.core import file_updater
from envshield.core.exceptions import EnvShieldException


def test_updates_existing_key_in_place_and_appends_missing_ones(tmp_path):
    target = tmp_path / ".env"
    target.write_text("FOO=old\nUNRELATED=keep\n")

    file_updater.update_variables_in_file(
        str(target), [{"key": "FOO", "value": "new"}, {"key": "BAR", "value": "added"}]
    )

    content = target.read_text()
    assert "FOO=new\n" in content
    assert "UNRELATED=keep\n" in content
    assert "BAR=added\n" in content


def test_quotes_dotenv_value_containing_whitespace(tmp_path):
    target = tmp_path / ".env"
    target.write_text("FOO=old\n")

    file_updater.update_variables_in_file(
        str(target), [{"key": "FOO", "value": "has space"}]
    )

    assert target.read_text() == 'FOO="has space"\n'


def test_escapes_embedded_newline_instead_of_injecting_a_line(tmp_path):
    """
    Regression: a literal newline in a value used to be written verbatim,
    splitting the single KEY=VALUE assignment into extra physical lines --
    potentially injecting an unintended new assignment into the file.
    """
    target = tmp_path / ".env"
    target.write_text("FOO=old\n")

    file_updater.update_variables_in_file(
        str(target), [{"key": "FOO", "value": "line1\nEVIL_KEY=injected"}]
    )

    content = target.read_text()
    # Exactly one physical line for FOO's assignment -- the newline is
    # escaped as literal backslash-n text, not a real line break.
    assert content.count("\n") == 1
    assert "EVIL_KEY" not in content.split("=", 1)[0]
    assert "line1\\nEVIL_KEY=injected" in content


def test_updates_python_file_key_with_repr_escaping(tmp_path):
    target = tmp_path / "config.py"
    target.write_text("FOO = 'old'\n")

    file_updater.update_variables_in_file(
        str(target), [{"key": "FOO", "value": "has 'quotes' and \"both\""}]
    )

    content = target.read_text()
    assert content.startswith("FOO = ")
    # repr() round-trips correctly through Python's own literal syntax.
    rhs = content.split("=", 1)[1].strip()
    assert ast.literal_eval(rhs) == "has 'quotes' and \"both\""


def test_rejects_an_unsafe_key_in_a_python_file_and_leaves_it_untouched(tmp_path):
    """
    Regression coverage for P0-5: 'key' is about to become a bare Python
    assignment target -- unlike 'value', it can't be escaped into a safe
    form without changing its identity, so an unsafe one must be rejected
    outright, before the file is ever opened for writing.
    """
    target = tmp_path / "config.py"
    original = "FOO = 'old'\n"
    target.write_text(original)

    malicious_key = "FOO = 1\nimport os; os.system('true')  #"
    with pytest.raises(EnvShieldException):
        file_updater.update_variables_in_file(
            str(target), [{"key": malicious_key, "value": "x"}]
        )

    assert target.read_text() == original


def test_rejects_an_unsafe_key_in_a_dotenv_file_and_leaves_it_untouched(tmp_path):
    target = tmp_path / ".env"
    original = "FOO=old\n"
    target.write_text(original)

    malicious_key = "FOO\nENVSHIELD_P0_5_SENTINEL"
    with pytest.raises(EnvShieldException):
        file_updater.update_variables_in_file(
            str(target), [{"key": malicious_key, "value": "x"}]
        )

    assert target.read_text() == original


def _mode(path) -> int:
    """The permission bits only, ignoring the file-type bits stat.st_mode also carries."""
    return stat.S_IMODE(os.stat(path).st_mode)


class TestOpenNewSecretFileGuaranteesRestrictivePermissions:
    """
    Regression coverage for P1-1: a freshly-created secret-bearing file
    (setup's local .env / local_file module) used to inherit whatever the
    process umask produced, landing world-readable on a permissive-umask
    or shared host. open_new_secret_file must guarantee 0600 regardless of
    umask, and must never touch the permissions of a file that already
    exists there.
    """

    def test_new_file_gets_0600(self, tmp_path):
        target = tmp_path / "secrets.env"

        with file_updater.open_new_secret_file(str(target)) as f:
            f.write("API_KEY=abc123\n")

        assert _mode(target) == 0o600

    def test_restrictive_umask_does_not_break_creation_or_content(self, tmp_path):
        target = tmp_path / "secrets.env"
        old_umask = os.umask(0o077)
        try:
            with file_updater.open_new_secret_file(str(target)) as f:
                f.write("API_KEY=abc123\n")
        finally:
            os.umask(old_umask)

        assert _mode(target) == 0o600
        assert target.read_text() == "API_KEY=abc123\n"

    def test_permissive_umask_cannot_widen_a_new_secret_file_beyond_0600(
        self, tmp_path
    ):
        """The exact failure this fix closes: umask(0) previously meant a
        freshly-created file landed as 0666, world-readable and
        world-writable."""
        target = tmp_path / "secrets.env"
        old_umask = os.umask(0o000)
        try:
            with file_updater.open_new_secret_file(str(target)) as f:
                f.write("API_KEY=abc123\n")
        finally:
            os.umask(old_umask)

        assert _mode(target) == 0o600

    def test_existing_files_permissions_are_never_touched(self, tmp_path):
        """
        Overwriting an existing secret file must not silently change
        permissions a developer (or another tool) deliberately set --
        POSIX's open()+O_CREAT already guarantees this: the mode argument
        only applies at true creation.
        """
        target = tmp_path / "secrets.env"
        target.write_text("OLD=value\n")
        os.chmod(target, 0o640)

        with file_updater.open_new_secret_file(str(target)) as f:
            f.write("NEW=value\n")

        assert _mode(target) == 0o640
        assert target.read_text() == "NEW=value\n"

    def test_a_symlink_to_an_existing_target_is_followed_not_rejected(self, tmp_path):
        """
        Documents the architectural contract, not a security boundary:
        open_new_secret_file() does no project-containment check of its
        own and follows ordinary OS symlink semantics -- it writes through
        the symlink to its existing target, and (per the "never touch an
        existing file's permissions" contract above) leaves that target's
        own, already-established permissions exactly as they were. This is
        NOT a rejection -- a caller that needs to keep a repository-
        controlled path from escaping the project is expected to validate
        it (e.g. via config.manager._ensure_within_project) *before*
        calling this helper, not rely on this helper to do it.
        """
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()
        target = outside_dir / "existing_target.env"
        target.write_text("OLD_CONTENT\n")
        os.chmod(target, 0o644)
        link = tmp_path / "link_to_existing"
        os.symlink(target, link)

        with file_updater.open_new_secret_file(str(link)) as f:
            f.write("NEW_CONTENT\n")

        assert target.read_text() == "NEW_CONTENT\n"
        assert _mode(target) == 0o644

    def test_a_dangling_symlink_materializes_a_new_file_at_its_target(self, tmp_path):
        """
        Same contract as above, for the "target doesn't exist yet" case:
        O_CREAT fires for real at the symlink's target location, which can
        be anywhere the symlink points -- including outside whatever a
        caller considers "the project". open_new_secret_file() has no way
        to know that boundary and isn't meant to; that's exactly why
        callers must validate the path first.
        """
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()
        dangling_target = outside_dir / "does_not_exist_yet.env"
        link = tmp_path / "link_to_dangling"
        os.symlink(dangling_target, link)
        assert not dangling_target.exists()

        with file_updater.open_new_secret_file(str(link)) as f:
            f.write("MATERIALIZED_OUTSIDE\n")

        assert dangling_target.exists()
        assert dangling_target.read_text() == "MATERIALIZED_OUTSIDE\n"
        assert _mode(dangling_target) == 0o600
