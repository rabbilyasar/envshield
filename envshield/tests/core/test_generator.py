# envshield/tests/core/test_generator.py
import json
import subprocess

import pytest

from envshield.core import generator


def test_generate_config_required_secret_field():
    schema = {
        "DATABASE_URL": {"description": "DB connection string.", "secret": True},
    }

    content = generator.generate_config(schema, lang="python")

    assert "class Settings(BaseSettings):" in content
    assert "database_url: SecretStr = Field(" in content
    assert "..., description='DB connection string.', alias='DATABASE_URL'" in content
    assert "settings = Settings()" in content


def test_generate_config_infers_types_from_default_values():
    schema = {
        "LOG_LEVEL": {
            "description": "Verbosity.",
            "secret": False,
            "defaultValue": "info",
        },
        "MAX_RETRIES": {
            "description": "Retry count.",
            "secret": False,
            "defaultValue": "3",
        },
        "DEBUG": {
            "description": "Debug flag.",
            "secret": False,
            "defaultValue": "true",
        },
    }

    content = generator.generate_config(schema)

    assert "log_level: str = Field(" in content
    assert "'info', description='Verbosity.', alias='LOG_LEVEL'" in content

    assert "max_retries: int = Field(" in content
    assert "3, description='Retry count.', alias='MAX_RETRIES'" in content

    assert "debug: bool = Field(" in content
    assert "True, description='Debug flag.', alias='DEBUG'" in content


def test_generate_config_secret_with_default_stays_secret_str():
    """
    A secret field's declared type (SecretStr) is unaffected by BL-001's
    fix -- only the literal default value is withheld. config_manager.
    load_schema already refuses this schema shape outright (see
    test_config_manager.py's TestLoadSchemaRejectsSecretDefaults); this
    exercises generate_config's own defense-in-depth guard directly, for a
    schema dict that reached it some other way.
    """
    schema = {
        "API_KEY": {
            "description": "3rd party key.",
            "secret": True,
            "defaultValue": "changeme",
        },
    }

    content = generator.generate_config(schema)

    assert "api_key: SecretStr = Field(" in content
    # BL-001: the literal default must never appear in generated, committed
    # source -- the field becomes required instead of silently defaulting.
    assert "changeme" not in content
    assert "..., description='3rd party key.', alias='API_KEY'" in content


def test_generate_config_empty_schema():
    content = generator.generate_config({})

    assert "class Settings(BaseSettings):" in content
    assert "pass" in content


def test_generate_config_unsupported_language_raises():
    with pytest.raises(ValueError, match="Unsupported language"):
        generator.generate_config({"KEY": {}}, lang="rust")


def test_generate_typescript_required_secret_field():
    schema = {
        "DATABASE_URL": {"description": "DB connection string.", "secret": True},
    }

    content = generator.generate_config(schema, lang="typescript")

    assert 'import { z } from "zod";' in content
    assert "class Secret<T>" in content
    assert '"DATABASE_URL": z.string().min(1),' in content
    assert '"DATABASE_URL": new Secret(_parsed["DATABASE_URL"]),' in content
    assert "export const env = {" in content


def test_generate_typescript_infers_types_from_default_values():
    schema = {
        "LOG_LEVEL": {
            "description": "Verbosity.",
            "secret": False,
            "defaultValue": "info",
        },
        "MAX_RETRIES": {
            "description": "Retry count.",
            "secret": False,
            "defaultValue": "3",
        },
        "DEBUG": {
            "description": "Debug flag.",
            "secret": False,
            "defaultValue": "true",
        },
    }

    content = generator.generate_config(schema, lang="typescript")

    assert '"LOG_LEVEL": z.string().default("info"),' in content
    assert '"MAX_RETRIES": z.coerce.number().default(3),' in content
    # The bool default sits on the inner z.string(), as a string -- not
    # appended to the end of the chain, where zod 3 would re-parse a
    # boolean through z.string() and throw. See
    # TestTypeScriptBooleanCoercionMatchesTheContract for the runtime proof;
    # this assertion previously encoded the broken trailing form.
    assert (
        '"DEBUG": z.string().default("true").transform((s) => s.toLowerCase())'
        '.pipe(z.enum(["true", "false"])).transform((s) => s === "true"),'
        in content
    )

    # Non-secret fields are passed through directly, not wrapped in Secret.
    assert '"LOG_LEVEL": _parsed["LOG_LEVEL"],' in content
    assert 'new Secret(_parsed["LOG_LEVEL"])' not in content


def test_generate_typescript_reads_bundler_public_vars_from_import_meta_env():
    """
    Regression: a Vite/Next/CRA-style bundler-public variable is inlined
    into the client bundle at build time and is only ever available via
    `import.meta.env`, never `process.env` -- the generator used to emit a
    single schema parsed unconditionally against `process.env` for every
    field, which would throw at import time in real browser/Vite client
    code for a var like VITE_PUBLIC_ANALYTICS_ID.
    """
    schema = {
        "DATABASE_URL": {"description": "DB connection string.", "secret": True},
        "VITE_PUBLIC_ANALYTICS_ID": {
            "description": "Client-side analytics ID.",
            "secret": False,
        },
    }

    content = generator.generate_config(schema, lang="typescript")

    assert "const _parsed = _schema.parse(process.env);" in content
    assert "const _clientParsed = _clientSchema.parse(import.meta.env);" in content
    # The server-only schema must not declare the client var, and vice versa.
    assert '"DATABASE_URL": z.string().min(1),' in content
    assert '"VITE_PUBLIC_ANALYTICS_ID": z.string().min(1),' in content
    assert '"DATABASE_URL": new Secret(_parsed["DATABASE_URL"]),' in content
    assert (
        '"VITE_PUBLIC_ANALYTICS_ID": _clientParsed["VITE_PUBLIC_ANALYTICS_ID"],'
        in content
    )


def test_generate_typescript_schema_with_no_public_vars_is_unaffected():
    """A schema with no bundler-public vars renders exactly as before this split existed -- no _clientSchema/_clientParsed at all."""
    schema = {"DATABASE_URL": {"description": "DB connection string.", "secret": True}}

    content = generator.generate_config(schema, lang="typescript")

    assert "_clientSchema" not in content
    assert "_clientParsed" not in content
    assert "import.meta.env" not in content


def test_generate_config_explicit_enum_type_python():
    schema = {
        "LOG_LEVEL": {
            "description": "Verbosity.",
            "secret": False,
            "enum": ["debug", "info", "warn", "error"],
        },
    }

    content = generator.generate_config(schema, lang="python")

    assert "from typing import Literal" in content
    assert "log_level: Literal['debug', 'info', 'warn', 'error'] = Field(" in content


def test_generate_config_explicit_port_type_python():
    schema = {
        "API_PORT": {"description": "Port.", "type": "port", "defaultValue": "8080"}
    }

    content = generator.generate_config(schema, lang="python")

    assert "api_port: int = Field(" in content
    assert "8080, description='Port.', alias='API_PORT', ge=1, le=65535" in content


def test_generate_config_explicit_url_type_python_adds_import():
    schema = {"API_URL": {"description": "URL.", "type": "url"}}

    content = generator.generate_config(schema, lang="python")

    assert "from pydantic import AnyUrl" in content
    assert "api_url: AnyUrl = Field(" in content


def test_generate_config_explicit_email_type_python_notes_extra():
    schema = {"ADMIN_EMAIL": {"description": "Admin.", "type": "email"}}

    content = generator.generate_config(schema, lang="python")

    assert "from pydantic import EmailStr" in content
    assert "pydantic[email]" in content


def test_generate_config_python_merges_pydantic_imports_into_one_line():
    """
    Regression: a schema needing more than one extra pydantic name (here,
    url + email, on top of the always-present Field/SecretStr) used to
    render three separate 'from pydantic import ...' lines -- one per
    name -- instead of one combined, deduplicated import.
    """
    schema = {
        "API_URL": {"description": "URL.", "type": "url"},
        "ADMIN_EMAIL": {"description": "Admin.", "type": "email"},
    }

    content = generator.generate_config(schema, lang="python")

    assert content.count("from pydantic import") == 1
    assert "from pydantic import AnyUrl, EmailStr, Field, SecretStr" in content


def test_generate_config_pattern_becomes_field_constraint():
    schema = {"VERSION": {"description": "Semver.", "pattern": r"^v\d+\.\d+\.\d+$"}}

    content = generator.generate_config(schema, lang="python")

    assert "pattern='^v\\\\d+\\\\.\\\\d+\\\\.\\\\d+$'" in content


def test_generate_config_required_if_becomes_optional_python():
    schema = {
        "FEATURE_X_API_KEY": {
            "description": "Only needed when feature X is on.",
            "secret": True,
            "requiredIf": {"var": "FEATURE_X_ENABLED", "equals": "true"},
        }
    }

    content = generator.generate_config(schema, lang="python")

    assert "from typing import Optional" in content
    assert "feature_x_api_key: Optional[SecretStr] = Field(" in content
    assert "None, description=" in content


def test_generate_typescript_explicit_enum_type():
    schema = {"LOG_LEVEL": {"description": "Verbosity.", "enum": ["debug", "info"]}}

    content = generator.generate_config(schema, lang="typescript")

    assert '"LOG_LEVEL": z.enum(["debug", "info"])' in content


def test_generate_typescript_explicit_port_type_with_default():
    schema = {
        "API_PORT": {"description": "Port.", "type": "port", "defaultValue": "8080"}
    }

    content = generator.generate_config(schema, lang="typescript")

    assert '"API_PORT": z.coerce.number().min(1).max(65535).default(8080),' in content


def test_generate_typescript_secret_with_default_never_embeds_the_literal():
    """
    BL-001 regression (TypeScript surface): a secret field's real default
    must never be embedded in the generated '.default(...)' literal.
    config_manager.load_schema already refuses this schema shape outright;
    this exercises generate_config's own defense-in-depth guard for a
    schema dict that reached it some other way. The field still renders as
    required (no '.default(' / '.optional()' at all) rather than silently
    dropping the field, and it's still wrapped in Secret(...) at export.
    """
    content = generator.generate_config(
        {
            "STRIPE_SECRET_KEY": {
                "description": "Stripe key.",
                "secret": True,
                "defaultValue": "sk_live_SYNTHETIC_NOT_A_REAL_SECRET",
            }
        },
        lang="typescript",
    )

    assert "sk_live_SYNTHETIC_NOT_A_REAL_SECRET" not in content
    assert '"STRIPE_SECRET_KEY": z.string().min(1),' in content
    assert 'new Secret(_parsed["STRIPE_SECRET_KEY"])' in content


def test_generate_typescript_explicit_url_type():
    schema = {"API_URL": {"description": "URL."}}
    schema["API_URL"]["type"] = "url"

    content = generator.generate_config(schema, lang="typescript")

    assert '"API_URL": z.string().url(),' in content


def test_generate_typescript_required_if_becomes_optional():
    schema = {
        "FEATURE_X_API_KEY": {
            "description": "Only needed when feature X is on.",
            "requiredIf": {"var": "FEATURE_X_ENABLED", "equals": "true"},
        }
    }

    content = generator.generate_config(schema, lang="typescript")

    assert '"FEATURE_X_API_KEY": z.string().optional(),' in content


def test_generate_typescript_secret_uses_true_private_field():
    """
    Regression: the Secret<T> wrapper used TypeScript's `private` keyword,
    which is compile-time-only and still emits a plain, enumerable runtime
    property -- so a bare `console.log(secret)` printed the real value in
    full, directly contradicting the wrapper's own doc comment. A true
    EcmaScript private field (`#value`) is invisible to default object
    inspection, so it must be used instead.
    """
    content = generator.generate_config(
        {"API_KEY": {"secret": True}}, lang="typescript"
    )

    assert "#value" in content
    assert "private _value" not in content
    assert "this.#value = value" in content
    assert "nodejs.util.inspect.custom" in content


def test_generate_typescript_empty_schema():
    content = generator.generate_config({}, lang="typescript")

    assert "const _schema = z.object({});" in content
    assert "export const env = _parsed;" in content


def _node_check(tmp_path, script: str) -> subprocess.CompletedProcess:
    """Syntax-only parse via a real node -- proves a comment's structure
    wasn't broken, without needing zod or any other module installed."""
    script_path = tmp_path / "check.js"
    script_path.write_text(script)
    return subprocess.run(
        ["node", "--check", str(script_path)], capture_output=True, text=True
    )


class TestTypeScriptJSDocInjectionIsPrevented:
    """
    Regression coverage for P0-5: 'description' is repository-controlled
    (env.schema.toml is committed/PR-editable) and lands inside a
    '/** ... */' JSDoc block comment with no in-band escape mechanism --
    the only sequence that matters is the literal '*/' that terminates the
    comment early. Everything else (quotes, backticks, newlines, '${...}',
    '<script>', a nested-looking '//'/'/*', Unicode) is inert plain text in
    a block comment and needs no escaping at all.

    Each test runs the actual escaping/rendering output through a real
    node, either for a syntax-only parse or a real execution with a
    sentinel side effect, rather than only asserting the string "looks"
    escaped -- the key claim is that attacker-controlled input cannot
    change the generated program's structure.
    """

    def test_normal_description_passes_through_unescaped(self):
        content = generator.generate_config(
            {"KEY": {"description": "A normal description."}}, lang="typescript"
        )
        assert "/** A normal description. */" in content

    @pytest.mark.parametrize(
        "payload",
        [
            "it's got a single quote",
            'it has a "double quote"',
            "it has a `backtick`",
            "line one\nline two",
            "carriage\rreturn",
            "a\\backslash",
            "${1 + 1} template syntax",
            "<script>alert(1)</script>",
            "// a line comment lookalike",
            "Unicode: héllo wörld 日本語 🎉",
        ],
    )
    def test_benign_special_characters_produce_syntactically_valid_output(
        self, tmp_path, payload
    ):
        """None of these are dangerous in a block comment on their own --
        generation must succeed and the resulting comment, parsed by a real
        node, must remain valid JS syntax."""
        escaped = generator._escape_jsdoc_comment(payload)
        script = f"/** {escaped} */\nglobalThis.__ENVSHIELD_OK__ = true;\n"

        result = _node_check(tmp_path, script)

        assert result.returncode == 0, result.stderr

    def test_comment_terminator_is_neutralized(self):
        payload = "ends the comment */ and starts new code"

        escaped = generator._escape_jsdoc_comment(payload)

        assert "*/" not in escaped

    def test_unescaped_payload_would_have_executed_injected_code(self, tmp_path):
        """
        Demonstrates the vulnerability is real, not hypothetical: the exact
        same payload, WITHOUT the fix applied, closes the comment early and
        the injected statement actually runs -- proving the next test's
        "it doesn't run" assertion is meaningful, not vacuous.
        """
        sentinel = tmp_path / "pwned.flag"
        payload = f"desc */ require('fs').writeFileSync({str(sentinel)!r}, 'x'); /*"
        unescaped_script = f"/** {payload} */\nglobalThis.__ENVSHIELD_OK__ = true;\n"

        result = subprocess.run(
            ["node", "-e", unescaped_script], capture_output=True, text=True
        )

        assert result.returncode == 0, result.stderr
        assert sentinel.exists()

    def test_terminate_and_inject_payload_cannot_run(self, tmp_path):
        """The security invariant itself: attacker-controlled input cannot
        change the generated program's structure -- proven by actually
        running the escaped output and confirming the injected statement
        never executes."""
        sentinel = tmp_path / "pwned.flag"
        payload = f"desc */ require('fs').writeFileSync({str(sentinel)!r}, 'x'); /*"
        escaped = generator._escape_jsdoc_comment(payload)
        script = f"/** {escaped} */\nglobalThis.__ENVSHIELD_OK__ = true;\n"

        result = subprocess.run(["node", "-e", script], capture_output=True, text=True)

        assert result.returncode == 0, result.stderr
        assert not sentinel.exists()

    def test_generate_config_end_to_end_neutralizes_the_terminator(self):
        """Wired into the real code path, not just the helper in isolation."""
        payload = "desc */ globalThis.pwned = true; /*"

        content = generator.generate_config(
            {"KEY": {"description": payload}}, lang="typescript"
        )

        comment_line = next(
            line for line in content.splitlines() if line.startswith("  /**")
        )
        assert comment_line.count("*/") == 1
        assert comment_line.rstrip().endswith("*/")


def _extract_field_expr(content: str, key: str) -> str:
    """Pulls the zod builder expression for one field out of generated TS
    source, e.g. content containing '"ALLOW_EMAILS": z.string()...,' returns
    'z.string()...' with the trailing comma stripped."""
    prefix = f'"{key}": '
    line = next(line for line in content.splitlines() if line.strip().startswith(prefix))
    return line.strip()[len(prefix) :].rstrip(",")


# A minimal, faithful stand-in for the exact five zod methods BL-003's fix
# uses (z.string, z.enum, .transform, .pipe, .default, .optional) -- NOT a
# general zod reimplementation, and not a substitute for verifying against
# the real npm package. This repo has no package.json/npm dependency
# management and no node/npm setup step in CI (see BL-003's BACKLOG.md
# entry) -- adding real `zod` as a devDependency to runtime-test generated
# TypeScript is a real infrastructure decision, not something to introduce
# silently inside this fix. Real-zod verification (real `npm install zod`,
# real `node`, the actual generator output) was performed ad hoc in an
# isolated scratch directory during implementation and is recorded in
# BACKLOG.md's BL-003 entry, but is not part of the committed test suite.
# This harness instead runs the *actual* generated expression text through
# real, bare `node` (already an unconditional test dependency -- see
# `_node_check` above) against hand-written stand-ins that implement these
# five methods' real, documented parse semantics -- proving the generated
# code is syntactically valid and behaves as intended, without a new
# project dependency.
_MINI_ZOD_JS = """
class MiniSchema {
  constructor(parseFn) { this._parse = parseFn; }
  parse(input) { return this._parse(input); }
  transform(fn) { return new MiniSchema((input) => fn(this._parse(input))); }
  pipe(next) { return new MiniSchema((input) => next.parse(this._parse(input))); }
  default(value) {
    // The two zod majors disagree here, and generated code has to be
    // correct on both:
    //   zod 3  -- ZodDefault substitutes the default and then parses it
    //             THROUGH the inner schema, so a default whose type the
    //             inner schema rejects throws on every unset variable.
    //   zod 4  -- returns the default as-is, short-circuiting the chain.
    // DEFAULT_REPARSE selects which is modelled; every bool matrix below
    // is run under both, so a default that only works on one major can
    // never pass again (this is exactly how BL-003's follow-up regression
    // reached a release candidate undetected).
    return new MiniSchema((input) =>
      input === undefined
        ? (DEFAULT_REPARSE ? this._parse(value) : value)
        : this._parse(input));
  }
  optional() {
    return new MiniSchema((input) => (input === undefined ? undefined : this._parse(input)));
  }
}
const z = {
  string: () => new MiniSchema((input) => {
    if (typeof input !== "string") throw new Error("expected string, got " + typeof input);
    return input;
  }),
  enum: (values) => new MiniSchema((input) => {
    if (!values.includes(input)) throw new Error("invalid enum value: " + JSON.stringify(input));
    return input;
  }),
};
"""


def _to_js_literal(value) -> str:
    """`None` represents JS `undefined` (an absent env var) -- the only
    value this generated pipeline's callers ever actually pass besides a
    string, since `process.env[...]` is either a string or undefined,
    never `null`."""
    if value is None:
        return "undefined"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, int):
        return str(value)
    raise TypeError(f"unsupported test input type: {type(value)!r}")


def _run_bool_matrix(tmp_path, expr: str, inputs: list) -> dict:
    """
    Runs `expr.parse(input)` (via the mini-zod stand-in above) for every
    input in `inputs`, returning {JSON-stringified-input: "ok:<value>" |
    "rejected"}. A real SyntaxError/crash in `expr` itself surfaces as a
    non-zero exit with a Python-side assertion failure, distinct from a
    controlled per-input rejection.

    Every matrix is run twice -- once under each zod major's '.default()'
    semantics (see _MINI_ZOD_JS) -- and the two runs must agree exactly.
    Generated code has no way to know which major the consuming project
    installed, so an expression that behaves differently between them is a
    defect regardless of which one happens to be "right": that is precisely
    how a trailing '.default(false)' on the bool chain, correct on zod 4 and
    throwing on zod 3, reached a release candidate. Callers get dual-major
    coverage without having to ask for it.
    """
    zod3 = _run_bool_matrix_under(tmp_path, expr, inputs, reparse=True)
    zod4 = _run_bool_matrix_under(tmp_path, expr, inputs, reparse=False)
    assert zod3 == zod4, (
        "generated expression behaves differently across zod majors -- "
        f"zod 3 (default re-parsed): {zod3}; zod 4 (default returned as-is): {zod4}"
    )
    return zod3


def _run_bool_matrix_under(
    tmp_path, expr: str, inputs: list, *, reparse: bool
) -> dict:
    """One matrix run under a single major's '.default()' semantics."""
    js_inputs = "[" + ", ".join(_to_js_literal(v) for v in inputs) + "]"
    script = (
        f"const DEFAULT_REPARSE = {'true' if reparse else 'false'};\n"
        + _MINI_ZOD_JS
        + f"\nconst _schema = {expr};\n"
        + f"const _inputs = {js_inputs};\n"
        + "const _results = {};\n"
        + "for (const input of _inputs) {\n"
        "  const key = input === undefined ? 'undefined' : JSON.stringify(input);\n"
        "  try {\n"
        "    const value = _schema.parse(input);\n"
        "    _results[key] = 'ok:' + (value === undefined ? 'undefined' : JSON.stringify(value));\n"
        "  } catch (e) {\n"
        "    _results[key] = 'rejected';\n"
        "  }\n"
        "}\n"
        "console.log(JSON.stringify(_results));\n"
    )
    script_path = tmp_path / f"bool_matrix_{'zod3' if reparse else 'zod4'}.js"
    script_path.write_text(script)
    result = subprocess.run(
        ["node", str(script_path)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


class TestTypeScriptBooleanCoercionMatchesTheContract:
    """
    Regression coverage for BL-003: z.coerce.boolean() followed JavaScript
    truthiness, so an explicit "false" silently became `true` at runtime --
    inverting a developer's explicit intent (e.g. a safety switch like
    ALLOW_EMAILS=false actually enabling emails). The fix must accept only
    a case-insensitive "true"/"false", matching schema_types._BOOL_VALUES/
    validate_value exactly, and reject everything else it rejects --
    without silently broadening what's accepted.
    """

    def test_required_bool_field_renders_the_explicit_parser(self):
        content = generator.generate_config(
            {"ALLOW_EMAILS": {"description": "Safety switch.", "type": "bool"}},
            lang="typescript",
        )

        assert (
            '"ALLOW_EMAILS": z.string().transform((s) => s.toLowerCase())'
            '.pipe(z.enum(["true", "false"])).transform((s) => s === "true"),'
            in content
        )
        assert "z.coerce.boolean()" not in content

    def test_bool_field_true_and_false_round_trip_correctly(self, tmp_path):
        """The exact BL-003 scenario: an explicit "false" must parse to
        `false`, not silently invert to `true`."""
        content = generator.generate_config(
            {"ALLOW_EMAILS": {"description": "Safety switch.", "type": "bool"}},
            lang="typescript",
        )
        expr = _extract_field_expr(content, "ALLOW_EMAILS")

        results = _run_bool_matrix(tmp_path, expr, ["true", "false"])

        assert results['"true"'] == "ok:true"
        assert results['"false"'] == "ok:false"

    def test_bool_field_accepts_case_variants(self, tmp_path):
        content = generator.generate_config(
            {"ALLOW_EMAILS": {"description": "Safety switch.", "type": "bool"}},
            lang="typescript",
        )
        expr = _extract_field_expr(content, "ALLOW_EMAILS")

        results = _run_bool_matrix(
            tmp_path, expr, ["True", "FALSE", "TrUe", "fAlSe"]
        )

        assert results['"True"'] == "ok:true"
        assert results['"FALSE"'] == "ok:false"
        assert results['"TrUe"'] == "ok:true"
        assert results['"fAlSe"'] == "ok:false"

    def test_bool_field_rejects_values_the_validator_also_rejects(self, tmp_path):
        """Mirrors schema_types._BOOL_VALUES = {"true", "false"} exactly --
        must not silently broaden what's accepted."""
        content = generator.generate_config(
            {"ALLOW_EMAILS": {"description": "Safety switch.", "type": "bool"}},
            lang="typescript",
        )
        expr = _extract_field_expr(content, "ALLOW_EMAILS")

        results = _run_bool_matrix(
            tmp_path, expr, ["0", "1", "", "no", "yes", " true", "true "]
        )

        for value, outcome in results.items():
            assert outcome == "rejected", f"{value} should have been rejected"

    def test_bool_field_rejects_non_string_input(self, tmp_path):
        """A required field (no default, not optional) must also reject a
        genuinely absent value -- `undefined` is not a string either."""
        content = generator.generate_config(
            {"ALLOW_EMAILS": {"description": "Safety switch.", "type": "bool"}},
            lang="typescript",
        )
        expr = _extract_field_expr(content, "ALLOW_EMAILS")

        results = _run_bool_matrix(tmp_path, expr, [123, True, None])

        assert results["123"] == "rejected"
        assert results["true"] == "rejected"
        assert results["undefined"] == "rejected"

    def test_a_defaulted_bool_defaults_correctly_under_zod3_semantics(
        self, tmp_path
    ):
        """
        The BL-003 follow-up regression, pinned directly.

        A trailing '.default(false)' on the bool chain works on zod 4 (which
        returns the default untouched) and throws on zod 3 (which re-parses
        it through the inner z.string(), receiving a boolean). Verified
        against real zod 3.25.76 and 4.5.4: 'Expected string, received
        boolean' on every unset variable -- a generated config module that
        crashes at startup in exactly the case the default exists for.

        The default therefore has to sit on the inner z.string(), as a
        string, so it takes the same parse path an environment value would.
        This test asserts the zod-3 semantics specifically; the sibling test
        below covers zod 4, and _run_bool_matrix cross-checks every other
        bool case against both.
        """
        content = generator.generate_config(
            {
                "FEATURE_ON": {
                    "description": "Flag.",
                    "defaultValue": "false",
                    "type": "bool",
                }
            },
            lang="typescript",
        )
        expr = _extract_field_expr(content, "FEATURE_ON")

        results = _run_bool_matrix_under(tmp_path, expr, [None], reparse=True)

        assert results["undefined"] == "ok:false"

    def test_a_defaulted_bool_defaults_correctly_under_zod4_semantics(
        self, tmp_path
    ):
        content = generator.generate_config(
            {
                "FEATURE_ON": {
                    "description": "Flag.",
                    "defaultValue": "false",
                    "type": "bool",
                }
            },
            lang="typescript",
        )
        expr = _extract_field_expr(content, "FEATURE_ON")

        results = _run_bool_matrix_under(tmp_path, expr, [None], reparse=False)

        assert results["undefined"] == "ok:false"

    def test_a_defaulted_bool_never_appends_default_after_the_transform(self):
        """
        Structural guard for the same regression: the default must be
        attached to the inner z.string(), never to the end of the chain,
        where its type no longer matches what the chain accepts as input.
        """
        content = generator.generate_config(
            {
                "FEATURE_ON": {
                    "description": "Flag.",
                    "defaultValue": "false",
                    "type": "bool",
                }
            },
            lang="typescript",
        )
        expr = _extract_field_expr(content, "FEATURE_ON")

        assert expr.startswith('z.string().default("false")')
        assert not expr.endswith(".default(false)")
        assert ".default(false)" not in expr

    def test_bool_field_with_default_still_defaults_correctly(self, tmp_path):
        content = generator.generate_config(
            {
                "DEBUG": {
                    "description": "Debug flag.",
                    "defaultValue": "true",
                    "type": "bool",
                }
            },
            lang="typescript",
        )
        expr = _extract_field_expr(content, "DEBUG")

        results = _run_bool_matrix(tmp_path, expr, [None])

        assert results["undefined"] == "ok:true"

    def test_bool_field_marked_optional_still_allows_undefined(self, tmp_path):
        content = generator.generate_config(
            {
                "MAINTENANCE_MODE": {
                    "description": "Maintenance toggle.",
                    "type": "bool",
                    "requiredIf": {"OTHER_FIELD": "x"},
                }
            },
            lang="typescript",
        )
        expr = _extract_field_expr(content, "MAINTENANCE_MODE")

        results = _run_bool_matrix(tmp_path, expr, [None, "false"])

        assert results["undefined"] == "ok:undefined"
        assert results['"false"'] == "ok:false"
