# envshield/core/file_updater.py
# Contains logic for safely updating variables within configuration files.
import re
from typing import List


def update_variables_in_file(file_path: str, updates: List[dict]):
    """
    Updates one or more variables in a given file in-place.

    Args:
        file_path: The path to the file to be updated.
        updates: A list of dictionaries, where each dict has a 'key' and a 'value'
                 e.g., [{'key': 'SECRET_KEY', 'value': 'new_secret'}]
    """
    try:
        with open(file_path, "r") as f:
            lines = f.readlines()
    except IOError:
        return

    update_map = {u["key"]: u["value"] for u in updates}
    updated_keys_handled = set()

    new_lines = []
    for line in lines:
        match_found = False
        for key, value in update_map.items():
            # Skip keys we've already updated to avoid duplicate processing
            if key in updated_keys_handled:
                continue

            # This regex is more specific: it looks for the key at the start of the line,
            # ignoring whitespace, followed by an equals sign.
            pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
            if pattern.match(line):
                if file_path.endswith(".py"):
                    # For Python files, format as: KEY = "VALUE"
                    new_lines.append(f'{key} = "{value}"\n')
                else:
                    # For .env files, format as: KEY=VALUE
                    new_lines.append(f"{key}={value}\n")

                updated_keys_handled.add(key)
                match_found = True
                break

        if not match_found:
            new_lines.append(line)

    try:
        with open(file_path, "w") as f:
            f.writelines(new_lines)
    except IOError:
        pass
