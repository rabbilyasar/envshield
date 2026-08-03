# envshield/core/service_manager.py
# Helpers for multi-service projects.

from typing import List, Optional

import questionary
from rich.console import Console

from ..config import manager as config_manager

console = Console()


def get_available_services() -> List[str]:
    """Returns a sorted list of service names configured in envshield.yml."""
    services = config_manager.get_services()
    return sorted(services.keys())


def resolve_service(
    service_name: Optional[str] = None, allow_multiple: bool = False
) -> Optional[str] | List[str]:
    """
    Resolves which service(s) to operate on.

    If `service_name` is provided, validates it exists and returns it.
    If None and there's only one service, returns that service.
    If None and multiple services, prompts the user to pick one (or all if allowed).

    Returns:
        - A single service name (str)
        - A list of service names (if allow_multiple=True and user selects multiple)
        - None if no services are configured (single-service project)
    """
    available = get_available_services()

    # Single-service project: no selection needed
    if not available:
        return None

    # User explicitly specified a service
    if service_name:
        if service_name not in available:
            raise ValueError(f"Service '{service_name}' not found. Available: {', '.join(available)}")
        return service_name

    # Only one service: select it automatically
    if len(available) == 1:
        return available[0]

    # Multiple services: prompt the user
    choices = available + (["All services"] if allow_multiple else [])
    selected = questionary.select(
        "Which service?",
        choices=choices,
    ).ask()

    if selected == "All services":
        return available

    return selected


def prompt_for_services(default_all: bool = False) -> Optional[str] | List[str]:
    """Prompts user to select one or more services. Returns None for single-service projects."""
    return resolve_service(allow_multiple=True)
