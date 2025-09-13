# envshield/core/inspector.py
# Logic for inspecting a project to determine its type.
import json
import toml
import os
from typing import Optional, List


def _find_framework_in_list(dependencies: List[str]) -> Optional[str]:
    """Helper to find a known framework in a list of Python dependencies."""
    for dep in dependencies:
        dep_lower = dep.lower()
        if "django" in dep_lower:
            return "python-django"
        if "flask" in dep_lower:
            return "python-flask"
    return None


def _check_pyproject_toml() -> Optional[str]:
    """Parses pyproject.toml to find a framework."""
    if not os.path.exists("pyproject.toml"):
        return None

    try:
        with open("pyproject.toml", "r") as f:
            data = toml.load(f)

        dependencies = data.get("project", {}).get("dependencies", [])
        framework = _find_framework_in_list(dependencies)
        if framework:
            return framework

        optional_deps = data.get("project", {}).get("optional-dependencies", {})
        for group in optional_deps.values():
            framework = _find_framework_in_list(group)
            if framework:
                return framework

    except (IOError, toml.TomlDecodeError):
        pass

    return "python"


def _check_requirements_txt() -> Optional[str]:
    """Parses requirements.txt to find a framework."""
    if not os.path.exists("requirements.txt"):
        return None

    try:
        with open("requirements.txt", "r") as f:
            dependencies = f.readlines()

        framework = _find_framework_in_list(dependencies)
        if framework:
            return framework
    except IOError:
        pass

    return "python"


def detect_project_type() -> Optional[str]:
    """
    Detects the project type by looking for marker files and their content.
    """
    # Check for Node.js projects
    if os.path.exists("package.json"):
        try:
            with open("package.json", "r") as f:
                lines = [line for line in f if not line.strip().startswith("//")]
                content = "".join(lines)
                data = json.loads(content)
            dependencies = data.get("dependencies", {})
            devDependencies = data.get("devDependencies", {})

            if "next" in dependencies or "next" in devDependencies:
                return "nextjs"
            if "vite" in dependencies or "vite" in devDependencies:
                return "vite"
            return "nodejs"
        except (IOError, json.JSONDecodeError):
            return "nodejs"

    # Check for Python projects with specific frameworks
    framework = _check_pyproject_toml()
    if framework:
        return framework

    framework = _check_requirements_txt()
    if framework:
        return framework

    # Check for Go projects
    if os.path.exists("go.mod"):
        return "go"

    return None
