# envshield/tests/test_rich_markup_text.py
"""
BL-220 (#120): text from the schema or envshield.yml is printed as itself.

Every string a project supplies (a description, a default, an enum value, a
pattern, a variable or service name, a file path such as Next.js's
'app/[slug]/') is untrusted configuration text (§4 item 12). Printed as
Rich markup, '[/x]' crashed the command and '[red]R[/red]' silently
disappeared. The class tested here is every command over a project with
brackets in every field: none may crash, and the brackets stay visible.
"""

import os
import subprocess
import traceback

import pytest
from typer.testing import CliRunner

from envshield.cli import app

runner = CliRunner()

# '[/x]' makes Rich raise; '[red]R[/red]' would vanish without escaping.
M = "[/x] [red]R[/red]"
SERVICE = "web[/x]"
SERVICE_DIR = "app/[slug]"
MANIFEST = "deploy/[x]/docker-compose.yml"
CONTAINER = "api[/x]"

BASE_SCHEMA = """\
[A]
description = "a"
type = "string"

[ENUMV]
description = "enum"
type = "enum"
enum = ["two"]
"""

SCHEMA = f"""\
[A]
description = "see {M}"
type = "string"
defaultValue = "d{M}"
requiredIn = ["production"]

[ENUMV]
description = "enum {M}"
type = "enum"
enum = ["one{M}", "two"]

[PAT]
description = "pat {M}"
type = "string"
pattern = "^a[/x]$"

[FLAG]
description = "flag"
type = "string"

[COND]
description = "cond {M}"
type = "string"
requiredIf = {{ var = "FLAG", equals = "on{M}" }}
"""

CONFIG = f"""\
environments: [production]
services:
  "{SERVICE}":
    dir: "{SERVICE_DIR}"
    schema: env.schema.toml
manifests:
  - file: "{MANIFEST}"
    environment: production
    containers:
      "{CONTAINER}": "{SERVICE}"
secrets:
  run: "bws run [/x] --"
"""

COMPOSE = f"""\
services:
  "{CONTAINER}":
    image: x
    environment:
      ENUMV: "bad{M}"
      PAT: "${{PAT}}"
"""


def _write(path, text):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def _git(*args):
    subprocess.run(
        [
            "git",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "user.email=t@example.com",
            "-c",
            "user.name=t",
            *args,
        ],
        check=True,
        capture_output=True,
    )


@pytest.fixture
def bracket_project(tmp_path, monkeypatch):
    """A committed base, then a working tree with brackets everywhere, so
    the revision commands have changes to print."""
    monkeypatch.chdir(tmp_path)
    _git("init", "-q")
    _write("envshield.yml", CONFIG)
    _write("env.schema.toml", BASE_SCHEMA)
    _write(f"{SERVICE_DIR}/page.py", "import os\nos.environ['A']\n")
    _write(MANIFEST, COMPOSE)
    _git("add", "-A")
    _git("commit", "-qm", "base")
    _write("env.schema.toml", SCHEMA)
    _write(
        f"{SERVICE_DIR}/.env",
        f"ENUMV=bad{M}\nPAT=zz\nFLAG=on{M}\n",
    )
    _write(f"{SERVICE_DIR}/more.py", "import os\nos.environ['UNDECLARED']\n")
    return tmp_path


def _crash_site(exc):
    """Where in EnvShield the exception was raised, for the failure message."""
    frames = [
        f
        for f in traceback.extract_tb(exc.__traceback__)
        if "site-packages" not in f.filename
    ]
    return f"{frames[-1].filename}:{frames[-1].lineno}" if frames else "?"


def _invoke(args, **kwargs):
    result = runner.invoke(app, args, **kwargs)
    if result.exception and not isinstance(result.exception, SystemExit):
        raise AssertionError(
            f"envshield {' '.join(args)} raised {result.exception!r} at "
            f"{_crash_site(result.exception)}\n{result.output}"
        )
    return result


COMMANDS = [
    ["check"],
    ["check", "--env", "production"],
    ["check", "--since", "HEAD"],
    ["check", "--code-only"],
    ["check", "--json"],
    ["doctor"],
    ["doctor", "--json"],
    ["setup", "--non-interactive"],
    ["explain", "A"],
    ["explain", "ENUMV"],
    ["explain", "PAT"],
    ["explain", "FLAG"],
    ["explain", "COND"],
    ["explain", "UNDECLARED"],
    ["schema", "diff"],
    ["schema", "sync"],
    ["generate", "--lang", "python", "--force"],
    ["service", "list"],
    ["hook", "status"],
    ["secrets", "--help"],
    ["scan"],
]


@pytest.mark.parametrize("args", COMMANDS, ids=" ".join)
def test_no_command_crashes_on_brackets(bracket_project, args):
    _invoke(args)


def test_runtime_check_prints_brackets(bracket_project):
    result = _invoke(
        ["check", "--runtime", "--env", "production"],
        env={"ENUMV": f"bad{M}", "PAT": "zz"},
    )
    assert "[/x]" in result.output


def test_explain_prints_every_field_as_written(bracket_project):
    out = _invoke(["explain", "A"]).output
    assert f"see {M}" in out
    assert f"d{M}" in out
    out = _invoke(["explain", "ENUMV"]).output
    assert f"one{M}" in out
    out = _invoke(["explain", "PAT"]).output
    assert "^a[/x]$" in out
    out = _invoke(["explain", "COND"]).output
    assert f"on{M}" in out


def test_check_prints_paths_and_names_as_written(bracket_project):
    out = _invoke(["check"]).output
    assert "[slug]" in out
    assert "[red]R[/red]" in out


def test_setup_prints_descriptions_as_written(bracket_project):
    out = _invoke(["setup", "--non-interactive"]).output
    assert SERVICE in out
    assert "[red]R[/red]" in out


def test_schema_diff_prints_values_as_written(bracket_project):
    out = _invoke(["schema", "diff"]).output
    # The table wraps the line between the two markers.
    assert "'one[/x]" in out
    assert "[red]R[/red]" in out


def test_doctor_prints_names_as_written(bracket_project):
    out = _invoke(["doctor"]).output
    assert "[/x]" in out


def test_init_and_discover_print_paths_as_written(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _git("init", "-q")
    _write(f"{SERVICE_DIR}/page.py", "import os\nos.environ['A']\n")
    _write(f"{SERVICE_DIR}/.env.example", "A=1\n")
    _write("svc[/x]/main.py", "import os\nos.environ['B']\n")
    _write("svc[/x]/.env.example", "B=1\n")
    out = _invoke(["init", "--yes"]).output
    assert "[/x]" in out or "[slug]" in out
    _invoke(["service", "discover", "--yes"])
    _invoke(["service", "add", "x[/x]", "--yes"])
    _invoke(["service", "list"])
    _invoke(["service", "remove", "x[/x]", "--yes"])


# A schema error names what's wrong, and that can be the project's own text:
# the error is printed as written too.
ERROR_COMMANDS = [
    ["check"],
    ["doctor"],
    ["setup", "--non-interactive"],
    ["explain", "A"],
    ["schema", "diff"],
    ["schema", "sync"],
    ["generate", "--lang", "python", "--force"],
]


@pytest.mark.parametrize("args", ERROR_COMMANDS, ids=" ".join)
def test_a_schema_error_is_printed_as_written(tmp_path, monkeypatch, args):
    monkeypatch.chdir(tmp_path)
    _git("init", "-q")
    _write("envshield.yml", "services:\n  api:\n    schema: env.schema.toml\n")
    _write("env.schema.toml", '["B[/x]"]\ndescription = "b"\n')
    result = _invoke(args)
    assert result.exit_code != 0
    assert "B[/x]" in result.output


def test_scan_prints_a_bracketed_path_and_never_the_secret(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _git("init", "-q")
    # Built at runtime so this file holds no token-shaped literal.
    token = "sk_live_" + "".join(chr(97 + (i * 7) % 26) for i in range(24))
    _write(f"{SERVICE_DIR}/keys.py", f'stripe = "{token}"\n')
    for args in (["scan"], ["scan", "--staged"]):
        _git("add", "-A")
        out = _invoke(args).output
        assert f"{SERVICE_DIR}/keys.py" in out, args
        assert token not in out, args
