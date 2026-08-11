# envshield/tests/core/test_schema_snapshot.py
import subprocess

import pytest

from envshield.core import schema_snapshot
from envshield.core.exceptions import (
    ConfigParseError,
    SchemaNotFoundError,
    SchemaParseError,
    UnsafePathError,
)


def _init_repo(path):
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=path, check=True
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)


def _commit(path, message):
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=path, check=True)


def _write(path, relative, content):
    full = path / relative
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content)


class TestLoadSchemaForDiffAtARevision:
    def test_loads_a_plain_schema_at_head(self, tmp_path, monkeypatch):
        _init_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        _write(
            tmp_path,
            "envshield.yml",
            "services:\n  api:\n    schema: env.schema.toml\n",
        )
        _write(
            tmp_path, "env.schema.toml", '[API_KEY]\ndescription = "x"\nsecret = true\n'
        )
        _commit(tmp_path, "v1")

        schema = schema_snapshot.load_schema_for_diff("api", "HEAD")

        assert schema == {"API_KEY": {"description": "x", "secret": True}}

    def test_loads_an_older_revision_distinct_from_head(self, tmp_path, monkeypatch):
        _init_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        _write(
            tmp_path,
            "envshield.yml",
            "services:\n  api:\n    schema: env.schema.toml\n",
        )
        _write(tmp_path, "env.schema.toml", '[OLD_VAR]\ndescription = "x"\n')
        _commit(tmp_path, "v1")
        _write(tmp_path, "env.schema.toml", '[NEW_VAR]\ndescription = "x"\n')
        _commit(tmp_path, "v2")

        old_schema = schema_snapshot.load_schema_for_diff("api", "HEAD~1")
        new_schema = schema_snapshot.load_schema_for_diff("api", "HEAD")

        assert old_schema == {"OLD_VAR": {"description": "x"}}
        assert new_schema == {"NEW_VAR": {"description": "x"}}

    def test_working_tree_side_is_unaffected_and_reflects_uncommitted_edits(
        self, tmp_path, monkeypatch
    ):
        _init_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        _write(
            tmp_path,
            "envshield.yml",
            "services:\n  api:\n    schema: env.schema.toml\n",
        )
        _write(tmp_path, "env.schema.toml", '[COMMITTED]\ndescription = "x"\n')
        _commit(tmp_path, "v1")
        _write(tmp_path, "env.schema.toml", '[UNCOMMITTED]\ndescription = "x"\n')

        working_tree_schema = schema_snapshot.load_schema_for_diff("api", None)
        head_schema = schema_snapshot.load_schema_for_diff("api", "HEAD")

        assert working_tree_schema == {"UNCOMMITTED": {"description": "x"}}
        assert head_schema == {"COMMITTED": {"description": "x"}}

    def test_unknown_service_at_a_revision_raises_clearly(self, tmp_path, monkeypatch):
        _init_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        _write(
            tmp_path,
            "envshield.yml",
            "services:\n  api:\n    schema: env.schema.toml\n",
        )
        _write(tmp_path, "env.schema.toml", '[X]\ndescription = "x"\n')
        _commit(tmp_path, "v1")

        with pytest.raises(SchemaNotFoundError):
            schema_snapshot.load_schema_for_diff("does-not-exist", "HEAD")

    def test_missing_schema_file_at_a_revision_raises_clearly(
        self, tmp_path, monkeypatch
    ):
        _init_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        _write(
            tmp_path,
            "envshield.yml",
            "services:\n  api:\n    schema: env.schema.toml\n",
        )
        _commit(tmp_path, "no schema file yet")

        with pytest.raises(SchemaNotFoundError):
            schema_snapshot.load_schema_for_diff("api", "HEAD")

    def test_unresolvable_revision_raises_clearly(self, tmp_path, monkeypatch):
        _init_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        _write(
            tmp_path,
            "envshield.yml",
            "services:\n  api:\n    schema: env.schema.toml\n",
        )
        _write(tmp_path, "env.schema.toml", '[X]\ndescription = "x"\n')
        _commit(tmp_path, "v1")

        with pytest.raises(SchemaNotFoundError):
            schema_snapshot.load_schema_for_diff("api", "not-a-real-revision")

    def test_malformed_envshield_yml_at_a_revision_raises_a_clean_parse_error(
        self, tmp_path, monkeypatch
    ):
        """Regression: envshield.yml at a historical revision can be
        malformed YAML just as easily as a live one -- must surface as a
        clean ConfigParseError, not a raw yaml.YAMLError."""
        _init_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        _write(tmp_path, "envshield.yml", "services:\n  api: [unterminated\n")
        _commit(tmp_path, "malformed config")

        with pytest.raises(ConfigParseError):
            schema_snapshot.load_schema_for_diff("api", "HEAD")


class TestHistoricalExtendsMatchesLiveSemantics:
    """
    The invariant: historical schema loading behaves exactly like
    config_manager's live loader, except every read comes from the
    specified revision instead of disk. Each test here builds the same
    extends scenario twice -- once loaded live (config_manager.load_schema
    against the working tree) and once loaded historically (at HEAD, right
    after committing the identical files) -- and asserts they produce the
    byte-for-byte same effective schema.
    """

    def test_single_extends_base(self, tmp_path, monkeypatch):
        from envshield.config import manager as config_manager

        _init_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        _write(
            tmp_path,
            "envshield.yml",
            "services:\n  api:\n    schema: env.schema.toml\n",
        )
        _write(tmp_path, "base.schema.toml", '[SHARED]\ndescription = "shared"\n')
        _write(
            tmp_path,
            "env.schema.toml",
            'extends = "base.schema.toml"\n\n[OWN]\ndescription = "own"\n',
        )
        _commit(tmp_path, "v1")

        live = config_manager.load_schema(service_name="api")
        historical = schema_snapshot.load_schema_for_diff("api", "HEAD")

        assert live == historical

    def test_child_overrides_base_on_conflict(self, tmp_path, monkeypatch):
        from envshield.config import manager as config_manager

        _init_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        _write(
            tmp_path,
            "envshield.yml",
            "services:\n  api:\n    schema: env.schema.toml\n",
        )
        _write(tmp_path, "base.schema.toml", '[SHARED]\ndescription = "base version"\n')
        _write(
            tmp_path,
            "env.schema.toml",
            'extends = "base.schema.toml"\n\n[SHARED]\ndescription = "child version"\n',
        )
        _commit(tmp_path, "v1")

        live = config_manager.load_schema(service_name="api")
        historical = schema_snapshot.load_schema_for_diff("api", "HEAD")

        assert live == {"SHARED": {"description": "child version"}}
        assert live == historical

    def test_multiple_extends_later_entry_wins(self, tmp_path, monkeypatch):
        from envshield.config import manager as config_manager

        _init_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        _write(
            tmp_path,
            "envshield.yml",
            "services:\n  api:\n    schema: env.schema.toml\n",
        )
        _write(tmp_path, "base1.schema.toml", '[SHARED]\ndescription = "from base1"\n')
        _write(tmp_path, "base2.schema.toml", '[SHARED]\ndescription = "from base2"\n')
        _write(
            tmp_path,
            "env.schema.toml",
            'extends = ["base1.schema.toml", "base2.schema.toml"]\n',
        )
        _commit(tmp_path, "v1")

        live = config_manager.load_schema(service_name="api")
        historical = schema_snapshot.load_schema_for_diff("api", "HEAD")

        assert live == {"SHARED": {"description": "from base2"}}
        assert live == historical

    def test_chained_extends(self, tmp_path, monkeypatch):
        from envshield.config import manager as config_manager

        _init_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        _write(
            tmp_path,
            "envshield.yml",
            "services:\n  api:\n    schema: env.schema.toml\n",
        )
        _write(
            tmp_path, "grandbase.schema.toml", '[FROM_GRANDBASE]\ndescription = "x"\n'
        )
        _write(
            tmp_path,
            "base.schema.toml",
            'extends = "grandbase.schema.toml"\n\n[FROM_BASE]\ndescription = "x"\n',
        )
        _write(
            tmp_path,
            "env.schema.toml",
            'extends = "base.schema.toml"\n\n[OWN]\ndescription = "x"\n',
        )
        _commit(tmp_path, "v1")

        live = config_manager.load_schema(service_name="api")
        historical = schema_snapshot.load_schema_for_diff("api", "HEAD")

        assert set(live.keys()) == {"FROM_GRANDBASE", "FROM_BASE", "OWN"}
        assert live == historical

    def test_circular_extends_detected_identically(self, tmp_path, monkeypatch):
        from envshield.config import manager as config_manager
        from envshield.core.exceptions import SchemaParseError as LiveSchemaParseError

        _init_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        _write(
            tmp_path, "envshield.yml", "services:\n  api:\n    schema: a.schema.toml\n"
        )
        _write(tmp_path, "a.schema.toml", 'extends = "b.schema.toml"\n')
        _write(tmp_path, "b.schema.toml", 'extends = "a.schema.toml"\n')
        _commit(tmp_path, "v1")

        with pytest.raises(LiveSchemaParseError):
            config_manager.load_schema(service_name="api")
        with pytest.raises(SchemaParseError):
            schema_snapshot.load_schema_for_diff("api", "HEAD")

    def test_extends_escaping_the_project_rejected_identically(
        self, tmp_path, monkeypatch
    ):
        from envshield.config import manager as config_manager
        from envshield.core.exceptions import UnsafePathError as LiveUnsafePathError

        _init_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        _write(
            tmp_path,
            "envshield.yml",
            "services:\n  api:\n    schema: env.schema.toml\n",
        )
        _write(tmp_path, "env.schema.toml", 'extends = "../../../../etc/passwd"\n')
        _commit(tmp_path, "v1")

        with pytest.raises(LiveUnsafePathError):
            config_manager.load_schema(service_name="api")
        with pytest.raises(UnsafePathError):
            schema_snapshot.load_schema_for_diff("api", "HEAD")


class TestHistoricalLoadingNeverFallsBackToTheWorkingTree:
    """
    The hard invariant: once a real revision is given, nothing in this
    module may read from disk. Each test commits one version, then
    overwrites the SAME file on disk with a sentinel that would appear in
    the result if a fallback ever fired -- proving it doesn't by asserting
    the sentinel is absent.
    """

    def test_envshield_yml_never_falls_back(self, tmp_path, monkeypatch):
        _init_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        _write(
            tmp_path,
            "envshield.yml",
            "services:\n  api:\n    schema: env.schema.toml\n",
        )
        _write(tmp_path, "env.schema.toml", '[X]\ndescription = "x"\n')
        _commit(tmp_path, "v1")
        # Overwrite envshield.yml on disk to point at a schema that only
        # exists on disk, not at HEAD -- if this were ever read, the
        # historical load would succeed against the wrong file instead of
        # raising.
        _write(
            tmp_path,
            "envshield.yml",
            "services:\n  api:\n    schema: sentinel_only_on_disk.schema.toml\n",
        )
        _write(
            tmp_path,
            "sentinel_only_on_disk.schema.toml",
            '[SENTINEL]\ndescription = "x"\n',
        )

        schema = schema_snapshot.load_schema_for_diff("api", "HEAD")

        assert schema == {"X": {"description": "x"}}
        assert "SENTINEL" not in schema

    def test_schema_file_never_falls_back(self, tmp_path, monkeypatch):
        _init_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        _write(
            tmp_path,
            "envshield.yml",
            "services:\n  api:\n    schema: env.schema.toml\n",
        )
        _write(tmp_path, "env.schema.toml", '[COMMITTED]\ndescription = "x"\n')
        _commit(tmp_path, "v1")
        _write(tmp_path, "env.schema.toml", '[SENTINEL_ON_DISK]\ndescription = "x"\n')

        schema = schema_snapshot.load_schema_for_diff("api", "HEAD")

        assert schema == {"COMMITTED": {"description": "x"}}
        assert "SENTINEL_ON_DISK" not in schema

    def test_extends_dependency_never_falls_back(self, tmp_path, monkeypatch):
        _init_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        _write(
            tmp_path,
            "envshield.yml",
            "services:\n  api:\n    schema: env.schema.toml\n",
        )
        _write(tmp_path, "base.schema.toml", '[COMMITTED_BASE]\ndescription = "x"\n')
        _write(tmp_path, "env.schema.toml", 'extends = "base.schema.toml"\n')
        _commit(tmp_path, "v1")
        _write(tmp_path, "base.schema.toml", '[SENTINEL_ON_DISK]\ndescription = "x"\n')

        schema = schema_snapshot.load_schema_for_diff("api", "HEAD")

        assert schema == {"COMMITTED_BASE": {"description": "x"}}
        assert "SENTINEL_ON_DISK" not in schema


class TestLexicalContainment:
    """
    Deliberately distinct from config_manager._ensure_within_project
    (which is realpath/live-cwd based, inapplicable to historical Git blob
    content). Uses posixpath exclusively so behavior is deterministic
    regardless of host platform, matching Git's always-POSIX paths.
    """

    def test_parent_traversal_is_rejected(self):
        with pytest.raises(UnsafePathError):
            schema_snapshot._ensure_lexically_within_project("../outside.toml", "HEAD")

    def test_double_parent_traversal_is_rejected(self):
        with pytest.raises(UnsafePathError):
            schema_snapshot._ensure_lexically_within_project(
                "../../outside.toml", "HEAD"
            )

    def test_traversal_via_a_nested_join_is_rejected(self):
        with pytest.raises(UnsafePathError):
            schema_snapshot._ensure_lexically_within_project(
                "a/../../outside.toml", "HEAD"
            )

    def test_absolute_path_is_rejected(self):
        with pytest.raises(UnsafePathError):
            schema_snapshot._ensure_lexically_within_project("/etc/passwd", "HEAD")

    def test_a_name_that_merely_starts_with_dot_dot_is_not_rejected(self):
        """'..foo/schema.toml' is a real filename component, not a
        traversal token -- must not be rejected on a naive
        startswith('..') check."""
        result = schema_snapshot._ensure_lexically_within_project(
            "..foo/schema.toml", "HEAD"
        )
        assert result == "..foo/schema.toml"

    def test_a_normal_relative_path_is_accepted(self):
        result = schema_snapshot._ensure_lexically_within_project(
            "services/api/env.schema.toml", "HEAD"
        )
        assert result == "services/api/env.schema.toml"

    def test_an_internal_dotdot_that_stays_within_bounds_is_accepted(self):
        """'a/../b' normalizes to 'b' -- never actually escapes anything."""
        result = schema_snapshot._ensure_lexically_within_project("a/../b", "HEAD")
        assert result == "a/../b"
