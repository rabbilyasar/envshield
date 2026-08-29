# Changelog

All notable changes to this project are documented in this file.

## [4.6.1] - 2026-08-30

A security and correctness patch. It carries the first release of four fixes
that were committed after 4.6.0 was tagged but never published, plus two found
by a production-repository adoption audit — most importantly, `envshield
schema diff` with no arguments reported the contract diff backwards in 4.6.0,
classifying a newly added *required* variable as a non-breaking removal and
letting `--fail-on` exit `0` on it.

### Security
- **A schema field marked `secret = true` with a real `defaultValue` no longer
  loads.** That value was previously written verbatim into `.env.example`,
  generated Python (`pydantic-settings`), generated TypeScript (`zod`),
  `explain`'s output, and `check`'s "Missing in Local" table — every one of
  them a surface `secret` exists to keep values out of, and the first three
  committed to the repository. Such a schema is now refused at load, naming the
  offending fields but never their values. **This is the one change that can
  make a previously-loading schema fail:** remove the `defaultValue`, or unset
  `secret` if the value genuinely isn't sensitive.

### Fixed
- **`envshield schema diff` with no arguments compared the wrong way round.**
  It passed the working tree as the baseline and `HEAD` as the target, so every
  uncommitted change was reported inverted: a newly added variable came back as
  `removed` / `informational`, and a deleted one as `added`. Because a required
  addition is the change this command most needs to classify as breaking, the
  inversion also made `has_breaking_changes` report `false` and let `--fail-on`
  exit `0` on a genuinely breaking uncommitted change — a silent false-clean in
  exactly the pre-commit position the no-argument form exists for. It now means
  `HEAD` -> working tree, matching the explicit two-revision form and
  `envshield undeclared`'s identical default. The explicit
  `schema diff REV_A REV_B` form was never affected.
- **Seeding a service's schema from a Python config module used a different
  definition of that file than `check` did.** When `envshield service discover`
  or `service add --import` seeded a schema from the same file it was
  registering as that service's `local_file`, the importer read it for
  environment *reads* while `check`/`doctor`/`setup`/`schema sync` subsequently
  read it for top-level *assignments*. On a real config-as-code module — many
  literal assignments plus a couple of `os.environ` reads guarding local
  overrides — the two disagreed completely, and the first `check` after
  adoption reported nearly every variable as both extra and missing. The
  reading is now chosen by the file's role, so a file registered as a local
  values file is seeded the same way every command that consumes it reads it.
  Standalone `envshield import` on a settings module that genuinely resolves
  its config from the environment is unchanged, and only *newly* seeded
  schemas are affected — an existing schema file is never rewritten.
- **A malformed or non-UTF-8 Python config file could produce a false clean.**
  `PythonParser` swallowed the parse failure, printed a warning to stdout, and
  returned an empty result — so `check --json` / `doctor --json` emitted a
  warning line *before* the JSON document (breaking `json.loads` on the output
  entirely) and, against a schema whose fields all have defaults, could report
  `"success": true` for a local file that could not be read at all. A parse
  failure is now a structured error with a non-zero exit, and a binary file no
  longer crashes with an uncaught `UnicodeDecodeError`.
- **`scan` reported `clean: true` while silently skipping files it never
  read.** Files over 1 MB were skipped without being counted, so a secret in a
  large file passed the pre-commit hook. Scan results now carry a `complete`
  flag alongside `clean`, incomplete coverage is reported explicitly, and it is
  fatal for `--staged` and `--json`.
- **Generated TypeScript turned the string `"false"` into `true`.**
  `z.coerce.boolean()` is JavaScript's truthiness coercion, so every non-empty
  string — including `"false"` and `"no"` — became `true` at runtime, inverting
  the declared intent of a boolean variable. Generated Python was unaffected.

### Known limitations
Unchanged in this release, and documented here so they aren't mistaken for
regressions:
- A schema may still declare a `defaultValue` that its own `type` rejects
  (e.g. `type = "bool"` with `defaultValue = "no"`); `check` will report the
  schema's own default as invalid. Model `yes`/`no` flags as
  `enum = ["yes", "no"]`. A fix is written and held for the next minor
  release, since rejecting such a schema would break projects that currently
  load.
- `docker-compose.override.yml` and other Compose *delta* files cannot be
  registered as manifests — EnvShield validates each registered manifest as a
  complete deployment, so an override file reports the base file's variables
  as missing. Register only standalone manifests.
- `undeclared` and `explain` walk a registered service's own directory only,
  so environment reads inside a shared library that lives outside every
  service directory are invisible to them. `scan` is repo-wide and does see
  them. Registering the library as its own service closes the gap.

## [4.6.0] - 2026-08-19

This release completes the EnvShield **V1** product milestone: the schema-to-schema
contract diff, source-code configuration discovery, and source-to-contract change
analysis capabilities described in the project charter (Phases 2A, 2B, and 2C) are
now all implemented, alongside a full security-hardening pass and a set of
release-readiness fixes found during end-to-end validation.

### Added
- **`envshield schema diff [REV_A] [REV_B] [--service] [--json]`** — compares a
  service's schema contract between two Git revisions (or the working tree against
  `HEAD` with no arguments), classifying every added/removed/changed variable as
  breaking, security-sensitive, or informational. See
  [Contract diffing and CI enforcement](README.md#contract-diffing-and-ci-enforcement).
- **`envshield undeclared [REV_A] [REV_B] [--service] [--json]`** — reports
  environment-variable reads newly introduced in source code since a given revision
  that aren't declared in the schema, so a new configuration dependency can be
  caught before it's committed. Distinct from `scan`, which inventories every
  currently-undeclared read across the whole codebase regardless of when it was
  introduced.
- **Source-code configuration discovery for Python and JavaScript/TypeScript** —
  AST-based analysis for Python and pattern-based analysis for JavaScript/TypeScript,
  of where a variable is actually read in source (`os.environ`, `os.getenv`,
  `process.env`), independent of the CLI, powering `undeclared` and
  `explain`. Correctly scopes to a service's own directory in a multi-service
  project, with no cross-service attribution.
- **`envshield explain VARIABLE [--service]`** — reports everything EnvShield
  currently knows about one variable in one place: its contract, where it's
  declared (including through `extends`), what source code reads it, which other
  variables' `requiredIf` conditions reference it, and which registered deployment
  manifests declare it.
- **`doctor` diagnoses a per-service `manifests:` key** — this key was never valid
  at any version (deployment manifests are only ever read from a top-level
  `manifests:` list in `envshield.yml`), and was previously ignored silently
  instead of flagged.

### Fixed
- **Six security hardening fixes, closing the P0 release blockers from the
  2026-08-10 audit**, plus one related P1: `scan` no longer prints an unredacted
  secret value in a finding; a validation error message no longer leaks the value
  it's rejecting; `scan` no longer follows a symlink into arbitrary file content
  and reports it as a legitimate finding; a schema-sourced path (service name,
  description, schema path) can no longer be interpolated unescaped into a
  generated hook script or generated source file; a configured schema/local-file/
  deployment-manifest path is now checked for containment after resolving
  symlinks, not just lexically; a freshly-created local secrets file now gets
  `chmod 0600` instead of inheriting the process umask.
- **A Kubernetes manifest's unresolved `envFrom` reference (an external ConfigMap
  or Secret EnvShield can't read from the manifest alone) is now reported as
  unresolved** — distinct from both "missing" and "satisfied" — consistently
  across `check`, `doctor`, `explain`, and their `--json` output, instead of being
  misreported as a plain missing declaration.
- **`schema diff`'s two-revision form no longer hard-fails when the older revision
  predates EnvShield's adoption in the project** (no `envshield.yml`/schema existed
  yet at that revision) — it's treated as an empty contract so the diff still runs
  and reports every variable as newly added, instead of erroring. The default
  (no-arguments) form is unchanged and still errors, since there's no "older
  revision" to be lenient about.
- **`scan --service` no longer reports a false positive from another service's
  files.** Its default scan path now scopes to the named service's own directory
  (an explicitly-supplied path is never altered), closing a gap where the
  single-schema-for-every-file resolver used with `--service` could flag an
  unrelated service's variables against the wrong schema.
- **`setup` no longer reports "Configuration complete!" after a declined
  overwrite.** `run_setup` now reports whether it actually ran to completion, and
  the CLI checks that result before printing success.
- **A generated Python config module no longer duplicates its `pydantic` import
  line** when more than one extra import shares that module — all imports from
  the same module are now merged onto a single `from module import ...` line.
- **`scan` and `undeclared` always report a scanned file's path relative to the
  current working directory**, regardless of whether an absolute or relative path
  argument was given, instead of sometimes showing an absolute path.
- A deployment manifest failing `check` now suggests fixing the manifest itself,
  not running `envshield setup` (which only ever writes a local config file, never
  a deployment manifest).
- Multi-document Compose YAML and the long-form `env_file` mapping are now parsed
  correctly; a `.env`-style file with non-UTF-8 bytes no longer crashes parsing.
- Pre-commit/post-merge hook checks use exact-line matching instead of a loose
  grep, closing a gap where one service's change could spuriously trigger
  another's hook.
- A service's legacy `path:` key (pre-4.5.0, moved to `schema:`) is now
  distinguished from a genuinely nonexistent service instead of being reported
  identically.

## [4.5.1] - 2026-08-10

### Fixed
- **A schema variable with a `defaultValue` must now be explicitly present and non-blank** in the local file, Python `local_file`, and deployment manifests alike — a default only ever changed whether `setup` prompts for a value, never whether the file's own copy could be absent or blank. `check`/`doctor` flag it the same as a truly required variable, with the default named inline so the fix is obvious. See [Every field a variable can have](README.md#every-field-a-variable-can-have).
- **`init --force` no longer risks silently regressing a shared schema.** The real config source a schema was built from is now recorded and reused on re-runs instead of being re-detected from scratch (a real `.env` created later would otherwise always outrank the actual source, even drifted); re-scans only ever add to an existing schema, never drop or reclassify a variable it already declares. `doctor` gains a "Config Source Drift" check for a variable added to a sibling config file after the pin, detection skips a dotenv file EnvShield itself generated, and a config source with no recognizable environment-reading call at all is flagged. See [Maintaining EnvShield over time](README.md#maintaining-envshield-over-time).
- **Pre-commit and post-merge hooks now scope every check to the service actually touched**, not every registered service whenever any one schema changed — staging or merging only one service's schema could previously fail (or silently pass) another service's check that nothing in the change affected. Pre-commit also catches a template with unstaged changes when its schema is staged, closing a gap where running `schema sync` and forgetting to `git add` the result let a commit land with a stale, mismatched template anyway.
- **`scan` skips git-ignored files by default** — a real `.env` was being reported as "DANGER" even though it can never be committed; `--staged` is unaffected, since a force-staged ignored file is a real risk. `scan` also rejects a nonexistent path instead of silently reporting a clean scan, and tolerates a broken schema in one service without crashing the scan for every other service.
- `hook install --yes` now actually reaches the second, foreign-hook-overwrite confirmation instead of leaving it ungated (a real terminal got an unexpected extra prompt anyway; no terminal hit undefined input instead of the safe "warn and skip" path).
- `service add` rejects a nonexistent directory instead of registering a service pointing at nothing; `service remove` names any leftover files and points at what to run next when it was the last registered service.
- `generate` refuses to shadow an existing same-named package directory instead of silently breaking its imports.
- `setup` no longer accepts a blank answer for a variable that has to be present, and `schema sync` distinguishes a real change from an already-in-sync no-op instead of always claiming success.

## [4.5.0] - 2026-08-08

### Added
- **`envshield.yml` always registers at least one service.** A single-service project is now just the one-entry case of the same `services` map a multi-service project has, instead of a separate "rootless" shape — growing from 1 to N services is appending an entry, not a structural migration. A service's schema path moves from `path:` to `schema:` in `envshield.yml` (no backward-compat shim — an existing multi-service config needs updating), and deployment manifests move from a per-service `deployment_manifest` field to a top-level `manifests:` list, so one manifest naming several services isn't duplicated per entry and a service can validate against more than one manifest at once. Also adds `service remove` and schema `extends` composition. See [Command reference](README.md#command-reference).
- **Directory-context inference.** A command run from inside a registered service's own directory (e.g. `services/api/`) now scopes to that service automatically — no `--service`, no prompt. `envshield.yml` lookup also walks upward from the current directory looking for it, the way Git finds `.git`, instead of requiring you to stand at the exact project root. See [Monorepo: managing multiple services](README.md#monorepo-managing-multiple-services).
- **`envshield hook install` / `hook status` / `hook remove`.** A proper noun/verb group for git hooks, alongside the existing `service`/`schema` groups. The old flat `envshield install-hook` still works, identically to `hook install`. `hook status` reports which hooks are currently installed; `hook remove` deletes only hooks EnvShield itself installed, leaving anything else (Husky, a hand-written script) untouched. See [Git hooks](README.md#git-hooks).

### Fixed
- A command run without `--service` on a multi-service project, with no terminal attached (CI, a script, a piped invocation), used to either hang waiting for input that would never arrive or crash with a raw `EOFError` from the interactive "Which service?" picker. It now runs against every configured service automatically (for `check`/`doctor`/`setup`/`schema sync`) or fails with a clear "pass `--service` explicitly" error (for `generate`/`import`, which can't run against more than one target).
- `init`, `setup`, and `service discover --yes` could fully succeed — registering services, seeding schemas — and then exit 1 with a bare `Aborted.` anyway, because the hook-install offer that runs right after has its own confirmation prompt with no equivalent TTY guard. It now silently declines the offer instead of aborting when there's no terminal to ask on.

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
