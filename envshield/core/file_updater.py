# envshield/core/file_updater.py
# Contains logic for safely updating variables within configuration files.
import re
from typing import List


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
