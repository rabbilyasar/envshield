# envshield/cli.py
import os
from typing import List, Optional

import questionary
import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from envshield.core import importer

from .config import manager as config_manager
from .core import (
    schema_manager,
    scanner,
    doctor,
    inspector,
    setup_manager,
    service_manager,
    service_discovery,
    generator,
)
from .core.exceptions import EnvShieldException

# --- Main App Setup ---
app = typer.Typer(
    name="envshield",
    help="🛡️ EnvShield: Your Environment's First Line of Defense.",
    rich_markup_mode="markdown",
    add_completion=False,
)
console = Console()
schema_app = typer.Typer(
    name="schema", help="Check and sync your environment schema.", no_args_is_help=True
)
app.add_typer(schema_app, name="schema")
service_app = typer.Typer(
    name="service",
    help="Discover, add, and list services for a multi-service project.",
    no_args_is_help=True,
)
app.add_typer(service_app, name="service")


def _seed_schema_from_file(config_file: str, schema_path: str) -> None:
    """Writes a schema at `schema_path` generated from `config_file`'s real values, unless one already exists there."""
    if os.path.exists(schema_path):
        return
    content = importer.generate_schema_from_file(config_file)
    with open(schema_path, "w") as f:
        f.write(content)


def _print_service_header(targets: List[Optional[str]], target: Optional[str]) -> None:
    """Labels each service's output when a command is running against more than one."""
    if len(targets) > 1:
        console.print(f"\n[bold underline]── {target} ──[/bold underline]")


# --- Commands ---
@app.command()
def init(
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Overwrite existing EnvShield configuration files.",
    ),
):
    """Initializes EnvShield with intelligent, framework-aware defaults."""
    console.print(
        Panel(
            "[bold cyan]Welcome to EnvShield! Setting up your secure foundation...[/bold cyan]",
            title="🛡️ EnvShield",
            border_style="green",
        )
    )

    if os.path.exists(config_manager.CONFIG_FILE_NAME) and not force:
        console.print(
            "[yellow]An EnvShield setup already exists. Use '--force' to overwrite.[/yellow]"
        )
        raise typer.Exit()

    if force and os.path.exists(config_manager.CONFIG_FILE_NAME):
        import questionary

        overwrite = questionary.confirm(
            "Are you sure you want to overwrite your existing EnvShield configuration? This cannot be undone.",
            default=False,
        ).ask()
        if not overwrite:
            console.print("[yellow]Initialization cancelled.[/yellow]")
            raise typer.Exit()

    try:
        project_type = inspector.detect_project_type()
        if project_type:
            console.print(
                f"Detected a [bold yellow]{project_type}[/bold yellow] project."
            )
        else:
            console.print(
                "Could not detect a specific framework, using general defaults."
            )

        project_name = os.path.basename(os.getcwd())
        schema_content = config_manager.generate_default_schema_content(project_type)
        config_manager.write_file(
            config_manager.SCHEMA_FILE_NAME,
            schema_content,
            f"Created/updated schema: [bold cyan]{config_manager.SCHEMA_FILE_NAME}[/bold cyan]",
        )

        config_content = config_manager.generate_default_config_content(project_name)
        config_manager.write_file(
            config_manager.CONFIG_FILE_NAME,
            config_content,
            f"Created/updated config: [bold cyan]{config_manager.CONFIG_FILE_NAME}[/bold cyan]",
        )

        config_manager.update_gitignore()
        schema_manager.sync_schema()

        try:
            scanner.install_pre_commit_hook(non_interactive=True)
            scanner.install_post_merge_hook(non_interactive=True)
        except EnvShieldException as e:
            console.print(
                f"\n[bold yellow]⚠️  Warning:[/] Could not install Git hooks: {e}"
            )
            console.print(
                "    You can install them later by running 'envshield install-hook' after initializing your Git repository."
            )

    except EnvShieldException as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)
    except (KeyboardInterrupt, TypeError):
        console.print("\n[yellow]Initialization cancelled by user.[/yellow]")
        raise typer.Exit()

    console.print("\n[bold green]✨ Setup Complete! ✨[/bold green]")
    console.print(
        "Your project is now protected. Define your variables in 'env.schema.toml'."
    )


@app.command()
def check(
    file: Optional[str] = typer.Argument(
        None,
        help="The local environment file to validate. Defaults to the project's (or service's) local file.",
    ),
    service: Optional[str] = typer.Option(
        None,
        "--service",
        "-s",
        help="If set, validate against this service's schema (for multi-service projects).",
    ),
):
    """Validates a local environment file against the schema."""
    try:
        targets = service_manager.resolve_targets(service)
    except EnvShieldException as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)

    # An explicit file only makes sense for a single target -- it can't
    # apply to every service's own local file at once.
    if len(targets) > 1 and file:
        console.print(
            "[yellow]Ignoring the explicit file argument -- each service's own local file is checked when validating multiple services.[/yellow]"
        )
        file = None

    had_error = False
    for target in targets:
        _print_service_header(targets, target)
        try:
            resolved_file = file or config_manager.get_env_paths(service_name=target)["local_file"]
            if not schema_manager.check_schema(resolved_file, service_name=target):
                had_error = True
        except EnvShieldException as e:
            console.print(f"[bold red]Error:[/bold red] {e}")
            had_error = True

    if had_error:
        raise typer.Exit(code=1)


@app.command(name="doctor")
def doctor_command(
    fix: bool = typer.Option(
        False,
        "--fix",
        help="Interactively attempt to fix any issues that are found.",
    ),
    service: Optional[str] = typer.Option(
        None,
        "--service",
        "-s",
        help="If set, check health of this service (for multi-service projects).",
    ),
):
    """Runs a full health check on your project's EnvShield setup."""
    try:
        targets = service_manager.resolve_targets(service)
    except EnvShieldException as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)

    had_error = False
    for target in targets:
        _print_service_header(targets, target)
        try:
            doctor.run_health_check(fix=fix, service_name=target)
        except typer.Exit as e:
            if e.exit_code:
                had_error = True
        except EnvShieldException as e:
            console.print(f"[bold red]Error:[/bold red] {e}")
            had_error = True

    if had_error:
        raise typer.Exit(code=1)


@app.command()
def setup(
    output_file: Optional[str] = typer.Argument(
        None,
        help="The name of the local environment file to create. Defaults to the project's (or service's) local file.",
    ),
    service: Optional[str] = typer.Option(
        None,
        "--service",
        "-s",
        help="If set, setup this service's config (for multi-service projects).",
    ),
):
    """Interactively creates (or completes) a local environment file from the schema."""
    try:
        targets = service_manager.resolve_targets(service)
    except EnvShieldException as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)

    # An explicit output file only makes sense for a single target -- it
    # can't apply to every service's own local file at once.
    if len(targets) > 1 and output_file:
        console.print(
            "[yellow]Ignoring the explicit output file argument -- each service uses its own local file when setting up multiple services.[/yellow]"
        )
        output_file = None

    try:
        for target in targets:
            _print_service_header(targets, target)
            setup_manager.run_setup(output_file, service_name=target)
    except EnvShieldException as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)
    except (KeyboardInterrupt, TypeError):
        console.print("\n[yellow]Setup cancelled by user.[/yellow]")
        raise typer.Exit()


@schema_app.command("sync")
def schema_sync(
    service: Optional[str] = typer.Option(
        None,
        "--service",
        "-s",
        help="If set, sync this service's schema (for multi-service projects).",
    ),
):
    """Generates/updates the environment template from your schema."""
    try:
        targets = service_manager.resolve_targets(service)
    except EnvShieldException as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)

    had_error = False
    for target in targets:
        _print_service_header(targets, target)
        try:
            schema_manager.sync_schema(service_name=target)
        except EnvShieldException as e:
            console.print(f"[bold red]Error:[/bold red] {e}")
            had_error = True

    if had_error:
        raise typer.Exit(code=1)


_GENERATE_LANG_ALIASES = {
    "py": "python",
    "python": "python",
    "ts": "typescript",
    "js": "typescript",
    "typescript": "typescript",
    "javascript": "typescript",
}
# Frameworks/ecosystems detected by `inspector` that should default to a TypeScript
# config module instead of Python's. Anything else (python-*, go, unknown) defaults
# to Python, which remains the tool's original/primary target language.
_FRAMEWORK_DEFAULT_LANG = {
    "nextjs": "typescript",
    "vite": "typescript",
    "nodejs": "typescript",
}

_GENERATE_HELP_TEXT = {
    "python": (
        "[dim]Requires 'pydantic' and 'pydantic-settings' in your project. "
        "Import with: from {module} import settings[/dim]"
    ),
    "typescript": (
        "[dim]Requires 'zod' in your project. "
        "Import with: import {{ env }} from './{module}'[/dim]"
    ),
}
_GENERATE_DEFAULT_OUTPUT = {"python": "config.py", "typescript": "config.ts"}


def _resolve_generate_lang(explicit_lang: Optional[str]) -> str:
    if explicit_lang:
        resolved = _GENERATE_LANG_ALIASES.get(explicit_lang.lower())
        if not resolved:
            raise EnvShieldException(
                f"Unsupported --lang '{explicit_lang}'. Use 'python' or 'typescript'."
            )
        return resolved

    project_type = inspector.detect_project_type()
    resolved = _FRAMEWORK_DEFAULT_LANG.get(project_type, "python") if project_type else "python"
    console.print(f"[dim]No --lang given; detected '{resolved}' for this project.[/dim]")
    return resolved


@app.command()
def generate(
    output_file: Optional[str] = typer.Argument(
        None,
        help="Path to write the generated config module to. Defaults to 'config.py' or 'config.ts' based on --lang.",
    ),
    lang: Optional[str] = typer.Option(
        None,
        "--lang",
        "-l",
        help="Target language: 'python' or 'typescript'. Auto-detected from your project if omitted.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Overwrite the output file if it already exists.",
    ),
    service: Optional[str] = typer.Option(
        None,
        "--service",
        "-s",
        help="If set, generate config for this service (for multi-service projects).",
    ),
):
    """Generates a typed, validated config module (pydantic-settings or zod) from your schema."""
    try:
        resolved_lang = _resolve_generate_lang(lang)
        resolved_output = output_file or _GENERATE_DEFAULT_OUTPUT[resolved_lang]

        if os.path.exists(resolved_output) and not force:
            console.print(
                f"[bold yellow]Warning:[/] Output file '{resolved_output}' already exists. Use --force to overwrite."
            )
            raise typer.Exit()

        schema = config_manager.load_schema(service_name=service)
        content = generator.generate_config(schema, lang=resolved_lang)

        with open(resolved_output, "w") as f:
            f.write(content)

        console.print(
            f"\n[bold green]✓[/bold green] Generated typed config at [bold cyan]{resolved_output}[/bold cyan]"
        )
        module_name = os.path.splitext(os.path.basename(resolved_output))[0]
        console.print(_GENERATE_HELP_TEXT[resolved_lang].format(module=module_name))

    except EnvShieldException as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)


@app.command()
def scan(
    paths: List[str] = typer.Argument(
        None,
        help="Paths to files or directories to scan. Defaults to current directory.",
    ),
    staged: bool = typer.Option(
        False, "--staged", help="Only scan files staged for the next Git commit."
    ),
    config: Optional[str] = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to a custom envshield.yml configuration file.",
    ),
    exclude: Optional[List[str]] = typer.Option(
        None,
        "--exclude",
        "-e",
        help="Glob patterns to exclude. Can be used multiple times.",
    ),
    service: Optional[str] = typer.Option(
        None,
        "--service",
        "-s",
        help="If set, scan against this service's schema (for multi-service projects).",
    ),
):
    """Scans files for hardcoded secrets and undeclared variables."""
    try:
        scanner.run_scan(
            paths=paths,
            staged_only=staged,
            config_path=config,
            exclude_patterns=exclude,
            service_name=service,
        )
    except EnvShieldException as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)


@app.command("install-hook")
def install_hook():
    """Installs Git hooks: pre-commit (scan for secrets) and post-merge (check env config after pull)."""
    try:
        scanner.install_pre_commit_hook()
        scanner.install_post_merge_hook()
    except EnvShieldException as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)


@app.command(name="import")
def import_command(
    file: str = typer.Argument(
        ..., help="The .env file to import and convert to a schema."
    ),
    output: str = typer.Option(
        config_manager.SCHEMA_FILE_NAME,
        "--output",
        "-o",
        help="Path to write the new schema file to.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Overwrite the schema file if it already exists.",
    ),
    interactive: bool = typer.Option(
        False,
        "--interactive",
        help="Interactively guide you through classifying each variable.",
    ),
    service: Optional[str] = typer.Option(
        None,
        "--service",
        "-s",
        help="If set, import to this service's schema path (for multi-service projects).",
    ),
):
    """Generates an env.schema.toml from an existing .env file."""
    try:
        # If service is specified, use that service's schema path
        if service:
            resolved_output = config_manager.get_service_schema_path(service)
            if not resolved_output:
                console.print(f"[bold red]Error:[/bold red] Service '{service}' not found.")
                raise typer.Exit(code=1)
            output = resolved_output

        if os.path.exists(output) and not force and not interactive:
            console.print(
                f"[bold yellow]Warning:[/] Output file '{output}' already exists. Use --force to overwrite."
            )
            raise typer.Exit()

        if os.path.exists(output) and interactive:
            overwrite = questionary.confirm(
                f"Output file '{output}' already exists. Overwrite?"
            ).ask()
            if not overwrite:
                console.print("[yellow]Import cancelled.[/yellow]")
                raise typer.Exit()

        schema_content = importer.generate_schema_from_file(file, interactive)

        with open(output, "w") as f:
            f.write(schema_content)

        console.print(
            f"\nSuccessfully generated schema at [bold cyan]{output}[/bold cyan]"
        )
    except EnvShieldException as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)
    except (KeyboardInterrupt, TypeError):
        console.print("\n[yellow]Import cancelled by user.[/yellow]")
        raise typer.Exit()


@service_app.command("list")
def service_list():
    """Lists the services currently configured in envshield.yml."""
    services = config_manager.get_services()
    if not services:
        console.print(
            "[yellow]No services configured -- this is a single-service/root project.[/yellow]"
        )
        return

    table = Table(title="Configured Services")
    table.add_column("Name", style="cyan")
    table.add_column("Schema", style="white")
    table.add_column("Local File", style="white")
    for name in sorted(services.keys()):
        paths = config_manager.get_env_paths(service_name=name)
        schema_path = config_manager.get_service_schema_path(name)
        table.add_row(name, schema_path or "-", paths["local_file"])
    console.print(table)


@service_app.command("add")
def service_add(
    name: str = typer.Argument(
        ..., help="Name for the service (used everywhere else via --service)."
    ),
    directory: str = typer.Argument(..., help="The service's own directory."),
    local_file: Optional[str] = typer.Option(
        None,
        "--local-file",
        help="Override the local env file path -- required when it isn't a dotenv file, e.g. a Python config module.",
    ),
    example_file: Optional[str] = typer.Option(
        None, "--example-file", help="Override the tracked template file path."
    ),
    description: Optional[str] = typer.Option(
        None, "--description", "-d", help="Optional description for this service."
    ),
    schema: Optional[str] = typer.Option(
        None,
        "--schema",
        help="Path to the schema file. Defaults to '<directory>/env.schema.toml'.",
    ),
    import_from: Optional[str] = typer.Option(
        None,
        "--import",
        help="If given, seed the new service's schema from this existing config file (same as running 'envshield import').",
    ),
):
    """Registers one service in envshield.yml by hand, creating the file if needed."""
    try:
        schema_path = schema or os.path.join(directory, config_manager.SCHEMA_FILE_NAME)
        config_manager.add_service(
            name,
            schema_path,
            local_file=local_file,
            example_file=example_file,
            description=description,
        )
        console.print(
            f"[bold green]✓[/bold green] Registered service [bold cyan]{name}[/bold cyan] → {schema_path}"
        )

        if import_from:
            if os.path.exists(schema_path):
                console.print(
                    f"[yellow]'{schema_path}' already exists -- not overwriting. "
                    f"Run 'envshield import {import_from} --service {name} --force' to regenerate it.[/yellow]"
                )
            else:
                _seed_schema_from_file(import_from, schema_path)
                console.print(
                    f"[bold green]✓[/bold green] Seeded schema from [bold cyan]{import_from}[/bold cyan]"
                )
    except EnvShieldException as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)


@service_app.command("discover")
def service_discover(
    root: str = typer.Argument(
        ".", help="Directory to scan for service-like subdirectories."
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Register every discovered service without an interactive confirmation (for scripting/CI).",
    ),
):
    """
    Scans for service-like directories not already configured -- a
    directory with a dotenv file, or a recognizable Python config module --
    and offers to register them in envshield.yml, seeding each one's schema
    from its real, current config where one was found.

    Bootstraps a fresh envshield.yml if none exists yet, or extends an
    existing one with whatever's new -- already-configured services are
    never touched.
    """
    try:
        known_dirs = [
            config_manager.get_service_dir(name)
            for name in config_manager.get_services().keys()
        ]
        candidates = service_discovery.discover_candidates(root, known_dirs=known_dirs)
    except EnvShieldException as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)

    if not candidates:
        console.print("[green]No new service-like directories found.[/green]")
        return

    table = Table(title="Discovered Services")
    table.add_column("Name", style="cyan")
    table.add_column("Directory", style="white")
    table.add_column("Format", style="magenta")
    table.add_column("Config File", style="white")
    for c in candidates:
        config_file_display = c["local_file"] or c["example_file"] or "(default .env)"
        table.add_row(c["name"], c["dir"], c["format"], config_file_display)
    console.print(table)

    if yes:
        selected_names = [c["name"] for c in candidates]
    else:
        choices = [
            questionary.Choice(
                f"{c['name']}  ({c['dir']}, {c['format']})", value=c["name"]
            )
            for c in candidates
        ]
        choices.append(questionary.Separator())
        choices.append(questionary.Choice("All", value="__all__"))

        selection = questionary.select(
            "Add these services to envshield.yml? (seeds each schema from its real config where found)",
            choices=choices,
        ).ask()
        if selection is None:
            console.print("[yellow]Cancelled.[/yellow]")
            raise typer.Exit()

        if selection == "__all__":
            selected_names = [c["name"] for c in candidates]
        else:
            selected_names = [selection]

    selected = [c for c in candidates if c["name"] in selected_names]
    if not selected:
        console.print("[yellow]Nothing selected.[/yellow]")
        return

    for c in selected:
        schema_path = os.path.join(c["dir"], config_manager.SCHEMA_FILE_NAME)
        config_manager.add_service(
            c["name"], schema_path, local_file=c["local_file"], example_file=c["example_file"]
        )
        console.print(
            f"[bold green]✓[/bold green] Registered [bold cyan]{c['name']}[/bold cyan] → {schema_path}"
        )

        # Seed from whichever real signal was actually found: the local
        # file if one exists, else a template (blank values, but still
        # documents every var name) if that's all there was.
        config_file = c["local_file"] or c["example_file"] or os.path.join(c["dir"], ".env")
        if os.path.exists(config_file) and not os.path.exists(schema_path):
            _seed_schema_from_file(config_file, schema_path)
            console.print(f"    seeded schema from [dim]{config_file}[/dim]")

    console.print(f"\n[bold green]✨ Added {len(selected)} service(s) to envshield.yml.[/bold green]")

    try:
        scanner.install_pre_commit_hook(non_interactive=True)
        scanner.install_post_merge_hook(non_interactive=True)
    except EnvShieldException as e:
        console.print(
            f"\n[bold yellow]⚠️  Warning:[/] Could not install Git hooks: {e}"
        )
        console.print(
            "    You can install them later by running 'envshield install-hook' after initializing your Git repository."
        )
