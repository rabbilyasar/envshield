from rich.console import Console
from rich.theme import Theme
from rich.table import Table
from rich.markdown import Markdown
from rich.text import Text
from rich.progress import Progress, SpinnerColumn, TextColumn # For progress bars

from envguard.core.constants import SYMBOL_SUCCESS, SYMBOL_WARNING, SYMBOL_ERROR, SYMBOL_INFO, SYMBOL_QUESTION, SYMBOL_STAR

console = Console()

def print_success(message: str):
    """Prints a success message in bold green."""
    console.print(Text(f"✔ {message}", style="bold green"))

def print_error(message: str):
    """Prints an error message in bold red."""
    console.print(Text(f"✖ {message}", style="bold red"))

def print_warning(message: str):
    """Prints a warning message in bold yellow."""
    console.print(Text(f"▲ {message}", style="bold yellow"))

def print_info(message: str):
    """Prints an informational message in blue."""
    console.print(Text(f"→ {message}", style="blue"))

def print_header(message: str):
    """Prints a prominent header message in bold cyan."""
    console.print(Text(f"\n==={message}===", style="bold cyan"))

def print_table(headers: list[str], rows: list[list[any]], title: str = None):
    """Prints a formatted table to the console using Rich's Table."""
    table = Table(title=title, show_header=True, header_style="bold magenta", expand=True)
    for header in headers:
        table.add_column(header)
    for row in rows:
        table.add_row(*[str(item) for item in row])
    console.print(table)