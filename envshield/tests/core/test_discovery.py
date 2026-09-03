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


class TestOsEnvironSubscriptWriteIsNotARead:
    def test_a_write_only_assignment_is_not_reported(self):
        """
        Regression: visit_Subscript never checked node.ctx, so
        os.environ["X"] = value (a write, never a read) was recorded
        identically to os.environ["X"] (a read) -- misreporting every
        write-only assignment as a configuration dependency.
        """
        assert _usages("os.environ['X'] = 'value'\n") == []

    def test_a_read_in_the_same_file_is_still_reported(self):
        usage = _one("os.environ['X'] = 'value'\ny = os.environ['X']\n")
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

    def test_getenv_aliased_import_is_not_reported(self):
        assert _usages("import os as o\nx = o.getenv('X')\n") == []

    def test_bare_import_inside_a_function_is_not_reported(self):
        """
        _collect_os_bindings only scans top-level statements (performance:
        see its own docstring) -- a 'from os import getenv' nested inside
        a function body is a real but rare enough case that this is a
        deliberate scope boundary, not an oversight.
        """
        content = "def f():\n    from os import getenv\n    return getenv('X')\n"
        assert _usages(content) == []


class TestBareOsImportRecognition:
    """
    'from os import getenv'/'from os import environ' followed by a bare
    getenv(...)/environ.get(...)/environ[...] call is now treated as
    equivalent to the qualified os.getenv/os.environ form -- this used to
    be silently invisible (test_from_import_indirection_is_not_reported,
    removed from TestOutOfScopeByDesign above, asserted the old, incorrect
    "not reported" behavior as correct). Found via a real Zeus codebase
    where a central config-switching module uses exactly this import style
    throughout, making every variable read only through it permanently
    undiscoverable. Unaliased only -- 'from os import getenv as ge' stays
    out of scope, matching the aliased-import precedent above.
    """

    def test_bare_getenv_after_unaliased_import(self):
        usage = _one("from os import getenv\nx = getenv('X')\n")
        assert usage.variable == "X"
        assert usage.access_type == "os.getenv"

    def test_bare_environ_get_after_unaliased_import(self):
        usage = _one("from os import environ\nx = environ.get('X')\n")
        assert usage.variable == "X"
        assert usage.access_type == "os.environ.get"

    def test_bare_environ_subscript_after_unaliased_import(self):
        usage = _one("from os import environ\nx = environ['X']\n")
        assert usage.variable == "X"
        assert usage.access_type == "os.environ[]"

    def test_bare_getenv_with_default(self):
        usage = _one("from os import getenv\nx = getenv('X', 'fallback')\n")
        assert usage.variable == "X"

    def test_aliased_bare_import_is_not_reported(self):
        """'from os import getenv as ge' is still out of scope -- an
        aliased bare import is a different, deliberately unsupported case
        from the unaliased one this fix adds."""
        assert _usages("from os import getenv as ge\nx = ge('X')\n") == []

    def test_unrelated_bare_getenv_with_no_os_import_is_not_reported(self):
        """A locally-defined getenv() with no 'from os import getenv'
        anywhere in the file must never be mistaken for an environment
        read -- the exact false-positive risk this feature must avoid."""
        content = "def getenv(key):\n    return 'local'\nx = getenv('X')\n"
        assert _usages(content) == []

    def test_unrelated_bare_getenv_from_a_different_module_is_not_reported(self):
        content = "from myapp.config import getenv\nx = getenv('X')\n"
        assert _usages(content) == []

    def test_qualified_form_still_works_alongside_a_bare_import(self):
        """A file that imports both ways ('import os' AND 'from os import
        getenv') must still recognize the qualified os.getenv(...) form
        too -- the two recognition paths are additive, not exclusive."""
        content = (
            "import os\nfrom os import getenv\na = os.getenv('A')\nb = getenv('B')\n"
        )
        result = _usages(content)
        assert {u.variable for u in result} == {"A", "B"}

    def test_discover_python_env_vars_also_recognizes_the_bare_form(self):
        """The richer schema-generation engine (discover_python_env_vars)
        must never disagree with discover_python_usages on what counts as
        a read -- both share the same _OsBindings computation."""
        result = discovery.discover_python_env_vars(
            "from os import getenv\nx = getenv('X', 'fallback')\n", "config.py"
        )
        assert len(result) == 1
        assert result[0].variable == "X"
        assert result[0].default_value == "fallback"


class TestFlaskCurrentAppConfigRead:
    """
    BL-113: current_app.config[...]/.get(...) is recognized, at "medium"
    confidence (never "high" -- see DiscoveredVariableUsage), gated on a
    real 'from flask import current_app' import anywhere in the file.
    """

    def test_subscript_form(self):
        content = "from flask import current_app\nx = current_app.config['X']\n"
        usage = _one(content)
        assert usage.variable == "X"
        assert usage.access_type == "flask.current_app.config[]"
        assert usage.confidence == "medium"

    def test_get_form(self):
        content = "from flask import current_app\nx = current_app.config.get('X')\n"
        usage = _one(content)
        assert usage.variable == "X"
        assert usage.access_type == "flask.current_app.config.get"
        assert usage.confidence == "medium"

    def test_get_form_with_a_default_argument(self):
        content = "from flask import current_app\nx = current_app.config.get('X', 'fallback')\n"
        usage = _one(content)
        assert usage.variable == "X"


class TestFlaskConfigAliasedImport:
    """
    BL-113: 'from flask import current_app as <alias>' is tracked -- unlike
    _OsBindings' aliased-os-import case, cross-codebase evidence (two
    independent real Flask applications) found this to be the *dominant*
    real-world spelling (84-89% of measured reads), not a rare exception.
    """

    def test_aliased_subscript_form(self):
        content = "from flask import current_app as app\nx = app.config['X']\n"
        usage = _one(content)
        assert usage.variable == "X"
        assert usage.access_type == "flask.current_app.config[]"
        assert usage.confidence == "medium"

    def test_aliased_get_form(self):
        content = "from flask import current_app as app\nx = app.config.get('X')\n"
        usage = _one(content)
        assert usage.variable == "X"
        assert usage.access_type == "flask.current_app.config.get"

    def test_a_different_alias_name_is_still_recognized(self):
        content = (
            "from flask import current_app as flask_app\nx = flask_app.config['X']\n"
        )
        usage = _one(content)
        assert usage.variable == "X"


class TestFlaskConfigDeferredImport:
    """
    BL-113: unlike _collect_os_bindings (top-level statements only),
    _collect_flask_bindings scans the whole file -- evidence found the
    majority of real 'from flask import current_app' imports are
    function-local, deferred specifically to avoid Flask's app-context/
    circular-import issues at module load time. A top-level-only scan
    would silently miss most real-world Flask evidence.
    """

    def test_function_local_unaliased_import_is_still_recognized(self):
        content = (
            "def f():\n"
            "    from flask import current_app\n"
            "    return current_app.config['X']\n"
        )
        usage = _one(content)
        assert usage.variable == "X"

    def test_function_local_aliased_import_is_still_recognized(self):
        content = (
            "def f():\n"
            "    from flask import current_app as app\n"
            "    return app.config.get('X')\n"
        )
        usage = _one(content)
        assert usage.variable == "X"


class TestFlaskConfigFalsePositives:
    """
    Realistic false-positive cases found during BL-113's evidence study,
    each locked in as a negative test rather than left to chance.
    """

    def test_unrelated_dot_config_with_no_flask_import_is_not_reported(self):
        """A bare-Name '<name>.config[...]' with no 'from flask import
        current_app' anywhere in the file must never be mistaken for a
        Flask read -- the exact false-positive risk this feature must
        avoid, mirroring TestBareOsImportRecognition's own
        unrelated-bare-getenv case."""
        assert _usages("x = app.config['X']\n") == []

    def test_attribute_chain_base_is_not_reported(self):
        """
        Real false positive found in evidence: 'self.gateway.config.get(...)'
        (an unrelated object's own settings dict, nothing to do with
        Flask). Its base is an ast.Attribute chain ('self.gateway'), never
        a bare ast.Name, so _is_flask_config_attr's existing bare-Name
        requirement (the same shape _is_os_environ already requires for
        os.environ) excludes it with no extra logic needed.
        """
        content = "from flask import current_app\nx = self.gateway.config.get('X')\n"
        assert _usages(content) == []

    def test_self_config_inside_a_flask_subclass_is_not_reported(self):
        """
        Explicitly out of scope (see _FlaskBindings) -- 'self.config[...]'
        inside a method of a class that happens to subclass Flask would
        require class-hierarchy tracking to resolve; evidence found this
        pattern real but low-volume, not worth the added complexity.
        """
        content = (
            "from flask import current_app, Flask\n"
            "class MyFlask(Flask):\n"
            "    def f(self):\n"
            "        return self.config['X']\n"
        )
        assert _usages(content) == []


class TestFlaskConfigAliasingOutOfScope:
    """Explicitly excluded per BL-113's own scope decision -- confirmed
    absent (zero occurrences) in both of this feature's evidence
    codebases, and out of scope for the same reason this module tracks no
    other alias/import-indirection (see the module docstring)."""

    def test_assignment_style_aliasing_is_not_reported(self):
        content = (
            "from flask import current_app\n"
            "config = current_app.config\n"
            "x = config['X']\n"
        )
        assert _usages(content) == []

    def test_module_qualified_access_is_not_reported(self):
        content = "import flask\nx = flask.current_app.config['X']\n"
        assert _usages(content) == []

    def test_locally_constructed_flask_app_is_not_reported(self):
        """
        The originally-proposed 'app = Flask(...)' construction heuristic
        was dropped entirely per BL-113's evidence study -- it caught
        almost no real reads in either evidence codebase. A locally
        constructed app with no current_app import is not recognized.
        """
        content = (
            "from flask import Flask\napp = Flask(__name__)\nx = app.config['X']\n"
        )
        assert _usages(content) == []


class TestFlaskConfigCoexistsWithOsRecognition:
    def test_a_file_with_both_os_and_flask_reads_reports_both_at_their_own_confidence(
        self,
    ):
        content = (
            "import os\n"
            "from flask import current_app as app\n"
            "a = os.getenv('A')\n"
            "b = app.config['B']\n"
        )
        result = _usages(content)
        by_var = {u.variable: u for u in result}
        assert by_var["A"].confidence == "high"
        assert by_var["B"].confidence == "medium"

    def test_flask_prefilter_does_not_suppress_existing_os_recognition(self):
        """The cheap 'current_app' in content prefilter only gates the
        extra Flask-binding scan -- it must never affect os.environ/
        os.getenv recognition in a file that happens to have no Flask
        involvement at all."""
        usage = _one("x = os.environ.get('X')\n")
        assert usage.confidence == "high"


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


def _env_vars(content, file_path="config.py"):
    return discovery.discover_python_env_vars(content, file_path)


def _one_env_var(content, file_path="config.py"):
    result = _env_vars(content, file_path)
    assert len(result) == 1, f"expected exactly one usage, got {result}"
    return result[0]


class TestDiscoverPythonEnvVarsBasicReads:
    """
    Reuses the same os.environ.get/os.getenv/os.environ[] recognition as
    discover_python_usages (including at any nesting depth), plus a
    best-effort recovered default -- the new capability discover_python_usages
    itself was never designed to have.
    """

    def test_os_environ_get_no_default(self):
        usage = _one_env_var("x = os.environ.get('X')\n")
        assert usage.variable == "X"
        assert usage.access_type == "os.environ.get"
        assert usage.default_value is None

    def test_os_getenv_no_default(self):
        usage = _one_env_var("x = os.getenv('X')\n")
        assert usage.variable == "X"
        assert usage.access_type == "os.getenv"
        assert usage.default_value is None

    def test_os_environ_subscript(self):
        usage = _one_env_var("x = os.environ['X']\n")
        assert usage.variable == "X"
        assert usage.access_type == "os.environ[]"
        assert usage.default_value is None

    def test_recognizes_a_read_inside_a_class_body(self):
        """The exact gap discover_python_usages already closes for free -- verified here too, since this new engine has its own separate visitor."""
        usage = _one_env_var(
            "import os\nclass Config:\n    SECRET_KEY = os.environ.get('SECRET_KEY')\n"
        )
        assert usage.variable == "SECRET_KEY"


class TestLiteralDefaults:
    def test_os_getenv_literal_default(self):
        usage = _one_env_var("x = os.getenv('PORT', '8000')\n")
        assert usage.variable == "PORT"
        assert usage.default_value == "8000"

    def test_os_environ_get_literal_default(self):
        usage = _one_env_var("x = os.environ.get('PORT', '8000')\n")
        assert usage.variable == "PORT"
        assert usage.default_value == "8000"

    def test_non_literal_default_is_not_recovered(self):
        usage = _one_env_var("fallback = compute()\nx = os.getenv('X', fallback)\n")
        assert usage.variable == "X"
        assert usage.default_value is None

    def test_subscript_form_has_no_default_slot_at_all(self):
        usage = _one_env_var("x = os.environ['X']\n")
        assert usage.default_value is None


class TestOrFallbackPattern:
    def test_os_environ_get_or_literal_fallback(self):
        usage = _one_env_var("x = os.environ.get('SECRET_KEY') or 'fallback'\n")
        assert usage.variable == "SECRET_KEY"
        assert usage.access_type == "os.environ.get"
        assert usage.default_value == "fallback"

    def test_os_getenv_or_literal_fallback(self):
        usage = _one_env_var("x = os.getenv('HOST') or 'localhost'\n")
        assert usage.variable == "HOST"
        assert usage.default_value == "localhost"

    def test_os_environ_subscript_or_literal_fallback(self):
        usage = _one_env_var("x = os.environ['HOST'] or 'localhost'\n")
        assert usage.variable == "HOST"
        assert usage.default_value == "localhost"

    def test_does_not_double_record_the_or_operand(self):
        """The recognized call inside the BoolOp must be recorded exactly once -- not once by visit_BoolOp and again by the ordinary visit_Call traversal."""
        result = _env_vars("x = os.environ.get('SECRET_KEY') or 'fallback'\n")
        assert len(result) == 1

    def test_a_non_literal_right_hand_side_invents_no_default(self):
        """'X or Y' where Y isn't a literal -- no evaluation semantics are invented; the read is still recorded, just with no default."""
        usage = _one_env_var("y = compute()\nx = os.getenv('X') or y\n")
        assert usage.variable == "X"
        assert usage.default_value is None

    def test_three_operand_or_chain_is_left_to_ordinary_traversal(self):
        """Only the exact two-operand shape is special-cased -- a three-way 'or' still records the read via the ordinary Call visitor, with no default."""
        usage = _one_env_var("x = os.getenv('X') or compute() or 'fallback'\n")
        assert usage.variable == "X"
        assert usage.default_value is None


class TestWrapperCallsAreTransparent:
    """int()/bool()/str() etc. need no special handling at all -- the visitor already descends through any wrapper into the inner recognized call."""

    def test_int_wrapped_getenv_with_default(self):
        usage = _one_env_var("x = int(os.getenv('PORT', '8000'))\n")
        assert usage.variable == "PORT"
        assert usage.default_value == "8000"

    def test_bool_wrapped_environ_get(self):
        usage = _one_env_var("x = bool(os.environ.get('DEBUG', 'false'))\n")
        assert usage.variable == "DEBUG"
        assert usage.default_value == "false"


class TestDjangoStyleNoiseIsExcluded:
    def test_only_env_reading_assignments_are_reported(self):
        content = (
            "import os\n"
            "SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY')\n"
            "DATABASE_URL = os.getenv('DATABASE_URL')\n"
            "PROJECT_ROOT = 'BASE_DIR/foo'\n"
            "ROOT_URLCONF = 'project.urls'\n"
            "APPEND_SLASH = True\n"
        )
        result = _env_vars(content)
        assert {u.variable for u in result} == {"DJANGO_SECRET_KEY", "DATABASE_URL"}


class TestBaseSettingsFields:
    def test_bare_annotated_field_uses_uppercased_attribute_name(self):
        usage = _one_env_var(
            "from pydantic_settings import BaseSettings\n"
            "class Settings(BaseSettings):\n"
            "    secret_key: str\n"
        )
        assert usage.variable == "SECRET_KEY"
        assert usage.access_type == "pydantic.BaseSettings.field"
        assert usage.default_value is None

    def test_field_with_alias_uses_the_alias(self):
        usage = _one_env_var(
            "from pydantic_settings import BaseSettings\n"
            "from pydantic import Field\n"
            "class Settings(BaseSettings):\n"
            "    database_url: str = Field(..., alias='DATABASE_URL')\n"
        )
        assert usage.variable == "DATABASE_URL"
        assert usage.default_value is None

    def test_field_with_default_and_alias_recovers_both(self):
        usage = _one_env_var(
            "from pydantic_settings import BaseSettings\n"
            "from pydantic import Field\n"
            "class Settings(BaseSettings):\n"
            "    log_level: str = Field('info', alias='LOG_LEVEL')\n"
        )
        assert usage.variable == "LOG_LEVEL"
        assert usage.default_value == "info"

    def test_field_without_alias_uses_uppercased_attribute_name(self):
        usage = _one_env_var(
            "from pydantic_settings import BaseSettings\n"
            "from pydantic import Field\n"
            "class Settings(BaseSettings):\n"
            "    log_level: str = Field('info')\n"
        )
        assert usage.variable == "LOG_LEVEL"
        assert usage.default_value == "info"

    def test_field_ellipsis_default_is_not_a_literal_default(self):
        usage = _one_env_var(
            "from pydantic_settings import BaseSettings\n"
            "from pydantic import Field\n"
            "class Settings(BaseSettings):\n"
            "    api_key: str = Field(..., alias='API_KEY')\n"
        )
        assert usage.default_value is None

    def test_an_explicit_os_getenv_default_wins_over_the_attribute_name(self):
        """
        A field whose own value calls os.getenv/os.environ.get explicitly
        names the real env var -- that always wins over guessing from the
        (possibly differently-named) attribute name.
        """
        usage = _one_env_var(
            "import os\n"
            "from pydantic_settings import BaseSettings\n"
            "class Settings(BaseSettings):\n"
            "    db_url: str = os.getenv('DATABASE_URL')\n"
        )
        assert usage.variable == "DATABASE_URL"

    def test_a_plain_class_not_inheriting_base_settings_is_not_treated_as_config(self):
        """Do not treat arbitrary annotated class attributes as configuration fields -- only an actual BaseSettings subclass."""
        result = _env_vars("class Point:\n    x: int\n    y: int = 0\n")
        assert result == []

    def test_aliased_base_settings_import_is_out_of_scope_by_design(self):
        """Matches discover_python_usages' own precedent (no import-alias resolution) -- consistent, not a bug."""
        result = _env_vars(
            "from pydantic_settings import BaseSettings as BS\n"
            "class Settings(BS):\n"
            "    secret_key: str\n"
        )
        assert result == []

    def test_reads_elsewhere_in_the_same_file_are_still_reported(self):
        """generic_visit still runs after BaseSettings-specific handling -- a plain read outside the class is unaffected."""
        result = _env_vars(
            "import os\n"
            "from pydantic_settings import BaseSettings\n"
            "class Settings(BaseSettings):\n"
            "    secret_key: str\n"
            "OTHER = os.getenv('OTHER_VAR')\n"
        )
        assert {u.variable for u in result} == {"SECRET_KEY", "OTHER_VAR"}


class TestMalformedInputNeverRaisesForEnvVars:
    def test_a_syntax_error_returns_an_empty_list(self):
        assert _env_vars("def f(:\n") == []

    def test_empty_content_returns_an_empty_list(self):
        assert _env_vars("") == []

    def test_content_with_no_relevant_usages_returns_an_empty_list(self):
        assert _env_vars("x = 1\ny = 'hello'\n") == []


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


class TestJsAssignmentTargetIsNotARead:
    """
    Regression: process.env.FOO/process.env['FOO'] on the left side of an
    assignment (a write) used to be matched identically to a read -- these
    regexes have no notion of assignment target vs. value.
    """

    def test_dot_access_assignment_is_not_reported(self):
        assert _js("process.env.FOO = 'bar';\n") == []

    def test_bracket_access_assignment_is_not_reported(self):
        assert _js("process.env['FOO'] = 'bar';\n") == []

    def test_a_read_elsewhere_in_the_file_is_still_reported(self):
        usage = _one_js("process.env.FOO = 'bar';\nconst x = process.env.FOO;\n")
        assert usage.variable == "FOO"
        assert usage.line == 2

    def test_comparison_is_still_reported_as_a_read(self):
        """A '==' comparison is a read, not an assignment -- must not be
        mistaken for one just because it starts with '='."""
        usage = _one_js("if (process.env.FOO == 'bar') {}\n")
        assert usage.variable == "FOO"


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
