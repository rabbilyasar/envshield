# envshield/tests/core/test_dependency_diff.py
from envshield.core import dependency_diff
from envshield.core.discovery import DiscoveredVariableUsage


def _usage(variable, file_path="app.py", line=1, access_type="os.environ.get"):
    return DiscoveredVariableUsage(
        variable=variable,
        file_path=file_path,
        line=line,
        language="python",
        access_type=access_type,
        confidence="high",
    )


class TestFindNewUsages:
    def test_a_usage_absent_at_a_is_new(self):
        result = dependency_diff.find_new_usages([], [_usage("FOO")])
        assert [u.variable for u in result] == ["FOO"]

    def test_a_usage_present_at_both_is_not_new(self):
        result = dependency_diff.find_new_usages([_usage("FOO")], [_usage("FOO")])
        assert result == []

    def test_identity_ignores_access_type(self):
        """
        Locked decision: `variable` alone is the identity, not
        (file_path, variable, access_type). Rewriting os.environ.get("FOO")
        as os.environ["FOO"] must not be reported as a new dependency.
        """
        old = _usage("FOO", access_type="os.environ.get")
        new = _usage("FOO", access_type="os.environ[]")

        result = dependency_diff.find_new_usages([old], [new])

        assert result == []

    def test_identity_ignores_line_number(self):
        old = _usage("FOO", line=1)
        new = _usage("FOO", line=42)

        result = dependency_diff.find_new_usages([old], [new])

        assert result == []

    def test_same_variable_in_a_different_file_is_not_new(self):
        """
        Locked decision: identity is `variable` alone, not
        (file_path, variable). A file move/rename is reported by
        git_utils.list_changed_files (which passes --no-renames) as the old
        path deleted and the new path added -- under a file-inclusive
        identity, every usage in the moved file would be misreported as a
        newly introduced dependency even though nothing about the
        dependency itself changed. The accepted trade-off is the inverse:
        an independently new call site for a variable already used
        elsewhere in the project goes undetected (see the same-file case
        above, which already accepted this for call sites within one file).
        """
        old = _usage("FOO", file_path="a.py")
        new = _usage("FOO", file_path="b.py")

        result = dependency_diff.find_new_usages([old], [new])

        assert result == []

    def test_a_second_new_call_site_for_an_already_used_variable_in_the_same_file_is_not_flagged(
        self,
    ):
        """
        Accepted consequence of the (file_path, variable) identity: a
        genuinely new, independent call site for a variable already used
        elsewhere in the same file isn't detected as new -- the identity
        is file+variable, not call-site.
        """
        old = [_usage("FOO", line=1)]
        new = [_usage("FOO", line=1), _usage("FOO", line=99)]

        result = dependency_diff.find_new_usages(old, new)

        assert result == []

    def test_a_file_new_at_b_contributes_every_usage_as_new(self):
        result = dependency_diff.find_new_usages(
            [], [_usage("FOO", file_path="new.py"), _usage("BAR", file_path="new.py")]
        )
        assert {u.variable for u in result} == {"FOO", "BAR"}

    def test_a_file_removed_at_b_contributes_nothing(self):
        old = [_usage("FOO", file_path="gone.py")]
        result = dependency_diff.find_new_usages(old, [])
        assert result == []

    def test_empty_inputs_produce_no_findings(self):
        assert dependency_diff.find_new_usages([], []) == []


class TestClassifyAgainstSchema:
    def test_a_declared_variable_is_categorized_declared(self):
        report = dependency_diff.classify_against_schema([_usage("FOO")], {"FOO"})

        assert len(report.changes) == 1
        assert report.changes[0].category == "declared"
        assert report.has_missing_declarations is False

    def test_an_undeclared_variable_is_categorized_missing_declaration(self):
        report = dependency_diff.classify_against_schema([_usage("FOO")], set())

        assert report.changes[0].category == "missing_declaration"
        assert report.has_missing_declarations is True

    def test_both_categories_are_always_returned_not_just_missing(self):
        report = dependency_diff.classify_against_schema(
            [_usage("FOO"), _usage("BAR")], {"FOO"}
        )

        categories = {c.variable: c.category for c in report.changes}
        assert categories == {"FOO": "declared", "BAR": "missing_declaration"}

    def test_access_type_is_carried_through_as_evidence_not_identity(self):
        report = dependency_diff.classify_against_schema(
            [_usage("FOO", access_type="process.env[]")], set()
        )

        assert report.changes[0].access_type == "process.env[]"

    def test_empty_new_usages_is_not_a_missing_declaration(self):
        report = dependency_diff.classify_against_schema([], {"FOO"})

        assert report.changes == []
        assert report.has_missing_declarations is False


class TestToDictNeverCarriesAValue:
    """
    Security invariant (CLAUDE.md §4): a secret value must never enter an
    output representation. Unlike contract_diff.py's ContractChange (which
    can carry a schema field's defaultValue), dependency_diff.py has no
    value in scope anywhere -- discovery.py only ever reports a variable's
    *name*. Asserted explicitly so a future field addition can't
    accidentally introduce one without this failing.
    """

    def test_dependency_change_to_dict_has_no_value_shaped_key(self):
        change = dependency_diff.classify_against_schema(
            [_usage("FOO")], set()
        ).changes[0]

        keys = set(change.to_dict().keys())

        assert keys == {
            "variable",
            "file_path",
            "line",
            "language",
            "access_type",
            "category",
        }
        assert not any("value" in k.lower() or "default" in k.lower() for k in keys)

    def test_report_to_dict_shape(self):
        report = dependency_diff.classify_against_schema([_usage("FOO")], set())

        payload = report.to_dict()

        assert payload["has_missing_declarations"] is True
        assert payload["changes"][0]["variable"] == "FOO"
