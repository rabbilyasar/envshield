# Progress

Internal status of in-flight multi-phase work. Public direction lives in
[ROADMAP.md](ROADMAP.md); individual findings in [BACKLOG.md](BACKLOG.md).

Last updated: 2026-09-30.

---

## Shared System Schema

Architecture: [docs/architecture/shared-system-schema.md](docs/architecture/shared-system-schema.md).
Findings: BACKLOG.md Part 5b (`BL-135` to `BL-142`).

| Phase | Status |
|---|---|
| Phase 1 | Complete (committed `f34bfb9`) |
| Phase 2 | Complete (committed `a2be864`) |
| Phase 3 | Complete (committed `e336812`) |
| Phase 4 | Complete (committed `5a2c61e`) |
| Phase 5 | Not started |
| Phase 6 | Not started |

**Next phase: Phase 5, hooks (not started).**

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
