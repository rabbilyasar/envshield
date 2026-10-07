# envshield/tests/core/test_file_updater.py
import ast
import os
import stat

import pytest

from envshield.core import file_updater
from envshield.core.exceptions import EnvShieldException, UnsafeWriteTargetError


@pytest.fixture(autouse=True)
def _project_root(tmp_path, monkeypatch):
    # Writers only write inside the project directory (the cwd, BL-154).
    (tmp_path / "project").mkdir()
    monkeypatch.chdir(tmp_path)


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

    def test_an_existing_file_is_left_0600(self, tmp_path):
        """
        A secrets file ends up 0600 whether or not it already existed (set
        on the open descriptor). Every caller already tightened an existing
        file to 0600 right after writing; that's now part of the primitive.
        """
        target = tmp_path / "secrets.env"
        target.write_text("OLD=value\n")
        os.chmod(target, 0o640)

        with file_updater.open_new_secret_file(str(target)) as f:
            f.write("NEW=value\n")

        assert _mode(target) == 0o600
        assert target.read_text() == "NEW=value\n"

    def test_a_symlink_to_an_existing_target_is_refused(self, tmp_path):
        """
        BL-154. This test used to document that the helper followed the
        symlink and wrote the outside target; that was the vulnerability.
        """
        outside_dir = tmp_path.parent / f"{tmp_path.name}-outside"
        outside_dir.mkdir()
        target = outside_dir / "existing_target.env"
        target.write_text("OLD_CONTENT\n")
        os.chmod(target, 0o644)
        link = tmp_path / "link_to_existing"
        os.symlink(target, link)

        with pytest.raises(UnsafeWriteTargetError):
            with file_updater.open_new_secret_file(str(link)) as f:
                f.write("SECRET_DO_NOT_LEAK\n")

        assert target.read_text() == "OLD_CONTENT\n"
        assert _mode(target) == 0o644

    def test_a_dangling_symlink_is_refused_and_creates_nothing(self, tmp_path):
        outside_dir = tmp_path.parent / f"{tmp_path.name}-outside-dangling"
        outside_dir.mkdir()
        dangling_target = outside_dir / "does_not_exist_yet.env"
        link = tmp_path / "link_to_dangling"
        os.symlink(dangling_target, link)

        with pytest.raises(UnsafeWriteTargetError):
            with file_updater.open_new_secret_file(str(link)) as f:
                f.write("SECRET_DO_NOT_LEAK\n")

        assert not dangling_target.exists()


# --- Truncation waits for the permission change (BL-154 regression) ---------


def _eperm(fd, mode):
    raise PermissionError(1, "Operation not permitted")


@pytest.fixture
def opened_fds(monkeypatch):
    """Every descriptor os.open hands out during the test."""
    fds = []
    real_open = os.open

    def recording_open(*args, **kwargs):
        fd = real_open(*args, **kwargs)
        fds.append(fd)
        return fd

    monkeypatch.setattr(os, "open", recording_open)
    return fds


def _assert_all_closed(fds):
    assert fds
    for fd in fds:
        with pytest.raises(OSError):
            os.fstat(fd)


class TestPermissionFailureKeepsTheExistingFile:
    def test_secret_open_leaves_content_intact_and_closes_descriptors(
        self, monkeypatch, opened_fds
    ):
        with open(".env", "w") as f:
            f.write("API_KEY=keep-me\n")
        monkeypatch.setattr(os, "fchmod", _eperm)

        with pytest.raises(PermissionError):
            file_updater.open_new_secret_file(".env")

        with open(".env", "rb") as f:
            assert f.read() == b"API_KEY=keep-me\n"
        _assert_all_closed(opened_fds)

    def test_update_raises_instead_of_reporting_success(self, monkeypatch):
        with open("settings.py", "w") as f:
            f.write("# keep me\nPORT = '8000'\n")
        monkeypatch.setattr(os, "fchmod", _eperm)

        with pytest.raises(
            EnvShieldException, match="Could not write to 'settings.py'"
        ):
            file_updater.update_variables_in_file(
                "settings.py", [{"key": "API_KEY", "value": "x"}], secret=True
            )

        with open("settings.py", "rb") as f:
            assert f.read() == b"# keep me\nPORT = '8000'\n"


def test_update_raises_when_the_file_cannot_be_read(monkeypatch):
    with open("settings.py", "w") as f:
        f.write("# keep me\nPORT = '8000'\n")

    def unreadable(*args, **kwargs):
        raise PermissionError(13, "Permission denied")

    # Only file_updater's own open() (its read); nothing else is affected.
    monkeypatch.setattr(file_updater, "open", unreadable, raising=False)

    with pytest.raises(EnvShieldException, match="Could not read 'settings.py'"):
        file_updater.update_variables_in_file(
            "settings.py", [{"key": "API_KEY", "value": "x"}], secret=True
        )

    with open("settings.py", "rb") as f:
        assert f.read() == b"# keep me\nPORT = '8000'\n"


class TestWithoutFchmod:
    """Windows before Python 3.13 has no os.fchmod (simulated here)."""

    def test_secret_write_to_an_existing_file_succeeds(self, monkeypatch):
        with open(".env", "w") as f:
            f.write("OLD=1\n")
        monkeypatch.delattr(os, "fchmod")

        with file_updater.open_new_secret_file(".env") as f:
            f.write("NEW=2\n")

        with open(".env") as f:
            assert f.read() == "NEW=2\n"

    def test_a_new_secret_file_is_still_created_0600(self, monkeypatch):
        monkeypatch.delattr(os, "fchmod")

        with file_updater.open_new_secret_file("fresh.env") as f:
            f.write("NEW=2\n")

        assert _mode("fresh.env") == 0o600


class TestTruncationAndAppend:
    def test_shorter_content_fully_replaces_a_longer_file(self):
        with open("notes.txt", "w") as f:
            f.write("a much longer original line\n")

        with file_updater.open_for_write("notes.txt") as f:
            f.write("short\n")

        with open("notes.txt") as f:
            assert f.read() == "short\n"

    def test_append_keeps_existing_content(self):
        with open("notes.txt", "w") as f:
            f.write("first\n")

        with file_updater.open_for_write("notes.txt", append=True) as f:
            f.write("second\n")

        with open("notes.txt") as f:
            assert f.read() == "first\nsecond\n"
