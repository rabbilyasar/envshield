# Changelog

All notable changes to this project are documented in this file.

## [4.4.0] - 2026-08-07

### Added
- **`--json` on `check`, `doctor`, and `scan`.** Suppresses every Rich table/panel/progress-bar and prints exactly one JSON object to stdout instead, with exit codes unchanged (non-zero on any issue) — a drop-in for a CI gate, a dashboard, or an agent loop that needs to branch on the result instead of parsing colored text. Runs every configured service automatically (instead of the interactive "Which service?" picker) when multiple services exist and `--service` isn't given. `doctor` rejects `--json` combined with `--fix`, since an interactive confirm prompt has no place in a machine-readable mode. See [Machine-readable output](README.md#machine-readable-output---json).

### Fixed
- `generate` silently fell back to Python codegen for any detected project type with no TypeScript mapping — including Go, which has nothing to do with `pydantic-settings`. A detected ecosystem with no codegen target now errors and asks for `--lang` explicitly instead of guessing.
- CI's `ruff check .`/`ruff format --check .` ran with zero project configuration, at the mercy of whatever rule set and default line-formatting ruff's own (much broader, and steadily expanding) defaults happened to enable on that run's installed version — the actual reason CI's lint step had been silently red on every push since 2026-08-04, including the 4.2.0 and 4.3.0 releases. Pinned an explicit, deliberate rule selection and ruff version range, and applied a first-ever repo-wide `ruff format` pass (style only, no behavior change).
- envshield's own CI self-scan (`envshield scan . --config .github/envshield.ci.yml`) never actually ran either, for the same reason (a later step in the same job, aborted before reaching it). Running it for the first time surfaced real gaps in the exclude list — README's own documented example output, the `.gif/` demo-recording scripts, and envshield's own secret-detection source code (which will always self-trigger the generic-API-key pattern on its own implementation) are now excluded; none were real leaks.

## [4.3.0] - 2026-08-07

### Added
- **`init` now builds the schema from your real config, not just a template.** It looks for an existing `.env`, `.env.example`, or a recognizable Python config module first and runs the same real-variable analysis `import` does, classifying each as secret or not with a suggested default and inferred type. Only a genuinely fresh project with nothing to read yet falls back to the old fixed per-framework template.

### Fixed
- A malformed `envshield.yml` produced an unhandled traceback instead of a clean error — unlike a malformed `env.schema.toml`, which already got one. Added `ConfigParseError` to match.
- `generate`, `scan`, and `import --service` each had their own inline check for an unknown `--service` name, with inconsistent (or missing) "Available: ..." listings compared to `check`/`doctor`/`setup`/`schema sync`. All six commands now resolve `--service` the same way.
- `import` never resynced `.env.example` after writing a new/changed schema, so the tracked template could silently drift out of date. `import` now syncs it automatically when writing to the project's/service's real schema path.
- `service discover`/`service add` auto-attached a nearby `docker-compose.yml` to a service directory without checking that the service was actually declared in it — a shared root compose file with exactly one container was silently wired up to every discovered service regardless of whether it belonged there. Auto-attachment now requires a name match; an explicit `--deployment-manifest` is unaffected.

## [4.2.0] - 2026-08-06

### Added
- **Richer schema types.** A variable in `env.schema.toml` can now declare `type` (`string`, `int`, `float`, `bool`, `port`, `url`, `email`), `enum` (a list of allowed values), and `pattern` (a regex constraint) — enforced by `check`, `doctor`, and `setup`, and reflected in generated Python (`AnyUrl`, `EmailStr`, `Literal[...]`, `Field(pattern=..., ge=1, le=65535)`) and TypeScript (`z.string().url()`, `z.string().email()`, `z.enum([...])`, `.regex(...)`) config code. A variable can also declare `requiredIf = { var = "OTHER_VAR", equals = "true" }` to be required only when another variable currently has a specific value, instead of unconditionally.
- **Deployment-manifest validation.** `envshield check` now accepts a docker-compose file or a Kubernetes manifest (Deployment/StatefulSet/DaemonSet/Job/CronJob/Pod, including multi-document files) in addition to a plain `.env` file, auto-detected by content. A new `--container` flag picks which service/container to validate when a manifest declares more than one — auto-resolved from `--service`'s name first, so it's rarely needed explicitly.
- **Schema composition.** A schema can declare `extends = "path/to/base.schema.toml"` (or a list, for multiple bases) to inherit variables from a shared base schema — for common variables (`LOG_LEVEL`, `SENTRY_DSN`, ...) duplicated across every service in a monorepo. Chained and multiple `extends` are supported; a variable defined in more than one place is fully overridden by whichever definition is closest to the schema actually being loaded.
- `service discover`/`service add`/`init` now auto-detect a docker-compose file (in the service's own directory, or the project root) and register it as that service's/project's deployment manifest automatically — no separate opt-in step.
- Once a deployment manifest is registered, `envshield check` validates it automatically alongside the local `.env` file in the same invocation (when no explicit file argument is given), and `doctor` gains a "Deployment Manifest" health check that's only shown at all when one is actually registered.
- `import` now infers a variable's `type` (`int`/`port`/`bool`/`url`/`email`) from its sample value wherever the shape is unambiguous, so a freshly-imported schema starts with real constraints instead of every variable defaulting to an unconstrained string. Never applied to a variable already classified as a secret.
- `setup` now re-validates a variable's *existing* value (not just whether it's present) — a value hand-edited into something the schema no longer allows (an enum typo, a bad URL) is re-prompted for, while everything already correct is left untouched. Enum fields are now selected from a picker instead of typed freehand, so an invalid enum value can no longer be entered in the first place.
- `doctor --fix` for "Local Environment Sync" now does something: it delegates to `setup`, which fills in whatever's missing, blank, or invalid.

### Fixed
- `check`'s and `doctor`'s missing/blank/invalid/extra comparisons were two separately-maintained implementations that could in principle drift out of agreement; they now share one function (`schema_manager.diff_against_schema`).

## [4.1.1] - 2026-08-06

### Fixed
- **Critical:** the "Generic API Key" and "AWS Secret Access Key" scan patterns required the value to be quoted (`KEY = "value"`), so they were completely blind to plain, unquoted `KEY=value` assignments — the conventional `.env` format this tool exists to protect, and the format most real secrets are actually committed in. Both patterns now also match the unquoted form, bounded so they can't start or stop mid-token.
- **Critical:** a service's `path` / `local_file` / `example_file` in `envshield.yml` was used verbatim, with no check that it stayed inside the project. Since `envshield.yml` is normally committed to the repo, a malicious or mistaken entry (an absolute path, or `../../../.ssh/authorized_keys`) could make ordinary commands like `setup` or `schema sync` read or overwrite an arbitrary file outside the project for any teammate who cloned it. These paths are now validated to resolve within the project directory, raising a clear error otherwise.
- `scan` silently skipped any file over 1MB, with no indication that coverage was incomplete — a real secret padded past the size threshold would pass the pre-commit hook unnoticed. Skipped files are now listed in a warning.
- The diff-aware scanning exclusion matched "new" lines by comparing line *text* against the full set of lines in HEAD, so a genuinely new line was treated as pre-existing whenever some unrelated line elsewhere in the file happened to have identical text (e.g. a repeated comment or template block). It now uses a proper positional diff.
- Generated TypeScript config's `Secret<T>` wrapper used TypeScript's `private` keyword, which is compile-time-only and still emits a plain, enumerable runtime property — so a bare `console.log(secret)` printed the real value in full, directly contradicting the wrapper's own doc comment. It now uses a true EcmaScript private field (`#value`) plus an explicit Node inspect hook, so default object inspection can no longer see it.
- Git hook install/checks (`install-hook`, `scan`'s hook installer, `doctor`'s hook check) hardcoded `.git/hooks`, ignoring a configured `core.hooksPath` (e.g. Husky) — silently installing or checking a hook Git never actually runs, with `doctor` falsely reporting it as active. They now resolve the real hooks directory via `git config core.hooksPath`.
- Overwriting an existing pre-commit/post-merge hook no longer just asks a generic yes/no — it now says whether the existing hook was installed by EnvShield or is foreign, and how many lines of unrelated logic would be deleted, before confirming.
- `file_updater.update_variables_in_file` (used by `schema sync`/`setup` when patching a non-dotenv local file) wrote dotenv values with no escaping at all, so a value containing a literal newline would split into extra physical lines — potentially injecting an unintended new assignment into the file. `setup`'s own dotenv writer had the same gap for embedded newlines despite already quoting other special characters. Both now escape embedded newlines/carriage returns.
- `doctor --fix`'s "Configuration Files" fix shelled out to a bare `envshield init` via `os.system`, which silently did nothing if the console script wasn't on `PATH` in whatever shell/venv `doctor` was run from, with no error surfaced either way. It now runs `init` via the current Python interpreter and reports a non-zero exit instead of swallowing it.
- Private-key detection only matched the `-----BEGIN ... PRIVATE KEY-----` header; it now also matches the `-----END-----` footer, in case one was stripped from a leaked key blob.
- Removed a stale, unused `[tool.bumpversion]` block from `pyproject.toml` that had drifted to a different version than the actual release config in `.bumpversion.cfg`.

## [4.0.1] - 2026-08-03

### Fixed
- `envshield check` and `envshield doctor` reported a required variable (no schema `defaultValue`) as in sync even when it was declared only as a blank placeholder — e.g. `SECRETS_ENCRYPTION_KEY = ""` checked into a Python config module ahead of a real per-developer secret. Both checks only looked at whether the key was *present*, never at its actual value, so a developer only found out it was unset when the app raised at runtime. Both now flag a required-but-blank variable distinctly (`Blank in Local`), the same way `setup` already treats it.

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
