# EnvShield

Environment variables, as a version-controlled configuration contract.

`.env` files and environment variables have no contract. Nothing declares what a project actually needs, what's required where, or whether a change to one breaks another service. EnvShield gives that contract a real, committed file — `env.schema.toml` — and everything else follows from it: validating a local file against it, discovering what code actually reads that the schema doesn't know about, and reviewing how the contract itself changes across git revisions. Secret handling is one part of that contract, not the product's center of gravity.

Free, open source (MIT), and fully local. EnvShield never sends your configuration or secrets anywhere.

[Website](https://www.envshield.dev) · [Documentation](https://docs.envshield.dev) · [GitHub](https://github.com/rabbilyasar/envshield) · [PyPI](https://pypi.org/project/envshield/)

---

## Why EnvShield?

Environment variables tend to go through the same decay, on every real project:

```
.env / os.environ
    → undocumented      (what does this app actually need?)
    → inconsistent      (staging has it, local doesn't)
    → undeclared        (new code reads a var nobody wrote down)
    → accidentally exposed   (a real key ends up in a commit)
    → hard to review    ("what did this PR actually change about config?")
```

EnvShield's answer is to make the configuration contract a real, versioned file, and let every other command work from it:

```
env.schema.toml
    → contract          (types, required/optional, defaults, conditional requirements)
    → validation        (check, doctor, setup)
    → usage discovery   (undeclared — what does the code actually read?)
    → schema diff       (what changed to the contract, across two git revisions?)
    → secret safety     (scan — a supporting check, not the product)
```

`env.schema.toml` is the product. Everything above reads from that one file; nothing above it invents its own idea of what your configuration is supposed to be.

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
| `defaultValue` | What `setup` writes automatically. A variable still has to be explicitly present in your local file even with a default — `check`/`doctor` name the default inline so a missing one is obvious. |
| `description` | Shown in `setup`, copied into generated code. |

**There is no separate `required = true/false` field.** Requiredness is derived: unconditional by default, waived by a `defaultValue`, or made conditional by `requiredIf` — see below.

### Conditional requirements (`requiredIf`)

A variable that's only relevant behind a feature flag doesn't need to be required unconditionally. `requiredIf = { var = "OTHER_VAR", equals = "some value" }` makes a field required only when that condition holds — with no `defaultValue` and no `requiredIf`, a variable stays required unconditionally, exactly as before this existed:

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

## Core workflow

Four stages, in order: define the contract, validate against it, discover what code actually depends on, review how the contract itself changes.

### Define

`env.schema.toml` (see [The Schema](#the-schema) above) is what you define. `envshield init`/`envshield import <file>` build it from your real, existing configuration — see [Existing projects](#existing-projects).

### Validate

**`envshield check`** — validate local configuration, and commonly-used Docker Compose and Kubernetes deployment-manifest patterns, against the same schema (see [Known limitations](#known-limitations)).

```bash
$ envshield check
┌───────────────────┬────────────────┬────────────────────────────────────┐
│ Status             │ Variable Name  │ Source                              │
├───────────────────┼────────────────┼────────────────────────────────────┤
│ Missing in Local   │ DATABASE_URL   │ env.schema.toml (Required)          │
│ Invalid Value      │ PORT           │ must be a port number from 1-65535  │
└───────────────────┴────────────────┴────────────────────────────────────┘
```

**`envshield setup`** — interactive onboarding: fills in whatever's missing, blank, or now-invalid in your local file, prompting with each variable's description.

**`envshield doctor`** — a full health check on your project's EnvShield setup at once (config files present, schema/template in sync, deployment manifest registered and valid, and more). `--fix` offers to fix what it can.

Want typed, validated config code instead of raw `os.getenv()` calls? **`envshield generate`** compiles the schema into a Python (`pydantic-settings`) or TypeScript (`zod`) module — a secondary, opt-in convenience once the contract exists, not a separate thing to learn.

### Discover

**`envshield undeclared`** — catch a new environment-variable read that isn't in the schema yet, before you commit it. Compares source code between two git revisions (or your working tree against `HEAD` by default):

```bash
$ envshield undeclared
┌────────────────┬─────────────┬──────┬───────────┬──────────────────────┐
│ Variable       │ File        │ Line │ Access    │ Contract status      │
├────────────────┼─────────────┼──────┼───────────┼──────────────────────┤
│ ANALYTICS_KEY  │ app/main.py │ 42   │ os.getenv │ missing declaration  │
└────────────────┴─────────────┴──────┴───────────┴──────────────────────┘
```

**`envshield explain VARIABLE`** — everything EnvShield knows about one variable: its contract, where it's declared, what source code reads it, and which deployment manifests reference it.

### Review / govern

**`envshield schema diff`** — compare the schema contract between two git revisions, classified by impact:

```bash
$ envshield schema diff origin/main HEAD
┌───────────────┬──────────┬───────────────────────────────────────────┐
│ Variable      │ Category │ Description                                │
├───────────────┼──────────┼───────────────────────────────────────────┤
│ PORT          │ BREAKING │ lost its default and is now always         │
│               │          │ required                                   │
│ STRIPE_KEY    │ SECURITY │ secret classification WEAKENED             │
└───────────────┴──────────┴───────────────────────────────────────────┘
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
envshield undeclared origin/main HEAD --service api
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
          envshield undeclared "$BASE" HEAD
```

Both commands exit non-zero on a real finding, and also on an error that prevented evaluation (a bad revision, a schema that failed to parse) — never a silent "clean" for something it couldn't actually check. Add `--json` if you want to post a custom summary instead.

### SARIF output (`undeclared --sarif`)

`envshield undeclared` also supports `--sarif` — a standard [SARIF 2.1.0](https://docs.oasis-open.org/sarif/sarif/v2.1.0/) log, for GitHub Code Scanning or any other SARIF-consuming CI tool, so a missing declaration is annotated on its exact file and line rather than only appearing in a CI log:

```yaml
      - run: |
          BASE=$(git merge-base "origin/${{ github.base_ref }}" HEAD)
          envshield undeclared "$BASE" HEAD --sarif > envshield.sarif
        continue-on-error: true   # upload the report even when it found something
      - uses: github/codeql-action/upload-sarif@v3
        with:
          sarif_file: envshield.sarif
```

`--sarif` only exists for `undeclared`, deliberately: each finding there has a real file and line to point at. `schema diff` findings describe the *contract* (a variable added, removed, or changed) rather than a source location, so forcing them into SARIF would mean inventing a line number that isn't real — use its `--json`/exit-code output instead, or annotate it yourself (e.g. a `jq` step over the JSON, emitting `::error::` workflow commands).

An error that prevents evaluating a service (a bad revision, a schema that fails to parse) is never turned into a fabricated SARIF result — it's reported as a SARIF `invocation` with `executionSuccessful: false`, so a consumer can't mistake "couldn't check" for "checked and clean."

---

## Existing projects

Adopting EnvShield into a project that already has configuration doesn't require rewriting anything:

```
.env (existing)
    ↓
envshield import .env
    ↓
review env.schema.toml       (fix classifications, add descriptions)
    ↓
envshield check               (confirm your local file already satisfies it)
    ↓
envshield undeclared          (confirm nothing's silently missing)
    ↓
CI: schema diff + undeclared  (governance from here on)
```

`import` reads your real values and does most of the schema-writing for you — it always leaves a `"TODO: Add description."` marker as a nudge to review, and any automatic secret/type classification is a starting point, not a final answer. Not using a `.env` file? `envshield import config/settings.py` reads a Python config module's assignments via its AST (never executes the file).

---

## Monorepos / services

If a repo has more than one service, `envshield.yml` registers each one with its own schema:

```yaml
services:
  api:
    schema: services/api/env.schema.toml
    local_file: services/api/.env
  worker:
    schema: services/worker/env.schema.toml
```

```bash
envshield service discover     # finds service-like directories, registers + seeds each one
envshield service list
envshield check --service api
```

Every command is service-aware. `--service` is optional with exactly one service configured, inferred automatically when you're standing inside a registered service's own directory, and only prompted for interactively when there's more than one and neither applies.

### Discovering reads in a shared library (`additional_source_roots`)

`undeclared` and `explain` only walk a service's own directory by default. If part of a service's real configuration is read from a shared internal library that lives outside that directory — a common-utilities package imported by several services, say — those reads are invisible to both commands, for every service that imports the library.

`additional_source_roots` widens a service's discovery scope to cover extra directories:

```yaml
services:
  api:
    schema: services/api/env.schema.toml
    additional_source_roots:
      - libs/shared
```

`undeclared --service api` and `explain --service api` now also look inside `libs/shared` — a variable read there counts toward `api`'s own discovery results exactly as if it lived in `services/api/` itself. The same directory can be listed by more than one service; each one independently sees the reads it contains. A path that doesn't exist yet contributes nothing (no error); a path resolving outside the project is refused, the same protection every other path in `envshield.yml` already has. `scan` is unaffected — it's already repository-wide.

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

Source-code discovery (what powers `undeclared` and `explain`'s "used in source" section) is AST-based for **Python** (`os.environ.get`, `os.getenv`, `os.environ[...]`) and pattern-based for **JavaScript/TypeScript** (`process.env.X`, `process.env["X"]`, `import.meta.env.X`, including single-level destructuring). No other language is discovered yet — the schema and validation commands work with any stack, but `undeclared`/`explain` can only see what's read from these two.

`explain` additionally recognizes Flask's `current_app.config[...]`/`.get(...)` in Python — including the common `from flask import current_app as <alias>` import form — and reports it at **medium confidence**, shown inline as `(medium confidence)`: it's one level of indirection through a config object whose contents could come from anywhere, not a direct environment read. This recognition is deliberately `explain`-only — it never affects `undeclared`'s or `scan`'s pass/fail signal, so a Flask-config read can never quietly satisfy (or break) a completeness check the way a direct `os.environ`/`os.getenv` read does.

---

## Secret safety

A supporting check alongside the contract, not the product itself. `secret = true` on a field tells EnvShield (and `setup`, and code generation) that it's sensitive — the schema records that fact, never the actual value.

### Secret scanning

**`envshield scan`** looks for hardcoded secrets by pattern (Stripe, AWS, GitHub tokens, and more), and for env-var reads the schema doesn't declare, in one pass. Values are always redacted in output — a Stripe *publishable* key (`pk_...`) is never flagged, since detection matches the secret-key pattern (`sk_...`) by shape, not the variable's name.

### Context-aware classification

Python candidates are analyzed for syntactic context to distinguish code patterns from secret values:

```python
# Classified as code (suppressed):
client(key=CONFIG_CONSTANT)
def handler(api_key: str):
    ...

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
envshield uninstall
```

Removes EnvShield's Git hook integration while preserving your project configuration — it does not delete `envshield.yml`, any `env.schema.toml`, `.env.example`, any registered local configuration file (`.env`, a Python config module, etc.), `.gitignore`, or any code `envshield generate` has produced, regardless of whether EnvShield originally created any of it. Those are your project's own configuration contract and application config, not disposable installation artifacts.

The only thing `uninstall` ever deletes is a Git hook whose content exactly matches what EnvShield would generate right now — the same ownership rule `hook remove` already uses. A hand-modified hook, a foreign hook (even one that happens to contain EnvShield's marker comment), or an EnvShield hook that's gone stale relative to your current `envshield.yml` (e.g. after registering a new service) is left in place and reported, never deleted or silently regenerated.

```bash
envshield uninstall --yes    # skip the confirmation prompt
```

`--yes` only skips the prompt — it never overrides the ownership check; a modified, foreign, or stale hook is preserved with `--yes` exactly as it is interactively. There is no `--force` and no `--purge`: `uninstall` is intentionally not a "delete everything EnvShield ever created" command. To remove EnvShield's configuration from a project entirely, delete `envshield.yml` and the schema files yourself once you're sure you no longer need them.

---

## Known limitations

EnvShield is built around the patterns real projects actually use. A few narrower cases aren't handled yet — none of them let a secret leak or let a genuinely missing required variable pass as clean:

- **Docker Compose:** `${VAR}`, `${VAR:-default}`, and the no-colon `${VAR-default}` form are all supported. The `${VAR:?}`/`${VAR:+}` forms and embedded/multiple interpolation references in one value aren't evaluated yet.
- **Kubernetes:** `env[].valueFrom.secretKeyRef`/`configMapKeyRef` are matched by the container's environment-variable name (`env[].name`) — name your schema variable after the env var, not the key — and, when the referenced Secret/ConfigMap is defined in the same manifest, the referenced key is validated to actually exist there. `envFrom.prefix` is applied. `initContainers` are selectable via `--container` but never auto-selected by default.
- **Source discovery:** Python is AST-based; JavaScript/TypeScript is pattern-based. No other language is discovered yet.
- **.env:** multiline quoted values aren't supported.

These are bounded, tracked engineering work — see the [roadmap](ROADMAP.md) for what's next and what EnvShield deliberately isn't building.

---

## Documentation

This README is enough to get real value out of EnvShield. For the full command reference, every schema field, `envshield.yml` structure, exit codes, and JSON output shapes, see the [documentation](https://docs.envshield.dev).

---

## License

MIT — see [LICENSE.md](LICENSE.md).
