## EnvGuard 🛡️

**Your Environment's First Line of Defense.**

Tired of the chaos of local development? Manually copying `.env` files, hunting for secrets on Slack, and constantly worrying if you just committed an API key? This messy workflow isn't just frustrating—it's a security risk waiting to happen.

**EnvGuard brings order and security to your local environment.** It's a command-line tool that automates the entire setup process, making it simple, repeatable, and safe.

![onboarding.gif](onboarding.gif)

---

## The Problem: The Silent Threat of Secret Leaks

Managing secrets and environment variables is more than just an inconvenience; it's a massive security vulnerability.

**The Stakes Are High**:

-   **A Constant Barrage**: Security researchers find that over 1,000 unique, hardcoded secrets are leaked to public GitHub repositories every single day.

-   **The Cost of a Mistake**: According to IBM's 2024 "Cost of a Data Breach" report, the average cost of a data breach is $4.45 million. A single leaked credential can be the starting point for a catastrophic attack.

-   **The Developer Toil**: A single leaked key doesn't just cost money; it costs developer time. The immediate aftermath involves a frantic scramble of revoking keys, auditing for unauthorized access, and deploying hotfixes, often derailing an entire team's sprint.

**The Old Way is Broken**: Manually managing .env files, copying templates, and sharing secrets over Slack is a recipe for disaster. It's not a matter of if a secret will be leaked, but when.

## Your Proactive Security Net

EnvGuard is more than just a workflow tool; it's your first line of defense against accidental secret leaks. While a smooth setup prevents mistakes, EnvGuard also provides an active shield that integrates directly into your Git workflow.

### **Never Commit a Secret Again: `envguard scan` & `install-hook`**

![commit-blocked.gif](commit-blocked.gif)

**Demo**:

```
# First, install the automatic hook once per project
$ envguard install-hook
✓ Git pre-commit hook installed successfully!

# Now, try to commit a file with a secret in it
$ git add config.py
$ git commit -m "Add new feature"

🛡️  Running EnvGuard Secret Scanner...
Scanning staged files...

🚨 DANGER: Found 1 potential secret(s)!
╭────────────────── Secret Scan Results ───────────────────╮
│ File       │ Line │ Secret Type  │ Line Content          │
├────────────┼──────┼──────────────┼───────────────────────┤
│ config.py  │ 42   │ Stripe API Key │ STRIPE_KEY="sk_test..." │
╰────────────┴──────┴──────────────┴───────────────────────╯

Please remove these secrets from your files before committing.

# The commit is automatically ABORTED.
```

**How it improves development:** It makes it virtually impossible to accidentally commit a plaintext secret. This single feature provides immense peace of mind and protects your company from potentially devastating security breaches.

## Streamlining Your Workflow

Beyond security, EnvGuard eliminates the daily friction of managing complex environments.

#### **The "Before & After"**

**Before EnvGuard**: A new developer spends half a day hunting for secrets, manually copying files, and debugging a broken setup.

**With EnvGuard**: A new developer runs one command, envguard onboard dev, follows a guided wizard, and has a fully configured, working application in minutes.

#### **Feature Demo & User Guide**

Here’s a detailed look at every command and how it improves your development workflow.

`envguard init` - **The Project Starter**

This is the very first command a project maintainer will run. It's an interactive wizard that creates the initial envguard.yml file.

![envguard-init.gif](envguard-init.gif)

**How it improves development**: It provides a simple, guided entry point for adding EnvGuard to new or existing projects, creating a basic configuration that you can then expand upon.

`envguard onboard` - **The New Developer's Best Friend**

This is the killer feature for team productivity. It's a single command that takes a new developer from a fresh git clone to a fully configured, running application.

![onboarding.gif](onboarding.gif)

**How it improves development**: It dramatically reduces onboarding time, eliminates setup errors, and ensures every developer starts with a consistent, correct configuration.

`envguard list & use` - **Daily Context Switching**

These are your day-to-day commands for managing your environment. list shows you your options, and use activates one.

![workflow.gif](workflow.gif)

**How it improves development**: It turns the complex, manual process of changing configurations for different tasks (e.g., local coding vs. running tests) into a single, instant, and error-free command.

`envguard template` - **Preventing Configuration Drift**

These commands are your tools for keeping your project's "master blueprint" up-to-date.

![drift-fix.gif](drift-fix.gif)

**How it improves development**: It makes maintaining your configuration templates effortless. This prevents bugs caused by developers having outdated environments and ensures your documentation is never out of sync with reality.

```yaml
#### The `envguard.yml` File in Detail

This file is the heart of your configuration. Here's a breakdown of all the keys:

# The name of your project (used for display purposes).
project_name: my-project

# The configuration schema version.
version: 1.3

# A dictionary of all your environment profiles.
profiles:
    # The name of your profile (e.g., 'local-dev').
    local-dev:
        # A helpful description shown in `envguard list`.
        description: 'For daily development.'

        # A list of source-to-target file mappings.
        links:
            - source: .env.dev
              target: .env
              template: .env.template # Template is specific to this link
            - source: config.dev.py
              target: config.py
              template: config.template.py

        # A list of variables that `envguard onboard` should always prompt for,
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

Phase 1 provides a complete solution for local environment management. The future of EnvGuard is focused on enhancing security and team collaboration.

    Phase 2: The Secure Team Enabler

        Encrypted Files (sops-like): Safely commit encrypted secret files to your repository.

        Secure P2P Secret Sharing (age-like): Securely share secrets with teammates directly from the command line.

        Environment Health Check (envguard doctor): A comprehensive diagnostic command.

    Phase 3: The Intelligent Ecosystem

        Shell Integration (direnv-like): Automatically load/unload environments as you cd.

        Deployment Bridge (envguard export): Export configurations for Docker, Kubernetes, etc.

        Deep IDE Integration: Get real-time feedback directly in your code editor.

We welcome feedback and contributions to make EnvGuard the best tool it can be!
