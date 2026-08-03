# envshield/tests/test_config_manager.py
import pytest

from envshield.config import manager as config_manager
from envshield.core.exceptions import SchemaNotFoundError


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


def test_get_env_paths_defaults_to_cwd_for_single_service_project(tmp_path, monkeypatch):
    """With no service, paths stay exactly '.env.example' / '.env' -- unchanged behaviour."""
    monkeypatch.chdir(tmp_path)

    paths = config_manager.get_env_paths()

    assert paths == {"example_file": ".env.example", "local_file": ".env"}


def test_get_env_paths_scopes_to_service_directory(tmp_path, monkeypatch):
    """
    Regression: 'schema sync'/'setup' used to always read/write '.env.example'
    and '.env' in the current directory regardless of --service, so two
    services in one monorepo would clobber the same root-level file. Each
    service must get its own directory by default.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "services" / "api").mkdir(parents=True)
    with open("envshield.yml", "w") as f:
        f.write(
            "services:\n"
            "  api:\n"
            "    path: services/api/env.schema.toml\n"
        )

    paths = config_manager.get_env_paths(service_name="api")

    assert paths == {
        "example_file": "services/api/.env.example",
        "local_file": "services/api/.env",
    }


def test_get_env_paths_honors_local_file_override(tmp_path, monkeypatch):
    """
    A service can point 'local_file' at a non-dotenv file (e.g. a Python
    config module) -- required for projects whose local config isn't a
    dotenv file at all.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "athena" / "config").mkdir(parents=True)
    with open("envshield.yml", "w") as f:
        f.write(
            "services:\n"
            "  athena:\n"
            "    path: athena/env.schema.toml\n"
            "    local_file: athena/config/env_config.local.py\n"
        )

    paths = config_manager.get_env_paths(service_name="athena")

    assert paths["local_file"] == "athena/config/env_config.local.py"
    # example_file still defaults sensibly even though only local_file was overridden
    assert paths["example_file"] == "athena/.env.example"


def test_get_env_paths_raises_for_unknown_service(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with open("envshield.yml", "w") as f:
        f.write("services:\n  api:\n    path: services/api/env.schema.toml\n")

    with pytest.raises(SchemaNotFoundError):
        config_manager.get_env_paths(service_name="does-not-exist")


def test_add_service_creates_envshield_yml_when_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    config_manager.add_service("athena", "athena/env.schema.toml")

    services = config_manager.get_services()
    assert services == {"athena": {"path": "athena/env.schema.toml"}}


def test_add_service_extends_without_touching_existing_services_or_other_keys(
    tmp_path, monkeypatch
):
    """The whole point of 'extend': adding a new service must never disturb an already-configured one."""
    monkeypatch.chdir(tmp_path)
    with open("envshield.yml", "w") as f:
        f.write(
            "project_name: zeus\n"
            "services:\n"
            "  athena:\n"
            "    path: athena/env.schema.toml\n"
            "    local_file: athena/config/env_config.local.py\n"
        )

    config_manager.add_service("hermes", "hermes/env.schema.toml", local_file="hermes/config/env_config.local.py")

    config = config_manager.load_config()
    assert config["project_name"] == "zeus"
    assert config["services"]["athena"] == {
        "path": "athena/env.schema.toml",
        "local_file": "athena/config/env_config.local.py",
    }
    assert config["services"]["hermes"] == {
        "path": "hermes/env.schema.toml",
        "local_file": "hermes/config/env_config.local.py",
    }


def test_add_service_includes_optional_fields_only_when_given(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    config_manager.add_service(
        "athena",
        "athena/env.schema.toml",
        local_file="athena/config/env_config.local.py",
        example_file="athena/.env.example",
        description="Backend API",
    )

    entry = config_manager.get_services()["athena"]
    assert entry == {
        "path": "athena/env.schema.toml",
        "description": "Backend API",
        "local_file": "athena/config/env_config.local.py",
        "example_file": "athena/.env.example",
    }


def test_add_service_overwrites_a_service_registered_under_the_same_name(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    config_manager.add_service("athena", "athena/env.schema.toml", description="old")

    config_manager.add_service("athena", "athena/env.schema.toml", description="new")

    assert config_manager.get_services()["athena"]["description"] == "new"
