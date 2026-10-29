# envshield/tests/core/test_contract_writer.py
"""V2-2 (#19): the append-only project-contract writer.

The contract is maintained by hand: the original file must always be a
byte prefix of the result, nothing already declared is appended again, a
contract that doesn't parse is never touched, and grants follow the rule
(a secret always names its service; a non-secret only in a multi-service
project).
"""

import os

import pytest
import toml

from envshield.core import contract_writer
from envshield.core.exceptions import EnvShieldException, UnsafeWriteTargetError

HAND_WRITTEN = """\
# Our contract. Keep the comments!
[DATABASE_URL]
description = "Primary DB"   # trailing comment
secret = true
services = ["api"]

[LOG_LEVEL]
description = "Log level"
defaultValue = "info"
"""


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _append(variables, service="api", multi=False, path="env.schema.toml"):
    return contract_writer.append_variables(
        path, variables, service, multi, "envshield test"
    )


def _read(path="env.schema.toml"):
    with open(path, "rb") as f:
        return f.read()


@pytest.mark.parametrize("original", [HAND_WRITTEN, HAND_WRITTEN.rstrip("\n")])
def test_original_is_a_byte_prefix(project, original):
    (project / "env.schema.toml").write_text(original)
    before = _read()
    result = _append({"NEW_FLAG": {"description": "n", "secret": False}})
    after = _read()
    assert after.startswith(before)
    assert result.appended == ["NEW_FLAG"]
    parsed = toml.loads(after.decode())
    assert parsed["NEW_FLAG"]["description"] == "n"
    assert parsed["LOG_LEVEL"] == toml.loads(original)["LOG_LEVEL"]
    assert b"# Our contract. Keep the comments!" in after
    assert b"# Added by envshield test on " in after


def test_unparsable_contract_is_refused_and_unchanged(project):
    broken = HAND_WRITTEN + "[UNCLOSED\n"
    (project / "env.schema.toml").write_text(broken)
    with pytest.raises(EnvShieldException, match="doesn't parse"):
        _append({"NEW_FLAG": {"description": "n"}})
    assert (project / "env.schema.toml").read_text() == broken


def test_already_declared_is_not_appended_again(project):
    (project / "env.schema.toml").write_text(HAND_WRITTEN)
    before = _read()
    result = _append({"LOG_LEVEL": {"description": "other"}, "DATABASE_URL": {}})
    assert result.appended == []
    assert sorted(result.already_declared) == ["DATABASE_URL", "LOG_LEVEL"]
    assert _read() == before


def test_a_name_declared_in_an_extended_base_is_not_appended(project):
    (project / "base.schema.toml").write_text('[BASE_VAR]\ndescription = "b"\n')
    (project / "env.schema.toml").write_text('extends = "base.schema.toml"\n')
    result = _append({"BASE_VAR": {"description": "x"}, "OWN": {"description": "o"}})
    assert result.appended == ["OWN"]
    assert result.already_declared == ["BASE_VAR"]


@pytest.mark.parametrize("multi", [False, True])
def test_an_appended_secret_is_always_scoped(project, multi):
    result = _append({"API_KEY": {"description": "k", "secret": True}}, multi=multi)
    assert result.appended == ["API_KEY"]
    assert toml.load("env.schema.toml")["API_KEY"]["services"] == ["api"]


@pytest.mark.parametrize("multi,scoped", [(False, False), (True, True)])
def test_a_non_secret_is_scoped_only_when_multi_service(project, multi, scoped):
    _append({"PORT": {"description": "p", "secret": False}}, multi=multi)
    entry = toml.load("env.schema.toml")["PORT"]
    assert ("services" in entry) is scoped
    if scoped:
        assert entry["services"] == ["api"]


def test_a_name_several_services_read_is_one_table_for_all(project):
    result = contract_writer.append_for_services(
        "env.schema.toml",
        [
            ("api", {"PORT": {"description": "p", "defaultValue": "8000"}}),
            ("web", {"PORT": {"description": "p", "defaultValue": "3000"}}),
            ("api", {"KEY": {"description": "k", "secret": False}}),
            ("web", {"KEY": {"description": "k", "secret": True}}),
            ("web", {"HOST": {"description": "h", "defaultValue": "x"}}),
            ("api", {"HOST": {"description": "h", "defaultValue": "x"}}),
        ],
        True,
        "envshield test",
    )
    schema = toml.load("env.schema.toml")
    assert schema["PORT"]["services"] == ["api", "web"]
    assert "defaultValue" not in schema["PORT"]
    assert result.conflicting_defaults == ["PORT"]
    # A secret in any reader makes it secret, with no default.
    assert schema["KEY"]["secret"] is True
    assert schema["KEY"]["services"] == ["api", "web"]
    # The same default from both is kept.
    assert schema["HOST"]["defaultValue"] == "x"
    assert schema["HOST"]["services"] == ["web", "api"]


@pytest.mark.parametrize(
    "value",
    ['quote " inside', "back\\slash", "new\nline", "tab\there", "ünïcødé ✓", "${X}"],
)
def test_awkward_values_round_trip(project, value):
    (project / "env.schema.toml").write_text(HAND_WRITTEN)
    _append({"ODD": {"description": value, "defaultValue": value}})
    parsed = toml.load("env.schema.toml")
    assert parsed["ODD"]["description"] == value
    assert parsed["ODD"]["defaultValue"] == value


def test_the_command_label_cannot_break_the_file(project):
    contract_writer.append_variables(
        "env.schema.toml",
        {"A": {"description": "a"}},
        "api",
        False,
        "envshield service add x\n[INJECTED]\nsecret = false",
    )
    parsed = toml.load("env.schema.toml")
    assert set(parsed) == {"A"}


def test_creates_the_contract_when_it_does_not_exist(project):
    result = _append({"A": {"description": "a"}})
    assert result.appended == ["A"]
    assert toml.load("env.schema.toml")["A"]["description"] == "a"


def test_never_writes_through_a_symlink(project, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside") / "victim.toml"
    outside.write_text(HAND_WRITTEN)
    os.symlink(outside, project / "env.schema.toml")
    with pytest.raises(UnsafeWriteTargetError):
        _append({"A": {"description": "a"}})
    assert outside.read_text() == HAND_WRITTEN


def test_nothing_new_writes_nothing(project):
    (project / "env.schema.toml").write_text(HAND_WRITTEN)
    mtime = os.stat("env.schema.toml").st_mtime_ns
    result = _append({})
    assert result.appended == []
    assert os.stat("env.schema.toml").st_mtime_ns == mtime
