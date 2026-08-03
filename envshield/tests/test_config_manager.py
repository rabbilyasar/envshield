# envshield/tests/test_config_manager.py
from envshield.config import manager as config_manager


def test_update_gitignore_creates_file_with_env_pattern(tmp_path, monkeypatch):
    """A fresh project must get '.env' ignored, not just the '.local' variants."""
    monkeypatch.chdir(tmp_path)
    config_manager.update_gitignore()

    with open(".gitignore", "r") as f:
        lines = {line.strip() for line in f.read().splitlines()}

    assert ".env" in lines
    assert ".env.local" in lines
    assert ".env.*.local" in lines
    assert ".envshield/" in lines


def test_update_gitignore_adds_missing_pattern_to_existing_file(tmp_path, monkeypatch):
    """
    Regression: a project that already has some EnvShield patterns (e.g. from
    an older EnvShield version, before '.env' was added to the list) must
    still get '.env' appended -- not have the whole update skipped just
    because *some* pattern already matched.
    """
    monkeypatch.chdir(tmp_path)
    with open(".gitignore", "w") as f:
        f.write("*.pyc\n.env.local\n.envshield/\n")

    config_manager.update_gitignore()

    with open(".gitignore", "r") as f:
        lines = {line.strip() for line in f.read().splitlines()}

    assert ".env" in lines


def test_update_gitignore_skips_when_all_patterns_already_present(
    tmp_path, monkeypatch
):
    """No duplicate lines are written if every pattern is already ignored."""
    monkeypatch.chdir(tmp_path)
    with open(".gitignore", "w") as f:
        f.write(".env\n.env.local\n.env.*.local\n.envshield/\n")

    config_manager.update_gitignore()

    with open(".gitignore", "r") as f:
        content = f.read()

    assert content.count(".env\n") == 1
