# Working with the team's secret manager (V3-3, #22)

Status: **design approved by the maintainer, 2026-10-30. `check` half
implemented (2026-10-31): `secrets:`, `source =`, the approval,
`secrets allow`/`revoke`, the probe, `check --with-secrets` and `doctor`.
`setup` is next.** Replaces the
original V3-3 plan (copy values from 1Password into `.env`), which
amendment A2 returned to planning. Not implemented yet.

## The decision

EnvShield works with whatever secret manager a team already uses
(Bitwarden, 1Password, Doppler, Infisical, Vault, SOPS, dotenvx, cloud
stores) without fetching from any of them. It checks what they supply
against the contract.

Every one of these tools already has a command that starts a process with
the secrets set as environment variables:

| Secret manager | Run command |
|---|---|
| Bitwarden Secrets Manager | `bws run --project-id <id> --` |
| 1Password | `op run --env-file=<file> --`, or a mounted `.env` (Environments) |
| Doppler | `doppler run --` |
| Infisical | `infisical run --env=<env> --` |
| HashiCorp Vault | `envconsul -secret=<path>`, Vault Agent's process supervisor |
| SOPS | `sops exec-env <file> '<cmd>'` |
| dotenvx | `dotenvx run --` |
| Google Cloud / AWS | `berglas exec --`, `chamber exec <service> --` |

So the secret manager runs EnvShield, the same way it runs the app.
EnvShield reads what was injected, checks it against the schema, and
never stores, writes, logs or prints a value. It never sees the secret
manager's credentials and has no code for any provider.

**Not building:** provider plugins or API clients, fetching values,
copying secrets into `.env` or any file, launching the application.
Teller, fnox and Varlock already inject secrets from many providers;
none of them checks what's injected against a contract across services,
environments and deployment manifests, which is what EnvShield adds.

## Commands per stage

With Bitwarden as the example; any run command from the table works the
same way.

| Stage | Command | What it does |
|---|---|---|
| Onboarding | `envshield setup` | With an approved secrets command (below), runs it and reports vault-supplied variables READY / NEEDS ACCESS; asks only for the rest; never writes a vault value to `.env` |
| Development | `envshield check` | Fast, no vault: vault-supplied variables absent locally are COULD NOT VERIFY (`secret_manager`), accepted by default in `local` |
| Development, with the vault | `envshield check --with-secrets` | Runs the approved secrets command; vault values are validated in a child process |
| Hooks, agent hook | unchanged | They check code and the contract, never the vault |
| CI, code | `envshield check --since origin/main --code-only` | Unchanged |
| CI, vault | `bws run --project-id $STAGING -- envshield check --runtime --env staging` | Does the staging vault satisfy the contract? Explicit prefix: CI owns its credentials |
| Deployment | entrypoint: `envshield check --runtime --env production` before `exec <app>` | Unchanged (V5-1); the platform or its run command supplies the secrets |
| No secret manager | `envshield setup`, `envshield check` | Exactly as today |
| Wrapped by hand | `bws run -- envshield setup` / `bws run -- envshield check` | Same results without configuring anything: injected values are read directly |

## Which variables come from the secret manager

- A project opts in with a `secrets:` section in `envshield.yml`. Without
  it, nothing changes: secrets are asked for and written as today.
- With it, every `secret = true` variable is **vault-supplied** by
  default. A schema field overrides it per variable:
  `source = "local"` (a personal value each developer sets, such as a
  personal access token) or `source = "secret-manager"` (a non-secret the
  vault supplies too).

```yaml
# envshield.yml
secrets:
  run: "bws run --project-id 1234 --"   # the command prefix; optional
```

`secrets.run` is the prefix the team uses. It appears in messages
("`DATABASE_URL` comes from your secret manager: run
`bws run --project-id 1234 -- envshield setup`") and is what `setup` and
`check --with-secrets` run once a developer approves it.

## Running the secrets command: allow once, per developer

`envshield.yml` is committed and can be changed by a pull request, and
untrusted configuration must never become executed code (AGENTS.md §4).
So EnvShield runs `secrets.run` only after the developer approves that
exact command, the way `direnv allow` works:

```
$ envshield setup
This project gets its secrets with:  bws run --project-id 1234 --
Allow EnvShield to run this command? It runs again without asking until it changes. [y/N]
```

- The approval is stored per developer, outside the repository
  (`$XDG_CONFIG_HOME/envshield/allowed-commands.json`, 0600), keyed by the
  project's real path and a SHA-256 of the exact command string. It holds
  no value and no credential.
- A changed command (a pull request edits it) is not approved: EnvShield
  asks again and shows the new command.
- Without a terminal, EnvShield never runs it, approved or not. It says
  which command to wrap EnvShield in instead. CI and deploys use the
  explicit prefix.
- `envshield secrets allow` / `envshield secrets revoke` manage the
  approval without running anything; `envshield doctor` reports whether
  the command is approved and whether its first word is on `PATH`, and
  never runs it.

## How a value is checked without EnvShield holding it

`setup` and `check --with-secrets` don't read the vault's output. They
start the approved command with EnvShield itself as the child:

```
<secrets.run> <python> -m envshield secrets probe --service S --env E
```

The child reads its own environment, validates each vault-supplied
variable against the contract, and prints one JSON line of name-only
statuses (`present`, `missing`, `invalid` with a value-free reason) on
stdout. The parent reads only that line. The values exist only in the
child, as they would in the application. The secret manager's own output
is classified in memory (exit code, whether the child ran) and never
printed; on failure EnvShield shows the exit code and suggests running
the command by hand. A timeout (default 60 s) stops a hung provider.

## Outcomes

| Situation | `setup` | `check` (local) | `check --runtime` |
|---|---|---|---|
| Supplied by the secret manager, valid | READY "from your secret manager" | VALID (with `--with-secrets`, or wrapped by hand) | VALID |
| Supplied, invalid | SET YOURSELF "your secret manager's value is invalid (…)" | INVALID | INVALID |
| Not supplied (no access, or not in the vault) | NEEDS ACCESS "not supplied by your secret manager: you may lack access, or it hasn't been added" | without the vault: COULD NOT VERIFY `secret_manager`; with it: INVALID `missing` | INVALID `missing` |
| The command failed (not signed in, CLI missing) | NEEDS ACCESS "couldn't run your secrets command (exit N)" for every vault variable | ERROR, naming the command and exit code | n/a |
| Not approved / no terminal | NEEDS ACCESS with the command to wrap EnvShield in | COULD NOT VERIFY `secret_manager` | n/a |
| A personal value (`source = "local"`) | as today: asked for, written to `.env` | as today | as today |

EnvShield can't tell "no access" from "not in the vault": both mean the
variable wasn't injected. The text says both; telling them apart would
need provider-specific code.

`secret_manager` is a new COULD NOT VERIFY reason. It is accepted by
default in `local` (the daily `check` stays fast and passes when
everything else is valid, with the lines still shown), and never accepted
by default elsewhere: `--runtime` and CI need the values.

## Fixed alongside (found while testing today's commands)

- **`BL-213` (#103), fixed:** a value that is a reference or an encrypted
  value was reported VALID: `op://…`, `sm://…` for a string or URL field;
  `vault:…`, dotenvx `encrypted:…` and SOPS `ENC[…]` for a string field
  (a URL field reported them INVALID instead). EnvShield never saw the real
  value. They are now COULD NOT VERIFY `external_reference`, naming the
  form ("a 1Password reference"), in every source.
- **`BL-214` (#104), fixed:** `check` and `doctor` blocked forever on a `.env` that is a
  named pipe nobody is serving: a 1Password Environments mount while
  1Password is locked or not running. A pipe is read with a timeout
  (default 10 s); unserved, its variables are COULD NOT VERIFY
  `value_not_inspected` ("the mounted file wasn't served; is the secret
  manager unlocked?"). A served pipe is read once and validated (it works
  today). Nothing ever writes to a pipe (already true).

Both are independent of the rest and are their own issues.

## Security review (the plan's provider threat rows)

- **Committed command execution:** runs only after per-developer approval
  of the exact command, never without a terminal.
- **Confused deputy:** the child is EnvShield's own code; the parent gets
  statuses, never values.
- **Leaks:** no value in any output or JSON; provider stderr is never
  printed; canary tests for every command and output.
- **Credentials:** never read, stored or passed. The secret manager
  authenticates however it is configured (session, desktop app, token).
- **Persistence:** no value is written anywhere; the approval file holds
  a path and a hash.
- **Hung provider:** a timeout for the secrets command and for a mounted
  pipe.

## Tests

Fake run commands (scripts that set variables and `exec` their
arguments) stand in for every provider, as in the edge-case run recorded
on #22 (34 cases of today's behavior pass). New tests must cover:

- every row of the outcomes table, for `setup`, `check`,
  `check --with-secrets` and `check --runtime`;
- approval: first use asks; approved runs without asking; a changed
  command asks again; declining runs nothing; no terminal never runs it;
  `secrets revoke`;
- the command failing, timing out, not existing, and printing a canary to
  its own stderr (never shown);
- the probe: values never reach the parent process (checked by a canary
  in the parent's memory-visible output and JSON);
- `source = "local"` and `source = "secret-manager"` overrides;
- a project without `secrets:` behaving exactly as today;
- `BL-213` (#103) for every listed form, and `BL-214` (#104) for an unserved pipe, a
  served pipe, and a pipe that stops mid-read;
- scenarios 2, 7 and 10 from the plan, rewritten for this design:
  (2) a fake command supplies everything: `setup` reports READY, `.env`
  holds no vault value, `check --with-secrets` is VALID, nothing leaks;
  (7) it withholds one variable: NEEDS ACCESS for it, the rest READY,
  exit 1; (10) a commit changes `secrets.run`: `setup` asks again before
  running it, and without a terminal never runs it;
- a manual run with a real secret manager on one Kriyonix project,
  recorded on #22.

## Edge cases already checked (2026-10-30, today's code)

With a fake run command: valid, invalid, empty, overriding a `.env`
value, overriding a non-secret, an undeclared extra secret, non-POSIX
names, a multi-line value, a 100 KB value, a failing command; `--runtime`
per environment, `--project`, multi-service `--service` and
`ENVSHIELD_SERVICE`, never reading `.env`; the code-only CI check; the
deploy line starting the app only on a pass and passing on its exit code;
a served pipe. All pass, with no value in any output. Findings: the two
gaps this design closes (`--process-env` easy to forget; `setup` ignores
injected values), `BL-213` (#103) and `BL-214` (#104).
