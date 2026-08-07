# envshield/tests/test_config_manager.py
import os

import pytest

from envshield.config import manager as config_manager
from envshield.core.exceptions import (
    ConfigParseError,
    SchemaNotFoundError,
    SchemaParseError,
    UnsafePathError,
)


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


def test_get_env_paths_defaults_to_cwd_for_single_service_project(
    tmp_path, monkeypatch
):
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
        f.write("services:\n  api:\n    path: services/api/env.schema.toml\n")

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
            "services:\n  athena:\n    path: athena/env.schema.toml\n    local_file: athena/config/env_config.local.py\n"
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


def test_get_env_paths_rejects_local_file_override_escaping_project(
    tmp_path, monkeypatch
):
    """
    Security regression: envshield.yml is normally committed to the repo, so
    an unvalidated 'local_file'/'example_file' override there is a
    supply-chain-style arbitrary-write vector -- any teammate who clones a
    repo with a malicious override and runs an ordinary command like 'setup'
    would otherwise have that path written to, however far outside the
    project it points.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "api").mkdir()
    with open("envshield.yml", "w") as f:
        f.write(
            "services:\n  api:\n    path: api/env.schema.toml\n    local_file: ../../../../tmp/evil\n"
        )

    with pytest.raises(UnsafePathError):
        config_manager.get_env_paths(service_name="api")


def test_get_env_paths_rejects_absolute_local_file_override(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "api").mkdir()
    with open("envshield.yml", "w") as f:
        f.write(
            "services:\n  api:\n    path: api/env.schema.toml\n    local_file: /etc/passwd\n"
        )

    with pytest.raises(UnsafePathError):
        config_manager.get_env_paths(service_name="api")


def test_get_service_schema_path_rejects_schema_path_escaping_project(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    with open("envshield.yml", "w") as f:
        f.write("services:\n  api:\n    path: ../../outside/env.schema.toml\n")

    with pytest.raises(UnsafePathError):
        config_manager.get_service_schema_path("api")


def test_add_service_rejects_schema_path_escaping_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    with pytest.raises(UnsafePathError):
        config_manager.add_service("api", "../../outside/env.schema.toml")

    # Nothing should have been written for a rejected entry.
    assert not os.path.exists(config_manager.CONFIG_FILE_NAME)


def test_add_service_rejects_local_file_escaping_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    with pytest.raises(UnsafePathError):
        config_manager.add_service(
            "api", "api/env.schema.toml", local_file="/etc/passwd"
        )


def test_get_env_paths_allows_local_file_override_within_project(tmp_path, monkeypatch):
    """Sanity check: the validation only rejects paths that escape the project -- normal overrides still work."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "athena" / "config").mkdir(parents=True)
    with open("envshield.yml", "w") as f:
        f.write(
            "services:\n  athena:\n    path: athena/env.schema.toml\n    local_file: athena/config/env_config.local.py\n"
        )

    paths = config_manager.get_env_paths(service_name="athena")

    assert paths["local_file"] == "athena/config/env_config.local.py"


def test_load_schema_merges_in_a_local_extends_base(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "shared").mkdir()
    (tmp_path / "services" / "api").mkdir(parents=True)
    with open("shared/base.schema.toml", "w") as f:
        f.write('[LOG_LEVEL]\ndescription="shared"\ndefaultValue="info"\n')
    with open("services/api/env.schema.toml", "w") as f:
        f.write(
            'extends = "../../shared/base.schema.toml"\n\n[DATABASE_URL]\ndescription="api-specific"\nsecret=true\n'
        )
    with open("envshield.yml", "w") as f:
        f.write("services:\n  api:\n    path: services/api/env.schema.toml\n")

    schema = config_manager.load_schema(service_name="api")

    assert set(schema.keys()) == {"LOG_LEVEL", "DATABASE_URL"}
    assert schema["LOG_LEVEL"]["defaultValue"] == "info"
    assert "extends" not in schema


def test_load_schema_child_definition_overrides_base_on_conflict(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with open("base.schema.toml", "w") as f:
        f.write('[LOG_LEVEL]\ndescription="base"\ndefaultValue="info"\n')
    with open("env.schema.toml", "w") as f:
        f.write(
            'extends = "base.schema.toml"\n\n[LOG_LEVEL]\ndescription="overridden"\ndefaultValue="debug"\n'
        )

    schema = config_manager.load_schema()

    assert schema["LOG_LEVEL"]["description"] == "overridden"
    assert schema["LOG_LEVEL"]["defaultValue"] == "debug"


def test_load_schema_supports_chained_extends(tmp_path, monkeypatch):
    """A extends B extends C -- variables from every level are merged."""
    monkeypatch.chdir(tmp_path)
    with open("grandparent.schema.toml", "w") as f:
        f.write('[FROM_GRANDPARENT]\ndescription="x"\n')
    with open("parent.schema.toml", "w") as f:
        f.write(
            'extends = "grandparent.schema.toml"\n\n[FROM_PARENT]\ndescription="x"\n'
        )
    with open("env.schema.toml", "w") as f:
        f.write('extends = "parent.schema.toml"\n\n[FROM_CHILD]\ndescription="x"\n')

    schema = config_manager.load_schema()

    assert set(schema.keys()) == {"FROM_GRANDPARENT", "FROM_PARENT", "FROM_CHILD"}


def test_load_schema_supports_multiple_extends_with_later_entries_winning(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    with open("base_a.schema.toml", "w") as f:
        f.write('[SHARED]\ndescription="from a"\n\n[FROM_A]\ndescription="x"\n')
    with open("base_b.schema.toml", "w") as f:
        f.write('[SHARED]\ndescription="from b"\n\n[FROM_B]\ndescription="x"\n')
    with open("env.schema.toml", "w") as f:
        f.write('extends = ["base_a.schema.toml", "base_b.schema.toml"]\n')

    schema = config_manager.load_schema()

    assert set(schema.keys()) == {"SHARED", "FROM_A", "FROM_B"}
    assert schema["SHARED"]["description"] == "from b"


def test_load_config_raises_clean_error_on_malformed_yaml(tmp_path, monkeypatch):
    """
    Regression: a malformed envshield.yml used to print a message and then
    re-raise the raw yaml.YAMLError, which no cli.py handler catches (they
    only catch EnvShieldException) -- producing an unhandled traceback
    instead of the clean error every other parse failure gets.
    """
    monkeypatch.chdir(tmp_path)
    with open("envshield.yml", "w") as f:
        f.write("services: [unclosed\n")

    with pytest.raises(ConfigParseError, match="envshield.yml"):
        config_manager.load_config()


def test_load_schema_detects_circular_extends(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with open("a.schema.toml", "w") as f:
        f.write('extends = "b.schema.toml"\n')
    with open("b.schema.toml", "w") as f:
        f.write('extends = "a.schema.toml"\n')

    with pytest.raises(SchemaParseError, match="circular"):
        config_manager._load_schema_file("a.schema.toml")


def test_load_schema_raises_when_extends_target_is_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with open("env.schema.toml", "w") as f:
        f.write('extends = "does-not-exist.schema.toml"\n')

    with pytest.raises(SchemaNotFoundError):
        config_manager.load_schema()


def test_load_schema_rejects_extends_path_escaping_the_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with open("env.schema.toml", "w") as f:
        f.write('extends = "../../../../etc/passwd"\n')

    with pytest.raises(UnsafePathError):
        config_manager.load_schema()


def test_load_schema_without_extends_is_unaffected(tmp_path, monkeypatch):
    """Sanity check: a plain schema with no 'extends' loads exactly as before."""
    monkeypatch.chdir(tmp_path)
    with open("env.schema.toml", "w") as f:
        f.write('[FOO]\ndescription="x"\n')

    schema = config_manager.load_schema()

    assert schema == {"FOO": {"description": "x"}}


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
            "project_name: zeus\nservices:\n  athena:\n    path: athena/env.schema.toml\n    local_file: athena/config/env_config.local.py\n"
        )

    config_manager.add_service(
        "hermes",
        "hermes/env.schema.toml",
        local_file="hermes/config/env_config.local.py",
    )

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


def test_add_service_registers_deployment_manifest_and_container(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "api").mkdir()
    (tmp_path / "docker-compose.yml").write_text("services:\n  api:\n    image: x\n")

    config_manager.add_service(
        "api",
        "api/env.schema.toml",
        deployment_manifest="docker-compose.yml",
        container="api-container",
    )

    entry = config_manager.get_services()["api"]
    assert entry["deployment_manifest"] == "docker-compose.yml"
    assert entry["container"] == "api-container"


def test_get_deployment_manifest_returns_none_when_not_registered(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    config_manager.add_service("api", "api/env.schema.toml")

    assert config_manager.get_deployment_manifest("api") is None


def test_get_deployment_manifest_for_a_service(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "docker-compose.yml").write_text("services:\n  api:\n    image: x\n")
    config_manager.add_service(
        "api", "api/env.schema.toml", deployment_manifest="docker-compose.yml"
    )

    manifest = config_manager.get_deployment_manifest("api")

    assert manifest == {"path": "docker-compose.yml", "container": None}


def test_get_deployment_manifest_for_root_project(tmp_path, monkeypatch):
    """A single-service/root project registers its manifest at the top level of envshield.yml, not under 'services'."""
    monkeypatch.chdir(tmp_path)
    with open("envshield.yml", "w") as f:
        f.write("project_name: demo\ndeployment_manifest: docker-compose.yml\n")

    manifest = config_manager.get_deployment_manifest()

    assert manifest == {"path": "docker-compose.yml", "container": None}


def test_get_deployment_manifest_rejects_manifest_path_escaping_project(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    with open("envshield.yml", "w") as f:
        f.write("deployment_manifest: ../../../../etc/passwd\n")

    with pytest.raises(UnsafePathError):
        config_manager.get_deployment_manifest()


def test_add_service_overwrites_a_service_registered_under_the_same_name(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    config_manager.add_service("athena", "athena/env.schema.toml", description="old")

    config_manager.add_service("athena", "athena/env.schema.toml", description="new")

    assert config_manager.get_services()["athena"]["description"] == "new"
