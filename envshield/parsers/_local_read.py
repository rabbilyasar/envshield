# envshield/parsers/_local_read.py
"""
Reading a local file that may be a named pipe (BL-214).

A secret manager can mount a project's .env as a named pipe that is served
only while it's unlocked (1Password Environments). Opening an unserved
pipe blocks forever, so a pipe is opened without blocking and read with a
deadline. It is read once per process: every later read of the same pipe
gets the same text, or the same PipeNotServedError, without waiting again.
Nothing here ever opens a pipe for writing.
"""

import os
import select
import stat
import time
from typing import Dict, List, Tuple, Union

from ..core.exceptions import PipeNotServedError

# Seconds to wait for a pipe's writer to connect and finish.
PIPE_READ_TIMEOUT = 10.0

_pipe_reads: Dict[Tuple[int, int], Union[str, PipeNotServedError]] = {}


def read_local_text(path: str, errors: str = "replace") -> str:
    """`path`'s text (UTF-8). A regular file is read as usual; a named pipe
    with a deadline, or PipeNotServedError."""
    try:
        st = os.stat(path)
    except OSError:
        st = None
    if st is None or not stat.S_ISFIFO(st.st_mode):
        with open(path, "r", encoding="utf-8", errors=errors) as f:
            return f.read()
    key = (st.st_dev, st.st_ino)
    if key not in _pipe_reads:
        try:
            text = _read_pipe(path, PIPE_READ_TIMEOUT).decode("utf-8", errors=errors)
            # The newline translation open() applies to a regular file.
            _pipe_reads[key] = text.replace("\r\n", "\n").replace("\r", "\n")
        except PipeNotServedError as e:
            _pipe_reads[key] = e
    result = _pipe_reads[key]
    if isinstance(result, PipeNotServedError):
        raise result
    return result


def _read_pipe(path: str, timeout: float) -> bytes:
    """
    Everything a writer sends before it closes the pipe. No writer before
    the deadline, or one that hasn't closed by then (stopped mid-read):
    PipeNotServedError, and the partial text is discarded.
    """
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    try:
        poller = select.poll()
        poller.register(fd, select.POLLIN)
        deadline = time.monotonic() + timeout
        chunks = []
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise PipeNotServedError(path, timeout)
            if not poller.poll(remaining * 1000):
                continue  # nothing yet; the deadline check ends the wait
            try:
                chunk = os.read(fd, 65536)
            except BlockingIOError:
                continue
            if chunk:
                chunks.append(chunk)
                continue
            if chunks:
                return b"".join(chunks)  # the writer closed: done
            # End of file before any writer (some systems report it at
            # once): wait a little and look again.
            time.sleep(0.05)
    finally:
        os.close(fd)


def read_local_lines(path: str, errors: str = "replace") -> List[str]:
    """read_local_text's lines, split exactly as iterating an open text
    file splits them ('\\n', '\\r\\n' and '\\r'), without line endings."""
    return read_local_text(path, errors).split("\n")


def clear_pipe_reads() -> None:
    """Forget pipes read in this process (tests)."""
    _pipe_reads.clear()
