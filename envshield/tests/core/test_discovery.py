# envshield/tests/core/test_discovery.py
from envshield.core import discovery


def _usages(content, file_path="app.py"):
    return discovery.discover_python_usages(content, file_path)


def _one(content, file_path="app.py"):
    result = _usages(content, file_path)
    assert len(result) == 1, f"expected exactly one usage, got {result}"
    return result[0]


class TestOsEnvironGet:
    def test_no_default(self):
        usage = _one("x = os.environ.get('X')\n")
        assert usage.variable == "X"
        assert usage.access_type == "os.environ.get"
        assert usage.language == "python"
        assert usage.confidence == "high"
        assert usage.file_path == "app.py"
        assert usage.line == 1

    def test_with_default(self):
        usage = _one("x = os.environ.get('X', 'fallback')\n")
        assert usage.variable == "X"

    def test_double_quoted(self):
        usage = _one('x = os.environ.get("X")\n')
        assert usage.variable == "X"


class TestOsGetenv:
    def test_no_default(self):
        usage = _one("x = os.getenv('X')\n")
        assert usage.variable == "X"
        assert usage.access_type == "os.getenv"

    def test_with_default(self):
        usage = _one("x = os.getenv('X', 'fallback')\n")
        assert usage.variable == "X"


class TestOsEnvironSubscript:
    def test_bracket_access(self):
        usage = _one("x = os.environ['X']\n")
        assert usage.variable == "X"
        assert usage.access_type == "os.environ[]"


class TestMultiLineCalls:
    def test_call_spanning_multiple_lines_reports_the_call_start_line(self):
        content = (
            "x = os.environ.get(\n"  # line 1 -- the call starts here
            "    'X',\n"  # line 2
            "    'fallback',\n"  # line 3
            ")\n"  # line 4
        )
        usage = _one(content)
        assert usage.variable == "X"
        assert usage.line == 1

    def test_bracket_access_spanning_multiple_lines(self):
        content = "x = os.environ[\n    'X'\n]\n"
        usage = _one(content)
        assert usage.variable == "X"
        assert usage.line == 1


class TestNonLiteralKeysAreNotReported:
    def test_a_variable_key_is_not_reported(self):
        assert _usages("key = 'X'\nx = os.environ.get(key)\n") == []

    def test_an_fstring_key_is_not_reported(self):
        assert _usages("x = os.environ.get(f'X')\n") == []

    def test_string_concatenation_with_a_name_is_not_reported(self):
        assert _usages("prefix = 'X'\nx = os.environ.get(prefix + 'X')\n") == []

    def test_subscript_with_a_variable_key_is_not_reported(self):
        assert _usages("key = 'X'\nx = os.environ[key]\n") == []

    def test_getenv_with_no_arguments_is_not_reported(self):
        assert _usages("x = os.getenv()\n") == []


class TestOutOfScopeByDesign:
    """
    Explicitly excluded per the Phase 2B Milestone 1 plan -- not gaps to
    fix, deliberate scope boundaries verified so a future change doesn't
    silently start matching these without a conscious decision.
    """

    def test_keyword_form_is_not_reported(self):
        assert _usages("x = os.environ.get(key='X')\n") == []

    def test_aliased_import_is_not_reported(self):
        assert _usages("import os as o\nx = o.environ.get('X')\n") == []

    def test_from_import_indirection_is_not_reported(self):
        assert _usages("from os import environ\nx = environ.get('X')\n") == []

    def test_getenv_aliased_import_is_not_reported(self):
        assert _usages("import os as o\nx = o.getenv('X')\n") == []


class TestMultipleUsages:
    def test_two_distinct_call_sites_produce_two_records(self):
        content = "a = os.environ.get('A')\nb = os.getenv('B')\n"
        result = _usages(content)
        assert {u.variable for u in result} == {"A", "B"}
        assert len(result) == 2

    def test_the_same_variable_read_twice_produces_two_records(self):
        content = "a = os.environ.get('X')\nb = os.getenv('X')\n"
        result = _usages(content)
        assert len(result) == 2
        assert all(u.variable == "X" for u in result)


class TestMalformedInputNeverRaises:
    def test_a_syntax_error_returns_an_empty_list(self):
        assert _usages("def f(:\n") == []

    def test_empty_content_returns_an_empty_list(self):
        assert _usages("") == []

    def test_content_with_no_relevant_usages_returns_an_empty_list(self):
        assert _usages("x = 1\ny = 'hello'\n") == []


class TestToDict:
    def test_to_dict_matches_the_normalized_shape(self):
        usage = _one("x = os.environ.get('X')\n")
        assert usage.to_dict() == {
            "variable": "X",
            "file_path": "app.py",
            "line": 1,
            "language": "python",
            "access_type": "os.environ.get",
            "confidence": "high",
        }
