# Changelog

All notable changes to this project are documented in this file.

## [3.1.0] - 2026-07-31

### Added
- `envshield generate` — compiles `env.schema.toml` directly into a typed, validated config module: `pydantic-settings` for Python, or a `zod`-based module for TypeScript. Secret variables are masked by default in the generated code's runtime representation (`SecretStr` in Python, a local `Secret<T>` wrapper in TypeScript), so a secret can't accidentally leak into a log line, a `console.log`, or a stack trace.
- `--lang` option on `generate` (`python` or `typescript`), auto-detected from your project when omitted — Next.js/Vite/Node.js projects default to TypeScript, everything else defaults to Python.

### Fixed
- `envshield check` now exits with a non-zero status when the local env file is missing required variables or has undeclared extras, so it can be used as a CI gate. Previously it always exited `0` regardless of drift.
- `envshield import` no longer fails silently on Python config files (e.g. Django/Flask `settings.py`). `PythonParser.get_vars()` had a signature mismatch with the parser interface that caused a `TypeError`, which was misreported as "Import cancelled by user." with a success exit code.
- `envshield scan` no longer walks into `.git`, `node_modules`, `venv`/`.venv`, `__pycache__`, `dist`, `build`, and similar directories by default. Previously these had to be manually excluded per-project via `envshield.yml`, causing noisy false positives and slow scans on any real project.

## [3.0.0] - 2025-10-28
### Added
- `envshield import <file>` — converts an existing `.env` file into a new `env.schema.toml`, with an `--interactive` mode to confirm secret/default classification per variable.

## [2.1.0] - 2025-09-22
### Added
- Support for validating/checking custom, non-default env file names.

## [2.0.1] - 2025-09-15
- Packaging and documentation fixes following the 2.0.0 redesign.

## [2.0.0] - 2025-09-13
### Added
- Project redesign around the `env.schema.toml` schema-first workflow.
- `envshield doctor` health-check command.
- `envshield setup` interactive onboarding wizard.
- Framework detection (Next.js, Django, Flask, etc.) for `envshield init`.

## [1.4.0] - 2025-09-03
- Earlier release predating the schema-first redesign. Detailed changes were not tracked in a changelog at this point in the project's history.

[3.1.0]: https://github.com/rabbilyasar/envshield/compare/v3.0.0...v3.1.0
[3.0.0]: https://github.com/rabbilyasar/envshield/compare/v2.1.0...v3.0.0
[2.1.0]: https://github.com/rabbilyasar/envshield/compare/v2.0.1...v2.1.0
[2.0.1]: https://github.com/rabbilyasar/envshield/compare/v2.0.0...v2.0.1
[2.0.0]: https://github.com/rabbilyasar/envshield/compare/v1.4.0...v2.0.0
