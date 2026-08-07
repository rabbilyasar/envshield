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
