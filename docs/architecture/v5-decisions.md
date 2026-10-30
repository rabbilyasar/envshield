# v5 decisions

The decisions behind EnvShield v5, recorded in the repository by V0-1
([#14](https://github.com/rabbilyasar/envshield/issues/14)).

**Sources.** The approved v5 plan ("EnvShield v5 — Product & Technical
Implementation Plan", approved and frozen 2026-10-07, private artifact
`claude.ai/artifact/QWiPyjtKjWQyQhsUxyVy2V`), and the plan amendment A1–A4,
approved by the maintainer on 2026-10-14 and posted on #14. Nothing here
reopens, reinterprets or extends a decision.

**What is verbatim.** The decision record (plan §20) and the rule text of
plan §7, §10 and §11 are copied word for word. The plan is an HTML page, so
its formatting is carried over as Markdown: tables, bold, italics and code
are kept, and an approval label is shown as `[Approved]`. One table in §10
(Compose interpolation) merges cells across rows 2–3 and 4–5; Markdown can't
merge cells, so the merged text is repeated in each row. Section references
inside the copied text (§4, §8, §10, §11) point at the plan, not at this file.

**What isn't here.** The plan's D-2 requiredness-transition table lives in
[evaluator-decisions.md](evaluator-decisions.md). V4-1
([#23](https://github.com/rabbilyasar/envshield/issues/23)) updates it, since
that's where the `requiredIf` + `defaultValue` change (Q3) is implemented.

**Build order.** [ROADMAP.md](../../ROADMAP.md)'s "v5 — in progress"
section is the authoritative order. It was written with A3 on 2026-10-14
and differs from A3's wording in one place: it puts the CLI work (V0-2b #42,
V6-2 #45, and since 2026-10-19 V6-3 #63) before onboarding (V3).
**2026-10-21, maintainer decision:** that CLI work also moves ahead of V2
(one contract). The order is now V1 → V4 → V5 → CLI consolidation (#42,
#45, #63) → V2 → V3 → V6 → V7. The evidence is in BACKLOG.md's `BL-201`.

## Decision record (plan §20)

Each decision appears once. The last column is added here: it records only
what A1–A4 changed, or where a decision has since been implemented.

| # | Final decision | Status | Depends on | Since the amendment |
|---|---|---|---|---|
| Q1 | Environment names under `environments:` in `envshield.yml`; `local` built in; undeclared names are load errors | Approved | — | — |
| Q2 | `requiredIn = [..]`; at most one of `required`/`requiredIn`/`requiredIf`; combining with `requiredIf` deferred | Approved | Q1 | — |
| Q3 | `defaultValue` is a local fallback applied only when absent, never to an explicit empty value; never changes requiredness (`requiredIf` + default becomes conditional); never runtime evidence; generated defaults are app behavior; no secret defaults; no service-level defaults flag; absent ≠ empty ≠ value; template empties count as absent | Approved | Q2 | — |
| Q4 | Five verdicts; VALID only for a read and validated concrete value; precedence ERROR > INVALID > COULD NOT VERIFY > VALID, NOT REQUIRED neutral, nothing hidden; exit 0/1/2, no 3; `--accept-unverified=REASON[,REASON]` (closed set, gate only); runtime boundary; Compose matrix; F1–F7 covered | Approved | N1 | — |
| Q5 | In-file K8s Secret with an existing key → COULD NOT VERIFY (`value_not_inspected`); decoding Secret `data` deferred | Approved | Q4 | — |
| Q6 | Setup precedence: existing value (including explicit empty, never overwritten without confirmation) → provider (no silent fallback) → default only when absent → example (non-secret, non-empty, valid) → prompt → SET YOURSELF; `--refresh` | Approved | Q3, Q7 | Step 2 (provider) is subject to A2. |
| Q7 | Top-level `secrets:` map in `envshield.yml`; native `op://`; scheme dispatch; setup only | Superseded | — | Returned to planning by A2 ([#22](https://github.com/rabbilyasar/envshield/issues/22)); replaced 2026-10-30 by the secret-manager design ([secret-managers.md](secret-managers.md)): `secrets.run` (the team's run command), no provider code. |
| Q8 | References shown and confirmed every interactive run; `--yes` required non-interactive | Superseded | Q7 | Replaced 2026-10-30: the exact `secrets.run` command is approved once per developer (direnv-style), asked again when it changes, never run without a terminal ([secret-managers.md](secret-managers.md)). |
| Q9 | `check --runtime`: process environment only, no file, exclusive with `--process-env`, no acceptance flag, explicit service selection | Approved | Q4 | — |
| Q10 | Top-level `schema:`; append-only contract writes; appended secrets always scoped | Approved | — | — |
| Q11 | Explicit `schema merge`; conflicts refused; confirmed `envshield.yml` change; verdict-equivalence gate | Approved | Q10 | — |
| Q12 | `extends` frozen, loaded, no warning in v5 | Approved | — | — |
| Q13 | v5.0.0 major after Phase 7, justified by report v2 and behavior changes, not exit codes | Approved | Q4, Q17 | The phase order is amended by A3. |
| Q14 | Decisions in Phase 0; GitHub Issues are the execution tracker; per issue: issue → branch → scoped work → tests → verification → commit → PR → review → merge only on explicit authorization → close; insufficient architecture returns to planning. Applies to v4.8.0 and v5. BACKLOG.md remains the findings record. | Approved | — | — |
| Q15 | `--process-env` stays opt-in | Approved | Q9 | — |
| Q16 | `generate`: a variable not required everywhere is optional; defaults remain loader fallbacks (documented as app behavior); empty-number guard (F8, in v4.8.0) | Approved | Q2, Q3 | — |
| Q17 | Release the current state as v4.8.0 (not v4.7.5) with F3, F5, F6, F8, BL-162, report honesty for F1/F2/F4/F7 (v4 gate kept), bookkeeping, CHANGELOG and release review | Approved | Q14 | — |
| Q18 | `setup` is local-only | Approved | — | — |
| Q19 | Remaining details settled by tests during implementation: `op read --` support, `op` stderr fragments. (Missing Compose `env_file` → INVALID is settled.) | Approved (deferred to implementation) | — | The `op` details are subject to A2 (#22). |
| N1 | Typed value states replace `UNRESOLVED_VALUE` | Approved (with Q4) | — | Implemented by V1-1 ([#15](https://github.com/rabbilyasar/envshield/issues/15), PR #65). |
| N2 | The v5 verdict model applies to `check`; `doctor`, `scan`, `undeclared`, `schema diff` keep their contracts | Approved | Q4 | — |
| N3 | Setup's explicit-empty handling (§8 table) | Approved (with Q6) | Q3 | — |
| N4 | Resolving Compose interpolation against the host environment is deferred beyond v5 | Approved | Q4 | — |

## Plan amendment A1–A4 (approved 2026-10-14)

These are new decisions, not reinterpretations of Q1–Q19 or N1–N4. Copied
from the amendment as approved:

- **A1 · Positioning.** The three stages stay. Development and deployment lead the story: "every service and every deploy gets the config it needs, and you find out before it breaks." Onboarding works *with* the team's vault rather than being the headline. Evidence: BL-174 (1Password Developer Environments and Varlock already ship vault → `.env`, without writing values to disk).
- **A2 · V3-3 redesign.** Provider support is redesigned before implementation: validate or reference what the provider supplies (for example a 1Password Environment or `op://` references) against the schema, instead of copying secret values into a plaintext `.env`. #22 returns to planning; see the comment there. **Resolved 2026-10-30 (maintainer):** EnvShield works with any secret manager through the team's own run command and checks what it injects; it never fetches, stores or copies a value. Design: [secret-managers.md](secret-managers.md).
- **A3 · Sequence.** V1 (verdicts, gate) → V4 (environments) → V5 (`--runtime`) → V2 (one contract) → V3 (setup) → V6 → V7. Positioning (V0-3, #43) happens now.
- **A4 · CLI consolidation in v5.0.0.** V0-2 (#42): commands grouped by stage, overlapping ones deprecated (kept working through v5).
- **AI agents (amended 2026-10-26, maintainer decision):** EnvShield is the deterministic configuration check in an agent's loop, and v5.0.0 ships agent setup (V6-3, #63): `init`/`agent install` writes a short `AGENTS.md`/`CLAUDE.md` section and the Claude Code/Codex hook config, and `agent guide` prints version-matched instructions for running the check *and* maintaining the schema (declare new variables, `secret`, `requiredIn`, never a secret value; `by: "human"` steps are reported, never invented). This replaces the earlier "no new feature in v5.0.0" line. No MCP server in v5; BL-040's questions still apply. Evidence: BL-192, BL-201. Implementation decisions (maintainer, 2026-10-27, on #63): the hook runs `check --since HEAD --code-only` through a hidden `envshield agent hook` that is silent on a pass and exits 2 with a short stderr summary on a failure (the signal Claude Code and Codex both feed back to the model), so a local secret the agent can't supply never fails an edit; the Claude Code hook goes in the committed `.claude/settings.json`, Codex's in `.codex/hooks.json`; `init` offers the setup on a terminal and names `envshield agent install` otherwise. On #89 (2026-10-27): `agent install` adds exact allow rules to `.claude/settings.json` for the four commands the instructions name (guide, both checks, `schema sync`), never a prefix, and nothing for Codex; because Claude Code applies project allow rules only in a trusted folder, it also prints the same rules as an `--allowedTools` flag for `claude -p` and CI.

Where the plan text below mentions provider references, `secrets:`, `op://` or
1Password, A2 applies: that design is back in planning.

## Plan §7 — Environments, defaults and empty values

### Environments [Approved] Q1

- **Environment:** a named context with its own configuration requirements: `local`, `staging`, `production`.
- `local` always exists: a developer machine, configured by files that `setup` writes.
- Other environments are declared by name in `envshield.yml` and carry no settings in v5.

### Requiredness in environment E [Approved] Q2, Q3

At most one of `required`, `requiredIn` or `requiredIf` may be set; more is a load error (`required` + `requiredIf` is already rejected today).

| Schema | Required in E? |
|---|---|
| none of the three (legacy), with or without `defaultValue` | always, in every environment, as today |
| `required = true` | always |
| `required = false` | never; a present value must still be valid |
| `requiredIn = [..]` | when E is listed; otherwise optional |
| `requiredIf = {var, equals}`, with or without `defaultValue` | when the condition holds against E's observed values. **Change:** today a `defaultValue` makes this "always"; in v5 a default never changes requiredness. |
| `requiredIf` whose trigger is not a `Concrete` value (forwarded, external, key present) | **ambiguous**: the dependent variable is COULD NOT VERIFY (`ambiguous_requirement`), never dropped (F3). An empty trigger is a concrete `""` and compares normally. |

An existing schema behaves the same in every environment, so declaring environments changes no verdict until a variable uses `requiredIn`. Combining `requiredIn` with `requiredIf` is deferred (additive later).

### `defaultValue` [Approved] Q3

> **A schema `defaultValue` is a fallback for local configuration. It applies only when the variable is absent. It does not apply to an explicitly empty value.**
>
> `setup` writes the default into a developer's local file only when the variable is absent from that file and no provider reference is configured for it. A default never changes whether a variable is required, and it is never evidence that a runtime value exists. `check` judges only values it actually reads, in every environment including `local`. Generated application defaults are application behavior, not EnvShield verification. Secrets cannot have defaults.

| Context | `defaultValue` behavior |
|---|---|
| `setup` (local) | Written only when the variable is absent and no provider reference exists. Never replaces an existing value, empty or not. |
| `check` (local) | Not evidence. A required defaulted variable absent from `.env` is INVALID (`missing`), with the hint "has a default; run `envshield setup`". |
| `check --env X` (manifests) | Not evidence. A Compose `${VAR:-x}` fallback is a manifest's text, not a schema default (§10). |
| `check --runtime` | Not evidence. Absent and required is INVALID; absent and not required in E is NOT REQUIRED. |
| `schema sync` | Writes the default into `.env.example` (unchanged). |
| `generate` | Emitted as the loader's fallback. The loader applies it at startup; `check` never does. Documented, not changed. |
| Optional variable | `setup` still writes the default when absent. `check` never requires it; a present value must be valid. |
| Secret | No default (unchanged; `SecretDefaultConflictError`). |

If an application supplies its own default in some environment, the contract says so by not requiring the variable there: `required = false`, or a `requiredIn` that omits that environment.

### Absent ≠ empty ≠ value [Approved] Q3

| State | `.env` | Process environment | Python config |
|---|---|---|---|
| Absent | no line, or a bare `PORT` line (ignored, like python-dotenv) | not in `os.environ` | no assignment |
| Empty | `PORT=` or `PORT=""` | `PORT=""` | `PORT = ""` |
| Value | `PORT=8000` | `PORT=8000` | `PORT = "8000"` |

**How `check` judges each state** (in every environment):

- **Absent:** INVALID (`missing`) if required in E; otherwise NOT REQUIRED. A default is never applied.
- **Empty, required in E:** INVALID (`blank`), whatever its `defaultValue`.
- **Empty, optional:** validated like any other value. A typed field (`port`, `int`, `float`, `bool`, `url`, `email`, `enum`) is INVALID (`invalid_value`). With a `pattern`, INVALID unless the pattern matches `""`. An unconstrained `string` is VALID.
- **Value:** validated against type, enum and pattern.
- With `--process-env`, an empty process value overrides the file's value (D-3, unchanged), the way dotenv loaders behave.

**Template exception:** `schema sync` writes `KEY=` into `.env.example` for every variable without a default. An empty value *seeded from `.env.example`* means "no example value" and counts as absent. An empty value in the *developer's local file* is explicit and is never treated as absent.

### Environment selection in the CLI [Approved]

- `--env NAME` on `check`, `explain` and `doctor`. Precedence: `--env`, then `ENVSHIELD_ENV`, then `local`. An undeclared name is an error listing the declared names.
- `setup` is local-only (Q18).

### Deploy targets and environments

A manifest entry may carry `environment:`. `check --env X` evaluates manifests tagged X against X's requirements. Plain `check` evaluates the local file and untagged manifests (today's behavior). `doctor` warns when environments are declared but no manifest is tagged. `check --runtime --env X` evaluates the process environment against X's requirements (§11).

### `schema diff`

Requiredness transitions are classified per environment using D-2's table; the change's category is its most severe per-environment category. JSON adds `detail.environments` only when the categories differ, so single-environment output is unchanged. The `requiredIf` + default change updates D-2's table.

### `explain`

```
STRIPE_SECRET_KEY   secret · granted to: api
  Required in   staging, production
  Optional in   local
  Value source  1Password (op://Engineering/stripe-test/secret-key)   # reference only, never a value
```

## Plan §10 — Verdicts and gate

This section applies to `check`. `doctor`, `scan`, `undeclared` and `schema diff` keep their current contracts in v5 (N2).

### Five verdicts [Approved] Q4

| Verdict | Meaning | Examples |
|---|---|---|
| **VALID** | EnvShield read a concrete value and it satisfies everything the contract requires in this environment. | `PORT=8000`; a runtime value that passes; an optional unconstrained string `""` |
| **INVALID** | The evidence EnvShield read shows a contract violation. | `PORT=` (required, or typed); `PORT=abc`; a required variable absent from its configured source; a configured local file or Compose `env_file` that doesn't exist; a manifest fallback that breaks the contract; a missing key in an in-file Secret; `extra`, `out_of_scope`, an undeclared high-confidence code read (D-7) |
| **COULD NOT VERIFY** | The value or the requirement lies outside the evidence available to EnvShield. | Compose host forwards; K8s Secrets/ConfigMaps outside the manifest; an in-file Secret whose key exists; a `requiredIf` trigger that is itself unverified |
| NOT REQUIRED | Not required in this environment, and absent. Neutral. | An optional variable that isn't set |
| **ERROR** | EnvShield could not perform the evaluation. Says nothing about the configuration. | Malformed contract or `envshield.yml`; a manifest or source that won't parse; an unreadable file; an internal failure |

> **Invariant: VALID requires a concrete value that EnvShield actually read and validated.** No sentinel, unresolved reference, forwarded host value, fallback, placeholder or unreadable requirement may produce VALID. In code: VALID implies a `Concrete` value state was validated (§4). A class-level test enforces it over every parser and source fixture.

**ERROR is never COULD NOT VERIFY.** A source that hits ERROR contributes nothing for the variables it would supply; those variables are listed under the error, not marked unverified. A contract or topology error stops that service's evaluation; other services are still evaluated.

### Reason codes (closed sets)

| Verdict | Reason codes | Acceptable with `--accept-unverified`? |
|---|---|---|
| INVALID | `missing`, `blank`, `invalid_value`, `file_not_found`, `invalid_fallback`, `extra`, `out_of_scope`, `undeclared_reference` | never |
| COULD NOT VERIFY | `host_forward`, `external_reference`, `value_not_inspected` | yes, each named explicitly |
| COULD NOT VERIFY | `ambiguous_requirement` | **never**: the requirement itself is unknown |
| ERROR | `contract_error`, `topology_error`, `source_parse_error`, `source_unreadable`, `internal_error` | **never** |

### Source matrix

| Source / case | Value state | v5 verdict (reason) |
|---|---|---|
| `.env` / Python config / process environment: value | `Concrete` | VALID / INVALID (`invalid_value`) |
| Same sources, or a literal in a manifest: a reference or an encrypted value (`op://`, `sm://`, `berglas://`, `vault:`, dotenvx `encrypted:`, SOPS `ENC[...]`) | `Referenced` | COULD NOT VERIFY (`external_reference`), whatever the field's type: "a 1Password reference" (`BL-213`, #103) |
| Same sources: empty | `Concrete("")` | §7 empty rule: INVALID (`blank` or `invalid_value`) or VALID |
| Same sources: absent | `Absent` | INVALID (`missing`) if required; NOT REQUIRED otherwise |
| Configured local file doesn't exist | — | INVALID (`file_not_found`) for required variables (keeps today's exit 1) |
| Source fails to parse / can't be read | — | ERROR (`source_parse_error` / `source_unreadable`) |
| K8s literal `value:` | `Concrete` | VALID / INVALID |
| K8s `secretKeyRef`, Secret in file, key exists | `KeyPresent` | COULD NOT VERIFY (`value_not_inspected`) — Q5 |
| K8s `secretKeyRef`, Secret in file, key missing | `Absent` | INVALID (`missing`) |
| K8s `secretKeyRef`/`configMapKeyRef`, object not in file | `External` | COULD NOT VERIFY (`external_reference`): "Secret 'x' is defined outside this manifest" |
| K8s `envFrom`, object not in file | `External` | COULD NOT VERIFY (`external_reference`) for each required variable not otherwise supplied |
| Compose `env_file` exists / missing | `Concrete` / — | VALID/INVALID / INVALID (`file_not_found`; Compose itself refuses to start) |
| Provider reference | — | Not a `check` source |
| Future adapters | must map each key to a typed state | Only `Concrete` can be VALID |

### Compose interpolation [Approved]

Compose interpolates from the deploy host's shell environment plus the project `.env`/`--env-file`. A static manifest check sees none of them, so it never claims to have verified a host value.

| # | Expression, host state | Container receives | Static `check --env X` | Reason |
|---|---|---|---|---|
| 1 | `${VAR}` or bare `KEY`, any host state | host value; `""` if unset (Compose warns) | COULD NOT VERIFY | `host_forward` |
| 2 | `${VAR-x}`, host absent | `x` | COULD NOT VERIFY, if `x` is valid | `host_forward`; detail: "with `-`, an empty host value reaches the container empty" |
| 3 | `${VAR-x}`, host empty | `""` | COULD NOT VERIFY, if `x` is valid | `host_forward`; detail: "with `-`, an empty host value reaches the container empty" |
| 4 | `${VAR:-x}`, host absent | `x` | COULD NOT VERIFY, if `x` is valid | `host_forward`; detail: "fallback applies when the host value is unset or empty" |
| 5 | `${VAR:-x}`, host empty | `x` | COULD NOT VERIFY, if `x` is valid | `host_forward`; detail: "fallback applies when the host value is unset or empty" |
| 6 | Fallback `x` violates the contract (including an empty `x` for a required or typed variable) | `x` whenever the fallback applies | **INVALID** | `invalid_fallback`: the manifest's own text breaks the contract in a reachable host state |
| 7 | Concrete value in the manifest | that value | VALID / INVALID | the value itself |
| 8 | `${VAR:?err}`, `${VAR+alt}` | host-dependent | COULD NOT VERIFY | `host_forward` (today these stay literal text) |

Cases 2–5 can't be told apart statically because the host state is unknown; their verdict is the same and only the detail differs. To verify the effective value, run `check --runtime` inside the container. Resolving interpolation against the deploy host's environment is deferred beyond v5 (N4).

### Aggregation [Approved]

**Precedence: ERROR > INVALID > COULD NOT VERIFY > VALID.** NOT REQUIRED is neutral. ERROR ranks first because an evaluation that errored can't claim its INVALID list is complete.

- **Levels:** per variable per source → per variable → per service and environment → per invocation (the highest-precedence service verdict).
- **Union mode** (`completeness: union`, frozen per D-8): a value verified VALID in any source makes the variable VALID; a non-concrete state counts as COULD NOT VERIFY, never "ok".
- **Nothing is hidden by precedence.** `summary.verdict` is the highest-precedence verdict, `summary.counts` covers all five, and `errors[]`, `invalid[]` and `unverified[]` are each listed in full with reason codes. Human output always prints, in order, Errors, Invalid, Could not verify.

| Mix | Verdict | Gate |
|---|---|---|
| All VALID (+ NOT REQUIRED) | VALID | pass |
| 8 VALID + 1 COULD NOT VERIFY + 1 INVALID | INVALID (all three sections listed) | fail |
| VALID + COULD NOT VERIFY, or all COULD NOT VERIFY, or + NOT REQUIRED | COULD NOT VERIFY | fail; pass only if every unverified reason is accepted |
| INVALID + COULD NOT VERIFY | INVALID | fail |
| INVALID + ERROR | ERROR (INVALID still listed) | fail |
| ERROR in part, rest VALID | ERROR | fail |
| No required variables | VALID ("0 required variables") | pass |

### Gate and exit codes [Approved]

The verdict says what EnvShield found. The gate says whether the pipeline may proceed. They are reported separately.

- **0** = gate passes: the verdict is VALID, or COULD NOT VERIFY with every unverified reason explicitly accepted.
- **1** = gate fails: anything else, including INVALID and ERROR.
- **2** = CLI/usage error (Typer, and `--accept-unverified` misuse).

Compared with v4 the exit contract is unchanged (v4 already exits 0 only when every service passed, 1 otherwise, 2 for usage errors; a malformed configuration stays exit 1). The intentional behavior changes are: configurations that used to pass now fail (F1/F2/F4/F7 gate), a gate may pass with an unverified verdict only through explicit acceptance, and the flag adds new usage errors.

### `--accept-unverified=REASON[,REASON]` [Approved]

- **Closed set:** `host_forward`, `external_reference`, `value_not_inspected`. Repeatable. No `all`.
- **Usage errors (exit 2):** an unknown reason, `ambiguous_requirement`, any ERROR reason, `all`, and any use together with `--runtime`.
- **The verdict never changes.** Only the gate does. Scope is the whole invocation.
- **Human output** never prints VALID for an accepted run. It lists every `?` line and ends with "PASSED WITH UNVERIFIED VALUES", naming the accepted reasons.
- **JSON** keeps the unchanged `verdict` and adds `gate: {"result": "pass", "accepted": {"host_forward": ["DATABASE_URL"]}}`.
- **Forbidden with `--runtime`:** every runtime value is visible, so COULD NOT VERIFY can't legitimately occur there; if it does, it's a bug, and accepting it would hide the bug.
- Per-manifest acceptance in `envshield.yml` is a possible later refinement, not v5.

### Report v2

- `report_version: 2`.
- Per variable and per source: `verdict` (`valid | invalid | unverified | not_required | error`) and `reason_code` from the closed sets above.
- `summary: {"verdict": "valid|invalid|unverified|error", "counts": {…}}`; `errors[]`, `invalid[]`, `unverified[]`; `gate`.
- A `coverage` block for code references (`files_scanned`, `skipped`, `dynamic`): reported, outside the verdict except high-confidence undeclared reads (D-7).
- **`success` stays strict:** `true` only when the verdict is VALID, and `false` under accepted unverified values even though the exit code is 0, so v4 consumers fail safe. The v1 fields released in 4.8.0 stay through v5.

### Human output

```
api · production · k8s/production.yaml
Invalid
  ✗ STRIPE_SECRET_KEY   missing (required in production)
Could not verify
  ? INTERNAL_TOKEN      Secret "shared-tokens" is defined outside k8s/production.yaml (external_reference)
  ? DATABASE_URL        Secret "db" is in this file; its value isn't inspected (value_not_inspected)
  ✓ LOG_LEVEL           valid
  · SENTRY_DSN          not required in production

INVALID: 1 invalid, 2 could not be verified, 1 valid.
```

## Plan §11 — Deployment model and runtime boundary

### Two different questions

|  | Manifest check | Runtime check |
|---|---|---|
| Question | Does this manifest supply everything production requires, as far as the manifest shows? | Does the environment this check runs in have everything production requires? |
| Command | `envshield check --env production` | `envshield check --runtime --env production [--service api]` |
| Where it runs | CI or locally, on the repository | Inside the launch path, as the last step before the application starts |
| Sources | Manifests tagged `production` | The process environment only; no `.env` read or required |
| Typical verdict | Often COULD NOT VERIFY for secrets | VALID or INVALID (or ERROR); values are visible |

### The runtime boundary [Approved]

> **`check --runtime` establishes the configuration state of the environment in which EnvShield itself runs.**
>
> Deployment guarantees apply only when the check runs after all relevant environment transformations and immediately before the application launches, so that the application inherits the environment that was checked. A later environment mutation is outside the guarantee: a wrapper that changes variables after the check, an inline `VAR=… app` prefix, a different process or release phase, or a platform that injects variables differently at run time.
>
> EnvShield does not parse launch scripts, launchd plists, systemd units or other launcher configuration, and does not become the application launcher.

```
# Valid: the check sees exactly what the app inherits
export PORT="${PORT:-8000}"                      # wrapper substitutions first
envshield check --runtime --env production && exec app

# Not covered: the environment changes after the check
envshield check --runtime --env production
export PORT="${PORT:-8000}"                      # mutation after the check
exec app
```

**Guaranteed:** when placed as above, `check --runtime --env E` exits non-zero if any variable required in E for that service is absent, empty or invalid in that environment, or if EnvShield couldn't evaluate. It exits 0 only if every required value was read and is valid. **Not guaranteed:** that the step runs at all; that a later process receives the same environment; that a valid value is the right one (a valid URL to the wrong database).

### Runtime details [Approved] Q9

- **Service selection:** `--service`, then `ENVSHIELD_SERVICE`, then the only service. Several services and none named is an error. Directory inference is off.
- **Inputs:** `env.schema.toml` and `envshield.yml` in the working directory (or `--project DIR`), and EnvShield installed. Only names in the service's projection are read from the environment (D-3).
- **Code references** are not evaluated (source may not ship in the image).
- `--runtime` and `--process-env` are mutually exclusive. `--process-env` stays the opt-in local overlay (Q15). `--accept-unverified` is forbidden with `--runtime`.
- Remediation text in runtime mode names the deploy environment, not "run setup" (fixes B3). Runtime mode needs no local file (fixes B2's deploy case).

### CI

- Manifest checks in CI commonly return COULD NOT VERIFY for secrets. A team that accepts specific reasons runs, for example, `check --env production --accept-unverified=external_reference`, and still sees every `?` line.
- Docs show placements: platform release command (Railway, Heroku, Fly), Kubernetes init container, and the container entrypoint immediately before `exec`, each with its boundary caveat.

### Known adoption cost

The runtime check needs Python and EnvShield in the deploy environment. Mitigations: generated loaders validate at startup without EnvShield, and a release phase can run in a separate image. A standalone binary is deferred until demand shows.
