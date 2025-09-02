# **EnvShield 🛡️**

[![CI](https://github.com/rabbilyasar/envshield/actions/workflows/ci.yml/badge.svg)](https://github.com/rabbilyasar/envshield/actions/workflows/ci.yml) 
[![PyPI version](https://badge.fury.io/py/envshield.svg)](https://pypi.org/project/envshield/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Downloads](https://static.pepy.tech/personalized-badge/envshield?period=month&units=international_system&left_color=grey&right_color=blue&left_text=Downloads)](https://pepy.tech/project/envshield)
[![Website](https://img.shields.io/badge/Website-envshield.dev-blue?logo=google-chrome&logoColor=white)](https://www.envshield.dev)

![Stars](https://img.shields.io/github/stars/rabbilyasar/envshield?style=social)

**Stop setting your project on fire. Let's talk about your .env files.**

Ever had that 3 AM, caffeine-fueled moment of panic 😱, wondering if you just pushed the entire company's AWS account to a public repo? We've all been there.

EnvShield is the command-line companion that saves you from yourself. It automates the chaotic ritual of managing environment variables and acts as the slightly over-attached guardian angel for your secrets.

![scan](.gif/scan.gif)

### **Table of Contents**

1. [Why Bother? (The Problem We All Ignore)](#why-bother-the-problem-we-all-ignore)  
2. [How EnvShield is Different ](#how-envshield-is-different)  
3. [The EnvShield Experience](#the-envshield-experience)  
4. [Installation (The Easy Part)](#installation-the-easy-part)  
5. [The Spellbook: A Guide to the Commands](#the-spellbook-a-guide-to-the-commands)  
6. [The Brain of the Operation: envshield.yml](#the-brain-of-the-operation-envshieldyml)  
7. [The Future: Now With More Lazers (and Encryption)](#the-future-now-with-more-lazers-and-encryption)  
8. [Community & Support](#community--support)  
9. [Contributing (Don't Be Shy)](#contributing)

## **Why Bother? (The Problem We All Ignore)**

Let's be honest, managing secrets is a dumpster fire 🔥. It's a mess of manually copied files, outdated templates, and that one Slack DM with the production key that you *really* hope nobody finds.

This isn't just annoying; it's how disasters happen.

* **The Daily Leak:** Over **1,000 unique secrets** are pushed to public GitHub repositories *every single day* ([Source: GitGuardian](https://www.google.com/search?q=https://www.gitguardian.com/state-of-secrets-sprawl-report)).  
* **The "Oops" Button Costs Millions:** The average cost of a data breach is now **$4.45 million USD** ([Source: IBM's 2023 Report](https://www.ibm.com/reports/data-breach)). A single leaked key can be the starting point.

EnvShield exists to make the secure way the *easiest* and *laziest* way.

## **How EnvShield is Different**

EnvShield is designed to be a complete solution for the *entire* local development lifecycle. Here’s a breakdown of how it compares to other popular tools when tackling common developer pain points.

| Developer Pain Point / Workflow | EnvShield 🛡️ | TruffleHog / Gitleaks | Doppler / Infisical | direnv |
| :---- | :---- | :---- | :---- | :---- |
| **New Developer Onboarding** | ✅ **Automated.** onboard command creates files from templates and interactively populates secrets. | ❌ **Not addressed.** Focuses only on scanning, not setup. | ✅ **Strong.** Provides a central place for new devs to get secrets, but doesn't manage local files. | ❌ **Not addressed.** Assumes .envrc file already exists. |
| **Switching Local Contexts** (e.g., dev vs. test) | ✅ **Automated.** use command instantly switches the state of multiple config files. | ❌ **Not addressed.** Not a workflow management tool. | ✅ **Strong.** doppler run injects the correct secrets for a specific command. | ✅ **Automated.** Seamlessly loads/unloads shell variables on cd. Does not manage files. |
| **Preventing Secret Commits** | ✅ **Built-in.** Provides a pre-commit hook that scans staged files before every commit. | ✅ **Core feature.** Also provides pre-commit hooks and CI scanning. | ❌ **Indirectly.** Encourages not having local secret files, but doesn't actively scan commits. | ❌ **Not addressed.** Can load secret files, increasing the risk if not gitignored. |
| **Handling Configuration Drift** | ✅ **Built-in.** template check and sync commands find and fix inconsistencies. | ❌ **Not addressed.** No concept of templates. | ✅ **Solved.** The cloud is the single source of truth, so there is no drift. | ❌ **Not addressed.** |
| **Primary Focus** | **Complete Local Workflow.** Manages files, populates secrets, and scans for leaks. | **Secret Detection Engine.** A specialized tool for finding secrets. | **Cloud-Based Secret Management.** A centralized vault for teams. | **Shell Environment Automation.** A lightweight tool for managing shell variables. |

**Conclusion:** A scanner is a smoke detector. A cloud vault is an off-site storage unit. **EnvShield is the fireproof, self-organizing house you should have been living in all along.** It provides the complete workflow that developers need to manage their environment securely from day one.

## **The EnvShield Experience**

#### **1. The "It Actually Works On My Machine" Onboarding 🪄**

A new developer joins. Instead of a 4-hour scavenger hunt for secrets, they run one command.

`envshield onboard local-dev`

![onboarding](.gif/onboard.gif)

Boom. They're done. Files created, secrets prompted for, database seeded. Time for coffee ☕.

#### **2. The "Nope, Not Today" Safety Net**

You're tired. You paste a token where it doesn't belong. You try to commit.

`git commit -m "Quick fix, definitely nothing wrong here"`

[!scan](.gif/scan.gif)

EnvShield's pre-commit hook steps in and saves you from having to update your resume.

## **Installation (The Easy Part)**

EnvShield is on PyPI. You'll need Python 3.10+.

`pip install envshield`

Check if the magic worked:

`envshield --help`

## **The Spellbook: A Guide to the Commands**

### **`envshield init` - The Architect**

Run this first. It asks you a few simple questions and builds the envshield.yml blueprint for your project.

### **`envshield onboard` - The Butler**

The white-glove service for setting up an environment. It creates files, politely asks for secrets, and runs your setup scripts so you don't have to.

### **`envshield list` & `use` - The Time-Turner**

list shows you your configured realities. use instantly transports your project's configuration to one of them. No more manual file-swapping shenanigans.

### **`envshield template` - The Librarian**

Includes check and sync. It reads your config files, compares them to the template, and shushes you until they're in sync. Prevents the "but it works on *my* machine" category of bugs.

### **`envshield scan` & `install-hook` - The Bodyguard 💪**

scan actively looks for trouble. install-hook puts a bodyguard in front of your git commit command to make sure no secrets get past.

## **The Brain of the Operation: envshield.yml**

This is where you tell the robot what to do. It's simple for simple projects, and powerful for complex ones.

```yaml
# A simple project:  
project_name: my-cool-api
profiles:
  dev:
    links:
      - source: .env.dev
        target: .env
        template: .env.template

# A wonderfully complex project:  
project_name: complex-project  
profiles:  
  local-dev:  
    links:  
      - { source: config/env_config.dev.py, target: config/env_config.py, template: config/env_config.local.py }  
      - { source: .env.local-dev, target: .env, template: .env.template }  
    onboarding_prompts: [SECRET_KEY]  
    post_onboard_script: "./tools/ctl.sh pre-deploy"  
secret_scanning:  
  exclude_files: ["node_modules/*", "dist/*"]
```

## **The Future: Now With More Lazers (and Encryption) ✨**

Phase 1 is ready and already awesome. But we're just getting started. Here's a sneak peek at what's coming.

### **Phase 2: The "We're a Real Team Now" Upgrade**

* **Encrypted Files:** Safely commit secret files to Git. Because you know you want to.  
* **P2P Secret Sharing:** envshield share SECRET --to @teammate. The secure Slack DM you've always needed.  
* **The Global Vault:** Store your personal GITHUB_TOKEN once and never type it again.  
* **envshield doctor:** The "is it plugged in?" command for your environment.

### **Phase 3: The "Okay, This is Serious Business" Expansion**

* **Live Secret Verification:** We'll actually check if that AWS key you found is still active.  
* **Automated Remediation:** EnvShield finds a secret, gets nervous, and opens a PR to fix it for you.  
* **Centralized Enforcement:** A non-bypassable webhook shield for the whole organization.

## **Community & Support**

Got questions? Have a brilliant idea? Come hang out with us!

* 
* 🤔 **Ask a question on GitHub Discussions:**[Discussions](https://github.com/rabbilyasar/envshield/discussions/)

Or, Follow us on our socials:

## 🌍 Community & Links

- 🌐 Website: [envshield.dev](https://www.envshield.dev)  
- 🐙 GitHub: [rabbilyasar/envshield](https://github.com/rabbilyasar/envshield)  
- 🐍 PyPI: [EnvShield on PyPI](https://pypi.org/project/envshield/)  
- 🤔 GitHub Discussions: [GitHub Discussions](https://github.com/rabbilyasar/envshield/discussions)  
- 💬 Join our Discord:[@discord](https://discord.gg/dSEbvPW57N)  I 


## **Contributing (Don't Be Shy)**

Spotted a bug? Think our jokes are terrible? We want to hear it all. Check out CONTRIBUTING.md to get started.