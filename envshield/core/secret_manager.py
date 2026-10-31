# envshield/core/secret_manager.py
"""
Working with the team's secret manager through its own run command (V3-3,
#22; docs/architecture/secret-managers.md).

EnvShield never fetches, stores or copies a value. A project names the
command its secret manager uses to start a process with the secrets set
(`secrets.run` in envshield.yml: `bws run --project-id 1234 --`,
`op run --env-file=.env --`, ...). With a developer's one-time approval of
that exact command, `check --with-secrets` runs it with EnvShield itself as
the child (`secrets probe`). The child validates its own environment and
prints name-only statuses; the parent reads only those, so a value never
reaches it. The command's own output is never shown.

Which variables come from the secret manager: once `secrets:` exists, every
`secret = true` variable, unless the field says `source = "local"`; and any
field with `source = "secret-manager"`.
"""

import hashlib
import json
import os
import secrets as _random
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional

from ..config import manager as config_manager
from ..parsers._base import Concrete, FromSecretManager, Referenced, read_value
from . import schema_types
from .exceptions import ConfigParseError

SOURCE_LOCAL = "local"
SOURCE_SECRET_MANAGER = "secret-manager"
SOURCES = (SOURCE_LOCAL, SOURCE_SECRET_MANAGER)

PROBE_MARKER = "ENVSHIELD-PROBE"
# Seconds the secrets command may take, sign-in included.
RUN_TIMEOUT = 60.0

_SECRETS_KEYS = {"run"}


# --- Configuration ----------------------------------------------------------


@dataclass(frozen=True)
class SecretsConfig:
    """envshield.yml's `secrets:` section."""

    run: Optional[str] = None

    @property
    def argv(self) -> List[str]:
        return shlex.split(self.run) if self.run else []


def _invalid(problem: str) -> ConfigParseError:
    return ConfigParseError(config_manager.CONFIG_FILE_NAME, f"'secrets': {problem}")


def get_config() -> Optional[SecretsConfig]:
    """The project's `secrets:` section, or None when there isn't one.
    Raises ConfigParseError for an invalid one."""
    config = config_manager.load_config()
    if "secrets" not in config:
        return None
    section = config["secrets"]
    if section is None:
        return SecretsConfig()
    if not isinstance(section, dict):
        raise _invalid('must be a table, like secrets: {run: "op run --"}')
    unknown = sorted(set(section) - _SECRETS_KEYS)
    if unknown:
        raise _invalid(f"unknown key {unknown[0]!r} (known: run)")
    run = section.get("run")
    if run is None:
        return SecretsConfig()
    if not isinstance(run, str) or not run.strip():
        raise _invalid("'run' must be the command prefix, like \"bws run --\"")
    # The command is shown to the developer before they approve it: a
    # control character could make it display as something else.
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in run):
        raise _invalid("'run' must not contain control characters")
    try:
        if not shlex.split(run):
            raise ValueError("empty")
    except ValueError as e:
        raise _invalid(f"'run' isn't a valid command ({e})")
    return SecretsConfig(run=run.strip())


def is_vault_supplied(field_schema: Mapping[str, Any], enabled: bool) -> bool:
    """Whether a variable comes from the secret manager."""
    source = field_schema.get("source")
    if source == SOURCE_SECRET_MANAGER:
        return True
    if source == SOURCE_LOCAL:
        return False
    return enabled and bool(field_schema.get("secret", False))


def vault_names(fields: Mapping[str, Mapping[str, Any]], enabled: bool) -> List[str]:
    return sorted(n for n, f in fields.items() if is_vault_supplied(f, enabled))


# --- Approval (allow once, per developer) ------------------------------------


def _approvals_path() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
        os.path.expanduser("~"), ".config"
    )
    return os.path.join(base, "envshield", "allowed-commands.json")


def _approval_key(project_root: str, command: str) -> Dict[str, str]:
    return {
        "project": os.path.realpath(project_root),
        "sha256": hashlib.sha256(command.encode("utf-8")).hexdigest(),
    }


def _read_approvals() -> List[Dict[str, str]]:
    try:
        with open(_approvals_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    entries = data.get("approved") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return []
    return [
        e
        for e in entries
        if isinstance(e, dict)
        and isinstance(e.get("project"), str)
        and isinstance(e.get("sha256"), str)
    ]


def _write_approvals(entries: List[Dict[str, str]]) -> None:
    path = _approvals_path()
    directory = os.path.dirname(path)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".allowed-commands.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "approved": entries}, f, indent=2)
            f.write("\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def is_approved(project_root: str, command: str) -> bool:
    return _approval_key(project_root, command) in _read_approvals()


def approve(project_root: str, command: str) -> None:
    key = _approval_key(project_root, command)
    entries = [e for e in _read_approvals() if e.get("project") != key["project"]]
    _write_approvals(entries + [key])


def revoke(project_root: str) -> bool:
    """Forgets this project's approval. True if there was one."""
    project = os.path.realpath(project_root)
    entries = _read_approvals()
    kept = [e for e in entries if e.get("project") != project]
    if len(kept) == len(entries):
        return False
    _write_approvals(kept)
    return True


def stdin_is_terminal() -> bool:
    """Whether a person is there to approve and to sign in. Tests replace it."""
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def command_on_path(config: SecretsConfig) -> bool:
    argv = config.argv
    return bool(argv) and shutil.which(argv[0]) is not None


# --- The probe (the child) -----------------------------------------------------

STATUSES = ("valid", "invalid", "blank", "missing", "referenced")


def probe_status(
    field_schema: Mapping[str, Any], text: Optional[str]
) -> Dict[str, Any]:
    """One variable's name-only status in the child's environment. The
    detail is a constraint description or a reference's form, never a value."""
    if text is None:
        return {"status": "missing"}
    state = read_value(text)
    if isinstance(state, Referenced):
        return {"status": "referenced", "detail": state.form}
    assert isinstance(state, Concrete)
    error = schema_types.validate_value(state.value, dict(field_schema))
    if state.value == "":
        return {"status": "blank", "detail": error}
    if error:
        return {"status": "invalid", "detail": error}
    return {"status": "valid"}


def probe(services: Iterable[str], environ: Mapping[str, str]) -> Dict[str, Any]:
    """`secrets probe`'s result: each service's vault-supplied variables'
    statuses in `environ`."""
    enabled = get_config() is not None
    result: Dict[str, Any] = {}
    for service in services:
        fields = config_manager.load_schema(service_name=service)
        result[service] = {
            name: probe_status(fields[name], environ.get(name))
            for name in vault_names(fields, enabled)
        }
    return {"services": result}


def probe_line(nonce: str, payload: Mapping[str, Any]) -> str:
    return f"{PROBE_MARKER} {nonce} {json.dumps(payload, sort_keys=True)}"


# --- Running the command (the parent) ----------------------------------------


@dataclass
class ProbeOutcome:
    """What running the secrets command gave: statuses per service and
    variable, or why there are none (value-free)."""

    statuses: Optional[Dict[str, Dict[str, Dict[str, Any]]]] = None
    failure: Optional[str] = None


def _parse_probe(stdout: str, nonce: str, services: List[str]):
    prefix = f"{PROBE_MARKER} {nonce} "
    for line in stdout.splitlines():
        if not line.startswith(prefix):
            continue
        try:
            payload = json.loads(line[len(prefix) :])
        except ValueError:
            return None
        if isinstance(payload, dict) and isinstance(payload.get("error"), str):
            return payload["error"]
        found = payload.get("services") if isinstance(payload, dict) else None
        if not isinstance(found, dict):
            return None
        statuses: Dict[str, Dict[str, Dict[str, Any]]] = {}
        for service in services:
            entries = found.get(service)
            if not isinstance(entries, dict):
                return None
            statuses[service] = {}
            for name, entry in entries.items():
                if not (
                    isinstance(entry, dict)
                    and entry.get("status") in STATUSES
                    and isinstance(entry.get("detail"), (str, type(None)))
                ):
                    return None
                statuses[service][name] = {
                    "status": entry["status"],
                    "detail": entry.get("detail"),
                }
        return statuses
    return None


def run_probe(
    config: SecretsConfig,
    services: List[str],
    timeout: Optional[float] = None,
) -> ProbeOutcome:
    """
    Runs `<secrets.run> <python> -m envshield secrets probe ...` in the
    project root. Its output (the secret manager's and the probe's) is read
    in memory and never printed; only the probe's status line is used.
    The caller has checked the approval and that a person is present.
    """
    timeout = RUN_TIMEOUT if timeout is None else timeout
    nonce = _random.token_hex(16)
    argv = config.argv + [
        sys.executable,
        "-m",
        "envshield",
        "secrets",
        "probe",
        "--nonce",
        nonce,
    ]
    for service in services:
        argv += ["--service", service]
    name = config.argv[0]
    try:
        completed = subprocess.run(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return ProbeOutcome(failure=f"'{name}' isn't installed or isn't on PATH")
    except PermissionError:
        return ProbeOutcome(failure=f"'{name}' can't be run (permission denied)")
    except subprocess.TimeoutExpired:
        return ProbeOutcome(failure=f"your secrets command took over {timeout:g} s")
    except OSError as e:
        return ProbeOutcome(failure=f"'{name}' couldn't start ({e.strerror})")
    stdout = completed.stdout.decode("utf-8", errors="replace")
    statuses = _parse_probe(stdout, nonce, services)
    del stdout, completed.stderr
    if isinstance(statuses, str):
        return ProbeOutcome(failure=f"EnvShield couldn't check them: {statuses}")
    if statuses is None:
        return ProbeOutcome(
            failure=(
                f"your secrets command didn't run EnvShield (exit "
                f"{completed.returncode}); run it by hand to see why"
            )
        )
    return ProbeOutcome(statuses=statuses)


# --- What check sees ---------------------------------------------------------


@dataclass
class SecretInputs:
    """
    What a check knows about vault-supplied variables, beyond the local
    file: the process environment (when EnvShield runs inside the secret
    manager's command), and a probe's statuses or why there are none.
    """

    environ: Mapping[str, str] = field(default_factory=dict)
    outcome: Optional[ProbeOutcome] = None
    # Why the probe didn't run, as the COULD NOT VERIFY detail.
    pending: Optional[str] = None


def pending_detail(
    config: Optional[SecretsConfig], reason: Optional[str] = None
) -> str:
    """The detail for a vault-supplied variable EnvShield didn't check."""
    wrapped = (
        f"'{config.run} envshield check'"
        if config is not None and config.run
        else "your secret manager's run command (e.g. 'op run -- envshield check')"
    )
    if reason:
        return f"from your secret manager, not checked ({reason}); or run {wrapped}"
    if config is not None and config.run:
        return (
            "from your secret manager; check it with 'envshield check --with-secrets'"
        )
    return f"from your secret manager; check it by running {wrapped}"


def secret_states(
    service: str,
    fields: Mapping[str, Mapping[str, Any]],
    inputs: Optional[SecretInputs],
) -> Dict[str, Any]:
    """
    The value state of each of `service`'s vault-supplied variables, to lay
    over its local file. A value in the process environment is read (the
    secret manager injected it); otherwise the probe's status; otherwise
    FromSecretManager("unchecked"). Empty when the project has no
    `secrets:` and no field says `source = "secret-manager"`.
    """
    config = get_config()
    names = vault_names(fields, config is not None)
    if not names:
        return {}
    inputs = inputs or SecretInputs()
    outcome = inputs.outcome
    states: Dict[str, Any] = {}
    for name in names:
        if outcome is not None and outcome.failure is not None:
            states[name] = FromSecretManager("failed", outcome.failure)
        elif outcome is not None and outcome.statuses is not None:
            entry = (outcome.statuses.get(service) or {}).get(name) or {
                "status": "missing"
            }
            states[name] = FromSecretManager(entry["status"], entry.get("detail"))
        elif name in inputs.environ:
            states[name] = read_value(inputs.environ[name])
        else:
            states[name] = FromSecretManager(
                "unchecked", inputs.pending or pending_detail(config)
            )
    return states
