# envshield/tests/core/test_source_files.py
import os

import pytest
from typer.testing import CliRunner

from envshield.cli import app
from envshield.core import source_files

runner = CliRunner()


@pytest.mark.parametrize(
    "path",
    [".github/workflows/ci.yml", "./.github/workflows/ci.yml"],
)
def test_root_anchored_pattern_matches_every_spelling_of_the_path(path):
    # BL-128: a walk from "." yields "./..." paths, which a root-anchored
    # pattern never matched, so a full 'scan' ignored the exclusion.
    assert source_files.matches_exclusion(path, ".github/workflows/ci.yml")


def test_absolute_path_is_matched_relative_to_the_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = os.path.join(str(tmp_path), "legacy", "old.js")
    assert source_files.matches_exclusion(path, "legacy/*.js")


@pytest.mark.parametrize("path", ["tests/a.py", "./tests/a.py", "src/tests/a.py"])
def test_double_star_prefix_also_matches_zero_directories(path):
    # A full-tree walk always excluded a top-level tests/ via "**/tests/*";
    # a staged (Git-relative) path must match the same way.
    assert source_files.matches_exclusion(path, "**/tests/*")


def test_dot_slash_pattern_still_matches():
    assert source_files.matches_exclusion("legacy/old.js", "./legacy/*.js")


def test_unrelated_path_is_not_excluded():
    assert not source_files.matches_exclusion("app/ci.yml", ".github/workflows/ci.yml")


def test_full_scan_honours_a_root_anchored_exclusion(tmp_path):
    """BL-128, end to end: the KemonChilo shape (exclude_files names
    '.github/workflows/ci.yml'; a bare 'scan' used to flag it anyway)."""
    with runner.isolated_filesystem(temp_dir=tmp_path):
        with open("envshield.yml", "w") as f:
            f.write(
                "secret_scanning:\n  exclude_files:\n    - '.github/workflows/ci.yml'\n"
            )
        os.makedirs(".github/workflows")
        with open(".github/workflows/ci.yml", "w") as f:
            f.write("env:\n  API_KEY: 'sk_live_123456789abcdefghijklmnopqrstuv'\n")

        result = runner.invoke(app, ["scan"])

        assert result.exit_code == 0, result.stdout
        assert "No issues found" in result.stdout


def test_discoverable_files_skips_symlinks_and_excluded_dirs(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "a.py").write_text("x = 1\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "b.js").write_text("x\n")
    (tmp_path / "link.py").symlink_to(tmp_path / "app" / "a.py")

    files = source_files.discoverable_files(str(tmp_path))

    assert files == [os.path.join(str(tmp_path), "app", "a.py")]
