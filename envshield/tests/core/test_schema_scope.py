# envshield/tests/core/test_schema_scope.py
"""
Pure projection tests for schema_scope.project -- a merged system schema
plus the registered services using that schema file, in; one service's
effective view, out. No filesystem, no envshield.yml.
"""

import pytest

from envshield.core import schema_scope
from envshield.core.exceptions import (
    SchemaParseError,
    SchemaScopeError,
    SecretDefaultConflictError,
)

PATH = "env.schema.toml"


def _project(merged, service, users, registered=None):
    return schema_scope.project(
        merged, PATH, service, users, registered=registered or users
    )


class TestSingleUserSchema:
    def test_unscoped_schema_is_returned_unchanged(self):
        merged = {
            "DATABASE_URL": {"secret": True},
            "PORT": {"defaultValue": "8000"},
        }
        view = _project(merged, "api", ["api"])
        assert view.fields == merged
        assert view.system == merged
        assert not view.shared
        assert not view.scoped

    def test_unscoped_secret_is_allowed_when_not_shared(self):
        view = _project({"KEY": {"secret": True}}, "api", ["api"])
        assert view.fields == {"KEY": {"secret": True}}

    def test_scope_naming_its_only_user_is_valid_and_stripped(self):
        view = _project({"KEY": {"secret": True, "services": ["api"]}}, "api", ["api"])
        assert view.fields == {"KEY": {"secret": True}}
        assert view.grants == {"KEY": frozenset({"api"})}

    def test_scope_naming_another_service_fails_even_when_not_shared(self):
        with pytest.raises(SchemaScopeError, match="worker"):
            _project({"KEY": {"services": ["worker"]}}, "api", ["api"])


class TestSharedSchemaProjection:
    MERGED = {
        "DATABASE_URL": {"type": "url", "secret": True, "services": ["api", "worker"]},
        "STRIPE_SECRET_KEY": {"secret": True, "services": ["api"]},
        "LOG_LEVEL": {"enum": ["debug", "info"], "defaultValue": "info"},
        "PORT": {
            "type": "port",
            "services": {
                "api": {"defaultValue": "8000"},
                "worker": {"defaultValue": "9000", "description": "worker port"},
            },
        },
    }

    def test_api_view(self):
        view = _project(self.MERGED, "api", ["api", "worker"])
        assert view.fields == {
            "DATABASE_URL": {"type": "url", "secret": True},
            "STRIPE_SECRET_KEY": {"secret": True},
            "LOG_LEVEL": {"enum": ["debug", "info"], "defaultValue": "info"},
            "PORT": {"type": "port", "defaultValue": "8000"},
        }
        assert view.shared and view.scoped

    def test_worker_view_filters_and_applies_overrides(self):
        view = _project(self.MERGED, "worker", ["api", "worker"])
        assert view.fields == {
            "DATABASE_URL": {"type": "url", "secret": True},
            "LOG_LEVEL": {"enum": ["debug", "info"], "defaultValue": "info"},
            "PORT": {
                "type": "port",
                "defaultValue": "9000",
                "description": "worker port",
            },
        }

    def test_status_distinguishes_in_scope_out_of_scope_and_undefined(self):
        view = _project(self.MERGED, "worker", ["api", "worker"])
        assert view.status("DATABASE_URL") == "in_scope"
        assert view.status("STRIPE_SECRET_KEY") == "out_of_scope"
        assert view.status("NOPE") == "undefined"
        assert view.granted_to("STRIPE_SECRET_KEY") == frozenset({"api"})
        assert view.granted_to("LOG_LEVEL") == frozenset({"api", "worker"})
        assert view.granted_to("NOPE") is None

    def test_system_keeps_every_variable_without_services_key(self):
        view = _project(self.MERGED, "worker", ["api", "worker"])
        assert set(view.system) == set(self.MERGED)
        assert all("services" not in f for f in view.system.values())
        assert all("services" not in f for f in view.fields.values())

    def test_projection_does_not_mutate_input(self):
        import copy

        before = copy.deepcopy(self.MERGED)
        _project(self.MERGED, "worker", ["api", "worker"])
        assert self.MERGED == before

    def test_projection_is_deterministic_regardless_of_user_order(self):
        a = _project(self.MERGED, "api", ["api", "worker"])
        b = _project(self.MERGED, "api", ["worker", "api"])
        assert a.fields == b.fields and a.grants == b.grants
        assert list(a.fields) == list(b.fields)

    def test_override_on_an_existing_system_default(self):
        merged = {
            "PORT": {
                "type": "int",
                "defaultValue": "8000",
                "services": {"api": {}, "worker": {"defaultValue": "9000"}},
            }
        }
        users = ["api", "worker"]
        assert _project(merged, "api", users).fields["PORT"]["defaultValue"] == "8000"
        assert (
            _project(merged, "worker", users).fields["PORT"]["defaultValue"] == "9000"
        )


class TestSharedSchemaFailsClosed:
    USERS = ["api", "worker"]

    def test_unscoped_secret_in_shared_schema(self):
        with pytest.raises(SchemaScopeError, match="STRIPE_SECRET_KEY"):
            _project({"STRIPE_SECRET_KEY": {"secret": True}}, "api", self.USERS)

    def test_unscoped_secret_fails_for_every_service_not_just_one(self):
        merged = {"A": {"secret": True, "services": ["api"]}, "B": {"secret": True}}
        for service in self.USERS:
            with pytest.raises(SchemaScopeError, match="'B'"):
                _project(merged, service, self.USERS)

    def test_broken_grant_for_worker_fails_the_api_load_too(self):
        merged = {"A": {"services": ["api"]}, "B": {"services": ["wroker"]}}
        with pytest.raises(SchemaScopeError, match="wroker"):
            _project(merged, "api", self.USERS)

    def test_unknown_service_message_lists_users(self):
        with pytest.raises(SchemaScopeError) as exc:
            _project({"A": {"services": ["billing"]}}, "api", self.USERS)
        assert "billing" in str(exc.value)
        assert "api, worker" in str(exc.value)

    def test_registered_service_using_another_schema_is_named_as_such(self):
        with pytest.raises(SchemaScopeError, match="different schema"):
            _project(
                {"A": {"services": ["frontend"]}},
                "api",
                self.USERS,
                registered=self.USERS + ["frontend"],
            )

    def test_stale_scope_hint_mentions_removed_or_renamed(self):
        with pytest.raises(SchemaScopeError, match="removed or renamed"):
            _project({"A": {"services": ["gone"]}}, "api", self.USERS)

    @pytest.mark.parametrize("scope", [[], {}])
    def test_empty_grant(self, scope):
        with pytest.raises(SchemaScopeError, match="empty"):
            _project({"A": {"services": scope}}, "api", self.USERS)

    @pytest.mark.parametrize(
        "scope",
        ["api", 3, ["api", 3], ["api", "api"], {"api": "8000"}],
    )
    def test_malformed_scope(self, scope):
        with pytest.raises(SchemaScopeError):
            _project({"A": {"services": scope}}, "api", self.USERS)

    @pytest.mark.parametrize(
        "key", ["type", "secret", "enum", "pattern", "requiredIf", "services"]
    )
    def test_invalid_override_key(self, key):
        merged = {"A": {"services": {"api": {key: "x"}}}}
        with pytest.raises(SchemaScopeError, match="defaultValue and description"):
            _project(merged, "api", self.USERS)

    def test_secret_override_with_a_default_is_rejected_for_every_service(self):
        merged = {
            "TOKEN": {
                "secret": True,
                "services": {"api": {}, "worker": {"defaultValue": "leak"}},
            }
        }
        for service in self.USERS:
            with pytest.raises(SecretDefaultConflictError, match="TOKEN"):
                _project(merged, service, self.USERS)

    def test_secret_override_with_an_empty_default_is_allowed(self):
        merged = {"TOKEN": {"secret": True, "services": {"api": {"defaultValue": ""}}}}
        view = _project(merged, "api", self.USERS)
        assert view.fields["TOKEN"] == {"secret": True, "defaultValue": ""}

    def test_scope_error_is_a_schema_parse_error(self):
        assert issubclass(SchemaScopeError, SchemaParseError)


class TestRequiredIfScope:
    USERS = ["api", "worker"]

    def test_dependency_granted_everywhere_the_field_is(self):
        merged = {
            "PAYMENTS": {"services": ["api"]},
            "STRIPE_KEY": {
                "secret": True,
                "services": ["api"],
                "requiredIf": {"var": "PAYMENTS", "equals": "true"},
            },
        }
        assert "STRIPE_KEY" in _project(merged, "api", self.USERS).fields

    def test_global_dependency_always_covers(self):
        merged = {
            "MODE": {},
            "X": {"services": ["worker"], "requiredIf": {"var": "MODE"}},
        }
        _project(merged, "worker", self.USERS)

    def test_dependency_not_granted_where_field_is_fails(self):
        merged = {
            "PAYMENTS": {"services": ["api"]},
            "X": {"requiredIf": {"var": "PAYMENTS"}},
        }
        with pytest.raises(SchemaScopeError, match="worker"):
            _project(merged, "api", self.USERS)

    def test_undefined_dependency_keeps_legacy_semantics(self):
        merged = {"X": {"requiredIf": {"var": "NOT_DECLARED"}}}
        assert "X" in _project(merged, "api", self.USERS).fields


def test_non_table_top_level_value_passes_through_untouched():
    """Legacy: a stray non-table value was never validated here -- still isn't."""
    view = _project({"ODD": "value"}, "api", ["api", "worker"])
    assert view.fields == {"ODD": "value"}
