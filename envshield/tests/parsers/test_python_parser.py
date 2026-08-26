import pytest

from envshield.core.exceptions import EnvShieldException
from envshield.parsers._python import PythonParser


def test_python_parser_happy_path(mocker):
    """Tests that the parser correctly extracts top-level variable assignments."""
    mock_file_content = "import os\n\nSECRET_KEY = '123'\nDATABASE_URL = os.getenv('DB')\n\nclass MySettings:\n    API_KEY = 'abc' # Should be ignored\n\ndef my_func():\n    LOCAL_VAR = True # Should be ignored\n"
    mocker.patch("builtins.open", mocker.mock_open(read_data=mock_file_content))
    mocker.patch("os.path.exists", return_value=True)

    parser = PythonParser()
    variables = parser.get_vars("dummy/config.py")

    assert variables == {"SECRET_KEY", "DATABASE_URL"}


def test_python_parser_syntax_error_raises_instead_of_returning_empty(mocker):
    """
    BL-002 regression: a syntax error must never be silently treated as
    "declares nothing" -- every caller that diffs this result against a
    schema would read that as a false-clean. Matches
    DockerComposeParser.get_vars's existing raise-on-parse-failure pattern.
    """
    mock_file_content = "SECRET_KEY ="  # Invalid syntax
    mocker.patch("builtins.open", mocker.mock_open(read_data=mock_file_content))
    mocker.patch("os.path.exists", return_value=True)

    parser = PythonParser()

    with pytest.raises(EnvShieldException, match="broken_config.py"):
        parser.get_vars("broken_config.py")


def test_python_parser_syntax_error_message_never_echoes_the_offending_line(mocker):
    """
    BL-002 regression, security invariant: a SyntaxError's default str()
    doesn't include the offending source line's text -- only its own
    '.text' attribute does. The exception message must keep relying on
    str(e) only, so a secret-shaped value sitting on the malformed line
    (a very plausible real case -- an unterminated string is exactly what
    a hand-edited secret assignment produces) never appears in the error.
    """
    sentinel = "sk_live_SYNTHETIC_NOT_A_REAL_SECRET"
    mock_file_content = f'API_KEY = "{sentinel}'  # unterminated string
    mocker.patch("builtins.open", mocker.mock_open(read_data=mock_file_content))
    mocker.patch("os.path.exists", return_value=True)

    parser = PythonParser()

    with pytest.raises(EnvShieldException) as exc_info:
        parser.get_vars("broken_config.py")

    assert sentinel not in str(exc_info.value)


def test_python_parser_type_error_also_raises(mocker):
    """The pre-existing (defensive) TypeError branch gets the same fix as SyntaxError."""
    mocker.patch("os.path.exists", return_value=True)
    mocker.patch("builtins.open", mocker.mock_open(read_data="X = 1"))
    mocker.patch("ast.parse", side_effect=TypeError("unexpected input"))

    parser = PythonParser()

    with pytest.raises(EnvShieldException, match="broken_config.py"):
        parser.get_vars("broken_config.py")


def test_python_parser_non_utf8_content_raises_instead_of_crashing_uncaught(tmp_path):
    """
    BL-092 regression: a binary/non-UTF-8 '.py' file must raise
    EnvShieldException, not an uncaught UnicodeDecodeError. Uses a real
    file on disk (not a mock) since the failure happens inside the actual
    text-mode read, not inside ast.parse.
    """
    path = tmp_path / "broken_config.py"
    path.write_bytes(b"\xff\xfe\x00\x01SECRET=1")

    parser = PythonParser()

    with pytest.raises(EnvShieldException) as exc_info:
        parser.get_vars(str(path))

    assert str(path) in str(exc_info.value)


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
