# envshield/tests/core/test_generator.py
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
    schema = {
        "API_KEY": {
            "description": "3rd party key.",
            "secret": True,
            "defaultValue": "changeme",
        },
    }

    content = generator.generate_config(schema)

    assert "api_key: SecretStr = Field(" in content
    assert "'changeme', description='3rd party key.', alias='API_KEY'" in content


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
    assert '"DEBUG": z.coerce.boolean().default(true),' in content

    # Non-secret fields are passed through directly, not wrapped in Secret.
    assert '"LOG_LEVEL": _parsed["LOG_LEVEL"],' in content
    assert 'new Secret(_parsed["LOG_LEVEL"])' not in content


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
