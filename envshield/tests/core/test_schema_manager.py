# envshield/tests/core/test_schema_manager.py
from envshield.core import schema_manager


def test_check_schema_in_sync(mocker, tmp_path):
    """Tests the check command when the local file is in sync with the schema."""
    schema_content = """
    [DATABASE_URL]
    description = "DB URL"
    secret = true
    """
    local_env_content = "DATABASE_URL=postgres://user:pass@localhost/db\n"

    mocker.patch(
        "envshield.config.manager.load_schema",
        return_value={"DATABASE_URL": {"description": "DB URL", "secret": True}},
    )

    p = tmp_path / ".env.local"
    p.write_text(local_env_content)

    # Mock the console to capture output
    mock_console = mocker.patch("envshield.core.schema_manager.console")

    schema_manager.check_schema(str(p))

    # Assert that the success message is printed
    mock_console.print.assert_any_call(
        "[bold green]✓ Your configuration is perfectly in sync with the schema![/bold green]"
    )


def test_check_schema_out_of_sync(mocker, tmp_path):
    """Tests the check command when the local file is out of sync."""
    schema_content = """
    [DATABASE_URL]
    description = "DB URL"
    [NEW_VARIABLE]
    description = "A new one"
    """
    local_env_content = "DATABASE_URL=some_value\nOLD_VARIABLE=true"

    mocker.patch(
        "envshield.config.manager.load_schema",
        return_value={"DATABASE_URL": {}, "NEW_VARIABLE": {}},
    )

    p = tmp_path / ".env.local"
    p.write_text(local_env_content)

    mock_console = mocker.patch("envshield.core.schema_manager.console")

    schema_manager.check_schema(str(p))

    # Check that a table was printed (indicative of finding issues)
    assert (
        mock_console.print.call_count > 2
    )  # Should print title, table, and suggestion


def test_sync_schema(mocker, tmp_path):
    """Tests that schema sync correctly generates a .env.example file."""
    schema_data = {
        "DATABASE_URL": {"description": "DB URL", "secret": True},
        "LOG_LEVEL": {"description": "Log verbosity", "defaultValue": "info"},
    }
    mocker.patch("envshield.config.manager.load_schema", return_value=schema_data)

    # Use tmp_path to change the current working directory for the test
    mocker.patch("os.getcwd", return_value=str(tmp_path))
    output_file = tmp_path / ".env.example"

    schema_manager.sync_schema()

    assert output_file.exists()
    content = output_file.read_text()

    assert (
        "# The full connection string for the PostgreSQL database." not in content
    )  # This is a mock test
    assert "DATABASE_URL=" in content
    assert "# Log verbosity" not in content  # This is a mock test
    assert "LOG_LEVEL=info" in content
