# Progress

Internal status of in-flight multi-phase work. Public direction lives in
[ROADMAP.md](ROADMAP.md); individual findings in [BACKLOG.md](BACKLOG.md).

Last updated: 2026-09-29.

---

## Shared System Schema

Architecture: [docs/architecture/shared-system-schema.md](docs/architecture/shared-system-schema.md).
Findings: BACKLOG.md Part 5b (`BL-135` to `BL-142`).

| Phase | Status |
|---|---|
| Phase 1 | Complete (committed `f34bfb9`) |
| Phase 2 | In progress: implemented in the working tree, pending approval, not committed |
| Phase 3 | Not started |
| Phase 4 | Not started |
| Phase 5 | Not started |
| Phase 6 | Not started |

**Next approved action: review and approve Phase 2.** Do not start
Phase 3 until Phase 2 is approved and committed.

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

### Phase 2: explicit service directories (in progress)

Uncommitted changes to `envshield/cli.py`, `envshield/config/manager.py`,
and three test files.

- Persisted `dir` on service entries.
- `get_service_dir()` uses an explicit `dir` when present.
- Legacy fallback: a single-user schema with no `dir` uses the schema's
  parent.
- A shared schema requires an explicit `dir` for every user; a missing one
  is a `ServiceConfigError`.
- `service add` persists the directory when the schema is outside it or
  already shared (including when the directory equals the schema's
  parent). Existing users missing `dir` are warned about, never backfilled.
- Equal-directory behavior intentionally left unresolved (`BL-137`).

Current working-tree state, verified 2026-09-29:

```text
1526 tests passing
1504 existing + 22 new
0 existing tests modified
ruff check passes
formatting passes
```
