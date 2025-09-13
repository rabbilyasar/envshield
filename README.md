
# EnvShield 🛡️

[![CI](https://github.com/rabbilyasar/envshield/actions/workflows/ci.yml/badge.svg)](https://github.com/rabbilyasar/envshield/actions/workflows/ci.yml)
[![PyPI version](https://badge.fury.io/py/envshield.svg)](https://pypi.org/project/envshield/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Downloads](https://static.pepy.tech/personalized-badge/envshield?period=month&units=international_system&left_color=grey&right_color=blue&left_text=Downloads)](https://pepy.tech/project/envshield)
[![Website](https://img.shields.io/badge/Website-envshield.dev-blue?logo=google-chrome&logoColor=white)](https://www.envshield.dev)
![Stars](https://img.shields.io/github/stars/rabbilyasar/envshield?style=social)

**Stop setting your project on fire 🔥. Let's talk about your environment.**

![Scan GIF](.gif/scan.gif)

Ever had that 3 AM, caffeine-fueled moment of panic 😱, wondering if you just pushed the entire company's AWS account to a public repo? I've definitely been there.

EnvShield is the command-line co-pilot that saves you from yourself. It automates the chaotic ritual of managing environment variables, documents your configuration for you, and acts as the slightly over-attached guardian angel for your secrets.

### Table of Contents

1. [Why Bother? (The Problem We All Ignore)](#why-bother-the-problem-we-all-ignore)
    
2. [The EnvShield Philosophy: Schema-First Configuration](#the-envshield-philosophy-schema-first-configuration)
    
3. [How EnvShield is Different (Competitor Smackdown)](#how-envshield-is-different-competitor-smackdown)
    
4. [Installation](#installation)
    
5. [The Spellbook: A Guide to the Commands](#the-spellbook-a-guide-to-the-commands)
    
6. [The Brains of the Operation: The Core Files](#the-brains-of-the-operation-the-core-files)
    
7. [The Future: Teams & Enterprise](#the-future-teams--enterprise)
    
8. [Community & Contributing](#community--contributing)
    

## Why Bother? (The Problem We All Ignore)

Let's be honest, managing environment configuration is often a dumpster fire 🔥. It's a mess of manually copied `.env` files, outdated `.env.example` templates, and that one Slack DM with the production key that you really hope nobody finds.

This isn't just annoying; it's how disasters happen. A single leaked key can cost millions. EnvShield exists to make the secure and documented way the easiest and laziest way.

## The EnvShield Philosophy: Schema-First Configuration

EnvShield's power comes from a simple idea: your configuration should be treated like code. It introduces a single source of truth, the `env.schema.toml` file.

This file is a **contract** that explicitly defines every environment variable your project needs. By defining your variables here, you get:

- **Automated Documentation**: Your `.env.example` is always perfectly in sync.
    
- **Ironclad Validation**: Catch typos and missing variables before you run your app.
    
- **Proactive Security**: A built-in scanner and Git hook prevent secrets from ever being committed.
    

## How EnvShield is Different (Competitor Smackdown)

A scanner is a smoke detector. A cloud vault is an off-site bank. **EnvShield is the fireproof, self-organizing house you should have been living in all along.** It provides the complete local workflow that developers need.

|                               |                                                                                                       |                                                            |                                                                                          |                                   |
| ----------------------------- | ----------------------------------------------------------------------------------------------------- | ---------------------------------------------------------- | ---------------------------------------------------------------------------------------- | --------------------------------- |
| **Developer Pain Point**      | **EnvShield** 🛡️                                                                                     | **TruffleHog / Gitleaks**                                  | **Doppler / Infisical**                                                                  | **direnv**                        |
| **Preventing Secret Commits** | ✅ **Built-in**. The **init** command sets up an automated pre-commit hook.                            | ✅ **Core feature**. Specialized tools for finding secrets. | ❌ **Indirectly**. Encourages not having secret files, but doesn't actively scan commits. | ❌ **Not addressed.**              |
| **New Developer Setup**       | ✅ Automated. The `setup` command interactively creates a local `.env` file from the example.          | ❌ **Not addressed.**                                       | ✅ **Strong**. Provides a central place to get secrets, but doesn't manage local files.   | ❌ **Not addressed.**              |
| **Handling Config Drift**     | ✅ **Solved**. The schema is the source of truth. `schema sync` and `check` enforce consistency.       | ❌ **Not addressed.**                                       | ✅ **Solved**. The cloud is the single source of truth.                                   | ❌ **Not addressed.**              |
| **Primary Focus**             | **Complete Local Workflow**. Manages files, documents schemas, validates setups, and scans for leaks. | **Secret Detection Engine.**                               | **Cloud-Based Secret** Vault.                                                            | **Shell Environment Automation.** |

## Installation

You'll need Python 3.10+.

```
pip install envshield
```
  

Check if the magic worked: `envshield --help`

## The Spellbook: A Guide to the Commands

These are the core commands for the free, local-only Phase 1.

### `envshield init`

The "zero-to-hero" command. Run this first in a new or existing project.

- **What it does:** Intelligently inspects your project to detect the framework (e.g., Next.js, Python/Django) and scaffolds a complete, best-practice configuration foundation. It creates your `env.schema.toml` with smart defaults, updates your `.gitignore`, and automatically installs the security hook.

- **Flags**:

- `--force` / `-f`: Overwrites existing EnvShield files. It will ask for confirmation before nuking your setup.

- **Real-Life Example**: You're starting a new Django project. You run `envshield init`. The tool detects Django, creates a schema with `SECRET_KEY` and `DATABASE_URL`, and installs the security hook. Your project is set up for success in one command.

### `envshield scan`

Your project's personal bodyguard. It scans files for hardcoded secrets.

- **What it does**: Uses a comprehensive list of patterns to find things that look like API keys, private keys, and other credentials.

- **Arguments & Flags**:

- `[PATHS]...`: The specific files or directories to scan. Defaults to the current directory.

	- **Use Case**: You've been working on a new module and want to be extra careful: `envshield scan src/billing/`

- `--staged`: Scans only the files you've staged for your next Git commit. This is the heart of the pre-commit hook.
    
	- **Use Case**: This is run automatically by the hook every time you `git commit`. If you accidentally staged a file with a secret, the commit is blocked, saving you from a very bad day.

- `--config <file>`: Use a different `envshield.yml` for this specific scan.

	- **Use Case:** Your CI/CD pipeline needs to run a scan but should ignore test files. You run `envshield scan . --config .github/ci.envshield.yml` to use a special configuration just for that run.

### `envshield install-hook`

Manually installs the Git pre-commit hook if you skipped it during `init` or if your project had an existing hook.

- **What it does**: Creates a `pre-commit` script in your `.git/hooks/` directory that runs `envshield scan --staged`.
    
- **Real-Life Example**: You've added `envshield` to a project that already uses a code formatting hook. You can manually merge the two scripts and then run `envshield install-hook --force` to create the combined hook.

### `envshield check <file>`

The "is it plugged in?" command for your local setup.

- **What it does**: Validates a local environment file (e.g., `.env`) against the official contract in `env.schema.toml`. It reports missing or extra variables, intelligently ignoring variables that have a `defaultValue` in the schema.
    
- **Real-Life Example**: Your app fails to start after a teammate's PR. You run `envshield check`. The tool reports `Missing in Local: NEW_SERVICE_API_KEY`, instantly telling you what's wrong.
    

### `envshield schema sync`

The project librarian. It ensures your public-facing documentation is never out of date.

- **What it does:** Reads your `env.schema.toml` and generates a perfect `.env.example` file, complete with comments from your variable descriptions.
    
- **Real-Life Example**: You add a new `REDIS_URL` variable to your schema. Instead of manually updating the example file, you just run `envshield schema sync`. The `.env.example` is instantly and correctly updated, ready to be committed.
    

### `envshield setup`

A taste of the automated onboarding magic.

![Setuf GIF](.gif/setup.gif)

- **What it does**: The perfect command for getting started. It reads your `.env.example` file, finds any variables without a value, and interactively prompts you for them. It then generates your first `.env` file.
    
- **Real-Life Example**: You just cloned a new project. You run `envshield setup`. The tool asks you for the `DATABASE_URL` and `STRIPE_API_KEY`, then generates your fully-populated `.env` file. You are ready to run the project in minutes.

### `envshield doctor`

The "turn it off and on again" command for your entire configuration.

![Doctor GIF](.gif/doctor.gif)

- **What it does**: Runs a comprehensive suite of health checks on your project's `envshield` setup. It checks for missing config files, validates your local environment against the schema, ensures your `.env.example` is in sync, and verifies that the security hook is installed correctly.
    
- **Flags**:
    
    - `--fix`: The magic wand. If the doctor finds a problem (like a missing hook or an out-of-date example file), it will interactively ask you if you want to fix it automatically.
        
- **Real-Life Example**: Something just feels wrong with your setup. You run `envshield doctor`. It reports that your `.env.example` is out of date and the Git hook is missing. You run `envshield doctor --fix`, answer "Yes" to both prompts, and the tool fixes everything for you.

## The Brains of the Operation: The Core Files

`envshield` is managed by two simple files you commit to your repository.

- `env.schema.toml`: The source of truth. This is where you define every variable your project needs.

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

- `envshield.yml`: The workflow config. In Phase 1, it's very simple and mainly points to your schema and defines scanner exclusions.
    

## The Future: Teams & Enterprise ✨

Phase 1 is the free, powerful "Local Guardian." But the journey doesn't end there. Upcoming paid tiers will turn `envshield` into a complete collaboration and automation platform.

### Phase 2: The Team Collaborator (Paid Tier)

- `envshield use <profile>`: Instantly switch your entire project's configuration between different environments (e.g., `local`, `staging`).
    
- `envshield onboard <profile>`: A supercharged `setup` that can also run scripts like` docker compose up` and database migrations for a true one-command setup.
    
- `envshield share`: Securely share a secret with a teammate via an encrypted, one-time-use link.
    
- `envshield docs`: Generate beautiful Markdown or HTML documentation from your schema.
    
### Phase 3: The Enterprise-Grade System (Paid Tier)

- `envshield login`, `pull`, `push`: Full integration with a centralized, cloud-based secret vault.
    
- `envshield export`: Securely inject secrets into your CI/CD pipelines for automated deployments.
    
- **Audit Logs & RBAC**: A complete, compliant, and auditable history of all secret access and team permissions, managed through a web dashboard.

## **Community & Support**

Got questions? Have a brilliant idea? Come hang out with us!

* 🤔 **Ask a question on GitHub Discussions:**[Discussions](https://github.com/rabbilyasar/envshield/discussions/)

Or, Follow us on our socials:
## 🌍 Community & Links

- 🌐 Website: [envshield.dev](https://www.envshield.dev)  
- 🐙 GitHub: [rabbilyasar/envshield](https://github.com/rabbilyasar/envshield)  
- 🐍 PyPI: [EnvShield on PyPI](https://pypi.org/project/envshield/)  
- 🤔 GitHub Discussions: [GitHub Discussions](https://github.com/rabbilyasar/envshield/discussions)  
- 💬 Join our Discord:[@discord](https://discord.gg/dSEbvPW57N)  
## **Contributing (Don't Be Shy)**

Spotted a bug? Think our jokes are terrible? We want to hear it all. Check out `CONTRIBUTING.md` to get started.