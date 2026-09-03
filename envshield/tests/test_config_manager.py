# envshield/tests/test_config_manager.py
import os

import pytest

from envshield.config import manager as config_manager
from envshield.core.exceptions import (
    ConfigParseError,
    SchemaNotFoundError,
    SchemaParseError,
    SecretDefaultConflictError,
    UnsafePathError,
)


class TestFindProjectRootGitBoundary:
    """
    P0 regression: find_project_root's upward walk for 'envshield.yml' must
    never cross past the nearest enclosing Git repository boundary
    relative to `start` -- otherwise a command run from inside an
    independent nested repository (a vendored dependency, a scratch
    checkout, a submodule) silently adopts an unrelated outer project's
    envshield.yml. See envshield/tests/test_service_cli.py for the
    end-to-end CLI-level reproduction of the actual failure mode.
    """

    def test_stops_at_an_independent_nested_repository_boundary(self, tmp_path):
        (tmp_path / ".git").mkdir()
        with open(tmp_path / "envshield.yml", "w") as f:
            f.write("services:\n  edge:\n    schema: edge/env.schema.toml\n")

        nested_repo = tmp_path / "edge" / "reserved3"
        nested_repo.mkdir(parents=True)
        (nested_repo / ".git").mkdir()

        assert config_manager.find_project_root(str(nested_repo)) is None

    def test_stops_at_a_dot_git_file_boundary_too(self, tmp_path):
        """Worktree/submodule-style '.git' *file* must block the walk exactly like a '.git' directory does."""
        (tmp_path / ".git").mkdir()
        with open(tmp_path / "envshield.yml", "w") as f:
            f.write("services:\n  edge:\n    schema: edge/env.schema.toml\n")

        nested_repo = tmp_path / "edge" / "reserved3"
        nested_repo.mkdir(parents=True)
        (nested_repo / ".git").write_text("gitdir: /elsewhere/.git/worktrees/x\n")

        assert config_manager.find_project_root(str(nested_repo)) is None

    def test_an_ordinary_subdirectory_without_its_own_git_still_resolves_normally(
        self, tmp_path
    ):
        """The single most common case: a plain service subdirectory inside the same repo as envshield.yml must be completely unaffected."""
        (tmp_path / ".git").mkdir()
        with open(tmp_path / "envshield.yml", "w") as f:
            f.write("services:\n  edge:\n    schema: edge/env.schema.toml\n")

        plain_subdir = tmp_path / "edge" / "nested"
        plain_subdir.mkdir(parents=True)

        assert config_manager.find_project_root(str(plain_subdir)) == str(tmp_path)

    def test_invocation_from_the_project_root_itself_still_works(self, tmp_path):
        (tmp_path / ".git").mkdir()
        with open(tmp_path / "envshield.yml", "w") as f:
            f.write("services:\n  app:\n    schema: env.schema.toml\n")

        assert config_manager.find_project_root(str(tmp_path)) == str(tmp_path)

    def test_no_git_repository_at_all_preserves_the_original_full_upward_walk(
        self, tmp_path
    ):
        """No '.git' anywhere above `start` at all -- the pre-existing behavior (walk all the way to the filesystem root) is completely unaffected."""
        with open(tmp_path / "envshield.yml", "w") as f:
            f.write("services:\n  app:\n    schema: env.schema.toml\n")

        nested = tmp_path / "a" / "b" / "c"
        nested.mkdir(parents=True)

        assert config_manager.find_project_root(str(nested)) == str(tmp_path)


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


def test_get_env_paths_defaults_to_cwd_for_a_root_level_service(tmp_path, monkeypatch):
    """
    A single-service project's one service is just the one-entry case of the
    same `services` map a multi-service project has -- its schema lives at
    the project root ('.'), so its paths stay exactly '.env.example' / '.env',
    unchanged from EnvShield's original single-project behaviour.
    """
    monkeypatch.chdir(tmp_path)
    config_manager.add_service("app", "env.schema.toml")

    paths = config_manager.get_env_paths(service_name="app")

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
        f.write("services:\n  api:\n    schema: services/api/env.schema.toml\n")

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
    (tmp_path / "alpha" / "config").mkdir(parents=True)
    with open("envshield.yml", "w") as f:
        f.write(
            "services:\n  alpha:\n    schema: alpha/env.schema.toml\n    local_file: alpha/config/env_config.local.py\n"
        )

    paths = config_manager.get_env_paths(service_name="alpha")

    assert paths["local_file"] == "alpha/config/env_config.local.py"
    # example_file still defaults sensibly even though only local_file was overridden
    assert paths["example_file"] == "alpha/.env.example"


def test_get_env_paths_raises_for_unknown_service(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with open("envshield.yml", "w") as f:
        f.write("services:\n  api:\n    schema: services/api/env.schema.toml\n")

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
            "services:\n  api:\n    schema: api/env.schema.toml\n    local_file: ../../../../tmp/evil\n"
        )

    with pytest.raises(UnsafePathError):
        config_manager.get_env_paths(service_name="api")


def test_get_env_paths_rejects_absolute_local_file_override(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "api").mkdir()
    with open("envshield.yml", "w") as f:
        f.write(
            "services:\n  api:\n    schema: api/env.schema.toml\n    local_file: /etc/passwd\n"
        )

    with pytest.raises(UnsafePathError):
        config_manager.get_env_paths(service_name="api")


def test_get_service_schema_path_rejects_schema_path_escaping_project(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    with open("envshield.yml", "w") as f:
        f.write("services:\n  api:\n    schema: ../../outside/env.schema.toml\n")

    with pytest.raises(UnsafePathError):
        config_manager.get_service_schema_path("api")


def test_get_service_schema_path_reports_legacy_path_key_distinctly(
    tmp_path, monkeypatch
):
    """
    A pre-4.5.0 service entry uses 'path:' instead of 'schema:' (a breaking,
    no-shim rename -- see CHANGELOG). Before this fix, get_service_schema_path
    returned None for this case exactly as it would for a nonexistent
    service, so load_schema's error read "Service 'api' not found...
    Available: api" -- self-contradicting, since 'api' is right there in its
    own available list.
    """
    monkeypatch.chdir(tmp_path)
    with open("envshield.yml", "w") as f:
        f.write("services:\n  api:\n    path: api/env.schema.toml\n")

    with pytest.raises(SchemaNotFoundError, match="legacy 'path:' key"):
        config_manager.get_service_schema_path("api")


def test_load_schema_reports_legacy_path_key_distinctly(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with open("envshield.yml", "w") as f:
        f.write("services:\n  api:\n    path: api/env.schema.toml\n")

    with pytest.raises(SchemaNotFoundError, match="legacy 'path:' key"):
        config_manager.load_schema("api")


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
    (tmp_path / "alpha" / "config").mkdir(parents=True)
    with open("envshield.yml", "w") as f:
        f.write(
            "services:\n  alpha:\n    schema: alpha/env.schema.toml\n    local_file: alpha/config/env_config.local.py\n"
        )

    paths = config_manager.get_env_paths(service_name="alpha")

    assert paths["local_file"] == "alpha/config/env_config.local.py"


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
        f.write("services:\n  api:\n    schema: services/api/env.schema.toml\n")

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

    schema = config_manager._load_schema_file("env.schema.toml")

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

    schema = config_manager._load_schema_file("env.schema.toml")

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

    schema = config_manager._load_schema_file("env.schema.toml")

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


def test_load_schema_detects_an_equivalent_relative_path_cycle(tmp_path, monkeypatch):
    """
    Two different relative-path spellings that already normalize to the
    same abspath (no symlink involved) -- confirms this pre-existing
    correctness (already handled by plain os.path.abspath's own lexical
    '.'/'..' collapsing) survives the BL-010 fix, not just the new
    symlink case."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "dir").mkdir()
    with open("dir/a.schema.toml", "w") as f:
        f.write('extends = "../dir/b.schema.toml"\n')
    with open("dir/b.schema.toml", "w") as f:
        f.write('extends = "./a.schema.toml"\n')

    with pytest.raises(SchemaParseError, match="circular"):
        config_manager._load_schema_file("dir/a.schema.toml")


def test_load_schema_detects_a_symlinked_extends_cycle(tmp_path, monkeypatch):
    """
    Regression for BL-010: cycle detection used a purely lexical
    os.path.abspath, which never follows symlinks -- a self-referencing
    symlink produced a different (never-repeating) path string on every
    recursive visit, so the cycle was never detected and the recursion
    was only bounded by the OS's own symlink-depth limit (ELOOP),
    producing a garbled error instead of a clean "circular" one.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "real_dir").mkdir()
    (tmp_path / "real_dir" / "self").symlink_to(tmp_path / "real_dir")
    with open("real_dir/x.schema.toml", "w") as f:
        f.write('extends = "self/x.schema.toml"\n\n[FOO]\ndescription = "x"\n')

    with pytest.raises(SchemaParseError, match="circular"):
        config_manager._load_schema_file("real_dir/x.schema.toml")


def test_resolve_field_provenance_detects_a_symlinked_extends_cycle(
    tmp_path, monkeypatch
):
    """Mirrors _load_schema_file's own symlink-cycle regression above --
    BACKLOG.md's BL-010 entry confirms both functions share the exact
    same lexical-not-physical bug, so both need their own regression."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "real_dir").mkdir()
    (tmp_path / "real_dir" / "self").symlink_to(tmp_path / "real_dir")
    with open("real_dir/x.schema.toml", "w") as f:
        f.write('extends = "self/x.schema.toml"\n\n[FOO]\ndescription = "x"\n')

    with pytest.raises(SchemaParseError, match="circular"):
        config_manager.resolve_field_provenance("real_dir/x.schema.toml")


def test_load_schema_raises_when_extends_target_is_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with open("env.schema.toml", "w") as f:
        f.write('extends = "does-not-exist.schema.toml"\n')

    with pytest.raises(SchemaNotFoundError):
        config_manager._load_schema_file("env.schema.toml")


def test_load_schema_rejects_extends_path_escaping_the_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with open("env.schema.toml", "w") as f:
        f.write('extends = "../../../../etc/passwd"\n')

    with pytest.raises(UnsafePathError):
        config_manager._load_schema_file("env.schema.toml")


def test_load_schema_without_extends_is_unaffected(tmp_path, monkeypatch):
    """Sanity check: a plain schema with no 'extends' loads exactly as before."""
    monkeypatch.chdir(tmp_path)
    with open("env.schema.toml", "w") as f:
        f.write('[FOO]\ndescription="x"\n')

    schema = config_manager._load_schema_file("env.schema.toml")

    assert schema == {"FOO": {"description": "x"}}


class TestLoadSchemaRejectsSecretDefaults:
    """
    BL-001 regression: a schema field marked 'secret' must never also carry
    a real defaultValue -- that value is written verbatim into
    '.env.example', generated Python, and generated TypeScript. The fix is
    enforced at schema load time, the single choke point every schema-
    consuming command (check/doctor/setup/sync/generate/explain/scan)
    already routes through, so one guard protects all of them.
    """

    def test_load_schema_rejects_secret_field_with_a_real_default(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        with open("env.schema.toml", "w") as f:
            f.write(
                '[STRIPE_SECRET_KEY]\ndescription="x"\nsecret=true\n'
                'defaultValue="sk_live_SYNTHETIC_NOT_A_REAL_SECRET"\n'
            )
        with open("envshield.yml", "w") as f:
            f.write("services:\n  api:\n    schema: env.schema.toml\n")

        with pytest.raises(SecretDefaultConflictError) as exc_info:
            config_manager.load_schema("api")

        # The field name is named so the fix is obvious; the actual
        # default value must never appear in the error message itself.
        assert "STRIPE_SECRET_KEY" in str(exc_info.value)
        assert "sk_live_SYNTHETIC_NOT_A_REAL_SECRET" not in str(exc_info.value)

    def test_load_bare_schema_rejects_secret_field_with_a_real_default(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        with open("env.schema.toml", "w") as f:
            f.write(
                '[API_TOKEN]\nsecret=true\ndefaultValue="SYNTHETIC_NOT_A_REAL_SECRET"\n'
            )

        with pytest.raises(SecretDefaultConflictError):
            config_manager.load_bare_schema()

    def test_load_schema_rejects_secret_default_inherited_via_extends(
        self, tmp_path, monkeypatch
    ):
        """The conflict must be caught after the extends-merge, not just on the schema's own directly-declared fields."""
        monkeypatch.chdir(tmp_path)
        with open("base.schema.toml", "w") as f:
            f.write(
                '[DB_PASSWORD]\nsecret=true\ndefaultValue="SYNTHETIC_NOT_A_REAL_SECRET"\n'
            )
        with open("env.schema.toml", "w") as f:
            f.write('extends = "base.schema.toml"\n')
        with open("envshield.yml", "w") as f:
            f.write("services:\n  api:\n    schema: env.schema.toml\n")

        with pytest.raises(SecretDefaultConflictError):
            config_manager.load_schema("api")

    def test_load_schema_allows_secret_field_with_no_default(
        self, tmp_path, monkeypatch
    ):
        """Sanity check: a secret field with no defaultValue at all -- the ordinary, correct case -- still loads."""
        monkeypatch.chdir(tmp_path)
        with open("env.schema.toml", "w") as f:
            f.write("[API_TOKEN]\nsecret=true\n")
        with open("envshield.yml", "w") as f:
            f.write("services:\n  api:\n    schema: env.schema.toml\n")

        schema = config_manager.load_schema("api")

        assert schema["API_TOKEN"]["secret"] is True

    def test_load_schema_allows_secret_field_with_empty_string_default(
        self, tmp_path, monkeypatch
    ):
        """An empty-string default on a secret field ('optional, blank if unset') carries nothing to leak."""
        monkeypatch.chdir(tmp_path)
        with open("env.schema.toml", "w") as f:
            f.write('[API_TOKEN]\nsecret=true\ndefaultValue=""\n')
        with open("envshield.yml", "w") as f:
            f.write("services:\n  api:\n    schema: env.schema.toml\n")

        schema = config_manager.load_schema("api")

        assert schema["API_TOKEN"]["defaultValue"] == ""

    def test_load_schema_allows_non_secret_field_with_a_default(
        self, tmp_path, monkeypatch
    ):
        """Sanity check: ordinary non-secret defaults are completely untouched by this change."""
        monkeypatch.chdir(tmp_path)
        with open("env.schema.toml", "w") as f:
            f.write('[LOG_LEVEL]\ndefaultValue="info"\n')
        with open("envshield.yml", "w") as f:
            f.write("services:\n  api:\n    schema: env.schema.toml\n")

        schema = config_manager.load_schema("api")

        assert schema["LOG_LEVEL"]["defaultValue"] == "info"


def test_add_service_creates_envshield_yml_when_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    config_manager.add_service("alpha", "alpha/env.schema.toml")

    services = config_manager.get_services()
    assert services == {"alpha": {"schema": "alpha/env.schema.toml"}}


def test_add_service_extends_without_touching_existing_services_or_other_keys(
    tmp_path, monkeypatch
):
    """The whole point of 'extend': adding a new service must never disturb an already-configured one."""
    monkeypatch.chdir(tmp_path)
    with open("envshield.yml", "w") as f:
        f.write(
            "project_name: acme\nservices:\n  alpha:\n    schema: alpha/env.schema.toml\n    local_file: alpha/config/env_config.local.py\n"
        )

    config_manager.add_service(
        "beta",
        "beta/env.schema.toml",
        local_file="beta/config/env_config.local.py",
    )

    config = config_manager.load_config()
    assert config["project_name"] == "acme"
    assert config["services"]["alpha"] == {
        "schema": "alpha/env.schema.toml",
        "local_file": "alpha/config/env_config.local.py",
    }
    assert config["services"]["beta"] == {
        "schema": "beta/env.schema.toml",
        "local_file": "beta/config/env_config.local.py",
    }


def test_add_service_includes_optional_fields_only_when_given(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    config_manager.add_service(
        "alpha",
        "alpha/env.schema.toml",
        local_file="alpha/config/env_config.local.py",
        example_file="alpha/.env.example",
        description="Backend API",
    )

    entry = config_manager.get_services()["alpha"]
    assert entry == {
        "schema": "alpha/env.schema.toml",
        "description": "Backend API",
        "local_file": "alpha/config/env_config.local.py",
        "example_file": "alpha/.env.example",
    }


def test_add_manifest_registers_a_container_to_service_mapping(tmp_path, monkeypatch):
    """
    A manifest is registered independently of any one service -- it's
    topology shared by whichever services it names, not owned by any single
    one of them (see config_manager.add_service's docstring for why).
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "api").mkdir()
    (tmp_path / "docker-compose.yml").write_text("services:\n  api:\n    image: x\n")
    config_manager.add_service("api", "api/env.schema.toml")

    config_manager.add_manifest("docker-compose.yml", {"api-container": "api"})

    config = config_manager.load_config()
    assert config["manifests"] == [
        {"file": "docker-compose.yml", "containers": {"api-container": "api"}}
    ]


def test_add_manifest_merges_into_an_existing_entry_for_the_same_file(
    tmp_path, monkeypatch
):
    """Calling add_manifest again for the same file extends its container map instead of replacing the entry."""
    monkeypatch.chdir(tmp_path)
    config_manager.add_service("api", "api/env.schema.toml")
    config_manager.add_service("web", "web/env.schema.toml")
    config_manager.add_manifest("docker-compose.yml", {"api": "api"})

    config_manager.add_manifest("docker-compose.yml", {"web": "web"})

    config = config_manager.load_config()
    assert config["manifests"] == [
        {"file": "docker-compose.yml", "containers": {"api": "api", "web": "web"}}
    ]


def test_get_deployment_manifests_returns_empty_list_when_not_registered(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    config_manager.add_service("api", "api/env.schema.toml")

    assert config_manager.get_deployment_manifests("api") == []


def test_get_deployment_manifests_for_a_service(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "docker-compose.yml").write_text("services:\n  api:\n    image: x\n")
    config_manager.add_service("api", "api/env.schema.toml")
    config_manager.add_manifest("docker-compose.yml", {"api": "api"})

    manifests = config_manager.get_deployment_manifests("api")

    assert manifests == [{"path": "docker-compose.yml", "container": "api"}]


def test_get_deployment_manifests_supports_a_service_named_in_more_than_one_manifest(
    tmp_path, monkeypatch
):
    """A service can legitimately be named in more than one manifest -- a local compose file and a production Kubernetes manifest, say."""
    monkeypatch.chdir(tmp_path)
    config_manager.add_service("api", "api/env.schema.toml")
    config_manager.add_manifest("docker-compose.yml", {"api": "api"})
    config_manager.add_manifest("k8s/deployment.yaml", {"api": "api"})

    manifests = config_manager.get_deployment_manifests("api")

    assert {m["path"] for m in manifests} == {
        "docker-compose.yml",
        "k8s/deployment.yaml",
    }


def test_get_deployment_manifests_rejects_manifest_path_escaping_project(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    config_manager.add_service("api", "api/env.schema.toml")
    with open("envshield.yml", "a") as f:
        f.write(
            "manifests:\n  - file: ../../../../etc/passwd\n    containers:\n      api: api\n"
        )

    with pytest.raises(UnsafePathError):
        config_manager.get_deployment_manifests("api")


def test_remove_service_deregisters_and_drops_its_manifest_mappings(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    config_manager.add_service("api", "api/env.schema.toml")
    config_manager.add_service("web", "web/env.schema.toml")
    config_manager.add_manifest("docker-compose.yml", {"api": "api", "web": "web"})

    config_manager.remove_service("api")

    assert "api" not in config_manager.get_services()
    assert "web" in config_manager.get_services()
    config = config_manager.load_config()
    assert config["manifests"] == [
        {"file": "docker-compose.yml", "containers": {"web": "web"}}
    ]


def test_remove_service_raises_for_an_unknown_service(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config_manager.add_service("api", "api/env.schema.toml")

    with pytest.raises(SchemaNotFoundError):
        config_manager.remove_service("does-not-exist")


def test_add_service_overwrites_a_service_registered_under_the_same_name(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    config_manager.add_service("alpha", "alpha/env.schema.toml", description="old")

    config_manager.add_service("alpha", "alpha/env.schema.toml", description="new")

    assert config_manager.get_services()["alpha"]["description"] == "new"


def test_add_service_merges_into_an_existing_entry_for_the_same_name(
    tmp_path, monkeypatch
):
    """
    Regression for BL-023: a second add_service call for an already-
    registered name used to build a fresh dict from only that call's own
    arguments, silently dropping every field the first call had set that
    wasn't repeated. It must now merge -- each field given here overwrites
    that field, any field left unset keeps whatever the entry already had --
    matching add_manifest's already-additive behavior.
    """
    monkeypatch.chdir(tmp_path)
    config_manager.add_service(
        "alpha",
        "alpha/env.schema.toml",
        description="Backend API",
        example_file="alpha/.env.example",
        config_source="alpha/config/settings.py",
    )

    config_manager.add_service(
        "alpha",
        "alpha/env.schema.toml",
        local_file="alpha/config/env_config.local.py",
    )

    assert config_manager.get_services()["alpha"] == {
        "schema": "alpha/env.schema.toml",
        "description": "Backend API",
        "example_file": "alpha/.env.example",
        "config_source": "alpha/config/settings.py",
        "local_file": "alpha/config/env_config.local.py",
    }


def test_get_service_completeness_mode_is_none_by_default(tmp_path, monkeypatch):
    """BL-030: completeness: union is opt-in -- unset means None, never inferred."""
    monkeypatch.chdir(tmp_path)
    config_manager.add_service("alpha", "alpha/env.schema.toml")

    assert config_manager.get_service_completeness_mode("alpha") is None


def test_get_service_completeness_mode_reads_the_envshield_yml_key(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    with open("envshield.yml", "w") as f:
        f.write(
            "services:\n  alpha:\n    schema: alpha/env.schema.toml\n    completeness: union\n"
        )

    assert config_manager.get_service_completeness_mode("alpha") == "union"


def test_get_service_completeness_mode_is_none_for_an_unknown_service(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)

    assert config_manager.get_service_completeness_mode("does-not-exist") is None


def test_get_service_additional_source_roots_is_empty_by_default(tmp_path, monkeypatch):
    """BL-106: additional_source_roots is opt-in -- unset means [], never inferred."""
    monkeypatch.chdir(tmp_path)
    config_manager.add_service("alpha", "alpha/env.schema.toml")

    assert config_manager.get_service_additional_source_roots("alpha") == []


def test_get_service_additional_source_roots_reads_the_envshield_yml_key(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    with open("envshield.yml", "w") as f:
        f.write(
            "services:\n"
            "  alpha:\n"
            "    schema: alpha/env.schema.toml\n"
            "    additional_source_roots:\n"
            "      - shared/lib\n"
            "      - vendor/other\n"
        )

    assert config_manager.get_service_additional_source_roots("alpha") == [
        "shared/lib",
        "vendor/other",
    ]


def test_get_service_additional_source_roots_is_empty_for_an_unknown_service(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)

    assert config_manager.get_service_additional_source_roots("does-not-exist") == []


def test_get_service_additional_source_roots_rejects_a_path_outside_the_project(
    tmp_path, monkeypatch
):
    """
    Same supply-chain-style protection as schema/local_file/example_file
    (_ensure_within_project) -- envshield.yml is committed, PR-editable,
    untrusted input.
    """
    monkeypatch.chdir(tmp_path)
    with open("envshield.yml", "w") as f:
        f.write(
            "services:\n"
            "  alpha:\n"
            "    schema: alpha/env.schema.toml\n"
            "    additional_source_roots:\n"
            "      - ../outside\n"
        )

    with pytest.raises(UnsafePathError):
        config_manager.get_service_additional_source_roots("alpha")


def test_get_service_additional_source_roots_ignores_a_non_list_value(
    tmp_path, monkeypatch
):
    """A malformed (non-list) value is treated as unset rather than raising --
    matching get_service_completeness_mode's own lenient-parse precedent for
    a similarly free-form envshield.yml value."""
    monkeypatch.chdir(tmp_path)
    with open("envshield.yml", "w") as f:
        f.write(
            "services:\n"
            "  alpha:\n"
            "    schema: alpha/env.schema.toml\n"
            "    additional_source_roots: not-a-list\n"
        )

    assert config_manager.get_service_additional_source_roots("alpha") == []


class TestSymlinkEscapeIsPrevented:
    """
    Regression coverage for P0-6: _ensure_within_project used a purely
    lexical os.path.abspath()/commonpath() check, which never resolves
    symlinks -- a symlinked path component (or the configured path itself)
    satisfied the check while its real target resolved outside the
    project. envshield.yml is committed/PR-editable (see UnsafePathError's
    own docstring), so a malicious repository could ship a symlink plus a
    matching envshield.yml entry and have an ordinary command read or write
    through it. These tests exercise the actual containment boundary with
    real symlinks on disk, not just string assertions -- and for the
    write case, prove the outside target was never created, not merely
    that an exception was raised.
    """

    def test_symlinked_directory_component_escaping_the_project_is_rejected(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        outside = tmp_path.parent / "outside_dir_component"
        outside.mkdir(exist_ok=True)
        os.symlink(outside, tmp_path / "inside-link")

        with pytest.raises(UnsafePathError):
            config_manager._ensure_within_project(
                os.path.join("inside-link", "target.toml"), "test path"
            )

    def test_leaf_symlink_escaping_the_project_is_rejected(self, tmp_path, monkeypatch):
        """The symlink IS the final path component, not a parent directory."""
        monkeypatch.chdir(tmp_path)
        outside_file = tmp_path.parent / "outside_leaf.toml"
        outside_file.write_text("[X]\n")
        os.symlink(outside_file, tmp_path / "leaf-link.toml")

        with pytest.raises(UnsafePathError):
            config_manager._ensure_within_project("leaf-link.toml", "test path")

    def test_nested_symlink_chain_escaping_the_project_is_rejected(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        outside = tmp_path.parent / "outside_nested"
        outside.mkdir(exist_ok=True)
        link_b = tmp_path / "link-b"
        os.symlink(outside, link_b)
        link_a = tmp_path / "link-a"
        os.symlink(link_b, link_a)

        with pytest.raises(UnsafePathError):
            config_manager._ensure_within_project(
                os.path.join("link-a", "target.toml"), "test path"
            )

    def test_symlink_resolving_inside_the_project_is_accepted(
        self, tmp_path, monkeypatch
    ):
        """Regression guard: the fix must not start rejecting a symlink
        whose target genuinely is inside the project."""
        monkeypatch.chdir(tmp_path)
        real_subdir = tmp_path / "real-subdir"
        real_subdir.mkdir()
        os.symlink(real_subdir, tmp_path / "in-project-link")

        result = config_manager._ensure_within_project(
            os.path.join("in-project-link", "config.toml"), "test path"
        )

        assert result == os.path.join("in-project-link", "config.toml")

    def test_dangling_symlink_escaping_the_project_is_rejected_not_crashed(
        self, tmp_path, monkeypatch
    ):
        """A dangling symlink (target doesn't exist) must still be resolved
        and rejected, not raise an unrelated OSError or silently pass."""
        monkeypatch.chdir(tmp_path)
        os.symlink(
            tmp_path.parent / "does-not-exist-anywhere",
            tmp_path / "dangling-link",
        )

        with pytest.raises(UnsafePathError):
            config_manager._ensure_within_project(
                os.path.join("dangling-link", "newfile.env"), "test path"
            )

    def test_returned_value_is_still_the_original_relative_path_string(
        self, tmp_path, monkeypatch
    ):
        """
        The fix changes the *comparison*, not the *return value* -- a
        legitimate relative path must still round-trip unchanged, since
        add_service/add_manifest persist this exact string into
        envshield.yml, and a resolved absolute path there would replace a
        portable path with a machine-specific one.
        """
        monkeypatch.chdir(tmp_path)
        (tmp_path / "services" / "api").mkdir(parents=True)

        result = config_manager._ensure_within_project(
            "services/api/env.schema.toml", "test path"
        )

        assert result == "services/api/env.schema.toml"
        assert not os.path.isabs(result)

    def test_add_service_persists_the_original_relative_path_through_a_legitimate_symlink(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        real_subdir = tmp_path / "real-subdir"
        real_subdir.mkdir()
        os.symlink(real_subdir, tmp_path / "svc-link")

        config_manager.add_service("api", "svc-link/env.schema.toml")

        with open("envshield.yml") as f:
            content = f.read()
        assert "svc-link/env.schema.toml" in content
        assert str(real_subdir) not in content

    def test_read_oriented_schema_path_cannot_escape_through_a_symlink(
        self, tmp_path, monkeypatch
    ):
        """End-to-end read path: a schema entry routed through a symlink
        must never actually be opened/parsed."""
        monkeypatch.chdir(tmp_path)
        outside = tmp_path.parent / "outside_schema_read"
        outside.mkdir(exist_ok=True)
        outside_schema = outside / "secret.schema.toml"
        outside_schema.write_text('[SHOULD_NEVER_BE_READ]\ndescription = "x"\n')
        os.symlink(outside, tmp_path / "schema-link")

        with open("envshield.yml", "w") as f:
            f.write("services:\n  api:\n    schema: schema-link/secret.schema.toml\n")

        with pytest.raises(UnsafePathError):
            config_manager.get_service_schema_path("api")

        with pytest.raises(UnsafePathError):
            config_manager.load_schema("api")

    def test_write_oriented_local_file_cannot_escape_through_a_symlink(
        self, tmp_path, monkeypatch
    ):
        """End-to-end write path: a local_file override routed through a
        symlink must never actually be written to -- proven by the outside
        target's absence, not merely by the exception."""
        monkeypatch.chdir(tmp_path)
        outside = tmp_path.parent / "outside_write_target"
        outside.mkdir(exist_ok=True)
        outside_file = outside / "secrets.env"
        assert not outside_file.exists()
        os.symlink(outside, tmp_path / "write-link")

        with open("envshield.yml", "w") as f:
            f.write(
                "services:\n  api:\n    schema: env.schema.toml\n"
                "    local_file: write-link/secrets.env\n"
            )
        with open("env.schema.toml", "w") as f:
            f.write('[API_KEY]\ndescription = "x"\n')

        with pytest.raises(UnsafePathError):
            config_manager.get_env_paths(service_name="api")

        assert not outside_file.exists()

    def test_deployment_manifest_path_cannot_escape_through_a_symlink(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        outside = tmp_path.parent / "outside_manifest"
        outside.mkdir(exist_ok=True)
        (outside / "docker-compose.yml").write_text("services: {}\n")
        os.symlink(outside, tmp_path / "manifest-link")

        config_manager.add_service("api", "env.schema.toml")
        with open("envshield.yml", "a") as f:
            f.write(
                "manifests:\n  - file: manifest-link/docker-compose.yml\n"
                "    containers:\n      api: api\n"
            )

        with pytest.raises(UnsafePathError):
            config_manager.get_deployment_manifests("api")


class TestServiceDirContains:
    """
    Phase 2C: mechanically extracted from scanner.py's previously-private
    per-service directory router (_normalize_for_dir_match plus the inline
    containment check in _build_undeclared_var_resolver) so
    dependency_snapshot.py can reuse the exact same routing logic instead
    of a second copy that could drift. Same behavior, new home.
    """

    def test_root_service_dir_matches_every_path(self):
        assert config_manager.service_dir_contains("app.py", ".") is True
        assert config_manager.service_dir_contains("nested/app.py", ".") is True

    def test_file_directly_inside_the_service_dir_matches(self):
        assert (
            config_manager.service_dir_contains("services/api/app.py", "services/api")
            is True
        )

    def test_the_service_dir_itself_matches(self):
        assert (
            config_manager.service_dir_contains("services/api", "services/api") is True
        )

    def test_sibling_service_dir_does_not_match(self):
        assert (
            config_manager.service_dir_contains("services/web/app.py", "services/api")
            is False
        )

    def test_a_prefix_that_is_not_a_real_subdirectory_does_not_match(self):
        """'services/api-extra' must not match 'services/api' just because
        the string happens to start with it."""
        assert (
            config_manager.service_dir_contains(
                "services/api-extra/app.py", "services/api"
            )
            is False
        )

    def test_absolute_file_path_is_normalized_against_cwd(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "services" / "api").mkdir(parents=True)

        absolute = str(tmp_path / "services" / "api" / "app.py")

        assert config_manager.service_dir_contains(absolute, "services/api") is True

    def test_unnormalized_service_dir_is_normalized_too(self):
        assert (
            config_manager.service_dir_contains(
                "services/api/app.py", "./services/api/"
            )
            is True
        )
