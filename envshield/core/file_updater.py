# envshield/core/file_updater.py
# Contains logic for safely updating variables within configuration files.
import errno
import os
import re
import stat
from typing import List, Optional, Tuple

from . import schema_types
from .exceptions import EnvShieldException, UnsafeWriteTargetError

_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_NONBLOCK = getattr(os, "O_NONBLOCK", 0)
# Every POSIX platform EnvShield supports; Windows has no dir_fd (and
# creating a symlink there needs a privilege an ordinary user lacks).
_HAS_DIR_FD = os.open in os.supports_dir_fd and os.mkdir in os.supports_dir_fd


def _split_under_root(path: str, root: Optional[str]) -> Tuple[str, List[str]]:
    """
    (root, components of `path` relative to it), lexically -- nothing is
    resolved, so a symlink can't redirect where the path is judged to be.
    Raises if the path is the root itself or leaves it.
    """
    root_abs = os.path.abspath(root or os.getcwd())
    rel = os.path.relpath(os.path.abspath(path), root_abs)
    if rel == os.curdir or rel == os.pardir or rel.startswith(os.pardir + os.sep):
        raise UnsafeWriteTargetError(path, "it is outside the project directory")
    return root_abs, rel.split(os.sep)


def _walk_parents(
    path: str, root: Optional[str], create: bool
) -> Tuple[Optional[int], str, List[str]]:
    """
    Walks `path`'s parent directories below `root` one component at a time,
    each opened relative to the previous one with O_NOFOLLOW|O_DIRECTORY,
    so a symlinked (or non-directory) component anywhere below the root is
    refused and nothing can be swapped between a check and the open.
    Returns (fd of the final parent -- None if it doesn't exist and
    `create` is False -- , the leaf name, the components walked).
    """
    root_abs, parts = _split_under_root(path, root)
    dir_fd = os.open(root_abs, os.O_RDONLY | _DIRECTORY)
    try:
        for i, part in enumerate(parts[:-1]):
            try:
                next_fd = os.open(
                    part, os.O_RDONLY | _DIRECTORY | _NOFOLLOW, dir_fd=dir_fd
                )
            except FileNotFoundError:
                if not create:
                    os.close(dir_fd)
                    return None, parts[-1], parts
                os.mkdir(part, 0o777, dir_fd=dir_fd)
                next_fd = os.open(
                    part, os.O_RDONLY | _DIRECTORY | _NOFOLLOW, dir_fd=dir_fd
                )
            except OSError as e:
                if e.errno in (errno.ELOOP, errno.ENOTDIR):
                    where = os.path.join(*parts[: i + 1])
                    raise UnsafeWriteTargetError(
                        path, f"'{where}' is a symlink or not a directory"
                    )
                raise
            os.close(dir_fd)
            dir_fd = next_fd
    except BaseException:
        os.close(dir_fd)
        raise
    return dir_fd, parts[-1], parts


def _check_leaf(path: str, st: os.stat_result) -> None:
    if stat.S_ISLNK(st.st_mode):
        raise UnsafeWriteTargetError(path, "it is a symlink")
    if stat.S_ISDIR(st.st_mode):
        raise UnsafeWriteTargetError(path, "it is a directory")
    if not stat.S_ISREG(st.st_mode):
        raise UnsafeWriteTargetError(path, "it is not a regular file")


def assert_safe_write_target(path: str, root: Optional[str] = None) -> None:
    """
    Raises UnsafeWriteTargetError if writing `path` would be refused by
    open_for_write -- without creating or modifying anything. For a caller
    that should refuse *before* collecting what it would write (e.g.
    'setup', before prompting for secrets). open_for_write still enforces
    the same rules at the moment it opens the file.
    """
    if not _HAS_DIR_FD:
        _assert_safe_write_target_lexically(path, root)
        return
    dir_fd, leaf, _parts = _walk_parents(path, root, create=False)
    if dir_fd is None:
        return
    try:
        st = os.stat(leaf, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    finally:
        os.close(dir_fd)
    _check_leaf(path, st)


def _assert_safe_write_target_lexically(path: str, root: Optional[str]) -> None:
    """Fallback without dir_fd (Windows): lstat each component. Not race-free."""
    root_abs, parts = _split_under_root(path, root)
    current = root_abs
    for part in parts[:-1]:
        current = os.path.join(current, part)
        if os.path.islink(current):
            raise UnsafeWriteTargetError(path, f"'{current}' is a symlink")
    target = os.path.join(current, parts[-1])
    if os.path.lexists(target):
        _check_leaf(path, os.lstat(target))


def open_for_write(
    path: str,
    root: Optional[str] = None,
    append: bool = False,
    mode: int = 0o666,
    secret: bool = False,
    create_parents: bool = False,
):
    """
    The one way EnvShield opens a file it writes (BL-154). `path` must be
    a regular file inside `root` (default: the project directory, the
    current working directory) reached without any symlink: a symlinked
    file, a symlinked parent directory at any depth below `root`, a
    directory, or a special file raises UnsafeWriteTargetError. `root`
    itself is trusted (the project directory, or a Git hooks directory)
    and may be reached through a symlink.

    Creates the file with `mode` (umask applies) if it doesn't exist;
    truncates it, or with `append` appends to it, if it does -- an existing
    file keeps its permissions, except that `secret` always leaves it 0600
    (set on the open descriptor, never by path, so it can't follow a
    swapped-in symlink). `create_parents` creates missing parent
    directories under the same no-symlink rule, in place of os.makedirs
    (which follows symlinks).
    """
    flags = os.O_WRONLY | os.O_CREAT | _NOFOLLOW | _NONBLOCK
    flags |= os.O_APPEND if append else os.O_TRUNC
    if secret:
        mode = 0o600
    if _HAS_DIR_FD:
        dir_fd, leaf, _parts = _walk_parents(path, root, create=create_parents)
        if dir_fd is None:
            raise FileNotFoundError(errno.ENOENT, "No such directory", path)
        try:
            fd = os.open(leaf, flags, mode, dir_fd=dir_fd)
        except OSError as e:
            if e.errno == errno.ELOOP:
                raise UnsafeWriteTargetError(path, "it is a symlink")
            if e.errno == errno.EISDIR:
                raise UnsafeWriteTargetError(path, "it is a directory")
            if e.errno == errno.ENXIO:
                raise UnsafeWriteTargetError(path, "it is not a regular file")
            raise
        finally:
            os.close(dir_fd)
    else:
        _assert_safe_write_target_lexically(path, root)
        if create_parents and os.path.dirname(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
        fd = os.open(path, flags, mode)
    try:
        _check_leaf(path, os.fstat(fd))
        if secret:
            os.fchmod(fd, 0o600)
        # O_NONBLOCK only mattered for refusing a FIFO at open time.
        if _NONBLOCK and hasattr(os, "set_blocking"):
            os.set_blocking(fd, True)
        return os.fdopen(fd, "a" if append else "w")
    except BaseException:
        os.close(fd)
        raise


def open_new_secret_file(path: str):
    """
    Opens a local secrets file for writing through open_for_write: refused
    if it isn't a plain file inside the project reached without a symlink
    (BL-154), created 0600 in the same open() call if it doesn't exist
    (umask can only clear bits, so it's never broader), and left 0600 if it
    does. Missing parent directories are created under the same rule.
    """
    return open_for_write(path, secret=True, create_parents=True)


def update_variables_in_file(file_path: str, updates: List[dict], secret: bool = False):
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

    The write goes through open_for_write (BL-154): an unsafe target raises
    UnsafeWriteTargetError rather than being silently skipped like an I/O
    error. `secret` leaves the file 0600 (a local secrets file).
    """
    assert_safe_write_target(file_path)
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
        with open_for_write(file_path, secret=secret) as f:
            f.writelines(new_lines)
    except IOError:
        pass
