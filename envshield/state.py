# Manages the state of the active profile for the current project.

import os
from typing import Optional
import json

# Define a hidden directory to store EnvShield's state and other internal files
STATE_DIR = ".envshield"
STATE_FILE = os.path.join(STATE_DIR, "state.json")


def _ensure_state_dir_exists():
    """Ensures the .envshield directory exists."""
    os.makedirs(STATE_DIR, exist_ok=True)


def get_active_profile() -> Optional[str | None]:
    """
    Retrieves the name of the currently active profile.

    Returns:
        The name of the active profile, or None if no profile is set.
    """
    if not os.path.exists(STATE_FILE):
        return None

    try:
        with open(STATE_FILE, "r") as f:
            state_data = json.load(f)
            return state_data.get("active_profile")
    except (json.JSONDecodeError, IOError):
        print(f"Error reading {STATE_FILE}. The file may be corrupted.")
        return None


def set_active_profile(profile_name: str):
    """
    Sets the active profile for the current project.

    Args:
        profile_name: The name of the profile to set as active.
    """
    _ensure_state_dir_exists()

    state_data = {"active_profile": profile_name}

    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state_data, f, indent=4)
    except IOError as e:
        print(f"Error writing to {STATE_FILE}: {e}")
