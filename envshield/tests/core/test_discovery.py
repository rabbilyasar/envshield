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


def _js(content, file_path="app.js"):
    return discovery.discover_js_usages(content, file_path)


def _one_js(content, file_path="app.js"):
    result = _js(content, file_path)
    assert len(result) == 1, f"expected exactly one usage, got {result}"
    return result[0]


class TestProcessEnvDotAccess:
    def test_basic(self):
        usage = _one_js("const x = process.env.FOO;\n")
        assert usage.variable == "FOO"
        assert usage.access_type == "process.env."
        assert usage.language == "javascript"
        assert usage.confidence == "high"
        assert usage.line == 1

    def test_typescript_file_is_labeled_typescript(self):
        usage = _one_js("const x = process.env.FOO;\n", "app.ts")
        assert usage.language == "typescript"

    def test_tsx_file_is_labeled_typescript(self):
        usage = _one_js("const x = process.env.FOO;\n", "app.tsx")
        assert usage.language == "typescript"

    def test_jsx_file_is_labeled_javascript(self):
        usage = _one_js("const x = process.env.FOO;\n", "app.jsx")
        assert usage.language == "javascript"


class TestProcessEnvBracketAccess:
    def test_single_quoted(self):
        usage = _one_js("const x = process.env['FOO'];\n")
        assert usage.variable == "FOO"
        assert usage.access_type == "process.env[]"

    def test_double_quoted(self):
        usage = _one_js('const x = process.env["FOO"];\n')
        assert usage.variable == "FOO"

    def test_mismatched_quotes_are_not_matched(self):
        assert _js("const x = process.env['FOO\"];\n") == []


class TestImportMetaEnv:
    def test_dot_access(self):
        usage = _one_js("const x = import.meta.env.VITE_FOO;\n")
        assert usage.variable == "VITE_FOO"
        assert usage.access_type == "import.meta.env."

    def test_bracket_access(self):
        usage = _one_js("const x = import.meta.env['VITE_FOO'];\n")
        assert usage.variable == "VITE_FOO"
        assert usage.access_type == "import.meta.env[]"


class TestDestructuring:
    def test_single_key_single_line(self):
        usage = _one_js("const { FOO } = process.env;\n")
        assert usage.variable == "FOO"
        assert usage.access_type == "process.env{}"
        assert usage.line == 1

    def test_multiple_keys_single_line(self):
        result = _js("const { FOO, BAR } = process.env;\n")
        assert {u.variable for u in result} == {"FOO", "BAR"}

    def test_multiline_reports_the_statement_start_line(self):
        content = "const {\n  FOO,\n  BAR,\n} = process.env;\n"
        result = _js(content)
        assert {u.variable for u in result} == {"FOO", "BAR"}
        assert all(u.line == 1 for u in result)

    def test_renamed_reports_the_original_key_not_the_rename_target(self):
        usage = _one_js("const { FOO: renamed } = process.env;\n")
        assert usage.variable == "FOO"

    def test_defaulted_reports_the_key_not_the_default(self):
        usage = _one_js("const { FOO = 'fallback' } = process.env;\n")
        assert usage.variable == "FOO"

    def test_renamed_and_defaulted(self):
        usage = _one_js("const { FOO: renamed = 'fallback' } = process.env;\n")
        assert usage.variable == "FOO"

    def test_rest_element_is_skipped(self):
        result = _js("const { FOO, ...rest } = process.env;\n")
        assert {u.variable for u in result} == {"FOO"}

    def test_import_meta_env_destructuring(self):
        usage = _one_js("const { VITE_FOO } = import.meta.env;\n")
        assert usage.variable == "VITE_FOO"
        assert usage.access_type == "import.meta.env{}"

    def test_default_value_containing_a_call_does_not_break_the_split(self):
        """A default's own parens/braces must not be mistaken for extra
        top-level entries (depth-aware splitting)."""
        usage = _one_js("const { FOO = someCall(1, 2) } = process.env;\n")
        assert usage.variable == "FOO"

    def test_nested_destructuring_reports_only_the_outer_key(self):
        """Not a supported case (out of scope), but must not crash, and
        must not fabricate a second finding for the inner name."""
        result = _js("const { A: { B } } = process.env;\n")
        assert {u.variable for u in result} == {"A"}

    def test_oversized_span_is_not_reported(self):
        many_keys = ", ".join(f"K{i}" for i in range(2000))
        content = f"const {{{many_keys}}} = process.env;\n"
        assert _js(content) == []


class TestJsOutOfScopeByDesign:
    def test_dynamic_key_is_not_reported(self):
        assert _js("const x = process.env[someVar];\n") == []

    def test_computed_bracket_with_a_template_literal_is_not_reported(self):
        assert _js("const x = process.env[`FOO`];\n") == []

    def test_aliased_process_env_is_not_reported(self):
        assert _js("const env = process.env;\nconst x = env.FOO;\n") == []

    def test_destructuring_from_an_aliased_source_is_not_reported(self):
        assert _js("const env = process.env;\nconst { FOO } = env;\n") == []


class TestJsMalformedInputNeverRaises:
    def test_unbalanced_braces_do_not_raise(self):
        assert _js("const { FOO = process.env;\n") == []

    def test_empty_content_returns_an_empty_list(self):
        assert _js("") == []

    def test_no_relevant_usages_returns_an_empty_list(self):
        assert _js("const x = 1;\nconst y = 'hello';\n") == []


class TestJsPathologicalInputCompletesQuickly:
    """
    Concrete, not just theoretical: proves the single-pass brace-matching
    design (plus the destructure-span cap) stays bounded on adversarially
    shaped input, rather than degrading toward the O(n^2) a naive
    per-match backward scan without these safeguards would hit.
    """

    def test_many_anchors_with_no_preceding_brace(self):
        content = "x = process.env;\n" * 20000
        assert _js(content) == []

    def test_deeply_nested_braces_before_a_single_anchor(self):
        content = "{" * 3000 + "X" + "}" * 3000 + " = process.env;\n"
        assert _js(content) == []

    def test_many_balanced_destructures_produce_correct_results_quickly(self):
        content = "const { A, B, C } = process.env;\n" * 10000
        result = _js(content)
        assert len(result) == 30000
