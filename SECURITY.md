# Security Policy

EnvShield is open source under the [MIT License](LICENSE.md). It handles security-sensitive information (environment variables, secret classification, deployment manifests), so a real vulnerability here can have real consequences for people using it.

## What to report privately

If you find a security issue that could let EnvShield expose a secret value, misclassify or bypass a security control, escape the intended filesystem boundary (e.g. via a symlink), or otherwise behave unsafely with sensitive configuration — please report it privately rather than filing a public issue.

**Do not open a public GitHub issue for an exploitable security vulnerability.** A public issue is visible to everyone, including anyone who might misuse it, before a fix exists.

## How to report

Use GitHub's private vulnerability reporting for this repository:

**[Report a vulnerability](https://github.com/rabbilyasar/envshield/security/advisories/new)**

This opens a draft security advisory visible only to the maintainer until it's ready to be disclosed — no email address or third-party account needed.

## What to include

A useful report includes:

- A clear description of the vulnerability and its impact.
- Steps to reproduce it, including a minimal `env.schema.toml`/`envshield.yml`/command sequence if relevant.
- The EnvShield version affected (`envshield --version`).
- Anything you think might be relevant context — a specific OS/filesystem behavior, a specific parser, etc.

## Non-security bugs

For anything that isn't a security vulnerability — a false positive, an incorrect validation result, a crash — please use the normal [GitHub Issues](https://github.com/rabbilyasar/envshield/issues) instead.
