# envshield/core/inspector.py
# Logic for inspecting a project to determine its type.

import json
import os
from typing import Optional


def detect_project_type() -> Optional[str]:
    """
    Detects the project type by looking for marker files.

    Returns:
        A string identifier for the project type (e.g., 'nextjs', 'python-flask') or None.
    """
    # Check for Node.js projects
    if os.path.exists("package.json"):
        try:
            with open("package.json", "r") as f:
                data = json.load(f)
            dependencies = data.get("dependencies", {})
            if "next" in dependencies:
                return "nextjs"
            if "vite" in dependencies:
                return "vite"
            return "nodejs"
        except (IOError, json.JSONDecodeError):
            return "nodejs"  # Fallback

    # Check for Python projects
    if os.path.exists("pyproject.toml") or os.path.exists("requirements.txt"):
        # A more sophisticated check could parse these files for flask/django
        return "python"

    # Check for Go projects
    if os.path.exists("go.mod"):
        return "go"

    return None
