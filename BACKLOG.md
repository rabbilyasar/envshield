# EnvShield Backlog

This is the durable, itemized engineering record: confirmed bugs, security
issues, limitations, feature requests, architectural questions, performance
unknowns, and strategic directions discovered through EnvShield development,
Zeus integration, external user feedback, competitive research, and audits.

**This is not the roadmap.** [ROADMAP.md](ROADMAP.md) is curated public
direction (what ships, what's next, what's explicitly not being built).
This file is the operational ledger behind it — every individual finding,
its evidence, and its status, so nothing gets silently lost or silently
promoted. ROADMAP.md should eventually *reference* major themes from here;
this pass does not rewrite ROADMAP.md.

**Rule zero:** never convert one evidence status into another without
re-stating the evidence. A `NEEDS_EVIDENCE` item becomes `CONFIRMED` only
by recording what was reproduced and how. A `CONFIRMED` item is never
deleted when fixed — it's marked `Decision: Fixed in vX.Y.Z` and kept, so a
regression can never quietly reintroduce something already found once.

## Evidence status legend

| Status | Meaning |
|---|---|
| `CONFIRMED` | Directly reproduced or verified against current source, live or via direct code reading with citation. |
| `NEEDS_EVIDENCE` | Plausible, sourced from a document or inference, not independently verified this pass. |
| `STALE` | Was true in an older version/state; no longer true. |
| `REFUTED` | Tested and found not to be true. Kept, not deleted, so it isn't re-raised. |
| `STRATEGIC` | Product/architecture direction, not a current defect. |
| `USER_FEEDBACK` | External user report/question that may indicate a product requirement. |
| `RESEARCH` | Competitive/market observation. |

## ID scheme

`BL-NNN` is the stable, primary ID going forward. Where a finding was
already named elsewhere (the original Zeus audit's `G-N`, the 2026-08-21
release-readiness audit's `RA-PN-N`, this engagement's own `EB-N`/`G3-N`/
`AD-N`/`FUT-N`), that name is kept alongside as an alias — never orphan a
finding from where it was first discovered.

---

## Part 0 — Release Blockers

**Do not claim EnvShield is release-ready while any of these remain open.**
All four are live-reproduced this engagement, not inferred. **Status as of
2026-08-26: `BL-001` fixed (code, not yet released — see its own entry);
`BL-002`, `BL-003`, `BL-004` remain open.** Release readiness still requires
all four resolved, not just this one.

### BL-001 (aka RA-P0-4) — Secret + `defaultValue` propagates into committed/generated output
Type: `SECURITY` · Evidence: `CONFIRMED` · Priority: **P0**
Source(s): 2026-08-21 release-readiness audit; independently live-reproduced this engagement's own verification pass.
Component: `schema_types.py` (shared `secret_default_conflict` predicate), `config/manager.py` (schema-load-time rejection — the fix's primary gate), `schema_manager.py` (`schema sync`'s `.env.example` body, `check`'s `_source_label`), `generator.py` (Python/TypeScript codegen), `explain.py` (`_describe_field`)

- **Problem:** A schema field with `secret = true` and a real `defaultValue` was accepted with no rejection anywhere.
- **Current behavior (fixed 2026-08-26):** Previously the value was written verbatim into `.env.example` (`schema sync`), into generated Python (`pydantic-settings` field default), and into generated TypeScript (`z.string().default(...)`, embedded in the literal before the `Secret<T>` wrapper is even applied at export). While tracing every consumer of a field's `defaultValue` for this fix, two further, previously-undiscovered manifestations of the same root cause were found and fixed in the same pass: `envshield explain`'s field description (`explain.py::_describe_field`, both Rich and `--json`) and `check`'s "Missing in Local"/"Blank in Local" Rich table (`schema_manager.py::_source_label`) both also echoed a secret field's real default with no check against the `secret` flag.
- **Root cause:** No code path anywhere consulted the `secret` flag before echoing `defaultValue`. The five manifestations above shared one implementation boundary that made a single fix possible: every schema-consuming command (`check`/`doctor`/`setup`/`schema sync`/`generate`/`explain`/`scan`) already loads its schema through exactly two entry points, `config_manager.load_schema`/`load_bare_schema` → `_load_schema_file`.
- **Fix:** Two-layer defense-in-depth, no new abstraction — both layers reuse a single new pure predicate, `schema_types.secret_default_conflict(field_schema)` (mirrors an invariant `importer.py`'s schema *generation* already enforced unconditionally: a variable classified secret never got a `defaultValue` in the first place).
  1. **Primary gate:** `config_manager.load_schema`/`load_bare_schema` now call `_reject_secret_defaults` immediately after resolving the schema (post-`extends`-merge), raising a new `SecretDefaultConflictError` naming the offending field(s) — never the value itself — if any field is `secret` with a non-empty `defaultValue`. This closes all five manifestations (and any future one) for every real command in one place, the same way an unsafe schema key name is already rejected outright rather than left for each generator to notice.
  2. **Defense in depth:** each of the five original render sites also independently treats a secret field's conflicting default as absent (masks/omits it) via the same predicate, so a schema dict that reaches `generate_config`/`sync_schema`/`_source_label`/`_describe_field` directly (bypassing the loader — which is exactly how the existing test suite's own `mocker.patch("envshield.config.manager.load_schema", return_value=...)` pattern works) still can't leak.
  - **Explicitly out of scope, by design:** the *local* Python secrets file (`_sync_python_local_file` / `setup_manager._fill_schema_defaults`) — that file is the intended destination for a real secret value (created via `file_updater.open_new_secret_file`, 0600 permissions, not committed), so a secret's `defaultValue` seeding it is correct behavior, not a leak. `schema_snapshot.py`'s explicit-revision path (`schema diff` against a historical Git revision, via `git show`) is also untouched — it must still be able to represent a past, possibly-broken schema state for comparison; only the live-working-tree path (`revision=None`, which already delegates to `config_manager.load_schema`) is gated.
- **Evidence/reproduction:** Original bug live-reproduced with a synthetic marker (`SYNTHETIC_NOT_A_SECRET_123` / a fake value) across all three originally-reported outputs in an isolated `/tmp` project. Fix independently live-reproduced 2026-08-26 against the real `envshield` console script (not just `pytest`) in a fresh isolated `/tmp` project with a `sk_live_SYNTHETIC_NOT_A_REAL_SECRET` marker: `check`/`schema sync`/`generate --lang python`/`generate --lang typescript`/`explain` all now refuse to load with a clear, non-leaking `SecretDefaultConflictError` and exit 1; after removing the conflicting default, all five commands succeed, `grep -r` for the marker across every generated artifact (`.env.example`, `config.py`, `config.ts`) found nothing, the field renders correctly as required (`...`/`z.string().min(1)`) and still typed `SecretStr`/`Secret<T>`, and a sibling non-secret default (`LOG_LEVEL=info`) was confirmed completely unaffected in all outputs.
- **Impact:** `.env.example` and generated config modules are exactly the files EnvShield's own workflow instructs a user to commit; `explain --json` and `check`'s terminal output are exactly the surfaces the charter's §4 invariants name directly ("never expose secret values in normal output," "...in JSON"). Directly violated the charter's own §4 invariant ("never store secret values in the configuration contract").
- **Dependencies:** None.
- **Related:** BL-070 (the DEV.to comment that first raised the conceptual question this bug concretely answers).
- **Tests:** Regression coverage added for the shared predicate (`test_schema_types.py::TestSecretDefaultConflict`, 6 cases including non-string TOML defaults and the empty-string non-conflict edge case), the primary load-time gate (`test_config_manager.py::TestLoadSchemaRejectsSecretDefaults`, 6 cases including an `extends`-inherited conflict and two non-secret/no-conflict sanity checks), and each of the five defense-in-depth render sites (`test_generator.py` ×2 — Python and TypeScript, `test_schema_manager.py` ×2 — `.env.example` sync and `_source_label`, `test_explain.py` ×1). Two pre-existing tests were found to lock in the old (buggy) behavior as their expected result and were corrected rather than left failing: `test_generator.py::test_generate_config_secret_with_default_stays_secret_str` previously asserted the literal secret default appeared in generated Python; `test_setup_manager.py::test_setup_requiredif_explanation_never_leaks_a_secret_triggers_value` and `test_cli.py::test_init_force_preserves_a_hand_corrected_secret_classification` both constructed now-invalid fixture schemas (a secret field with an unrelated defaultValue in the first; an unscoped string-replace that marked three importer-generated fields secret while leaving their inferred defaults in place in the second) — both fixture schemas were narrowed to no longer create the conflict, without weakening either test's actual assertion. Full suite: 979/979 passing. `ruff check .`: clean.
- **Decision:** **Fixed 2026-08-26.** Kept here, not removed, as the record of what was found and fixed — per the charter's own rule that a `CONFIRMED` finding is marked `Decision: Fixed in vX.Y.Z` and kept, never deleted, so a regression can't quietly reintroduce this.
- **Target:** Done. Not yet released — see CLAUDE.md/AGENTS.md §19's release-status note (`v4.6.0` tagged but not live on PyPI; this fix is a further local commit on top of it, same as BL-001–BL-004's siblings once each is fixed).
- **Notes:** This directly contradicted CLAUDE.md's own summary of the 2026-08-21 audit ("no secret leakage... findings"). **Resolved 2026-08-26 (documentation):** CLAUDE.md §19 (and its AGENTS.md counterpart) corrected to state this and the other three Part 0 blockers explicitly, rather than claiming none exist. **Resolved 2026-08-26 (code):** the bug itself, and the two additional manifestations discovered while fixing it, are fixed per above — this is the first of the four Part 0 blockers to move from "documented as open" to actually closed.

### BL-002 (aka RA-P1-4) — Malformed Python config breaks the `--json` contract and can produce a false clean
Type: `RELIABILITY` · Evidence: `CONFIRMED` · Priority: **P0**
Source(s): 2026-08-21 audit; independently live-reproduced.
Component: `parsers/_python.py` (`PythonParser.get_vars`), surfaced through `check --json` and `doctor --json`.

- **Problem:** `PythonParser.get_vars()` catches `SyntaxError`/`TypeError` internally, `print()`s a warning to stdout, and returns an empty result rather than raising.
- **Current behavior:** `check --json` prints the warning line *before* the JSON document (stdout is not valid single-document JSON); with an empty schema (or a schema where every field has a default), the command exits 0 with `"success": true`. The same warning-before-JSON break also reproduces in `doctor --json` — a second command, not named in the original report.
- **Expected behavior:** A parse failure becomes a structured command error; stdout remains exactly one JSON document; exit code is non-zero regardless of schema shape.
- **Evidence/reproduction:** Live-reproduced with an unterminated-string `.py` fixture; confirmed both the stdout corruption (`json.loads` on stdout raises `JSONDecodeError`) and the false `"success": true` with an empty schema; confirmed a non-empty schema with a required field does *not* false-clean (missing shows correctly) but the stdout corruption is unconditional either way; confirmed the same corruption in `doctor --json`.
- **Impact:** Breaks the documented machine-readable contract for any CI/tooling consumer; can silently report "clean" when the local config literally cannot be read.
- **Dependencies:** None. Distinct from BL-031 (G3-9), which is the same root cause in G-3's *new, unimplemented* union code — fixing this doesn't fix that, and vice versa.
- **Related:** BL-031.
- **Decision:** Open. Immediate fix candidate.
- **Target:** Before any release announcement.

### BL-003 (aka RA-P1-6) — Generated TypeScript boolean coercion inverts `"false"`
Type: `GENERATION` (bug) · Evidence: `CONFIRMED` · Priority: **P0** (scoped to `generate --lang typescript`)
Source(s): 2026-08-21 audit; independently live-reproduced with actual runtime execution, not just source reading.
Component: `generator.py:275` (TypeScript `bool` field rendering)

- **Problem:** `bool` fields render as `z.coerce.boolean()`, which follows JavaScript truthiness.
- **Current behavior:** `z.coerce.boolean().parse("false")` returns `true` at runtime. Confirmed by actually running the generated Zod code (`node` + real `zod` package), not just reading the source — every non-empty string, including `"false"` and `"0"`, coerces to `true`; only `""` coerces to `false`.
- **Expected behavior:** Agrees with EnvShield's own validator, which accepts only case-insensitive `"true"`/`"false"`.
- **Evidence/reproduction:** `node runtime_check.mjs` against real `zod`: `"false" -> true`, `"true" -> true`, `"0" -> true`, `"1" -> true`, `"" -> false`, `"no" -> true`, `"False" -> true`.
- **Impact:** Any boolean-typed environment variable explicitly set to `"false"` (a completely ordinary way to disable a flag — e.g. a safety switch like `ALLOW_EMAILS=false`) resolves to `true` in code generated by EnvShield itself. This inverts explicit developer intent, not just a validation miss.
- **Dependencies:** None.
- **Decision:** Open. Immediate fix candidate — generate an explicit `true`/`false` parser, or restrict/remove the TypeScript boolean promise until fixed.
- **Target:** Before shipping or promoting TypeScript `generate` output.

### BL-004 (aka RA-P1-7) — `scan`/`scan --staged` fail open on files over 1MB
Type: `SECURITY` · Evidence: `CONFIRMED` · Priority: **P0**
Source(s): 2026-08-21 audit; independently live-reproduced.
Component: `scanner.py` (large-file skip logic)

- **Problem:** Files >1MB are skipped and recorded in `skipped_files`, but do not affect `clean`.
- **Current behavior:** Both `scan --json` and `scan --staged --json` (the exact mode a pre-commit hook or CI gate uses) report `"clean": true` and exit 0 when the only file containing a secret-shaped marker was the one skipped.
- **Expected behavior:** A skipped file should produce a distinct "incomplete" state and a non-zero exit by default in staged/CI contexts.
- **Evidence/reproduction:** Live-reproduced with a synthetic 1.1MB file containing `AWS_SECRET_ACCESS_KEY=SYNTHETIC0000FAKEKEYNOTAREALSECRET1234` on its first line — both `scan` and `scan --staged` returned `clean: true`, exit 0, with the file correctly named in `skipped_files` but that fact having no effect on the pass/fail verdict.
- **Impact:** Any hook/CI gate that checks only the exit code (the standard integration pattern) is fully bypassed by padding a secret-bearing file past 1MB. This is a bypass of the one feature whose entire job is catching this.
- **Dependencies:** None.
- **Decision:** Open. Immediate fix candidate.
- **Target:** Before any release announcement.

---

## Part 1 — Immediate / high-priority correctness (not P0/P1 in the original report, but confirmed and should not wait for general triage)

### BL-005 (aka EB-3) — `schema sync --check` false-cleans against a Python local file genuinely missing a declared variable
Type: `RELIABILITY` (bug) · Evidence: `CONFIRMED` · Priority: **P1**
Source(s): This engagement's own G-3 investigation (flagged repeatedly across the spec's revisions); independently live-reproduced this pass.
Component: `doctor.py::_check_example_file_sync`, called by `schema sync --check`

- **Problem:** `--check` delegates entirely to `_check_example_file_sync`, which unconditionally returns `True` for any `.py` local_file without inspecting actual variable coverage.
- **Current behavior:** Live-reproduced: a schema with `FIELD_PRESENT` and `FIELD_MISSING`, a local `.py` file declaring only `FIELD_PRESENT` — `schema sync --check` prints `✓` and exits 0 despite `FIELD_MISSING` being completely absent. Also confirmed `--json` isn't a supported flag on this subcommand at all (inconsistent with its siblings).
- **Expected behavior:** Should fail (non-zero exit) when the local file is genuinely out of sync with the schema.
- **Evidence/reproduction:** Live-reproduced in an isolated fixture this pass.
- **Impact:** Same failure class as BL-002/BL-004 (a CI-facing "is this in sync" check that silently passes) — the original report didn't name this one, but it belongs in the same severity conversation on its own merits.
- **Dependencies:** None.
- **Decision:** Open. Should be treated as release-relevant even though it wasn't in the original report's numbered list.
- **Target:** Same cycle as Part 0.

### BL-090 — `AGENTS.md` has diverged from `CLAUDE.md`
Type: `DOCUMENTATION` · Evidence: `CONFIRMED` · Priority: **P2**
Source(s): Discovered this pass while inspecting the repo's existing documentation conventions.
Component: `AGENTS.md`

- **Problem:** `AGENTS.md` (the generic/Codex-equivalent charter, structurally parallel to `CLAUDE.md`) is dated 2026-08-18 and still references "Last revised: 2026-08-10" as its most recent update — missing the entire post-v1 direction-setting pass, Phase 0C/2C completion detail, the 2026-08-21 audit, and the external secret-provider-references section that `CLAUDE.md` already has.
- **Current behavior:** `diff AGENTS.md CLAUDE.md` shows real, substantive divergence, not just a section-numbering difference (`AGENTS.md` has its own "Codex behavior" section where `CLAUDE.md` has "Claude behavior," confirming they're deliberately parallel, not accidentally duplicated).
- **Expected behavior:** Both should stay in sync on shared charter content (identity, positioning, engineering/security principles, current-phase status), diverging only where genuinely tool-specific.
- **Evidence/reproduction:** Direct `diff` this pass.
- **Impact:** Any AI coding agent other than Claude working on this repo is currently operating from a five-day-stale picture of the product's own charter and current phase.
- **Dependencies:** None.
- **Decision:** **Fixed 2026-08-26.** `AGENTS.md` brought back to full content parity with `CLAUDE.md` — the "Last revised" pointer, the §2 external secret-provider-references subsection, and the entire §19 current-phase block (including the BL-001–BL-004 correction below) were synced. Re-diffed after the fix: the only remaining differences are the intentional `Codex`/`Claude` tool-naming substitutions throughout §18/§22/§23, which should stay divergent.
- **Target:** Done. Kept here, not deleted, as the record of what was found and fixed.

---

## Part 2 — Confirmed findings (non-blocking)

### BL-008 (aka RA-P2-9) — Compose `env_file` reads outside the project boundary
Type: `SECURITY` · Evidence: `CONFIRMED` · Priority: P2
Source(s): 2026-08-21 audit; live-reproduced.
Component: `parsers/_docker_compose.py`

- **Problem:** `env_file: ../outside.env` is read without a project-boundary check.
- **Current behavior:** Live-reproduced: a synthetic value in a file one directory above the project root was successfully read and validated as satisfying the schema.
- **Expected behavior:** Validated the same way `envshield.yml` paths already are (`_ensure_within_project`, realpath-based).
- **Impact:** A committed, untrusted manifest can make EnvShield read an arbitrary file outside the project.
- **Decision:** Open, backlog.
- **Related:** BL-010 (a different trust-boundary gap in the same general area — extends — kept separate, different code path and different failure mode).

### BL-010 (aka RA-P2-11) — `extends` cycle detection is lexical, not physical
Type: `SECURITY` / `RELIABILITY` · Evidence: `CONFIRMED` · Priority: P2
Source(s): 2026-08-21 audit; live-reproduced, including its actual practical severity.
Component: `config/manager.py::_load_schema_file`, `config/manager.py::resolve_field_provenance` (duplicated logic, both affected)

- **Problem:** `real_path = os.path.abspath(schema_path)` — despite the variable's name, this does not resolve symlinks, unlike `_ensure_within_project`'s already-hardened containment check.
- **Current behavior:** Live-reproduced with a self-referencing symlink (`real_dir/self -> real_dir`, `extends = "self/x.schema.toml"`): the lexical cycle detector never fires. Confirmed identical behavior in both `_load_schema_file` (via `check`) and `resolve_field_provenance` (via `explain`).
- **Practical severity correction from the original report:** does **not** hang or exhaust the stack. The growing `self/self/self/...` path is bounded by the *operating system's* own symlink-resolution depth limit (`MAXSYMLINKS`/`ELOOP`, ~40 hops on Linux), at which point `os.path.exists()` starts returning `False` and a normal (if extremely garbled — dozens of repeated `self/` segments) `SchemaNotFoundError` is raised. Terminates in well under a second.
- **Expected behavior:** Cycle detection should use `os.path.realpath`, matching `_ensure_within_project`'s already-correct pattern; the two duplicated implementations should likely share one helper so a future fix can't patch only one.
- **Decision:** Open, backlog. Not release-blocking — real but bounded, and the practical consequence today is a bad error message, not resource exhaustion.

### BL-011 (aka RA-P2-12, grouped) — Deployment-parser accuracy gaps (Compose + Kubernetes)
Type: `DEPLOYMENT` · Evidence: `CONFIRMED` (all five sub-cases live-reproduced) · Priority: P2
Source(s): 2026-08-21 audit; every sub-case independently live-reproduced this pass.
Component: `parsers/_docker_compose.py`, `parsers/_kubernetes.py`

Grouped as one backlog item per the instruction to avoid five near-duplicate entries — kept as distinct, independently-closeable sub-items underneath, since each has its own fix location and its own regression test.

1. **Compose `${VAR-default}` (no colon) is not resolved to the default.** Live-reproduced precisely: `${VAR:-default2}` correctly resolves and validates as `"default2"`; `${VAR-default1}` does not match `_INTERPOLATION_RE` (`^\$\{([A-Za-z_][A-Za-z0-9_]*)(:-(.*))?\}$`) at all and falls through to the raw, un-interpolated literal text, which then gets flagged `invalid` against a schema pattern the real default would have passed. **Worse than "unsupported": produces an actively misleading validation error on syntactically valid Compose**, not a clean "unrecognized syntax" signal.
2. **Kubernetes `envFrom.prefix` is ignored.** Live-reproduced: a ConfigMap key `SYNTHETIC_KEY` with `prefix: "PREFIXED_"` in the manifest — EnvShield reports `PREFIXED_SYNTHETIC_KEY` (what the container actually receives) as **missing**, and `SYNTHETIC_KEY` (the un-prefixed name, never actually delivered to the container) as **extra**. A compound, two-sided error from one root cause.
3. **Kubernetes `secretKeyRef`/`configMapKeyRef` matched by `env[].name`, not the referenced key.** Live-reproduced: a field satisfied purely because its schema name matched the env entry's `name:`, while its `secretKeyRef.key:` pointed at an entirely different Secret key.
4. **Kubernetes `initContainers` are not inspected.** Live-reproduced: a variable set only on an `initContainers` entry is reported fully missing.
5. **A Kubernetes manifest with no supported pod template gives an unhelpful generic all-missing result.** Live-reproduced with a ConfigMap-only manifest (no Deployment/StatefulSet/etc.) — indistinguishable output from a real, genuinely-empty container spec; no message clarifies that there was nothing to check.

- Decision: Open, backlog. Not release-blocking (these are within already-documented "supported patterns" territory per the charter, not silent secret exposure or false-clean-on-a-schema-required-field) — but real, and #1/#2/#3 produce actively misleading results, not just gaps, which should weigh toward earlier fixing than pure "missing feature" items.

### BL-020 (aka G-1) — `import`'s Python heuristic destroys config on mixed literal/getenv files
Type: `BUG` · Evidence: `CONFIRMED` · Priority: P1
Source(s): Original Zeus adoption audit (live-reproduced there); re-verified against current source this pass (`importer.py::_discover_python_variables`, unchanged).
Component: `core/importer.py`

- **Problem:** The moment `discover_python_env_vars` finds *any* recognized read anywhere in a `.py` file, the function returns *only* the discovered variables — every top-level literal assignment not independently recognized as a "read" is silently dropped.
- **Evidence:** Live-reproduced on Zeus's real `env_config.local.py` (59 of 61 variables dropped); code re-read this pass, confirmed unchanged.
- **Decision:** Open, backlog.

### BL-021 (aka G-2) — Static AST parsing can't see values set inside a conditional block
Type: `ARCHITECTURE` (documented limitation, intentional) · Evidence: `CONFIRMED` · Priority: P3
Source(s): Zeus audit; re-verified this pass.
Component: `parsers/_python.py`

- **Problem:** Only top-level `ast.Assign` nodes are read; `if os.environ.get("X") == "yes": DB_HOST = "db"` is invisible.
- **This is a deliberate tradeoff**, not an oversight: evaluating the conditional would mean executing untrusted repository code, which the parser's static-only design exists specifically to avoid (see BL-011's sibling security principle).
- **Decision:** Record as documented limitation. A narrow future direction (evaluate a single, simple `if os.environ.get(X) == Y:` block against real values at check-time — the same shape `requiredIf` already handles) is `EXPLORING`, not committed.

### BL-023 (aka G-4) — `service add` silently drops previously-set fields on a second call
Type: `BUG` · Evidence: `CONFIRMED` · Priority: P1
Source(s): Zeus audit (live-reproduced there); re-verified this pass by reading `config/manager.py::add_service` directly (lines 332–389) — confirmed unchanged: `entry: Dict[str, Any] = {"schema": schema_path}` builds a fresh dict from only the current call's arguments every time, replacing the prior entry wholesale.
Component: `config/manager.py`

- **Expected behavior:** Merge into the existing entry, matching `add_manifest`'s already-additive behavior.
- **Decision:** Open, backlog. Explicitly flagged (in the G-3 spec's own file map) as **not to be bundled into G-3's implementation** — independent fix.

### BL-024 (aka G-5) — No batch "reverse undeclared" / stale-schema-variable command
Type: `FEATURE` · Evidence: `CONFIRMED` (absence) · Priority: P3
Source(s): Zeus audit; re-verified this pass (no matching `@app.command()` exists in `cli.py`).
- **Decision:** Backlog / future.

### BL-025 (aka G-6) — No model for a Compose override/delta file
Type: `ARCHITECTURE` · Evidence: `CONFIRMED` · Priority: P3
Source(s): Zeus audit; re-verified this pass (no override-merge support found anywhere in `parsers/`/`config/manager.py`).
- **Note:** Explicitly distinct from G-3/BL-030 — this is a registration-time limitation (what *can* be registered), not a completeness-semantics one (what happens once something *is* registered).
- **Decision:** Backlog / future — a materially bigger feature (real Compose multi-file merge semantics) than G-3.

### BL-026 (aka G-7) — `check --help` undersells what a bare invocation does
Type: `DOCUMENTATION` / `CLI` · Evidence: `CONFIRMED` · Priority: P3
Source(s): Zeus audit; re-verified this pass — current help text still reads "Defaults to the project's (or service's) local file," no mention of deployment-manifest validation.
- **Decision:** Trivial, separate immediate fix candidate.

---

## Part 3 — Needs evidence (plausible, not independently reproduced this pass)

Sourced from the 2026-08-21 report's own text (a primary document, read in full) but not re-run live this pass. **Do not promote any of these to `CONFIRMED` without reproduction.**

- **BL-006 (RA-P1-5)** — Generator field-name collision (`API-KEY`/`API_KEY` both normalize to `api_key`). Structurally confirmed (the normalization function does produce the collision); the claim that the later assignment silently overwrites the earlier one was not traced end-to-end. `GENERATION`, P2.
- **BL-007 (RA-P1-8)** — Flaky `TestFindNearestGitBoundary` test (assumes no `.git` ancestor above the temp dir). `TECH_DEBT`, P3.
- **BL-009 (RA-P2-10)** — Dotenv multiline/unclosed-quote values silently mangled instead of rejected. `DISCOVERY`, P2.
- **BL-012 (RA-P2-13)** — Excluded-file diff-scan error-to-skip path (`_get_diff_lines()` failure → skip instead of "uncertain"). `SECURITY`, P2.
- **BL-013 (RA-P2-14)** — JS/TS discovery is pattern-based, `.js/.jsx/.ts/.tsx` only, misses `.mjs/.cjs/.mts/.cts` and dynamic access. `DISCOVERY`/`DOCUMENTATION` (positioning accuracy), P3.
- **BL-014 (RA-P2-15)** — Importer classification is advisory-only; an unrecognized secret can land in `defaultValue`. General version of BL-020's specific failure. `DOCUMENTATION`, P3.
- **BL-016 (RA-P3-17)** — Package metadata deprecation warnings (table-form license, MIT classifier) plus stale "configuration governance"/security-topic wording in `pyproject.toml`. `TECH_DEBT`, P3.
- **BL-017 (RA-P3-18)** — Dead legacy surface (`state.py`, unused profile exceptions) inviting accidental revival of the old profile/vault direction. `TECH_DEBT`, P3.
- **BL-018 (RA-P3-19)** — Generated TypeScript relies on `#private` fields without documenting a required target. `DOCUMENTATION`, P3.
- **BL-015 (RA-P3-16, aka EB-7)** — `pytest-cov`/coverage not set up. Corroborated by both the report *and* CLAUDE.md's own record independently — closer to `CONFIRMED` than the others in this section, kept here only because "absent" wasn't re-run as a literal command this pass. `TECH_DEBT`, P3.

---

## Part 4 — Stale / Refuted

Kept, not deleted, so nobody re-raises them without reason.

### BL-019 (aka EB-2) — REFUTED: "registered-but-missing manifest/local_file crashes `check`/`doctor`"
Evidence: `REFUTED`
- **Original claim:** `cli.py`'s outer `except EnvShieldException` handler wouldn't catch the bare `FileNotFoundError` every parser raises for a missing file, so a typo'd/uncommitted registered path would crash `check`/`doctor` uncaught.
- **What live testing found:** No crash, in any of three shapes tried (missing `.yml` manifest — `get_parser`'s content-sniffing returns `None` gracefully for a nonexistent file; missing `.py` local_file — `check_result` has its own **explicit, dedicated `except FileNotFoundError:` clause**, missed on the earlier source-only read; missing manifest in an otherwise-real directory).
- **Root cause of the false alarm:** `check_result` (`schema_manager.py`) already catches `FileNotFoundError` separately from `EnvShieldException`, before `cli.py`'s outer handler is ever reached — a pattern I hadn't read closely enough on the prior pass.
- **Decision:** Not an EnvShield finding. Dropped from active tracking. Kept here as a record that this was checked and found false — a methodology note, not a live issue.

---

## Part 5 — G-3 / `completeness: union`

### BL-030 (aka G-3) — Implement `completeness: union` per the Rev 3 specification
Type: `ARCHITECTURE` / `FEATURE` · Evidence: `CONFIRMED` problem, spec `STRATEGIC` (decided) · Priority: P1 (highest product value identified across this whole engagement)
Source(s): Original Zeus audit (live-reproduced there — `schema sync` tried to append 18 Compose-owned variables into a Python file); two full adversarial spec passes since.
Component: `config/manager.py`, `core/schema_manager.py`, `cli.py`, `core/doctor.py` (see the spec's own file map for exact functions)

- **Problem:** EnvShield's single-source model can't express a legitimate contract split across multiple registered sources (Zeus: mode-switches in Compose, secrets in a Python module) — a variable satisfied by one registered source is falsely reported missing because another source doesn't have it.
- **Chosen architecture:** `completeness: union` — a per-service, opt-in flag in `envshield.yml`; a set-union over *presence* only, never over values. Deliberately **not** the earlier "Satisfaction Modes" proposal (see BL-032) — no automatic source discovery, no precedence, no merge strategies, no per-field `source` schema annotation.
- **Status:** Fully specified across two adversarial review passes (Rev 2, Rev 3), published as a standalone spec artifact. Not yet implemented.
- **Implementation checklist** (each already resolved *within* the spec — tracked here as one item's sub-work, not as separate backlog rows, per the recommendation to avoid clutter):
  - `requiredIf` evaluated per-source independently, OR-reduced across sources (fixes a hidden-precedence bug found in Rev 2, backed by a worked counter-example).
  - `SourceResult`/`FieldStatus`/`FieldEvaluation` — a transient, discard-after-classification design; never retains raw values.
  - `check --json`'s new `combined` key — a top-level sibling to `results`, not a heterogeneous entry inside it, verified against two named existing tests (`test_check_json_reports_clean_state`'s exact-equality assertion, `test_json_shape_matches_check_doctor_convention`'s homogeneity rule).
  - `check`'s exit-code control flow restructured for union targets (evaluate all sources first, then decide, not an inline per-source flip).
  - "Registered-source completeness" and "Source health" kept as two structurally separate signals everywhere (CLI, JSON, `doctor`), so a source error can never hide behind a passing completeness line.
  - `doctor` wording corrected so a union-satisfied-but-locally-incomplete file is never described as self-sufficient.
  - `_evaluate_source`'s exception handling broadened to `(EnvShieldException, FileNotFoundError, OSError, SyntaxError)` plus an independent `ast.parse` pre-check for `.py` local files — see BL-031, the one item from this list that remains genuinely open.
  - The resolved-environment boundary made explicit everywhere in UI text: `completeness: union` validates registered, static sources — it is never a claim about the environment a running process actually receives.
  - `FieldStatus.PRESENT` (aka `G3-11`) — a guardrail note, not an implementation step: this status deliberately collapses "asserted" and "forwarded" values for completeness purposes; whoever builds provenance (`BL-033`) later must derive that distinction independently and must not reuse `FieldStatus.PRESENT` as if it already carried it.
  - The word "union" (aka `G3-12`) — a documentation note: `envshield.yml`'s `completeness: union` is a set-union over *presence*, not a Compose/Kustomize-style value merge; the spec's user-facing docs must say so explicitly so the two aren't conflated.
- **Dependencies:** BL-031 must be resolved before `_evaluate_source` is implemented.
- **Related:** BL-023 (explicitly must **not** be bundled into this implementation, despite being found in the same investigation).
- **Decision:** Architecture decided, spec ready. **Do not reopen Satisfaction Modes or runtime merge/precedence unless new evidence demonstrates the current architecture cannot solve a real user problem** (per explicit standing instruction).
- **Target:** Next implementation slot, pending BL-031's resolution and pending Part 0's release blockers being addressed first (a v1-quality release should not ship with confirmed secret-leakage/false-clean issues open, independent of G-3's own timeline).

### BL-031 (aka G3-10) — Parser exception content-safety verification
Type: `SECURITY` · Evidence: `NEEDS_EVIDENCE` · Priority: P1 (blocks BL-030's `_evaluate_source` specifically, not the architecture)
- **Problem:** Could a parser's exception message (`SyntaxError`, `YAMLError`-wrapped `EnvShieldException`, etc.) ever echo raw file content — a source line, an offending token — into `SourceResult.error` / the new `combined[].errors[]` JSON field?
- **Status:** Not yet checked line-by-line against every `raise` site in all four parsers. A five-minute read, not a design question — recommended to resolve *before* writing `_evaluate_source`, not after.
- **Decision:** Open, blocking BL-030's specific implementation of `_evaluate_source`.

### BL-032 (aka AD-1) — Satisfaction Modes rejected
Type: `ARCHITECTURE` (decision record) · Evidence: `STRATEGIC`
- **Decision:** Rejected in favor of `completeness: union` after a full comparative architecture review. Reasons on record: conflates contract semantics with source topology; automatic discovery introduces determinism problems; shell-environment discovery introduces security/reproducibility concerns; precedence answers a runtime-resolution question EnvShield should not own; merge strategies push EnvShield toward becoming a runtime configuration resolver; materially more complex than the actual problem requires.
- **Purpose of this entry:** Institutional memory, so it isn't re-proposed without whoever proposes it seeing why it was rejected.

### BL-033 (aka FUT-1) — Provenance ("which source satisfies this variable")
Type: `FEATURE` · Evidence: `STRATEGIC` · Priority: `EXPLORING`
- Designed only (`explain` provenance mockup, secret masking reusing the existing `<redacted, N chars>` convention). Not committed to a date. Depends on BL-030 shipping first.

### BL-034 (aka FUT-2) — Consistency / conflict detection ("do the sources that have it agree")
Type: `FEATURE` · Evidence: `STRATEGIC` · Priority: `EXPLORING`
- Designed only. Explicitly requires the "asserts vs. forwards" data-model distinction (case J — a bare `${VAR}` Compose forward) that doesn't exist yet; building this before that distinction exists would produce false positives on any legitimate forward. Depends on BL-030.

### BL-035 (aka FUT-3) — Runtime-boundary validation
Type: `ARCHITECTURE` · Evidence: `STRATEGIC` · Priority: `EXPLORING`, explicitly not scoped
- The question "what does the application process actually receive" (shell exports, launcher defaults, container-runtime injection) — real, named, explicitly **not** designed or committed to. Would need its own charter-level scoping discussion (touches §2/§4's existing boundaries) before any design work. Recorded so it isn't quietly built under the banner of "finishing" G-3.

---

## Part 6 — AI-Agent-Friendly EnvShield (strategic direction)

**Umbrella direction, not a single feature.** Do not implement MCP or any agent
interface yet — this section exists to hold the architectural questions until
they're answered, per explicit instruction.

### BL-040 — AI-Agent-Friendly EnvShield
Type: `AI_AGENT` · Evidence: `STRATEGIC` · Priority: `EXPLORING`
Source(s): AI/Varlock/MCP strategic discussion (this engagement).

- **Framing:** position as "AI-agent friendly," not "MCP scanner" — MCP scanning (BL-041) is one concrete feature within this direction, not the direction itself.
- **Open architectural questions, recorded verbatim, none answered yet:**
  1. What information can an agent safely request?
  2. What information must never be returned?
  3. How do we guarantee secrets are redacted?
  4. Should the agent interact with EnvShield through MCP specifically?
  5. Should EnvShield expose structured JSON APIs first and MCP second?
  6. What configuration graph/index (BL-042) would make agent queries useful?
  7. What questions should an agent actually be able to answer? Candidates on record: "What variables does service X depend on?", "Where is DATABASE_URL used?", "Which services are affected if DATABASE_URL becomes required?", "Is DATABASE_URL present in every deployment target?", "Which configuration variables are undeclared?", "Why is this deployment failing validation?", "Which configuration changes are security-sensitive?", "Does this MCP configuration expose a secret?"
- **Design principles already agreed, before any implementation:** deterministic structured responses; secret-safe output by construction; provenance/evidence attached to every answer; read-only inspection; explicit trust boundaries; no secret retrieval; no runtime secret resolution.
- **Cross-reference:** ROADMAP.md's existing "Next: AI-Agent Configuration Safety" and "Exploring: a more direct interface for AI agents (e.g. MCP)" are the same direction — this backlog item is the detailed record behind those two lines, not a competing plan.
- **Decision:** Not implemented. Architectural questions above must be answered before any code is written.

### BL-041 — MCP configuration scanning
Type: `MCP` · Evidence: `STRATEGIC` · Priority: `EXPLORING`
Source(s): Same discussion as BL-040.

- **Concept:** `envshield scan --mcp` or `envshield mcp scan` — inspect MCP configuration surfaces for unsafe secret exposure and configuration problems.
- **Explicitly a sub-feature of BL-040**, not a disconnected second product — must fit EnvShield's existing configuration-safety model (contract → discovery → deployment validation → secret safety), not become a separately-branded scanner.
- **Decision:** Not implemented. Blocked on BL-040's architectural questions, particularly #1–#4.

### BL-042 — Configuration Graph
Type: `ARCHITECTURE` · Evidence: `STRATEGIC` · Priority: `EXPLORING`
Source(s): AI/Varlock/MCP discussion; already named in ROADMAP.md's "Next" list (item 1, "Configuration Graph & Impact Analysis") — **same direction, not a new one.**

- **Concept:** `env.schema.toml` → schema + code + deployment → one queryable index → human CLI/CI and AI agents (BL-040) both consume it, never the raw filesystem directly.
- **Depends on:** Configuration Discovery (Phase 2B, already shipped) being the actual data source — not the regex-era scanner.
- **Decision:** Not implemented. Kept distinct from BL-033 (provenance) and BL-034 (consistency) — related, not automatically the same feature, per explicit instruction not to collapse these prematurely.

---

## Part 7 — Performance / Scalability (research, not yet benchmarked)

**No external report's numbers are trusted here.** Every item below exists to
*establish* real numbers, not to validate a claimed one.

### BL-050 — Large-repository / `undeclared` performance benchmark
Type: `RESEARCH` / `PERFORMANCE` · Evidence: `NEEDS_EVIDENCE`
- No real numbers exist yet for `undeclared`'s source-discovery pass on a large repository. First step is establishing a baseline, not architecture.

### BL-051 — Memory consumption under large schemas/discovery
Type: `RESEARCH` / `PERFORMANCE` · Evidence: `NEEDS_EVIDENCE`

### BL-052 — Incremental discovery / caching effectiveness
Type: `RESEARCH` / `PERFORMANCE` · Evidence: `NEEDS_EVIDENCE`
- **Checked before writing this item, per explicit instruction:** searched the codebase for any existing cache (`grep -rl "cache\|Cache\|lru_cache" envshield/`). **No EnvShield-authored caching mechanism exists anywhere** — every hit was either a git `--cached` flag or a directory-exclusion list (`__pycache__`, `.mypy_cache`) unrelated to EnvShield's own computation. Any future work here is greenfield design, not extension of an existing cache.

### BL-053 — Parallel/concurrent multi-service validation
Type: `RESEARCH` / `PERFORMANCE` · Evidence: `NEEDS_EVIDENCE`

---

## Part 8 — Pending reconciliation

### BL-060 — Comprehensive Technical Audit (2026-08-24) reconciliation
Type: `PRODUCT` / `TECH_DEBT` · Evidence: `NEEDS_EVIDENCE` (source document not available)
Source(s): Described by the user this session; the report itself is **not present in this repository** (checked `docs/reports/` and searched the whole tree — only the 2026-08-21 report exists as a file) and was not pasted in full.

**This is a source-availability tracking record, not a product or security finding.** It asserts nothing about EnvShield's current behavior. It exists so that "we were told a 2026-08-24 audit exists" isn't lost, and so nothing from it gets promoted to `CONFIRMED` by assumption once the document does surface.

- **What's known:** The report makes claims across architecture, CLI behavior, security, performance, scalability, discovery, deployment compatibility, codegen, and more. It is already known to contain **stale or incorrect claims** — e.g. it reportedly says `service add` doesn't exist (false; confirmed live and in source this engagement, see BL-023's own reproduction of a *different* bug in that exact command), and it uses CVE-style identifiers that must **not** be treated as real CVEs.
- **`check --all` claim, reconciled this pass:** the report is described as claiming `check --all` is missing. Checked directly against current `cli.py` — **confirmed true**: no `--all` flag exists anywhere in the `check` command today. This specific claim holds.
- **Decision:** Nothing from this report is promoted to `CONFIRMED` without the same live-verification methodology used for the 2026-08-21 report (see Part 0–2 above, and the standalone verification report this backlog is built from). Needs the actual document (file, link, or pasted text) before any further reconciliation is possible.
- **Target:** Blocked on obtaining the source document.

---

### BL-100 — 2026-08-10 security audit: outcome known, individual findings not representable
Type: `TECH_DEBT` (tracking) · Evidence: `NEEDS_EVIDENCE` (source document not available) · Priority: —
Source(s): Referenced only in CLAUDE.md/AGENTS.md §19 ("Phase 0 — all six P0 release blockers, P0-1 through P0-6, plus P1-1 — closed 2026-08-11").

**This is a source-availability tracking record, not an open security finding.** The six P0s and one P1 it refers to are already closed (per CLAUDE.md's own record, closed 2026-08-11) — this entry does not assert or imply that a vulnerability is currently present. Its `TECH_DEBT` type (not `SECURITY`) and blank priority are deliberate: it tracks a documentation gap (the individual findings can no longer be itemized), not a live risk.

- **What's known:** Six P0s and one P1 were found and closed. CLAUDE.md's §4 security invariants section names the shape of at least some of them (unredacted scan findings, unmasked validation-error text, a symlink-followed arbitrary-file-read misreported as a legitimate finding — the fix for the last one is independently corroborated: `config/manager.py::_ensure_within_project`'s `realpath`-based containment check exists in current code and its docstring explicitly names "the P0-6 vulnerability this guards against").
- **What's not available:** No report file exists anywhere in this repository (checked). The individual `P0-1` through `P0-6` and `P1-1` descriptions are not recoverable from anything available this session.
- **Decision:** Per the Finding and Evidence Management workflow's rule 8, this is recorded as a missing source, not reconstructed. **Do not invent `BL-NNN` entries for `P0-1`–`P0-6`/`P1-1`'s individual content.** If the original report or commit history (`d7e3a8c`, the symlink hardening pass named in §19) is ever located, split this into proper individual entries then.
- **Target:** Blocked on locating the source (report file or a sufficiently detailed commit trail).

### BL-101 — 2026-08-21 report items 1–3: PyPI/hosted-docs/website staleness claims, not independently verified
Type: `PRODUCT` / `DOCUMENTATION` · Evidence: `NEEDS_EVIDENCE` · Priority: P2
Source(s): 2026-08-21 release-readiness audit's own P0 narrative (its items 1–3, distinct from item 4 which is `BL-001`); CLAUDE.md §19's claim that a documentation/positioning pass addressed this.

**Unlike `BL-060`/`BL-100`, this is a real, substantive finding, not a source-availability record.** The source document is available and was read in full — the claims themselves are known; only independent live verification of whether they still hold is missing. `NEEDS_EVIDENCE` here means the ordinary thing it means elsewhere in this backlog (e.g. `BL-006`–`BL-018`): a real, specific, checkable claim awaiting reproduction — not "we don't have the document."

- **What the report claimed (2026-08-21):** PyPI was distributing `3.0.0`, not `4.6.0`, with stale secret-scanner-first copy; hosted docs were materially obsolete (missing `undeclared`, `explain`, service management, schema diff); the public website mixed current features with stale/absolute claims (e.g. "prevents secrets from ever reaching Git").
- **What CLAUDE.md claims happened since:** a documentation/positioning pass addressed README wording, the CLI tagline, and CHANGELOG wording — but this is about *repository* docs, not the *external* PyPI listing or hosted docs/website specifically.
- **Why this needs its own entry:** this same document trail already contained one confirmed-false "this was handled" claim (`BL-001`–`BL-004`'s "no P0/P1 findings"). That pattern is reason enough not to silently assume items 1–3 were also actually resolved just because a nearby claim says so.
- **What was checked this pass:** a local website repository exists at `/home/rabbil/dev/envshield_website`, last modified 2026-08-25 — after the audit, consistent with someone having worked on it — but its actual current content, the current hosted-docs content, and the current live PyPI listing were **not inspected this pass** (out of scope for a documentation-reconciliation task; this is a live-verification task like the one that produced `BL-001`–`BL-011`).
- **Decision:** Open, `NEEDS_EVIDENCE`. Recommend a live-verification pass (checking the actual PyPI page, hosted docs, and website repo content) the same way `BL-001`–`BL-011` were verified, before this is marked `CONFIRMED` fixed or reopened as a live finding.
- **Target:** Backlog — next live-verification pass, not this one.

---

## Part 9 — User feedback

### BL-070 — `.env.example`'s semantic role (DEV.to comment)
Type: `PRODUCT` · Evidence: `USER_FEEDBACK`
Source(s): External comment on an EnvShield `.env.example` blog post.

- **The conceptual point, preserved distinctly from BL-001:** an example file is normally documentation/onboarding material/a template, meant to be committed — it is *not* supposed to contain real secret values. This is a semantic-role observation about what `.env.example` is *for*, independent of any specific bug.
- **Relationship to BL-001:** this comment is the product evidence that *motivated* asking "what should EnvShield do with a secret field that has a default, or when generating `.env.example`" — a question this engagement later answered concretely by finding BL-001 (a real, live-reproduced release blocker). BL-070 is kept as its own entry specifically so the original user-facing framing isn't lost inside a bug report's implementation language.
- **Decision:** Context/product evidence. No separate action beyond BL-001 — track here so the "why we started looking" reasoning survives independent of the bug's own lifecycle.

---

## Part 10 — Research

### BL-080 — Varlock competitive positioning
Type: `RESEARCH` · Evidence: `RESEARCH`
Source(s): AI/Varlock/MCP strategic discussion; CLAUDE.md §2's own competitive table.

- **Finding:** EnvShield should not try to out-feature Varlock as a runtime environment/secrets loader. Strongest differentiation: configuration-as-code/contract validated across source+deployment boundaries; code-aware configuration analysis (AST discovery → eventual Configuration Graph, BL-042); deployment/CI safety without becoming a runtime resolver.
- **Cross-reference:** This is the same conclusion CLAUDE.md §2 already documents in more detail, with the explicit caveat that it's "only as good as its last evidence check; revisit when the competitive landscape moves." This entry exists so that re-verification is itself tracked as a discrete, dateable piece of work rather than assumed permanent.
- **Decision:** No action — recorded as the evidentiary basis behind an existing charter position.

---

## Part 11 — Explicitly not building yet

Cross-referencing rather than duplicating ROADMAP.md's own "What We're Deliberately Not Building" section (secret vault, out-detecting dedicated scanners, generic `.env` editor/chatbot, replacing typed config libraries) — that list stands unchanged by this pass.

Specific to this backlog's own findings:
- **Satisfaction Modes, automatic source discovery, source precedence, merge strategies** (BL-032) — rejected, not paused.
- **Runtime-boundary validation** (BL-035) — real, named, not scoped; needs its own charter-level discussion first.
- **MCP implementation, any AI-agent interface** (BL-040, BL-041) — architectural questions unanswered; no code until they are.
- **A generic project-level `completeness` default in `envshield.yml`** — considered and explicitly deferred inside the G-3 spec itself (not worth a new config surface for two lines saved on Zeus).

---

## Part 12a — Source Reconciliation Status

One row per source this backlog draws on. "Fully reconciled" means every
finding that source is known to contain has a `BL-NNN` mapping *or* an
explicit unresolved-limitation note — not that the source's claims were all
confirmed true.

**Two rows here point at source-availability tracking records, not product
findings:** the 2026-08-10 audit row (→ `BL-100`) and the 2026-08-24 audit
row (→ `BL-060`) exist because a *source document* is missing — they assert
nothing about EnvShield's current behavior. Every other "No"/"Partially" row
— in particular the 2026-08-21 audit row's `BL-101` — is the ordinary kind
of open item: the source is available, the claim is known, only
independent verification is outstanding.

| Source | Available? | Fully reconciled? | `BL-NNN` mapping(s) | Unresolved limitation |
|---|---|---|---|---|
| 2026-08-10 security audit | **No** — no report file exists in this repository; only its outcome is summarized in CLAUDE.md/AGENTS.md §19 | Outcome tracked; individual findings **not** reconstructable | `BL-100` (tracking only) | Individual `P0-1`–`P0-6`/`P1-1` content unrecoverable without the original report or a fuller commit trail. Not fabricated. |
| Zeus adoption audit | Yes — external artifact, its exact `G-1`–`G-7` text was re-fetched and quoted this engagement | **Yes** — all seven re-verified against current source | `BL-020`–`BL-026`; `G-3` → `BL-030` | None outstanding. |
| G-3 Rev 2 (adversarial pass) | Yes — this engagement's own published spec artifact | **Yes** | Folded into `BL-030`'s checklist | None outstanding. |
| G-3 Rev 3 (runtime-boundary re-review) | Yes — same artifact, revised | **Yes** — including the two items (`G3-11`, `G3-12`) missed on the first backlog-creation pass, added this pass | `BL-031`; `BL-030`'s checklist | None outstanding. |
| 2026-08-21 release-readiness audit | Yes — `docs/reports/2026-08-21-release-readiness-audit.md` | **Partially.** Every P0–P3 code-correctness finding (items 4–19) is reconciled with live-verification evidence. Items 1–3 (PyPI/hosted-docs/website staleness) are tracked but not independently re-verified. | `BL-001`–`BL-018`; items 1–3 → `BL-101` | `BL-101` needs its own live-verification pass (PyPI listing, hosted docs, website repo content) before it can be marked resolved either way. |
| 2026-08-24 comprehensive audit | **No** — not present in this repository, not pasted in full | No — cannot reconcile without the document | `BL-060` (tracking only) | Entirely blocked on obtaining the source document. Known-stale example claims (`service add` doesn't exist) already refuted without needing the full document. |
| AI/Varlock/MCP strategic discussion | Yes — this engagement's own conversation record | **Yes** | `BL-040`, `BL-041`, `BL-042`, `BL-080` | None outstanding — these are `STRATEGIC`/`RESEARCH` entries by nature, not findings awaiting verification. |
| DEV.to `.env.example` feedback | Yes — this engagement's own conversation record | **Yes** | `BL-070` | None outstanding. |
| "3.8" / "3.10" numbering | **No** — referenced only by CLAUDE.md/AGENTS.md §19, source of the numbering scheme itself unknown | No — cannot map to anything | None (explicitly unmapped) | Genuinely unresolved. Not converted into a fabricated `BL-NNN` entry — the numbering is preserved as a named, open loose end in §19 and here, nothing more. |

---

## Part 12 — Source traceability index

| Source | Findings it produced |
|---|---|
| Original Zeus adoption audit | BL-020–BL-026 (G-1–G-7), motivated BL-030 |
| G-3 architecture review (Satisfaction Modes) | BL-032 |
| G-3 implementation spec, Rev 2 adversarial pass | BL-030's checklist (requiredIf, JSON shape, exit-code flow, source-health separation, doctor wording) |
| G-3 implementation spec, Rev 3 (runtime-boundary re-review) | BL-031, the resolved-environment-boundary item in BL-030's checklist, `G3-11`/`G3-12` (also folded into BL-030's checklist) |
| 2026-08-21 release-readiness audit (document) | BL-001–BL-018 (as `RA-*`), sourced; items 1–3 of the same document → BL-101 |
| This engagement's live verification pass | BL-001–BL-005, BL-008, BL-010, BL-011 upgraded to `CONFIRMED`; BL-019 (`EB-2`) refuted |
| This session's own repo inspection | BL-090 (`AGENTS.md` drift), BL-052's caching finding, BL-101's website-repo existence check |
| AI/Varlock/MCP strategic discussion | BL-040, BL-041, BL-042, BL-080 |
| DEV.to external comment | BL-070 |
| Comprehensive Technical Audit (2026-08-24) | BL-060 only — content not yet available for further reconciliation |
| 2026-08-10 security audit | BL-100 (tracking only — see Part 12a) |
| CLAUDE.md's own record | BL-090 (dating comparison), BL-100 (audit-outcome summary); the "3.2–3.10" numbering referenced there remains unmapped to anything in this backlog — flagged, not guessed at |

---

*Last consolidated: 2026-08-26. Same-day follow-ups: (1) `CLAUDE.md`/`AGENTS.md` reconciled against this file's findings (`BL-090` fixed; `BL-001`'s `CLAUDE.md`-contradiction note resolved). (2) A source-reconciliation verification pass added `BL-100`/`BL-101` (previously-unmapped 2026-08-10-audit and 2026-08-21-report-items-1–3 gaps), folded `G3-11`/`G3-12` into `BL-030`'s checklist (previously missing entirely), and added Part 12a. (3) Both charter files gained an "Engineering Task Workflow" index section and a corrected §22 commit-boundary diagram. (4) `BL-001` implemented and fixed — the first Part 0 blocker actually closed in code, not just documented; two previously-undiscovered manifestations of the same root cause (`explain.py`, `schema_manager.py`'s `_source_label`) were found and fixed in the same pass, and two pre-existing tests that had locked in the old buggy behavior as "expected" were corrected. See each item's `Decision` line for current status — this file is the thing to update, not a substitute for actually doing the work.*
