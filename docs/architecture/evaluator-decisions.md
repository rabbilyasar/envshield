# Evaluator Decisions

Decision records for the evaluator migration: moving `check` (and later
`setup`, `explain`, hooks, and MCP) onto one evaluator that produces one
versioned report. Each record states what the code did before, what it
does now, and why. Findings live in [BACKLOG.md](../../BACKLOG.md);
phase status in [progress.md](../../progress.md).

Implementation: `envshield/core/evaluator.py`. It is orchestration only:
presence/validity is still `schema_manager.diff_against_schema` +
`schema_types.validate_value`, sources are still `parsers.factory`,
code references are still `discovery.py`. Since V1-2, v5's verdicts are
`envshield/core/verdicts.py`, judged from the parsers' value states (D-9),
and since V1-3 they decide `check`'s exit code (D-10).

---

## D-1. `defaultValue` is a seed value, not proof of presence

**Before (verified 2026-09-30).** Two contradictory meanings:

- `check`, `doctor`, `schema sync --check` use `should_be_present`: a
  defaulted variable must be explicitly present and non-blank in the file
  (deliberate since `bc151da`).
- `setup` uses `is_required_now`: a defaulted variable is filled with its
  default and never prompted for.
- `explain` labelled a defaulted variable `optional`, and `schema diff`
  classified adding one as `non_breaking` ("every config valid before is
  still valid"). Both contradict `check`: adding a defaulted `PORT` makes
  every existing `.env` without `PORT` fail `check`.

**Decision.** `defaultValue` means: the value `setup` writes without
prompting, `schema sync` writes into `.env.example`, and `generate` emits.
It is never evidence that a variable is present. `check`'s meaning is the
contract; `explain` and `schema diff` now agree with it:

- `explain` reports a defaulted variable as `required`, with its default
  shown separately (and never for a secret).
- `schema diff` classifies requiredness transitions by whether an
  existing file can fail `check` (see D-2's table).

The key keeps its name, `defaultValue`. `default` is not an alias; it is
rejected as an unknown key with a hint (D-2). Making a default count as
"satisfied" would loosen `check` and is a separate, breaking product
decision, not taken here.

## D-2. Schema keys are validated; `required` is supported

**Before.** Unknown field keys (`required`, `environments`, `example`,
typos) and unknown `type` values were silently accepted; an unknown type
behaved as an unconstrained string. Unsafe variable names were rejected
only by writers (`schema sync`, `setup`), so `check` accepted `BAD-NAME`.
There was no way to declare an unconditionally optional variable.

**Decision.** Every live schema load (`load_schema_view`,
`load_bare_schema`) validates the merged system schema and raises
`SchemaValidationError`:

- Field keys must be one of: `type`, `enum`, `pattern`, `defaultValue`,
  `requiredIf`, `required`, `secret`, `description`, `services`.
- `type` must be one of `string`, `int`, `float`, `bool`, `port`, `url`,
  `email`, `enum`.
- `required` and `secret` must be booleans; `requiredIf` must be a table
  whose keys are `var` and `equals`; `enum` must be a list.
- Variable names must match `^[A-Za-z_][A-Za-z0-9_]*$`.
- A top-level value must be a table, except `extends`.

The revision loader (`schema diff` at a Git revision) is deliberately not
validated: it must be able to represent a historical, possibly-invalid
schema (same reasoning as BL-001's revision-path exemption).

This is a breaking change for any schema that relied on an ignored key:
it now fails to load, with a message naming the key.

**`required` semantics:**

| Schema | Must be present in the file? | `setup` |
|---|---|---|
| no `required`, no `requiredIf`, no default (legacy) | always | prompts |
| no `required`, `defaultValue` (legacy) | always | fills default |
| `requiredIf`, no default | when the condition holds | prompts when it holds |
| `requiredIf` and `defaultValue` (legacy) | always: the default wins | fills default |
| `required = true` | always (same as legacy) | prompts, or fills default |
| `required = false` | never; if present and non-blank it must still be valid | fills default if any, never prompts |

`required` together with `requiredIf` is rejected: the two answer the same
question. `required = false` is the only new capability.

**`schema diff` requiredness states** (`always` covers legacy defaulted and
unconditional, and `required = true`):

| Before → after | Category |
|---|---|
| optional → always / conditional | breaking / requires_review |
| conditional → always | breaking |
| always / conditional → optional | non_breaking |
| always → conditional | non_breaking |
| gaining or losing a default with no presence change | non_breaking (reported as a default change, not a requiredness change) |
| added: always | breaking |
| added: conditional | informational |
| added: optional | non_breaking |

## D-3. Process environment is an opt-in overlay on the local file

**Before.** No command read the process environment as a source.

**Decision.** `check --process-env` overlays the process environment on
the service's local file (never on a deployment manifest): for a name the
contract declares (or names as a `requiredIf` trigger), a value in the
process environment wins over the file's, including an empty one, which
is how dotenv loaders behave (they never override an already-set
variable). Unrelated shell variables are never read into the evaluation.
No value is ever reported; the source list gains a
`process_environment` entry naming the overlaid variables.

**Off by default**, for two reasons: the first evaluator-backed `check`
must produce today's verdicts exactly, and `check` is run in hooks and CI
where the shell environment is not the application's. Whether it should
become the default (KemonChilo's `NODE_ENV`, which its schema says is set
by the run command, is the concrete case for it) is an open decision.

## D-4. Multiple sources keep their existing semantics

**Decision.** Unchanged, and frozen for this migration:

- Default: every registered source (the local file and each registered
  deployment manifest) must satisfy the contract on its own; each is
  reported and any failure fails `check`.
- `completeness: union` (opt-in per service, BL-030): per-source results
  are still reported but no longer decide the exit code; the union of
  presence across sources does, and any source that failed to load fails.
- An explicit `FILE` argument checks exactly that file: no manifests, no
  union, no code references.

A service's environment is arguably the combination of its sources, which
is what union evaluates. Whether that should become the default is open;
it is not changed here.

## D-5. Evaluator report, version 1

One report per evaluated service. No value, hash, length, fingerprint, or
file content ever appears in it; invalid values are described by the
constraint they break (`validate_value`'s never-echo rule).

```json
{
  "report_version": 1,
  "service": "api",
  "sources": [
    {"kind": "local_file", "path": ".env", "status": "checked"},
    {"kind": "manifest", "path": "docker-compose.yml", "container": "api",
     "status": "error", "error": "..."},
    {"kind": "process_environment", "status": "checked",
     "variables": ["PORT"]}
  ],
  "variables": [
    {
      "name": "DATABASE_URL",
      "declared": true,
      "scope": "in_scope",
      "secret": true,
      "requiredness": "required",
      "present": true,
      "valid": true,
      "reason": null,
      "sources": [{"path": ".env", "status": "ok"}],
      "references": [{"file": "app/db.py", "line": 10, "language": "python",
                      "access_type": "os.getenv", "confidence": "high"}],
      "referenced": true
    }
  ],
  "union": null,
  "code_references": {
    "status": "checked",
    "files_scanned": 12,
    "skipped": [],
    "undeclared": ["STRIPE_SECRET_KEY"],
    "dynamic_references": [
      {"file_path": "app/cfg.py", "line": 4, "language": "python",
       "access_type": "os.getenv"}
    ]
  },
  "summary": {"clean": false, "complete": true}
}
```

- `sources[].status`: `checked`, `missing` (file not found), `error`
  (parse/load failure), `unresolved` (checked, but a required name can't
  be confirmed because an external reference such as a Kubernetes
  `envFrom` Secret may supply it).
- `variables[].sources[].status`: `ok`, `missing`, `blank`, `invalid`,
  `unresolved`, `extra`, `out_of_scope`, `not_required`.
- `requiredness`: `required`, `conditional`, or `optional` (D-2).
- `present`: `true`, `false`, or `"unknown"` (a source is `unresolved`, or
  a source failed to load). `valid`: `true`, `false`, or `null` (not
  present anywhere).
- `declared: false` entries come from code references to a variable the
  service's contract doesn't declare (`scope` is `out_of_scope` when the
  system schema defines it but doesn't grant it to this service).
- `referenced`: only when code references were evaluated; `false` is
  informational and never fails a check (discovery doesn't see
  framework-style reads, e.g. Django settings).
- `code_references.status`: `checked`, or `not_checked` (explicit `FILE`,
  or the contract didn't load). `undeclared` lists the names that fail the
  check; `dynamic_references` (reads whose key isn't a literal) and
  `skipped` files are informational.
- `undeclared_reference: true` marks a variable in `undeclared`.
- `summary.complete` is `false` when any source is `missing`, `error`, or
  `unresolved`. `summary.clean` is never `true` when `complete` is
  `false`: incomplete is not clean.

`check --json` keeps its existing `success`/`results`/`combined` keys
unchanged and adds `reports` (one v1 report per service).

## D-6. Exit codes are unchanged

`check` still exits `0` when every service is clean and complete, and `1`
otherwise (contract failure, incomplete evaluation, or an error alike).
Scripts and hooks depend on that. The report's `summary` distinguishes
the cases (`clean` vs `complete`); a separate exit code for "incomplete"
(e.g. `2`) is a future compatibility decision, not made here. (Typer's own
usage errors already exit `2`.)

## D-7. Code references in `check`

`check` without an explicit `FILE` now evaluates code references for each
service, over the file set the pre-commit `scan` checks undeclared reads
in: the service's own files by `resolve_file_owner`, plus its
`additional_source_roots`, skipping default-excluded directories,
git-ignored files, symlinks, and `secret_scanning.exclude_files`.

A high-confidence read of a variable the contract doesn't declare fails
`check`, as it already blocks a commit. This is a user-visible change: a
project with undeclared reads that passed `check` before now fails it.
Medium-confidence reads (Flask `current_app.config`, renamed
`BaseSettings` fields) and dynamic reads are reported, never failures.
Files discovery couldn't read (over 1 MB, unreadable) are listed, and
never make a check incomplete: discovery is partial by nature (it doesn't
see framework-style reads such as Django settings), so its coverage is
reported, not treated as a source. "Declared but not referenced" is
reported in `--json` only, as informational.

Discovery is `discovery.discover_references`, which `explain` also uses:
`scan`'s patterns, plus

- pydantic `BaseSettings` fields (the upper-cased field name, or a
  `Field(alias=...)`), at `high` confidence, or `medium` when the class
  configures an `env_prefix`, alias generator, case sensitivity, or
  nested delimiter (the name actually read is then only a guess);
- reads whose key isn't a literal (`os.getenv(name)`, `process.env[k]`),
  as `dynamic_references`.

`scan` and `undeclared` keep their discovery unchanged, so what blocks a
commit or lands in SARIF doesn't change as a side effect; moving them
onto `discover_references` is a separate decision. For a project with
`BaseSettings` classes, `check` can therefore flag a field `scan` doesn't.

JS/TS discovery, shared by every command, no longer reports a match that
starts inside a comment or a string literal; template-literal `${...}`
expressions are still code. That removes false positives from `scan` and
`undeclared` too.

**Not migrated yet: code no service owns.** `scan` checks every file,
and flags a read in a file under no service's directory (e.g. a shared
`modules/` library) against an empty contract. `check` is per service, so
such a file is only covered when a service lists its directory in
`additional_source_roots` (BL-106). On Zeus (archived config) that's 21
names `scan` reports and `check` doesn't; for owned files the two agree
exactly. `scan`'s check must stay until this has a decided home.

`undeclared` (reads introduced between two revisions, JSON/SARIF) is a
different question and is unchanged.

## D-8. Frozen, not removed

Shared-schema projection (`services` scoping), the physical-file union
contract, `completeness: union`, and the Compose/Kubernetes adapters stay,
unchanged. The only evidence against them is that no verified real
project uses them today (KemonChilo is single-service; Zeus is not a
current user), which doesn't justify deleting working, tested behavior in
the middle of a migration. They sit behind `load_schema(service)` and
`parsers.factory`, so the evaluator consumes them without depending on
their internals. Removing any of them is a separate decision (charter §3
principle 7).

## D-9. Verdicts and report version 2 (V1-2, #16)

`check` gives every variable one of five verdicts, by the rules in
[v5-decisions.md](v5-decisions.md) (plan §10), computed in
`envshield/core/verdicts.py` from each source's typed value states (V1-1).
**VALID only ever comes from a Concrete value that was read and
validated.** `test_verdicts.py` checks that over every golden, matrix and
union fixture.

**Not yet the gate.** Until V1-3 (#17), the exit code, `success`,
`results`, `combined` and `summary.clean` are computed as before (D-6).
A run can therefore print COULD NOT VERIFY and still exit 0. *Superseded
by D-10: the exit code and `success` now follow the verdict.*

**JSON (additive).** Top level: `verdict`, the invocation's verdict (the
highest-precedence service verdict). Each report keeps every v1 field with
its meaning and adds:

| Key | Meaning |
|---|---|
| `report_version` | `2` |
| `variables[].verdict`, `.reason_code`, `.detail` | The variable's verdict: `valid`, `invalid`, `unverified` (COULD NOT VERIFY), `not_required` or `error`, and its reason code from the closed sets |
| `variables[].sources[].verdict`, `.reason_code` | The same, for each source |
| `summary.verdict`, `summary.counts` | The service's verdict, and how many variables got each verdict |
| `findings` | One entry per variable × source that is `invalid` or `unverified` (in union mode, one per variable), one per undeclared code read, and one per service- or source-level failure |
| `errors`, `invalid`, `unverified` | `findings`, split by verdict |
| `coverage` | `files_scanned`, `skipped`, `dynamic` for code references; outside the verdict except `undeclared_reference` |

**The finding shape**, shared by every command that emits findings (#42):

| Field | Value |
|---|---|
| `kind` | `variable`, `code_reference`, `source` (a whole source failed; `variables` lists the names it would supply) or `service` |
| `verdict`, `reason_code`, `detail` | As above; `detail` names objects and constraints, never a value |
| `variable`, `service` | Names; `variable` is null for a `source`/`service` finding |
| `location` | `{file, line, container}`, or null when the finding has no single place (a union variable missing everywhere) |
| `secret` | Whether the variable is declared `secret` |
| `next_step` | `{by: "agent" \| "human", text}`. `agent`: fixable from the repository alone; `human`: needs a value, access or a decision. Never a value, never an interactive command |

**Decisions made while implementing** (the plan doesn't settle them):

- A `requiredIf` whose trigger isn't Concrete makes the dependent COULD
  NOT VERIFY (`ambiguous_requirement`) only when its verdict depends on
  the requirement: absent, or empty and otherwise valid. A present value
  is judged on its own, since it must be valid either way. This matches
  how it replaces v4.8.0's `unresolved`, which also applied only to an
  absent or blank dependent.
- A trigger absent from a manifest that has an unbound envFrom is
  ambiguous too, since the envFrom could supply it.
- In union mode a source doesn't judge a name it lacks; the union does.
  The union's requiredness is ambiguous when sources disagree or any of
  them is ambiguous. A source that fails or is missing contributes no
  values, and is reported as its own finding, so a passing union can't
  hide it (BL-030).
- A Compose `env_file` marked `required: false` that doesn't exist is
  skipped, as Compose skips it; a required one gives `file_not_found`.
- A Kubernetes `fieldRef`/`resourceFieldRef` is `external_reference`.
- An unreadable source is ERROR (`source_unreadable`), no longer a crash
  (BL-196).

**Human output** prints, per source (in union mode, once for the union):
Errors, Invalid, Could not verify, then valid and not-required lines, and
ends each service with a line like `INVALID: 1 invalid, 2 could not be
verified, 1 valid.` It replaces the v1 table and its suggestions; next
steps are in the JSON findings.

## D-10. The gate and `--accept-unverified` (V1-3, #17)

**Before.** `check`'s exit code and `success` came from the v4.8.0 diff
(D-6): a value EnvShield never saw (a Compose host forward, an external
Kubernetes reference, an in-file Secret) passed, though V1-2 already
called it COULD NOT VERIFY.

**Now** (plan §10, in [v5-decisions.md](v5-decisions.md)). The verdict says
what EnvShield found; the gate says whether the pipeline may proceed.
`verdicts.gate()` computes it from the invocation's verdict and its
COULD NOT VERIFY findings:

- **Exit 0:** the verdict is VALID, or COULD NOT VERIFY with every
  finding's reason accepted. **Exit 1:** anything else, INVALID and ERROR
  included. **Exit 2:** a usage error.
- **`--accept-unverified=REASON[,REASON]`**, repeatable, accepts only
  `host_forward`, `external_reference` and `value_not_inspected`, for the
  whole invocation. `ambiguous_requirement`, any INVALID or ERROR reason,
  `all`, an unknown name or an empty value is a usage error (exit 2,
  `verdicts.parse_accepted`).
- **The verdict never changes.** JSON adds `gate: {"result": "pass" |
  "fail", "accepted": {"<reason>": ["NAME", ...]}}`, and `success` is
  `true` only when the verdict is VALID: `false` for an accepted run even
  though it exits 0.
- **Human output** ends an accepted run with `PASSED WITH UNVERIFIED
  VALUES (accepted: ...)`, after every `?` line. A run that fails only on
  unverified values ends with `FAILED: some values could not be verified
  (...)` and the `--accept-unverified=` that would pass it, or says why
  `ambiguous_requirement` can't be accepted.

**Decisions made while implementing:**

- `accepted` lists, per accepted reason, the variables it covered in this
  run; a reason that covered nothing maps to `[]`. It's reported on a
  failing gate too.
- The v1 fields `results[].clean`, `combined` and `summary.clean` keep
  their v4.8.0 meaning (plan: "the v1 fields released in 4.8.0 stay
  through v5"), so they can say clean when the gate fails on an
  unverified value. `success` is the one v1 field that follows the gate.
- A service with nothing required is VALID, not `not_required` (BL-197).
- `doctor`, `scan`, `undeclared`, `schema diff` and the hooks are
  unchanged; the hooks don't run `check`.
- Every Compose operator (`-`, `:-`, `?`, `:?`, `+`, `:+`) is keyed by
  the host variable it names, like `${VAR}`, and `?`/`:?`/`+`/`:+` are no
  longer read as literal text in the v1 fields (BL-195). This changes v1
  `results`, `doctor`'s manifest check and `import` for those operators;
  no exit code on the differential moves.
- A Compose value with any unescaped reference (`http://${HOST}:8000`,
  `$VAR`, `${A:-${B}}`) is never judged on its text: it's Forwarded, and
  COULD NOT VERIFY (`host_forward`). Only a value with no reference is
  Concrete, with `$$` unescaped to `$` (BL-199, #68).

**Effect.** On the 22 golden scenarios and a 30-scenario differential
against `main`, no exit code changed: each one that already failed had an
INVALID or ERROR, and the golden text gains only the closing line on
`kubernetes_unresolved_envfrom`. What changes is the class those
scenarios didn't cover, which the plan intends (F1/F2/F4/F7): a check whose
only problems are unverified values now exits 1.
