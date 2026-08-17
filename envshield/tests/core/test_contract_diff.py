# envshield/tests/core/test_contract_diff.py
from envshield.core import contract_diff


def _change_for(result, variable):
    matches = [c for c in result.changes if c.variable == variable]
    assert len(matches) == 1, (
        f"expected exactly one change for {variable!r}, got {matches}"
    )
    return matches[0]


class TestAddedVariables:
    def test_new_unconditionally_required_variable_is_breaking(self):
        result = contract_diff.diff_schemas({}, {"NEW": {"description": "x"}})

        change = _change_for(result, "NEW")
        assert change.category == "breaking"
        assert result.has_breaking_changes is True

    def test_new_variable_with_a_default_is_non_breaking(self):
        """A defaulted field is never required (is_required_now returns
        False unconditionally whenever defaultValue is present, matching
        _requiredness_state's 'defaulted' state) -- adding one can't
        invalidate any config that was valid before."""
        result = contract_diff.diff_schemas(
            {}, {"NEW": {"description": "x", "defaultValue": "y"}}
        )

        change = _change_for(result, "NEW")
        assert change.category == "non_breaking"

    def test_new_conditionally_required_variable_is_informational_not_breaking(self):
        result = contract_diff.diff_schemas(
            {},
            {
                "NEW": {
                    "description": "x",
                    "requiredIf": {"var": "FLAG", "equals": "true"},
                }
            },
        )

        change = _change_for(result, "NEW")
        assert change.category == "informational"
        assert result.has_breaking_changes is False


class TestRemovedVariables:
    def test_removed_variable_is_informational_not_breaking(self):
        result = contract_diff.diff_schemas({"OLD": {"description": "x"}}, {})

        change = _change_for(result, "OLD")
        assert change.category == "informational"
        assert result.has_breaking_changes is False


class TestRequirednessTransitions:
    def test_conditional_to_unconditional_is_breaking(self):
        a = {"V": {"description": "x", "requiredIf": {"var": "F", "equals": "true"}}}
        b = {"V": {"description": "x"}}

        result = contract_diff.diff_schemas(a, b)

        change = _change_for(result, "V")
        assert change.category == "breaking"

    def test_unconditional_to_conditional_is_not_breaking(self):
        a = {"V": {"description": "x"}}
        b = {"V": {"description": "x", "requiredIf": {"var": "F", "equals": "true"}}}

        result = contract_diff.diff_schemas(a, b)

        change = _change_for(result, "V")
        assert change.category == "non_breaking"

    def test_changed_condition_while_staying_conditional_requires_review(self):
        a = {"V": {"description": "x", "requiredIf": {"var": "F1", "equals": "true"}}}
        b = {"V": {"description": "x", "requiredIf": {"var": "F2", "equals": "true"}}}

        result = contract_diff.diff_schemas(a, b)

        change = _change_for(result, "V")
        assert change.category == "requires_review"

    def test_editing_an_existing_default_value_does_not_trigger_a_requiredness_change(
        self,
    ):
        """Both sides are 'defaulted' -- only the default's value itself
        changed, which is its own category, not a requiredness change."""
        a = {"V": {"description": "x", "defaultValue": "1"}}
        b = {"V": {"description": "x", "defaultValue": "2"}}

        result = contract_diff.diff_schemas(a, b)

        change = _change_for(result, "V")
        assert change.category == "default_changed"

    def test_losing_a_bare_default_with_no_requiredif_is_breaking(self):
        """'optional -> required': the variable had a fallback and now has
        none -- any config that omitted it, relying on the default, is now
        invalid. This is provable, so it's breaking, not the softer
        default_changed bucket."""
        a = {"V": {"description": "x", "defaultValue": "1"}}
        b = {"V": {"description": "x"}}

        result = contract_diff.diff_schemas(a, b)

        change = _change_for(result, "V")
        assert change.category == "breaking"
        assert result.has_breaking_changes is True

    def test_gaining_a_bare_default_with_no_requiredif_is_non_breaking(self):
        """'required -> optional': every config that already set it
        explicitly still works, and one that didn't now has a fallback."""
        a = {"V": {"description": "x"}}
        b = {"V": {"description": "x", "defaultValue": "1"}}

        result = contract_diff.diff_schemas(a, b)

        change = _change_for(result, "V")
        assert change.category == "non_breaking"
        assert result.has_breaking_changes is False

    def test_losing_a_default_while_gaining_a_requiredif_requires_review(self):
        """Neither provably breaking (the condition might never hold) nor
        provably safe (it might hold for many configs) -- schema-only diff
        can't evaluate the condition, so this is genuinely undecidable."""
        a = {"V": {"description": "x", "defaultValue": "1"}}
        b = {
            "V": {
                "description": "x",
                "requiredIf": {"var": "F", "equals": "true"},
            }
        }

        result = contract_diff.diff_schemas(a, b)

        change = _change_for(result, "V")
        assert change.category == "requires_review"

    def test_gaining_a_default_from_conditional_is_non_breaking(self):
        """The variable widens from 'required under some condition' to
        'never required' -- strictly safer."""
        a = {
            "V": {
                "description": "x",
                "requiredIf": {"var": "F", "equals": "true"},
            }
        }
        b = {"V": {"description": "x", "defaultValue": "1"}}

        result = contract_diff.diff_schemas(a, b)

        change = _change_for(result, "V")
        assert change.category == "non_breaking"


class TestTypeChanges:
    def test_narrowing_from_string_to_a_specific_type_is_breaking(self):
        a = {"V": {"description": "x", "type": "string"}}
        b = {"V": {"description": "x", "type": "port"}}

        result = contract_diff.diff_schemas(a, b)

        assert _change_for(result, "V").category == "breaking"

    def test_widening_from_a_specific_type_to_string_is_not_breaking(self):
        a = {"V": {"description": "x", "type": "port"}}
        b = {"V": {"description": "x", "type": "string"}}

        result = contract_diff.diff_schemas(a, b)

        assert _change_for(result, "V").category == "non_breaking"

    def test_change_between_two_incomparable_specific_types_is_breaking(self):
        a = {"V": {"description": "x", "type": "port"}}
        b = {"V": {"description": "x", "type": "email"}}

        result = contract_diff.diff_schemas(a, b)

        assert _change_for(result, "V").category == "breaking"

    def test_same_type_on_both_sides_produces_no_type_change_entry(self):
        a = {"V": {"description": "x", "type": "port"}}
        b = {"V": {"description": "x", "type": "port"}}

        result = contract_diff.diff_schemas(a, b)

        assert result.changes == []


class TestPatternChanges:
    def test_adding_a_pattern_where_none_existed_is_breaking(self):
        a = {"V": {"description": "x"}}
        b = {"V": {"description": "x", "pattern": "^v[0-9]+$"}}

        result = contract_diff.diff_schemas(a, b)

        assert _change_for(result, "V").category == "breaking"

    def test_removing_a_pattern_is_not_breaking(self):
        a = {"V": {"description": "x", "pattern": "^v[0-9]+$"}}
        b = {"V": {"description": "x"}}

        result = contract_diff.diff_schemas(a, b)

        assert _change_for(result, "V").category == "non_breaking"

    def test_changing_one_pattern_to_a_different_pattern_requires_review(self):
        a = {"V": {"description": "x", "pattern": "^v[0-9]+$"}}
        b = {"V": {"description": "x", "pattern": "^[0-9]+$"}}

        result = contract_diff.diff_schemas(a, b)

        assert _change_for(result, "V").category == "requires_review"


class TestEnumChanges:
    def test_removing_an_allowed_value_is_breaking(self):
        a = {"V": {"description": "x", "enum": ["debug", "info"]}}
        b = {"V": {"description": "x", "enum": ["info"]}}

        result = contract_diff.diff_schemas(a, b)

        change = _change_for(result, "V")
        assert change.category == "breaking"
        assert change.detail["removed"] == ["debug"]

    def test_adding_an_allowed_value_is_not_breaking(self):
        a = {"V": {"description": "x", "enum": ["info"]}}
        b = {"V": {"description": "x", "enum": ["debug", "info"]}}

        result = contract_diff.diff_schemas(a, b)

        change = _change_for(result, "V")
        assert change.category == "non_breaking"
        assert change.detail["added"] == ["debug"]

    def test_enum_to_enum_with_no_value_change_produces_no_entry(self):
        a = {"V": {"description": "x", "enum": ["debug", "info"]}}
        b = {"V": {"description": "x", "enum": ["info", "debug"]}}

        result = contract_diff.diff_schemas(a, b)

        assert result.changes == []


class TestSecretClassificationChanges:
    def test_tightening_from_false_to_true_is_security_category(self):
        a = {"V": {"description": "x", "secret": False}}
        b = {"V": {"description": "x", "secret": True}}

        result = contract_diff.diff_schemas(a, b)

        change = _change_for(result, "V")
        assert change.category == "security"
        assert change.detail["severity"] == "tightened"
        assert result.has_breaking_changes is False

    def test_weakening_from_true_to_false_is_security_category_with_stronger_wording(
        self,
    ):
        a = {"V": {"description": "x", "secret": True}}
        b = {"V": {"description": "x", "secret": False}}

        result = contract_diff.diff_schemas(a, b)

        change = _change_for(result, "V")
        assert change.category == "security"
        assert change.detail["severity"] == "weakened"
        assert "WEAKENED" in change.description


class TestDefaultChanges:
    def test_default_value_change_is_its_own_category_not_breaking(self):
        a = {"V": {"description": "x", "defaultValue": "old"}}
        b = {"V": {"description": "x", "defaultValue": "new"}}

        result = contract_diff.diff_schemas(a, b)

        change = _change_for(result, "V")
        assert change.category == "default_changed"
        assert result.has_breaking_changes is False

    def test_toml_native_int_vs_string_of_the_same_number_produces_no_entry(self):
        """
        Regression: TOML lets a defaultValue be written as either a native
        integer (8080) or a quoted string ("8080") -- these represent the
        exact same default, and must not be reported as a change purely
        because two revisions happened to spell it differently.
        """
        a = {"V": {"description": "x", "type": "port", "defaultValue": 8080}}
        b = {"V": {"description": "x", "type": "port", "defaultValue": "8080"}}

        result = contract_diff.diff_schemas(a, b)

        assert result.changes == []

    def test_bool_default_case_difference_produces_no_entry(self):
        a = {"V": {"description": "x", "type": "bool", "defaultValue": "TRUE"}}
        b = {"V": {"description": "x", "type": "bool", "defaultValue": "true"}}

        result = contract_diff.diff_schemas(a, b)

        assert result.changes == []

    def test_float_representation_difference_produces_no_entry(self):
        a = {"V": {"description": "x", "type": "float", "defaultValue": "1.50"}}
        b = {"V": {"description": "x", "type": "float", "defaultValue": 1.5}}

        result = contract_diff.diff_schemas(a, b)

        assert result.changes == []

    def test_genuinely_different_int_defaults_still_produce_default_changed(self):
        a = {"V": {"description": "x", "type": "port", "defaultValue": 8080}}
        b = {"V": {"description": "x", "type": "port", "defaultValue": 9090}}

        result = contract_diff.diff_schemas(a, b)

        change = _change_for(result, "V")
        assert change.category == "default_changed"
        assert change.detail == {"before": 8080, "after": 9090}

    def test_genuinely_different_bool_defaults_still_produce_default_changed(self):
        a = {"V": {"description": "x", "type": "bool", "defaultValue": "true"}}
        b = {"V": {"description": "x", "type": "bool", "defaultValue": "false"}}

        result = contract_diff.diff_schemas(a, b)

        assert _change_for(result, "V").category == "default_changed"

    def test_gaining_a_default_where_there_was_none_is_a_requiredness_change_not_default_changed(
        self,
    ):
        """Presence/absence of a defaultValue is now fully owned by the
        requiredness-transition axis (see TestRequirednessTransitions) --
        _defaults_differ must not also report it as default_changed,
        which would double-report the same underlying fact."""
        a = {"V": {"description": "x"}}
        b = {"V": {"description": "x", "defaultValue": "8080"}}

        result = contract_diff.diff_schemas(a, b)

        change = _change_for(result, "V")
        assert change.category == "non_breaking"
        assert len(result.changes) == 1


class TestCosmeticChangesAreNotReported:
    def test_description_only_change_produces_no_entry(self):
        a = {"V": {"description": "old text"}}
        b = {"V": {"description": "new text"}}

        result = contract_diff.diff_schemas(a, b)

        assert result.changes == []


class TestContractDiffResultShape:
    def test_to_dict_reflects_has_breaking_changes(self):
        result = contract_diff.diff_schemas({}, {"NEW": {"description": "x"}})

        as_dict = result.to_dict()

        assert as_dict["has_breaking_changes"] is True
        assert as_dict["changes"][0]["variable"] == "NEW"
        assert as_dict["changes"][0]["category"] == "breaking"

    def test_identical_schemas_produce_no_changes(self):
        schema = {"V": {"description": "x", "type": "port", "secret": True}}

        result = contract_diff.diff_schemas(schema, dict(schema))

        assert result.changes == []
        assert result.has_breaking_changes is False


class TestBlockingChanges:
    def test_breaking_change_blocks_under_the_default_set(self):
        result = contract_diff.diff_schemas({}, {"NEW": {"description": "x"}})

        assert result.has_blocking_changes() is True
        as_dict = result.to_dict()
        assert as_dict["has_blocking_changes"] is True
        assert as_dict["changes"][0]["blocking"] is True

    def test_secret_tightened_never_blocks_even_when_security_is_included(self):
        a = {"V": {"description": "x", "secret": False}}
        b = {"V": {"description": "x", "secret": True}}

        result = contract_diff.diff_schemas(a, b)

        assert result.has_blocking_changes() is False
        as_dict = result.to_dict()
        assert as_dict["has_blocking_changes"] is False
        assert as_dict["changes"][0]["blocking"] is False

    def test_secret_weakened_blocks_under_the_default_set(self):
        a = {"V": {"description": "x", "secret": True}}
        b = {"V": {"description": "x", "secret": False}}

        result = contract_diff.diff_schemas(a, b)

        assert result.has_blocking_changes() is True
        as_dict = result.to_dict()
        assert as_dict["has_blocking_changes"] is True
        assert as_dict["changes"][0]["blocking"] is True

    def test_requires_review_blocks_under_the_default_set(self):
        a = {"V": {"description": "x", "pattern": "^v[0-9]+$"}}
        b = {"V": {"description": "x", "pattern": "^[0-9]+$"}}

        result = contract_diff.diff_schemas(a, b)

        assert result.has_blocking_changes() is True

    def test_default_changed_and_non_breaking_never_block_by_default(self):
        a = {"V": {"description": "x", "defaultValue": "old"}}
        b = {"V": {"description": "x", "defaultValue": "new"}}

        result = contract_diff.diff_schemas(a, b)

        assert result.has_blocking_changes() is False

    def test_narrowing_the_blocking_set_excludes_requires_review(self):
        a = {"V": {"description": "x", "pattern": "^v[0-9]+$"}}
        b = {"V": {"description": "x", "pattern": "^[0-9]+$"}}

        result = contract_diff.diff_schemas(a, b)

        assert result.has_blocking_changes(frozenset({"breaking"})) is False
