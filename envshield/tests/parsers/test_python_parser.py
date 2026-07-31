from envshield.parsers._python import PythonParser


def test_python_parser_happy_path(mocker):
    """Tests that the parser correctly extracts top-level variable assignments."""
    mock_file_content = (
        "import os\n\n"
        "SECRET_KEY = '123'\n"
        "DATABASE_URL = os.getenv('DB')\n\n"
        "class MySettings:\n"
        "    API_KEY = 'abc' # Should be ignored\n\n"
        "def my_func():\n"
        "    LOCAL_VAR = True # Should be ignored\n"
    )
    mocker.patch("builtins.open", mocker.mock_open(read_data=mock_file_content))
    mocker.patch("os.path.exists", return_value=True)

    parser = PythonParser()
    variables = parser.get_vars("dummy/config.py")

    assert variables == {"SECRET_KEY", "DATABASE_URL"}


def test_python_parser_sad_path_syntax_error(mocker):
    """Tests that the parser handles a file with a Python syntax error gracefully."""
    mock_file_content = "SECRET_KEY ="  # Invalid syntax
    mocker.patch("builtins.open", mocker.mock_open(read_data=mock_file_content))
    mocker.patch("os.path.exists", return_value=True)

    parser = PythonParser()
    variables = parser.get_vars("broken_config.py")

    assert variables == set()


def test_python_parser_get_values_returns_literal_values(mocker):
    """Tests that get_values=True resolves literal assignments to their string values."""
    mock_file_content = "SECRET_KEY = 'abc123'\nDEBUG = True\nMAX_CONNECTIONS = 10\n"
    mocker.patch("builtins.open", mocker.mock_open(read_data=mock_file_content))
    mocker.patch("os.path.exists", return_value=True)

    parser = PythonParser()
    variables = parser.get_vars("dummy/config.py", get_values=True)

    assert variables == {
        "SECRET_KEY": "abc123",
        "DEBUG": "True",
        "MAX_CONNECTIONS": "10",
    }


def test_python_parser_get_values_handles_non_literal_expressions(mocker):
    """Non-literal expressions (e.g. os.getenv(...)) shouldn't raise; they resolve to ''."""
    mock_file_content = "DATABASE_URL = os.getenv('DB')\n"
    mocker.patch("builtins.open", mocker.mock_open(read_data=mock_file_content))
    mocker.patch("os.path.exists", return_value=True)

    parser = PythonParser()
    variables = parser.get_vars("dummy/config.py", get_values=True)

    assert variables == {"DATABASE_URL": ""}
