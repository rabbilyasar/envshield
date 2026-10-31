# EnvShield

**Every service and every deploy gets the configuration it needs, and you find out before something breaks.**

EnvShield checks your code, your local `.env` files and your Docker Compose and Kubernetes manifests against one committed contract, `env.schema.toml`. When code starts reading a variable nobody declared, when a service or a deployment is missing one, or when the contract itself changes, EnvShield tells you: on your machine, in a Git hook, or in CI.

Free, open source (MIT), and fully local. EnvShield never sends your configuration anywhere and never prints a secret value.

[Website](https://www.envshield.dev) · [Documentation](https://docs.envshield.dev) · [GitHub](https://github.com/rabbilyasar/envshield) · [PyPI](https://pypi.org/project/envshield/)

![A coding agent adds a read of STRIPE_WEBHOOK_SECRET; envshield undeclared names the file and line; after it is declared, envshield check shows the api service's .env and its Compose container both lack it while the worker service is unaffected](https://raw.githubusercontent.com/rabbilyasar/envshield/main/docs/assets/envshield-demo.gif)

*A coding agent adds a webhook that reads `STRIPE_WEBHOOK_SECRET`. `envshield undeclared` names the file and line. Once the variable is declared for the `api` service, `envshield check` shows that `api`'s `.env` and its Compose container both lack it, while `worker` is unaffected.*

---

## What it catches

**While you develop**

- **A variable the code reads but nobody declared.** `envshield check` names every such read by file and line, and `envshield check --since REV` says which ones are new since a Git revision (your working tree, or a pull request). Python discovery is AST-based; JavaScript/TypeScript is pattern-based.
- **A local setup that no longer matches.** `envshield check` validates your `.env` against the contract: missing, blank, wrongly typed or extra variables, and code that reads something the contract doesn't declare.
- **A contract change in review.** `envshield schema diff` classifies each change between two revisions as breaking, security-relevant or informational, so a configuration change is reviewed like any other code change.
- **What a pull just changed.** After a pull that touches a service's configuration, the post-merge hook runs `doctor` for it, which names any variable your local file is now missing.

**Before you deploy**

- **A deployment that doesn't provide what its service needs.** `envshield check` validates a Docker Compose file or Kubernetes manifest against the same contract. Register the manifest with a `containers:` map and every container is checked against its own service.
- **Values EnvShield can't see** (a Kubernetes Secret reference, a Compose `${VAR}`) are reported as unverified, never as valid. In v4 they don't fail the check yet; see [Known limitations](#known-limitations).

**When someone joins**

- `envshield init` builds the contract from the configuration you already have, and `envshield setup` walks a new developer through their local `.env`, with each variable's description and masked input for secrets. Filling values from your team's secret manager is planned for v5.

**Supporting tools:** `explain` (everything EnvShield knows about one variable), `generate` (typed `pydantic-settings` or `zod` config from the contract), `scan` (hardcoded secrets before they're committed) and `doctor` (a health check of the whole setup).

### For AI coding agents

Coding agents write code that reads configuration they don't know exists. EnvShield gives them a deterministic check to run in their loop: `envshield check --since HEAD --json` returns one machine-readable, value-free list of findings for a change (invalid or missing values, new undeclared reads, contract changes), each naming the variable, file and line and saying whether an agent or a person must act, and the Git hooks catch what an agent commits. No secret value appears in EnvShield's output, so it's safe to put in front of an agent.

`envshield agent install` (which `init` offers) sets this up: a short EnvShield section in `AGENTS.md` (and `CLAUDE.md`), and a Claude Code and Codex hook that runs `envshield check --since HEAD --code-only` after each file edit and shows the agent what fails. `envshield agent guide` prints the instructions the agent follows at each stage: how to declare a new variable in `env.schema.toml` without ever writing a secret value, what to tell you when a deployment needs it, and how to help a new developer set up without touching their secrets. Claude Code also gets permission to run the guide, the check and `schema sync` without asking, once you trust the project folder; for an unattended run (`claude -p`, CI), `agent install` prints the `--allowedTools` flag that does the same.

---

## Quick Start

```bash
pipx install envshield
```

Prefer an existing Python/pip workflow instead? `pip install envshield` works the same way, as an alternative.

In an existing project:

```bash
envshield init
```

`init` looks for a real config source — a `.env`, `.env.example`, or a recognizable Python config module — and builds `env.schema.toml` from your actual variables (classifying secrets, suggesting defaults, inferring types where the shape is unambiguous). A brand-new project with nothing to read yet gets a framework-aware starting template instead.

```bash
envshield setup      # interactive wizard: fills in your local .env from the schema
envshield check      # validate your local file against the schema
```

That's the whole loop for a single-service project. Everything past this point — multiple services, schema composition, CI, deployment manifests — is opt-in.

---

## The Schema

`env.schema.toml` is plain TOML, meant to be committed to git and hand-edited. It declares *shape*, never secret *values*:

```toml
[DATABASE_URL]
description = "PostgreSQL connection string"
type = "url"
secret = true

[APP_ENV]
description = "Deployment environment"
enum = ["local", "staging", "production"]
defaultValue = "local"

[PORT]
description = "Port the API listens on"
type = "port"
defaultValue = "8000"

[DEBUG]
description = "Enable debug mode"
type = "bool"
defaultValue = "false"

[ADMIN_EMAIL]
description = "Where alerts get sent"
type = "email"

[STRIPE_SECRET_KEY]
description = "Stripe secret key"
secret = true
requiredIf = { var = "PAYMENTS_ENABLED", equals = "true" }
```

What each field actually means:

| Field | Meaning |
|---|---|
| `type` | `string` (default), `int`, `float`, `bool`, `port` (1–65535), `url`, `email`. Enforced by `check`/`doctor`/`setup`. |
| `enum` | Value must be one of this list. Implies an enum type regardless of `type`. |
| `pattern` | A regex the value must also match, e.g. `pattern = "^v\\d+\\.\\d+\\.\\d+$"`. |
| `secret` | Marks the variable sensitive — masked input in `setup`, masked in generated code. `import` auto-suggests it from the variable's name/value; it never overwrites a `secret` value you've already committed. |
| `defaultValue` | What `setup` writes automatically (and `.env.example` and generated code show). A default never makes a variable optional: it still has to be explicitly present in your local file — `check`/`doctor` name the default inline so a missing one is obvious. |
| `required` | Optional. `required = false` makes a variable optional: it may be absent or blank, but a value that is set must still be valid. `required = true` is the default. |
| `requiredIn` | Optional. A list of environments where the variable is required, e.g. `["staging", "production"]`; it's optional everywhere else (see Environments, below). |
| `description` | Shown in `setup`, copied into generated code. |

Requiredness: every variable is required unless `required = false`, `requiredIn` limits it to some environments, or `requiredIf` makes it conditional (see below). A variable sets at most one of the three. A `defaultValue` never changes whether a variable is required. Any other key, or an unknown `type`, is an error — a typo like `default = "8000"` is reported (`did you mean 'defaultValue'?`) instead of silently ignored.

### Conditional requirements (`requiredIf`)

A variable that's only relevant behind a feature flag doesn't need to be required unconditionally. `requiredIf = { var = "OTHER_VAR", equals = "some value" }` makes a field required only when that condition holds, with or without a `defaultValue`. Without `requiredIf`, a variable stays required unconditionally:

```toml
[PAYMENTS_ENABLED]
type = "bool"
defaultValue = "false"

[STRIPE_SECRET_KEY]
secret = true
requiredIf = { var = "PAYMENTS_ENABLED", equals = "true" }
```

With `PAYMENTS_ENABLED=false`, `STRIPE_SECRET_KEY` is optional. Flip the flag in any environment, and `check`/`doctor`/`setup` immediately start requiring it there.

### Composing schemas (`extends`)

A monorepo with several services usually shares a handful of variables. Factor them into a base schema and extend it — the child's own definition always wins on a conflict, no per-field merging:

```toml
# services/api/env.schema.toml
extends = "../../shared/base.schema.toml"

[DATABASE_URL]
secret = true
```

---

## Commands

`envshield --help` groups commands by stage: **Onboarding** (`init`, `setup`), **Development & deployment** (`check`, `hook`) and **Supporting** (everything else). This section walks through them in the order you'd use them: define the contract, validate against it, discover what code depends on, and review how the contract changes.

### Define

`env.schema.toml` (see [The Schema](#the-schema) above) is what you define. `envshield init` (or `envshield init --from <file>`) builds it from your real, existing configuration — see [Existing projects](#existing-projects).

### Validate

**`envshield check`** — validate local configuration, and commonly-used Docker Compose and Kubernetes deployment-manifest patterns, against the same schema (see [Known limitations](#known-limitations)). It also scans the service's own source code and fails on a variable the code reads but the schema doesn't declare (the same files the pre-commit `scan` checks). `--process-env` also counts variables set in your shell (for a name the schema declares, the shell's value wins over the file's). `--json` adds a versioned, value-free report per service.

```bash
$ envshield check
Validating .env against schema...
Invalid
  ✗ DATABASE_URL   missing (required)
  ✗ PORT           invalid value (must be a port number from 1-65535)
  ✓ LOG_LEVEL      valid

INVALID: 2 invalid, 1 valid.
```

Every variable gets one verdict: valid, invalid, could not verify, not required, or error. `check` never calls a value valid unless it read it. A manifest often only *points* at a value (a Kubernetes Secret, a Compose `${VAR}` from the deploy host), and a `.env` can hold a secret manager's reference or an encrypted value (`op://...`, SOPS `ENC[...]`, dotenvx `encrypted:...`), so those variables are "could not verify", and `check` exits 1. When that's expected, accept the specific reason: `envshield check k8s/prod.yaml --accept-unverified=external_reference` exits 0, still lists every unverified variable, and ends with `PASSED WITH UNVERIFIED VALUES`. The reasons you can accept are `host_forward`, `external_reference`, `value_not_inspected` and `secret_manager`. In `--json`, `success` stays `false` for an accepted run.

**Secret managers.** EnvShield works with whatever secret manager the team uses (Bitwarden, 1Password, Doppler, Infisical, Vault, SOPS, dotenvx, ...) without fetching from it. Name the command that starts a process with your secrets, and every `secret = true` variable is expected from it (`source = "local"` on a field opts it out; `source = "secret-manager"` opts a non-secret in):

```yaml
# envshield.yml
secrets:
  run: "bws run --project-id 1234 --"
```

A plain `check` doesn't contact the vault: those variables are "could not verify" (`secret_manager`), accepted by default locally, with a line telling you how to check them. `envshield check --with-secrets` runs that command (after you approve it once; a changed command asks again, and it never runs without a terminal) with EnvShield as the child process, which checks the injected values and reports only names and verdicts back: values never reach the output, and the command's own output is never shown. Running EnvShield inside the command by hand (`bws run -- envshield check`) reads the injected values directly. `envshield secrets allow` / `revoke` manage the approval; `doctor` reports it. In CI and at deploy, wrap the check yourself: `bws run -- envshield check --runtime --env staging`.

**Environments.** A variable can be optional on a laptop and required in production. Declare the environments in `envshield.yml` and say where each variable is required:

```yaml
# envshield.yml
environments: [staging, production]   # `local` is built in
manifests:
  - file: k8s/production.yaml
    environment: production
    containers: {api: api}
```

```toml
# env.schema.toml
[GIT_SHA]
description = "Set by the deploy script; never in .env."
requiredIn = ["staging", "production"]
```

Plain `envshield check` judges your local file (and untagged manifests) by `local`'s requirements, so `GIT_SHA` isn't needed there. `envshield explain GIT_SHA` lists where it's required, `schema diff` says which environment a requiredness change affects (a variable added with `requiredIn = ["production"]` is breaking for production only), and `generate` makes it optional in the generated loader, since that loader runs in every environment. `envshield check --env production` (or `ENVSHIELD_ENV=production`) checks the manifests tagged `production` against production's requirements. `envshield check deploy/production.env --env production` checks one env file the same way. A variable sets at most one of `required`, `requiredIn` or `requiredIf`.

**Deploy-time check.** `envshield check --runtime --env production` checks the environment it runs in, and nothing else: no `.env` or manifest is read or needed, only the names the service's schema declares are read, and values are never printed. A `defaultValue` is never evidence: a required variable that's absent is invalid. It exits 0 only when every variable required in `production` is set and valid, and 1 otherwise. It reads `envshield.yml` and the schema from the working directory (or `--project DIR`). With several services, name one with `--service` or `ENVSHIELD_SERVICE`; it never guesses from the directory. `--process-env`, `--accept-unverified`, a file argument and `--container` can't be combined with it.

Where you put it decides what it proves. It checks *its own* environment, so it must run after every step that changes the environment, immediately before the application starts:

```bash
# Covered: the check sees exactly what the app inherits
export PORT="${PORT:-8000}"                      # wrapper substitutions first
envshield check --runtime --env production && exec app

# Not covered: the environment changes after the check
envshield check --runtime --env production
export PORT="${PORT:-8000}"                      # changed after the check
exec app
```

- **Container entrypoint**, as the last step before `exec`, as above. An inline `VAR=… app` prefix, or a wrapper that changes variables after the check, isn't covered.
- **Platform release command** (Railway, Heroku `release:` in a `Procfile`, Fly `release_command`): `envshield check --runtime --env production`. It fails the release before traffic moves. The release phase runs in its own process; this holds only if the platform gives the release and the app the same configuration.
- **Kubernetes init container** using the app's image and the same `env`/`envFrom` as the app container. The check sees the init container's environment, so it proves nothing about the app container if the two differ.

Not guaranteed: that the step runs at all, that a later process gets the same environment, or that a valid value is the right one (a valid URL to the wrong database). EnvShield doesn't read launch scripts or systemd units, and doesn't start your application. It needs Python and EnvShield in the deploy environment.

If the application loads its own env file instead (Next.js and many Node apps read `.env` at startup; a systemd unit may set only `NODE_ENV`), the process environment doesn't hold the configuration, so check the file the application reads instead. In a deploy script, once the release's `.env` is in place and before traffic switches:

```bash
(cd "$REL" && envshield check .env --env production) || die "configuration incomplete for production"
```

**`envshield setup`** — interactive onboarding: fills in whatever's missing, blank, or now-invalid in your local file, prompting with each variable's description.

**`envshield doctor`** — a full health check on your project's EnvShield setup at once (config files present, schema/template in sync, deployment manifest registered and valid, and more). `--fix` offers to fix what it can.

Want typed, validated config code instead of raw `os.getenv()` calls? **`envshield generate`** compiles the schema into a Python (`pydantic-settings`) or TypeScript (`zod`) module — a secondary, opt-in convenience once the contract exists, not a separate thing to learn.

### Discover

**`envshield check --since REV`** — catch a new environment-variable read that isn't in the schema yet, before you commit it. It runs the normal check and also compares your working tree with a Git revision: which undeclared reads are new, and how the contract changed.

```bash
$ envshield check --since HEAD
...
Code references (1 file(s) scanned)
┏━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━┓
┃ Status             ┃ Variable Name ┃ Referenced At ┃
┡━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━┩
│ Not Declared (new) │ ANALYTICS_KEY │ app/main.py:5 │
└────────────────────┴───────────────┴───────────────┘
...
Contract unchanged since HEAD.

INVALID: 1 invalid, 1 valid.
```

Only a *new* undeclared read fails it: one that was already there at `REV` is listed as `(before REV)` and doesn't, so an older project can block new ones without fixing every old one first (plain `check` still fails on all of them). Contract changes are listed but never fail it; `schema diff` is the gate for those. With no `.env` (CI), add `--code-only`: it reads no local file or manifest, only the code and the contract. `envshield undeclared` still works through v5 and prints the `check --since` form to use instead.

**`envshield explain VARIABLE`** — everything EnvShield knows about one variable: its contract, where it's declared, what source code reads it, and which deployment manifests reference it.

### Review / govern

**`envshield schema diff`** — compare the schema contract between two git revisions, classified by impact:

```bash
$ envshield schema diff origin/main HEAD
┌───────────────────────┬──────────┬───────────────────────────────────────────┐
│ Variable              │ Category │ Description                               │
├───────────────────────┼──────────┼───────────────────────────────────────────┤
│ STRIPE_WEBHOOK_SECRET │ BREAKING │ 'STRIPE_WEBHOOK_SECRET' was added and     │
│                       │          │ must always be present (no requiredIf, so │
│                       │          │ nothing exempts an existing config from   │
│                       │          │ now needing it).                          │
│ STRIPE_KEY            │ SECURITY │ 'STRIPE_KEY' secret classification        │
│                       │          │ WEAKENED (true -> false) -- it will no    │
│                       │          │ longer be masked in generated code, scan  │
│                       │          │ output, or validation-error messages.     │
└───────────────────────┴──────────┴───────────────────────────────────────────┘
```

This is the schema functioning as a real contract: a change to it is reviewable on a PR, the same way an API contract change would be — not just a local file that happens to agree with itself.

---

**`envshield service discover` / `service add`** register services in a multi-service project — see [Monorepos](#monorepos--services). A supporting safety check, `scan`, exists alongside all of this — see [Secret safety](#secret-safety).

Every command above documents its own options with `--help`; the full reference is in the [docs](https://docs.envshield.dev).

---

## Git / CI workflow

This is where the schema stops being documentation and starts being enforcement. Three concrete things EnvShield catches before a change reaches production:

**A new environment variable used in code but missing from the schema:**

```bash
envshield check --since origin/main --code-only --service api
```

**A schema contract change:**

```bash
envshield schema diff origin/main HEAD --service api
```

**A secret accidentally introduced into a commit:**

```bash
envshield hook install
```

The pre-commit hook runs `scan --staged --enforce`, applying classification-based enforcement with interactive override for high-confidence findings on local commits, and strict blocking in CI. See [Secret safety](#secret-safety) for enforcement behavior details.

In CI, diff against the merge-base, not the base branch's current tip (which can move while the PR is open):

```yaml
# .github/workflows/envshield.yml
on: pull_request
jobs:
  contract-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }
      - run: pipx install envshield
      - run: |
          BASE=$(git merge-base "origin/${{ github.base_ref }}" HEAD)
          envshield schema diff "$BASE" HEAD
          envshield check --since "$BASE" --code-only
```

Both commands exit non-zero on a real finding (for `check --since`, a new undeclared read), and also on an error that prevented evaluation (a bad revision, a schema that failed to parse) — never a silent "clean" for something it couldn't actually check. Add `--json` if you want to post a custom summary instead.

### SARIF output (`check --since --sarif`)

`envshield check --since` also supports `--sarif` — a standard [SARIF 2.1.0](https://docs.oasis-open.org/sarif/sarif/v2.1.0/) log, for GitHub Code Scanning or any other SARIF-consuming CI tool, so a missing declaration is annotated on its exact file and line rather than only appearing in a CI log:

```yaml
      - run: |
          BASE=$(git merge-base "origin/${{ github.base_ref }}" HEAD)
          envshield check --since "$BASE" --code-only --sarif > envshield.sarif
        continue-on-error: true   # upload the report even when it found something
      - uses: github/codeql-action/upload-sarif@v3
        with:
          sarif_file: envshield.sarif
```

`--sarif` lists only the new undeclared reads, deliberately: each one has a real file and line to point at. `schema diff` findings describe the *contract* (a variable added, removed, or changed) rather than a source location, so forcing them into SARIF would mean inventing a line number that isn't real — use its `--json`/exit-code output instead, or annotate it yourself (e.g. a `jq` step over the JSON, emitting `::error::` workflow commands).

An error that prevents evaluating a service (a bad revision, a schema that fails to parse) is never turned into a fabricated SARIF result — it's reported as a SARIF `invocation` with `executionSuccessful: false`, so a consumer can't mistake "couldn't check" for "checked and clean."

---

## Existing projects

Adopting EnvShield into a project that already has configuration doesn't require rewriting anything:

```
.env (existing)
    ↓
envshield init --from .env
    ↓
review env.schema.toml       (fix classifications, add descriptions)
    ↓
envshield check              (your local file satisfies it, and the code
                              reads nothing it doesn't declare)
    ↓
CI: schema diff + check --since --code-only   (governance from here on)
```

`init --from` reads your real values and does most of the schema-writing for you — it always leaves a `"TODO: Add description."` marker as a nudge to review, and any automatic secret/type classification is a starting point, not a final answer. Not using a `.env` file? `envshield init --from config/settings.py` reads a Python config module's assignments via its AST (never executes the file).

---

## Monorepos / services

One schema can serve every service in a repository. Grant each variable to the services that use it, and each service is checked against only its own variables:

```toml
[LOG_LEVEL]                  # no services: every service gets it
defaultValue = "info"

[DATABASE_URL]
secret = true
services = ["api", "worker"]

[PORT]                       # per-service values
type = "port"
[PORT.services.api]
defaultValue = "8000"
[PORT.services.worker]
defaultValue = "9000"
```

```yaml
# envshield.yml
services:
  api:
    schema: env.schema.toml
    dir: api
    local_file: api/.env
  worker:
    schema: env.schema.toml
    dir: worker
    local_file: worker/.env
manifests:
  - file: docker-compose.yml
    containers:       # Compose service -> EnvShield service
      api: api
      worker: worker
```

A few rules keep this safe:

- **A secret in a shared schema must name its services.** An unscoped secret is an error, so a secret never reaches a service by accident.
- **Each service sharing a schema has its own `dir:`.** Two services can't claim the same directory (`service add` writes `dir:` for you).
- **A registered manifest maps containers to services.** `check` then validates every mapped container against its own service's variables.

The full model is in [docs/architecture/shared-system-schema.md](docs/architecture/shared-system-schema.md). One schema per service still works too, with shared variables composed through `extends` (see [Composing schemas](#composing-schemas-extends)).

```bash
envshield service discover     # finds service-like directories, registers + seeds each one
envshield service list
envshield check --service api
```

Every command is service-aware. `--service` is optional with exactly one service configured, inferred automatically when you're standing inside a registered service's own directory, and only prompted for interactively when there's more than one and neither applies.

### Discovering reads in a shared library (`additional_source_roots`)

`check`'s code references and `explain` only walk a service's own directory by default. If part of a service's real configuration is read from a shared internal library that lives outside that directory — a common-utilities package imported by several services, say — those reads are invisible to both commands, for every service that imports the library.

`additional_source_roots` widens a service's discovery scope to cover extra directories:

```yaml
services:
  api:
    schema: services/api/env.schema.toml
    additional_source_roots:
      - libs/shared
```

`check --service api` and `explain --service api` now also look inside `libs/shared` — a variable read there counts toward `api`'s own discovery results exactly as if it lived in `services/api/` itself. The same directory can be listed by more than one service; each one independently sees the reads it contains. A path that doesn't exist yet contributes nothing (no error); a path resolving outside the project is refused, the same protection every other path in `envshield.yml` already has. The pre-commit `scan --staged` is unaffected: it already checks every staged file.

---

## Multiple sources per service (`completeness: union`)

By default, every registered source for a service — its `local_file`, each registered deployment manifest — is validated independently against the *whole* schema: `check`/`doctor` expect any one of them to be self-sufficient on its own. Some services genuinely don't work that way: part of the configuration lives in a local config file, the rest is supplied by a deployment manifest. Under the default model, a variable that only ever lives in one of them shows up as "missing" everywhere else, even though nothing is actually wrong.

Set `completeness: union` on a service to validate against the *combined* presence of all its registered sources instead — a variable satisfied by any one of them satisfies the service as a whole:

```yaml
services:
  api:
    schema: services/api/env.schema.toml
    local_file: services/api/config/settings.py
    completeness: union
manifests:
  - file: docker-compose.yml
    containers:
      api: api
```

```toml
[DB_HOST]
description = "set in config/settings.py"

[FEATURE_MODE]
description = "set via docker-compose.yml"
```

With `completeness: union`, `check`/`doctor` stop expecting `config/settings.py` to declare `FEATURE_MODE`, or `docker-compose.yml` to set `DB_HOST` — the schema is satisfied as long as every variable is present *somewhere* among the registered sources. `envshield schema sync` respects this too: it only ever appends a variable to a Python `local_file` that's genuinely missing from every registered source, never one that another source already covers.

A few things worth knowing:

- **Opt-in, per service.** Unset (the default) means every source must be independently self-sufficient, exactly as before this existed — nothing changes unless you set the key.
- **Union means presence, not values.** Whether an ordinary variable is satisfied depends only on whether it's present (and valid) somewhere — never on merging, comparing, or picking between its values across sources. A blank or invalid value is still reported, wherever it occurs. **The one deliberate exception** is a `requiredIf` trigger's own value (see next) — deciding whether a condition currently holds has always meant reading one specific value, even before `completeness: union` existed; union only extends *where* that one value may come from, it doesn't turn this into general value merging.
- **A `requiredIf` condition (see [Conditional requirements](#conditional-requirements-requiredif) above) is resolved against the union too** — if the variable a condition depends on lives in a different source than the one being checked, `completeness: union` still finds it. Sources that agree on the trigger's value resolve normally; if two sources genuinely disagree, EnvShield reports it as an explicit conflict rather than guessing which one wins — registration order is never used as a tiebreaker.
- **A source that fails to load is always a failure**, even if the sources that did load happen to satisfy the schema between them. `doctor` reports this as two separate signals for a union-mode service — source health (does this file exist and parse) and registered-source completeness (does the union satisfy the schema) — so one can never quietly hide behind the other. `check --json` reflects the same split: each source's own result is still reported individually, plus one `combined` entry per union-mode service carrying the aggregate verdict.

---

## Docker Compose base + override layers (`files:`)

A manifest registration normally points at one file:

```yaml
manifests:
  - file: docker-compose.yml
    containers:
      api: api
```

A real Compose project commonly splits its configuration across a base file plus an override that Compose applies automatically (`docker-compose.yml` + `docker-compose.override.yml`, or any other `-f a -f b` combination). Registering only the base misses whatever the override adds; registering the override alone reports the base's own variables as falsely missing, since an override file is deliberately just a delta. `files:` registers the whole ordered layer set as **one** logical manifest instead:

```yaml
manifests:
  - files:
      - docker-compose.yml
      - docker-compose.override.yml
    containers:
      api: api
```

Order matters — later files override earlier ones, exactly like `docker compose -f docker-compose.yml -f docker-compose.override.yml` itself. `check`/`doctor`/`explain` all validate the merged result as a single source, under one combined label (`"docker-compose.yml + docker-compose.override.yml"`).

This is Compose-specific merge behavior for the fields EnvShield already understands (`environment:`, `env_file:`) — **not** a general YAML merge engine. Volumes, networks, ports, build, and every other Compose field are never inspected or merged. `files:` is unrelated to `completeness: union` above: union combines *presence across different kinds of source* (a local file and a manifest); `files:` merges *values across ordered layers of one Compose source*. A manifest entry can declare `file:` or `files:`, never both.

---

## Supported languages / discovery

Source-code discovery (what powers `check`'s code references and `explain`'s "used in source" section) is AST-based for **Python** (`os.environ.get`, `os.getenv`, `os.environ[...]`) and pattern-based for **JavaScript/TypeScript** (`process.env.X`, `process.env["X"]`, `import.meta.env.X`, including single-level destructuring). No other language is discovered yet — the schema and validation commands work with any stack, but `check` and `explain` can only see what's read from these two.

`explain` additionally recognizes Flask's `current_app.config[...]`/`.get(...)` in Python — including the common `from flask import current_app as <alias>` import form — and reports it at **medium confidence**, shown inline as `(medium confidence)`: it's one level of indirection through a config object whose contents could come from anywhere, not a direct environment read. This recognition is deliberately `explain`-only — it never affects `check`'s or `scan`'s pass/fail signal, so a Flask-config read can never quietly satisfy (or break) a completeness check the way a direct `os.environ`/`os.getenv` read does.

---

## Secret safety

A supporting check alongside the contract, not the product itself. `secret = true` on a field tells EnvShield (and `setup`, and code generation) that it's sensitive — the schema records that fact, never the actual value.

### Secret scanning

**`envshield scan`** looks for hardcoded secrets by pattern (Stripe, AWS, GitHub tokens, and more). With `--staged` (what the pre-commit hook runs) it also blocks a commit that adds an env-var read the schema doesn't declare; outside the hook, `check` reports those. Values are always redacted in output — a Stripe *publishable* key (`pk_...`) is never flagged, since detection matches the secret-key pattern (`sk_...`) by shape, not the variable's name.

### Context-aware classification

Python candidates are analyzed for syntactic context to distinguish code patterns from secret values:

```python
# Classified as code (suppressed):
client(key=CONFIG_CONSTANT)


def handler(api_key: str): ...


# Classified as likely secret (detected):
client(key="sk_live_abc123...")
```

This reduces false positives on ordinary code patterns without broadly suppressing secret-shaped values. Classification is lightweight and syntactic — it recognizes type annotations, function arguments, and identifier references, but doesn't perform semantic analysis beyond what the tokenizer can see.

### Git hook enforcement

**`envshield hook install`** wires secret scanning into your Git workflow with classification-based enforcement:

```bash
envshield hook install
```

The pre-commit hook runs `scan --staged --enforce`, which scans only staged content and applies an enforcement policy based on classification confidence.

#### Enforcement behavior

| Finding type | Interactive (local commit) | Non-interactive (CI) |
|---|---|---|
| No findings | Allow | Allow |
| Clearly code-shaped | Suppressed | Suppressed |
| Ambiguous | Block | Block |
| High-confidence secret | Block + explicit override | Block |

**Clearly code-shaped** candidates (type annotations, function keyword arguments, etc.) are suppressed by the classifier and don't produce findings.

**Ambiguous** findings — insufficient context to confidently classify — block without override in both modes.

**High-confidence secrets** (string literals matching secret patterns) block by default but offer an interactive override for local commits:

```
⚠ EnvShield found potential secrets

HIGH CONFIDENCE
  src/config.py:42    Generic API Key    [REDACTED (30 chars)]

[1] Abort commit
[2] Commit anyway

Choice: 2

Type COMMIT ANYWAY to confirm: COMMIT ANYWAY

Proceeding with commit. The finding remains in the repository history.
```

The override requires exact confirmation text — `yes` or `Y` won't work. EOF (Ctrl+D) or Ctrl+C aborts. Raw secret values are never displayed in enforcement output.

In CI or other non-interactive contexts, high-confidence findings block with no prompt — the commit is refused, and the scan exits non-zero.

#### Scanning modes

```bash
envshield scan                      # Audit entire codebase
envshield scan --staged             # Scan only staged Git content
envshield scan --staged --enforce   # Staged scan + enforcement policy
```

The pre-commit hook uses the third form. `scan` without flags is for repository-wide audits, not commit-time enforcement.

---

## Removing EnvShield

```bash
envshield hook remove
```

Removes EnvShield's Git hook integration while preserving your project configuration — it does not delete `envshield.yml`, any `env.schema.toml`, `.env.example`, any registered local configuration file (`.env`, a Python config module, etc.), `.gitignore`, or any code `envshield generate` has produced, regardless of whether EnvShield originally created any of it. Those are your project's own configuration contract and application config, not disposable installation artifacts. It lists what it removed and what it preserved.

The only thing it ever deletes is a Git hook whose content exactly matches what EnvShield would generate right now. A hand-modified hook, a foreign hook (even one that happens to contain EnvShield's marker comment), or an EnvShield hook that's gone stale relative to your current `envshield.yml` (e.g. after registering a new service) is left in place and reported, never deleted or silently regenerated.

```bash
envshield hook remove --yes    # skip the confirmation prompt
```

`--yes` only skips the prompt — it never overrides the ownership check; a modified, foreign, or stale hook is preserved with `--yes` exactly as it is interactively. There is no `--force` and no `--purge`: this is intentionally not a "delete everything EnvShield ever created" command. To remove EnvShield's configuration from a project entirely, delete `envshield.yml` and the schema files yourself once you're sure you no longer need them.

`envshield uninstall` (and `envshield install-hook`, for `hook install`) still work but are deprecated and will be removed in v6.

---

## Known limitations

EnvShield is built around the patterns real projects actually use. A few narrower cases aren't handled yet. None of them let a secret leak:

- **Docker Compose:** a value with any `${VAR}` or `$VAR` reference in it, with or without an operator (`-`, `:-`, `?`, `:?`, `+`, `:+`), comes from the deploy host, which a static check can't see, so `check` reports it as could not verify (`host_forward`). A `${VAR-x}`/`${VAR:-x}` fallback that itself breaks the contract is still invalid. Interpolation inside an `env_file` isn't evaluated.
- **Kubernetes:** `env[].valueFrom.secretKeyRef`/`configMapKeyRef` are matched by the container's environment-variable name (`env[].name`) — name your schema variable after the env var, not the key — and, when the referenced Secret/ConfigMap is defined in the same manifest, the referenced key is validated to actually exist there. `envFrom.prefix` is applied. `initContainers` are selectable via `--container` but never auto-selected by default.
- **Source discovery:** Python is AST-based; JavaScript/TypeScript is pattern-based. No other language is discovered yet.
- **.env:** multiline quoted values aren't supported.
- **Windows:** the write-safety fix for a failed permission change (BL-162) hasn't been run on Windows; where `os.fchmod` is missing (Windows before Python 3.13) it's skipped, which is tested on Linux by simulation only.
- **CI:** runs on Ubuntu only (Python 3.10–3.12).

These are bounded, tracked engineering work — see the [roadmap](ROADMAP.md) for what's next and what EnvShield deliberately isn't building.

---

## Documentation

This README is enough to get real value out of EnvShield. For the full command reference, every schema field, `envshield.yml` structure, exit codes, and JSON output shapes, see the [documentation](https://docs.envshield.dev).

---

## License

MIT — see [LICENSE.md](LICENSE.md).
