# Core logic for template checking and syncing.

from rich.console import Console
from rich.table import Table

from envguard.config import manager as config_manager
from envguard.core.exceptions import ProfileNotFoundError
from envguard.parsers.factory import get_parser

console = Console()

def check_template(profile_name: str):
    """
    Compares a profile's source file(s) against its template file.
    """
    config = config_manager.load_config()
    profiles = config.get("profiles", {})

    if profile_name not in profiles:
        raise ProfileNotFoundError(profile_name)

    profile_details = profiles[profile_name]
    template_file = profile_details.get("template")
    links = profile_details.get("links", [])

    if not template_file:
        console.print(f"[yellow]Profile '{profile_name}' has no template file defined. Nothing to check.[/yellow]")
        return
