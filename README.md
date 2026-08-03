# EnvShield 🛡️ – Configuration Orchestration for Multi-Service Projects

[![CI](https://github.com/rabbilyasar/envshield/actions/workflows/ci.yml/badge.svg)](https://github.com/rabbilyasar/envshield/actions/workflows/ci.yml)
[![PyPI version](https://badge.fury.io/py/envshield.svg)](https://pypi.org/project/envshield/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Downloads](https://static.pepy.tech/personalized-badge/envshield?period=month&units=international_system&left_color=grey&right_color=blue&left_text=Downloads)](https://pepy.tech/project/envshield)
[![Website](https://img.shields.io/badge/Website-envshield.dev-blue?logo=google-chrome&logoColor=white)](https://www.envshield.dev)
![Stars](https://img.shields.io/github/stars/rabbilyasar/envshield?style=social)

**Stop managing configuration like it's a secret. Start declaring it like it's code.**

EnvShield turns scattered `.env` files and configuration chaos into a single, **versioned contract** (`env.schema.toml`) that every service agrees on. One schema, multiple services, always in sync — with typed config code, automated onboarding, and zero-friction validation.

For teams that have:
- 🔀 Multiple services in one repo (API + web + worker)
- 😰 Config drift between local, staging, and production
- ⏰ Long onboarding where devs spend 2 hours figuring out what env vars they need
- 🤦 Silent failures because a typo in `os.getenv("DATABSE_URL")` returns `None`

**EnvShield is your answer.**

[📚 Full Documentation](https://docs.envshield.dev/) | [🌐 Website](https://www.envshield.dev)

---

## The Problem

**Configuration is chaos.**

Your project probably looks like this:
```
├── .env.example        (2 years out of date)
├── services/
│   ├── api/
│   │   ├── config/env_config.local.py
│   │   └── app/...
│   ├── web/
│   │   ├── .env.example  (missing DATABASE_URL, has dead vars)
│   │   └── src/...
│   └── worker/
│       └── ... (config docs are in a Slack thread)
```

**The pain:**
- New dev: "What env vars do I need?" → 2 hours of digging
- Refactoring `DB_HOST` to `DATABASE_HOST` → breaks 3 services before anyone notices
- `.env.example` drifts from reality → someone commits a secret, doesn't catch it, pushes to prod
- CI fails because `STRIPE_API_KEY` isn't in the staging secrets — nobody knows if it should be there
- Adding a new third-party integration → scattered across 5 config files with no central record

**EnvShield solves this by treating configuration as a contract, not a file.**

---

## The Solution: Configuration as Contract

One file, multiple services, single source of truth.

```bash
# Single command: read your existing configs
envshield import services/api/config/env_config.local.py --service api
envshield import services/web/.env --service web

# Now you have a contract
cat services/api/env.schema.toml
```

```toml
[DATABASE_URL]
description = "PostgreSQL connection string for the API"
secret = true

[API_PORT]
description = "Port the API listens on (dev: 5000, prod: 8000)"
secret = false
defaultValue = "5000"

[STRIPE_API_KEY]
description = "Stripe API key for payment processing"
secret = true
```

**What this gets you:**

1. **Typed config code** — Not raw strings, actual validated objects:
   ```python
   from config import env
   
   db = psycopg2.connect(env.DATABASE_URL)  # Type-checked, SecretStr (won't log)
   port = env.API_PORT  # Typed as int, validates on startup
   ```

2. **Sync everything** — All services agree on what config exists:
   ```bash
   envshield check services/api/config/.env --service api     # Is API's config valid?
   envshield scan --staged --service api                      # Are there secrets staged?
   envshield setup --service api                              # New dev? Interactive setup
   ```

3. **One source of truth** — Change the contract, everyone sees it:
   ```bash
   # Edit services/api/env.schema.toml
   envshield schema sync --service api    # Regenerates .env.example from the contract
   envshield doctor --service api         # Health check: is .env.example in sync?
   ```

---

## Quick Start

### 1. Install
```bash
pip install envshield
```

### 2. For a single service
```bash
cd my-project
envshield init                    # Create env.schema.toml + .env.example
envshield setup                   # Interactive setup for new devs
envshield generate --lang python  # Generate typed config.py (pydantic-settings)
```

### 3. For multi-service (the real win)
```bash
# At repo root with multiple services
cat envshield.yml
```
```yaml
project_name: my-platform
services:
  api:
    path: services/api/env.schema.toml
    description: Backend API
  web:
    path: services/web/env.schema.toml
    description: Frontend
```

```bash
# Now each command is service-aware
envshield import services/api/.env --service api
envshield import services/web/.env --service web
envshield scan --service api              # Scan API's code for undeclared vars
envshield setup --service web             # Setup web service
envshield setup                           # "Which service? (api / web / all)"
```

---

## Key Features

### ✅ One Schema, All Services
Declare what config each service needs in one place. Multi-service projects finally have a single source of truth.

**Real-life:** Your API added `STRIPE_API_KEY` last month. Did the worker service get it? Web service? With EnvShield, you know instantly.

### ✅ Migrate Existing Projects in Seconds
`envshield import` reads your actual `.env` or `settings.py` and generates 90% of the schema for you. No manual TOML writing.

```bash
envshield import .env                 # Reads your current .env
# → Generates env.schema.toml with:
#   - All variables auto-detected
#   - Secrets vs. non-secrets classified
#   - Default values extracted
envshield import --interactive        # Confirm each classification
```

### ✅ Typed, Validated Config Code
Stop using `os.getenv()`. Generate real, importable config modules:

```python
# Before (error-prone, untyped)
from os import getenv
db_url = getenv("DATABASE_URL")  # str | None, untyped
api_port = getenv("API_PORT", "5000")  # defaults as strings
stripe_key = getenv("STRIPE_API_KEY")  # visible in logs if leaked!

# After (type-safe, validated, secret-masked)
from config import env

db_url = env.DATABASE_URL    # Validated on startup, typed
api_port = env.API_PORT      # Validated as int, wrong type fails immediately
stripe_key = env.STRIPE_API_KEY  # SecretStr — won't appear in logs
```

For TypeScript:
```typescript
import { env } from './config';

const db = await postgres.connect(env.DATABASE_URL);  // Zod-validated, typed
const port: number = env.API_PORT;  // Type error if not number
```

### ✅ Interactive Onboarding
New developer? `envshield setup` walks them through creating `.env`, knows which vars are secrets, shows descriptions:

```
🛡️  EnvShield Setup
Which service? api

Please provide values for the following variables:

[DATABASE_URL]
PostgreSQL connection string for the API
Enter value (password): ••••••••••••

[API_PORT]
Port the API listens on (dev: 5000, prod: 8000)
Enter value: 5000

✓ Successfully created your .env file!
```

### ✅ Prevents Configuration Drift
Validate at every step:

```bash
envshield check services/api/.env --service api
# ✗ Missing in Local: STRIPE_API_KEY (required, no default)
# ✓ Extra in Local: DEBUG_MODE (not in schema — typo?)

envshield doctor --service api
# ✗ .env.example is out of sync with schema (5 new vars added)
# Suggestion: run `envshield schema sync --service api`
```

### ✅ Secret Scanning (Pre-commit Hook)
Blocks secrets before they're committed:

```bash
git add config.py  # Accidentally left DB_PASSWORD in it
envshield scan --staged
# 🚨 DANGER: Found 1 potential secret(s)!
# Line 5: DB_PASSWORD = 'postgres://...:secretpassword@...'
# Commit aborted.
```

---

## CLI Commands

| Command | What It Does | Real-Life Use |
|---|---|---|
| `envshield init` | Auto-detect framework, create schema & hook | Fresh project setup |
| `envshield import <file>` | Convert existing `.env` to schema | Adopting on existing project |
| `envshield check <file>` | Validate local env against schema | "Is my .env valid?" |
| `envshield scan [paths]` | Find secrets & undeclared variables | CI gate, pre-commit |
| `envshield setup` | Interactive onboarding wizard | New dev on the team |
| `envshield generate` | Compile schema into typed config code | Generate `config.py` or `config.ts` |
| `envshield schema sync` | Regenerate `.env.example` from schema | Keep docs fresh |
| `envshield doctor` | Health check your setup | "Is everything wired up?" |
| `envshield install-hook` | Install Git pre-commit hook | CI/security integration |

**All commands support `--service <name>` for multi-service projects.**

---

## Real-Life Example: A Multi-Service Monorepo

### Before EnvShield
```
services/
├── api/
│   ├── .env (has DATABASE_URL, missing STRIPE_API_KEY)
│   └── config/app_config.py (uses getenv, untyped)
├── web/
│   ├── .env.example (outdated, has LEGACY_VAR)
│   └── config/.env (fresh, correct)
└── worker/
    └── (no documented env vars at all)

Problem: Is DATABASE_URL the same across all three? Nobody knows.
New dev: "I set up my .env but the API won't start."
         → After 2 hours: missing STRIPE_API_KEY (only used in API, not documented)
```

### After EnvShield
```bash
# Phase 1: Import existing configs (30 seconds)
envshield import services/api/.env --service api
envshield import services/web/.env --service web
envshield import services/worker/config.py --service worker

# Now we have:
services/
├── api/env.schema.toml (5 variables, all documented)
├── web/env.schema.toml (5 variables, all documented)
└── worker/env.schema.toml (3 variables, all documented)

# Phase 2: Sync documentation (5 seconds)
envshield schema sync --service api
envshield schema sync --service web
envshield schema sync --service worker
# → .env.example files regenerated, always in sync

# Phase 3: Onboarding (2 minutes instead of 2 hours)
# New dev clones repo
envshield setup
# ? Which service? (api / web / worker / all)
#   → New dev runs through *all three* services interactively
#   → Gets descriptions of what each variable is for
#   → Passwords are prompted as hidden input
#   → Done

# Phase 4: Prevent drift
# Someone adds ANALYTICS_KEY to API mid-sprint
envshield scan --staged
# ✗ Undeclared variable: ANALYTICS_KEY (used in code, not in schema)
# Commit aborted. Update env.schema.toml first.

envshield doctor
# ✓ Configuration Files
# ✗ Example File Sync: api/.env.example missing ANALYTICS_KEY
# → Run `envshield schema sync --service api` to fix
```

---

## How It Compares

| Problem | EnvShield | Gitleaks | dotenvx | Infisical | direnv |
|---|---|---|---|---|---|
| Prevent secrets in commits | ✅ | ✅ Better detection | ✅ | ❌ (stores them) | ❌ |
| Validate .env against schema | ✅ Unique | ❌ | ✅ | ✅ | ❌ |
| Generate typed config code | ✅ Unique | ❌ | ❌ | ❌ | ❌ |
| Onboarding wizard | ✅ | ❌ | ❌ | ✅ Cloud-only | ❌ |
| Multi-service support | ✅ Built-in | ❌ | ❌ | ❌ | ❌ |
| Works offline | ✅ | ✅ | ✅ | ❌ | ✅ |
| Single source of truth | ✅ | ❌ | ❌ | ✅ Cloud | ❌ |

**The difference:** EnvShield is the only tool that treats configuration as a *contract* — one schema that drives docs, validation, onboarding, and code generation. Others solve pieces of the puzzle; EnvShield solves the whole problem.

---

## Roadmap

**Phase 1 (Free)** ✅ Live now
- Multi-service schema management
- Schema validation & syncing
- Typed config generation (Python + TypeScript)
- Secret scanning & pre-commit hook
- Interactive onboarding

**Phase 2 (Paid tier, coming soon)**
- Config profiles (dev/staging/prod schemas with environment-specific overrides)
- Schema diffing ("What changed between staging and prod?")
- CI/CD integration (validate config before deploy)
- Team workspaces & change notifications
- Auto-generated team documentation

**Phase 3 (Enterprise, future)**
- Cloud config backend (optional)
- Vault integration (pull from HashiCorp, AWS Secrets)
- Audit logs & advanced RBAC
- Policy engine (enforce naming conventions, require descriptions)
- Deployment automation

---

## Installation & Next Steps

```bash
pip install envshield
envshield --help

# Read the docs
open https://docs.envshield.dev/
```

---

## Community

Questions? Ideas? Found a bug?

- 🐙 [GitHub Issues](https://github.com/rabbilyasar/envshield/issues)
- 💬 [GitHub Discussions](https://github.com/rabbilyasar/envshield/discussions)
- 🌐 [Website](https://www.envshield.dev)
- 🐍 [PyPI](https://pypi.org/project/envshield/)

---

## License

MIT — Use it freely, in any project.

---

**TL;DR:** EnvShield stops configuration chaos. One schema, multiple services, always in sync. Migrate existing projects in seconds. Generate typed config code. Onboard devs in minutes, not hours. All free, all local, all in your git repo.
