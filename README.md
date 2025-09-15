
# EnvShield 🛡️ – Environment Variable Management & Secret Scanner CLI


[![CI](https://github.com/rabbilyasar/envshield/actions/workflows/ci.yml/badge.svg)](https://github.com/rabbilyasar/envshield/actions/workflows/ci.yml)
[![PyPI version](https://badge.fury.io/py/envshield.svg)](https://pypi.org/project/envshield/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Downloads](https://static.pepy.tech/personalized-badge/envshield?period=month&units=international_system&left_color=grey&right_color=blue&left_text=Downloads)](https://pepy.tech/project/envshield)
[![Website](https://img.shields.io/badge/Website-envshield.dev-blue?logo=google-chrome&logoColor=white)](https://www.envshield.dev)
![Stars](https://img.shields.io/github/stars/rabbilyasar/envshield?style=social)

**EnvShield is an open-source CLI for environment variable management, configuration as code, and proactive secret scanning—your caffeine-proof way to avoid the “oops, I just leaked prod keys” nightmare.**

[📚 Full Documentation](https://docs.envshield.dev/)


### Table of Contents

1. [Why Secure Environment Management Matters](#why-secure-environment-management-matters)
2. [Key Features of the EnvShield CLI](#key-features-of-the-envshield-cli)
3. [Installation](#installation)

4. [The EnvShield Philosophy: Schema-First Configuration](#the-envshield-philosophy-schema-first-configuration)
5. [CLI Commands](#cli-commands)

6. [Competitor Comparison: Choosing the Right Tool](#competitor-comparison-choosing-the-right-tool)

7. [The Brains of the Operation: The Core Files](#the-brains-of-the-operation-the-core-files)

8. [Future Roadmap: Teams & Enterprise](#future-roadmap-teams--enterprise)

9. [Community & Support](#community--support)
10. [TL;DR](#tldr)

## Why Secure Environment Management Matters

Managing `.env` files by copy-pasting them around like a hot potato is fun… until a secret lands in a public repo.  
EnvShield solves the “dotenv dumpster fire” by giving you **schema-first configuration**, automatic documentation, and built-in **secret scanning**—all wrapped in a workflow lazy enough for a 3 a.m. commit. 


## Key Features of the EnvShield CLI

![Demo of EnvShield scanning secrets](.gif/scan.gif)

-   **Environment Variable Governance** – One `env.schema.toml` file becomes your single source of truth.
    
-   **Open-Source Secret Scanner** – Detects API keys, private keys, and other credentials _before_ you push.
    
-   **Local Development Workflow** – Automatic `.env.example` syncing and onboarding that even future-you will thank you for.
    
-   **Git Pre-commit Hook** – Blocks secret leaks faster than you can say `git push --force`.
    
-   **Configuration as Code** – Your environment config is version-controlled and documented like real code.

_(Translation: EnvShield is that overprotective friend who checks the door lock five times so you can sleep.)_

## Installation
Requires **Python 3.10+**.
```bash
pip install envshield
envshield --help
```
 Done. Your project is now 72 % less combustible.

## The EnvShield Philosophy: Schema-First Configuration

EnvShield's power comes from a simple idea: your configuration should be treated like code. It introduces a single source of truth, the `env.schema.toml` file.

This file is a **"configuration contract"** that explicitly defines every environment variable your project needs. By defining your variables here, you get:

-   **Automated Documentation**: Your `.env.example` is always perfectly in sync with your schema.
-   **Ironclad Validation**: Catch typos and missing variables before you even run your app.
-   **Proactive Security**: A built-in scanner and Git hook **prevent secrets from ever being committed**.

## CLI Commands

| Command | Purpose | Demo |
|---|---|---|
| `envshield init` | Auto-detects framework, creates env.schema.toml, installs the Git hook. | ![Demo of EnvShield init](.gif/init.gif) |
| `envshield scan` | Scans files or staged commits for secrets. | ![Demo of EnvShield scan](.gif/scan.gif) |
| `envshield install-hook` | Manually install or update the Git pre-commit hook. | For when you skipped step one because YOLO. |
| `envshield check <file>` | Validates a local .env file against the schema. | ![Demo of EnvShield check](.gif/check.gif) |
| `envshield schema sync` | Regenerates .env.example from the schema. | ![Demo of EnvShield sync](.gif/sync.gif) |
| `envshield setup` | Interactive onboarding to create a local .env. | ![Demo of EnvShield setup](.gif/setup.gif) |
| `envshield doctor` | Runs a full health check (and can auto-fix). | ![Demo of EnvShield check](.gif/check.gif) |


## Competitor Comparison: Choosing the Right Tool

A scanner is a smoke detector. A cloud vault is an off-site bank. **EnvShield is the fireproof, self-organizing house you should have been living in all along.** It provides the complete local workflow that developers need to prevent secret leaks in the first place.

| **Developer Pain Point**              | **EnvShield** 🛡️                                                                                             | **TruffleHog / Gitleaks**                                        | **Doppler / Infisical**                                                                 | **`direnv`**                      |
| :------------------------------------ | :----------------------------------------------------------------------------------------------------------- | :--------------------------------------------------------------- | :-------------------------------------------------------------------------------------- | :-------------------------------- |
| **Preventing Secret Commits**         | ✅ **Built-in**. `init` sets up an automated pre-commit hook.                                                | ✅ **Core feature**. Specialized tools for just finding secrets. | ❌ **Indirectly**. Doesn't actively scan commits.                                       | ❌ **Not addressed.**             |
| **Streamlining Developer Onboarding** | ✅ **Automated**. The `setup` command interactively creates a local `.env` file from the project's template. | ❌ **Not addressed.**                                            | ✅ **Strong**. Provides a central place to get secrets, but doesn't manage local files. | ❌ **Not addressed.**             |
| **Preventing Configuration Drift**    | ✅ **Solved**. The schema is the source of truth. `schema sync` and `check` enforce consistency.             | ❌ **Not addressed.**                                            | ✅ **Solved**. The cloud is the single source of truth.                                 | ❌ **Not addressed.**             |
| **Primary Focus**                     | **Complete Local Workflow**. Manages files, documents schemas, validates setups, and scans for leaks.        | **Secret Detection Engine.**                                     | **Cloud-Based Secret** Vault.                                                           | **Shell Environment Automation.** |

Think of scanners as smoke detectors and cloud vaults as off-site banks.  
**EnvShield is the fire-proof, self-organizing house you should have been living in all along.**

## The Brains of the Operation: The Core Files

`envshield` is managed by two simple files you commit to your repository.

-   `env.schema.toml`: The source of truth. This is where you define every variable your project needs.

```
    # env.schema.toml

    [DATABASE_URL]
    description = "The full connection string for the PostgreSQL database."
    secret = true # Marks this as sensitive

    [LOG_LEVEL]
    description = "Controls the application's log verbosity."
    secret = false
    defaultValue = "info" # Provides a fallback
```

-   `envshield.yml`: The workflow config. In Phase 1, it's very simple and mainly points to your schema and defines scanner exclusions.

## Future Roadmap: Teams & Enterprise✨

Phase 1 is the free, powerful "Local Guardian." But the journey doesn't end there. Upcoming paid tiers will turn `envshield` into a complete collaboration and automation platform.

### Phase 2: The Team Collaborator (Paid Tier)

-   `envshield use <profile>`: Instantly switch your entire project's configuration between different environments (e.g., `local`, `staging`).

-   `envshield onboard <profile>`: A supercharged `setup` that can also run scripts like` docker compose up` and database migrations for a true one-command setup.

-   `envshield share`: Securely share a secret with a teammate via an encrypted, one-time-use link.

-   `envshield docs`: Generate beautiful Markdown or HTML documentation from your schema.

### Phase 3: The Enterprise-Grade System (Paid Tier)

-   `envshield login`, `pull`, `push`: Full integration with a centralized, cloud-based secret vault.

-   `envshield export`: Securely inject secrets into your CI/CD pipelines for automated deployments.

-   **Audit Logs & RBAC**: A complete, compliant, and auditable history of all secret access and team permissions, managed through a web dashboard.

## **Community & Support**

Got questions? Have a brilliant idea? Come hang out with us!

-   🤔 **Ask a question on GitHub Discussions:**[Discussions](https://github.com/rabbilyasar/envshield/discussions/)

Or, Follow us on our socials:

## 🌍 Community & Links

-   🌐 Website: [envshield.dev](https://www.envshield.dev)
-   🐙 GitHub: [rabbilyasar/envshield](https://github.com/rabbilyasar/envshield)
-   🐍 PyPI: [EnvShield on PyPI](https://pypi.org/project/envshield/)
-   🤔 GitHub Discussions: [GitHub Discussions](https://github.com/rabbilyasar/envshield/discussions)
-   💬 Join our Discord:[@discord](https://discord.gg/dSEbvPW57N)

## **Contributing (Don't Be Shy)**

Spotted a bug? Think our jokes are terrible? We want to hear it all. Check out `CONTRIBUTING.md` to get started.

### TL;DR

**EnvShield = environment variable management + secret scanning + configuration as code + just enough sarcasm to keep you awake.**  
Stop leaking secrets. Start shipping securely.
