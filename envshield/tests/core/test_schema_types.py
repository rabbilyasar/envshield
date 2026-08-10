# envshield/tests/core/test_schema_types.py
from envshield.core import schema_types


def test_resolve_field_type_defaults_to_string():
    assert schema_types.resolve_field_type({}) == "string"


def test_resolve_field_type_enum_wins_regardless_of_explicit_type():
    assert (
        schema_types.resolve_field_type({"enum": ["a", "b"], "type": "string"})
        == "enum"
    )


def test_resolve_field_type_uses_explicit_type():
    assert schema_types.resolve_field_type({"type": "int"}) == "int"


def test_validate_value_enum_rejects_value_outside_the_list():
    schema = {"enum": ["debug", "info", "warn", "error"]}
    assert schema_types.validate_value("verbose", schema) is not None
    assert schema_types.validate_value("info", schema) is None


def test_validate_value_int_rejects_non_numeric():
    schema = {"type": "int"}
    assert schema_types.validate_value("abc", schema) is not None
    assert schema_types.validate_value("-42", schema) is None


def test_validate_value_bool_only_accepts_true_false():
    schema = {"type": "bool"}
    assert schema_types.validate_value("yes", schema) is not None
    assert schema_types.validate_value("true", schema) is None
    assert schema_types.validate_value("False", schema) is None


def test_validate_value_port_enforces_range():
    schema = {"type": "port"}
    assert schema_types.validate_value("0", schema) is not None
    assert schema_types.validate_value("70000", schema) is not None
    assert schema_types.validate_value("8080", schema) is None


def test_validate_value_url_requires_scheme_and_host():
    schema = {"type": "url"}
    assert schema_types.validate_value("not a url", schema) is not None
    assert schema_types.validate_value("https://example.com/api", schema) is None


def test_validate_value_email_requires_at_and_domain():
    schema = {"type": "email"}
    assert schema_types.validate_value("not-an-email", schema) is not None
    assert schema_types.validate_value("dev@example.com", schema) is None


def test_validate_value_pattern_applies_on_top_of_type():
    schema = {"pattern": r"^v\d+\.\d+\.\d+$"}
    assert schema_types.validate_value("not-a-version", schema) is not None
    assert schema_types.validate_value("v1.2.3", schema) is None


def test_validate_value_string_with_no_constraints_always_passes():
    assert schema_types.validate_value("anything at all", {}) is None


class TestValidateValueNeverEchoesTheSuppliedValue:
    """
    Regression coverage for the security invariant behind P0-3: a
    validation error describes what's wrong, but must never disclose what
    value was supplied -- for every type this function validates, not just
    the reported case, and regardless of the field's `secret` flag (the
    fix is unconditional; there is no secret-only special case to test
    around). Uses a distinctive sentinel per test so a leak is unambiguous
    rather than something that could coincidentally match constraint text.
    """

    SENTINEL = "SUPER_SECRET_TEST_VALUE_12345"

    def _assert_rejected_without_echo(self, value, field_schema):
        error = schema_types.validate_value(value, field_schema)
        assert error is not None, "expected this value to be rejected"
        assert value not in error
        return error

    def test_enum_rejection_does_not_echo_the_value(self):
        error = self._assert_rejected_without_echo(
            self.SENTINEL, {"enum": ["debug", "info", "warn", "error"]}
        )
        assert "must be one of: debug, info, warn, error" in error

    def test_int_rejection_does_not_echo_the_value(self):
        error = self._assert_rejected_without_echo(self.SENTINEL, {"type": "int"})
        assert "must be an integer" in error

    def test_float_rejection_does_not_echo_the_value(self):
        error = self._assert_rejected_without_echo(self.SENTINEL, {"type": "float"})
        assert "must be a number" in error

    def test_bool_rejection_does_not_echo_the_value(self):
        error = self._assert_rejected_without_echo(self.SENTINEL, {"type": "bool"})
        assert "must be 'true' or 'false'" in error

    def test_port_rejection_does_not_echo_the_value(self):
        error = self._assert_rejected_without_echo(self.SENTINEL, {"type": "port"})
        assert "must be a port number from 1-65535" in error

    def test_url_rejection_does_not_echo_the_value(self):
        error = self._assert_rejected_without_echo(self.SENTINEL, {"type": "url"})
        assert "must be a valid URL" in error

    def test_email_rejection_does_not_echo_the_value(self):
        error = self._assert_rejected_without_echo(self.SENTINEL, {"type": "email"})
        assert "must be a valid email address" in error

    def test_pattern_rejection_does_not_echo_the_value(self):
        pattern = r"^v\d+\.\d+\.\d+$"
        error = self._assert_rejected_without_echo(self.SENTINEL, {"pattern": pattern})
        assert "must match pattern" in error
        # The pattern itself is schema-authored (committed in
        # env.schema.toml, readable by anyone who can read the schema at
        # all) -- not user-supplied -- so it's legitimate constraint
        # context to keep, unlike the value.
        assert repr(pattern) in error

    def test_a_value_containing_quotes_does_not_leak(self):
        value = f'"{self.SENTINEL}"\''
        error = schema_types.validate_value(value, {"type": "int"})
        assert error is not None
        assert self.SENTINEL not in error
        assert value not in error

    def test_a_value_containing_whitespace_does_not_leak(self):
        value = f"  {self.SENTINEL}  with spaces  "
        error = schema_types.validate_value(value, {"type": "port"})
        assert error is not None
        assert self.SENTINEL not in error

    def test_a_secret_flagged_fields_invalid_value_does_not_leak(self):
        """
        The fix is unconditional, but this exercises the exact reported
        scenario directly: a field explicitly marked `secret` still gets
        the same value-free message, not a different one -- there's no
        separate 'secret path' to regress independently of the general one.
        """
        error = schema_types.validate_value(
            self.SENTINEL, {"type": "port", "secret": True}
        )
        assert error is not None
        assert self.SENTINEL not in error

    def test_valid_values_are_unaffected(self):
        """The fix must not change what's accepted -- only what's said when
        something is rejected."""
        assert schema_types.validate_value("info", {"enum": ["info", "warn"]}) is None
        assert schema_types.validate_value("42", {"type": "int"}) is None
        assert schema_types.validate_value("8080", {"type": "port"}) is None
        assert (
            schema_types.validate_value("https://example.com", {"type": "url"}) is None
        )


def test_is_required_now_true_when_no_default_and_no_condition():
    assert schema_types.is_required_now({}, {}) is True


def test_is_required_now_false_when_default_present():
    assert schema_types.is_required_now({"defaultValue": "x"}, {}) is False


def test_is_required_now_respects_required_if_condition_met():
    field_schema = {"requiredIf": {"var": "FEATURE_X_ENABLED", "equals": "true"}}
    assert (
        schema_types.is_required_now(field_schema, {"FEATURE_X_ENABLED": "true"})
        is True
    )


def test_is_required_now_respects_required_if_condition_not_met():
    field_schema = {"requiredIf": {"var": "FEATURE_X_ENABLED", "equals": "true"}}
    assert (
        schema_types.is_required_now(field_schema, {"FEATURE_X_ENABLED": "false"})
        is False
    )
    assert schema_types.is_required_now(field_schema, {}) is False
