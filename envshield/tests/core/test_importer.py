# envshield/tests/core/test_importer.py
from envshield.core import importer


def test_import_command_happy_path(tmp_path):
    """Tests that a standard .env file is correctly converted into a schema."""
    env_content = (
        "DATABASE_URL=postgres://user:pass@localhost/db\n"
        "LOG_LEVEL=info\n"
        "STRIPE_API_KEY=sk_live_123456789abcdefghijklmnopqrstuv\n"
    )
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


def test_importer_classifies_correctly(mocker):
    """Tests the importer's smart classification logic."""
    variables = {
        "STRIPE_SECRET_KEY": "sk_live_12345",
        "API_TOKEN": "some_random_string_without_pattern",
        "DEBUG": "True",
        "HOST": "localhost",
        "APP_NAME": "My Awesome App",
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
    assert "defaultValue" not in schema_content.split("[APP_NAME]")[1]
