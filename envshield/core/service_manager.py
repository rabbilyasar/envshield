# envshield/core/service_manager.py
# Helpers for multi-service projects.

import os
import sys
from typing import List, Optional, Tuple, Union

import questionary
from rich.console import Console

from ..config import manager as config_manager
from ..utils import git_utils
from .exceptions import EnvShieldException

console = Console()

ALL_SERVICES_CHOICE = "All services"

# How a resolved target was actually selected -- not merely what directory
# inference *would* produce (single-service and interactive-pick can both
# coincidentally match that), but which branch of resolution actually ran.
# Only a caller that needs to explain *why* a target was picked (currently:
# 'setup's inference announcement) needs this; every other caller keeps
# using resolve_service/resolve_targets exactly as before.
PROVENANCE_EXPLICIT = "explicit"
PROVENANCE_SINGLE_SERVICE = "single_service"
PROVENANCE_INFERRED = "inferred"
PROVENANCE_INTERACTIVE = "interactive"
PROVENANCE_ALL_SERVICES = "all_services"


def _is_interactive() -> bool:
    """
    Whether there's a real terminal to prompt on. A separate, mockable seam
    rather than inlining 'sys.stdin.isatty()' at the call site -- test
    runners (Typer's CliRunner included) swap sys.stdin for their own
    stream for the duration of a run, which would silently defeat a patch
    aimed directly at 'sys.stdin.isatty'.
    """
    return sys.stdin.isatty()


def get_available_services() -> List[str]:
    """Returns a sorted list of service names configured in envshield.yml."""
    services = config_manager.get_services()
    return sorted(services.keys())


def _infer_from_invocation_dir(
    available: List[str], invocation_dir: Optional[str]
) -> Optional[str]:
    """
    If the command was run from inside (or at) exactly one registered
    service's own directory -- e.g. 'cd services/api && envshield doctor' --
    that's the service, no --service or prompt needed. Falls through to the
    normal resolution (silently returning None) whenever that's ambiguous:
    invoked from the project root, from outside every service directory, or
    -- unlikely, but possible with nested service dirs -- inside more than
    one at once.

    A candidate is also rejected if its directory sits outside
    `invocation_dir`'s own nearest Git repository boundary (a '.git'
    directory or file -- see git_utils.find_nearest_git_boundary): a
    registered service directory that happens to be, or to contain, an
    independent nested Git repository (e.g. a submodule) must not be
    silently treated as "this is where I am" once the invocation itself
    has crossed into that nested repo's own boundary -- the same reasoning
    config_manager.find_project_root applies to 'envshield.yml' itself.
    """
    if not invocation_dir:
        return None
    invocation_dir = os.path.abspath(invocation_dir)
    git_boundary = git_utils.find_nearest_git_boundary(invocation_dir)
    matches = []
    for name in available:
        try:
            service_dir = os.path.abspath(config_manager.get_service_dir(name))
        except EnvShieldException:
            continue
        if not (
            invocation_dir == service_dir
            or invocation_dir.startswith(service_dir + os.sep)
        ):
            continue
        if git_boundary is not None and not (
            service_dir == git_boundary or service_dir.startswith(git_boundary + os.sep)
        ):
            continue  # service_dir sits above invocation_dir's own repo boundary
        matches.append(name)
    return matches[0] if len(matches) == 1 else None


def _resolve_service_with_provenance(
    service_name: Optional[str] = None,
    allow_multiple: bool = False,
    invocation_dir: Optional[str] = None,
) -> Tuple[Union[str, List[str]], str]:
    """
    The actual resolution logic behind both `resolve_service` and
    `resolve_service_with_provenance` -- one implementation, so the two
    can never quietly diverge. Returns the resolved service(s) alongside
    exactly which branch produced them (see the PROVENANCE_* constants),
    since a caller that only checks "does the result match what inference
    would produce" can be fooled by coincidence (a single-service project's
    root directory, or an interactive pick that happens to match cwd).

    envshield.yml always has at least one registered service once a project
    has been initialized -- a single-service project is just the one-entry
    case of the same `services` map a five-service project has (see
    config_manager.generate_default_config_content) -- so there's no more
    "no services configured" state to silently fall back to; that's just an
    uninitialized project now, and it's raised as such.
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
        return service_name, PROVENANCE_EXPLICIT

    # Only one service: select it automatically
    if len(available) == 1:
        return available[0], PROVENANCE_SINGLE_SERVICE

    inferred = _infer_from_invocation_dir(available, invocation_dir)
    if inferred:
        return inferred, PROVENANCE_INFERRED

    # Multiple services, none specified, and no TTY to prompt on (CI, a
    # piped/redirected invocation, etc.) -- never block on a prompt that can
    # never be answered. A command that can run against every service
    # (allow_multiple) does so, matching what --json already does explicitly;
    # one that can't (it writes a single output, e.g. 'generate'/'import')
    # has to be told which service, so fail with a clear, actionable error
    # instead of hanging or raising a raw EOFError from questionary.
    if not _is_interactive():
        if allow_multiple:
            return available, PROVENANCE_ALL_SERVICES
        raise EnvShieldException(
            "Multiple services configured and no terminal to prompt on. "
            f"Pass --service explicitly. Available: {', '.join(available)}"
        )

    # Multiple services, none specified: prompt the user
    choices = available + ([ALL_SERVICES_CHOICE] if allow_multiple else [])
    selected = questionary.select(
        "Which service?",
        choices=choices,
    ).ask()

    if selected is None:
        hint = f"Pass --service explicitly next time. Available: {', '.join(available)}"
        if allow_multiple:
            hint += f", or choose '{ALL_SERVICES_CHOICE}' from the prompt to run against every one."
        raise EnvShieldException(f"No service selected (prompt cancelled). {hint}")

    if selected == ALL_SERVICES_CHOICE:
        return available, PROVENANCE_ALL_SERVICES

    return selected, PROVENANCE_INTERACTIVE


def resolve_service(
    service_name: Optional[str] = None,
    allow_multiple: bool = False,
    invocation_dir: Optional[str] = None,
) -> Union[str, List[str]]:
    """
    Resolves which service(s) a command should operate on.

    If `service_name` is provided, validates it exists and returns it.
    If None and there's only one service, returns that service automatically.
    If None, multiple services are configured, and `invocation_dir` sits
    inside exactly one of them, that one is picked -- no flag or prompt
    needed for the common case of standing inside a service's own directory.
    Otherwise, interactively prompts the user to pick one (or, if
    `allow_multiple`, all of them at once).

    Returns:
        - A single service name (str)
        - A list of every configured service name (if `allow_multiple=True`
          and the user picks "All services")
    """
    resolved, _provenance = _resolve_service_with_provenance(
        service_name, allow_multiple=allow_multiple, invocation_dir=invocation_dir
    )
    return resolved


def resolve_service_with_provenance(
    service_name: Optional[str] = None,
    allow_multiple: bool = False,
    invocation_dir: Optional[str] = None,
) -> Tuple[Union[str, List[str]], str]:
    """
    Same resolution as `resolve_service`, but also returns *how* the
    result was actually selected (one of the PROVENANCE_* constants) --
    for a caller that needs to explain a directory-inferred choice to the
    user without being fooled by a single-service or interactive-pick
    result that happens to coincide with what inference would produce.
    """
    return _resolve_service_with_provenance(
        service_name, allow_multiple=allow_multiple, invocation_dir=invocation_dir
    )


def resolve_targets(
    service_name: Optional[str] = None, invocation_dir: Optional[str] = None
) -> List[str]:
    """
    Resolves --service into the list of service_name targets a command should
    run against. An explicit service, one auto-selected because it's the
    only one configured, or one inferred from `invocation_dir` yields a
    single target; picking "All services" (see resolve_service) yields every
    configured service. Always returns a list, so callers can loop
    unconditionally instead of branching on the return type of
    `resolve_service`.
    """
    targets, _provenance = resolve_targets_with_provenance(
        service_name, invocation_dir=invocation_dir
    )
    return targets


def resolve_targets_with_provenance(
    service_name: Optional[str] = None, invocation_dir: Optional[str] = None
) -> Tuple[List[str], str]:
    """Same resolution as `resolve_targets`, but also returns how the target(s) were actually selected -- see `resolve_service_with_provenance`."""
    resolved, provenance = _resolve_service_with_provenance(
        service_name, allow_multiple=True, invocation_dir=invocation_dir
    )
    targets = resolved if isinstance(resolved, list) else [resolved]
    return targets, provenance
