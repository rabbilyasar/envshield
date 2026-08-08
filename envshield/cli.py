# envshield/cli.py
import json
import os
from typing import List, Optional, cast

import questionary
import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from envshield import __version__
from envshield.core import importer

from .config import manager as config_manager
from .core import (
    doctor,
    generator,
    hooks_manager,
    inspector,
    scanner,
    schema_manager,
    service_discovery,
    service_manager,
    setup_manager,
)
from .core.exceptions import EnvShieldException


# --- Main App Setup ---
def _version_callback(version: bool) -> None:
    """Print version and exit."""
    if version:
        console = Console()
        console.print(f"envshield {__version__}")
        raise typer.Exit()


app = typer.Typer(
    name="envshield",
    help="🛡️ EnvShield: Your Environment's First Line of Defense.",
    rich_markup_mode="markdown",
    add_completion=False,
)
console = Console()


# The directory envshield was actually invoked from, captured before any
# chdir below -- lets a command run from inside a service's own directory
# (e.g. 'services/api') still be matched to that service automatically. See
# service_manager.resolve_service's `invocation_dir` parameter.
INVOCATION_DIR: Optional[str] = None


@app.callback()
def main(
    ctx: typer.Context,
    version: bool = typer.Option(
        None,
        "--version",
        "-v",
        help="Show version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    """EnvShield: Your Environment's First Line of Defense."""
    global INVOCATION_DIR
    INVOCATION_DIR = os.getcwd()

    # 'init' always operates on literal cwd -- it's the one command whose
    # whole job is "set up a project right here"; walking up to an
    # ancestor's envshield.yml first would make it report an unrelated
    # parent project as already set up instead of initializing this one.
    if ctx.invoked_subcommand != "init":
        root = config_manager.find_project_root()
        if root and os.path.abspath(root) != INVOCATION_DIR:
            os.chdir(root)


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
hook_app = typer.Typer(
    name="hook",
    help="Install, check, and remove EnvShield's Git hooks.",
    no_args_is_help=True,
)
app.add_typer(hook_app, name="hook")


def _seed_schema_from_file(config_file: str, schema_path: str) -> None:
    """Writes a schema at `schema_path` generated from `config_file`'s real values, unless one already exists there."""
    if os.path.exists(schema_path):
        return
    content = importer.generate_schema_from_file(config_file)
    with open(schema_path, "w") as f:
        f.write(content)


def _print_service_header(targets: List[str], target: str) -> None:
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
    """Initializes EnvShield -- builds env.schema.toml from your real config if one is found, otherwise a framework-aware template."""
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
        # envshield.yml always registers at least one named service, even
        # here for a brand-new single-service project -- there's no
        # separate "rootless" shape. Growing into a second service later is
        # then just appending another entry to the same `services` map,
        # not a structural migration (see config_manager.add_service).
        service_name = project_name

        # Prefer building the schema from a real, already-existing config
        # source over a generic framework template -- a fixed template can
        # only ever guess at your actual variables.
        config_source = service_discovery.find_config_source(".")
        used_real_source = bool(config_source)
        if config_source:
            console.print(
                f"Found [bold yellow]{config_source}[/bold yellow] -- building your schema from its real variables."
            )
            schema_content = importer.generate_schema_from_file(
                config_source, interactive=False
            )
        else:
            schema_content = config_manager.generate_default_schema_content(
                project_type
            )

        config_manager.write_file(
            config_manager.SCHEMA_FILE_NAME,
            schema_content,
            f"Created/updated schema: [bold cyan]{config_manager.SCHEMA_FILE_NAME}[/bold cyan]",
        )

        compose_file = service_discovery.find_compose_file(".", ".")
        if compose_file and service_discovery.compose_declares_service(
            compose_file, service_name
        ):
            console.print(
                f"Found deployment manifest [bold yellow]{compose_file}[/bold yellow] -- registering it so 'check'/'doctor' validate it automatically."
            )
        else:
            compose_file = None
        config_content = config_manager.generate_default_config_content(
            project_name, service_name, deployment_manifest=compose_file
        )
        config_manager.write_file(
            config_manager.CONFIG_FILE_NAME,
            config_content,
            f"Created/updated config: [bold cyan]{config_manager.CONFIG_FILE_NAME}[/bold cyan]",
        )

        config_manager.update_gitignore()
        schema_manager.sync_schema(service_name=service_name)

        # Offer to install git hooks
        hm = hooks_manager.HooksManager()
        hm.install_hooks_if_needed(auto=True, force=False)

    except EnvShieldException as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)
    except (KeyboardInterrupt, TypeError):
        console.print("\n[yellow]Initialization cancelled by user.[/yellow]")
        raise typer.Exit()

    console.print("\n[bold green]✨ Setup Complete! ✨[/bold green]")
    if used_real_source:
        console.print(
            "Your project is now protected. Review 'env.schema.toml' -- it was built from your real config, but double-check the secret/type guesses."
        )
    else:
        console.print(
            "Your project is now protected. Define your variables in 'env.schema.toml'."
        )
    console.print("\n[bold cyan]Next step:[/bold cyan]")
    console.print("  envshield setup    # Configure your local environment")


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
    container: Optional[str] = typer.Option(
        None,
        "--container",
        help=(
            "For a docker-compose or Kubernetes manifest declaring more than one service/container, which one to validate."
        ),
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Print machine-readable JSON instead of a table; suppresses all other output.",
    ),
):
    """Validates a local environment file against the schema. Also accepts a docker-compose or Kubernetes manifest."""
    try:
        # resolve_targets already never blocks on the interactive "Which
        # service?" picker without a TTY to answer it (CI/scripting,
        # --json included) -- it runs every configured service instead.
        targets = service_manager.resolve_targets(
            service, invocation_dir=INVOCATION_DIR
        )
    except EnvShieldException as e:
        if json_output:
            print(json.dumps({"success": False, "error": str(e)}, indent=2))
        else:
            console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)

    # An explicit file only makes sense for a single target -- it can't
    # apply to every service's own local file at once.
    if len(targets) > 1 and file:
        if not json_output:
            console.print(
                "[yellow]Ignoring the explicit file argument -- each service's own local file is checked when validating multiple services.[/yellow]"
            )
        file = None

    had_error = False
    results = []
    for target in targets:
        if not json_output:
            _print_service_header(targets, target)
        try:
            resolved_file = (
                file or config_manager.get_env_paths(service_name=target)["local_file"]
            )
            if json_output:
                result = schema_manager.check_result(
                    resolved_file, service_name=target, container=container
                )
                results.append(result)
                if not result["clean"]:
                    had_error = True
            elif not schema_manager.check_schema(
                resolved_file, service_name=target, container=container
            ):
                had_error = True

            # An explicit file argument means the user asked for exactly
            # that file, and nothing else -- only pile on registered
            # deployment manifests when we're checking the service's own
            # default local file. A service can be named in more than one
            # manifest (a local compose file and a production Kubernetes
            # manifest, say), so every match gets validated, not just one.
            if not file:
                for manifest in config_manager.get_deployment_manifests(target):
                    manifest_container = manifest.get("container") or container
                    if json_output:
                        result = schema_manager.check_result(
                            manifest["path"],
                            service_name=target,
                            container=manifest_container,
                        )
                        results.append(result)
                        if not result["clean"]:
                            had_error = True
                    elif not schema_manager.check_schema(
                        manifest["path"],
                        service_name=target,
                        container=manifest_container,
                    ):
                        had_error = True
        except EnvShieldException as e:
            if json_output:
                results.append({"service": target, "clean": False, "error": str(e)})
            else:
                console.print(f"[bold red]Error:[/bold red] {e}")
            had_error = True

    if json_output:
        print(json.dumps({"success": not had_error, "results": results}, indent=2))

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
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Print machine-readable JSON instead of a report; suppresses all other output. Incompatible with --fix.",
    ),
):
    """Runs a full health check on your project's EnvShield setup."""
    if json_output and fix:
        console.print(
            "[bold red]Error:[/bold red] --json and --fix cannot be used together."
        )
        raise typer.Exit(code=1)

    try:
        # resolve_targets already never blocks on the interactive "Which
        # service?" picker without a TTY to answer it (CI/scripting,
        # --json included) -- it runs every configured service instead.
        targets = service_manager.resolve_targets(
            service, invocation_dir=INVOCATION_DIR
        )
    except EnvShieldException as e:
        if json_output:
            print(json.dumps({"success": False, "error": str(e)}, indent=2))
        else:
            console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)

    had_error = False
    results = []
    for target in targets:
        if not json_output:
            _print_service_header(targets, target)
        try:
            if json_output:
                result = doctor.run_health_check_json(service_name=target)
                results.append(result)
                if not result["passed"]:
                    had_error = True
            else:
                doctor.run_health_check(fix=fix, service_name=target)
        except typer.Exit as e:
            if e.exit_code:
                had_error = True
        except EnvShieldException as e:
            if json_output:
                results.append({"service": target, "passed": False, "error": str(e)})
            else:
                console.print(f"[bold red]Error:[/bold red] {e}")
            had_error = True

    if json_output:
        print(json.dumps({"success": not had_error, "results": results}, indent=2))

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
        targets = service_manager.resolve_targets(
            service, invocation_dir=INVOCATION_DIR
        )
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
            setup_manager.run_setup(service_name=target, output_file=output_file)

        # After successful setup, offer to install git hooks
        hm = hooks_manager.HooksManager()
        hm.install_hooks_if_needed(auto=True, force=False)

        console.print("\n[bold green]✓ Configuration complete![/bold green]")
        hm.print_hook_status()
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
        targets = service_manager.resolve_targets(
            service, invocation_dir=INVOCATION_DIR
        )
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
# config module instead of Python's.
_FRAMEWORK_DEFAULT_LANG = {
    "nextjs": "typescript",
    "vite": "typescript",
    "nodejs": "typescript",
}
# Detected types with no codegen mapping at all (python-* isn't here -- those
# fall through to the python default below, same as an undetected project).
# Silently guessing 'python' for one of these would be actively wrong, not
# just unhelpful -- e.g. a Go project has nothing to do with pydantic-settings.
# Erroring and asking for an explicit --lang beats a wrong file nobody asked for.
_NO_DEFAULT_LANG_TYPES = {"go"}

_GENERATE_HELP_TEXT = {
    "python": (
        "[dim]Requires 'pydantic' and 'pydantic-settings' in your project. Import with: from {module} import settings[/dim]"
    ),
    "typescript": (
        "[dim]Requires 'zod' in your project. Import with: import {{ env }} from './{module}'[/dim]"
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
    if project_type in _NO_DEFAULT_LANG_TYPES:
        raise EnvShieldException(
            f"No --lang given, and '{project_type}' has no default codegen target. Pass --lang python or --lang typescript explicitly."
        )
    resolved = (
        _FRAMEWORK_DEFAULT_LANG.get(project_type, "python")
        if project_type
        else "python"
    )
    console.print(
        f"[dim]No --lang given; detected '{resolved}' for this project.[/dim]"
    )
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

        if service or service_manager.get_available_services():
            resolved_service = cast(
                str,
                service_manager.resolve_service(service, invocation_dir=INVOCATION_DIR),
            )
            schema = config_manager.load_schema(service_name=resolved_service)
        else:
            # Nothing registered yet -- generate doesn't touch a service's
            # local files, so there's nothing registration would actually
            # buy here. Read the schema directly rather than forcing
            # 'envshield init'/'service add' just to run this command.
            schema = config_manager.load_bare_schema()
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
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Print machine-readable JSON instead of tables; suppresses all other output.",
    ),
):
    """Scans files for hardcoded secrets and undeclared variables."""
    try:
        if service:
            # Validate eagerly for a consistent "Available: ..." error --
            # run_scan's own service_name=None path means "check every
            # configured service", so this only fires for an explicit name.
            service_manager.resolve_service(service, invocation_dir=INVOCATION_DIR)

        if json_output:
            result = scanner.scan_result(
                paths=paths,
                staged_only=staged,
                config_path=config,
                exclude_patterns=exclude,
                service_name=service,
            )
            print(json.dumps(result, indent=2))
            if not result["clean"]:
                raise typer.Exit(code=1)
        else:
            scanner.run_scan(
                paths=paths,
                staged_only=staged,
                config_path=config,
                exclude_patterns=exclude,
                service_name=service,
            )
    except EnvShieldException as e:
        if json_output:
            print(json.dumps({"clean": False, "error": str(e)}, indent=2))
        else:
            console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)


def _install_hooks() -> None:
    scanner.install_pre_commit_hook()
    scanner.install_post_merge_hook()


@app.command("install-hook")
def install_hook():
    """Installs Git hooks: pre-commit (scan for secrets) and post-merge (check env config after pull). Same as 'hook install'."""
    try:
        _install_hooks()
    except EnvShieldException as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)


@hook_app.command("install")
def hook_install():
    """Installs Git hooks: pre-commit (scan for secrets) and post-merge (check env config after pull)."""
    try:
        _install_hooks()
    except EnvShieldException as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)


@hook_app.command("status")
def hook_status():
    """Shows which EnvShield Git hooks are currently installed."""
    hooks_manager.HooksManager().print_hook_status()


@hook_app.command("remove")
def hook_remove():
    """Removes any EnvShield-installed Git hook. Leaves alone any hook EnvShield didn't install."""
    try:
        removed = scanner.remove_hooks()
    except EnvShieldException as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)

    if not removed:
        console.print("[yellow]No EnvShield-installed hooks found to remove.[/yellow]")
        return
    for hook_name in removed:
        console.print(f"[bold green]✓[/bold green] Removed {hook_name} hook.")


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
        if service:
            service_manager.resolve_service(service, invocation_dir=INVOCATION_DIR)
            output = cast(str, config_manager.get_service_schema_path(service))
        elif output == config_manager.SCHEMA_FILE_NAME:
            if service_manager.get_available_services():
                # No explicit --service or --output, but something is
                # already registered: target whichever service is
                # currently the (only, or interactively chosen) one --
                # same default-targeting every other command uses, rather
                # than writing to a literal path that's only coincidentally
                # correct for a single, unnested service.
                service = cast(
                    str,
                    service_manager.resolve_service(
                        None, invocation_dir=INVOCATION_DIR
                    ),
                )
                output = cast(str, config_manager.get_service_schema_path(service))
            else:
                # Totally fresh project, nothing registered yet: bootstrap
                # it the same way 'init' does (one service, named after the
                # project directory) rather than writing a schema to a
                # location nothing in envshield.yml knows about -- that's
                # what lets the auto-sync below keep .env.example honest
                # from this very first import, not just from the next one.
                service = os.path.basename(os.getcwd())
                config_manager.add_service(service, output)

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

        # Keep the tracked template (.env.example) in sync with the schema
        # we just (re)wrote -- only when we resolved a real service above,
        # not for an arbitrary --output destination there's no template
        # mapping for.
        if service:
            schema_manager.sync_schema(service_name=service)
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
            "[yellow]No services configured yet. Run 'envshield init' first.[/yellow]"
        )
        return

    table = Table(title="Configured Services")
    table.add_column("Name", style="cyan")
    table.add_column("Schema", style="white")
    table.add_column("Local File", style="white")
    had_error = False
    for name in sorted(services.keys()):
        try:
            paths = config_manager.get_env_paths(service_name=name)
            schema_path = config_manager.get_service_schema_path(name)
            table.add_row(name, schema_path or "-", paths["local_file"])
        except EnvShieldException as e:
            table.add_row(name, f"[red]error: {e}[/red]", "-")
            had_error = True
    console.print(table)
    if had_error:
        raise typer.Exit(code=1)


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
    deployment_manifest: Optional[str] = typer.Option(
        None,
        "--deployment-manifest",
        help="A docker-compose file or Kubernetes manifest to validate automatically with 'check'/'doctor'.",
    ),
    manifest_container: Optional[str] = typer.Option(
        None,
        "--container",
        help="Which service/container in --deployment-manifest is this service, if it isn't named the same.",
    ),
):
    """Registers one service in envshield.yml by hand, creating the file if needed."""
    try:
        schema_path = schema or os.path.join(directory, config_manager.SCHEMA_FILE_NAME)
        if not deployment_manifest:
            found_manifest = service_discovery.find_compose_file(directory, ".")
            # Auto-discovery only wires it up when the manifest actually
            # names this service -- an explicit --deployment-manifest below
            # always overrides this, since that's the user saying so directly.
            if found_manifest and service_discovery.compose_declares_service(
                found_manifest, manifest_container or name
            ):
                deployment_manifest = found_manifest
                console.print(
                    f"[dim]Found deployment manifest {deployment_manifest} -- registering it too.[/dim]"
                )
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
        if deployment_manifest:
            config_manager.add_manifest(
                deployment_manifest, {manifest_container or name: name}
            )

        if import_from:
            if os.path.exists(schema_path):
                console.print(
                    f"[yellow]'{schema_path}' already exists -- not overwriting. Run 'envshield import {import_from} --service {name} --force' to regenerate it.[/yellow]"
                )
            else:
                _seed_schema_from_file(import_from, schema_path)
                console.print(
                    f"[bold green]✓[/bold green] Seeded schema from [bold cyan]{import_from}[/bold cyan]"
                )
    except EnvShieldException as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)


@service_app.command("remove")
def service_remove(
    name: str = typer.Argument(..., help="Name of the service to de-register."),
):
    """
    De-registers one service from envshield.yml, and drops it from any
    deployment manifest's container mapping. Never deletes the service's
    own files (schema, local env file, etc.) -- only the registration.
    """
    try:
        config_manager.remove_service(name)
    except EnvShieldException as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)
    console.print(
        f"[bold green]✓[/bold green] Removed service [bold cyan]{name}[/bold cyan] from envshield.yml."
    )


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
    table.add_column("Deployment Manifest", style="white")
    for c in candidates:
        config_file_display = c["local_file"] or c["example_file"] or "(default .env)"
        table.add_row(
            c["name"],
            c["dir"],
            c["format"],
            config_file_display,
            c["deployment_manifest"] or "-",
        )
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

    registered_count = 0
    for c in selected:
        schema_path = os.path.join(c["dir"], config_manager.SCHEMA_FILE_NAME)
        try:
            config_manager.add_service(
                c["name"],
                schema_path,
                local_file=c["local_file"],
                example_file=c["example_file"],
            )
            if c["deployment_manifest"]:
                config_manager.add_manifest(
                    c["deployment_manifest"], {c["name"]: c["name"]}
                )
        except EnvShieldException as e:
            console.print(f"[bold red]Error:[/bold red] Skipping '{c['name']}': {e}")
            continue
        console.print(
            f"[bold green]✓[/bold green] Registered [bold cyan]{c['name']}[/bold cyan] → {schema_path}"
        )
        registered_count += 1

        # Seed from whichever real signal was actually found: the local
        # file if one exists, else a template (blank values, but still
        # documents every var name) if that's all there was.
        config_file = (
            c["local_file"] or c["example_file"] or os.path.join(c["dir"], ".env")
        )
        if os.path.exists(config_file) and not os.path.exists(schema_path):
            _seed_schema_from_file(config_file, schema_path)
            console.print(f"    seeded schema from [dim]{config_file}[/dim]")

    console.print(
        f"\n[bold green]✨ Added {registered_count} service(s) to envshield.yml.[/bold green]"
    )

    # Offer to install git hooks
    hm = hooks_manager.HooksManager()
    hm.install_hooks_if_needed(auto=True, force=False)

    console.print("\n[bold cyan]Next step:[/bold cyan]")
    console.print("  envshield setup    # Configure your local environment")
