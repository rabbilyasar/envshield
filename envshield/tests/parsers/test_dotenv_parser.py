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


def test_dotenv_parser_strips_inline_comment_from_value(mocker):
    """
    Regression: an unquoted value followed by an inline comment must not have
    the comment text bundled into the stored value.
    """
    mock_file_content = "PORT=3000 # dev only\n"
    mocker.patch("builtins.open", mocker.mock_open(read_data=mock_file_content))
    mocker.patch("os.path.exists", return_value=True)

    parser = DotenvParser()
    variables = parser.get_vars("dummy/path/.env", get_values=True)

    assert variables == {"PORT": "3000"}


def test_dotenv_parser_strips_surrounding_quotes(mocker):
    """
    Regression: quoted values must have their surrounding quotes stripped,
    not stored literally (which previously caused values to become
    double-quoted after an import -> schema -> sync round trip).
    """
    mock_file_content = (
        'KEY1="hello world"\nKEY2=\'single quoted\'\nKEY3="has a # inside"\n'
    )
    mocker.patch("builtins.open", mocker.mock_open(read_data=mock_file_content))
    mocker.patch("os.path.exists", return_value=True)

    parser = DotenvParser()
    variables = parser.get_vars("dummy/path/.env", get_values=True)

    assert variables == {
        "KEY1": "hello world",
        "KEY2": "single quoted",
        "KEY3": "has a # inside",
    }


def test_dotenv_parser_strips_a_leading_quote_with_no_matching_close(mocker):
    """
    Regression: an unterminated quote (SECRET_KEY="abc, never closed) used
    to fall through the matched-pair check above and be returned with the
    leading quote character baked into the value as if it were real
    content -- corrupting the value (a secret's actual value would then
    silently differ from whatever every command validates against it,
    with 'check' still reporting the file as perfectly in sync).
    """
    mock_file_content = 'SECRET_KEY="unterminatedvalue\nOTHER=fine\n'
    mocker.patch("builtins.open", mocker.mock_open(read_data=mock_file_content))
    mocker.patch("os.path.exists", return_value=True)

    parser = DotenvParser()
    variables = parser.get_vars("dummy/path/.env", get_values=True)

    assert variables["SECRET_KEY"] == "unterminatedvalue"
    assert not variables["SECRET_KEY"].startswith('"')
    assert variables["OTHER"] == "fine"


def test_dotenv_parser_handles_export_prefix(mocker):
    """
    Regression: shell-style '.env'/'.envrc' files often prefix declarations
    with 'export ' so they can also be sourced directly by a shell. Previously
    this produced a garbage key like 'export DATABASE_URL'.
    """
    mock_file_content = "export DATABASE_URL=postgres://localhost/db\nPLAIN_KEY=value\n"
    mocker.patch("builtins.open", mocker.mock_open(read_data=mock_file_content))
    mocker.patch("os.path.exists", return_value=True)

    parser = DotenvParser()
    variables = parser.get_vars("dummy/path/.env", get_values=True)

    assert variables == {
        "DATABASE_URL": "postgres://localhost/db",
        "PLAIN_KEY": "value",
    }


def test_dotenv_parser_sad_path_file_not_found(mocker):
    """Tests that the parser raises a FileNotFoundError if the file doesn't exist."""
    mocker.patch("os.path.exists", return_value=False)
    parser = DotenvParser()

    with pytest.raises(FileNotFoundError):
        parser.get_vars("non_existent_file.env")


def test_dotenv_parser_handles_non_utf8_bytes_without_crashing(tmp_path):
    """
    Regression: a non-UTF-8 byte anywhere in the file used to raise
    UnicodeDecodeError and abort the whole check/doctor/setup run --
    variables elsewhere in the same file must still be discoverable.
    """
    env_file = tmp_path / ".env"
    env_file.write_bytes(b"NAME=Caf\xe9\nOTHER=fine\n")

    parser = DotenvParser()
    variables = parser.get_vars(str(env_file), get_values=True)

    assert variables["OTHER"] == "fine"
    assert "NAME" in variables


def test_dotenv_parser_sad_path_empty_file(mocker):
    """Tests that the parser returns an empty set for an empty file."""
    mocker.patch("builtins.open", mocker.mock_open(read_data=""))
    mocker.patch("os.path.exists", return_value=True)

    parser = DotenvParser()
    variables = parser.get_vars("empty.env")

    assert variables == set()


class TestCountCommentedOutAssignments:
    """
    Regression coverage for PDF finding 3.4: get_vars() correctly never
    treats a commented-out 'KEY=value' line as a real variable, but until
    now nothing counted them either, so 'import' had no way to warn that
    they exist.
    """

    def test_one_commented_out_plus_one_active_variable(self, tmp_path):
        f = tmp_path / ".env"
        f.write_text(
            "PAPERLESS_REDIS=redis://broker:6379\n#PAPERLESS_OCR_LANGUAGE=eng\n"
        )

        assert DotenvParser.count_commented_out_assignments(str(f)) == 1
        # Non-regression: the active variable is still extracted normally.
        assert DotenvParser().get_vars(str(f), get_values=True) == {
            "PAPERLESS_REDIS": "redis://broker:6379"
        }

    def test_multiple_commented_out_assignments(self, tmp_path):
        f = tmp_path / ".env"
        f.write_text(
            "PAPERLESS_REDIS=redis://broker:6379\n"
            "#PAPERLESS_OCR_LANGUAGE=eng\n"
            "#PAPERLESS_OCR_LANGUAGES=deu eng\n"
            "#PAPERLESS_TIME_ZONE=America/Chicago\n"
            "#PAPERLESS_SECRET_KEY=change-me\n"
            "#PAPERLESS_ADMIN_USER=admin\n"
        )

        assert DotenvParser.count_commented_out_assignments(str(f)) == 5

    def test_ordinary_comments_are_not_counted(self, tmp_path):
        f = tmp_path / ".env"
        f.write_text(
            "# This is a configuration file.\n"
            "# See the docs for more information.\n"
            "FOO=bar\n"
        )

        assert DotenvParser.count_commented_out_assignments(str(f)) == 0

    def test_prose_containing_equals_is_not_counted(self, tmp_path):
        """
        A '=' appearing inside ordinary prose (a URL query string, a
        parenthetical example) must not be mistaken for a commented-out
        assignment -- only a line whose remainder is exactly an identifier
        followed by '=' counts.
        """
        f = tmp_path / ".env"
        f.write_text(
            "# see https://example.com/docs?ref=readme for more info\n"
            "# note: x=y is not a real setting\n"
            "FOO=bar\n"
        )

        assert DotenvParser.count_commented_out_assignments(str(f)) == 0

    def test_export_prefix_is_recognized_when_commented_out(self, tmp_path):
        f = tmp_path / ".env"
        f.write_text("# export DATABASE_URL=postgres://localhost/db\n")

        assert DotenvParser.count_commented_out_assignments(str(f)) == 1

    def test_empty_file_returns_zero(self, tmp_path):
        f = tmp_path / ".env"
        f.write_text("")

        assert DotenvParser.count_commented_out_assignments(str(f)) == 0

    def test_missing_file_returns_zero_rather_than_raising(self, tmp_path):
        assert (
            DotenvParser.count_commented_out_assignments(str(tmp_path / "nope.env"))
            == 0
        )
