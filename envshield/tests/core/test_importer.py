# envshield/tests/core/test_importer.py
import toml

from envshield.core import importer
from envshield.core.scanner import MAX_SCANNABLE_SIZE_BYTES


class TestOversizedValuesAreSkippedNotEmbedded:
    """
    Regression coverage for PDF finding 3.7: 'import' used to bake an
    oversized value verbatim into the generated schema as a suggested
    defaultValue, with no guard at all (scan already had one for exactly
    this class of problem -- see MAX_SCANNABLE_SIZE_BYTES). The variable
    itself is still kept; only the oversized default is withheld.
    """

    def test_value_just_below_threshold_is_imported_normally(self, tmp_path):
        value = "A" * (MAX_SCANNABLE_SIZE_BYTES - 1)
        env_file = tmp_path / ".env"
        env_file.write_text(f"BELOW_LIMIT={value}\n")

        schema_content = importer.generate_schema_from_file(str(env_file))
        schema = toml.loads(schema_content)

        assert schema["BELOW_LIMIT"]["defaultValue"] == value

    def test_value_above_threshold_is_not_written_as_default(self, tmp_path):
        value = "A" * (MAX_SCANNABLE_SIZE_BYTES + 1)
        env_file = tmp_path / ".env"
        env_file.write_text(f"OVER_LIMIT={value}\n")

        schema_content = importer.generate_schema_from_file(str(env_file))
        schema = toml.loads(schema_content)

        assert "defaultValue" not in schema["OVER_LIMIT"]
        # Not truncated and partially embedded either -- no prefix of the
        # oversized value appears anywhere in the generated schema.
        assert value[:1000] not in schema_content

    def test_variable_is_preserved_without_its_oversized_default(self, tmp_path):
        value = "A" * (MAX_SCANNABLE_SIZE_BYTES + 1)
        env_file = tmp_path / ".env"
        env_file.write_text(f"OVER_LIMIT={value}\n")

        schema_content = importer.generate_schema_from_file(str(env_file))
        schema = toml.loads(schema_content)

        assert "OVER_LIMIT" in schema
        assert schema["OVER_LIMIT"]["secret"] is False
        assert schema["OVER_LIMIT"]["description"] == "TODO: Add description."

    def test_warning_is_emitted_for_an_oversized_value(self, tmp_path, capsys):
        value = "A" * (MAX_SCANNABLE_SIZE_BYTES + 1)
        env_file = tmp_path / ".env"
        env_file.write_text(f"OVER_LIMIT={value}\n")

        importer.generate_schema_from_file(str(env_file))

        warning = capsys.readouterr().out
        assert "Skipped 1 value(s)" in warning
        assert "OVER_LIMIT" in warning

    def test_multiple_oversized_values_are_all_skipped_and_counted(
        self, tmp_path, capsys
    ):
        value = "A" * (MAX_SCANNABLE_SIZE_BYTES + 1)
        env_file = tmp_path / ".env"
        env_file.write_text(f"FIRST_BIG={value}\nSECOND_BIG={value}\nNORMAL=fine\n")

        schema_content = importer.generate_schema_from_file(str(env_file))
        schema = toml.loads(schema_content)

        assert "defaultValue" not in schema["FIRST_BIG"]
        assert "defaultValue" not in schema["SECOND_BIG"]
        assert schema["NORMAL"]["defaultValue"] == "fine"

        warning = capsys.readouterr().out
        assert "Skipped 2 value(s)" in warning
        assert "FIRST_BIG" in warning
        assert "SECOND_BIG" in warning

    def test_no_warning_when_nothing_is_oversized(self, tmp_path, capsys):
        env_file = tmp_path / ".env"
        env_file.write_text("NORMAL=fine\n")

        importer.generate_schema_from_file(str(env_file))

        assert "Skipped" not in capsys.readouterr().out

    def test_oversized_secret_value_still_never_gets_a_default(self, tmp_path, capsys):
        """
        Non-regression: an oversized *secret* value was already never
        written as a defaultValue (secret classification withholds it
        unconditionally) -- confirms this fix doesn't change that, and
        doesn't add a spurious size warning for a value that was never
        going to get a default in the first place.
        """
        value = "A" * (MAX_SCANNABLE_SIZE_BYTES + 1)
        env_file = tmp_path / ".env"
        env_file.write_text(f"API_SECRET_KEY={value}\n")

        schema_content = importer.generate_schema_from_file(str(env_file))
        schema = toml.loads(schema_content)

        assert schema["API_SECRET_KEY"]["secret"] is True
        assert "defaultValue" not in schema["API_SECRET_KEY"]
        assert value[:1000] not in schema_content
        assert "Skipped" not in capsys.readouterr().out

    def test_merge_variables_from_other_sources_also_skips_oversized_default(
        self, tmp_path
    ):
        """The same guard applies to init's secondary 'other sources' merge path -- the identical bug shape, a different call site."""
        value = "A" * (MAX_SCANNABLE_SIZE_BYTES + 1)
        other_file = tmp_path / ".env"
        other_file.write_text(f"OVER_LIMIT={value}\n")
        schema_dict = {}

        added = importer.merge_variables_from_other_sources(
            schema_dict, [str(other_file)]
        )

        assert added == {str(other_file): ["OVER_LIMIT"]}
        assert "defaultValue" not in schema_dict["OVER_LIMIT"]
        assert schema_dict["OVER_LIMIT"]["secret"] is False


class TestCommentedOutAssignmentsAreWarnedNotImported:
    """
    Regression coverage for PDF finding 3.4: 'import' used to silently
    drop commented-out '#KEY=value' lines with no signal at all -- it must
    now warn how many it found, without importing them into the schema.
    """

    def test_warns_and_excludes_commented_out_variable_from_schema(
        self, tmp_path, capsys
    ):
        env_file = tmp_path / "docker-compose.env"
        env_file.write_text(
            "PAPERLESS_REDIS=redis://broker:6379\n#PAPERLESS_OCR_LANGUAGE=eng\n"
        )

        schema_content = importer.generate_schema_from_file(str(env_file))

        assert "PAPERLESS_REDIS" in schema_content
        assert "PAPERLESS_OCR_LANGUAGE" not in schema_content

        warning = capsys.readouterr().out
        assert (
            "Found 1 commented-out variable assignment(s); these were not imported."
            in warning
        )

    def test_warns_with_correct_count_for_multiple_commented_out_variables(
        self, tmp_path, capsys
    ):
        env_file = tmp_path / "docker-compose.env"
        env_file.write_text(
            "PAPERLESS_REDIS=redis://broker:6379\n"
            "#PAPERLESS_OCR_LANGUAGE=eng\n"
            "#PAPERLESS_OCR_LANGUAGES=deu eng\n"
            "#PAPERLESS_TIME_ZONE=America/Chicago\n"
            "#PAPERLESS_SECRET_KEY=change-me\n"
            "#PAPERLESS_ADMIN_USER=admin\n"
        )

        schema_content = importer.generate_schema_from_file(str(env_file))

        for name in (
            "PAPERLESS_OCR_LANGUAGE",
            "PAPERLESS_OCR_LANGUAGES",
            "PAPERLESS_TIME_ZONE",
            "PAPERLESS_SECRET_KEY",
            "PAPERLESS_ADMIN_USER",
        ):
            assert name not in schema_content

        warning = capsys.readouterr().out
        assert (
            "Found 5 commented-out variable assignment(s); these were not imported."
            in warning
        )

    def test_no_warning_when_nothing_is_commented_out(self, tmp_path, capsys):
        env_file = tmp_path / ".env"
        env_file.write_text("DATABASE_URL=postgres://localhost/db\n")

        importer.generate_schema_from_file(str(env_file))

        assert "commented-out" not in capsys.readouterr().out

    def test_ordinary_comments_do_not_trigger_a_warning(self, tmp_path, capsys):
        env_file = tmp_path / ".env"
        env_file.write_text(
            "# Configuration for the app.\n"
            "# See https://example.com/docs?ref=readme\n"
            "DATABASE_URL=postgres://localhost/db\n"
        )

        importer.generate_schema_from_file(str(env_file))

        assert "commented-out" not in capsys.readouterr().out


def test_import_command_python_settings_file(tmp_path):
    """Tests that a Django/Flask-style settings.py is correctly converted into a schema."""
    settings_content = "SECRET_KEY = 'django-insecure-abc123'\nDEBUG = True\nDATABASE_URL = 'postgres://user:pass@localhost/db'\n"
    settings_file = tmp_path / "settings.py"
    settings_file.write_text(settings_content)

    schema_content = importer.generate_schema_from_file(str(settings_file))

    assert "SECRET_KEY" in schema_content
    assert "DEBUG" in schema_content
    assert "DATABASE_URL" in schema_content
    assert "secret = true" in schema_content.split("[SECRET_KEY]")[1]


def test_import_command_happy_path(tmp_path):
    """Tests that a standard .env file is correctly converted into a schema."""
    env_content = "DATABASE_URL=postgres://user:pass@localhost/db\nLOG_LEVEL=info\nSTRIPE_API_KEY=sk_live_123456789abcdefghijklmnopqrstuv\n"
    env_file = tmp_path / ".env.prod"
    env_file.write_text(env_content)

    schema_content = importer.generate_schema_from_file(str(env_file))

    assert "DATABASE_URL" in schema_content
    assert "LOG_LEVEL" in schema_content
    assert "STRIPE_API_KEY" in schema_content

    # Test that the secret was correctly identified
    assert "secret = true" in schema_content.split("[STRIPE_API_KEY]")[1]

    # Test that the non-secret was correctly identified
    assert "secret = false" in schema_content.split("[LOG_LEVEL]")[1]


def test_classify_variable_does_not_flag_compound_word_false_positives():
    """
    Regression: substring matching on secret keywords flagged compound words
    that merely *contain* a keyword -- e.g. MONKEY_PATCH_ENABLED contains
    "key" and AUTHOR_NAME contains "auth" -- even though neither is a secret.
    """
    is_secret, _ = importer._classify_variable("MONKEY_PATCH_ENABLED", "true")
    assert is_secret is False

    is_secret, _ = importer._classify_variable("AUTHOR_NAME", "Jane Doe")
    assert is_secret is False

    is_secret, _ = importer._classify_variable("KEYBOARD_LAYOUT", "us")
    assert is_secret is False


def test_classify_variable_still_flags_real_secret_keywords():
    """Token-based matching must still catch the real, non-compound cases."""
    is_secret, _ = importer._classify_variable("API_KEY", "abcdef")
    assert is_secret is True

    is_secret, _ = importer._classify_variable("AUTH_TOKEN", "abcdef")
    assert is_secret is True

    is_secret, _ = importer._classify_variable("DB_PASSWORD", "abcdef")
    assert is_secret is True


def test_importer_classifies_correctly(mocker):
    """Tests the importer's smart classification logic."""
    variables = {
        "STRIPE_SECRET_KEY": "sk_live_12345",
        "API_TOKEN": "some_random_string_without_pattern",
        "DEBUG": "True",
        "HOST": "localhost",
        "APP_NAME": "My Awesome App",
        "OPTIONAL_FLAG": "",
    }

    mock_parser_instance = mocker.Mock()
    mock_parser_instance.get_vars.return_value = variables

    # Add this mock to bypass the file existence check
    mocker.patch("os.path.exists", return_value=True)

    mocker.patch(
        "envshield.core.importer.get_parser", return_value=mock_parser_instance
    )

    # Now, run the function that uses get_parser
    schema_content = importer.generate_schema_from_file("dummy.env")

    # Assertions remain the same
    assert "secret = true" in schema_content.split("[STRIPE_SECRET_KEY]")[1]
    assert "secret = true" in schema_content.split("[API_TOKEN]")[1]

    assert 'defaultValue = "True"' in schema_content.split("[DEBUG]")[1]
    assert 'defaultValue = "localhost"' in schema_content.split("[HOST]")[1]

    assert "secret = false" in schema_content.split("[APP_NAME]")[1]
    # Any non-secret var with a concrete value gets that value suggested as
    # the default now, not just names on a small hardcoded whitelist.
    assert 'defaultValue = "My Awesome App"' in schema_content.split("[APP_NAME]")[1]
    # A blank value has no signal to suggest a default from at all.
    assert "defaultValue" not in schema_content.split("[OPTIONAL_FLAG]")[1]


def test_classify_variable_suggests_default_for_any_nonsecret_value_with_content():
    """
    Regression: default-value suggestion used to be limited to a small
    hardcoded whitelist of variable names (DEBUG, LOG_LEVEL, PORT, HOST,
    ...), so importing a real project's config -- most of whose non-secret
    variables aren't on that list -- suggested zero defaults even for
    obviously safe, stable values like a local dev DB name or cache port.
    """
    is_secret, default = importer._classify_variable("DB_NAME", "alpha")
    assert is_secret is False
    assert default == "alpha"

    is_secret, default = importer._classify_variable("CACHE_PORT", "6379")
    assert is_secret is False
    assert default == "6379"


def test_classify_variable_suggests_no_default_for_blank_value():
    is_secret, default = importer._classify_variable("CACHE_HOST", "")
    assert is_secret is False
    assert default is None


def test_classify_variable_treats_next_public_prefixed_vars_as_non_secret():
    """
    Regression: NEXT_PUBLIC_/VITE_/REACT_APP_/NUXT_PUBLIC_-prefixed vars are
    inlined straight into the client-side bundle by design -- they are
    public regardless of what their name contains. A Stripe *publishable*
    key legitimately has "key" in its name (NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY)
    but was getting flagged secret purely from the keyword heuristic, which
    would wrap it in a masking Secret<T> in generated code and break the app
    (it needs to be a plain embeddable string).
    """
    is_secret, default = importer._classify_variable(
        "NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY", "pk_test_fakekeyforfakekeyforfakekey"
    )
    assert is_secret is False
    assert default == "pk_test_fakekeyforfakekeyforfakekey"

    is_secret, _ = importer._classify_variable("VITE_API_KEY", "abc123")
    assert is_secret is False

    is_secret, _ = importer._classify_variable(
        "REACT_APP_AUTH_DOMAIN", "example.auth0.com"
    )
    assert is_secret is False


def test_classify_variable_treats_dotenv_public_key_as_non_secret():
    """dotenvx's own DOTENV_PUBLIC_KEY holds a public (not secret) encryption key, despite the name."""
    is_secret, _ = importer._classify_variable(
        "DOTENV_PUBLIC_KEY", "03b3c5a1a1f4b5b2f1e2c3d4e5f6a7b8c9d0e1f2"
    )
    assert is_secret is False


def test_classify_variable_still_flags_a_real_secret_under_a_public_prefixed_name():
    """
    A conventionally-public prefix must never override a high-confidence
    match against an actual secret-shaped *value* -- a real secret key
    accidentally placed under a NEXT_PUBLIC_ name is a genuine leak, not a
    false positive to suppress.
    """
    is_secret, _ = importer._classify_variable(
        "NEXT_PUBLIC_STRIPE_SECRET_KEY", "sk_test_fakekeyforfakekeyforfakekey"
    )
    assert is_secret is True


def test_stripe_publishable_key_is_not_flagged_by_the_secret_scanner():
    """
    Regression: the scanner's Stripe pattern used to match both 'sk_' and
    'pk_' prefixes, so a publishable key (meant to be public, e.g. sitting
    right in committed frontend source) triggered a false "secret found" DANGER.
    """
    import re

    from envshield.core.scanner import SECRET_PATTERNS

    publishable = "pk_test_fakekeyforfakekeyforfakekey"
    secret = "sk_test_fakekeyforfakekeyforfakekey"

    assert not any(re.search(p["pattern"], publishable) for p in SECRET_PATTERNS)
    assert any(re.search(p["pattern"], secret) for p in SECRET_PATTERNS)


def test_infer_type_recognizes_int():
    assert importer._infer_type("MAX_RETRIES", "3") == "int"


def test_infer_type_recognizes_port_by_key_name():
    assert importer._infer_type("API_PORT", "8080") == "port"
    assert importer._infer_type("DB_PORT", "5432") == "port"


def test_infer_type_recognizes_bool():
    assert importer._infer_type("DEBUG", "true") == "bool"
    assert importer._infer_type("DEBUG", "False") == "bool"


def test_infer_type_recognizes_url():
    assert importer._infer_type("API_BASE_URL", "https://api.example.com") == "url"


def test_infer_type_recognizes_email():
    assert importer._infer_type("ADMIN_EMAIL", "ops@example.com") == "email"


def test_infer_type_returns_none_for_a_plain_string():
    assert importer._infer_type("LOG_LEVEL", "info") is None


def test_infer_type_returns_none_for_blank_value():
    assert importer._infer_type("SOMETHING", "") is None


def test_generate_schema_from_file_includes_inferred_types(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "API_PORT=8080\nDEBUG=true\nAPI_BASE_URL=https://api.example.com\nLOG_LEVEL=info\n"
    )

    schema_content = importer.generate_schema_from_file(str(env_file))
    schema = toml.loads(schema_content)

    assert schema["API_PORT"]["type"] == "port"
    assert schema["DEBUG"]["type"] == "bool"
    assert schema["API_BASE_URL"]["type"] == "url"
    assert "type" not in schema["LOG_LEVEL"]


def test_generate_schema_from_file_still_infers_a_type_for_secrets(tmp_path):
    """A secret still gets a shape constraint (e.g. type = "url") from its
    sample value -- inferring type never exposes the value itself, only its
    shape, so a secret field isn't left with zero validation. defaultValue
    is the one field that would actually leak the value, and that must
    still never be set for a secret."""
    env_file = tmp_path / ".env"
    env_file.write_text("DATABASE_URL=postgres://user:pass@localhost/db\n")

    schema_content = importer.generate_schema_from_file(str(env_file))
    schema = toml.loads(schema_content)

    assert schema["DATABASE_URL"]["secret"] is True
    assert schema["DATABASE_URL"]["type"] == "url"
    assert "defaultValue" not in schema["DATABASE_URL"]


def test_generate_schema_from_file_does_not_leak_a_postgresql_password(tmp_path):
    """
    P0 regression: a real embedded-credential 'postgresql://' connection
    string (the SQLAlchemy/Django/psycopg scheme, as opposed to the shorter
    'postgres://') used to fall through both the SECRET_PATTERNS value
    check and the DATABASE_URL name-keyword heuristic, getting classified
    non-secret with its literal password written into the generated schema
    as defaultValue -- a file explicitly designed to be committed to git.
    """
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DATABASE_URL=postgresql://appuser:sup3rsecret@db.internal.prod:5432/appdb\n"
    )

    schema_content = importer.generate_schema_from_file(str(env_file))
    schema = toml.loads(schema_content)

    assert schema["DATABASE_URL"]["secret"] is True
    assert "sup3rsecret" not in schema_content
    assert "defaultValue" not in schema["DATABASE_URL"]


def test_generate_schema_from_file_interactive_overrides_existing_declaration_for_a_rescanned_var(
    tmp_path, mocker
):
    """
    Real bug: '--interactive --force' let a user re-confirm a variable's
    classification live, then silently discarded that answer and kept the
    stale existing schema entry instead -- defeating the entire point of
    '--interactive' re-classification for anything already declared.
    """
    settings = tmp_path / "settings.py"
    settings.write_text("DATABASE_URL = 'postgres://user:pass@localhost/db'\n")

    existing_schema = {
        "DATABASE_URL": {
            "description": "TODO: Add description.",
            "secret": False,
            "defaultValue": "thisisdatabasse",
        },
        "STRIPE_API": {"description": "TODO: Add description.", "secret": True},
    }

    # Live answers: not a secret, use the freshly-scanned value as the default.
    mocker.patch("questionary.confirm").return_value.ask.side_effect = [False, True]

    schema_content = importer.generate_schema_from_file(
        str(settings), interactive=True, existing_schema=existing_schema
    )
    schema = toml.loads(schema_content)

    assert schema["DATABASE_URL"]["defaultValue"] == "postgres://user:pass@localhost/db"
    # STRIPE_API wasn't in this scan at all -- still preserved unchanged.
    assert schema["STRIPE_API"] == existing_schema["STRIPE_API"]


def test_generate_schema_from_file_noninteractive_keeps_existing_declaration_for_a_rescanned_var(
    tmp_path,
):
    """Without --interactive, the existing declaration still wins outright -- protecting an unreviewed re-scan."""
    settings = tmp_path / "settings.py"
    settings.write_text("DATABASE_URL = 'postgres://user:pass@localhost/db'\n")

    existing_schema = {
        "DATABASE_URL": {
            "description": "TODO: Add description.",
            "secret": False,
            "defaultValue": "thisisdatabasse",
        }
    }

    schema_content = importer.generate_schema_from_file(
        str(settings), interactive=False, existing_schema=existing_schema
    )
    schema = toml.loads(schema_content)

    assert schema["DATABASE_URL"]["defaultValue"] == "thisisdatabasse"


def test_merge_variables_from_other_sources_adds_only_new_keys(tmp_path):
    other_file = tmp_path / "settings.py"
    other_file.write_text(
        "SECRET_KEY = 'sk_live_x'\nDEBUG = True\nLOG_LEVEL = 'info'\n"
    )
    schema_dict = {"SECRET_KEY": {"description": "x", "secret": True}}

    added = importer.merge_variables_from_other_sources(schema_dict, [str(other_file)])

    assert added == {str(other_file): ["DEBUG", "LOG_LEVEL"]}
    assert schema_dict["SECRET_KEY"] == {"description": "x", "secret": True}
    assert schema_dict["LOG_LEVEL"]["defaultValue"] == "info"
    assert schema_dict["DEBUG"]["type"] == "bool"


def test_generate_schema_from_file_does_not_leak_a_sentry_style_dsn(tmp_path):
    """
    Regression, confirmed via real onboarding testing: a DSN-style value
    (a single long key before '@', no ':pass' pair -- e.g. a real Sentry
    DSN) used to fall through both the SECRET_PATTERNS value check and the
    SENTRY_DSN name-keyword heuristic, getting classified non-secret with
    the literal key written into the generated schema as defaultValue.
    """
    env_file = tmp_path / ".env"
    env_file.write_text(
        "SENTRY_DSN=https://a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6@o123456.ingest.sentry.io/7890123\n"
    )

    schema_content = importer.generate_schema_from_file(str(env_file))
    schema = toml.loads(schema_content)

    assert schema["SENTRY_DSN"]["secret"] is True
    assert "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6" not in schema_content
    assert "defaultValue" not in schema["SENTRY_DSN"]


def test_merge_variables_from_other_sources_no_op_when_nothing_new(tmp_path):
    other_file = tmp_path / ".env"
    other_file.write_text("SECRET_KEY=x\n")
    schema_dict = {"SECRET_KEY": {"description": "x", "secret": True}}

    added = importer.merge_variables_from_other_sources(schema_dict, [str(other_file)])

    assert added == {}
    assert schema_dict == {"SECRET_KEY": {"description": "x", "secret": True}}


class TestImportRecognizesRealEnvironmentReads:
    """
    Regression coverage for PDF findings 3.2/3.3/3.5: 'import' previously
    discovered variables via PythonParser's top-level-assignment-only scan
    -- blind to anything inside a class body (3.2, Flask/pydantic-settings),
    and reporting the Python assignment TARGET rather than the real env
    var name for any call-wrapped read (3.3), while also treating every
    unrelated top-level constant as a candidate variable (3.5). Each
    example below is lettered exactly as in the reconnaissance report and
    was independently verified against the pre-fix code to reproduce the
    described bug.
    """

    def test_a_flask_class_based_config_with_a_matching_name(self, tmp_path):
        """Example A."""
        config = tmp_path / "config.py"
        config.write_text(
            "import os\nclass Config:\n    SECRET_KEY = os.environ.get('SECRET_KEY')\n"
        )

        schema_content = importer.generate_schema_from_file(str(config))

        assert "[SECRET_KEY]" in schema_content
        assert "secret = true" in schema_content.split("[SECRET_KEY]")[1]

    def test_a_superset_style_assignment_where_the_name_differs(self, tmp_path):
        """
        Example B, and the exact real-world repro (Apache Superset's real
        config.py): the schema must record the actual environment variable
        name the app reads, never the differently-named Python attribute
        it happens to be assigned to.
        """
        config = tmp_path / "config.py"
        config.write_text(
            "import os\n"
            "class Config:\n"
            "    SECRET_KEY = os.environ.get('SUPERSET_SECRET_KEY') or 'CHANGE_ME_SECRET_KEY'\n"
        )

        schema_content = importer.generate_schema_from_file(str(config))

        assert "[SUPERSET_SECRET_KEY]" in schema_content
        assert "[SECRET_KEY]" not in schema_content
        # The fake placeholder fallback must never surface as a suggested
        # default -- the name is secret-keyword-classified regardless of
        # the recovered value, and a secret classification never suggests one.
        assert (
            "CHANGE_ME_SECRET_KEY"
            not in schema_content.split("[SUPERSET_SECRET_KEY]")[1].split("\n\n")[0]
        )
        assert "secret = true" in schema_content.split("[SUPERSET_SECRET_KEY]")[1]

    def test_a_pydantic_field_with_a_manual_getenv_default(self, tmp_path):
        """Example C."""
        config = tmp_path / "config.py"
        config.write_text(
            "import os\n"
            "from pydantic_settings import BaseSettings\n"
            "class Settings(BaseSettings):\n"
            "    database_url: str = os.getenv('DATABASE_URL')\n"
        )

        schema_content = importer.generate_schema_from_file(str(config))

        assert "[DATABASE_URL]" in schema_content

    def test_django_settings_noise_is_excluded(self, tmp_path):
        """Example D -- the exact class of noise reported against Saleor's real 1,293-line settings.py."""
        settings = tmp_path / "settings.py"
        settings.write_text(
            "import os\n"
            "SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY')\n"
            "DATABASE_URL = os.getenv('DATABASE_URL')\n"
            "PROJECT_ROOT = 'BASE_DIR/foo'\n"
            "ROOT_URLCONF = 'project.urls'\n"
            "APPEND_SLASH = True\n"
        )

        schema_content = importer.generate_schema_from_file(str(settings))
        schema = toml.loads(schema_content)

        assert set(schema.keys()) == {"DJANGO_SECRET_KEY", "DATABASE_URL"}

    def test_a_direct_environment_subscript(self, tmp_path):
        """Example E."""
        config = tmp_path / "config.py"
        config.write_text("import os\nAPI_KEY = os.environ['REAL_API_KEY']\n")

        schema_content = importer.generate_schema_from_file(str(config))

        assert "[REAL_API_KEY]" in schema_content
        assert "[API_KEY]" not in schema_content

    def test_a_type_cast_wrapped_defaulted_read(self, tmp_path):
        """Example F -- the wrapper needs no special handling; the default and inferred type both survive."""
        config = tmp_path / "config.py"
        config.write_text("import os\nPORT = int(os.getenv('PORT', '8000'))\n")

        schema_content = importer.generate_schema_from_file(str(config))
        schema = toml.loads(schema_content)

        assert schema["PORT"]["defaultValue"] == "8000"
        assert schema["PORT"]["type"] == "port"

    def test_envshields_own_generated_python_config_is_readable(self, tmp_path):
        """
        Example G, and the PDF's own pointed observation: 'import' must be
        able to read back the exact format 'envshield generate --lang
        python' produces -- Field(..., alias=...), no os.environ call
        anywhere in the file at all.
        """
        from envshield.core import generator

        original_schema = {
            "DATABASE_URL": {"description": "DB URL", "secret": True},
            "LOG_LEVEL": {"description": "Log verbosity", "defaultValue": "info"},
        }
        generated_source = generator.generate_config(original_schema, "python")

        config = tmp_path / "config.py"
        config.write_text(generated_source)

        schema_content = importer.generate_schema_from_file(str(config))
        schema = toml.loads(schema_content)

        assert set(schema.keys()) == {"DATABASE_URL", "LOG_LEVEL"}
        assert schema["LOG_LEVEL"]["defaultValue"] == "info"

    def test_repeated_reads_of_the_same_variable_keep_the_first_default(self, tmp_path):
        """
        Deterministic dedup rule, explicitly tested per the approved
        design: multiple reads of the same env var collapse to one schema
        entry, keeping the FIRST occurrence's recovered default -- later,
        conflicting defaults are never reconciled or merged.
        """
        config = tmp_path / "config.py"
        config.write_text(
            "import os\n"
            "PORT = os.getenv('PORT', '8000')\n"
            "BACKUP_PORT = os.getenv('PORT', '9000')\n"
        )

        schema_content = importer.generate_schema_from_file(str(config))
        schema = toml.loads(schema_content)

        assert set(schema.keys()) == {"PORT"}
        assert schema["PORT"]["defaultValue"] == "8000"

    def test_a_mixed_file_ignores_unrelated_literal_assignments(self, tmp_path):
        """
        As soon as a file contains at least one real environment read, the
        new discovery path is authoritative for the whole file -- an
        unrelated, non-env-reading constant sitting in the same file (the
        exact shape of Django-style noise) must not also appear.
        """
        config = tmp_path / "config.py"
        config.write_text(
            "import os\n"
            "API_KEY = os.environ.get('REAL_API_KEY')\n"
            "APP_NAME = 'My Cool App'\n"
            "VERSION = '1.0.0'\n"
        )

        schema_content = importer.generate_schema_from_file(str(config))
        schema = toml.loads(schema_content)

        assert set(schema.keys()) == {"REAL_API_KEY"}

    def test_a_zero_env_read_file_falls_back_to_the_old_top_level_scan_unchanged(
        self, tmp_path
    ):
        """
        Explicit coverage for the approved compatibility fallback: a
        plain, flat, already-resolved config module with no os.environ
        call anywhere continues to be read exactly as before this fix,
        via PythonParser's top-level-literal scan -- not silently emptied.
        """
        config = tmp_path / "config.py"
        config.write_text(
            "SECRET_KEY = 'django-insecure-abc123'\nDEBUG = True\nDATABASE_URL = 'postgres://user:pass@localhost/db'\n"
        )

        schema_content = importer.generate_schema_from_file(str(config))
        schema = toml.loads(schema_content)

        assert set(schema.keys()) == {"SECRET_KEY", "DEBUG", "DATABASE_URL"}
        assert schema["SECRET_KEY"]["secret"] is True

    def test_merge_variables_from_other_sources_uses_the_new_discovery_path_too(
        self, tmp_path
    ):
        """merge_variables_from_other_sources shares the exact same fix -- not a separate, potentially-diverging implementation."""
        other_file = tmp_path / "config.py"
        other_file.write_text(
            "import os\n"
            "SECRET_KEY = os.environ.get('SUPERSET_SECRET_KEY')\n"
            "PROJECT_ROOT = 'noise'\n"
        )
        schema_dict = {}

        added = importer.merge_variables_from_other_sources(
            schema_dict, [str(other_file)]
        )

        assert added == {str(other_file): ["SUPERSET_SECRET_KEY"]}
        assert "PROJECT_ROOT" not in schema_dict
