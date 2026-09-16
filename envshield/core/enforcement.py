"""
Enforcement layer for EnvShield secret scanning.

Separates detection/classification (scanner) from policy/UX decisions:
- Scanner: "What did we find?"
- Classifier: "What kind of candidate is this?"
- Enforcement: "What should happen because of this classification?"
- UX: "How do we communicate that decision?"

M7: Git Hook Enforcement & Developer UX
"""

import sys
from typing import Any, Dict, List

from rich.console import Console

console = Console()


def _is_interactive() -> bool:
    """
    Whether there's a real terminal to prompt on.

    Reuses the same sys.stdin.isatty() check that hooks_manager uses,
    maintaining consistent interactive detection across EnvShield.
    """
    return sys.stdin.isatty()


def _group_findings_by_classification(findings: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """
    Groups findings by classification level for display.

    Returns:
        Dict with keys: "high_confidence", "ambiguous", "unclassified"
        Each maps to a list of findings in that category.
    """
    high_confidence = []
    ambiguous = []
    unclassified = []

    for finding in findings:
        classification = finding.get("classification")
        if classification == "likely_secret":
            high_confidence.append(finding)
        elif classification == "ambiguous":
            ambiguous.append(finding)
        else:
            # Findings without classification (e.g., from detectors where
            # classifier doesn't apply) default to ambiguous policy
            unclassified.append(finding)

    return {
        "high_confidence": high_confidence,
        "ambiguous": ambiguous,
        "unclassified": unclassified,
    }


def _display_enforcement_warning(findings: List[Dict[str, Any]]) -> None:
    """
    Displays the enforcement warning with grouped findings.

    SECURITY: Never displays raw secret values - only safe metadata
    (file path, line number, secret type).
    """
    grouped = _group_findings_by_classification(findings)

    total_count = len(findings)
    console.print(f"\n[bold red]⚠ EnvShield found {total_count} potential secret(s)[/bold red]\n")

    # Display high-confidence findings
    if grouped["high_confidence"]:
        console.print("[bold]HIGH CONFIDENCE[/bold]")
        for finding in grouped["high_confidence"]:
            console.print(
                f"  [cyan]{finding['file_path']}:{finding['line_num']}[/cyan]    "
                f"[magenta]{finding['secret_type']}[/magenta]"
            )
        console.print()

    # Display ambiguous findings
    if grouped["ambiguous"] or grouped["unclassified"]:
        console.print("[bold]AMBIGUOUS[/bold]")
        for finding in grouped["ambiguous"] + grouped["unclassified"]:
            console.print(
                f"  [cyan]{finding['file_path']}:{finding['line_num']}[/cyan]    "
                f"[magenta]{finding['secret_type']}[/magenta]"
            )
        console.print()

    console.print("[bold]The staged changes contain potential secrets.[/bold]\n")


def _prompt_user_decision() -> bool:
    """
    Prompts the user to abort or override the commit.

    Returns:
        True if user wants to commit anyway (after explicit confirmation)
        False if user wants to abort
    """
    console.print("[1] Abort commit")
    console.print("[2] Commit anyway\n")

    try:
        choice = console.input("Choose an option: ").strip()
    except (EOFError, KeyboardInterrupt):
        # EOF or Ctrl+C = abort
        console.print("\n[yellow]Aborted by user.[/yellow]")
        return False

    if choice != "2":
        # Anything other than "2" = abort
        return False

    # User chose "2" - require explicit confirmation
    console.print("\n[bold yellow]⚠ You are about to commit potentially sensitive data.[/bold yellow]")

    try:
        confirmation = console.input("Type [bold]COMMIT ANYWAY[/bold] to confirm: ").strip()
    except (EOFError, KeyboardInterrupt):
        console.print("\n[yellow]Aborted by user.[/yellow]")
        return False

    if confirmation == "COMMIT ANYWAY":
        console.print("\n[yellow]Proceeding with commit...[/yellow]")
        return True
    else:
        console.print("\n[yellow]Invalid confirmation. Aborting commit.[/yellow]")
        return False


def enforce_findings(
    secret_findings: List[Dict[str, Any]],
    undeclared_findings: List[Dict[str, Any]],
    interactive: bool,
) -> bool:
    """
    Enforcement layer: decides whether to allow the operation based on findings.

    Policy (M7 Phase 2):
    - No findings: Allow
    - LIKELY_CODE: Never reaches enforcement (already suppressed)
    - AMBIGUOUS: Block (existing policy)
    - LIKELY_SECRET: Block, with interactive override option if TTY available

    Args:
        secret_findings: List of secret findings (may include classification metadata)
        undeclared_findings: List of undeclared variable findings
        interactive: Whether interactive prompting is available (TTY)

    Returns:
        True if operation should be allowed (clean or explicitly overridden)
        False if operation should be blocked
    """
    # No findings = allow
    if not secret_findings and not undeclared_findings:
        return True

    # Undeclared variables always block (existing policy, unchanged in M7)
    # These are configuration contract violations, not candidates for override
    if undeclared_findings:
        return False

    # At this point: only secret findings exist
    # Check if any are high-confidence (LIKELY_SECRET)
    grouped = _group_findings_by_classification(secret_findings)
    has_high_confidence = bool(grouped["high_confidence"])

    # Non-interactive: always block if any findings exist
    if not interactive:
        return False

    # Interactive + no high-confidence findings: block (existing AMBIGUOUS policy)
    # These are uncertain findings - current policy doesn't offer override
    if not has_high_confidence:
        return False

    # Interactive + high-confidence findings: offer explicit override
    _display_enforcement_warning(secret_findings)
    return _prompt_user_decision()
