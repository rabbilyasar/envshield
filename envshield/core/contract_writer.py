# envshield/core/contract_writer.py
"""
The append-only writer for the project contract (V2-2, #19).

The project contract (envshield.yml's top-level `schema:`, #18) is
maintained by hand, so EnvShield never rewrites it. Commands that learn
about new variables (`init`, `service add`, `service discover`,
`init --from`) append them here instead:

- new `[VAR]` tables go at the end, after one comment naming the command
  and the date;
- the existing bytes are never changed: the original file is a byte prefix
  of the result;
- a variable already declared (in the contract or a schema it `extends`)
  is never appended again;
- a contract that doesn't parse is refused, and the result must parse to
  the previous tables plus the new ones, or nothing is written.

Grants: an appended secret always gets `services = ["<service>"]`; a
non-secret is granted to the service only when the project has more than
one service (in a single-service project it is the whole project's).
"""

import datetime
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import toml

from envshield.config import manager as config_manager
from envshield.core import file_updater
from envshield.core.exceptions import EnvShieldException

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


# Marks an entry built from a code read rather than a config file (#44):
# merging it adds a reader but never changes a default. Never written.
FROM_CODE = "__from_code__"


@dataclass
class AppendResult:
    path: str
    appended: List[str] = field(default_factory=list)
    already_declared: List[str] = field(default_factory=list)
    # Variables several services read with different defaults: declared
    # once for all of them, without a default.
    conflicting_defaults: List[str] = field(default_factory=list)


def _read_contract(path: str, root: str) -> Optional[str]:
    """The contract's text, or None if it doesn't exist. Never through a symlink."""
    file_updater.assert_safe_write_target(path, root)
    if not os.path.exists(path):
        return None
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, encoding="utf-8") as f:
        try:
            return f.read()
        except UnicodeDecodeError as e:
            raise EnvShieldException(
                f"'{path}' isn't UTF-8 text; left unchanged."
            ) from e


def _parse(text: str, path: str) -> Dict[str, Any]:
    try:
        return toml.loads(text)
    except (toml.TomlDecodeError, TypeError, IndexError) as e:
        raise EnvShieldException(
            f"'{path}' doesn't parse as TOML ({e}), so EnvShield won't add to "
            "it. Fix it and run the command again; the file was left unchanged."
        ) from e


CONTRACT_HEADER = (
    "# The project contract: every environment variable the services\n"
    "# read. 'services = [...]' grants a variable to some services only;\n"
    "# a secret always names its services.\n"
)


def declared_names(path: str) -> set:
    """Every variable the contract at `path` declares (empty if none yet)."""
    text = _read_contract(path, os.getcwd())
    if text is None:
        return set()
    return _declared_names(path, _parse(text, path))


def _declared_names(path: str, parsed: Dict[str, Any]) -> set:
    """Every variable the contract declares, its `extends` bases included."""
    names = {k for k in parsed if k != "extends"}
    try:
        for _chain_path, raw in config_manager._iter_schema_chain(path):
            names.update(k for k in raw if k != "extends")
    except EnvShieldException:
        pass  # an unreadable base: judge by the contract's own tables
    return names


def _render_value(value: Any) -> Optional[str]:
    """A TOML literal for the simple values a field holds, or None."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        # A JSON string is a valid TOML basic string.
        return json.dumps(value)
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return "[" + ", ".join(json.dumps(v) for v in value) + "]"
    return None


def _render_table(name: str, details: Dict[str, Any]) -> str:
    lines = [f"[{name}]"]
    for key, value in details.items():
        literal = _render_value(value)
        if literal is None or not re.fullmatch(r"[A-Za-z0-9_-]+", key):
            # Anything unusual goes through the TOML library instead.
            return toml.dumps({name: details})
        lines.append(f"{key} = {literal}")
    return "\n".join(lines) + "\n"


def _render(entries: Dict[str, Dict[str, Any]], command: str) -> str:
    stamp = datetime.date.today().isoformat()
    label = _CONTROL.sub(" ", command)
    tables = "\n".join(_render_table(name, d) for name, d in entries.items())
    return f"# Added by {label} on {stamp}\n" + tables


def append_variables(
    path: str,
    variables: Dict[str, Dict[str, Any]],
    service: str,
    multi_service: bool,
    command: str,
    root: Optional[str] = None,
) -> AppendResult:
    """
    Appends `variables` ({name: field details}) to the contract at `path`
    for `service`, granted as described in the module docstring. Creates
    the file if it doesn't exist. Returns what was appended and what was
    skipped because it's already declared.
    """
    return append_for_services(
        path, [(service, variables)], multi_service, command, root
    )


def append_for_services(
    path: str,
    per_service: List[Any],
    multi_service: bool,
    command: str,
    root: Optional[str] = None,
) -> AppendResult:
    """
    Appends the variables several services read, found in one run ('init'
    or 'service discover' registering several directories), as one block.
    A name more than one of them reads becomes one table granted to all of
    them -- these tables are all new, so nothing existing is edited. A
    secret in any of them makes it secret; defaults that differ are left
    out (reported in `conflicting_defaults`).
    """
    root = root or os.getcwd()
    original = _read_contract(path, root)
    old = _parse(original, path) if original is not None else {}
    declared = _declared_names(path, old) if original is not None else set()

    result = AppendResult(path)
    merged: Dict[str, Dict[str, Any]] = {}
    readers: Dict[str, List[str]] = {}
    for service, variables in per_service:
        for name, details in variables.items():
            if name == "extends" or not isinstance(details, dict):
                continue
            if name in declared:
                if name not in result.already_declared:
                    result.already_declared.append(name)
                continue
            from_code = bool(details.get(FROM_CODE))
            if name not in merged:
                merged[name] = {
                    k: v for k, v in details.items() if k not in ("services", FROM_CODE)
                }
                readers[name] = [service]
                continue
            if service not in readers[name]:
                readers[name].append(service)
            entry = merged[name]
            if from_code:
                # A code read says nothing about values: it only adds a
                # reader, and can make the variable secret.
                if details.get("secret"):
                    entry["secret"] = True
                    entry.pop("defaultValue", None)
                continue
            if details.get("secret"):
                entry["secret"] = True
            if entry.get("secret"):
                entry.pop("defaultValue", None)
            elif entry.get("defaultValue") != details.get("defaultValue"):
                if "defaultValue" in entry and name not in result.conflicting_defaults:
                    result.conflicting_defaults.append(name)
                entry.pop("defaultValue", None)
    new: Dict[str, Dict[str, Any]] = {}
    for name, entry in merged.items():
        if entry.get("secret"):
            entry.pop("defaultValue", None)
        if entry.get("secret") or multi_service:
            entry["services"] = readers[name]
        new[name] = entry
    if not new:
        return result

    prefix = original or ""
    if prefix and not prefix.endswith("\n"):
        prefix += "\n"
    separator = "\n" if prefix and not prefix.endswith("\n\n") else ""
    suffix = (prefix[len(original or "") :]) + separator + _render(new, command)
    updated = (original or "") + suffix

    # The result must read back as the old tables plus exactly the new ones.
    parsed = _parse(updated, path)
    if (
        {k: parsed.get(k) for k in old} != old
        or {k: parsed.get(k) for k in new} != new
        or set(parsed) != set(old) | set(new)
    ):
        raise EnvShieldException(
            f"Adding to '{path}' would change what it declares, so nothing was "
            "written. Check the end of the file for an unclosed table or array."
        )

    with file_updater.open_for_write(
        path, root, append=original is not None, create_parents=True
    ) as f:
        f.write(suffix if original is not None else updated)
    result.appended = list(new)
    return result


def project_contract() -> Optional[str]:
    """The project contract's path, or None for a legacy project."""
    return config_manager.get_project_schema_path()


LEGACY_TIP = (
    "Tip: this project keeps a schema per service. New projects keep one "
    "project contract instead: a top-level 'schema:' in envshield.yml that "
    "every service uses."
)
