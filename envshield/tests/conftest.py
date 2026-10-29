# envshield/tests/conftest.py
"""
Compatibility shim: this pinned `typer` version vendors its own CLI engine
and no longer subclasses Click's `CliRunner`, so `isolated_filesystem()` --
used throughout this test suite -- doesn't exist on it. Restore it here
rather than editing every test file that relies on it.
"""

import contextlib
import os
import shutil
import tempfile
from typing import Iterator, Optional

from typer.testing import CliRunner


@contextlib.contextmanager
def _isolated_filesystem(
    self: CliRunner, temp_dir: Optional[str] = None
) -> Iterator[str]:
    cwd = os.getcwd()
    t = tempfile.mkdtemp(dir=temp_dir)
    os.chdir(t)
    try:
        yield t
    finally:
        os.chdir(cwd)
        if temp_dir is None:
            shutil.rmtree(t, ignore_errors=True)


CliRunner.isolated_filesystem = _isolated_filesystem


import pytest  # noqa: E402


@pytest.fixture
def scan_reports_undeclared(monkeypatch):
    """
    'scan' with its undeclared-read pass on, outside --staged too. The
    command enables it only with --staged since V0-2b (#42); the scanner
    itself still runs it by default, and the pre-commit hook depends on it.
    Tests of that detection logic (ownership, scope, confidence, fail-closed)
    drive it through the CLI with this fixture.
    """
    from envshield.core import scanner

    real_run, real_result = scanner.run_scan, scanner.scan_result

    def run_scan(*args, check_undeclared=True, **kwargs):
        return real_run(*args, check_undeclared=True, **kwargs)

    def scan_result(*args, check_undeclared=True, **kwargs):
        return real_result(*args, check_undeclared=True, **kwargs)

    monkeypatch.setattr(scanner, "run_scan", run_scan)
    monkeypatch.setattr(scanner, "scan_result", scan_result)


@pytest.fixture(autouse=True)
def setup_sees_a_terminal(monkeypatch):
    """
    'setup' prompts only on a terminal (#21), and a test runner's stdin
    never is one. Tests simulate a terminal by default; a test of the
    non-interactive path sets this back with
    `monkeypatch.setattr(setup_manager, "stdin_is_terminal", lambda: False)`
    or passes --non-interactive.
    """
    from envshield.core import setup_manager

    monkeypatch.setattr(setup_manager, "stdin_is_terminal", lambda: True)
