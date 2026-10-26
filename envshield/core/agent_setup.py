# envshield/core/agent_setup.py
"""
Agent setup (V6-3, #63; BL-192): what a coding agent needs to run EnvShield
after each change and to keep env.schema.toml current, without anyone
reading EnvShield's docs.

- guide(): the full instructions for this version ('envshield agent guide').
  The AGENTS.md section only points at it, so it can't go stale when
  commands change.
- install_instructions(): writes or refreshes EnvShield's marked section in
  AGENTS.md (and CLAUDE.md when it doesn't already import AGENTS.md),
  leaving the rest of the file alone.
- install_hook_config(): adds a PostToolUse hook that runs
  'envshield agent hook' after file edits, to Claude Code's
  .claude/settings.json or Codex's .codex/hooks.json. Other hooks and
  settings are kept.
- run_hook(): what that hook runs. Silent when the check passes; otherwise
  a short summary on stderr and exit code 2, which both Claude Code and
  Codex feed back to the model.

Every write goes through file_updater.open_for_write (BL-154). Nothing here
reads or prints a configuration value: the hook summarizes 'check --json'
findings, which are value-free.
"""

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from envshield import __version__
from envshield.config import manager as config_manager
from envshield.core import file_updater
from envshield.core.exceptions import EnvShieldException, UnsafeWriteTargetError
from envshield.utils import git_utils

SECTION_START = "<!-- envshield:agent-instructions:start -->"
SECTION_END = "<!-- envshield:agent-instructions:end -->"

HOOK_COMMAND = "envshield agent hook"
# What the hook runs after an edit: only what the code reads and how the
# contract changed, so a local secret the agent can't supply doesn't fail
# every edit (maintainer decision on #63).
POST_CHANGE_CHECK = "envshield check --since HEAD --code-only --json"
FULL_CHECK = "envshield check --json"

# Tool names each agent uses for file edits. Codex reports every edit as
# apply_patch; Edit and Write are accepted aliases.
_AGENTS: Dict[str, Tuple[str, str]] = {
    "claude": (
        os.path.join(".claude", "settings.json"),
        "Edit|MultiEdit|Write|NotebookEdit",
    ),
    "codex": (os.path.join(".codex", "hooks.json"), "apply_patch|Edit|Write"),
}
AGENT_NAMES = tuple(_AGENTS)

_HOOK_TIMEOUT_SECONDS = 60


def guide() -> str:
    """The full agent instructions for the installed version."""
    return f"""\
# EnvShield agent guide (envshield {__version__})

This project's configuration is governed by `env.schema.toml`: it declares
every environment variable the code reads, which services get it, whether
it's a secret, and in which environments it's required. EnvShield checks
the code, local files and deployment manifests against it. Its output
never contains a configuration value.

## After every change

Run:

    {POST_CHANGE_CHECK}

It reports what the code reads that the schema doesn't declare (only reads
new since HEAD fail it) and how the contract changed. If a hook runs it for
you after each edit, act on what the hook reports the same way.

After changing `env.schema.toml`, `.env*` files, a Python config module or
a deployment manifest, also run:

    {FULL_CHECK}

This also checks local values and manifests.

Exit code 0 means pass. Exit code 1 means act on the findings. The JSON has
`reports[].findings[]`, one per problem: `variable`, `service`,
`location` (file, line), `verdict`, `reason_code`, `detail`, and
`next_step` with `by` ("agent" or "human") and `text`. An `"error"` key
instead means the check couldn't run: fix that first (often a TOML mistake
in `env.schema.toml`).

## Keeping env.schema.toml current

When your code reads a new environment variable:

1. Declare it in `env.schema.toml` as a table named after the variable,
   with a real `description` and a `type` (string, int, float, bool, port,
   url, email or enum). Follow the finding's `next_step` text: when it
   names `services = [...]`, add that key.
2. If the value is a credential, token, key or password, set
   `secret = true`.
3. If it must be set in some environments, set `requiredIn` to those
   environment names (for example `["staging", "production"]`; `local` and
   the names declared under `environments:` in `envshield.yml`).
4. Never put a value in the schema for a secret: no `defaultValue`, and no
   real value in `description` or an example. A non-secret may have a
   safe `defaultValue`.
5. Run `envshield schema sync` to update `.env.example`.
6. Run the check again.

When you stop reading a variable, leave its declaration unless you're sure
no other code, service or deployment uses it; `envshield explain NAME`
shows where it is read and which manifests set it.

## What not to do

- Never invent, guess or generate a value for a configuration variable,
  and never copy one from one file to another to make a check pass.
- When a finding's `next_step.by` is "human" (for example "supply this
  secret"), tell the user what is needed and why. Don't work around it.
- Don't run `envshield setup` (it prompts on a terminal) or
  `envshield init --force` (it rebuilds the schema).
- Don't delete or weaken a declaration to make a check pass.
"""


def _instructions_section() -> str:
    return f"""\
{SECTION_START}
## Configuration (EnvShield)

Environment variables in this project are governed by `env.schema.toml`.
Before adding, removing or changing configuration, run `envshield agent guide`
and follow it. After each change, run
`{POST_CHANGE_CHECK}` and act on its findings. Never
invent a configuration value; report steps marked for a human to the user.
{SECTION_END}
"""


@dataclass
class WriteResult:
    path: str
    # "created", "updated", "unchanged", or "skipped"
    status: str
    detail: str = ""


def _read_text(path: str, root: str) -> Optional[str]:
    """
    The file's text, or None if it doesn't exist. Refuses (raises
    UnsafeWriteTargetError) a path open_for_write would refuse, before
    reading it: a symlinked AGENTS.md is never followed, even to read.
    """
    file_updater.assert_safe_write_target(path, root)
    if not os.path.exists(path):
        return None
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, encoding="utf-8") as f:
        try:
            return f.read()
        except UnicodeDecodeError as e:
            raise EnvShieldException(
                f"'{_rel(path, root)}' isn't UTF-8 text; left unchanged."
            ) from e


def _write_text(path: str, root: str, content: str) -> None:
    with file_updater.open_for_write(path, root, create_parents=True) as f:
        f.write(content)


def upsert_section(text: Optional[str]) -> str:
    """
    `text` with EnvShield's marked section added (at the end) or replaced
    in place. Everything outside the markers is kept byte for byte.
    """
    section = _instructions_section()
    if text is None or text == "":
        return section
    starts = text.count(SECTION_START)
    ends = text.count(SECTION_END)
    if starts == 0 and ends == 0:
        sep = "" if text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")
        return text + sep + section
    start = text.find(SECTION_START)
    end = text.find(SECTION_END)
    if starts != 1 or ends != 1 or end < start:
        raise EnvShieldException(
            "its EnvShield section markers are damaged (expected one "
            f"'{SECTION_START}' followed by one '{SECTION_END}'). Fix or "
            "remove them, then run 'envshield agent install' again."
        )
    after = end + len(SECTION_END)
    if text[after : after + 1] == "\n":
        after += 1
    return text[:start] + section + text[after:]


_INSTRUCTION_FILES = ("AGENTS.md", "CLAUDE.md")


def _instructions_target(path: str, root: str) -> str:
    """
    The file to edit for `path`. Projects often link CLAUDE.md and
    AGENTS.md to each other (or to .agents/AGENTS.md), so a symlink that
    resolves to an AGENTS.md or CLAUDE.md inside the project is followed:
    the resolved file is read and written directly, never through the link,
    under open_for_write's usual rules. Any other symlink is refused.
    """
    if not os.path.islink(path):
        return path
    resolved = os.path.realpath(path)
    root_real = os.path.realpath(root)
    rel = os.path.relpath(resolved, root_real)
    inside = rel != os.pardir and not rel.startswith(os.pardir + os.sep)
    if (
        inside
        and os.path.basename(resolved) in _INSTRUCTION_FILES
        and os.path.isfile(resolved)
    ):
        return os.path.join(root, rel)
    raise UnsafeWriteTargetError(
        path,
        "it is a symlink that doesn't point to an AGENTS.md or CLAUDE.md "
        "inside the project",
    )


def is_installed(root: str) -> bool:
    """Whether AGENTS.md already has EnvShield's section ('init' won't re-offer)."""
    try:
        text = _read_text(
            _instructions_target(os.path.join(root, "AGENTS.md"), root), root
        )
    except (EnvShieldException, OSError, UnicodeDecodeError):
        return False
    return text is not None and SECTION_START in text


def _imports_agents_md(text: str) -> bool:
    """A CLAUDE.md that imports AGENTS.md already gets the section."""
    return re.search(r"(^|\s)@(\./)?AGENTS\.md\b", text) is not None


def install_instructions(root: str) -> List[WriteResult]:
    """
    Writes the marked section to AGENTS.md, and to CLAUDE.md if present.
    Two names linked to one file are written once.
    """
    done: Dict[str, WriteResult] = {}
    results: List[WriteResult] = []
    for name in _INSTRUCTION_FILES:
        path = os.path.join(root, name)
        if name == "CLAUDE.md" and not os.path.lexists(path):
            continue
        try:
            target = _instructions_target(path, root)
        except EnvShieldException as e:
            raise EnvShieldException(f"Can't update '{name}': {e}") from e
        key = os.path.realpath(target)
        if key in done:
            if target != path:
                note = f"{name} links to it"
                prev = done[key]
                prev.detail = f"{prev.detail}; {note}" if prev.detail else note
            continue
        if name == "CLAUDE.md":
            text = _read_text(target, root)
            if (
                text is not None
                and _imports_agents_md(text)
                and SECTION_START not in text
            ):
                results.append(WriteResult(target, "skipped", "it imports AGENTS.md"))
                continue
        result = _upsert_file(target, root)
        if target != path:
            result.detail = f"{name} links to it"
        done[key] = result
        results.append(result)
    return results


def _upsert_file(path: str, root: str) -> WriteResult:
    try:
        text = _read_text(path, root)
        new = upsert_section(text)
    except EnvShieldException as e:
        raise EnvShieldException(f"Can't update '{_rel(path, root)}': {e}") from e
    if new == text:
        return WriteResult(path, "unchanged")
    _write_text(path, root, new)
    return WriteResult(path, "created" if text is None else "updated")


def _hook_entry(matcher: str) -> Dict[str, Any]:
    return {
        "matcher": matcher,
        "hooks": [
            {
                "type": "command",
                "command": HOOK_COMMAND,
                "timeout": _HOOK_TIMEOUT_SECONDS,
            }
        ],
    }


def _is_ours(group: Any) -> bool:
    return isinstance(group, dict) and any(
        isinstance(h, dict)
        and isinstance(h.get("command"), str)
        and h["command"].strip().startswith(HOOK_COMMAND)
        for h in group.get("hooks") or []
    )


def install_hook_config(
    agent: str, root: str, confirm: Callable[[str], bool]
) -> WriteResult:
    """
    Adds EnvShield's PostToolUse hook to `agent`'s project hook config.
    Existing settings and hooks are kept. If an EnvShield hook is already
    there but differs from this version's, it's replaced only when
    `confirm` says so. A file that isn't valid JSON is never touched.
    """
    rel_path, matcher = _AGENTS[agent]
    path = os.path.join(root, rel_path)
    text = _read_text(path, root)
    if text is None or not text.strip():
        data: Dict[str, Any] = {}
    else:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise EnvShieldException(
                f"'{rel_path}' isn't valid JSON ({e.msg}, line {e.lineno}); "
                "left unchanged."
            ) from e
    if not isinstance(data, dict):
        raise EnvShieldException(
            f"'{rel_path}' doesn't hold a JSON object; left unchanged."
        )
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise EnvShieldException(
            f"'hooks' in '{rel_path}' isn't a JSON object; left unchanged."
        )
    groups = hooks.setdefault("PostToolUse", [])
    if not isinstance(groups, list):
        raise EnvShieldException(
            f"'hooks.PostToolUse' in '{rel_path}' isn't a list; left unchanged."
        )
    wanted = _hook_entry(matcher)
    ours = [i for i, g in enumerate(groups) if _is_ours(g)]
    if ours:
        if len(ours) == 1 and groups[ours[0]] == wanted:
            return WriteResult(path, "unchanged")
        if not confirm(
            f"'{rel_path}' already has an EnvShield hook that differs from "
            "this version's. Replace it?"
        ):
            return WriteResult(path, "skipped", "kept the existing EnvShield hook")
        groups[ours[0]] = wanted
        for i in reversed(ours[1:]):
            del groups[i]
    else:
        groups.append(wanted)
    _write_text(path, root, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return WriteResult(path, "created" if text is None else "updated")


def _rel(path: str, root: str) -> str:
    return os.path.relpath(path, root)


# --- The hook itself -------------------------------------------------------


def _hook_check_args() -> List[str]:
    """
    'check --since HEAD --code-only --json', or without --since when there's
    no HEAD to compare with (not a Git repository, or no commit yet): then
    every undeclared read counts, which is right for a project with no
    history to call them old.
    """
    args = ["check", "--code-only", "--json"]
    try:
        if git_utils.get_git_root() and git_utils.revision_exists("HEAD"):
            args = ["check", "--since", "HEAD", "--code-only", "--json"]
    except Exception:
        pass
    return args


def _location(finding: Dict[str, Any]) -> str:
    loc = finding.get("location") or {}
    file = loc.get("file")
    if not file:
        return ""
    return f" at {file}:{loc['line']}" if loc.get("line") else f" at {file}"


def summarize(report: Dict[str, Any]) -> List[str]:
    """
    One line per finding that fails the check, from 'check --json'. Old
    undeclared reads (new: false) and contract changes don't fail it with
    --since and are left out. Only names, files, lines and EnvShield's own
    text: the report carries no values.
    """
    lines: List[str] = []
    for svc in report.get("reports") or []:
        for f in svc.get("findings") or []:
            if f.get("new") is False or f.get("kind") == "contract_change":
                continue
            if f.get("verdict") not in ("invalid", "error"):
                continue
            name = f.get("variable") or f.get("kind") or "problem"
            service = f.get("service") or svc.get("service")
            line = f"- {name}"
            if service:
                line += f" ({service})"
            line += f"{_location(f)}: {f.get('detail') or f.get('reason_code')}."
            step = f.get("next_step") or {}
            if step.get("text"):
                who = "a person" if step.get("by") == "human" else "you"
                line += f" Next, for {who}: {step['text']}"
            lines.append(line)
    return lines


def run_hook(project_root: Optional[str]) -> Tuple[int, str]:
    """
    (exit code, stderr text) for the PostToolUse hook. 0 and nothing when
    this isn't an EnvShield project or the check passes; 2 and a summary
    when it fails or can't run, so the agent sees it.
    """
    if not project_root:
        return 0, ""
    proc = subprocess.run(
        [sys.executable, "-m", "envshield", *_hook_check_args()],
        cwd=project_root,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
    )
    if proc.returncode == 0:
        return 0, ""
    try:
        report = json.loads(proc.stdout)
    except json.JSONDecodeError:
        report = None
    if not isinstance(report, dict):
        return 2, (
            "EnvShield couldn't check this change (exit "
            f"{proc.returncode}). Run '{POST_CHANGE_CHECK}' to see why.\n"
        )
    if report.get("error"):
        return 2, (
            f"EnvShield couldn't check this change: {report['error']}\n"
            "Fix this first; run 'envshield agent guide' for help.\n"
        )
    lines = summarize(report) or [
        f"- the check failed; run '{POST_CHANGE_CHECK}' for details."
    ]
    return 2, (
        "EnvShield: this change breaks the configuration contract "
        "(env.schema.toml).\n"
        + "\n".join(lines)
        + "\nFix these, then the check runs again on your next edit. Run "
        "'envshield agent guide' for how to declare variables. Never invent "
        "a value; tell the user about steps for a person.\n"
    )


def find_root() -> Optional[str]:
    return config_manager.find_project_root()
