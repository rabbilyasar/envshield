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