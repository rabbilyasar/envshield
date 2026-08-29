# envshield/cli.py
import json
import os
from typing import Any, Dict, List, Optional, cast

import questionary
import toml
import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from envshield import __version__
from envshield.core import importer

from .config import manager as config_manager
from .core import (
    contract_diff,
    dependency_diff,
    dependency_snapshot,
    doctor,
    explain,
    generator,
    hooks_manager,
    inspector,
    scanner,
    schema_manager,
    schema_snapshot,
    service_discovery,
    service_manager,
    setup_manager,
)
from .core.exceptions import EnvShieldException, SchemaNotFoundError
from .utils import git_utils


# --- Main App Setup ---
def _version_callback(version: bool) -> None:
    """Print version and exit."""
    if version:
        console = Console()
        console.print(f"envshield {__version__}")
        raise typer.Exit()


app = typer.Typer(
    name="envshield",
    help="EnvShield: environment variables as a version-controlled configuration contract.",
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
    """EnvShield: environment variables as a version-controlled configuration contract."""
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
    help="Discover, add, list, and remove services for a multi-service project.",
    no_args_is_help=True,
)
app.add_typer(service_app, name="service")
hook_app = typer.Typer(
    name="hook",
    help="Install, inspect the status of, and remove EnvShield's Git hooks.",
    no_args_is_help=True,
)
app.add_typer(hook_app, name="hook")


def _seed_schema_from_file(
    config_file: str, schema_path: str, local_file: Optional[str] = None
) -> None:
    """
    Writes a schema at `schema_path` generated from `config_file`'s real
    values, unless one already exists there.

    `local_file` is the path this service is being registered with as its
    own local values file, when there is one. Seeding from that same file
    means its configuration surface must be read the way every command
    that later consumes it reads it -- see
    importer._discover_python_variables. Seeding from anything else (a
    template, a settings module the user pointed `--import` at) keeps the
    importer's own default reading.
    """
    if os.path.exists(schema_path):
        return
    content = importer.generate_schema_from_file(
        config_file,
        as_local_values=(local_file is not None and config_file == local_file),
    )
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
    """Initializes EnvShield -- builds env.schema.toml from your real config if one is found, otherwise a framework-aware template.

    Whole-project setup: also writes envshield.yml, registers a deployment
    manifest if one is found, updates .gitignore, and offers to install Git
    hooks. Use this whenever 'envshield.yml' doesn't exist yet -- including
    on an already-existing project, since it detects and imports real
    config the same way 'import' does. For re-deriving a single already-
    registered service's schema only (no project-wide setup), use
    'envshield import' instead.
    """
    console.print(
        Panel(
            "[bold cyan]Welcome to EnvShield! Setting up your configuration contract...[/bold cyan]",
            title="EnvShield",
            border_style="green",
        )
    )

    # Whether there's actually anything real to protect -- not just
    # whether the file happens to exist. 'envshield.yml' can exist with
    # zero registered services (e.g. right after removing the last one via
    # 'service remove'), and that's functionally identical to an
    # uninitialized project everywhere else in this codebase (see
    # get_services, resolve_service) -- 'init' gating on raw file
    # existence instead would block its own suggested "run 'envshield
    # init'" recovery path with a confusing "already exists."
    has_services = bool(config_manager.get_services())

    if has_services and not force:
        console.print(
            "[yellow]An EnvShield setup already exists. Run '[bold]envshield "
            "setup[/bold]' to configure your local environment, or use "
            "'--force' to overwrite the existing schema/config.[/yellow]"
        )
        raise typer.Exit()

    if force and has_services:
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

        # Reuse whichever real file the schema was originally built from,
        # if this is a re-run (--force) and one was recorded, instead of
        # re-detecting from scratch -- auto-detection can legitimately
        # favor a different file this time (e.g. a real '.env' that now
        # exists but has since drifted, missing a variable the actual
        # source file still declares), silently regressing the schema.
        recorded_source = config_manager.get_service_config_source(service_name)
        reuse_recorded = bool(recorded_source and os.path.exists(recorded_source))

        # The one case worth breaking that stability for: the recorded
        # source has itself since become an EnvShield-generated file (e.g.
        # 'setup' regenerated it from the schema) -- a derivative, not an
        # independent source, so re-scanning it forever can never pick up
        # a variable added straight to the real config elsewhere. Caught
        # and offered as an explicit choice right here, rather than a
        # separate flag to remember exists.
        if (
            reuse_recorded
            and recorded_source
            and service_discovery.is_envshield_generated(recorded_source)
        ):
            if hooks_manager._is_interactive():
                repin = questionary.confirm(
                    f"Recorded config source '{recorded_source}' looks "
                    "auto-generated by EnvShield itself, not a real source -- "
                    "re-detect a better one?",
                    default=False,
                ).ask()
                if repin:
                    reuse_recorded = False
            else:
                console.print(
                    f"[yellow]Recorded config source '{recorded_source}' looks "
                    "auto-generated by EnvShield itself, not a real source. "
                    "Re-run 'envshield init --force' from an interactive shell "
                    "to be prompted to fix this.[/yellow]"
                )

        if reuse_recorded:
            config_source = recorded_source
        else:
            # Prefer building the schema from a real, already-existing
            # config source over a generic framework template -- a fixed
            # template can only ever guess at your actual variables.
            config_source = service_discovery.find_config_source(".")
        if config_source:
            config_source = os.path.normpath(config_source)
        used_real_source = bool(config_source)

        # A re-scan should only ever be able to learn about new variables,
        # never silently discard or reclassify one the schema already
        # declares -- whether that's a whole variable a drifted source no
        # longer mentions, or a hand-picked detail (a corrected 'secret'
        # flag, an added 'pattern') on one that survives.
        existing_schema = None
        if os.path.exists(config_manager.SCHEMA_FILE_NAME):
            try:
                existing_schema = config_manager.load_toml_schema(
                    config_manager.SCHEMA_FILE_NAME
                )
            except EnvShieldException:
                existing_schema = None

        if config_source:
            console.print(
                f"Found [bold yellow]{config_source}[/bold yellow] -- building your schema from its real variables."
            )
            schema_content = importer.generate_schema_from_file(
                config_source, interactive=False, existing_schema=existing_schema
            )

            # A pinned config_source is only ever re-scanned itself, so it
            # can't notice a variable added straight to some OTHER real
            # config file sitting right next to it (e.g. a Python config
            # module gaining a var a stale '.env' never picked up). Check
            # for that drift right here instead of leaving it to a later,
            # separate 'envshield doctor' run to catch.
            other_sources = service_discovery.find_other_config_sources(
                ".", config_source
            )
            if other_sources:
                schema_dict = toml.loads(schema_content)
                added = importer.merge_variables_from_other_sources(
                    schema_dict, other_sources
                )
                if added:
                    schema_content = importer.SCHEMA_HEADER + toml.dumps(schema_dict)
                    for path, keys in added.items():
                        console.print(
                            f"[yellow]Also found in '{path}' (not in '{config_source}'): "
                            f"{', '.join(keys)} -- added to the schema.[/yellow]"
                        )
        else:
            # No real config source at the project root -- before falling
            # back to a purely generic, fabricated template, check whether
            # this actually looks like a multi-service project instead
            # (service-like subdirectories with their own real config).
            # Silently building a fictional root-level schema here and
            # declaring success is worse than the fallback itself: it
            # never mentions the real services at all.
            candidates = service_discovery.discover_candidates(".")
            if candidates:
                names = sorted(c["name"] for c in candidates)
                console.print(
                    "[yellow]This looks like a multi-service project -- found "
                    f"service-like director{'y' if len(names) == 1 else 'ies'} "
                    f"with their own config: {', '.join(names)}. A single generic "
                    "schema at the project root won't capture their real "
                    "variables. Consider 'envshield service discover' "
                    "instead.[/yellow]"
                )
                if hooks_manager._is_interactive():
                    proceed = questionary.confirm(
                        "Continue with a single generic template at the "
                        "project root anyway?",
                        default=False,
                    ).ask()
                    if not proceed:
                        console.print("[yellow]Initialization cancelled.[/yellow]")
                        raise typer.Exit()
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
            project_name,
            service_name,
            deployment_manifest=compose_file,
            config_source=config_source,
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
        metavar="FILE",
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


_EXPLAIN_REQUIREDNESS_LABEL = {
    "required": "Yes",
    "optional": "No (has default)",
    "conditional": "Conditional",
}


def _render_explain_report(report: "explain.ExplainReport") -> None:
    console.print(f"\n[bold cyan]{report.variable}[/bold cyan]")
    console.print("─" * max(len(report.variable), 8))

    schema = report.schema
    console.print("\n[bold]Contract[/bold]")
    console.print(f"  Type:       {schema['type']}")
    console.print(
        f"  Required:   {_EXPLAIN_REQUIREDNESS_LABEL[schema['requiredness']]}"
    )
    if schema["requiredness"] == "conditional":
        condition = schema["requiredIf"] or {}
        console.print(
            f"              when {condition.get('var')} == "
            f'"{condition.get("equals", "true")}"'
        )
    default = schema["default"]
    console.print(f"  Default:    {default if default is not None else '—'}")
    console.print(f"  Secret:     {'Yes' if schema['secret'] else 'No'}")
    if schema["enum"]:
        console.print(f"  Enum:       {', '.join(schema['enum'])}")
    if schema["pattern"]:
        console.print(f"  Pattern:    {schema['pattern']}")
    if schema["description"]:
        console.print(f"  Description: {schema['description']}")

    console.print("\n[bold]Declared in[/bold]")
    console.print(f"  {report.provenance['schema_path']}")
    if report.provenance["inherited"] is True:
        console.print(f"  inherited from {report.provenance['declared_in']}")
    elif report.provenance["inherited"] is None:
        console.print(
            "  [dim](could not determine whether this is inherited via extends)[/dim]"
        )

    console.print("\n[bold]Used in source[/bold]")
    if report.source_usages:
        for usage in report.source_usages:
            console.print(f"  {usage.file_path}:{usage.line}")
    else:
        console.print("  None discovered")
        console.print(
            "  [dim]EnvShield only recognizes os.environ/os.getenv/process.env-style "
            "reads in Python and JS/TS -- this doesn't prove the variable is unused.[/dim]"
        )

    if report.required_by:
        console.print("\n[bold]Required by[/bold]")
        for entry in report.required_by:
            condition = entry["condition"] or {}
            console.print(f"  {entry['variable']}")
            console.print(
                f'    when {report.variable} == "{condition.get("equals", "true")}"'
            )

    console.print("\n[bold]Deployment[/bold]")
    if not report.manifest_references:
        console.print("  No deployment manifest registered for this service.")
    else:
        declared = [r for r in report.manifest_references if r.status == "declared"]
        not_declared = [
            r for r in report.manifest_references if r.status == "not_declared"
        ]
        unresolved = [r for r in report.manifest_references if r.status == "unresolved"]
        errored = [r for r in report.manifest_references if r.status == "error"]
        if declared:
            for ref in declared:
                console.print(f"  {ref.path}")
                if ref.container:
                    console.print(f"    container: {ref.container}")
        elif not_declared:
            # Only claim "not found" when at least one manifest positively
            # doesn't declare it -- printing this alongside "Cannot
            # confirm" below (when every non-declared reference is
            # actually unresolved) would read as EnvShield confidently
            # asserting absence in the same breath it admits it can't
            # tell.
            checked = ", ".join(r.path for r in report.manifest_references)
            console.print(
                "  Not found in any registered deployment manifest "
                f"(checked: {checked}) -- this doesn't prove it isn't deployed elsewhere."
            )
        if unresolved:
            console.print(
                f"  [yellow]Cannot confirm for {len(unresolved)} manifest(s): "
                f"{', '.join(r.path for r in unresolved)} -- references an external "
                f"ConfigMap/Secret not included in the manifest; '{report.variable}' "
                "may be supplied from there.[/yellow]"
            )
        if errored:
            console.print(
                f"  [dim]Could not check {len(errored)} manifest(s): "
                f"{', '.join(r.path for r in errored)}[/dim]"
            )


@app.command(name="explain")
def explain_command(
    variable: str = typer.Argument(..., metavar="VARIABLE"),
    service: Optional[str] = typer.Option(
        None,
        "--service",
        "-s",
        help="Which service's schema to explain the variable against.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Print machine-readable JSON instead of a report; suppresses all other output.",
    ),
):
    """
    Reports what EnvShield currently knows about one environment variable:
    its contract, where it's declared (including through 'extends'), what
    source code reads it, which other variables' requiredIf conditions
    reference it, and which registered deployment manifests declare it.
    """
    try:
        resolved_service = service_manager.resolve_service(
            service, invocation_dir=INVOCATION_DIR
        )
        assert isinstance(resolved_service, str)
    except EnvShieldException as e:
        if json_output:
            print(json.dumps(explain.error_dict(variable, None, str(e)), indent=2))
        else:
            console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)

    try:
        report = explain.build_report(variable, resolved_service)
    except EnvShieldException as e:
        if json_output:
            print(
                json.dumps(
                    explain.error_dict(variable, resolved_service, str(e)), indent=2
                )
            )
        else:
            console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)

    if json_output:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        _render_explain_report(report)


@app.command()
def setup(
    output_file: Optional[str] = typer.Argument(
        None,
        metavar="OUTPUT_FILE",
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
        targets, provenance = service_manager.resolve_targets_with_provenance(
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

    # Only worth explaining *why* a service was picked when directory
    # inference is actually what picked it -- not merely when the result
    # happens to match what inference would have produced (a single-
    # service project's root directory, or an interactive pick, can both
    # coincidentally match cwd without inference having run at all).
    if provenance == service_manager.PROVENANCE_INFERRED:
        console.print(
            f"[dim]Configuring '{targets[0]}' (inferred from the current directory).[/dim]"
        )

    try:
        results: List[setup_manager.SetupResult] = []
        for target in targets:
            _print_service_header(targets, target)
            results.append(
                setup_manager.run_setup(service_name=target, output_file=output_file)
            )

        completed = [r for r in results if r.completed]
        skipped_count = len(results) - len(completed)

        if completed:
            # After successful setup, offer to install git hooks -- even a
            # partial success (some services configured, others declined)
            # still means there's now something worth hooking.
            hm = hooks_manager.HooksManager()
            hm.install_hooks_if_needed(auto=True, force=False)
            total_configured = sum(r.configured_count for r in completed)
            if len(results) > 1:
                console.print(
                    f"\n[bold green]✓ Configuration complete[/bold green] for "
                    f"{len(completed)} of {len(results)} service(s) "
                    f"({total_configured} variable(s) set)."
                )
            else:
                console.print(
                    f"\n[bold green]✓ Configuration complete[/bold green] "
                    f"({total_configured} variable(s) set)."
                )
            hm.print_hook_status()

        if skipped_count:
            console.print(
                f"[yellow]{skipped_count} of {len(results)} service(s) left "
                "unchanged.[/yellow]"
            )
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
    check: bool = typer.Option(
        False,
        "--check",
        help="Don't write anything -- report whether the tracked template already matches the schema, exiting non-zero if not. For a pre-commit hook: catches a schema edited without also regenerating .env.example, before it's committed stale.",
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
    any_changed = False
    for target in targets:
        _print_service_header(targets, target)
        if check:
            passed, message = doctor._check_example_file_sync(service_name=target)
            if passed:
                console.print(f"[green]✓[/green] {message}")
            else:
                console.print(f"[red]✗[/red] {message}")
                had_error = True
            continue
        try:
            if schema_manager.sync_schema(service_name=target):
                any_changed = True
        except EnvShieldException as e:
            console.print(f"[bold red]Error:[/bold red] {e}")
            had_error = True

    if had_error:
        raise typer.Exit(code=1)

    # A real change gets an actionable next step; a no-op still gets an
    # explicit closing line rather than trailing off after the per-service
    # "already up to date" -- silence there reads as "did this actually
    # finish?" rather than "there was nothing to do."
    if not check:
        if any_changed:
            console.print(
                "\n[bold cyan]Next step:[/bold cyan]\n"
                "  Review the updated template, then commit it.\n"
                "  If it added a variable you need locally, run 'envshield setup'."
            )
        else:
            console.print("\n[dim]Nothing to do -- already in sync.[/dim]")


_CONTRACT_DIFF_CATEGORY_STYLE = {
    "breaking": ("bold red", "BREAKING"),
    "security": ("bold yellow", "SECURITY"),
    "requires_review": ("yellow", "REVIEW"),
    "default_changed": ("cyan", "default changed"),
    "informational": ("dim", "info"),
    "non_breaking": ("dim", "non-breaking"),
}


def _render_contract_diff_table(
    result: "contract_diff.ContractDiff",
    label_a: str,
    label_b: str,
    fail_on_categories: frozenset = contract_diff.DEFAULT_BLOCKING_CATEGORIES,
    service: Optional[str] = None,
    multi_service: bool = False,
) -> None:
    if not result.changes:
        console.print(
            f"[dim]No contract changes between '{label_a}' and '{label_b}'.[/dim]"
        )
        return

    table = Table(title=f"Contract diff: '{label_a}' -> '{label_b}'")
    table.add_column("Variable", style="bold cyan")
    table.add_column("Category")
    table.add_column("Description")

    # Breaking first, then security, then everything else -- the changes
    # most likely to need action shouldn't be buried below cosmetic ones.
    order = {
        "breaking": 0,
        "security": 1,
        "requires_review": 2,
        "default_changed": 3,
        "informational": 4,
        "non_breaking": 5,
    }
    for change in sorted(result.changes, key=lambda c: order.get(c.category, 99)):
        style, label = _CONTRACT_DIFF_CATEGORY_STYLE.get(
            change.category, ("", change.category)
        )
        table.add_row(
            change.variable, f"[{style}]{label}[/{style}]", change.description
        )

    console.print(table)
    blocking_changes = [
        c
        for c in result.changes
        if contract_diff.is_blocking_change(c, fail_on_categories)
    ]
    if blocking_changes:
        blocking_categories_present = sorted({c.category for c in blocking_changes})
        console.print(
            f"\n[bold red]This change includes {', '.join(blocking_categories_present)} "
            "change(s) that block under --fail-on.[/bold red]"
        )
        # Only a service-scoped run knows what '--service' value to suggest
        # -- a single-service project's 'explain' call needs none, matching
        # every other command's own "only show --service when it's
        # genuinely ambiguous" convention (see _print_service_header).
        service_flag = f" --service {service}" if multi_service and service else ""
        blocking_vars = sorted({c.variable for c in blocking_changes})
        console.print(
            f"[dim]Inspect with 'envshield explain <VAR>{service_flag}': "
            f"{', '.join(blocking_vars)}[/dim]"
        )


_DEPENDENCY_CHANGE_CATEGORY_STYLE = {
    "missing_declaration": ("bold red", "missing declaration"),
    "declared": ("dim", "declared"),
}


def _render_dependency_change_table(
    result: "dependency_diff.DependencyChangeReport", label_a: str, label_b: str
) -> None:
    if not result.changes:
        console.print(
            f"[dim]No new source dependencies between '{label_a}' and '{label_b}'.[/dim]"
        )
        return

    table = Table(title=f"New source dependencies: '{label_a}' -> '{label_b}'")
    table.add_column("Variable", style="bold cyan")
    table.add_column("File")
    table.add_column("Line")
    table.add_column("Access")
    table.add_column("Contract status")

    order = {"missing_declaration": 0, "declared": 1}
    for change in sorted(
        result.changes, key=lambda c: (order.get(c.category, 99), c.file_path, c.line)
    ):
        style, label = _DEPENDENCY_CHANGE_CATEGORY_STYLE.get(
            change.category, ("", change.category)
        )
        table.add_row(
            change.variable,
            change.file_path,
            str(change.line),
            change.access_type,
            f"[{style}]{label}[/{style}]",
        )

    console.print(table)
    if result.has_missing_declarations:
        console.print(
            "\n[bold red]New source dependencies are missing from the contract.[/bold red]"
        )


@app.command(name="undeclared")
def undeclared(
    rev_a: Optional[str] = typer.Argument(
        None,
        metavar="[REV_A]",
        help="Baseline revision. Omit both REV_A and REV_B to compare HEAD against your current working tree (uncommitted and untracked files included).",
    ),
    rev_b: Optional[str] = typer.Argument(
        None,
        metavar="[REV_B]",
        help="Revision to check for newly introduced dependencies.",
    ),
    service: Optional[str] = typer.Option(
        None,
        "--service",
        "-s",
        help="Which service's schema to check against. Required in a multi-service project unless run from inside that service's own directory.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Print machine-readable JSON instead of a table; suppresses all other output.",
    ),
):
    """
    Reports environment-variable reads newly introduced since a given
    revision that aren't declared in the schema -- unlike 'scan' (which
    inventories every currently-undeclared read across the whole
    codebase), this only flags what's new. With no arguments, compares
    HEAD against your current working tree (uncommitted and untracked
    files included) -- catching a newly introduced dependency before you
    commit it.
    """
    if (rev_a is None) != (rev_b is None):
        message = (
            "pass both revisions, or neither -- 'envshield undeclared' "
            "alone compares HEAD against your current working tree."
        )
        if json_output:
            print(
                json.dumps(
                    {"has_missing_declarations": False, "error": message}, indent=2
                )
            )
        else:
            console.print(f"[bold red]Error:[/bold red] {message}")
        raise typer.Exit(code=1)

    if rev_a is None and rev_b is None:
        revision_a = dependency_snapshot.DEFAULT_REVISION_A
        revision_b = dependency_snapshot.DEFAULT_REVISION_B
        label_a, label_b = "HEAD", "working tree"
    else:
        # The mismatched-pair case already exited above -- both are
        # guaranteed non-None here.
        assert rev_a is not None and rev_b is not None
        revision_a, revision_b = rev_a, rev_b
        label_a, label_b = rev_a, rev_b

        # git diff/show both treat a bad revision as "no output," which
        # would otherwise read as "nothing changed" rather than "that
        # revision doesn't exist" -- a genuinely empty diff (zero changed
        # files) skips every downstream read that would otherwise surface
        # the problem. Explicit check, rather than let that ambiguity
        # stand (CLAUDE.md's "prefer explicit errors over silent
        # assumptions").
        for candidate in (revision_a, revision_b):
            if not git_utils.revision_exists(candidate):
                message = f"revision '{candidate}' does not resolve to a commit."
                if json_output:
                    print(
                        json.dumps(
                            {"has_missing_declarations": False, "error": message},
                            indent=2,
                        )
                    )
                else:
                    console.print(f"[bold red]Error:[/bold red] {message}")
                raise typer.Exit(code=1)

    try:
        targets = service_manager.resolve_targets(
            service, invocation_dir=INVOCATION_DIR
        )
    except EnvShieldException as e:
        if json_output:
            print(
                json.dumps(
                    {"has_missing_declarations": False, "error": str(e)}, indent=2
                )
            )
        else:
            console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)

    # Mirrors schema_diff's own multi-target loop exactly: a per-service
    # failure (bad schema, missing service dir, etc.) is recorded and
    # forces a non-zero exit, but never aborts the remaining services --
    # one broken service shouldn't hide a real finding in another.
    had_error = False
    any_missing = False
    results: List[Dict[str, Any]] = []

    for target in targets:
        if not json_output:
            _print_service_header(targets, target)
        try:
            usages_a, usages_b = dependency_snapshot.discover_usages_for_service(
                target, revision_a, revision_b, quiet=json_output
            )
            schema_vars = set(
                schema_snapshot.load_schema_for_diff(target, revision_b).keys()
            )
            new_usages = dependency_diff.find_new_usages(usages_a, usages_b)
            result = dependency_diff.classify_against_schema(new_usages, schema_vars)
        except EnvShieldException as e:
            had_error = True
            if json_output:
                results.append({"service": target, "error": str(e)})
            else:
                console.print(f"[bold red]Error:[/bold red] {e}")
            continue

        if result.has_missing_declarations:
            any_missing = True

        if json_output:
            entry = result.to_dict()
            entry["service"] = target
            results.append(entry)
        else:
            _render_dependency_change_table(result, label_a, label_b)

    if json_output:
        # Exactly one resolved target (a single-service project with no
        # --service, or --service NAME in a multi-service project) keeps
        # Milestone 1's flat contract byte-for-byte -- no 'results' key.
        # Only a genuinely new invocation pattern (multi-service, no
        # --service) gets the 'results' wrapper. Branches on len(targets),
        # not on whether --service was passed or on project topology, so
        # every invocation Milestone 1 already supported keeps its exact
        # shape regardless of how many services the project has overall.
        if len(targets) == 1:
            single = results[0]
            if "error" in single:
                payload = {
                    "has_missing_declarations": False,
                    "error": single["error"],
                }
            else:
                payload = {
                    "has_missing_declarations": single["has_missing_declarations"],
                    "changes": single["changes"],
                    "service": single["service"],
                    "revision_a": label_a,
                    "revision_b": label_b,
                }
        else:
            payload = {
                "has_missing_declarations": any_missing,
                "revision_a": label_a,
                "revision_b": label_b,
                "results": results,
            }
        print(json.dumps(payload, indent=2))

    if had_error or any_missing:
        raise typer.Exit(code=1)


_FAIL_ON_DEFAULT = ",".join(sorted(contract_diff.DEFAULT_BLOCKING_CATEGORIES))


@schema_app.command("diff")
def schema_diff(
    rev_a: Optional[str] = typer.Argument(
        None,
        metavar="[REV_A]",
        help="Baseline revision. Omit both REV_A and REV_B to compare HEAD against your current working tree (uncommitted changes, staged or not).",
    ),
    rev_b: Optional[str] = typer.Argument(
        None, metavar="[REV_B]", help="Second revision."
    ),
    service: Optional[str] = typer.Option(
        None,
        "--service",
        "-s",
        help="If set, diff only this service's schema (for multi-service projects).",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Print machine-readable JSON instead of a table; suppresses all other output.",
    ),
    fail_on: str = typer.Option(
        _FAIL_ON_DEFAULT,
        "--fail-on",
        help=(
            "Comma-separated categories that cause a non-zero exit. Valid: "
            f"{', '.join(sorted(contract_diff.CATEGORIES))}. A secret "
            "classification that TIGHTENED (false -> true) never blocks, "
            "even when 'security' is included -- only a WEAKENED one "
            "(true -> false) does."
        ),
    ),
):
    """
    Compares a service's schema contract between two Git revisions --
    added/removed/changed variables, classified as breaking, security-
    sensitive, or informational. With no arguments, compares HEAD against
    your current working tree (uncommitted changes, staged or not).

    Exits non-zero when any change falls in a --fail-on category (default:
    breaking, security, requires_review) -- see 'envshield schema diff
    --help' for the exact rule on secret-classification changes.
    """
    if (rev_a is None) != (rev_b is None):
        message = (
            "pass both revisions, or neither -- 'envshield schema diff' alone "
            "compares HEAD against your current working tree."
        )
        if json_output:
            print(
                json.dumps(
                    {
                        "has_breaking_changes": False,
                        "has_blocking_changes": False,
                        "error": message,
                    },
                    indent=2,
                )
            )
        else:
            console.print(f"[bold red]Error:[/bold red] {message}")
        raise typer.Exit(code=1)

    fail_on_categories = frozenset(c.strip() for c in fail_on.split(",") if c.strip())
    unknown_categories = fail_on_categories - contract_diff.CATEGORIES
    if unknown_categories:
        message = (
            f"unknown --fail-on categor{'y' if len(unknown_categories) == 1 else 'ies'}: "
            f"{', '.join(sorted(unknown_categories))}. Valid: "
            f"{', '.join(sorted(contract_diff.CATEGORIES))}."
        )
        if json_output:
            print(
                json.dumps(
                    {
                        "has_breaking_changes": False,
                        "has_blocking_changes": False,
                        "error": message,
                    },
                    indent=2,
                )
            )
        else:
            console.print(f"[bold red]Error:[/bold red] {message}")
        raise typer.Exit(code=1)

    explicit_revisions = rev_a is not None
    if rev_a is None and rev_b is None:
        # HEAD is the BASELINE and the working tree is the target -- the
        # same direction as the explicit two-revision form (older on the
        # left), as 'undeclared's identical default, and as this
        # command's own documented wording. Passing these the other way
        # round reports every uncommitted change inside out: an added
        # variable comes back "removed / informational" and a required
        # addition stops counting as breaking at all, so --fail-on lets
        # it through -- a silent false-clean in the pre-commit position
        # this form exists for. `None` means the live working tree; see
        # schema_snapshot.load_schema_for_diff.
        left_revision, right_revision = "HEAD", None
        left_label, right_label = "HEAD", "working tree"
    else:
        # The mismatched-pair case already exited above -- both are
        # guaranteed non-None here.
        assert rev_a is not None and rev_b is not None
        left_revision, right_revision = rev_a, rev_b
        left_label, right_label = rev_a, rev_b

        # git show treats a bad revision as "doesn't exist" -- the exact
        # same signal a valid-but-pre-adoption revision produces (see the
        # left_revision-missing-schema handling below). Without this
        # upfront check, a typo'd revision and "this revision predates
        # EnvShield" would be indistinguishable; explicit, mirrors
        # 'undeclared's identical check (CLAUDE.md's "prefer explicit
        # errors over silent assumptions").
        for candidate in (rev_a, rev_b):
            if not git_utils.revision_exists(candidate):
                message = f"revision '{candidate}' does not resolve to a commit."
                if json_output:
                    print(
                        json.dumps(
                            {
                                "has_breaking_changes": False,
                                "has_blocking_changes": False,
                                "error": message,
                            },
                            indent=2,
                        )
                    )
                else:
                    console.print(f"[bold red]Error:[/bold red] {message}")
                raise typer.Exit(code=1)

    try:
        targets = service_manager.resolve_targets(
            service, invocation_dir=INVOCATION_DIR
        )
    except EnvShieldException as e:
        if json_output:
            print(
                json.dumps(
                    {
                        "has_breaking_changes": False,
                        "has_blocking_changes": False,
                        "error": str(e),
                    },
                    indent=2,
                )
            )
        else:
            console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)

    had_error = False
    any_breaking = False
    any_blocking = False
    results = []

    for target in targets:
        if not json_output:
            _print_service_header(targets, target)
        left_predates_adoption = False
        try:
            try:
                schema_left = schema_snapshot.load_schema_for_diff(
                    target, left_revision
                )
            except SchemaNotFoundError:
                # Only for the explicit two-revision form's OLDER side: a
                # revision that genuinely doesn't resolve was already
                # rejected above, so a missing schema/envshield.yml/service
                # here means this revision predates EnvShield adopting this
                # service at all -- an adoption-boundary diff, not an
                # error. Treated as an empty contract (diff_schemas already
                # supports this input; every declared variable on the right
                # side reports as "added", which is the correct, reviewable
                # answer to "what does the very first contract declare?").
                # The NEWER side (schema_right, below) is never given this
                # leniency: a target revision with no contract at all is
                # far more likely a mistake (e.g. arguments swapped) than
                # an intentional query, so it still raises.
                if not explicit_revisions:
                    raise
                schema_left = {}
                left_predates_adoption = True
            schema_right = schema_snapshot.load_schema_for_diff(target, right_revision)
            result = contract_diff.diff_schemas(schema_left, schema_right)
        except EnvShieldException as e:
            had_error = True
            if json_output:
                results.append({"service": target, "error": str(e)})
            else:
                console.print(f"[bold red]Error:[/bold red] {e}")
            continue

        if result.has_breaking_changes:
            any_breaking = True
        if result.has_blocking_changes(fail_on_categories):
            any_blocking = True

        if json_output:
            entry = result.to_dict(fail_on_categories)
            entry["service"] = target
            entry["revision_a"] = left_label
            entry["revision_b"] = right_label
            entry["revision_a_predates_adoption"] = left_predates_adoption
            results.append(entry)
        else:
            if left_predates_adoption:
                console.print(
                    f"[dim]ℹ️  No EnvShield contract existed for '{target}' at "
                    f"'{left_label}' -- every variable below is shown as added, "
                    "against an implicit empty starting contract.[/dim]"
                )
            _render_contract_diff_table(
                result,
                left_label,
                right_label,
                fail_on_categories,
                service=target,
                multi_service=len(targets) > 1,
            )

    if json_output:
        print(
            json.dumps(
                {
                    "has_breaking_changes": any_breaking,
                    "has_blocking_changes": any_blocking,
                    "results": results,
                },
                indent=2,
            )
        )

    if had_error or any_blocking:
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
        metavar="OUTPUT_FILE",
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

        # For Python, a same-named package directory sitting right next to
        # the output file is a real hazard, not a mere overwrite: CPython
        # resolves a plain module over a same-named package in the same
        # directory, so writing 'config.py' next to an existing 'config/'
        # package would silently make 'import config' resolve to the
        # freshly generated file instead -- breaking every 'from
        # config.whatever import ...' in the real app, with no error at
        # write time to warn about it. --force is about overwriting the
        # output file itself, not about this -- an explicit different
        # output path is the only real fix, so this isn't overridable.
        if resolved_lang == "python":
            module_dir = os.path.dirname(resolved_output) or "."
            module_stem = os.path.splitext(os.path.basename(resolved_output))[0]
            colliding_package = os.path.join(module_dir, module_stem)
            if os.path.isdir(colliding_package):
                console.print(
                    f"[bold red]Error:[/bold red] A directory '{colliding_package}/' "
                    f"already exists here. Writing '{resolved_output}' would shadow it "
                    f"as a Python import -- 'import {module_stem}' would resolve to the "
                    f"generated file instead of your package, breaking anything that "
                    f"does 'from {module_stem}.something import ...'. Pass a different "
                    f"output path instead, e.g. 'envshield generate "
                    f"{module_stem}_settings.py'."
                )
                raise typer.Exit(code=1)

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
        metavar="[PATHS]...",
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
    """Scans files for hardcoded secrets and reports every currently-undeclared environment-variable read."""
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
            # Incomplete coverage is fatal here unconditionally -- '--json'
            # output is inherently a machine/automation signal, and a
            # consumer that only checks the exit code (the standard
            # integration pattern -- see BL-004) must never be told this
            # scan succeeded when eligible content was actually skipped.
            if not result["clean"] or not result["complete"]:
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


def _install_hooks(yes: bool = False) -> None:
    """
    `yes` (this command's own `--yes`) also has to reach the *second*
    confirmation buried inside these scanner calls -- overwriting a
    pre-existing hook EnvShield didn't install itself. That one has no
    TTY guard of its own; it relied on the caller passing
    `non_interactive=True`, which nothing here ever did. Real
    consequence: '--yes' at a real terminal got an unexpected extra
    prompt anyway, and '--yes' with no TTY hit questionary's raw,
    undefined no-input behavior instead of the safe "warn and skip"
    path these functions already implement for exactly this case.
    """
    scanner.install_pre_commit_hook(non_interactive=yes)
    scanner.install_post_merge_hook(non_interactive=yes)


def _confirm_hook_action(prompt: str, yes: bool) -> bool:
    """
    Shared confirmation gate for 'hook install'/'hook remove' (and the
    legacy 'install-hook' alias) -- these write to or delete files outside
    the project (.git/hooks/...), so they ask first, the same as
    doctor --fix already does for the identical underlying operations.
    `--yes` skips the prompt for scripting; with no TTY to answer on and
    no --yes, this declines safely rather than blocking on an unanswerable
    prompt -- the same rule every other prompt in this CLI already follows.
    """
    if yes:
        return True
    if not hooks_manager._is_interactive():
        console.print(
            "[yellow]No terminal to confirm on -- pass --yes to run non-interactively.[/yellow]"
        )
        return False
    return bool(questionary.confirm(prompt, default=True).ask())


@app.command("install-hook")
def install_hook(
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the confirmation prompt."
    ),
):
    """Installs Git hooks: pre-commit (scan for secrets) and post-merge (check env config after pull). Same as 'hook install'."""
    if not _confirm_hook_action(
        "Install git hooks? (pre-commit: secret scanning, post-merge: config change detection)",
        yes,
    ):
        console.print("[yellow]Cancelled.[/yellow]")
        raise typer.Exit()
    try:
        _install_hooks(yes=yes)
    except EnvShieldException as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)


@hook_app.command("install")
def hook_install(
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the confirmation prompt."
    ),
):
    """Installs Git hooks: pre-commit (scan for secrets) and post-merge (check env config after pull)."""
    if not _confirm_hook_action(
        "Install git hooks? (pre-commit: secret scanning, post-merge: config change detection)",
        yes,
    ):
        console.print("[yellow]Cancelled.[/yellow]")
        raise typer.Exit()
    try:
        _install_hooks(yes=yes)
    except EnvShieldException as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)


@hook_app.command("status")
def hook_status():
    """Shows which EnvShield Git hooks are currently installed."""
    hooks_manager.HooksManager().print_hook_status()


@hook_app.command("remove")
def hook_remove(
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the confirmation prompt."
    ),
):
    """Removes any EnvShield-installed Git hook. Leaves alone any hook EnvShield didn't install."""
    if not _confirm_hook_action("Remove EnvShield-installed git hooks?", yes):
        console.print("[yellow]Cancelled.[/yellow]")
        raise typer.Exit()
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
        ...,
        metavar="FILE",
        help="The config file to import and convert to a schema (.env, a Python config module, or a deployment manifest).",
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
    """Builds (or refreshes) a schema from an existing config file -- .env,
    a Python config module, or a deployment manifest.

    Narrower than 'init': only (re)writes one schema, from one file --
    no envshield.yml, .gitignore, or hook changes. Use this to refresh an
    already-registered service's schema from a changed config file. If
    'envshield.yml' doesn't exist yet for this project at all, use
    'envshield init' instead -- it does the whole-project setup 'import'
    intentionally skips.
    """
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

        # A re-import should only ever be able to learn about new
        # variables, never silently discard or reclassify one the schema
        # already declares -- same reasoning as 'init --force'.
        existing_schema = None
        if os.path.exists(output):
            try:
                existing_schema = config_manager.load_toml_schema(output)
            except EnvShieldException:
                existing_schema = None

        schema_content = importer.generate_schema_from_file(
            file, interactive, existing_schema=existing_schema
        )

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
        ...,
        metavar="NAME",
        help="Name for the service (used everywhere else via --service).",
    ),
    directory: str = typer.Argument(
        ..., metavar="DIRECTORY", help="The service's own directory."
    ),
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
        if not os.path.isdir(directory):
            raise EnvShieldException(
                f"Directory '{directory}' does not exist. Create it first, or "
                "check for a typo."
            )
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
                _seed_schema_from_file(import_from, schema_path, local_file)
                console.print(
                    f"[bold green]✓[/bold green] Seeded schema from [bold cyan]{import_from}[/bold cyan]"
                )
        elif not os.path.exists(schema_path):
            # 'add' only ever registers the path -- it has no source to
            # build a schema from without '--import'. Without this, the
            # checkmark above reads as "done" while every other command
            # (check/doctor/setup) would immediately fail against this
            # service with "schema not found."
            console.print(
                f"[yellow]'{schema_path}' doesn't exist yet -- '{name}' has no "
                f"schema. Run 'envshield import <file> --service {name}' to build "
                f"one from a real config file, or create '{schema_path}' by "
                "hand.[/yellow]"
            )
    except EnvShieldException as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)


@service_app.command("remove")
def service_remove(
    name: str = typer.Argument(
        ..., metavar="NAME", help="Name of the service to de-register."
    ),
):
    """
    De-registers one service from envshield.yml, and drops it from any
    deployment manifest's container mapping. Never deletes the service's
    own files (schema, local env file, etc.) -- only the registration.
    """
    # Captured before removal -- these paths become unresolvable the
    # moment the service is gone, but they're exactly what a developer
    # needs next if they actually want the leftover files gone too, not
    # just the registration.
    try:
        schema_path = config_manager.get_service_schema_path(name)
        local_file = config_manager.get_env_paths(service_name=name)["local_file"]
    except EnvShieldException:
        schema_path = None
        local_file = None

    try:
        config_manager.remove_service(name)
    except EnvShieldException as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)
    console.print(
        f"[bold green]✓[/bold green] Removed service [bold cyan]{name}[/bold cyan] from envshield.yml."
    )

    leftover = [p for p in (schema_path, local_file) if p and os.path.exists(p)]
    if leftover:
        console.print(
            f"[dim]Its files are untouched: {', '.join(leftover)}. Delete them "
            "by hand if you don't want them anymore.[/dim]"
        )

    if not config_manager.get_services():
        console.print(
            "\n[bold cyan]Next step:[/bold cyan]\n"
            "  envshield init               # Single service\n"
            "  envshield service add|discover  # Multi-service"
        )


@service_app.command("discover")
def service_discover(
    root: str = typer.Argument(
        ".", metavar="ROOT", help="Directory to scan for service-like subdirectories."
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
            _seed_schema_from_file(config_file, schema_path, c["local_file"])
            console.print(f"    seeded schema from [dim]{config_file}[/dim]")

    console.print(
        f"\n[bold green]✨ Added {registered_count} service(s) to envshield.yml.[/bold green]"
    )

    # Offer to install git hooks
    hm = hooks_manager.HooksManager()
    hm.install_hooks_if_needed(auto=True, force=False)

    console.print("\n[bold cyan]Next step:[/bold cyan]")
    console.print("  envshield setup    # Configure your local environment")
