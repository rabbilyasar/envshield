# envshield/tests/test_source_line_leak.py
"""BL-182: parsing a user's Python file must never print its source lines.

Python emits a SyntaxWarning, quoting the offending line, when it compiles a
string with an invalid escape such as "\\d". If EnvShield lets that warning
through, a hardcoded secret on that line is printed in full, bypassing
scan's own redaction.

These run the real CLI in a subprocess: pytest captures warnings itself, so
an in-process CliRunner test would pass even with the leak present.
"""

import ast
import json
import os
import pathlib
import subprocess
import sys

import pytest

# A distinctive canary, not shaped like any provider key (push protection).
SECRET = "envshield-canary-7f3a9c1e-not-a-real-value"
# The invalid "\d" escape is what makes Python warn about this line.
LEAKY_LINE = 'TOKEN = "' + SECRET + '\\d"\n'


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def project(tmp_path):
    _git("init", "-q", cwd=tmp_path)
    _git("config", "user.email", "test@example.com", cwd=tmp_path)
    _git("config", "user.name", "Test", cwd=tmp_path)
    (tmp_path / "envshield.yml").write_text(
        "services:\n  app:\n    schema: env.schema.toml\n    local_file: .env\n"
    )
    (tmp_path / "env.schema.toml").write_text('[DB_URL]\ndescription = "x"\n')
    (tmp_path / ".gitignore").write_text(".env\n")
    (tmp_path / ".env").write_text("DB_URL=x\n")
    _git("add", "-A", cwd=tmp_path)
    _git("commit", "-q", "-m", "init", cwd=tmp_path)
    # Uncommitted, so undeclared (HEAD vs working tree) parses it too.
    (tmp_path / "app.py").write_text(
        "import os\n" + LEAKY_LINE + 'DB = os.environ["DB_URL"]\n'
        'NEW = os.environ["NEW_VAR"]\n'
    )
    return tmp_path


def _run(cwd, *args, warnings_env=None):
    env = {k: v for k, v in os.environ.items() if k != "PYTHONWARNINGS"}
    if warnings_env:
        env["PYTHONWARNINGS"] = warnings_env
    return subprocess.run(
        [sys.executable, "-m", "envshield", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )


@pytest.mark.parametrize(
    "args",
    [
        ["check"],
        ["check", "--json"],
        ["scan"],
        ["scan", "--json"],
        ["undeclared"],
        ["explain", "DB_URL"],
        ["doctor"],
    ],
    ids=" ".join,
)
def test_no_command_echoes_a_source_line_from_a_python_warning(project, args):
    result = _run(project, *args)
    output = result.stdout + result.stderr
    assert SECRET not in output
    assert "SyntaxWarning" not in output


def test_warnings_as_errors_does_not_hide_a_files_env_reads(project):
    # With warnings turned into errors, the invalid escape would raise a
    # SyntaxError, and discovery would treat app.py as reading nothing.
    result = _run(project, "check", "--json", warnings_env="error::SyntaxWarning")
    report = json.loads(result.stdout)["reports"][0]
    undeclared = json.dumps(report["code_references"])
    assert "NEW_VAR" in undeclared


def test_user_source_is_parsed_only_through_the_shared_helper():
    # A new ast.parse call on user files would reopen the leak.
    package = pathlib.Path(__file__).resolve().parents[1]
    callers = []
    for path in package.rglob("*.py"):
        if "tests" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("parse", "compile")
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "ast"
            ):
                callers.append(f"{path.relative_to(package)}:{node.lineno}")
    assert callers == ["core/discovery.py:" + callers[0].split(":")[1]], callers
