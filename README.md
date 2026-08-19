# EnvShield

A schema-first environment configuration governance CLI for developers.

`.env` files and environment variables have no contract. Nothing declares what a project actually needs, what's secret, what's required where, or whether a change to one breaks another service. EnvShield gives that contract a file — `env.schema.toml` — and drives validation, discovery, review, and secret scanning from it.

Free, open source (MIT), and fully local. EnvShield never sends your configuration or secrets anywhere.

[Documentation](https://docs.envshield.dev) · [GitHub](https://github.com/rabbilyasar/envshield) · [PyPI](https://pypi.org/project/envshield/)

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
    → contract          (types, required/optional, secret, defaults)
    → validation        (check, doctor, setup)
    → usage discovery   (undeclared — what does the code actually read?)
    → schema diff       (what changed, across two git revisions?)
    → secret scanning   (scan — nothing hardcoded, nothing leaked)
```

The schema is the center. Secret scanning is one capability that sits on top of it, not the other way around.

---

## Quick Start

```bash
pip install envshield
```

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
| `secret` | Marks the variable sensitive — masked input in `setup`, never inferred by `import`, masked in generated code. |
| `defaultValue` | What `setup` writes automatically. A variable still has to be explicitly present in your local file even with a default — `check`/`doctor` name the default inline so a missing one is obvious. |
| `requiredIf` | `{ var = "OTHER_VAR", equals = "some value" }` — required only when that condition holds. With no `defaultValue` and no `requiredIf`, a variable is required unconditionally. |
| `description` | Shown in `setup`, copied into generated code. |

**There is no separate `required = true/false` field.** Requiredness is derived: unconditional by default, waived by a `defaultValue`, or made conditional by `requiredIf`.

**Composing schemas (`extends`).** A monorepo with several services usually shares a handful of variables. Factor them into a base schema and extend it — the child's own definition always wins on a conflict, no per-field merging:

```toml
# services/api/env.schema.toml
extends = "../../shared/base.schema.toml"

[DATABASE_URL]
secret = true
```

---

## Core workflow

**`envshield check`** — validate a local file against the schema.

```bash
$ envshield check
┌───────────────────┬────────────────┬────────────────────────────────────┐
│ Status             │ Variable Name  │ Source                              │
├───────────────────┼────────────────┼────────────────────────────────────┤
│ Missing in Local   │ DATABASE_URL   │ env.schema.toml (Required)          │
│ Invalid Value      │ PORT           │ must be a port number from 1-65535  │
└───────────────────┴────────────────┴────────────────────────────────────┘
```

**`envshield undeclared`** — catch a new environment-variable read that isn't in the schema yet, before you commit it. Compares source code between two git revisions (or your working tree against `HEAD` by default):

```bash
$ envshield undeclared
┌────────────────┬─────────────┬──────┬───────────┬──────────────────────┐
│ Variable       │ File        │ Line │ Access    │ Contract status      │
├────────────────┼─────────────┼──────┼───────────┼──────────────────────┤
│ ANALYTICS_KEY  │ app/main.py │ 42   │ os.getenv │ missing declaration  │
└────────────────┴─────────────┴──────┴───────────┴──────────────────────┘
```

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

**`envshield scan`** — hardcoded secrets and undeclared variables, in one pass. Values are always redacted, never printed in the clear:

```bash
$ envshield scan --staged
🚨 DANGER: Found 1 potential secret(s)!
┌───────────┬──────┬─────────────────┬──────────────────────┐
│ File      │ Line │ Secret Type     │ Preview              │
├───────────┼──────┼─────────────────┼──────────────────────┤
│ config.py │ 12   │ Generic API Key │ <redacted, 40 chars> │
└───────────┴──────┴─────────────────┴──────────────────────┘
```

**`envshield setup`** — interactive onboarding: fills in whatever's missing, blank, or now-invalid in your local file, prompting with each variable's description and masking secret input.

**`envshield doctor`** — a full health check on your project's EnvShield setup at once (config files present, schema/template in sync, deployment manifest registered and valid, and more). `--fix` offers to fix what it can.

**`envshield explain VARIABLE`** — everything EnvShield knows about one variable: its contract, where it's declared, what source code reads it, and which deployment manifests reference it.

**`envshield import <file>`** — the same real-config analysis `init` runs automatically, as its own command, for refreshing a schema after your config changes.

**`envshield service discover` / `service add`** — register services in a multi-service project (see [Monorepos](#monorepos--services) below).

Every command above documents its own options with `--help`; the full reference is in the [docs](https://docs.envshield.dev).

---

## Git / CI workflow

This is where the schema stops being documentation and starts being enforcement. Three concrete things EnvShield catches in a PR:

**A new environment variable used in code but missing from the schema:**

```bash
envshield undeclared origin/main HEAD --service api
```

**A schema contract change:**

```bash
envshield schema diff origin/main HEAD --service api
```

**A secret accidentally introduced into a commit** — the pre-commit hook runs `scan --staged` automatically:

```bash
envshield hook install
```

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
      - run: pip install envshield
      - run: |
          BASE=$(git merge-base "origin/${{ github.base_ref }}" HEAD)
          envshield schema diff "$BASE" HEAD
          envshield undeclared "$BASE" HEAD
```

Both commands exit non-zero on a real finding — nothing beyond the exit code is required to gate a PR. Add `--json` if you want to post a custom summary instead.

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

---

## Secret safety

- **Classification lives in the schema, not the value.** `secret = true` tells EnvShield (and `setup`, and code generation) that a field is sensitive — the schema never contains the actual value, only the fact that it's secret.
- **`scan`** looks for hardcoded secrets by pattern (Stripe, AWS, GitHub tokens, and more) and for env-var reads the schema doesn't declare, in one pass.
- **`envshield hook install`** wires `scan --staged` into a pre-commit hook, so a real secret is caught before it's committed, not after.
- Detection is value-shape based, not name-based: a Stripe *publishable* key (`pk_...`) never matches the secret-key pattern (`sk_...`) regardless of what the variable is called — a genuinely secret-shaped value is still caught under any name.
- A file excluded from scanning (a shared local-dev fixture with intentionally fake values) is still diffed line-by-line against `HEAD` when staged — a real secret added to that same file later is still caught.

---

## Supported languages / discovery

Source-code discovery (what powers `undeclared` and `explain`'s "used in source" section) is AST-based for **Python** (`os.environ.get`, `os.getenv`, `os.environ[...]`) and pattern-based for **JavaScript/TypeScript** (`process.env.X`, `process.env["X"]`, `import.meta.env.X`, including single-level destructuring). No other language is discovered yet — the schema and validation commands work with any stack, but `undeclared`/`explain` can only see what's read from these two.

---

## Documentation

This README is enough to get real value out of EnvShield. For the full command reference, every schema field, `envshield.yml` structure, exit codes, and JSON output shapes, see the [documentation](https://docs.envshield.dev).

---

## License

MIT — see [LICENSE.md](LICENSE.md).
