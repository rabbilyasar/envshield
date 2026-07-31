# envshield/tests/core/test_generator.py
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
    assert "new Secret(_parsed[\"LOG_LEVEL\"])" not in content


def test_generate_typescript_empty_schema():
    content = generator.generate_config({}, lang="typescript")

    assert "const _schema = z.object({});" in content
    assert "export const env = _parsed;" in content
