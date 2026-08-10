# envshield/core/file_updater.py
# Contains logic for safely updating variables within configuration files.
import os
import re
from typing import List

from . import schema_types
from .exceptions import EnvShieldException


def open_new_secret_file(path: str):
    """
    Opens `path` for writing, guaranteeing 0600 permissions if this call is
    what actually creates the file on disk -- never touching the
    permissions of a file that already exists there.

    Both properties come from POSIX open()'s own semantics for O_CREAT,
    not from any extra logic here: the `mode` argument only takes effect
    at true creation (an existing file's permissions are left exactly as
    they are, no matter what mode is passed), and the OS ANDs that mode
    with the complement of the process umask before applying it -- umask
    can only clear bits, never add them, so passing 0o600 guarantees the
    result is never broader than 0600 regardless of how permissive the
    umask is. A plain open(path, "w") followed by a separate os.chmod()
    would leave a brief window where the file exists with whatever the
    umask produced before the chmod call catches up; doing it in one
    open() call has no such window.

    Callers needing to distinguish "created a fresh file" from "reused an
    existing one" for other reasons (e.g. a different message to print)
    should still check os.path.exists() themselves beforehand -- this
    function only guarantees the permission outcome, not that signal.

    This function is responsible for secure permissions on a newly-created
    file, not for project-boundary enforcement: it follows normal OS
    symlink semantics with no containment check of its own, so a caller
    must validate any repository-controlled path (e.g. via
    config.manager._ensure_within_project) before passing it here.
    """
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        return os.fdopen(fd, "w")
    except Exception:
        os.close(fd)
        raise


def update_variables_in_file(file_path: str, updates: List[dict]):
    """
    Updates one or more variables in a given file in-place, preserving
    everything else in the file untouched.

    Args:
        file_path: The path to the file to be updated.
        updates: A list of dictionaries, where each dict has a 'key' and a 'value'
                 e.g., [{'key': 'SECRET_KEY', 'value': 'new_secret'}]

    Any key whose assignment already exists in the file has that single line
    replaced in place. Any key with no existing assignment is appended to the
    end of the file instead -- this is what lets callers like 'schema sync'
    and 'setup' add newly-declared schema variables to an existing,
    hand-maintained config file (e.g. a Python module) without touching its
    other content.
    """
    try:
        with open(file_path, "r") as f:
            lines = f.readlines()
    except IOError:
        return

    is_python = file_path.endswith(".py")

    def _render(key: str, value: str) -> str:
        # 'key' is about to become a bare assignment target (a Python
        # identifier, or the left-hand side of a dotenv 'KEY=value' line) --
        # unlike 'value', it can't be escaped into a safe form without
        # changing its identity (callers match on it verbatim elsewhere), so
        # an unsafe key is rejected outright rather than sanitized.
        if not schema_types.is_safe_variable_name(key):
            raise EnvShieldException(
                f"{key!r} is not a safe variable name (must match "
                f"^[A-Za-z_][A-Za-z0-9_]*$) -- refusing to write it into "
                f"'{file_path}'."
            )

        # For Python files, format as: KEY = "VALUE" (repr() handles escaping
        # quotes/backslashes that plain string values may contain).
        if is_python:
            return f"{key} = {value!r}\n"

        # For .env files: dotenv has no line-continuation syntax, so a literal
        # newline/carriage-return in the value would otherwise split it into
        # extra lines -- potentially injecting an unintended new KEY=VALUE
        # assignment into the file. There's no unescaping on read (see
        # parsers/_dotenv.py), so this is a one-way, safety-only transform,
        # not a round-trip-preserving escape. Quoting whitespace/'#'/'='
        # mirrors setup_manager._write_dotenv_local_file's existing heuristic
        # for the same file format.
        safe_value = value.replace("\n", "\\n").replace("\r", "\\r")
        already_quoted = (safe_value.startswith("'") and safe_value.endswith("'")) or (
            safe_value.startswith('"') and safe_value.endswith('"')
        )
        if re.search(r"[#\s=]", safe_value) and not already_quoted:
            return f'{key}="{safe_value}"\n'
        return f"{key}={safe_value}\n"

    remaining = {u["key"]: u["value"] for u in updates}

    new_lines = []
    for line in lines:
        matched_key = None
        for key in remaining:
            # This regex is more specific: it looks for the key at the start of the line,
            # ignoring whitespace, followed by an equals sign.
            if re.match(rf"^\s*{re.escape(key)}\s*=", line):
                matched_key = key
                break

        if matched_key:
            new_lines.append(_render(matched_key, remaining.pop(matched_key)))
        else:
            new_lines.append(line)

    if remaining:
        if new_lines and not new_lines[-1].endswith("\n"):
            new_lines[-1] += "\n"
        new_lines.append("\n")
        for key, value in remaining.items():
            new_lines.append(_render(key, value))

    try:
        with open(file_path, "w") as f:
            f.writelines(new_lines)
    except IOError:
        pass
