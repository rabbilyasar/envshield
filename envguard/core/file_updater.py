# envguard/core/file_updater.py
# Contains logic for safely updating variables within configuration files.
import re
from typing import List, Dict

def update_variables_in_file(file_path: str, updates: List[dict]):
    """
    Updates one or more variables in a given file in-place.

    Args:
        file_path: The path to the file to be updated.
        updates: A list of dictionaries, where each dict has a 'key' and a 'value'
                 e.g., [{'key': 'SECRET_KEY', 'value': 'new_secret'}]
    """
    try:
        with open(file_path, 'r') as f:
            lines = f.readlines()
    except IOError as e:
        return

    new_lines = []
    updated_keys = {u['key'] for u in updates}

    # Create a dictionary for quick lookup of new values
    updated_map = {u['key']: u['value'] for u in updates}

    for line in lines:
        # Use regex to find a top-level assignment for any of our keys
        # This is safer than a simple string search.
        # It looks for: start of line, optional whitespace, key, optional whitespace, =, anything
        match_found = False
        for key in updated_keys:
            # We escape the key to handle special regex characters if any
            pattern = re.compile(rf"^\s*{re.escape(key)}\s*=\s*.*")
            if pattern.match(line):
                # Determine the correct format for the new value
                if file_path.endswith('.py'):
                    # For Python files, wrap strings in quotes
                    new_value = f'"{updated_map[key]}"\n'
                else:
                    # For .env or similar files, just use the raw value
                    new_value = {updated_map[key]}

                new_lines.append(f"{key}={new_value}\n")
                match_found = True
                break # Move to the next line once a key is matched and replaced
        if not match_found:
            new_lines.append(line)

    # Write the modified content back to the file
    try:
        with open(file_path, 'w') as f:
            f.writelines(new_lines)
    except IOError as e:
        print(f"Error writing to file {file_path}: {e}")
        return