# envshield/core/service_manager.py
# Helpers for multi-service projects.

from typing import List, Optional, Union

import questionary
from rich.console import Console

from ..config import manager as config_manager
from .exceptions import EnvShieldException

console = Console()

ALL_SERVICES_CHOICE = "All services"


def get_available_services() -> List[str]:
    """Returns a sorted list of service names configured in envshield.yml."""
    services = config_manager.get_services()
    return sorted(services.keys())


def resolve_service(
    service_name: Optional[str] = None, allow_multiple: bool = False
) -> Union[str, List[str]]:
    """
    Resolves which service(s) a command should operate on.

    If `service_name` is provided, validates it exists and returns it.
    If None and there's only one service, returns that service automatically.
    If None and multiple services are configured, interactively prompts the
    user to pick one (or, if `allow_multiple`, all of them at once).

    envshield.yml always has at least one registered service once a project
    has been initialized -- a single-service project is just the one-entry
    case of the same `services` map a five-service project has (see
    config_manager.generate_default_config_content) -- so there's no more
    "no services configured" state to silently fall back to; that's just an
    uninitialized project now, and it's raised as such.

    Returns:
        - A single service name (str)
        - A list of every configured service name (if `allow_multiple=True`
          and the user picks "All services")
    """
    available = get_available_services()

    if not available:
        raise EnvShieldException(
            "No services configured yet. Run 'envshield init' first."
        )

    # User explicitly specified a service
    if service_name:
        if service_name not in available:
            raise EnvShieldException(
                f"Service '{service_name}' not found. Available: {', '.join(available)}"
            )
        return service_name

    # Only one service: select it automatically
    if len(available) == 1:
        return available[0]

    # Multiple services, none specified: prompt the user
    choices = available + ([ALL_SERVICES_CHOICE] if allow_multiple else [])
    selected = questionary.select(
        "Which service?",
        choices=choices,
    ).ask()

    if selected is None:
        raise EnvShieldException("No service selected.")

    if selected == ALL_SERVICES_CHOICE:
        return available

    return selected


def resolve_targets(service_name: Optional[str] = None) -> List[str]:
    """
    Resolves --service into the list of service_name targets a command should
    run against. An explicit service, or one auto-selected because it's the
    only one configured, yields a single target; picking "All services" (see
    resolve_service) yields every configured service. Always returns a list,
    so callers can loop unconditionally instead of branching on the return
    type of `resolve_service`.
    """
    resolved = resolve_service(service_name, allow_multiple=True)
    return resolved if isinstance(resolved, list) else [resolved]
