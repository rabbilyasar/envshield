import pytest
from envshield.parsers._dotenv import DotenvParser


def test_dotenv_parser_happy_path(mocker):
    """Tests that the parser correctly extracts variables from a standard .env file."""
    mock_file_content = "KEY1=VALUE1\nSECRET_KEY=12345\n"
    mocker.patch("builtins.open", mocker.mock_open(read_data=mock_file_content))
    mocker.patch("os.path.exists", return_value=True)

    parser = DotenvParser()
    variables = parser.get_vars("dummy/path/.env")

    assert variables == {"KEY1", "SECRET_KEY"}


def test_dotenv_parser_with_comments_and_whitespace(mocker):
    """Tests that the parser correctly ignores comments, blank lines, and extra whitespace."""
    mock_file_content = (
        "# This is a comment\n   KEY1 = VALUE1 # Inline comment\n\nKEY2=VALUE2\n"
    )
    mocker.patch("builtins.open", mocker.mock_open(read_data=mock_file_content))
    mocker.patch("os.path.exists", return_value=True)

    parser = DotenvParser()
    variables = parser.get_vars("dummy/path/.env")

    assert variables == {"KEY1", "KEY2"}


def test_dotenv_parser_sad_path_file_not_found(mocker):
    """Tests that the parser raises a FileNotFoundError if the file doesn't exist."""
    mocker.patch("os.path.exists", return_value=False)
    parser = DotenvParser()

    with pytest.raises(FileNotFoundError):
        parser.get_vars("non_existent_file.env")


def test_dotenv_parser_sad_path_empty_file(mocker):
    """Tests that the parser returns an empty set for an empty file."""
    mocker.patch("builtins.open", mocker.mock_open(read_data=""))
    mocker.patch("os.path.exists", return_value=True)

    parser = DotenvParser()
    variables = parser.get_vars("empty.env")

    assert variables == set()
