# envshield/tests/core/test_file_updater.py
import ast

from envshield.core import file_updater


def test_updates_existing_key_in_place_and_appends_missing_ones(tmp_path):
    target = tmp_path / ".env"
    target.write_text("FOO=old\nUNRELATED=keep\n")

    file_updater.update_variables_in_file(
        str(target), [{"key": "FOO", "value": "new"}, {"key": "BAR", "value": "added"}]
    )

    content = target.read_text()
    assert "FOO=new\n" in content
    assert "UNRELATED=keep\n" in content
    assert "BAR=added\n" in content


def test_quotes_dotenv_value_containing_whitespace(tmp_path):
    target = tmp_path / ".env"
    target.write_text("FOO=old\n")

    file_updater.update_variables_in_file(
        str(target), [{"key": "FOO", "value": "has space"}]
    )

    assert target.read_text() == 'FOO="has space"\n'


def test_escapes_embedded_newline_instead_of_injecting_a_line(tmp_path):
    """
    Regression: a literal newline in a value used to be written verbatim,
    splitting the single KEY=VALUE assignment into extra physical lines --
    potentially injecting an unintended new assignment into the file.
    """
    target = tmp_path / ".env"
    target.write_text("FOO=old\n")

    file_updater.update_variables_in_file(
        str(target), [{"key": "FOO", "value": "line1\nEVIL_KEY=injected"}]
    )

    content = target.read_text()
    # Exactly one physical line for FOO's assignment -- the newline is
    # escaped as literal backslash-n text, not a real line break.
    assert content.count("\n") == 1
    assert "EVIL_KEY" not in content.split("=", 1)[0]
    assert "line1\\nEVIL_KEY=injected" in content


def test_updates_python_file_key_with_repr_escaping(tmp_path):
    target = tmp_path / "config.py"
    target.write_text("FOO = 'old'\n")

    file_updater.update_variables_in_file(
        str(target), [{"key": "FOO", "value": "has 'quotes' and \"both\""}]
    )

    content = target.read_text()
    assert content.startswith("FOO = ")
    # repr() round-trips correctly through Python's own literal syntax.
    rhs = content.split("=", 1)[1].strip()
    assert ast.literal_eval(rhs) == "has 'quotes' and \"both\""
