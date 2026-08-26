# EnvShield Release-Readiness Audit

**Date:** 2026-08-21  
**Scope:** Production Python code, CLI behavior, test suite, package build/install,
security boundaries, deployment parsers, generated-code contract, repository
documentation, public website, hosted docs, PyPI listing, and competitive claims.

## Executive decision

**Do not release or market EnvShield yet.** The product core has become
substantially more credible: versioned schema diffing, Python/JS source
dependency discovery, multi-service routing, and local Compose/Kubernetes
validation are implemented. The release is blocked by an invalid full-test
baseline, a secret-contract invariant violation, two code-generation
correctness defects, one `--json` contract break, a fail-open scanner path,
and public distribution/documentation that still describes the old product.

No product code, public content, Git history, tags, or publishing state was
changed during this audit.

## Verification evidence

| Check | Result |
|---|---|
| `ruff check .` | Passed |
| `ruff format --check .` | Passed |
| `python -m compileall -q envshield` | Passed |
| `python -m build --no-isolation --sdist --wheel` | Passed; setuptools emitted future license-metadata deprecation warnings |
| Install generated wheel into fresh venv and run CLI | Passed (`envshield 4.6.0`) |
| `pytest -q` | **959 passed, 1 failed** |
| Tests excluding the environment-dependent Git-boundary test file | 927 passed |

The isolated default build failed only because this sandbox cannot resolve
PyPI to create its isolated build environment. The non-isolated package build
and installed-wheel smoke test both succeeded.

## Release blockers

### P0 — public package and public narrative are not the product being released

1. **PyPI distributes EnvShield 3.0.0, not the local 4.6.0 line.** A user who
   follows `pip install envshield` gets old code and a secret-scanner-first
   description. The PyPI page currently claims the product prevents secrets
   from ever being committed and outlines a future cloud secret vault, both of
   which conflict with the current product charter.

2. **The hosted documentation is materially obsolete.** It documents a flat
   `envshield.yml` structure, omits current commands such as `undeclared`,
   `explain`, service management, and schema diff, describes outdated default
   semantics, and advertises future secret sharing/vault functionality that is
   explicitly out of scope.

3. **The public website mixes current features with stale or absolute claims.**
   It says schema diffing is still being explored despite implementation,
   promises documentation “never rots,” and says the hook prevents secrets
   from ever reaching the repository. The latter is not defensible for a
   pattern scanner with exclusions, bypassable Git hooks, and intentional
   large-file skips.

**Required outcome:** Update the website, hosted docs, and PyPI metadata before
any launch announcement. Do not use absolute security/completeness language.

4. **A schema can store and propagate a secret value.** A field containing
`secret = true` plus `defaultValue = "real-secret"` is accepted. Schema
synchronization can write it to `.env.example`; Python and TypeScript
generation can embed it in generated configuration code. This directly
violates EnvShield's security invariant that a secret value must never be
stored in the configuration contract or unintentionally enter generated
output.

   - Affected paths: schema synchronization in
     `envshield/core/schema_manager.py`; Python and TypeScript rendering in
     `envshield/core/generator.py`.
   - Required outcome: reject secret fields with default values at
     schema-load/validation time, defensively omit them in every sync and
     generation path, and add regression coverage for validation,
     `.env.example`, Python generation, TypeScript generation, JSON output,
     and errors.

### Website source versus deployed site

The local website repository at `/home/rabbil/dev/envshield_website` contains
an **uncommitted, unpublished** positioning rewrite that is much closer to the
configuration-contract product. The deployed website and hosted docs remain
older content, so the release problem is deployment as well as copy.

The local rewrite still needs corrections before it is deployed:

- It says EnvShield finds *every* environment-variable read, while discovery
  supports defined Python and JavaScript/TypeScript patterns only.
- It says `secret = true` prevents type inference during import, but the
  current importer deliberately infers types for secret fields too.
- It says a schema never contains real secret values; that is the intended
  rule, but it is currently unenforced (the P0 above).
- It says circular `extends` is reliably rejected; symlink aliases can evade
  the lexical cycle check.
- It says JSON output is exactly one JSON object; malformed Python input can
  emit a warning on stdout before JSON.
- It says all examples run on the current release, but PyPI still serves 3.0.0
  while the working tree identifies itself as 4.6.0.

### P1 — code and CLI correctness

4. **Malformed Python config invalidates the `--json` contract and can report
   clean.** `PythonParser.get_vars()` prints a warning to stdout then returns an
   empty result on syntax errors. `envshield check --json` therefore emits a
   warning before the JSON document. With an empty schema, malformed Python
   config exits 0 with `"success": true`.

   - Location: `envshield/parsers/_python.py:41-44`
   - Reproduction: a service with `local_file: local.py`, empty
     `env.schema.toml`, and `local.py` containing invalid Python.
   - Expected behavior: parser failure becomes a structured command error;
     JSON stdout remains exactly one JSON document; exit code is non-zero.

5. **Python generated code silently drops a schema field on normalized-name
   collision.** `API-KEY` and `API_KEY` both generate `api_key`; Python keeps
   only the later assignment. The schema is therefore not faithfully compiled.

   - Location: `envshield/core/generator.py:63-70`
   - Expected behavior: reject the schema/output with both conflicting keys
     named, before writing a file.

6. **TypeScript boolean generation is not environment-safe.** `bool` fields
   generate `z.coerce.boolean()`, which follows JavaScript truthiness; the
   string `"false"` becomes `true`. This disagrees with EnvShield's own
   validator, which accepts only `true` and `false` case-insensitively.

   - Location: `envshield/core/generator.py:274-275`
   - Expected behavior: generate an explicit `true`/`false` parser (and test
     the produced module), or clearly restrict/remove the TypeScript runtime
     validation promise until it does.

7. **Large files make scan fail open.** Files larger than 1 MB are skipped,
   recorded in `skipped_files`, but do not affect `clean`; both `scan --json`
   and the normal CLI can exit zero when the only potentially secret-bearing
   file was skipped. This is especially problematic for `scan --staged` in a
   hook or CI gate.

   - Location: `envshield/core/scanner.py:669-671`, `700-705`, `815-826`
   - Expected behavior: a skipped file produces a distinct “incomplete” state
     and non-zero exit by default in staged/CI contexts, with an explicit
     opt-out only if desired.

8. **The full test suite is not deterministic.**
   `TestFindNearestGitBoundary.test_returns_none_when_not_inside_any_git_repository`
   assumes pytest’s temporary directory has no `.git` ancestor. In this
   environment `/tmp/.git` exists, so the test fails even though the function
   does what its stated implementation says.

   - Location: `envshield/tests/test_git_utils.py:46-50`
   - Expected behavior: isolate the test below a controlled synthetic root or
     mock the parent walk; never make a repository test depend on host `/tmp`.

## P2 — correctness, security hardening, and supported-pattern gaps

9. **Compose `env_file` path traversal lacks project-boundary validation.** A
   manifest can reference `../outside.env` and the parser reads it. This is a
   filesystem trust-boundary inconsistency with `envshield.yml` paths.

   - Location: `envshield/parsers/_docker_compose.py:93-110`
   - Reproduced with a Compose file whose `env_file` pointed at a parent file.

10. **Dotenv multiline and unclosed quoted values are silently changed instead
    of rejected.** The line-oriented parser cannot represent multiline values;
    an unclosed quote is stripped and the partial first line is treated as a
    normal value.

    - Location: `envshield/parsers/_dotenv.py:43-55`, `69-72`
    - Required outcome: either implement a documented dotenv grammar or reject
      unsupported/malformed quoting with a parse error.

11. **Schema `extends` cycle detection is lexical, not physical.** It records
    `abspath()` rather than `realpath()`. A symlink-derived chain can recurse
    through the same physical file until path growth/recursion limits interrupt
    it rather than reporting the intended circular-reference error.

    - Location: `envshield/config/manager.py:218-241`

12. **Known deployment-parser limitations remain.** These were already
    documented in the revised README and are acceptable only when public claims
    say “supported patterns,” not “validates Kubernetes/Compose” without
    qualification:

    - Compose does not support `${VAR-default}`, `${VAR:?}`, `${VAR:+}`, or
      embedded/multiple interpolations.
    - Kubernetes `envFrom.prefix` is ignored.
    - Kubernetes `secretKeyRef`/`configMapKeyRef` compare schema variables to
      `env[].name`, not an explicitly referenced key.
    - Kubernetes `initContainers` are not inspected.
    - A Kubernetes file with no supported pod template gives an unhelpful
      all-missing result.

13. **Excluded-file diff scanning has an error-to-skip path.** If determining
    added lines fails, `_get_diff_lines()` returns an empty set, causing an
    excluded staged file to be skipped instead of treated as uncertain. This is
    low-probability but wrong for security-oriented tooling.

    - Location: `envshield/core/scanner.py:223-268`

14. **Discovery is deliberately partial.** Python is AST-based, but JS/TS is
    pattern-based and only recognizes `.js`, `.jsx`, `.ts`, and `.tsx`; it
    misses dynamic access and `.mjs`/`.cjs`/`.mts`/`.cts`. `undeclared` should
    be marketed as detection for supported source patterns, not proof that a
    contract is complete.

15. **Importer classification must remain advisory.** It can write any
    non-keyword, non-pattern-matched value into `defaultValue`. A secret whose
    name/value is not recognized can therefore be committed into the schema.
    The repository README correctly instructs users to review imports; public
    copy must not imply automatic secret classification is complete.

## P3 — maintainability and packaging

16. **Coverage is not measured or gated.** The test suite is large, but
    `pytest-cov` is absent and `inspector.py` has no meaningful coverage.

17. **Package metadata needs cleanup.** The build passes but warns that the
    table-form license and MIT license classifier will be deprecated by
    setuptools. `pyproject.toml` also still uses the softer “configuration
    governance” wording and a security topic classifier, which pull the
    package description back toward its retired identity.

18. **Dead/legacy surface exists.** `state.py` and profile exceptions are
    unused. This is not a release blocker, but they invite accidental revival
    of the old profile/vault direction.

19. **Generated TypeScript relies on modern private-field syntax without
    documenting a TypeScript/ECMAScript target.** It may require a consumer
    compiler target compatible with `#private` fields.

## Claim audit: what is safe to say

### Safe, evidenced product statements

- “A committed TOML configuration contract for a Python-based CLI.”
- “Validates local dotenv/Python configuration against schema types, enums,
  patterns, defaults, and conditional requirements.”
- “Compares schemas across Git revisions and classifies contract changes.”
- “Finds supported Python and JavaScript/TypeScript environment-variable reads
  that were newly introduced and are absent from the schema.”
- “Checks commonly used Docker Compose and Kubernetes manifest patterns
  locally; known limitations are documented.”
- “Runs locally and does not retrieve or store secret values.”

### Do not claim

- “Prevents secrets from ever reaching Git.”
- “Validates every deployment manifest” or “all Kubernetes/Compose configs.”
- “Documentation never rots” or “always perfectly in sync.”
- “Detects every undeclared environment variable.”
- “A secret vault,” “secret sharing,” “secret rotation,” or “retrieves
  secrets from providers.”
- “No competitor does X” without a dated verification.

## Recommended product narrative

Lead with the PR-review workflow, not secret scanning:

> EnvShield makes environment configuration reviewable: one committed
> contract that validates supported configuration sources, detects newly added
> source dependencies, and shows how the contract changed across Git revisions.

The best launch demo is:

1. A developer adds `os.getenv("STRIPE_SECRET_KEY")`.
2. `envshield undeclared BASE HEAD` reports a missing declaration.
3. The schema is updated.
4. `envshield schema diff BASE HEAD` classifies the contract impact.
5. `envshield check` validates local configuration and supported manifest
   relationships.

Secret scanning remains a supporting hygiene feature. Recommend Gitleaks or
GitHub Secret Scanning when detection breadth is the buyer’s primary need.

## Competitive verification

Varlock is a real schema-driven competitor and should not be minimized. Its
current Kubernetes plugin fetches ConfigMap/Secret values and states that
schema-to-deployment validation is still being considered. EnvShield can
currently distinguish itself with local manifest-vs-contract validation, but
must describe its parser scope accurately and re-check this claim before each
public comparison.

## Release sequence

1. Fix P1 items 4–8 with regression tests and restore a full green suite.
2. Decide whether P2 items 9–13 are fixed now or explicitly accepted and
   documented; the Compose filesystem boundary deserves priority.
3. Replace the hosted documentation, website copy, and PyPI project
   description before any announcement.
4. Update packaging metadata/deprecation items.
5. Run the full matrix from a clean checkout, build distributions in isolated
   mode, install the wheel, and run CLI smoke tests.
6. Only then prepare a new release version. Do not reuse the already-created
   `v4.6.0` tag; follow the repository’s explicit changelog → bump2version →
   authorized push → CI/PyPI verification flow.

## External evidence reviewed

- PyPI package page: https://pypi.org/project/envshield/
- Website: https://www.envshield.dev/
- Hosted documentation: https://docs.envshield.dev/
- Zod boolean coercion documentation: https://zod.dev/v4?id=3x-faster-array-parsing
- Varlock Kubernetes plugin scope: https://varlock.dev/plugins/kubernetes/
