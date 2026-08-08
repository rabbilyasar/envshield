# EnvShield 🛡️

[![CI](https://github.com/rabbilyasar/envshield/actions/workflows/ci.yml/badge.svg)](https://github.com/rabbilyasar/envshield/actions/workflows/ci.yml)
[![PyPI version](https://badge.fury.io/py/envshield.svg)](https://pypi.org/project/envshield/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Downloads](https://static.pepy.tech/personalized-badge/envshield?period=month&units=international_system&left_color=grey&right_color=blue&left_text=Downloads)](https://pepy.tech/project/envshield)
[![Website](https://img.shields.io/badge/Website-envshield.dev-blue?logo=google-chrome&logoColor=white)](https://www.envshield.dev)
![Stars](https://img.shields.io/github/stars/rabbilyasar/envshield?style=social)

**Your `.env` file, but it's a contract.**

EnvShield turns your project's environment variables into a single, version-controlled schema — `env.schema.toml` — that describes every variable your app needs: its type, whether it's secret, what it defaults to, and when it's required. EnvShield then uses that one file to do everything that used to be manual, scattered, or forgotten:

- Onboard a new developer in minutes with an interactive wizard, instead of a stale wiki page.
- Catch a missing or malformed environment variable *before* it breaks staging — locally, in a pre-commit hook, or in CI.
- Generate real, typed config code (`pydantic-settings` for Python, `zod` for TypeScript) instead of untyped `os.getenv()` calls.
- Validate the docker-compose file or Kubernetes manifest that actually deploys your service, not just your local `.env`.
- Scan for hardcoded secrets before they're committed.

It works the same way whether you have one repo with one `.env` file, or a monorepo with a dozen services. Everything below is free, open source (MIT), and runs entirely on your machine — EnvShield never sends your configuration or secrets anywhere.

[📚 Full Documentation](https://docs.envshield.dev/) · [🌐 Website](https://www.envshield.dev) · [🐙 GitHub](https://github.com/rabbilyasar/envshield)

---

## Table of contents

- [The problem](#the-problem)
- [Installation](#installation)
- [Quick start](#quick-start)
  - [A single service](#a-single-service)
  - [A monorepo with multiple services (monorepo)](#a-monorepo-with-multiple-services)
- [Core concept: the schema is the contract](#core-concept-the-schema-is-the-contract)
  - [Every field a variable can have](#every-field-a-variable-can-have)
  - [Conditional requirements (`requiredIf`)](#conditional-requirements-requiredif)
  - [Sharing variables across services (monorepo, `extends`)](#sharing-variables-across-services-extends)
- [Command reference](#command-reference)
  - [Core commands](#core-commands)
  - [Monorepo: managing multiple services (monorepo)](#monorepo-managing-multiple-services)
- [Machine-readable output (`--json`)](#machine-readable-output---json)
- [Typed config code generation](#typed-config-code-generation)
- [Validating deployment manifests](#validating-deployment-manifests)
- [Secret scanning and git hooks](#secret-scanning-and-git-hooks)
- [Setting up EnvShield for your project](#setting-up-envshield-for-your-project)
- [Maintaining EnvShield over time](#maintaining-envshield-over-time)
- [How EnvShield compares](#how-envshield-compares)
- [Troubleshooting / FAQ](#troubleshooting--faq)
- [Roadmap](#roadmap)
- [Community](#community)

---

## The problem

If you've worked on more than one real project, this is probably familiar:

- **`.env.example` is two years out of date.** It's missing three variables the API actually needs and still lists two nobody's used since 2024.
- **A new developer spends their first afternoon guessing.** "What env vars do I need? Which ones are secret? What's a reasonable default for `API_PORT`?"
- **A typo in `os.getenv("DATABSE_URL")` returns `None`**, and you find out at runtime, in whatever environment happens to hit that code path first.
- **Config drifts silently between local, staging, and prod.** Someone adds `STRIPE_API_KEY` to the API service and forgets the worker also needs it. Nobody notices until a job fails.
- **A real secret gets committed** because the pre-commit hook (if there is one) doesn't understand the difference between a genuine leak and the 15 intentionally-fake values already sitting in a test fixture.

None of these are exotic problems. They're the default state of a project's configuration once more than one person, one environment, or one service is involved — which is almost immediately.

**EnvShield's answer:** stop treating configuration as a pile of loose files that happen to agree with each other (or don't). Declare it once, as a schema, and let every other command — onboarding, validation, code generation, deployment checks, secret scanning — be driven by that one source of truth.

---

## Installation

```bash
pip install envshield
```

Requires Python 3.10+. EnvShield is a standalone CLI — it doesn't need to be added to your project's own dependencies (`requirements.txt`, `pyproject.toml`, `package.json`, etc.) unless you want it pinned for your team; a global `pip install` (or `pipx install envshield`, if you prefer isolated CLI tools) is enough.

Verify it installed:

```bash
envshield --version
```

---

## Quick start

### A single service

The common case: one repo, one `.env` file. This is the whole workflow — nothing else in this README is required to get full value out of EnvShield.

```bash
cd my-project
envshield init                    # Detects your framework and builds env.schema.toml from your real config
envshield setup                   # Interactive wizard: fills in .env from the schema
envshield generate --lang python  # Generates a typed config.py (or config.ts for TypeScript)
```

`init` looks for a real config source first — an existing `.env`, `.env.example`, or a recognizable Python config module (`config/settings.py` and similar) — and builds the schema from its actual variables, classifying each as secret or not, with a suggested default and, where the value's shape is unambiguous, an inferred type. Only a genuinely fresh project with nothing to read yet falls back to a generic framework template:

```
Found config/settings.py -- building your schema from its real variables.
✓ Created/updated schema: env.schema.toml
```

`init` also offers to install a git pre-commit hook (secret scanning) and a post-merge hook (drift check after every `git pull`) — say yes unless you already have your own hook-management tooling (Husky, `pre-commit`, etc.), in which case EnvShield will detect it and won't clobber it (see [Secret scanning and git hooks](#secret-scanning-and-git-hooks)).

`envshield import <file>` does the same real-variable analysis `init` runs automatically, as its own command — reach for it later, when you want to re-import after adding new variables to your code, point at a file `init` wouldn't have found on its own, or add `--interactive` to confirm each classification by hand instead of accepting the automatic guess:

```bash
envshield import .env --interactive
```

```
Analyzing variables...

✓ Analysis complete!
- Processed 12 variables.
- Marked 4 variable(s) as secrets.
- Suggested 6 default value(s).
- Inferred a type (int/port/bool/url/email) for 3 variable(s).
```

---

**Everything above is the complete single-service story.** Everything below this point — multiple services, schema composition, deployment manifests with more than one container — is opt-in, and only relevant once your repo actually has more than one service. Skip straight to [Core concept: the schema is the contract](#core-concept-the-schema-is-the-contract) if that's not you yet.

### A monorepo with multiple services

*(Optional — skip this if you only have one service.)*

If your repo has more than one service (an API, a web frontend, a worker), run `service discover` at the repo root instead of `init`:

```bash
envshield service discover
```

```
                         Discovered Services
┏━━━━━━━━┳━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━┓
┃ Name   ┃ Directory      ┃ Format ┃ Config File           ┃ Deployment Manifest  ┃
┡━━━━━━━━╇━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━┩
│ api    │ services/api   │ dotenv │ (default .env)        │ docker-compose.yml   │
│ web    │ services/web   │ dotenv │ (default .env)        │ docker-compose.yml   │
└────────┴────────────────┴────────┴───────────────────────┴──────────────────────┘
? Add these services to envshield.yml? Yes
✓ Registered api → services/api/env.schema.toml
✓ Registered web → services/web/env.schema.toml
```

One command scans for service-like directories (anything with a real `.env`-style file or a recognizable Python config module), registers each one in `envshield.yml`, seeds each schema from that service's actual current values, and — if it finds a `docker-compose.yml` in the service's directory or the project root — registers that too. It's additive: run it again later and it only picks up what's new, leaving already-configured services untouched.

Every command below is service-aware once you have more than one:

```bash
envshield scan --service api              # Scan API's code for undeclared vars
envshield setup --service web             # Onboard into the web service
envshield setup                           # No service given, more than one configured → "Which service? (api / web / all)"
```

`--service` is optional whenever there's exactly one service configured (it's used automatically), and prompts you to choose — or run against every service at once via "All services" — whenever there's more than one and you didn't specify.

Prefer to register a service by hand instead of relying on auto-discovery?

```bash
envshield service add api services/api --import services/api/.env
envshield service list
```

---

## Core concept: the schema is the contract

Everything in EnvShield is driven by `env.schema.toml` — one per service, or one at the repo root for a single-service project. It's plain TOML, meant to be read and hand-edited, and it's meant to be committed to git (it declares *shape*, never secret *values*).

```toml
[DATABASE_URL]
description = "PostgreSQL connection string for the API"
secret = true

[API_PORT]
description = "Port the API listens on"
type = "port"
defaultValue = "5000"

[LOG_LEVEL]
description = "Log verbosity"
enum = ["debug", "info", "warn", "error"]
defaultValue = "info"

[ADMIN_EMAIL]
description = "Where alerts get sent"
type = "email"

[FEATURE_X_ENABLED]
description = "Toggles the new billing flow"
type = "bool"
defaultValue = "false"

[FEATURE_X_API_KEY]
description = "Only needed once feature X is turned on"
secret = true
requiredIf = { var = "FEATURE_X_ENABLED", equals = "true" }
```

That's the whole contract. Every command below — `check`, `doctor`, `setup`, `generate`, `scan` — reads this file and nothing else to know what your project's configuration is supposed to look like.

### Every field a variable can have

| Field | Type | Meaning |
|---|---|---|
| `description` | string | Shown during `setup`, and copied into generated code as documentation. Not required, but `import`-generated schemas leave `"TODO: Add description."` as a nudge to fill it in. |
| `secret` | boolean | Marks the variable as sensitive. Secrets are prompted as hidden input in `setup`, masked in generated code (`SecretStr` in Python, a private-field `Secret<T>` wrapper in TypeScript that survives `console.log`/`JSON.stringify`), and never inferred a `type` during `import`. |
| `defaultValue` | string | A fallback value. A variable **without** `defaultValue` is treated as required — `check`/`doctor` flag it as missing if it's absent from your local file, and `setup` will prompt for it. |
| `type` | string | One of `string` (the default — no shape constraint beyond `pattern`), `int`, `float`, `bool`, `port` (an int, 1–65535), `url`, `email`. Enforced by `check`/`doctor`/`setup`, and drives the type of the field in generated code. |
| `enum` | list of strings | The variable's value must be one of these. Implies a `type` of `enum` regardless of whatever `type` is also set. `setup` presents these as a picker instead of free text, so an invalid value can't even be typed in. |
| `pattern` | string (regex) | An additional constraint checked on top of whatever `type` is set — e.g. `pattern = "^v\\d+\\.\\d+\\.\\d+$"` to require a semver-shaped string. |
| `requiredIf` | table | `{ var = "OTHER_VAR", equals = "some value" }` — this variable is required only when `OTHER_VAR`'s *current local value* equals `"some value"`. Without `requiredIf`, "required" just means "no `defaultValue`," exactly as before this existed. |

A variable with no `type`/`enum`/`pattern` at all behaves exactly as it always has: an unconstrained string, required unless it has a default. Every schema written before these fields existed is still valid — nothing here is a breaking change.

**A worked example** — running `envshield check` against a `.env` that violates several of the constraints above:

```bash
$ envshield check
Validating .env against schema...

┌────────────────────┬──────────────────────┬────────────────────────────────────┐
│ Status              │ Variable Name        │ Source                             │
├────────────────────┼──────────────────────┼────────────────────────────────────┤
│ Missing in Local    │ DATABASE_URL         │ env.schema.toml (Required)         │
│ Invalid Value        │ API_PORT             │ must be a port number from 1-65535 │
│                      │                      │ (got '99999')                      │
│ Invalid Value        │ LOG_LEVEL            │ must be one of: debug, info, warn,  │
│                      │                      │ error (got 'verbose')              │
│ Extra in Local       │ OLD_UNUSED_FLAG      │ .env                                │
└────────────────────┴──────────────────────┴────────────────────────────────────┘

Suggestion: Please update your local file to match the schema contract.
```

### Conditional requirements (`requiredIf`)

Real schemas usually have a handful of variables that are only relevant behind a feature flag or a specific mode. Marking them as required unconditionally means every developer has to fill in a value they don't need yet; marking them optional means nobody gets warned when the flag flips on in an environment that's missing the value.

`requiredIf` splits the difference:

```toml
[FEATURE_X_ENABLED]
type = "bool"
defaultValue = "false"

[FEATURE_X_API_KEY]
secret = true
requiredIf = { var = "FEATURE_X_ENABLED", equals = "true" }
```

With `FEATURE_X_ENABLED=false`, `FEATURE_X_API_KEY` is optional — `setup` won't prompt for it, and `check`/`doctor` won't flag it missing. Flip `FEATURE_X_ENABLED=true` in any environment, and it immediately becomes required there.

**Where this doesn't reach (by design):** generated config code can't evaluate `requiredIf` ahead of time — it doesn't know what `FEATURE_X_ENABLED` will be at runtime when it's generated. A `requiredIf` field with no default is typed as optional (`Optional[SecretStr] = None` in Python, `.optional()` in the zod schema); the real, conditional enforcement happens in `check`/`doctor`/`setup` against your project's actual local values, not in the generated code itself.

### Sharing variables across services (`extends`)

*(Monorepo-only — skip this if you have one service.)*

A monorepo with ten services usually has five or six variables every single one of them needs — `LOG_LEVEL`, `SENTRY_DSN`, `DATADOG_API_KEY` — and copy-pasting the same `[LOG_LEVEL]` block into ten schema files is exactly the kind of drift EnvShield exists to prevent.

Factor them into a shared base schema, and have each service extend it:

```toml
# shared/base.schema.toml
[LOG_LEVEL]
description = "Log verbosity, shared across every service"
enum = ["debug", "info", "warn", "error"]
defaultValue = "info"

[SENTRY_DSN]
description = "Error tracking"
secret = true
```

```toml
# services/api/env.schema.toml
extends = "../../shared/base.schema.toml"

[DATABASE_URL]
description = "API-specific"
secret = true
```

Loading `services/api/env.schema.toml` now transparently gives you `LOG_LEVEL`, `SENTRY_DSN`, *and* `DATABASE_URL` — every command (`check`, `doctor`, `setup`, `generate`, `scan`) just sees the merged result, no extra flag needed. A few things worth knowing:

- **`extends` accepts a list**, for more than one base: `extends = ["../../shared/base.schema.toml", "../../shared/observability.schema.toml"]`.
- **Chains work**: a schema can extend a base that itself extends another base. A circular chain (A extends B extends A) is detected and rejected with a clear error rather than hanging.
- **The child always wins.** If both the base and the service redeclare `LOG_LEVEL`, the service's own definition is used in full — fields aren't merged individually. If you override a shared variable, redeclare every field you want it to have, not just the one you're changing.
- **Paths are local, not remote (for now).** `extends` resolves a path relative to the schema file's own directory, and only within your project — it does not fetch a schema from a git URL or a package registry. Sharing a base schema across *separate repositories* isn't supported yet; see [Roadmap](#roadmap).

---

## Command reference

### Core commands

Everything a single-service project ever needs. `--service` shows up on most of these for the multi-service case (see below), but it's entirely optional until you actually have more than one service.

| Command | What it does |
|---|---|
| `envshield init [--force/-f]` | Detects your framework and builds `env.schema.toml` from a real config source if it finds one, otherwise a framework-aware template. Also scaffolds `envshield.yml`, updates `.gitignore`, and offers to install git hooks. Auto-registers a root-level `docker-compose.yml` as the project's deployment manifest if it finds one. `--force` re-runs on a project that already has a config (with a confirmation before overwriting). |
| `envshield import <file> [--output/-o PATH] [--force/-f] [--interactive] [--service NAME]` | Runs the same real-variable analysis `init` does automatically, as its own command — for re-importing after your code gains new variables, pointing at a file `init` wouldn't have found, or adding `--interactive` to confirm each secret/type classification by hand instead of accepting the automatic guess. `--output` changes where the schema is written (defaults to `env.schema.toml`, or the target service's schema path with `--service`). |
| `envshield check [file] [--service NAME] [--container NAME] [--json]` | Validates a local file (or, if omitted, the project's/service's default local file *and* its registered deployment manifest, if any) against the schema. `file` can be a plain `.env`, a Python config module, a docker-compose file, or a Kubernetes manifest. `--container` picks which service/container to check in a manifest that declares more than one (tried against `--service`'s name automatically first). Exits non-zero on any drift — safe to use as a CI gate. `--json` prints a machine-readable result instead (see below) and never falls into an interactive service picker. |
| `envshield doctor [--fix] [--service NAME] [--json]` | Runs every health check at once (see below) and reports a summary. `--fix` interactively offers to fix whatever it can — re-running `init`, regenerating the template, installing the git hook, or running `setup` to fill in missing/invalid local values. Exits non-zero if anything's still broken afterward. `--json` is incompatible with `--fix` (an interactive confirm prompt makes no sense in a machine-readable mode). |
| `envshield setup [output_file] [--service NAME]` | Interactive onboarding wizard: walks through every variable that's missing, blank, or has an existing value the schema no longer allows, prompting with the variable's description, masking secret input, and offering a picker for `enum` fields. Leaves everything already correct untouched. |
| `envshield schema sync [--service NAME]` | Regenerates `.env.example` from the schema (a dotenv project), or patches a Python-module local file in place to declare any schema variable it's missing (never rewrites it wholesale — only appends/patches the specific lines it owns). `import` already calls this automatically for you when it changes a project's/service's real schema, so you'll rarely need to run it by hand except after a manual schema edit. |
| `envshield generate [output_file] [--lang/-l python\|typescript] [--force/-f] [--service NAME]` | Compiles the schema into a typed, validated config module. `--lang` is auto-detected from your project (Next.js/Vite/Node.js → TypeScript; Python/Django/Flask, or nothing detected → Python) if omitted. A detected ecosystem with no codegen target at all (currently: Go) errors and asks for `--lang` explicitly, rather than silently guessing Python. Defaults to writing `config.py`/`config.ts`; `--force` overwrites an existing output file. See [Typed config code generation](#typed-config-code-generation). |
| `envshield scan [paths...] [--staged] [--config/-c PATH] [--exclude/-e PATTERN] [--service NAME] [--json]` | Scans code for hardcoded secrets and for env vars used in code (`os.getenv`, `os.environ.get`, `process.env.X`) but never declared in the schema. `--staged` scans only what's staged for the next commit (what the pre-commit hook runs); `--exclude` (repeatable) adds glob patterns to skip, on top of whatever `secret_scanning.exclude_files` is set in `envshield.yml`. See [Secret scanning and git hooks](#secret-scanning-and-git-hooks). |
| `envshield install-hook` | Installs both git hooks by hand, without going through `init`/`setup`/`service discover`'s interactive prompt. |
| `envshield --version` / `-v` | Prints the installed version and exits. |

**Not using a `.env` file at all?** Some projects (a Flask app whose local config is a checked-in Python module, for example) don't use dotenv at all. Point `local_file` at it instead, and EnvShield reads and writes it as source code, not as a dotenv file — appending or patching only the specific assignments it owns, never touching anything else in the file:

```yaml
services:
  alpha:
    schema: alpha/env.schema.toml
    local_file: alpha/config/env_config.local.py
```

### Monorepo: managing multiple services

*(Only relevant once your repo has more than one service — see [A monorepo with multiple services](#a-monorepo-with-multiple-services).)* Every core command above already accepts `--service`: automatically, if there's only one service configured; interactively (or against every service at once, via "All services"), if there's more than one.

| Command | What it does |
|---|---|
| `envshield service list` | Lists every service currently configured in `envshield.yml`, with its schema and local file paths. |
| `envshield service add <name> <directory> [--local-file PATH] [--example-file PATH] [--description/-d TEXT] [--schema PATH] [--import FILE] [--deployment-manifest PATH] [--container NAME]` | Registers one service by hand. `--local-file` is required when the service's real config isn't a dotenv file (e.g. a Python module) — see above. `--import` seeds the new service's schema from an existing config file in one step. `--deployment-manifest` is auto-detected (a compose file in the given directory or the project root that actually declares this service) if not given explicitly. |
| `envshield service remove <name>` | De-registers one service from `envshield.yml` (and drops it from any deployment manifest's container mapping). Never deletes the service's own files — schema, local env file, etc. — only the registration. |
| `envshield service discover [root] [--yes/-y]` | Scans for service-like directories not already registered, and offers to add them — see [Quick start](#a-monorepo-with-multiple-services). `--yes` skips the interactive confirmation, for CI/scripting. |

---

## Machine-readable output (`--json`)

`check`, `doctor`, and `scan` all accept `--json`: every Rich table/panel/progress-bar is suppressed, and exactly one JSON object is printed to stdout instead. Exit codes are unchanged (non-zero on any issue), so `--json` is a drop-in for CI gates, dashboards, or an agent loop that needs to branch on the result instead of parsing colored text. If multiple services are configured and `--service` isn't given, `--json` runs every service automatically rather than falling into the interactive "Which service?" picker.

```bash
envshield check --json
```

```json
{
  "success": false,
  "results": [
    {
      "file": ".env",
      "service": null,
      "clean": false,
      "missing": [],
      "blank": ["SECRETS_ENCRYPTION_KEY"],
      "invalid": {},
      "extra": []
    }
  ]
}
```

`doctor --json` returns the same per-service shape, with each service's individual health checks instead of a variable diff (`--fix` is rejected together with `--json` — an interactive confirm prompt has no place in a machine-readable mode):

```json
{
  "success": true,
  "results": [
    {
      "service": "beta",
      "passed": true,
      "checks": [
        {"name": "Configuration Files", "passed": true, "message": "Found and accessible."}
      ]
    }
  ]
}
```

`scan --json` returns one flat object (scan doesn't fan out per-service the way check/doctor do — it scans a set of files against whichever schema each one belongs to in a single pass):

```json
{
  "clean": false,
  "secrets": [
    {"file_path": "./config.py", "line_num": 12, "secret_type": "Generic API Key", "line_content": "..."}
  ],
  "undeclared_variables": [],
  "skipped_files": []
}
```

---

## Typed config code generation

Stop writing `os.getenv("DATABASE_URL")` — untyped, unvalidated, and silently `None` on a typo — and generate a real module instead:

```bash
envshield generate --lang python
```

```python
"""
AUTO-GENERATED by `envshield generate` — do not edit by hand.
Source of truth: env.schema.toml. Regenerate with: envshield generate

Requires: pip install pydantic pydantic-settings
"""

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed, validated access to this project's environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env", populate_by_name=True, extra="ignore"
    )

    database_url: SecretStr = Field(
        ...,
        description="PostgreSQL connection string for the API",
        alias="DATABASE_URL",
    )

    api_port: int = Field(
        "5000", description="Port the API listens on", alias="API_PORT", ge=1, le=65535
    )


settings = Settings()
```

```python
from config import settings

db = psycopg2.connect(
    settings.database_url.get_secret_value()
)  # SecretStr — masked in logs/reprs
port = settings.api_port  # a real int, validated 1-65535 on startup — not a string
```

The same schema compiles to TypeScript with `--lang typescript` (or automatically, in a Next.js/Vite/Node.js project):

```typescript
import { env } from './config';

const port: number = env.API_PORT;               // z.coerce.number().min(1).max(65535)
const db = await connect(env.DATABASE_URL.value); // Secret<string> — masked on console.log/JSON.stringify
```

Secrets are masked by construction, not by convention: Python's `SecretStr` and the generated TypeScript `Secret<T>` wrapper (a real private field, not TypeScript's compile-time-only `private` keyword) both prevent the value from appearing in a log line, a `console.log`, or a stack trace by accident.

**Requirements for the generated code, not for EnvShield itself:** the Python output needs `pydantic` and `pydantic-settings` in your project; if any field has `type = "email"`, it needs `pydantic[email]` too. The TypeScript output needs `zod`. EnvShield only *generates* the file — it doesn't install these for you.

---

## Validating deployment manifests

Config drift rarely shows up in a `.env` file in production — it shows up in whatever actually deploys the service: a docker-compose file, a Kubernetes `Deployment`. `envshield check` validates those directly, the same way it validates a `.env` file:

```bash
envshield check docker-compose.yml
envshield check k8s/deployment.yaml --container api
```

**docker-compose:**

```yaml
services:
  api:
    image: myorg/api
    environment:
      - DATABASE_URL=postgres://user:pass@db/app
    env_file:
      - .env.production
```

EnvShield merges `environment:` with whatever `env_file:` references (`environment:` wins on a conflict, matching Compose's own precedence). A bare `KEY` with no value, or anything sourced only from `env_file`, is treated as "present, value not visible in this file" rather than flagged missing — the real value legitimately lives outside the manifest.

**Kubernetes:** Deployment, StatefulSet, DaemonSet, Job, CronJob, and bare Pod manifests are all supported, including multi-document files (`---`-separated). A `ConfigMap`/`Secret` referenced via `envFrom` is resolved if it's defined in the *same file*; a `valueFrom` reference (or an unresolvable `envFrom`) is treated the same way as compose's `env_file` case — present, value not visible here.

**Multiple services/containers in one file?** `--container` picks which one. If you don't pass it, EnvShield tries your `--service` name first (services and containers are very often named identically) before asking you to be explicit:

```bash
envshield check docker-compose.yml --service api --container api-backend
```

**Register it once, stop typing the path.** `service discover`/`service add`/`init` auto-detect a compose file and register it as that service's (or the project's) deployment manifest. Once registered, `envshield check` (with no file argument) validates it automatically alongside your `.env`, and `doctor` gains a "Deployment Manifest" health check — only shown for projects that actually have one registered.

```bash
envshield service add api services/api --deployment-manifest docker-compose.yml --container api
```

**What this doesn't do (by design):** EnvShield only *reads* deployment manifests — it never generates or rewrites one. Safely patching a real Kubernetes YAML file while preserving everything else in it is a meaningfully bigger, riskier problem than validating it, and isn't something this tool does yet.

---

## Secret scanning and git hooks

`envshield scan` looks for two different problems at once: hardcoded secrets, and environment variables your code reads but the schema never declared.

```bash
envshield scan                 # Scan the current directory recursively
envshield scan --staged        # Scan only what's staged for the next commit (what the pre-commit hook runs)
envshield scan app/ --exclude "**/tests/*"
```

```
🚨 DANGER: Found 1 potential secret(s)!
┌──────────────┬──────┬──────────────────┬─────────────────────────────────────┐
│ File         │ Line │ Secret Type      │ Line Content                        │
├──────────────┼──────┼──────────────────┼─────────────────────────────────────┤
│ config.py    │ 12   │ Stripe Secret Key│ STRIPE_KEY = 'sk_live_abc123...'     │
└──────────────┴──────┴──────────────────┴─────────────────────────────────────┘

⚠️  WARNING: Found 1 undeclared variable(s)!
┌──────────┬──────┬────────────────────┐
│ File     │ Line │ Variable Name      │
├──────────┼──────┼────────────────────┤
│ app.py   │ 8    │ ANALYTICS_KEY      │
└──────────┴──────┴────────────────────┘

Commit aborted. Please fix the issues above before committing.
```

Detection recognizes framework "intentionally public" naming conventions (`NEXT_PUBLIC_*`, `VITE_*`, `REACT_APP_*`, `NUXT_PUBLIC_*`, `GATSBY_*`, dotenvx's `DOTENV_PUBLIC_KEY`) and never flags them as secrets on name alone — a Stripe *publishable* key is meant to ship in client-side code. A genuinely secret-shaped *value* under one of those names is still caught; only the naming-convention false positive is suppressed. `node_modules`, `.git`, virtualenvs, and build output are excluded from scans by default.

### Diff-aware scanning for files with intentional baseline secrets

Some projects check in a config file with intentionally fake secrets for local dev (a shared team fixture, say) and exclude that file from scanning via `secret_scanning.exclude_files` in `envshield.yml`. A plain exclusion means a *real* secret added to that same file later would never be caught either. `scan --staged` handles this with line-level intelligence: it diffs an excluded file's staged content against `HEAD` and scans only the newly-added lines, so pre-existing baseline values are ignored but anything genuinely new is still checked.

```yaml
# envshield.yml
secret_scanning:
  exclude_files:
    - "config/dev_fixtures.py"
```

```
ℹ️  config/dev_fixtures.py (excluded; diffs only: 1 new line(s))
🚨 DANGER: Found 1 potential secret(s)!
Line 47: PRODUCTION_SECRET = 'a_real_secret_that_just_got_added'
```

### Git hooks

`envshield install-hook` (or the interactive prompt during `init`/`setup`/`service discover`) installs two hooks:

- **pre-commit** — runs `envshield scan --staged`, aborting the commit if it finds anything.
- **post-merge** — runs `envshield doctor` after every `git pull`/merge, but only when a schema file actually changed in that merge, so a teammate who just added a new required variable gets alerted immediately instead of finding out the next time the app crashes.

Hooks respect a configured `core.hooksPath` (as set by Husky or similar tools) instead of assuming `.git/hooks`, and installing over an existing hook always tells you first whether that hook was EnvShield's own (safe to replace) or something else (naming how many lines of unrelated logic would be lost) before asking you to confirm.

---

## Setting up EnvShield for your project

A few concrete starting points, depending on what you're working with:

**A brand-new project.** `envshield init` in the project root. It detects your framework (Next.js, Vite, Django, Flask, or a generic default) and scaffolds a schema with a handful of sensible starting variables for that stack. Add your own on top, run `envshield setup` to create your local `.env`, and `envshield generate` once you're ready for typed config code.

**An existing project with a real `.env`.** `envshield import .env` instead of `init` — it reads your actual values and does most of the schema-writing for you. Review the output (it leaves `"TODO: Add description."` on every variable as a deliberate nudge), fill in descriptions, and adjust any secret/type classification it got wrong before committing the schema.

**An existing Django/Flask project whose config is a Python module, not a dotenv file.** `envshield import config/settings.py` works the same way — the parser reads top-level variable assignments via Python's AST (never executes the file). Register it with `local_file` pointing at that module so `setup`/`schema sync` patch it in place instead of trying to write a `.env` you don't actually use.

**A monorepo you're adopting EnvShield into for the first time.** `envshield service discover` at the repo root, not `init` in each service directory — it finds every service in one pass and seeds each schema from that service's real config. Run it again any time a new service is added; already-configured services are never touched or re-suggested.

**A project deployed via docker-compose or Kubernetes.** Register the manifest once — `service add ... --deployment-manifest docker-compose.yml`, or let `init`/`service discover` find it automatically — and `check`/`doctor` start validating it immediately, with no change to how you deploy.

**A team of more than a couple of people.** Install the git hooks (say yes when prompted, or run `install-hook`). Add `envshield check` (or `envshield doctor`) as a CI step so drift fails a pull request instead of a deploy — see the next section.

---

## Maintaining EnvShield over time

Setting EnvShield up once is the easy part. What actually keeps a schema trustworthy for the life of a project:

**Make `check` a CI gate, not just a local habit.** A GitHub Actions step is enough — no special integration required:

```yaml
- name: Validate environment configuration
  run: |
    pip install envshield
    envshield check
    # For a multi-service project, run it per-service, or loop over `envshield service list`.
```

This catches the exact failure mode EnvShield exists to prevent: a pull request that adds code reading a new environment variable without adding that variable to the schema, or a schema that's drifted out of sync with `.env.example`. `check` and `doctor` both exit non-zero on any drift.

**Whenever you add a new environment variable, add it to the schema in the same commit, not after.** The pre-commit hook's `scan --staged` will actually catch you here — a newly-used, undeclared variable shows up as a warning before you can commit it. Treat that warning as the schema reminding you, not as noise to dismiss.

**Run `envshield doctor` after pulling, not just when something's already broken.** The post-merge hook does this automatically whenever a schema file changed in the merge — if you skipped installing hooks initially, `envshield install-hook` takes thirty seconds and pays for itself the first time a teammate adds a required variable without telling anyone.

**When a shared variable changes, update the base schema once, not every service that extends it.** If you're using `extends` (see [Sharing variables across services](#sharing-variables-across-services-extends)), a change to `LOG_LEVEL`'s description or default belongs in the base schema — every service extending it picks it up automatically, with nothing to keep in sync by hand.

**Re-run `envshield doctor --fix` instead of hand-editing your local `.env` when it's flagged as broken.** It delegates to the same `setup` wizard used for onboarding, so it fixes exactly what's wrong (missing, blank, or invalid values) and leaves everything else untouched.

**Keep EnvShield itself current.** `pip install --upgrade envshield`. Schema files written by an older version remain fully valid — every field documented above is additive, not a breaking change to the format.

---

## How EnvShield compares

EnvShield isn't trying to replace a dedicated secret scanner or a cloud secret manager — it's a different, complementary layer: the schema/contract/codegen layer that sits on top of (or alongside) whichever of those you already use.

| | EnvShield | Gitleaks | dotenvx | Infisical / Doppler | direnv |
|---|---|---|---|---|---|
| Schema-driven validation (types, enums, conditional requirements) | ✅ | ❌ | ❌ | ❌ | ❌ |
| Typed config code generation (Python, TypeScript) | ✅ | ❌ | ❌ | ❌ | ❌ |
| Validates deployment manifests (docker-compose, Kubernetes) | ✅ | ❌ | ❌ | ❌ | ❌ |
| Multi-service schema management, with shared/composed schemas | ✅ | ❌ | ❌ | Partial (multi-environment, not a documented shared contract) | ❌ |
| Interactive onboarding wizard | ✅ | ❌ | ❌ | ✅ (hosted) | ❌ |
| Secret detection in code/commits | ✅ (good enough for most teams) | ✅ (more detector rules, actively maintained by a dedicated team) | ✅ | Varies by plan | ❌ |
| Stores or syncs actual secret values across a team | ❌ (never touches real values) | ❌ | Encrypts values in the file | ✅ | ❌ |
| Works fully offline | ✅ | ✅ | ✅ | Depends on the tier/self-hosting | ✅ |
| Free / open source | ✅ (all of it, indefinitely) | ✅ | ✅ | Free tier + paid tiers | ✅ |

**Where each of these genuinely is a better choice than EnvShield today:** Gitleaks and similar dedicated scanners have years of tuned detector rules and are actively maintained specifically for detection accuracy — if secret-scanning coverage is your main concern, run one of them *alongside* EnvShield rather than relying on `scan` alone. Doppler and Infisical solve real-value distribution and multi-environment secret sync across a team, which EnvShield deliberately doesn't attempt (see the row above) — if that's your primary need, they're the right tool. direnv solves a different problem entirely (auto-loading env vars into your shell on `cd`) and isn't a substitute for schema validation or vice versa.

**Where EnvShield is the only thing in this table that does it at all:** one schema that's simultaneously documentation, a validation contract, a codegen input for two languages, and something your deployment manifests are checked against — kept in git, next to the code it configures, with nothing to host.

---

## Troubleshooting / FAQ

**`check`/`doctor` say a manifest declares "multiple services" / "multiple containers."** Pass `--container <name>` explicitly, or register the manifest with `service add --deployment-manifest ... --container <name>` so you never have to pass it again.

**My pre-commit hook doesn't seem to run.** Check `git config core.hooksPath` — if it's set (Husky sets this), EnvShield installs there instead of `.git/hooks`, but if the hook was installed by an older EnvShield version before that was supported, re-run `envshield install-hook`. `envshield doctor` includes a "Git Pre-commit Hook" check that catches this.

**`scan` is flagging something that isn't a secret, or missing something that is.** The scanner is regex/entropy-based, not a machine-learning classifier — it's tuned to be broadly useful, not perfect for every codebase. For a false positive, add a targeted `--exclude` glob or a `secret_scanning.exclude_files` entry. For a miss, please open an issue with the (redacted) pattern that slipped through — and if secret-detection *accuracy* specifically is your priority, consider running Gitleaks or a similar dedicated scanner alongside `scan` rather than relying on it alone.

**Can I use EnvShield without git hooks?** Yes — every command works standalone. Hooks are a convenience, not a requirement; decline the prompt (or never run `install-hook`) if your team manages hooks another way.

**Does EnvShield ever send my configuration or secrets anywhere?** No. Every command reads and writes local files. There is no telemetry, no network calls, and no cloud backend in the current release.

**What happens to my schema if I stop using EnvShield?** Nothing — `env.schema.toml` is plain TOML and your `.env` files are plain dotenv files. Neither is EnvShield-proprietary; both remain exactly as useful (as documentation, if nothing else) with the tool uninstalled.

---

## Roadmap

**Available now, free, forever:** everything documented above — schema management, typed codegen, deployment-manifest validation, schema composition, secret scanning, onboarding, and multi-service support. None of this is time-limited or moves behind a paywall later.

**Being explored, not yet built — no committed timeline:**
- A hosted way to share actual secret *values* and coordinate per-environment (dev/staging/prod) overrides across a team — the one thing this README is explicit about EnvShield not doing today.
- A shared/base schema referenced across *separate repositories* (today's `extends` is local-path-only, within one project — see [Sharing variables across services](#sharing-variables-across-services-extends)).
- Deeper integration with existing secret managers (Vault, AWS/GCP Secrets Manager) — pulling real values for local `setup`/`check` without EnvShield ever storing them itself.

If any of the above would matter to you, [open a discussion](https://github.com/rabbilyasar/envshield/discussions) — what actually gets built next is driven by what real projects hit first, not a fixed plan.

---

## Community

Questions? Ideas? Found a bug?

- 🐙 [GitHub Issues](https://github.com/rabbilyasar/envshield/issues)
- 💬 [GitHub Discussions](https://github.com/rabbilyasar/envshield/discussions)
- 🌐 [Website](https://www.envshield.dev)
- 🐍 [PyPI](https://pypi.org/project/envshield/)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT — see [LICENSE.md](LICENSE.md). Use it freely, in any project.
