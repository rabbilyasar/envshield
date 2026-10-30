# envshield/tests/test_unserved_pipe.py
"""
BL-214 (#104): a local .env that is a named pipe (a secret manager's
mounted file, such as 1Password Environments) is opened without blocking
and read with a deadline. Unserved, or stopped mid-read, its variables are
COULD NOT VERIFY `value_not_inspected` and the command finishes. Served,
it's read once and validated. Nothing ever writes to it.
"""

import json
import os
import subprocess
import sys
import threading
import time

import pytest
from typer.testing import CliRunner

from envshield.cli import app
from envshield.core.exceptions import PipeNotServedError
from envshield.parsers import _local_read
from envshield.parsers._base import UNSERVED_PIPE
from envshield.parsers._dotenv import DotenvParser

pytestmark = pytest.mark.skipif(
    not hasattr(os, "mkfifo"), reason="named pipes need a POSIX system"
)

runner = CliRunner()
TIMEOUT = 0.5
CANARY = "sk_live_PIPE_CANARY_8842"


@pytest.fixture(autouse=True)
def short_timeout(monkeypatch):
    monkeypatch.setattr(_local_read, "PIPE_READ_TIMEOUT", TIMEOUT)
    _local_read.clear_pipe_reads()
    yield
    _local_read.clear_pipe_reads()


def _serve(path, parts, pause=0.0, hold=0.0):
    """A writer thread: opens the pipe (waiting for a reader), writes each
    part with `pause` between them, waits `hold`, then closes it."""

    def run():
        fd = os.open(path, os.O_WRONLY)
        try:
            for i, part in enumerate(parts):
                if i:
                    time.sleep(pause)
                os.write(fd, part.encode())
            time.sleep(hold)
        finally:
            os.close(fd)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def _unblock(path):
    """Release a writer thread still waiting for a reader."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        os.close(fd)
    except OSError:
        pass


class TestReadLocalText:
    def test_regular_file_unchanged(self, tmp_path):
        path = tmp_path / ".env"
        path.write_bytes(b"A=1\r\nB=2\rC=3\nD=\x0cx\n")
        assert _local_read.read_local_lines(str(path)) == [
            "A=1",
            "B=2",
            "C=3",
            "D=\x0cx",
            "",
        ]

    def test_unserved_times_out_then_fails_fast(self, tmp_path):
        path = tmp_path / ".env"
        os.mkfifo(path)
        start = time.monotonic()
        with pytest.raises(PipeNotServedError) as info:
            _local_read.read_local_text(str(path))
        assert TIMEOUT <= time.monotonic() - start < TIMEOUT + 2
        assert "wasn't served" in str(info.value)
        start = time.monotonic()
        with pytest.raises(PipeNotServedError):
            _local_read.read_local_text(str(path))
        assert time.monotonic() - start < 0.2  # remembered, no second wait

    def test_served_is_read_once(self, tmp_path):
        path = tmp_path / ".env"
        os.mkfifo(path)
        thread = _serve(str(path), ["A=1\r\n", "B=2\n"], pause=0.1)
        assert _local_read.read_local_text(str(path)) == "A=1\nB=2\n"
        thread.join(2)
        # No writer now: the second read is the first one's text.
        assert _local_read.read_local_text(str(path)) == "A=1\nB=2\n"

    def test_stopped_mid_read_discards_the_partial_text(self, tmp_path):
        path = tmp_path / ".env"
        os.mkfifo(path)
        thread = _serve(str(path), [f"KEY={CANARY}\n"], hold=TIMEOUT + 1)
        with pytest.raises(PipeNotServedError) as info:
            _local_read.read_local_text(str(path))
        assert CANARY not in str(info.value)
        thread.join(3)

    def test_never_opened_for_writing(self, tmp_path, monkeypatch):
        path = tmp_path / ".env"
        os.mkfifo(path)
        flags = []
        real_open = os.open

        def spy(p, f, *a, **k):
            flags.append(f)
            return real_open(p, f, *a, **k)

        monkeypatch.setattr(_local_read.os, "open", spy)
        with pytest.raises(PipeNotServedError):
            _local_read.read_local_text(str(path))
        assert flags and all(f & (os.O_WRONLY | os.O_RDWR) == 0 for f in flags)

    def test_get_states_marks_the_file_unserved(self, tmp_path):
        path = tmp_path / ".env"
        os.mkfifo(path)
        states = DotenvParser().get_states(str(path))
        assert dict(states) == {}
        assert [(u.ref, u.kind) for u in states.unbound] == [(str(path), UNSERVED_PIPE)]

    def test_python_config_module(self, tmp_path):
        from envshield.parsers._python import PythonParser

        path = tmp_path / "settings.py"
        os.mkfifo(path)
        states = PythonParser().get_states(str(path))
        assert states.unbound[0].kind == UNSERVED_PIPE


# --- Through the CLI --------------------------------------------------------

SCHEMA = """
[DATABASE_URL]
type = "url"
secret = true
[PORT]
type = "port"
defaultValue = 3000
[DEBUG]
required = false
[FEATURE]
required = false
[FEATURE_KEY]
requiredIf = { var = "FEATURE", equals = "on" }
"""

CONFIG = """project_name: t
schema: env.schema.toml
services:
  app:
    dir: "."
"""


def _project(extra=None):
    subprocess.run(["git", "init", "-q"], check=True)
    files = {"env.schema.toml": SCHEMA, "envshield.yml": CONFIG, **(extra or {})}
    for path, content in files.items():
        with open(path, "w") as f:
            f.write(content)
    os.mkfifo(".env")


def _variables(payload):
    return {v["name"]: v for v in payload["reports"][0]["variables"]}


def test_check_unserved(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project()
        start = time.monotonic()
        as_json = runner.invoke(app, ["check", "--json"])
        as_text = runner.invoke(app, ["check"], env={"COLUMNS": "200"})
        accepted = runner.invoke(
            app, ["check", "--accept-unverified", "value_not_inspected"]
        )
        elapsed = time.monotonic() - start
    # The pipe is waited on once for the three runs in this process.
    assert elapsed < TIMEOUT + 3
    assert as_json.exit_code == 1, as_json.stdout
    variables = _variables(json.loads(as_json.stdout))
    for name in ("DATABASE_URL", "PORT"):
        assert variables[name]["verdict"] == "unverified"
        assert variables[name]["reason_code"] == "value_not_inspected"
    assert variables["DEBUG"]["verdict"] == "not_required"
    # Its trigger isn't seen either: the unread file is the reason, as for
    # a Kubernetes envFrom EnvShield can't see.
    assert variables["FEATURE_KEY"]["reason_code"] == "value_not_inspected"
    assert "secret manager unlocked" in as_text.stdout
    assert as_text.exit_code == 1
    assert accepted.exit_code == 0, accepted.stdout


def test_doctor_unserved(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project()
        start = time.monotonic()
        result = runner.invoke(app, ["doctor"], env={"COLUMNS": "200"})
        elapsed = time.monotonic() - start
    assert elapsed < TIMEOUT + 3
    assert result.exit_code == 1
    assert "mounted file that wasn't served" in result.stdout
    assert "ConfigMap" not in result.stdout


def test_setup_refuses_and_never_waits(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project()
        start = time.monotonic()
        result = runner.invoke(app, ["setup", "--non-interactive"])
        elapsed = time.monotonic() - start
    assert result.exit_code != 0
    assert elapsed < TIMEOUT + 3
    assert "not a regular file" in result.stdout


def test_check_served_is_validated(tmp_path):
    content = f"DATABASE_URL=postgres://app:{CANARY}@db/app\nPORT=8080\n"
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project()
        thread = _serve(".env", [content])
        as_json = runner.invoke(app, ["check", "--json"])
        as_text = runner.invoke(app, ["check"])
        thread.join(2)
        _unblock(".env")
    assert CANARY not in as_json.stdout + as_text.stdout
    assert as_json.exit_code == 0, as_json.stdout
    variables = _variables(json.loads(as_json.stdout))
    assert variables["DATABASE_URL"]["verdict"] == "valid"
    assert variables["PORT"]["verdict"] == "valid"


def test_check_served_invalid(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project()
        thread = _serve(".env", [f"DATABASE_URL={CANARY}\nPORT=1\n"])
        result = runner.invoke(app, ["check", "--json"])
        thread.join(2)
        _unblock(".env")
    assert CANARY not in result.stdout
    variables = _variables(json.loads(result.stdout))
    assert variables["DATABASE_URL"]["verdict"] == "invalid"


def test_union_completeness_unserved(tmp_path):
    config = (
        "services:\n  app:\n    schema: env.schema.toml\n"
        "    completeness: union\n"
        "manifests:\n  - file: docker-compose.yml\n"
        "    containers:\n      app: app\n"
    )
    compose = "services:\n  app:\n    image: x\n    environment:\n      PORT: '80'\n"
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _project({"docker-compose.yml": compose})
        with open("envshield.yml", "w") as f:
            f.write(config)
        start = time.monotonic()
        result = runner.invoke(app, ["check", "--json"])
        elapsed = time.monotonic() - start
    assert elapsed < TIMEOUT + 3
    assert result.exit_code == 1, result.stdout
    variables = _variables(json.loads(result.stdout))
    assert variables["PORT"]["verdict"] == "valid"  # the manifest supplies it
    assert variables["DATABASE_URL"]["verdict"] == "unverified"


def test_check_subprocess_finishes(tmp_path):
    """The real hang, end to end: a new process, an unserved pipe."""
    os.chdir(tmp_path)
    _project()
    code = (
        "from envshield.parsers import _local_read;"
        "_local_read.PIPE_READ_TIMEOUT = 0.5;"
        "from envshield.cli import app; app(['check', '--json'])"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 1
    assert json.loads(result.stdout)["reports"][0]["summary"]["verdict"] == (
        "unverified"
    )
