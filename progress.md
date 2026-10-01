# Progress

Internal status of in-flight multi-phase work. Public direction lives in
[ROADMAP.md](ROADMAP.md); individual findings in [BACKLOG.md](BACKLOG.md).

Last updated: 2026-09-30.

---

## Shared System Schema

Architecture: [docs/architecture/shared-system-schema.md](docs/architecture/shared-system-schema.md).
Findings: BACKLOG.md Part 5b (`BL-135` to `BL-147`).

| Phase | Status |
|---|---|
| Phase 1 | Complete (committed `f34bfb9`) |
| Phase 2 | Complete (committed `a2be864`) |
| Phase 3 | Complete (committed `e336812`) |
| Phase 4 | Complete (committed `5a2c61e`) |
| Phase 5 | Complete (committed `50720ad`) |
| Phase 6 | Complete (committed `26b821b`) |

**Complete.** (Reconciled 2026-10-01: Phase 6 was committed as `26b821b`; a follow-up, `d1435bb`, unified physical-file ownership.)

The scope of Phases 3–6 is not recorded in this repository. Define each
one here before starting it; don't infer it.

### Phase 1: shared schema projection (complete)

Commit `f34bfb9`, "feat: project shared system schemas per service
(shared-schema phase 1)". Unreleased; latest release tag is `v4.7.4`.

- `schema_scope.project()` / `ServiceSchemaView`: the single projection of
  a merged system schema into one service's effective view.
- Schema-user discovery: `config_manager.get_schema_users()` (live, by real
  path) and its revision counterpart in `schema_snapshot` (lexical, against
  `envshield.yml` at that revision). Fails closed on unreadable entries.
- Revision-aware projection: `load_schema_view_for_diff()`.
- `load_schema()` keeps its return shape (the projected fields).
  `load_schema_view()` exposes the full view.
- `load_bare_schema()` rejects scoped schemas.
- Shared/scoped schema writers protected: `import` and `init --force`
  refuse via `assert_schema_rewritable()`.
- Malformed TOML structures that crashed the `toml` library with
  `TypeError`/`IndexError` now raise `SchemaParseError`.
- Whole-system scope validation, fail-closed.

Verified 2026-09-29 by running the suite at `f34bfb9` and at its parent:

```text
1504 tests passing
1440 existing + 64 new
0 existing tests modified
ruff check passes
formatting passes
```

### Phase 2: explicit service directories (complete)

Commit `a2be864`, "feat: persist explicit service directories". Unreleased.

- Persisted `dir` on service entries.
- `get_service_dir()` uses an explicit `dir` when present.
- Legacy fallback: a single-user schema with no `dir` uses the schema's
  parent.
- A shared schema requires an explicit `dir` for every user; a missing one
  is a `ServiceConfigError`.
- `service add` persists the directory when the schema is outside it or
  already shared (including when the directory equals the schema's
  parent), and a re-add replaces a previously persisted `dir`. Existing
  users missing `dir` are warned about, never backfilled.
- Equal-directory behavior intentionally left unresolved (`BL-137`).

Verified 2026-09-29 before committing `a2be864`:

```text
1527 tests passing
1504 existing + 23 new
0 existing tests modified
ruff check passes
formatting passes
```

### Phase 3: physical-file contracts (complete)

Commit `e336812`, "feat: support shared physical file contracts". Unreleased.

- Services whose `.env` (or `.env.example`) resolves to the same physical
  file use the union of their projections for that file
  (`config_manager.get_file_peers` / `load_file_contract`,
  `schema_scope.union_fields`). `schema sync` and `setup` write from it;
  `check`/`doctor` don't report a peer's variables as extra.
- `.env` and `.env.example` peers are resolved independently.
- Conflicting definitions in a shared file (e.g. different per-service
  defaults) fail closed with `FileContractConflictError`. Differing
  descriptions are not a conflict.
- The union doesn't widen any service's grants; secrets stay scoped.

Verified 2026-09-29 before committing `e336812`:

```text
1548 tests passing
1527 existing + 21 new
0 existing tests modified
ruff check passes
formatting passes
```

### Phase 4: out-of-scope reporting (complete)

Commit `5a2c61e`, "feat: distinguish out of scope variables". Unreleased.

- Reporting distinguishes `out_of_scope` (defined in the system schema,
  not granted to this service) from `undefined`, via
  `ServiceSchemaView.status()`: `undeclared` (JSON/SARIF), `scan --json`,
  `explain` (`scope`, `granted_to`), `check --json` (`out_of_scope`, a
  subset of `extra`), `doctor` messages, and `schema diff` (grant
  revoked/added, per revision).
- Reporting only. Out-of-scope variables stay out of `load_schema()`, and
  every failure is unchanged: still `missing_declaration`, undeclared,
  `extra`, or `found: false`.
- New JSON keys appear only when a variable is out of scope, so ordinary
  and single-user payloads are unchanged.
- A peer's variable in a shared physical file is still not reported (the
  Phase 3 file contract allows it).

Verified 2026-09-30 before committing `5a2c61e`:

```text
1569 tests passing
1548 existing + 21 new
0 existing tests modified
ruff check passes
formatting passes
```

### Phase 5: hooks (complete)

Commit `50720ad`, "feat: resolve hook coverage at runtime". Unreleased.

- The installed pre-commit and post-merge hooks are a constant shim
  (`envshield hook run <event>`). They contain no service names, paths,
  or other project topology, so installing them no longer embeds the
  project's shape, and they don't go stale when `envshield.yml` changes.
- Coverage is resolved when the hook runs, from the current
  `envshield.yml`: changed files → each schema's dependency closure (the
  schema plus its `extends` chain, `config_manager.get_schema_files`) →
  every registered user of that schema (`get_schema_users`) → the existing
  per-service checks. A changed `envshield.yml` affects every service.
- Shared-schema users are all checked (fixes `BL-136`). Each is checked
  through its own projection; nothing is merged or granted.
- The `extends` walk is now one shared generator
  (`config_manager._iter_schema_chain`), used by schema loading,
  provenance, and the dependency closure.
- `hook status` reports live coverage and labels older EnvShield hooks.
  Ownership and overwrite rules are unchanged (exact content match).
- Pre-commit fails closed when topology can't be resolved; post-merge
  stays non-blocking.

Verified 2026-09-30 before committing `50720ad`:

```text
1608 tests passing
1569 existing + 43 new - 4 obsolete
17 existing tests rewritten or merged: they asserted the old generated-hook
  layout or the old stale-after-config-change behavior
ruff check passes
formatting passes
```

### Phase 6: lifecycle acceptance (implemented, uncommitted)

Objective: show, with repeatable tests, that the Phase 1-5 architecture
holds across the whole lifecycle (register → setup → sync → check/doctor →
scan → hooks → topology change → continued development) for every topology
the five real projects use. Fix only what blocks that. No new capability.

Acceptance criteria:

| # | Criterion | Pass condition |
|---|---|---|
| A1 | Single service | Semantic backward compatibility. Legacy shapes give structurally identical `check`/`doctor`/`explain` JSON, `scan --json`, sync results, and generated templates to `v4.7.4` (pre-Phase-1). Intentional differences are documented. |
| A2 | Separate schemas | Each service in its own directory is independently scoped; nested directories route `scan` to the deepest service. (Two separate schemas in *one* directory: see A12's limitation.) |
| A3 | Shared schema | Every user is discovered; each is checked through its own projection. |
| A4 | Shared physical file | `sync`/`setup` write the union; peers' variables aren't extras; no grant is widened. |
| A5 | One schema, separate files | Each file's contract is its own projection; no union. |
| A6 | `extends` | A base-only change reaches loading, `schema diff`, and hooks for every leaf schema's users; a scoped base shared across schemas fails closed. |
| A7 | Topology change | Adding/removing/re-pointing services, including by hand-editing `envshield.yml` after hook install, needs no hook reinstall; hook bytes never change. |
| A8 | Scope reporting | `out_of_scope` and undefined stay distinct; both still fail. |
| A9 | Hooks | Pre-commit fails closed on invalid topology; post-merge warns, never blocks. |
| A10 | Lifecycle safety | No lifecycle command drops, rewrites, or offers for deletion another service's values, schema, or file. |
| A11 | Opt-out | A project with little EnvShield-managed config keeps its legacy shape; nothing forces `dir`/`services`/manifests. |
| A12 | Shared-schema directory ambiguity fails closed | Two users of one shared schema in one directory are rejected at registration and fail closed when hand-written (BL-137, option a). Does **not** cover two separate schemas in one directory: that stays a documented legacy limitation (BL-137 residual, option b), where scan routing breaks the tie by name order. |

Real-project models (`envshield/tests/test_lifecycle_acceptance.py`):
layouts modelled on the real repositories, contents synthetic. The real
repositories were not modified.

| Model | Topology | Criteria |
|---|---|---|
| Zeus | athena + hermes, separate schemas `extends` one base, Python local files, several containers per service | A1, A2, A6, A7, A9 |
| KemonChilo | one service at the root, web + worker processes | A1, A7, A11, A12 |
| JossJobs | root + nested `frontend/`, separate schemas, `.env.local` naming, service added after hook install | A2, A7, A8, A10 |
| IssueBear | one service, Python local file, three containers → one service | A1, A11 |
| Portfolio (rabbilyasar.com) | one service, almost no managed config (Worker bindings) | A1, A11 |
| Synthetic | api + worker share a schema and `.env`; web shares the schema only; jobs added by hand | A3, A4, A5, A7, A8, A9, A10, A12 |

Work:

- `BL-137` (option a): `DuplicateServiceDirError` from `get_service_dir`;
  `scan` fails closed instead of skipping; `service add` rejects the join
  (a peer with no `dir` counts as at the schema's parent). The residual
  case (different schemas, same directory) is decided as a documented
  legacy limitation (option b), not fixed: single-user schemas keep
  their behavior.
- `BL-143`: `service remove` never offers a schema or file another service
  uses, including a schema another service `extends` (a gap found and
  fixed in the final acceptance review), and warns (then proceeds) when a
  `services` grant still names the removed service.
- `BL-138`: reproduction attempted on seven candidate paths before any
  change; none lose data; refuted.
- New follow-ups recorded, not fixed: `BL-144`, `BL-145`, `BL-146`, `BL-147`.

Intentional behavior change: two users of one shared schema can no longer
share a directory. Phase 3's tests used that as the way to share default
files; they now share the file through explicit `local_file`/`example_file`
overrides, with unchanged assertions.

Verified 2026-09-30 (working tree, not committed):

```text
1639 tests passing
1608 existing + 32 new - 1 replaced (2 of the new added in the final
  acceptance review: BL-143 extends-base removal; default-path file
  sharing, restoring coverage the old equal-directory fixture gave)
10 existing tests changed: 1 replaced (it asserted equal directories were
  allowed); 9 moved from the equal-directory fixture to explicit
  file overrides, assertions unchanged
lifecycle suite against pre-fix code: exactly the 3 BL-137/BL-143
  scenarios fail
A1 structural comparison vs v4.7.4, final working tree: 5 legacy-shape
  models (Zeus, KemonChilo, JossJobs, IssueBear, Portfolio), 71 surfaces
  (service add output and envshield.yml; schema sync and sync --check
  before/after; check, doctor, explain, scan, and undeclared JSON with
  exit codes; every project file afterwards): 0 differences after
  normalizing the generated template's timestamp line. 42 commands
  succeed and 19 fail by design (drift, an extra, undeclared reads), in
  both trees. A deliberate mutation (check's `extra` emptied) is caught.
  The earlier 38-surface harness wasn't preserved; this is a
  reconstruction over the same models and a superset of the A1 surfaces.
ruff check passes
formatting passes
```

Final acceptance matrix (2026-09-30, final working tree):

| # | Result | Notes |
|---|---|---|
| A1 | PASS | Final-tree comparison above. |
| A2 | PASS | Documented limitation: two separate schemas in one directory (`BL-137` residual). Follow-up: `BL-146`. |
| A3 | PASS | |
| A4 | PASS | |
| A5 | PASS | |
| A6 | PASS | |
| A7 | PASS | |
| A8 | PASS | |
| A9 | PASS | `BL-144` is a presentation follow-up, not a failure. |
| A10 | PASS | Includes the `BL-143` extends-chain case. `BL-138` refuted. |
| A11 | PASS | |
| A12 | PASS within its shared-schema scope | Two separate schemas in one directory are not rejected; that is `BL-137`'s documented legacy limitation. Duplicate directories are not universally rejected. |
