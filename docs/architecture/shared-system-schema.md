# Shared System Schema

How one hand-maintained `env.schema.toml` serves several logical services.
This is the canonical schema architecture: a schema with exactly one user
is simply the degenerate case of it, and behaves exactly as it always did.

Status and phase tracking live in [progress.md](../../progress.md);
individual findings live in [BACKLOG.md](../../BACKLOG.md) (Part 5b).
Implementation: `envshield/core/schema_scope.py` (projection),
`envshield/config/manager.py` (live loading, schema users, service
directory), `envshield/core/schema_snapshot.py` (revision loading).

---

## 1. The model

```text
envshield.yml
    ↓
registered logical services
    ↓
schema users              (services whose `schema:` resolves to the same file)
    ↓
merged system schema      (the file, with its `extends` chain merged)
    ↓
service projection        (schema_scope.project)
    ↓
effective service schema  (what load_schema(service) returns)
    ↓
physical-file contract    (union of projections that share one .env/.env.example)
```

Three distinct things, never to be conflated:

### System schema

The complete configuration contract: the merged `env.schema.toml`.

> What configuration exists in this system?

`ServiceSchemaView.system` — every variable the file defines, `services`
stripped, no per-service overrides applied.

### Service schema projection

A service-specific view of the system schema.

> What configuration does this logical service receive?

`ServiceSchemaView.fields` — only the variables granted to the service,
with that service's overrides applied. `load_schema(service)` returns
exactly this, so every existing caller keeps its interface.
`load_schema_view(service)` returns the whole `ServiceSchemaView` for
callers that must distinguish *defined but not granted to this service*
(`status(var) == "out_of_scope"`) from *not defined at all*
(`"undefined"`).

### Physical-file contract

When several service projections materialize into the same physical
`.env` or `.env.example`, that file's contract is the **union** of those
projections.

> What configuration must coexist in this physical file?

This is not another schema. It is derived, never declared, and never
stored. Any operation that rewrites or validates a shared physical file
must use the union, or it will drop or misreport another service's
variables.

Implemented by `config_manager.load_file_contract(service, key)`:

- **Peers** (`get_file_peers`) are the registered services whose
  `local_file` (or `example_file`) resolves to the same physical path.
  The two keys are resolved independently: sharing `.env` says nothing
  about sharing `.env.example`.
- **Union** (`schema_scope.union_fields`) is deterministic: projections
  are taken in service-name order, so `envshield.yml` order never
  changes it.
- **Conflicts fail closed.** A variable several peers receive must have
  the same effective definition in each. Different per-service
  `defaultValue`s raise `FileContractConflictError` rather than one being
  chosen. Differing `description`s are not a conflict; the shared file's
  entry simply carries none.
- **No grant is widened.** The union is for the file only. Each service's
  projection (`load_schema`) and grants are unchanged, so a secret stays
  out of scope for a service that merely shares a file holding it.
- **Single-service files** are unchanged: their contract is exactly
  `load_schema(service)`.
- **Writers vs. validators.** `schema sync` and `setup` write a shared
  file from the union. `check` and `doctor` still judge a service's own
  requirements against its projection, and only stop reporting a peer's
  variables as extra.
- **Unresolved peers.** A registered service whose paths can't be
  resolved (e.g. a shared-schema service without `dir`) is skipped only
  when it provably can't use the file: its own file override points
  elsewhere, or, without one, its default file next to its schema isn't
  this file. Otherwise the operation fails with `ServiceConfigError`.

### Scope status: in_scope, out_of_scope, undefined

For one service, every variable name falls in exactly one state
(`ServiceSchemaView.status(var)`):

```text
system schema
    ↓
service projection
    ├── in_scope       defined and granted to this service
    └── out_of_scope   defined, but granted only to other services
(absent)               undefined: not in the system schema at all
```

**Out-of-scope is a reporting distinction, not an authorization grant.**
An out-of-scope variable is never added to `load_schema(service)` and
never counts as valid for that service because it exists elsewhere in the
system. Every check that failed for an undefined variable still fails for
an out-of-scope one; only the wording and the suggested fix differ
("grant it via `services`", not "declare it").

The physical-file contract is a separate question. A variable can be out
of scope for a service and still legitimately sit in a `.env` that service
shares with the peer that receives it. There, `check`/`doctor` don't
report it at all, because the file contract allows it. Elsewhere (an
unshared file, a deployment manifest, source code) it's reported as out of
scope.

Where it's reported:

| Surface | Out-of-scope reporting |
|---|---|
| `undeclared` | Still `missing_declaration` (exit 1). JSON/SARIF add `scope: "out_of_scope"`. Judged against the projection at `REV_B`, using `envshield.yml` at that revision. |
| `scan` | Still an undeclared finding. JSON adds `scope: "out_of_scope"`. Routed to a service by directory, as before (see `BL-137`). |
| `explain` | Still `found: false` (exit 1). JSON adds `scope: "out_of_scope"` and `granted_to` (service names). |
| `check` / `doctor` | Still `extra` (not clean). `check --json` adds `out_of_scope`, the subset of `extra`. Human output labels those rows separately. |
| `schema diff` | Category unchanged. A revoked grant reports `detail.scope: "out_of_scope"` rather than a removal from the system. A new grant of an existing variable adds `detail.scope_before: "out_of_scope"`. Each side uses its own revision's topology. |

These keys appear only when a variable really is out of scope. A
single-user schema can't have out-of-scope variables, so its output is
unchanged.

### Logical service ≠ container/process

A registered service is a logical consumer of configuration: a unit that
is granted variables. It is not a container, a process, or a deployment
unit. One container may run several logical services, and one logical
service may run as several processes. Deployment manifests map containers
to services separately (`manifests:` in `envshield.yml`).

---

## 2. Scope semantics

Schema users are computed by `config_manager.get_schema_users(schema_path)`
(compared by resolved real path, so `./env.schema.toml` and a symlink to it
both count). At a git revision, `schema_snapshot` computes users from
`envshield.yml` *at that revision*, lexically, never from the live tree.

| Rule | Behavior |
|---|---|
| Schema used by exactly one registered service | Legacy behavior, unchanged. |
| Schema used by several registered services | Shared system schema; the rules below apply. |
| Unscoped non-secret variable | Global: every user receives it. |
| Unscoped secret in a **shared** schema | Error. A shared schema never grants a secret to every service implicitly. |
| Secret in a shared schema | Must declare `services` naming exactly which services receive it. |
| `services` | Reserved attribute name on a variable. It can't be used as a field attribute for anything else. |
| Service named in `services` that isn't a user of this schema | Error (message distinguishes "not registered" from "registered, but uses a different schema file"). |
| Empty `services` (`[]` or no override tables) | Error. Fails closed; it never means "everyone". |
| Same service listed twice | Error. |
| Per-service override keys | Only `defaultValue` and `description`. |
| `type`, `secret`, `enum`, `pattern`, `requiredIf` | System-wide. Setting any of them per service is an error. |
| Secret with a non-empty per-service `defaultValue` | Error (`SecretDefaultConflictError`), same rule as a system-level secret default. |
| `requiredIf` dependency | Must be granted to every service its dependent field is granted to. Otherwise, error. |
| `requiredIf` naming an *undefined* variable | Not validated (legacy semantics retained; see `BL-140`). |

Validation is whole-system and fail-closed: `project()` validates every
variable against every user, not just the requested service, so a defect
affecting one service fails every service's load. A registered service
whose entry is too broken to tell which schema it uses raises
`ServiceConfigError` rather than being skipped, because silently dropping
it could make a shared schema look single-user and so exempt its secrets
from scoping.

### Valid TOML

```toml
[LOG_LEVEL]                         # unscoped non-secret: global
type = "string"
defaultValue = "info"

[DATABASE_URL]                      # grant only
secret = true
services = ["api", "worker"]

[PORT]                              # grant + per-service overrides
type = "port"
[PORT.services.api]
defaultValue = "8000"
[PORT.services.worker]
defaultValue = "9000"
description = "Worker health-check port"

[TIMEOUT]                           # same, as a single-line inline table
services = { api = { defaultValue = "30" }, worker = {} }
```

A table form grants the variable to exactly the services it names; an
empty override table (`worker = {}`) is a grant with no overrides.

Not valid:

- **A multiline inline table.** TOML inline tables must fit on one line;
  the installed `toml` parser rejects
  `services = {\n  api = { ... },\n}` with a decode error. Use
  `[VAR.services.NAME]` sub-tables instead.
- **`services = [...]` together with `[VAR.services.NAME]`.** This defines
  one key twice with incompatible types. The `toml` library crashes on it
  with a bare `TypeError`/`IndexError`; EnvShield reports it as a
  `SchemaParseError`. Use the table form alone when overrides are needed.

### Loading and writing

- `load_bare_schema()` (no service context) refuses any schema that
  contains `services`, since it can't know which services exist.
- `schema_manager.assert_schema_rewritable()` refuses a wholesale rewrite
  of a schema that is shared by more than one service or uses `services`
  at all. `import` and `init --force` both call it. These schemas are
  hand-maintained, and regenerating them would discard comments and scope
  layout.

---

## 3. Directory semantics

A service's directory is where EnvShield treats "this service's code" as
living: env-file defaults, discovery, scan routing, and invocation-directory
inference. `config_manager.get_service_dir()` is the single source of
truth. Do not derive a service directory from the schema path anywhere
else.

### Single-user schema

If `dir` is absent:

```text
service directory = schema parent
```

Legacy behavior, preserved. An explicit `dir` still wins if present.

### Shared schema

Every registered service using a shared schema must have an explicit
`dir`. A shared service's directory is never inferred from the schema
parent. Doing so would give every sharing service the same directory
(typically the project root), and each would silently claim the others'
code. A missing `dir` on a shared-schema service is a `ServiceConfigError`.
`dir` must be a non-empty path inside the project (validated by the same
containment check as other `envshield.yml` paths).

### `service add`

`service add NAME DIRECTORY [--schema PATH]` persists `DIRECTORY` as `dir`
when the schema's location wouldn't imply it:

- the schema lives outside `DIRECTORY` (e.g. `--schema` pointing at a root
  schema), or
- other services already use the schema. **Joining an existing shared
  schema always persists `dir`, even when `DIRECTORY` equals the schema's
  parent.**

The legacy shape (`DIRECTORY/env.schema.toml`, sole user) is written
exactly as before, with no `dir`.

When a join makes a schema shared, existing users that lack `dir` are
**not** backfilled automatically. `service add` warns and names them
instead: their commands fail with `ServiceConfigError` until `dir` is set,
which an explicit re-run of `service add` does.

### Equal directories

Two services may legitimately declare the same `dir`. What that means for
directory-routed operations (scan routing, discovery) is intentionally
unresolved: nothing rejects it. Scan routing currently breaks the tie
silently by service name order, so the alphabetically-first service's
projection judges every file in that directory. This is a known defect,
not intended semantics, and Phase 2 deliberately leaves it as is. See
`BL-137`.

---

## 4. Rules for code that consumes schemas

- Get a service's fields from `load_schema` / `load_schema_view` (live) or
  `schema_snapshot.load_schema_for_diff` / `load_schema_view_for_diff`
  (revision). Never re-read `env.schema.toml` and interpret `services`
  yourself. `schema_scope.project()` is the only interpreter.
- Get a service's directory from `get_service_dir()`. Get the services
  sharing a schema from `get_schema_users()`.
- Operations on a physical file shared by several services must use the
  union of their projections, via `load_file_contract()` (or
  `get_file_contract_vars()` for names only).
- Never widen a secret's grant as a fallback or convenience.
- To tell out-of-scope from undefined, use `ServiceSchemaView.status()`
  (or `schema_manager.mark_out_of_scope()` / `system_only_vars()` for a
  `SchemaDiff`). Report the difference; never accept an out-of-scope
  variable because of it.
