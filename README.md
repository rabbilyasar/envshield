# EnvShield 🛡️

**Your Environment's First Line of Defense.**

Tired of the chaos of local development? Manually copying `.env` files, hunting for secrets on Slack, and constantly worrying if you just committed an API key? This messy workflow isn't just frustrating—it's a security risk waiting to happen.

**EnvShield brings order and security to your local environment.** It's a command-line tool that automates the entire setup process, making it simple, repeatable, and safe.

---

### **Table of Contents**

1.  [The Problem: The Silent Threat of Secret Leaks](#the-problem-the-silent-threat-of-secret-leaks)
2.  [Your Proactive Security Net](#your-proactive-security-net)
3.  [Streamlining Your Workflow](#streamlining-your-workflow)
4.  [Installation](#installation)
5.  [Core Concepts](#core-concepts)
6.  [Feature Guide & Demos](#feature-guide--demos)
    -   [`envshield init` - The Project Starter](#envshield-init---the-project-starter)
    -   [`envshield onboard` - The New Developer's Best Friend](#envshield-onboard---the-new-developers-best-friend)
    -   [`envshield list` & `use` - Daily Context Switching](#envshield-list--use---daily-context-switching)
    -   [`envshield template` - Preventing Configuration Drift](#envshield-template---preventing-configuration-drift)
    -   [`envshield scan` & `install-hook` - The Security Guard](#envshield-scan--install-hook---the-security-guard)
7.  [The `envshield.yml` File Explained](#the-envshieldyml-file-explained)
8.  [The Future of EnvShield: Roadmap](#the-future-of-envshield-roadmap)

---

## **The Problem: The Silent Threat of Secret Leaks**

Managing secrets and environment variables is more than just an inconvenience; it's a massive security vulnerability.

**The Stakes Are High:**

-   **A Constant Barrage:** Security researchers find that **over 1,000 unique, hardcoded secrets** are leaked to public GitHub repositories _every single day_.
-   **The Cost of a Mistake:** According to IBM's 2024 "Cost of a Data Breach" report, the average cost of a data breach is **$4.45 million**. A single leaked credential can be the starting point for a catastrophic attack.

**The Old Way is Broken:** Manually managing `.env` files, copying templates, and sharing secrets over Slack is a recipe for disaster. It's not a matter of _if_ a secret will be leaked, but _when_.

---

## **Your Proactive Security Net**

EnvShield is more than just a workflow tool; it's your first line of defense against accidental secret leaks.

### **Never Commit a Secret Again: `envshield scan` & `install-hook`**

**How it improves development:** It makes it virtually impossible to accidentally commit a plaintext secret. This single feature provides immense peace of mind and protects your company from potentially devastating security breaches.

---

## **Streamlining Your Workflow**

Beyond security, EnvShield eliminates the daily friction of managing complex environments.

### **The "Before & After"**

**Before EnvShield:** A new developer spends half a day hunting for secrets, manually copying files, and debugging a broken setup.

**With EnvShield:** A new developer runs one command, `envshield onboard dev`, follows a guided wizard, and has a fully configured, running application in minutes.

---

## **Installation**

To install EnvShield, you will need a Python environment (version 3.10+).

```bash
# Install from PyPI (once published)
pip install envshield

# Or, directly from GitHub
pip install git+[https://github.com/rabbilyasar/envshield.git](https://github.com/rabbilyasar/envshield.git)
```

Verify the installation

```
envshield --help
```

## Core Concepts

-   **Profiles**: A named "state" for your environment (e.g., `local-dev`, `test`).
-   **Links**: A profile is defined by a set of `links`. A link tells EnvShield to take a `source` file and make it available as a `target` file via a symlink.
-   **Templates**: A `template` is a version-controlled "master blueprint" for a configuration file.

## Feature Guide & Demos

`envshield init` - **The Project Starter**

This interactive wizard creates your initial `envshield.yml` file.

`envshield onboard` - **The New Developer's Best Friend**

A single command that takes a new developer from a fresh `git clone` to a fully configured, running application by creating files, prompting for secrets, and running setup scripts.

`envshield list` & `use` - **Daily Context Switching**

Your day-to-day commands for managing your environment. `list` shows your options, and `use` activates one.

`envshield template` - **Preventing Configuration Drift**

Commands (`check` and `sync`) to keep your project's "master blueprint" up-to-date.

`envshield scan` & `install-hook` - **The Security Guard**

-   `envshield scan`: Scans files for secrets.

-   `envshield install-hook`: Installs a Git pre-commit hook to automatically scan staged files before every commit, blocking the commit if secrets are found.

## The `envshield.yml` File Explained

This file is the heart of your configuration. It's designed to be simple enough for a small project but powerful enough for a complex monorepo.

**For a Simple Project**

If you just use a single `.env` file, your configuration is clean and minimal.

```yaml
project_name: my-cool-api
profiles:
    dev:
        description: 'For local development.'
        links:
            - source: .env.dev
              target: .env
              template: .env.template
```

**For a Complex Project**
This is where EnvShield's power shines. The `links` system allows you to manage multiple, distinct configuration files under a single profile, each with its own template.

```yaml
project_name: rocket_launcher
profiles:
    local-dev:
        description: 'Standard local development with Docker.'
        links:
            - source: config/env_config.dev.py
              target: config/env_config.py
              template: config/env_config.local.py
            - source: .env.local-dev
              target: .env
              template: .env.template
            - source: python.env.local-dev
              target: python.env
              template: python.env.template
        onboarding_prompts:
            - SECRET_KEY
        post_onboard_script: './tools/ctl.sh pre-deploy'
secret_scanning:
    exclude_files:
        - 'node_modules/*'
```

## The Future of EnvShield: Roadmap

Phase 1 provides a complete solution for local environment management. The future of EnvShield is focused on enhancing security, team collaboration, and intelligence.

**Phase 2: The Secure Team Enabler**
_This is where our paid "Team" tier begins, offering features for professional teams._

-   **Encrypted Files**: Safely commit encrypted secret files to your repository. `envshield use` will transparently decrypt them.

-   **Secure P2P Secret Sharing**: Securely share secrets with teammates directly from the command line, eliminating the need for insecure channels like Slack.

-   **The Global Vault**: Securely store and sync your personal, cross-project secrets (like your `GITHUB_TOKEN`) to a private GitHub repo, so you only have to enter them once.

-   `envshield doctor`: A comprehensive health check command.

**Phase 3: The Intelligent Ecosystem**

_This is where we build our "Enterprise" tier and make EnvShield an indispensable part of the development lifecycle._

-   **Shell & Git Integration**: Automatically load/unload environments as you `cd`.

-   **Deployment Bridge** (`export`): Export configurations for Docker, Kubernetes, etc.

-   **Deep IDE Integration**: Get real-time feedback directly in your code editor.

We welcome feedback and contributions to make EnvShield the best tool it can be!

## EnvShield 🛡️

**Your Environment's First Line of Defense.**

Tired of the chaos of local development? Manually copying `.env` files, hunting for secrets on Slack, and constantly worrying if you just committed an API key? This messy workflow isn't just frustrating—it's a security risk waiting to happen.

**EnvShield brings order and security to your local environment.** It's a command-line tool that automates the entire setup process, making it simple, repeatable, and safe.

![onboarding.gif](.gif/onboarding.gif)

---

## The Problem: The Silent Threat of Secret Leaks

Managing secrets and environment variables is more than just an inconvenience; it's a massive security vulnerability.

**The Stakes Are High**:

-   **A Constant Barrage**: Security researchers find that over 1,000 unique, hardcoded secrets are leaked to public GitHub repositories every single day.

-   **The Cost of a Mistake**: According to IBM's 2024 "Cost of a Data Breach" report, the average cost of a data breach is **$4.45 million**. A single leaked credential can be the starting point for a catastrophic attack.

-   **The Developer Toil**: A single leaked key doesn't just cost money; it costs developer time. The immediate aftermath involves a frantic scramble of revoking keys, auditing for unauthorized access, and deploying hotfixes, often derailing an entire team's sprint.

**The Old Way is Broken**: Manually managing .env files, copying templates, and sharing secrets over Slack is a recipe for disaster. It's not a matter of if a secret will be leaked, but when.

## Your Proactive Security Net

EnvShield is more than just a workflow tool; it's your first line of defense against accidental secret leaks. While a smooth setup prevents mistakes, EnvShield also provides an active shield that integrates directly into your Git workflow.

### **Never Commit a Secret Again: `envshield scan` & `install-hook`**

![commit-blocked.gif](.gif/commit-blocked.gif)

**Demo**:

```

# First, install the automatic hook once per project

$ envshield install-hook
✓ Git pre-commit hook installed successfully!

# Now, try to commit a file with a secret in it

$ git add config.py
$ git commit -m "Add new feature"

🛡️ Running EnvShield Secret Scanner...
Scanning staged files...

🚨 DANGER: Found 1 potential secret(s)!
╭────────────────── Secret Scan Results ───────────────────╮
│ File │ Line │ Secret Type │ Line Content │
├────────────┼──────┼──────────────┼───────────────────────┤
│ config.py │ 42 │ Stripe API Key │ STRIPE_KEY="sk_test..." │
╰────────────┴──────┴──────────────┴───────────────────────╯

Please remove these secrets from your files before committing.

# The commit is automatically ABORTED.

```

**How it improves development:** It makes it virtually impossible to accidentally commit a plaintext secret. This single feature provides immense peace of mind and protects your company from potentially devastating security breaches.

## Streamlining Your Workflow

Beyond security, EnvShield eliminates the daily friction of managing complex environments.

#### **The "Before & After"**

**Before EnvShield**: A new developer spends half a day hunting for secrets, manually copying files, and debugging a broken setup.

**With EnvShield**: A new developer runs one command, envshield onboard dev, follows a guided wizard, and has a fully configured, working application in minutes.

#### **Feature Demo & User Guide**

Here’s a detailed look at every command and how it improves your development workflow.

`envshield init` - **The Project Starter**

This is the very first command a project maintainer will run. It's an interactive wizard that creates the initial envshield.yml file.

![envshield-init.gif](.gif/envguard-init.gif)

**How it improves development**: It provides a simple, guided entry point for adding EnvShield to new or existing projects, creating a basic configuration that you can then expand upon.

`envshield onboard` - **The New Developer's Best Friend**

This is the killer feature for team productivity. It's a single command that takes a new developer from a fresh git clone to a fully configured, running application.

![onboarding.gif](.gif/onboarding.gif)

**How it improves development**: It dramatically reduces onboarding time, eliminates setup errors, and ensures every developer starts with a consistent, correct configuration.

`envshield list & use` - **Daily Context Switching**

These are your day-to-day commands for managing your environment. list shows you your options, and use activates one.

![workflow.gif](.gif/workflow.gif)

**How it improves development**: It turns the complex, manual process of changing configurations for different tasks (e.g., local coding vs. running tests) into a single, instant, and error-free command.

`envshield template` - **Preventing Configuration Drift**

These commands are your tools for keeping your project's "master blueprint" up-to-date.

![drift-fix.gif](.gif/drift-fix.gif)

**How it improves development**: It makes maintaining your configuration templates effortless. This prevents bugs caused by developers having outdated environments and ensures your documentation is never out of sync with reality.

```yaml
#### The `envshield.yml` File in Detail

This file is the heart of your configuration. Here's a breakdown of all the keys:

# The name of your project (used for display purposes).
project_name: my-project

# The configuration schema version.
version: 1.3

# A dictionary of all your environment profiles.
profiles:
    # The name of your profile (e.g., 'local-dev').
    local-dev:
        # A helpful description shown in `envshield list`.
        description: 'For daily development.'

        # A list of source-to-target file mappings.
        links:
            - source: .env.dev
              target: .env
              template: .env.template # Template is specific to this link
            - source: config.dev.py
              target: config.py
              template: config.template.py

        # A list of variables that `envshield onboard` should always prompt for,
        # even if they have a default value in the template.
        onboarding_prompts:
            - SECRET_KEY
            - API_TOKEN

        # A script (or list of scripts) to run BEFORE the onboarding process.
        # Perfect for checking dependencies like Docker.
        pre_onboard_script: './tools/check_deps.sh'

        # A script (or list of scripts) to run AFTER a successful onboard.
        # Perfect for running migrations or seeding a database.
        post_onboard_script:
            - 'npm install'
            - './tools/db_migrate_and_seed.sh'
```

### Roadmap

Phase 1 provides a complete solution for local environment management. The future of EnvShield is focused on enhancing security and team collaboration.

Phase 2: The Secure Team Enabler

-   Encrypted Files: Safely commit encrypted secret files to your repository.
-   Secure P2P Secret Sharing: Securely share secrets with teammates directly from the command line.
-   Environment Health Check (envshield doctor): A comprehensive diagnostic command.

Phase 3: The Intelligent Ecosystem

-   Shell Integration: Automatically load/unload environments as you cd.
-   Deployment Bridge (envshield export): Export configurations for Docker, Kubernetes, etc.
-   Deep IDE Integration: Get real-time feedback directly in your code editor.

We welcome feedback and contributions to make EnvShield the best tool it can be!
