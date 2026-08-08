# envshield/tests/core/test_importer.py
import toml

from envshield.core import importer


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


def test_generate_schema_from_file_does_not_infer_a_type_for_secrets(tmp_path):
    """A secret's value shape (e.g. a URL-shaped connection string) must never drive a non-secret type constraint."""
    env_file = tmp_path / ".env"
    env_file.write_text("DATABASE_URL=postgres://user:pass@localhost/db\n")

    schema_content = importer.generate_schema_from_file(str(env_file))
    schema = toml.loads(schema_content)

    assert schema["DATABASE_URL"]["secret"] is True
    assert "type" not in schema["DATABASE_URL"]
