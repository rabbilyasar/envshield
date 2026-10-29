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
    file keeps its permissions. `secret` creates a new file with mode 0600
    and, where os.fchmod exists (not on Windows before Python 3.13), also
    sets an existing one to 0600 (on the open descriptor, never by path, so
    it can't follow a swapped-in symlink). Truncation happens only after
    the checks and that permission change succeed, so a failure there
    leaves an existing file's content as it was. `create_parents` creates
    missing parent directories under the same no-symlink rule, in place of
    os.makedirs (which follows symlinks).
    """
    flags = os.O_WRONLY | os.O_CREAT | _NOFOLLOW | _NONBLOCK
    if append:
        flags |= os.O_APPEND
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
        # os.fchmod is missing on Windows before Python 3.13, where POSIX
        # mode bits don't exist anyway (chmod there only toggles read-only).
        if secret and hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)
        if not append:
            os.ftruncate(fd, 0)
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
    (umask can only clear bits, so it's never broader), and set to 0600 if
    it does, where os.fchmod exists. Missing parent directories are created
    under the same rule.
    """
    return open_for_write(path, secret=True, create_parents=True)


def atomic_write(
    path: str, content: str, root: Optional[str] = None, secret: bool = False
) -> None:
    """
    Replaces `path` with `content` in one step (V3-1, #20): the content goes
    to a new temporary file in the same directory, which is then renamed
    over `path`, so an interruption leaves the original byte-identical.
    The same rules as open_for_write apply (BL-154): `path` must be a plain
    file inside `root` reached without a symlink; missing parents are
    created under that rule. `secret` makes the result 0600 (the temporary
    file is created 0600, so a secret is never briefly world-readable);
    otherwise an existing file's mode is kept.
    """
    if not _HAS_DIR_FD:
        _assert_safe_write_target_lexically(path, root)
        directory = os.path.dirname(os.path.abspath(path))
        os.makedirs(directory, exist_ok=True)
        mode = (
            0o600
            if secret
            else (
                stat.S_IMODE(os.stat(path).st_mode) if os.path.exists(path) else 0o644
            )
        )
        tmp = os.path.join(
            directory, f".{os.path.basename(path)}.envshield-tmp-{os.getpid()}"
        )
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp, mode)
            os.replace(tmp, path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
        return

    dir_fd, leaf, _parts = _walk_parents(path, root, create=True)
    if dir_fd is None:  # pragma: no cover - create=True always opens it
        raise FileNotFoundError(errno.ENOENT, "No such directory", path)
    tmp = None
    try:
        try:
            st = os.stat(leaf, dir_fd=dir_fd, follow_symlinks=False)
        except FileNotFoundError:
            st = None
        if st is not None:
            _check_leaf(path, st)
        mode = 0o600 if secret else (stat.S_IMODE(st.st_mode) if st else None)
        for attempt in range(100):
            candidate = f".{leaf}.envshield-tmp-{os.getpid()}-{attempt}"
            try:
                fd = os.open(
                    candidate,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW,
                    0o600 if (secret or mode is not None) else 0o666,
                    dir_fd=dir_fd,
                )
            except FileExistsError:
                continue
            tmp = candidate
            break
        if tmp is None:
            raise EnvShieldException(f"Could not create a temporary file for '{path}'.")
        with os.fdopen(fd, "w") as f:
            if mode is not None and hasattr(os, "fchmod"):
                os.fchmod(f.fileno(), mode)
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.rename(tmp, leaf, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        tmp = None
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp, dir_fd=dir_fd)
            except OSError:
                pass
        os.close(dir_fd)


def _assignment_re(key: str, is_python: bool) -> "re.Pattern[str]":
    """A line that assigns `key`: `KEY=`, `export KEY=` (dotenv), `KEY =`."""
    if is_python:
        return re.compile(rf"^\s*{re.escape(key)}\s*=")
    return re.compile(rf"^\s*(?:export\s+)?{re.escape(key)}\s*=")


def render_assignment(key: str, value: str, is_python: bool, file_path: str) -> str:
    """One assignment line for `key`, escaped for the file's format."""
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
    # Python: KEY = 'VALUE' (repr() escapes quotes and backslashes).
    if is_python:
        return f"{key} = {value!r}\n"
    # dotenv has no line-continuation syntax, so a literal newline/carriage
    # return would split the value into extra lines -- potentially
    # injecting a new KEY=VALUE assignment. There's no unescaping on read
    # (parsers/_dotenv.py), so this is a one-way, safety-only transform.
    safe_value = value.replace("\n", "\\n").replace("\r", "\\r")
    already_quoted = (safe_value.startswith("'") and safe_value.endswith("'")) or (
        safe_value.startswith('"') and safe_value.endswith('"')
    )
    if re.search(r"[#\s=]", safe_value) and not already_quoted:
        return f'{key}="{safe_value}"\n'
    return f"{key}={safe_value}\n"


def patch_variable_lines(
    lines: List[str],
    updates: List[dict],
    file_path: str,
    removals: Optional[List[str]] = None,
) -> List[str]:
    """
    `lines` with each update's key replaced in place (its first assignment
    line), appended at the end if it has none, and each key in `removals`
    deleted. Every other line is kept as it was.
    """
    is_python = file_path.endswith(".py")
    remaining = {u["key"]: u["value"] for u in updates}
    patterns = {k: _assignment_re(k, is_python) for k in remaining}
    remove = {k: _assignment_re(k, is_python) for k in (removals or [])}
    new_lines: List[str] = []
    for line in lines:
        if any(p.match(line) for p in remove.values()):
            continue
        matched = next((k for k, p in patterns.items() if p.match(line)), None)
        if matched is not None and matched in remaining:
            rendered = render_assignment(
                matched, remaining.pop(matched), is_python, file_path
            )
            # Keep the line's own 'export ' (and indentation).
            prefix = re.match(r"^\s*(?:export\s+)?", line).group(0)
            new_lines.append(prefix + rendered if not is_python else rendered)
        else:
            new_lines.append(line)
    if remaining:
        if new_lines and not new_lines[-1].endswith("\n"):
            new_lines[-1] += "\n"
        if new_lines and new_lines[-1].strip():
            new_lines.append("\n")
        for key, value in remaining.items():
            new_lines.append(render_assignment(key, value, is_python, file_path))
    return new_lines


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
    UnsafeWriteTargetError. A file that can't be read or written raises
    EnvShieldException, so a caller never reports an update that didn't
    happen. `secret` leaves the file 0600 (a local secrets file).
    """
    assert_safe_write_target(file_path)
    try:
        with open(file_path, "r") as f:
            lines = f.readlines()
    except OSError as e:
        raise EnvShieldException(f"Could not read '{file_path}': {e}")
    new_lines = patch_variable_lines(lines, updates, file_path)
    try:
        atomic_write(file_path, "".join(new_lines), secret=secret)
    except OSError as e:
        raise EnvShieldException(f"Could not write to '{file_path}': {e}")
