# Changelog

All notable changes to this project are documented in this file.

## [4.0.0] - 2026-08-03

### Fixed
- **Critical for multi-service projects:** `envshield schema sync` and `envshield setup` now resolve `.env.example`/`.env` inside each service's own directory (via `--service`), instead of always reading/writing a single root-level `.env.example`/`.env` regardless of which service was targeted. Previously, syncing or setting up two services from a monorepo root would silently overwrite the same file.
- `envshield doctor --service <name>` now actually checks that service's own schema path and env files. Previously it always checked the root `env.schema.toml`/`.env`/`.env.example`, so a healthy service could be reported as completely misconfigured.
- `envshield schema sync` / `setup` / `doctor` / `check` now actually implement the "Which service? (api / web / all)" selection the README already advertised: omitting `--service` on a multi-service project prompts you to pick one or run against `All services`, instead of silently defaulting to a single root-level file and ignoring every configured service. (`resolve_service()`/`resolve_targets()` in `service_manager.py` existed to do exactly this but were never wired into any command.) With only one service configured, it's now selected automatically -- no prompt needed.

### Added
- Per-service `local_file` / `example_file` overrides in `envshield.yml`, for projects whose local config isn't a dotenv file at all — e.g. a Python module like Flask's `config/env_config.local.py`. `schema sync` and `setup` detect the target format from its extension: a `.py` local file is never rewritten wholesale (it may contain real logic beyond simple assignments) — only missing or blank variables are patched or appended in place, and everything else in the file is left untouched.
- `envshield service discover [root]` — scans for service-like directories not already in `envshield.yml` (a dotenv file, or a recognizable Python config module like `config/env_config.local.py`/`config/settings.py`), and registers whichever ones you confirm, seeding each one's schema from its real current config (the same logic as `import`). Bootstraps a fresh multi-service `envshield.yml` from nothing, or extends an existing one — already-configured services are never re-suggested or touched. Deliberately requires an actual env-config signal, not just a generic project marker (`pyproject.toml`/`package.json`/`go.mod`), so a shared library package sitting next to your real services doesn't get mistaken for one.
- `envshield service add <name> <directory>` — registers one service by hand (with `--local-file`, `--example-file`, `--description`, and an optional `--import <file>` to seed its schema), for when you'd rather be explicit than rely on detection.
- `envshield service list` — prints every service currently configured, with its schema and local file paths.

### Fixed
- `envshield import`'s default-value suggestion was limited to a small hardcoded whitelist of variable names (`DEBUG`, `PORT`, `HOST`, ...) — importing a real project's config, whose non-secret variables are almost all project-specific, suggested defaults for essentially nothing (0 out of 59 variables on one real Flask config). Any non-secret variable with a concrete value now gets that value suggested as its default.
- `envshield scan` (and the pre-commit hook it powers, which always runs without `--service`) used to look for a single root `env.schema.toml` on a multi-service project, found none, and silently skipped the undeclared-variable check entirely — for every service, all the time. Each scanned file is now checked against whichever service's schema its own directory belongs to.
- `envshield import`/`scan` no longer misclassify frontend "intentionally public" env vars as secrets. `NEXT_PUBLIC_*`/`VITE_*`/`REACT_APP_*`/`NUXT_PUBLIC_*`/`GATSBY_*`-prefixed vars (and dotenvx's own `DOTENV_PUBLIC_KEY`) are inlined straight into the client-side bundle by design — e.g. `NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY` was getting `secret = true` purely because its name contains "key", which would wrap it in a masking `Secret<T>` in generated code and break the app. A real secret-shaped *value* still overrides this — only the naming convention's false-positive-by-keyword is suppressed. Also split the scanner's Stripe pattern to only match the `sk_` (secret) prefix, not `pk_` (publishable, meant to be public) — a publishable key sitting in committed frontend source was triggering a false "secret found" alarm.
- `envshield service discover`'s dotenv detection matched only a short fixed list of filenames (`.env`, `.env.local`, `.env.development`, `.env.dev`) and missed real, documented project conventions entirely: Mastodon's actual production file is `.env.production` (not on the list), Nx's per-target convention is multi-segment (`.env.<target>.<configuration>`, e.g. `.env.serve.development`). Detection now matches any `.env.*` file, and separately recognizes checked-in templates (`.env.example`, `.env.sample`, `.env.template`, `.env.dist`) as evidence when no real local file exists yet, seeding the schema from whichever was actually found.

## [3.1.1] - 2026-08-03

### Fixed
- **Critical:** `envshield scan --staged` (the pre-commit hook) now reads each file's actual staged content via the Git index, instead of the working-tree copy on disk. Previously, staging a secret and then editing it out on disk *without* re-staging would let the commit through — the hook scanned the clean working-tree file while the secret still shipped in the index.
- `envshield doctor` now exits with a non-zero status when any health check fails, matching `envshield check`. Previously it always exited `0`, so a broken setup couldn't fail a CI job.
- `envshield init`'s `.gitignore` update now adds `.env` itself, not just the `.env.local`/`.env.*.local` override variants — the actual secrets file was previously left untracked-but-unprotected. Fixed alongside a related bug where the update would skip *all* patterns (including the new `.env` one) if *any* single pattern was already present, which would have silently prevented existing projects from ever getting the new `.env` entry.
- `envshield doctor`'s "Example File Sync" check now actually compares `.env.example`'s variables against the schema, instead of only checking that the file exists. It previously reported success even when the schema and `.env.example` had drifted apart.
- `envshield setup` now uses `env.schema.toml`'s `secret` flag (and shows its `description`) when prompting for a value, instead of re-deriving secrecy from its own hardcoded keyword list. The two heuristics could previously disagree with each other and with the schema, undermining the "one source of truth" premise.
- The dotenv parser (used by `import`, `check`, `setup`, `doctor`) now strips matching surrounding quotes from values, strips inline `# comments` from unquoted values, and correctly handles `export KEY=value`-style lines. Previously these could corrupt values on import or misparse shell-style `.env` files.
- The secret-keyword heuristic (`import`, `setup`) now matches whole `_`-delimited tokens instead of raw substrings, fixing false positives like `MONKEY_PATCH_ENABLED` or `AUTHOR_NAME` being flagged as secrets just because they contain "key" or "auth" as a substring.

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

[3.1.1]: https://github.com/rabbilyasar/envshield/compare/v3.1.0...v3.1.1
[3.1.0]: https://github.com/rabbilyasar/envshield/compare/v3.0.0...v3.1.0
[3.0.0]: https://github.com/rabbilyasar/envshield/compare/v2.1.0...v3.0.0
[2.1.0]: https://github.com/rabbilyasar/envshield/compare/v2.0.1...v2.1.0
[2.0.1]: https://github.com/rabbilyasar/envshield/compare/v2.0.0...v2.0.1
[2.0.0]: https://github.com/rabbilyasar/envshield/compare/v1.4.0...v2.0.0
