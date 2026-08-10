# envshield/tests/core/test_file_updater.py
import ast

import pytest

from envshield.core import file_updater
from envshield.core.exceptions import EnvShieldException


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


def test_rejects_an_unsafe_key_in_a_python_file_and_leaves_it_untouched(tmp_path):
    """
    Regression coverage for P0-5: 'key' is about to become a bare Python
    assignment target -- unlike 'value', it can't be escaped into a safe
    form without changing its identity, so an unsafe one must be rejected
    outright, before the file is ever opened for writing.
    """
    target = tmp_path / "config.py"
    original = "FOO = 'old'\n"
    target.write_text(original)

    malicious_key = "FOO = 1\nimport os; os.system('true')  #"
    with pytest.raises(EnvShieldException):
        file_updater.update_variables_in_file(
            str(target), [{"key": malicious_key, "value": "x"}]
        )

    assert target.read_text() == original


def test_rejects_an_unsafe_key_in_a_dotenv_file_and_leaves_it_untouched(tmp_path):
    target = tmp_path / ".env"
    original = "FOO=old\n"
    target.write_text(original)

    malicious_key = "FOO\nENVSHIELD_P0_5_SENTINEL"
    with pytest.raises(EnvShieldException):
        file_updater.update_variables_in_file(
            str(target), [{"key": malicious_key, "value": "x"}]
        )

    assert target.read_text() == original
