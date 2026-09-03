# envshield/tests/test_paths.py
import os

from envshield.utils.paths import is_within


def test_a_path_inside_root_is_within(tmp_path):
    inside = tmp_path / "sub" / "file.txt"
    inside.parent.mkdir()
    inside.write_text("x")

    assert is_within(str(inside), str(tmp_path)) is True


def test_a_path_outside_root_is_not_within(tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}_outside"
    outside.mkdir(exist_ok=True)

    assert is_within(str(outside), str(tmp_path)) is False


def test_the_root_itself_is_within(tmp_path):
    assert is_within(str(tmp_path), str(tmp_path)) is True


def test_a_symlink_inside_root_pointing_outside_is_not_within(tmp_path):
    """The exact P0-6-class case: a symlink's own lexical location can sit
    inside root while its real target does not -- the check must follow
    the symlink, not stop at its lexical position."""
    outside = tmp_path.parent / f"{tmp_path.name}_outside"
    outside.mkdir(exist_ok=True)
    (outside / "secret.txt").write_text("x")
    link = tmp_path / "link.txt"
    link.symlink_to(outside / "secret.txt")

    assert is_within(str(link), str(tmp_path)) is False


def test_a_symlink_inside_root_pointing_inside_is_within(tmp_path):
    real = tmp_path / "real.txt"
    real.write_text("x")
    link = tmp_path / "link.txt"
    link.symlink_to(real)

    assert is_within(str(link), str(tmp_path)) is True


def test_a_dangling_symlink_under_a_symlinked_parent_still_resolves_correctly(
    tmp_path,
):
    """A not-yet-existing target under a symlinked parent directory is
    still resolved and checked correctly -- realpath resolves as much of
    the path as exists and appends the remainder unresolved, no separate
    existence check needed."""
    outside = tmp_path.parent / f"{tmp_path.name}_outside"
    outside.mkdir(exist_ok=True)
    link_dir = tmp_path / "linked_dir"
    link_dir.symlink_to(outside)

    candidate = os.path.join(str(link_dir), "does-not-exist-yet.txt")

    assert is_within(candidate, str(tmp_path)) is False
