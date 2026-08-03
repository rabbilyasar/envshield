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
