# EnvShield — Engineering & Product Charter

This is the durable charter for EnvShield: identity, competitive positioning,
engineering/security principles, and target architecture. It changes rarely,
and only when reality (a shipped capability, a market shift, an audit)
requires it to.

For the current strategic direction, see [ROADMAP.md](ROADMAP.md). For the
itemized, evidence-tagged engineering backlog — confirmed bugs, security
findings, limitations, and their reproduction evidence — see
[BACKLOG.md](BACKLOG.md). This charter says what EnvShield *is* and *is
not*; ROADMAP.md says what's *done*, *in progress*, and *next*; BACKLOG.md
is the operational record behind both.

Last revised: 2026-08-26, updating §19 to record that `BL-002` (the
`check --json`/`doctor --json` false-clean) is now fixed in code, per the
Engineering Task Workflow — see BACKLOG.md's `BL-002` entry for the full
account, including the two related findings (`BL-092` fixed, `BL-093`
left open) discovered alongside it. Prior same-day revision: adding a
"Versioning and Release Cadence" section between §22 and §23 — the
durable rule that an engineering task or backlog item being completed
must never, by itself, trigger a version bump or release — and correcting
§4's "Release blockers" subsection, which still pointed at ROADMAP.md's
non-existent per-finding table. Prior same-day revision: updating §19 to
record that `BL-001` (the
secret-plus-`defaultValue` leak) is now fixed in code, following the
first implementation pass done under this charter's own new Engineering
Task Workflow — see BACKLOG.md's `BL-001` entry for the full account.
Prior same-day revision: adding an explicit "Engineering Task Workflow"
index (read → understand → implement → test → self-review → finding
reconciliation → documentation consistency → final report → commit
boundary) ahead of the pre-existing "Finding and Evidence Management"
section, and correcting §22's commit-boundary diagram to include the
finding-reconciliation and documentation-consistency steps it previously
omitted. Prior same-day revision: reconciling this file against
[CLAUDE.md](CLAUDE.md) (its Claude-specific counterpart, which had received
several updates this one hadn't): a corrected §19 release-readiness claim
against live-verified findings (see BACKLOG.md's BL-001–BL-004), the §2
external secret-provider references direction, and this pointer to
BACKLOG.md itself. Prior revision: 2026-08-10, following a full-repository
audit. Anyone reading this charter should assume BACKLOG.md is the source
of truth for "is this bug still open," and ROADMAP.md's phase-status
framing is the source of truth for "does X actually exist," not this
document's prose.

---

## 1. Product identity

**EnvShield enforces configuration contracts across code, Git, and
deployments.**

The underlying product is still: **a configuration contract engine for
modern software.**

> Does this application have exactly the configuration it expects, in every
> environment and every place it runs — and does every change to that
> requirement get caught before it breaks something?

EnvShield owns **configuration integrity**, not secret storage. Secret
detection is a supporting capability that exists because misconfigured
secrets are one visible symptom of a broken configuration contract — it is
not, and must not become, the product's center of gravity.

EnvShield is **not** a secret vault, password manager, or replacement for AWS
Secrets Manager, Vault, Infisical, or Doppler. It is also not a generic
`.env` editor, and it is not trying to out-scan GitHub Secret Scanning,
Gitleaks, or GitGuardian at their own game. See §2 for why, concretely.

---

## 2. Competitive positioning

Configuration contracts are **not an uncontested category.** Treat every
claim of differentiation as something to defend with evidence, not assert.

| Product | What it actually owns | Why EnvShield doesn't compete there |
|---|---|---|
| **Varlock** (dmno-dev) | Schema-driven, committed `.env.schema`; type-safe resolution; automatic secret redaction in logs/bundles; leak detection; GitHub Actions validation on push; provider plugins (1Password, AWS, Azure, Vault); explicitly "AI-safe .env files." | This is the closest thing to a direct competitor on EnvShield's own thesis. It is JS/TS-first (Next.js/Vite integrations) and, as far as evidence shows, does not validate Docker/Kubernetes deployment manifests and does not do git-revision content diffing of the contract. Do not describe "configuration contract" as EnvShield's unclaimed territory — it isn't. |
| **GitHub Secret Scanning / Push Protection** | Default-on, continuously expanding secret detection (28+ new detectors across 15 providers and counting in 2026 alone), validity checks, extended metadata — for free, on every repo. | Fully commoditized, moving faster than any small team can track. EnvShield must not compete on breadth of secret-pattern detection. |
| **GitGuardian / Gitleaks** | Dedicated secret-scanning depth and history-scanning. | Same reasoning as above — a deeper bench than EnvShield can build, in a category EnvShield doesn't want to own. |
| **Infisical / Doppler / Vault** | Secret storage, distribution, rotation, access control. | Answers "where does the value live and how does it reach runtime," not "does the contract hold." Genuinely complementary — EnvShield should validate that a manifest correctly *references* what these tools provide, and should never retrieve or store the values themselves. |
| **Typed config libraries** (pydantic-settings, zod, t3-env, envalid) | Typed loading and validation *inside one language, one app*. | Already solved well, widely adopted, and not worth out-building feature-for-feature. EnvShield's codegen is valuable specifically as an *output* of a schema that also drives cross-language, cross-service, cross-environment validation — not as a standalone typed-loader competitor. |

### Where EnvShield actually differentiates

All five of these hold *only* insofar as the code backs them — see
ROADMAP.md for what's real today versus aspirational:

1. **Configuration contract changes across Git revisions** — schema-vs-schema
   diff, classified breaking/non-breaking, surfaced on a PR.
2. **Source-code → configuration dependency analysis** — knowing what a
   codebase actually reads from the environment, not just what a schema
   declares.
3. **Configuration impact analysis** — what breaks, and for whom, when a
   variable changes.
4. **Docker/Kubernetes deployment-manifest correctness** — validating that a
   real deployment artifact satisfies the contract, not just a local `.env`.
5. **Configuration integrity across polyglot monorepos** — one contract,
   many languages, many services, one source of truth.
6. **Eventually, configuration-aware AI coding workflows** — an agent that
   can reason about the contract, discovery, and deployment impact together,
   not just avoid printing a secret value from a single repo's schema
   (Varlock already does that part).

None of Varlock, the secret managers, or GitHub touch #1, #3, #4, or #5 today.
That is the real white space. It is also, per the audit, the area with the
thinnest actual implementation — differentiation and technical debt currently
occupy the same ground. Close the gap there before investing anywhere else.

### What EnvShield should not build

- A larger secret-pattern library or vendor-detector bench — commoditized,
  GitHub is winning it for free.
- A hosted secret vault or broker.
- A generic AI chatbot for configuration.
- Deeper codegen polish for its own sake — the generated code is already
  correct; more languages/frameworks isn't where the leverage is.
- Any feature justified only by "a competitor has it." See §12.

### Future direction: external secret-provider references (exploratory, demand-gated)

EnvShield may eventually let a schema field describe *where* a secret is
expected to come from — a provider reference (1Password, HashiCorp Vault,
AWS Secrets Manager, a platform-native Kubernetes Secret, etc.) — without
EnvShield ever storing, retrieving, or transmitting the actual value. The
boundary is exact and permanent: **EnvShield owns the contract describing
the secret; an external system owns the secret value.** This is a natural
extension of this section's existing stance toward Infisical/Doppler/Vault
above — a provider reference is configuration-contract metadata, not a
secret value, and validating that a deployment manifest points at the
expected provider is the same category of work as validating any other
deployment reference (§7).

This is exploratory and demand-gated, not a committed feature: no schema
field, provider abstraction, or integration exists today, and none should
be added to production code merely because the concept has been discussed
— design the exact schema representation only after real use cases
validate the shape. See ROADMAP.md's "Post-v1 direction" for tracking.
Building this must never turn into building a secret vault, secret
storage, secret rotation, or credentials-management platform — that
boundary above is permanent, not something this direction is allowed to
erode.

---

## 3. Engineering principles

1. **Correctness over speed.** Optimize for correctness, reliability,
   security, predictable behavior, and maintainability — never for feature
   count.
2. **Small changes.** One coherent change → tests → review → merge, over a
   large architectural rewrite.
3. **Reuse existing architecture.** Before introducing a new abstraction,
   confirm the existing implementation can't be safely extended.
4. **Do not rewrite working code unnecessarily.** Refactor only when it
   fixes a real problem, reduces complexity, creates a necessary boundary,
   improves correctness, or enables a required capability.
5. **Domain logic must not depend on presentation.** This is not aspirational
   — it is already mostly true (`schema_types.py` is shared by `check`,
   `doctor`, and `setup` with no drift) and it is why the one place it broke
   down (a masking gap shared by all three callers) was a single-point fix.
   Keep it that way as new engines (discovery, deployment, graph) are added.

## Engineering Task Workflow

Every engineering task — bug fix, feature, refactor, or architectural
change — follows this loop. Most steps are already governed in full by the
section named; this is the index that makes the sequence itself mandatory,
not a restatement of rules that live elsewhere.

1. **Read.** Before changing anything: read the relevant BACKLOG.md
   item(s), the relevant ROADMAP.md phase/direction, the applicable rules
   in this document, and the existing implementation, adjacent code, and
   tests for the area being touched.
2. **Understand.** Identify the actual root cause, not the symptom named by
   the bug report — a fix that patches only the reported call site while
   leaving the same defect reachable through a sibling caller is not done
   (this is exactly why §4's security invariants require testing the
   *class* of failure, not the reported instance). Identify which product
   requirement, engineering principle (§3), and security invariant (§4) the
   work touches. Check whether it overlaps an existing BACKLOG.md item or
   ROADMAP.md phase. Per §3's "reuse existing architecture" principle,
   confirm the existing implementation can't be safely extended before
   introducing anything new.
3. **Implement.** Smallest coherent change (§3). Do not silently expand
   scope, and do not implement unrelated backlog findings encountered along
   the way — record them instead (§13), unless a fix is directly required
   for the correctness/security of the current change, the relationship is
   clear, and the change is small and safe.
4. **Test.** Per §15.
5. **Self-review.** Per §20's Pass 1 (Engineering Review).
6. **Finding reconciliation.** Per "Finding and Evidence Management" below,
   including its Required review closeout.
7. **Documentation consistency.** Per "Finding and Evidence Management"'s
   Synchronization rules — update ROADMAP.md/CLAUDE.md/AGENTS.md only for
   what they respectively own; never move an individual finding into
   ROADMAP.md merely because it surfaced during implementation.
8. **Final report.** State what changed, why, the tests actually run and
   their results, security and architectural implications, documentation
   changed, BACKLOG.md items created or updated, new findings discovered,
   and anything intentionally deferred. Per §18: never claim a test passed
   unless it was run, never claim a bug is fixed without regression
   coverage where practical, never claim the working tree is clean without
   checking it.
9. **Commit boundary.** Per §22 — do not commit automatically; wait for
   explicit instruction.

For feature work, also run §20's Pass 2 (Product Review) — user value,
competitive overlap, differentiation, adoption friction — and apply §14's
rule against building a feature merely because a competitor has it.

**Scope discipline.** This loop does not mean "fix every problem
discovered." It means: discover → classify → record → decide separately. A
newly discovered issue normally becomes a BACKLOG.md item, not a silent
expansion of the current change.

---

## Finding and Evidence Management

BACKLOG.md is the single source of truth for engineering findings.

Whenever Claude/Codex discovers a bug, security issue, reliability problem,
incorrect documentation claim, compatibility issue, architectural limitation,
performance concern, user-feedback item, or other material finding:

1. Check BACKLOG.md for an existing finding before creating a new one.
2. If the finding already exists, update that item's evidence/status rather
   than creating a duplicate.
3. If it is new, create a stable BL-NNN entry with:
   - source/provenance
   - evidence status
   - priority
   - affected component
   - problem
   - current behavior
   - expected behavior
   - reproduction/evidence
   - impact
   - dependencies/relationships
   - decision/status
4. Preserve the original audit/report identifier as an alias where one exists.
5. Never silently discard a finding because it is inconvenient, low priority,
   already known, or outside the current implementation task.
6. Never promote NEEDS_EVIDENCE to CONFIRMED without recording the evidence
   that established it.
7. Never delete a historical finding. Mark it Fixed, Stale, Refuted, or
   otherwise appropriately resolved.
8. If a source report is unavailable, record the missing source explicitly
   rather than reconstructing or guessing its contents.
9. If a finding cannot be mapped confidently to its original source, preserve
   the ambiguity and record it as unresolved rather than inventing a mapping.

### Synchronization rules

- BACKLOG.md owns individual findings and their evidence/status.
- ROADMAP.md owns phase status, strategic prioritization, and release direction.
- CLAUDE.md and AGENTS.md own engineering principles and agent workflow.
- Do not duplicate detailed finding lists across these files.
- When a finding materially changes release status or current phase, update the
  relevant summary in ROADMAP.md and/or the current-phase section of the
  applicable agent charter, while keeping BACKLOG.md as the authoritative
  record.
- When fixing a finding, update BACKLOG.md as part of the same task. The
  finding must remain in the backlog with its resolution and evidence.
- Before declaring a task complete, reconcile any findings discovered during
  the task against BACKLOG.md.


### Required review closeout

At the end of every engineering investigation or implementation task, before
reporting completion:

1. Review all findings discovered during the task.
2. Search BACKLOG.md for each finding.
3. Add missing findings or update existing entries.
4. Update evidence/status for anything that was verified, refuted, or fixed.
5. Check whether ROADMAP.md or the current-phase section of CLAUDE.md/AGENTS.md
   is now materially stale because of the work.
6. Report the documentation reconciliation explicitly in the final response.

A task is not considered complete if a newly discovered material finding has
not been recorded or explicitly justified as already represented.

### Target architecture

```
CLI
  → application services (command orchestration, --service/--json plumbing)
    → domain: configuration contract (schema, validation, diff)
      → discovery & deployment engines (source discovery, Docker/K8s parsing)
        → shared, normalized results
          → presentation: CLI output / JSON / SARIF / GitHub PR annotations / MCP
```

Presentation layers (Rich tables, JSON serialization, SARIF, GitHub Actions
output, MCP responses) **consume** results from the domain and engine layers.
They do not compute anything the domain layer should own, and they never
duplicate a validation, diff, or classification rule that already exists
below them. When two presentation surfaces (e.g. `check`'s Rich table and
`check --json`) disagree on a result, the bug is almost always that one of
them reimplemented logic instead of consuming the shared computation — fix
it by removing the duplicate, not by reconciling the two copies.

This is a documentation of intent, not a mandate to rewrite what already
works. Apply it going forward, especially as discovery/deployment/graph
engines are built (§7).

---

## 4. Security principles

EnvShield handles security-sensitive information. The bar is unchanged from
before this audit, because the audit found real violations of it, not a
reason to weaken it:

1. Never expose secret values in normal output.
2. Never expose secret values in JSON.
3. Never expose secret values through exceptions or error messages.
4. Never expose secret values through MCP.
5. Never include secrets in telemetry.
6. Never store secret values in the configuration contract.
7. Never write secrets to logs.
8. Treat generated files as potentially sensitive.
9. Validate filesystem boundaries.
10. Consider symlink attacks.
11. Consider malicious repository contents.
12. Treat configuration files as untrusted input.
13. Fail safely.
14. Prefer explicit errors over silent assumptions.

**Any bug that can expose a secret is P0.**

### Security invariants

A patch that fixes one reported instance of a leak without naming the
invariant it protects will regress. Every security fix must be traced back to
one of these invariants, and the regression test must exercise the
invariant's *class* of failure, not just the reported case:

- **A secret value must never enter an output representation
  unintentionally** — not in a Rich table, not in `--json`, not in a
  validation-error message, not in an exception, not in a generated file's
  comment. (Violated three separate ways in the audit: unredacted scan
  findings, unmasked validation-error text, and — as a variant — arbitrary
  file content read via a followed symlink and then printed as if it were a
  legitimate finding.)
- **Untrusted configuration data must never become executable shell or
  code.** Anything sourced from a committed, PR-editable file (`envshield.yml`,
  `env.schema.toml`) — a schema path, a service name, a `description` field —
  is untrusted input the moment it's interpolated into a generated shell
  script or generated source file. It must be escaped or validated before
  interpolation, every time, in every generator.
- **Filesystem operations must remain within the intended trust boundary.**
  A path being *inside the project directory* is not the same claim as a path
  *resolving* inside it — a symlink can satisfy the first and violate the
  second. Any containment check must be tested against a symlink specifically,
  not just against `../` traversal.

### Release blockers

The six P0 findings (plus one P1 — secret-file permission handling, a
freshly-created local secrets file inheriting the process umask instead of
`chmod 0600`) from the 2026-08-10 audit were the original release
blockers; all closed 2026-08-11 (see §19). This paragraph is kept as the
historical record, not as the current mechanism — it doesn't name today's
blockers and its "see ROADMAP.md's Release Blockers table" pointer is
stale (ROADMAP.md doesn't itemize individual findings). **The durable,
general rule — what counts as a release blocker, and where the current
list lives — is the "Versioning and Release Cadence" section's own
"Release blockers" subsection below, which points at BACKLOG.md's Part 0
as the living list.**

---

## 5. Configuration Contract principles

The contract (`env.schema.toml`) should be:

- deterministic
- versionable
- diffable
- machine-readable
- human-readable
- testable
- backwards compatible where possible

The schema is executable configuration policy, not documentation.

### Schema model guidance

Today the loaded schema is a plain, transient `dict`, re-parsed from disk on
every command invocation, with field-level semantics (`resolve_field_type`,
`is_required_now`, `should_be_present`, `validate_value`) as pure functions
layered on top of it in `schema_types.py`. That design is sound and should
**not** be replaced with a large framework, an ORM-style model, or a generic
"configuration object" abstraction.

If a structured `ConfigurationContract` model is introduced, it must be
minimal, and justified by one of these — not by "it would be cleaner":

- **Revision-aware loading** — a schema (and its resolved `extends` chain)
  needs to be loadable at an arbitrary git revision, not just from the
  working tree.
- **Semantic comparison** — schema-vs-schema diffing (§7A) needs a stable
  shape to diff against.
- **Future graph relationships** — the Configuration Graph (Phase 3) needs
  something to attach source/consumer/deployment edges to.
- **Stable machine-readable output** — a diff or graph result needs a shape
  that won't silently change under downstream JSON/SARIF/MCP consumers.

Preserve TOML as the file format and as the schema's on-disk representation.
Any structured model is an in-memory convenience layered on top of TOML, not
a replacement for it.

---

## 6. Configuration Contract Diff — three distinct capabilities

The old framing ("build contract diff") conflated three capabilities that
have different dependencies, different implementation cost, and different
product value. Keep them distinct in planning, code, and CLI surface:

### A. Schema-to-schema Contract Diff

Compare the contract at revision A against the contract at revision B.
Detect: added variables, removed variables, changed types, changed defaults,
changed requiredness, changed enum/pattern, changed secret classification,
changed environment requirements. Classify breaking vs. non-breaking where
the semantics allow it.

**This does not require source discovery.** It only needs revision-aware
schema loading (§5) and a diff function over two resolved schema dicts. It
is buildable directly on today's `schema_types.py` primitives.

### B. Configuration Discovery

Understand configuration dependencies *in source code* — where a variable is
declared and where it's consumed. Initial languages: Python, then
JavaScript/TypeScript. Prefer AST/tree-sitter-based analysis over regex.
Produces normalized, source-location-aware results (variable, file, line,
language, access type, confidence) independent of the CLI.

This is a real engine, not a byproduct of the secret scanner. The scanner's
existing "undeclared variable" check is a 3-pattern regex compliance linter
with discovery as an accidental side effect — it is a reasonable stopgap, not
the foundation this capability is built on.

### C. Source-to-Contract Change Analysis

The capability that connects A and B, and matters more than either alone.
Example: a developer writes `os.environ["STRIPE_SECRET_KEY"]` where no such
call existed before. EnvShield should be able to say:

```
New source configuration dependency: STRIPE_SECRET_KEY
Contract status: missing declaration
```

The end-to-end pipeline this is building toward:

```
code change → configuration dependency change → contract change
            → deployment impact → developer/PR notification
```

**C is the actual product goal.** A generic `schema diff` command (A alone)
is useful and should ship first because it's cheapest, but it is not the
point — the point is that a developer cannot silently introduce a
configuration dependency that the contract, the deployment manifests, and
their teammates don't know about.

---

## 7. Source discovery, Configuration Graph, deployment integrity

**Source discovery** (Configuration Discovery, §6B) should be AST/tree-sitter
based wherever practical, not regex-driven. Python and JavaScript/TypeScript
first — support these two well before adding a third language. Discovery
must remain independent of the CLI and produce the normalized shape above.

**Configuration Graph** (Phase 3) connects schema, source, consumers,
services, environments, providers, and deployments. It should eventually
answer: what uses this variable, what provides it, what breaks if it
changes, which environments/services/developers are affected. It depends on
Discovery (§6B) being real, not on the current regex scan.

**Deployment integrity** (Docker Compose, Kubernetes, and eventually CI
configuration) is **strategically important, not incidental** — it is one of
the clearest places EnvShield can differentiate from schema/runtime-focused
competitors like Varlock, which do not validate deployment manifests at all.
The corollary: false positives and false negatives here cost more trust than
almost anywhere else in the product, precisely because this is the pillar
carrying the most differentiation weight. Treat correctness bugs in this area
as high-priority even when they aren't security bugs.

Relationship validation only — EnvShield does not retrieve or store secret
values from a provider. It validates that a manifest correctly *references*
what the contract requires.

---

## 8. Environments

Support local, test, preview, staging, production. A variable's
requiredness and policy may differ per environment (e.g. optional locally,
required in staging and production).

---

## 9. Delivery mechanisms (hooks, CI, CLI, MCP)

**Git hooks are not a product phase.** Pre-commit, post-merge, and any future
post-checkout hook are one *delivery mechanism* — among the CLI, CI, a
GitHub PR bot, and MCP — for surfacing the contract-change intelligence that
Phase 2A–2C actually produces. Do not scope hook work as its own roadmap
phase; scope it under whichever phase produces the underlying capability
(2A/2C for the intelligence, Phase 5 for CI/PR surfacing).

The product capability that matters is: **a developer cannot silently miss a
configuration contract change** — whether they learn about it from a
pre-commit hook, a post-checkout hook, a CI check, or a PR comment is an
implementation choice, not the goal itself.

---

## 10. AI

AI is a consumer of EnvShield's configuration model, not the product. Future
integration should let agents inspect configuration, explain it, detect
missing/unused variables, validate deployments, analyze changes, and suggest
contract changes — never see secret values.

Note the competitive distinction from Varlock's "AI-safe .env files"
framing (§2): hiding secret values from an agent reading one repo's schema is
necessary but not differentiated — Varlock already does it. EnvShield's AI
angle is differentiated only if it reasons across discovery, deployment
impact, and polyglot services together, not just presence/absence of a
value in one schema.

---

## 11. Commercial strategy

Core CLI stays open source. Potential future paid capabilities: configuration
inventory, team visibility, drift monitoring, org-wide policy, ownership,
audit history, environment comparison, notifications, approvals, RBAC, SSO,
enterprise integrations.

**Do not charge for the developer workflow that creates adoption. Charge for
organizational visibility, governance, and control.** Do not build Cloud
before real users demonstrate demand for team-level functionality.

---

## 12. Roadmap (summary — see ROADMAP.md for detail and status)

| Phase | Scope |
|---|---|
| 0 | Security and Foundation Hardening |
| 1 | Configuration Contract v1 |
| 2A | Schema-to-Schema Contract Diff |
| 2B | Configuration Discovery |
| 2C | Source-to-Contract Change Analysis |
| 3 | Configuration Graph and Impact Analysis |
| 4 | Environment and Deployment Integrity |
| 5 | CI / GitHub / PR Integration |
| 6 | AI-Agent Configuration Safety |
| 7 | Product-Market Validation |
| 8 | EnvShield Cloud |
| 9 | Team / Enterprise |

Do not implement future phases while working on the current phase, without
explicit reasoning documented in ROADMAP.md. Phase status (done / reopened /
not started) is tracked in ROADMAP.md, not here — this table only fixes the
sequence and scope.

---

## 13. Bug-finding requirement

Whenever you touch an area of the codebase: inspect adjacent code, existing
tests, and look for related bugs, security issues, edge cases, backwards
compatibility problems, inconsistent behavior, and documentation that no
longer matches reality. Classify anything found (P0–P3). Do not silently fix
unrelated issues — record them in [BACKLOG.md](BACKLOG.md) (see "Finding
and Evidence Management" above) unless fixing them is directly related to
the current change and clearly safe.

---

## 14. Product Gap Review & Market Gap Review

At the end of every major phase, perform both reviews (see the original
question sets — still valid, unchanged by this revision) and answer them
honestly, including "has the product direction changed based on evidence?"

Two hard rules, made explicit by this audit's findings:

- **Do not add a feature merely because a competitor has it.** Evaluate
  whether it strengthens Configuration Contract as the core product first.
- **Do not expand secret scanning unless it directly supports configuration
  integrity.** Secret-pattern breadth is GitHub's game to win, not ours (§2).

---

## 15. Testing requirements

Every feature requires unit, integration, CLI, regression, security, and
malformed-input tests as appropriate. Parsers need valid/malformed/ambiguous/
edge-case/large-input tests. Contract changes need add/remove/modify/
breaking/environment-specific/policy-change tests.

**For every security fix specifically:** identify the underlying invariant
it protects (§4) and add a regression test for the *class* of vulnerability,
not just the specific reported instance. A test that only reproduces the
exact reported case will pass again the next time the same class of bug is
introduced through a different code path.

---

## 16. PR requirements

Every PR: focused, independently understandable, testable, reversible where
practical. Description includes Problem, Why, Design, Alternatives,
Security, Compatibility, Tests, Product impact, Market impact (does this
differentiate EnvShield or is it table-stakes?).

---

## 17. Definition of done

Not done on the happy path alone. Done when: implementation complete, tests
exist, edge cases addressed, security implications reviewed, backwards
compatibility considered, documentation updated, CLI behavior coherent,
errors useful, machine-readable output correct, architecture clean, no
obvious related bug remains, product value is clear, and finding
reconciliation is complete (see "Finding and Evidence Management" above —
every material finding discovered during the task is in BACKLOG.md, updated,
or explicitly justified as already represented).

---

## 18. Codex behavior

Act as a senior engineer, not a code-generation assistant. Do not blindly
agree. If a proposed implementation is bad, say so. If a feature doesn't fit
EnvShield, say so. If the architecture is over-engineering, say so. If a
competitor already solves something better, say so. If a feature nobody will
pay for is being proposed, say so. If a small feature reveals a deeper
architectural issue, stop and explain it.

Never claim a feature exists unless it does. Never claim tests pass unless
they were run. Never claim a bug is fixed without a regression test where
practical. Never claim market differentiation without examining competing
products — this charter's §2 is itself only as good as its last evidence
check; revisit it when the competitive landscape moves.

---

## 19. Current phase

**Phase 0 (all six P0 release blockers, P0-1 through P0-6, plus P1-1)
closed 2026-08-11. Phase 2A, Phase 2B, and Phase 2C are all complete.**

Phase 2C — Source-to-Contract Change Analysis — shipped across Milestone 1
(`envshield schema check-usages [REV_A] [REV_B] [--service] [--json]` at
the time of commit `68eeff2` — later renamed to the top-level `envshield
undeclared`, same argument/option surface, JSON shape, and exit codes; do
not invoke it as `schema check-usages`, that name no longer exists), a
symlink trust-boundary hardening pass (commit
`d7e3a8c`), Milestone 2 (multi-service parity via `resolve_targets`, plus
deliberate single-target JSON compatibility with Milestone 1's flat
contract, commit `6b37939`), and a regression-test fast-follow closing the
multi-service partial-failure gap (commit `3ebeb36`). Final verification:
full suite 711/711, `ruff check .` clean, working tree clean. Three
accepted, low-priority hardening items remain deferred in ROADMAP.md's
backlog (revision-aware service-directory resolution for the explicit
two-revision form, discovered-file size cap, `DEFAULT_EXCLUDED_DIRS`-style
pruning) — none are blockers, and none require another Phase 2C milestone.

**Current phase: v1 release preparation — blocked on confirmed findings.**
A 2026-08-21 release-readiness and marketing-claim audit reviewed the full
shipped surface (Phases 0-2C) end-to-end against live CLI reproductions.
This section previously summarized that audit as having returned "GO WITH
QUALIFICATIONS: no P0/P1 findings (no secret leakage, no false-clean on a
genuinely missing required variable)." **That summary was inaccurate and
was corrected on 2026-08-26** after a live-verification pass reproduced,
against current code, exactly the class of issue it claimed didn't exist:

- A P0 — a schema field with `secret = true` plus a real `defaultValue` was
  accepted and propagated into `.env.example`, generated Python, and
  generated TypeScript (`BACKLOG.md`'s `BL-001`). This was secret leakage.
  **Fixed 2026-08-26** (code committed locally, not yet released — see
  `BL-001`'s own entry for the full root-cause/fix account, including two
  further manifestations of the same root cause found and fixed in the
  same pass, in `explain` and `check`'s Rich table, that the original
  report and this section's prior wording didn't name).
- Three P1s — malformed Python config breaks the `check --json`/
  `doctor --json` machine-readable contract and can produce
  `"success": true` (`BL-002`, a false-clean). **Fixed 2026-08-26**
  (code committed locally, not yet released — see `BL-002`'s own entry;
  the same investigation also found and fixed `BL-092`, an uncaught crash
  on a binary/non-UTF-8 `.py` file in the same function, and left
  `BL-093`, a lower-confidence sibling gap in the Kubernetes parser, open
  as `NEEDS_EVIDENCE`). Generated TypeScript's `z.coerce.boolean()` turns
  the string `"false"` into `true` at runtime (`BL-003`); `scan`/
  `scan --staged` fail open on files over 1MB, reporting `clean: true`
  while silently skipping the one file that mattered (`BL-004`). Both
  remain open as of this revision.

Per this section's own release philosophy below, each independently
answers "yes" to "can this produce secret leakage" or "can this produce a
silent false-clean" — **v4.6.0 must not be treated as release-ready, and
no new release should be prepared, until `BL-001` through `BL-004` are
resolved** (`BL-001` and `BL-002` are fixed but not yet released;
`BL-003`/`BL-004` still need both). The documentation/
positioning pass this section previously described as having "closed out"
the audit — README's `secret`-field wording and new Known Limitations
section, the CLI tagline, CHANGELOG's Python-vs-JS/TS discovery wording,
ROADMAP.md's reconciliation against shipped code — did happen and remains
accurate; it addressed the audit's documentation/marketing-claim findings
only. The error being corrected here is that this section then described
the *entire* audit as closed out, when its P0/P1 code-correctness findings
were not addressed and, per direct re-verification, still aren't. Phase
0C's DI-1, DI-2, hook-scoping, and CI-lint items remain shipped; only
`pytest-cov`/coverage tooling remains open there (P3, non-blocking,
tracked as `BL-015`). The same audit's three P2 deployment-parser gaps
(Kubernetes `secretKeyRef`/`configMapKeyRef` key-vs-name matching,
Kubernetes `envFrom.prefix`, Docker Compose `${VAR-default}`) are tracked
in **[BACKLOG.md](BACKLOG.md)** as `BL-011` — not "filed in ROADMAP.md's
backlog" as previously stated; ROADMAP.md does not itemize individual
findings and never did. 3.8 (secret-keyword semantic classification) and
3.10 (batch/all-container check) remain explicitly deferred; their exact
original content could not be located during the 2026-08-26 reconciliation
pass and is not represented in BACKLOG.md — this numbering is a loose end,
not a resolved cross-reference, until someone locates what it originally
referred to. The next implementation direction past resolving `BL-001`
through `BL-004` is still an open decision.

**Release philosophy — optimize for a trustworthy v1, not zero known
bugs.** A known, bounded, honestly-documented P2/P3 limitation is
acceptable to ship with. A P0/P1 secret-safety or correctness issue is not
— see ROADMAP.md's severity scheme. Before proposing a new pre-release
engineering task, ask:

1. Does this break a core v1 promise?
2. Can it produce secret leakage?
3. Can it produce a silent false-clean on genuinely missing/invalid
   required configuration?
4. Can it corrupt generated schemas or cause destructive behavior?
5. Does it affect a common workflow EnvShield explicitly claims to
   support?

If every answer is no and the behavior can be honestly documented, it
belongs in [BACKLOG.md](BACKLOG.md) as post-v1 work, not immediate
implementation — do not fix a P2/P3 finding just because it was found (see
BACKLOG.md's Part 2/3 for current examples of exactly this).

**Release status (2026-08-19; do not disturb without explicit
instruction).** `v4.6.0` was tagged and pushed to GitHub; the test job
passed, but the publish job failed at the Sigstore attestation step
(`RekorClientError: Rekor returned an unknown error with HTTP 502` —
confirmed directly from the workflow log; external Sigstore infrastructure,
not a package or test failure). PyPI's currently published version is
therefore still `4.5.1` — `v4.6.0` exists as a pushed tag/GitHub state but
is not yet live on PyPI. Do not recreate or modify the `v4.6.0` tag, bump
the version, or rerun the publish workflow without explicit instruction —
resolving that failed publish is a separate release operation from any
documentation or roadmap work. Five further engineering fixes (3.2/3.3/3.5,
3.4, 3.6, 3.7, 3.9) are committed locally on top of `v4.6.0` and are not
yet part of any released version.

**Post-v1 direction (2026-08-23 pass).** ROADMAP.md's post-release backlog
was reconciled against the actual shipped code and reorganized into
committed/exploratory/demand-gated tiers (see ROADMAP.md's "Post-v1
direction" section). One new durable architectural boundary came out of
this pass and is captured in §2 above: external secret-provider
references. Everything else discussed in that pass — broader
source-language discovery beyond Python/JS-TS, a runtime leak-defense
wrapper, and deployment targets beyond Compose/Kubernetes — stays
exploratory/demand-gated backlog in ROADMAP.md, not a charter-level
commitment; do not treat any of it as already decided or scoped. A
separate marketing/SEO strategy was also developed in that pass; it is
deliberately not reflected here or in ROADMAP.md — this charter and
ROADMAP.md describe product/engineering direction only.

**Relevant existing modules:** `envshield/core/scanner.py`,
`envshield/core/schema_types.py`, `envshield/core/schema_manager.py`,
`envshield/config/manager.py`, `envshield/core/setup_manager.py`,
`envshield/core/generator.py`, `envshield/parsers/_kubernetes.py`,
`envshield/parsers/_docker_compose.py`, `envshield/core/dependency_snapshot.py`,
`envshield/core/dependency_diff.py`, `envshield/utils/git_utils.py`.

See ROADMAP.md for the full phase-status table, release-blocker history,
and Phase 2C backlog.

---

## 20. Two-pass workflow

After implementation: **Pass 1 — Engineering Review** (correctness,
security, architecture, tests, compatibility, performance, error handling,
CLI UX). **Pass 2 — Product Review** (user value, real-world usefulness,
market overlap, differentiation, adoption friction, future commercial
value). Report findings first; do not auto-modify code during review.

---

## 21. Final principle

Every EnvShield feature should reinforce: **EnvShield enforces configuration
contracts across code, Git, and deployments.** When choosing between two
implementations, prefer the one that makes configuration explicit,
understandable, verifiable, diffable, enforceable, observable, and safe.

Do not optimize for feature count. Optimize for trust — and remember that
trust is exactly what the six P0 findings in this audit spent.

---

## 22. Git, Commit, and Release Policy

### Commit authorship

All commits created for EnvShield must be authored solely under the
repository owner's configured Git identity.

Codex must NOT:

- add `Co-authored-by:` trailers
- add Codex/Anthropic as a co-author
- add AI attribution to commit messages
- change the repository owner's Git identity
- configure a different global or local Git user
- amend commits authored by the repository owner unless explicitly instructed

When Codex creates a commit, use the repository's existing Git identity.
Before committing, verify (and do not change these values):

```bash
git config user.name
git config user.email
```

Commit messages should follow the repository's existing conventional style.

### Normal development commits

Codex may create commits when explicitly instructed to do so. Codex must
NOT automatically commit every change. The normal workflow is:

```
implementation → tests → self-review → finding reconciliation
  → documentation consistency → user review
  → explicit instruction to commit → commit
```

Do not create commits merely because a task is complete unless the user
explicitly asks for the commit. Never include unrelated changes in a commit.

Before committing:

1. Inspect `git status`.
2. Inspect `git diff`.
3. Inspect `git diff --cached` if applicable.
4. Confirm only intended files are included.
5. Run the relevant tests.
6. Confirm the commit contains only the intended change.

---

## Versioning and Release Cadence

The pipeline, in order, with a hard boundary in the middle:

```
engineering commits → accumulated changes → release candidate / release review
  → version bump → tag + push → CI publication
```

Everything left of "release candidate / release review" is §22's territory
(normal development). Everything from "version bump" onward is §23's
territory (the mechanical publish steps, run only after explicit
authorization). This section owns the boundary itself — the judgment call
of *whether* accumulated commits currently constitute something worth
proposing as a release. **An engineering task or backlog item being
completed must never, by itself, trigger a version bump or a release.**
Closing `BL-001` did not make v4.6.1 happen; nothing does, until this
section's own process runs and the user authorizes it.

### Development commits

- Bug fixes, features, documentation changes, refactors, and backlog work
  may be committed during normal development, per §22.
- A commit does not imply a release.
- Do not bump the application version for every commit.
- Do not create release tags for normal development commits.
- Do not push automatically (§22).
- Do not publish automatically (§23).

### Release decision

A release should happen only when there is a coherent, user-facing set of
changes appropriate to publish — not on a schedule and not because a task
finished. Before proposing one, evaluate:

1. What user-facing changes have accumulated since the previous release
   (the most recent tag — `git log <last-tag>..HEAD`, mirroring how §19's
   own "committed locally on top of v4.6.0, not yet released" tracking
   already works)?
2. Do the accumulated fixes/features form a coherent release, or is this
   an arbitrary midpoint?
3. Are there open release blockers (see below)?
4. Is the current state actually safe to publish?
5. Do `CHANGELOG.md` and any other release-facing documentation accurately
   describe what's included?
6. Per `.bumpversion.cfg`'s existing patch/minor/major convention (§23),
   which increment does this set of changes actually call for?
7. Is there an actual reason to publish *now*, rather than continuing to
   accumulate changes?

Do not adopt an arbitrary "release every N commits" or "release every N
days" cadence — question 7 above exists specifically to rule that out.

### Release triggers

A release *may* be appropriate when, for example:

- one or more important user-facing features are complete,
- important bug or security fixes form a meaningful release,
- a milestone or phase reaches a coherent shipped state,
- compatibility or behavior changes need to be distributed, or
- accumulated changes have reached a meaningful release boundary.

These are illustrative, not automatic triggers — each one still has to
clear the full "Release decision" evaluation above before it turns into a
proposal, and even then only into a *proposal*, not an action.

### Release blockers

Before proposing or performing a release, check BACKLOG.md's **Part 0 —
Release Blockers** for open P0/P1 findings, and this charter's §4 release
principles. Do not recommend or perform a release while a release-blocking
finding remains open, unless this policy is explicitly overridden by the
user for that specific release. BACKLOG.md's Part 0 is the current, living
list — not §4's "Release blockers" subsection, which is historical (see
its own note), and not ROADMAP.md, which does not itemize individual
findings.

---

## 23. Release and Publishing Policy

EnvShield uses the repository's existing `bump2version` release workflow.
Do NOT invent or substitute a different release process.

Publishing is a privileged operation. Codex must NOT:

- publish a release automatically
- run `bump2version` unless explicitly instructed
- create release tags unless explicitly instructed
- push commits automatically
- push tags automatically
- publish to PyPI manually
- create GitHub releases manually
- modify release configuration without explicit instruction

Normal development work must never trigger a release.

### Required EnvShield release workflow

When the user explicitly asks to publish a release, follow this process
exactly.

**1. Verify the working tree is clean.**

```bash
git status --short
```

`bump2version` requires a clean working tree. Do not proceed if there are
uncommitted changes — stop and report them. Do not automatically commit
unrelated changes merely to make the tree clean.

**2. Update `CHANGELOG.md` separately.**

Before running `bump2version`, update `CHANGELOG.md` (new `## [X.Y.Z] -
<date>` section, or whatever the existing changelog format calls for), then
commit it on its own:

```bash
git add CHANGELOG.md
git commit -m "docs: update changelog for vX.Y.Z"
```

Do not combine the changelog commit with the version bump.

**3. Bump the version.**

```bash
bump2version patch   # bug fixes
bump2version minor   # new backwards-compatible features
bump2version major   # breaking changes
```

The repository's existing bump2version configuration is authoritative. Do
not manually edit the version files instead of using `bump2version`. It is
expected to update the version in `pyproject.toml`, update `.bumpversion.cfg`,
create the release commit, and create the corresponding `vX.Y.Z` Git tag.

**4. Push the commit and tag** — only after the user explicitly authorizes
publishing:

```bash
git push
git push --tags
```

Do not push automatically as part of normal development.

**5. GitHub Actions performs publishing.** The tag push triggers
`publish.yml`, which runs tests, builds the sdist/wheel, publishes to PyPI
through trusted publishing, and creates the corresponding GitHub Release. Do
not manually upload packages to PyPI. Do not manually create a GitHub
Release unless the workflow fails and the user explicitly asks for manual
recovery.

**6. Verify the release.** After an authorized release: inspect the GitHub
Actions workflow, confirm the test job succeeds, confirm the publish job
succeeds, verify the PyPI package version, verify the GitHub Release, and
report any failure clearly. Do not claim a release succeeded without
verifying the relevant workflow result.

### Special case: existing version without a tag

If the repository already contains a committed version (e.g. `4.0.1`) but
the corresponding `v4.0.1` tag does not exist, do NOT run `bump2version`
blindly. First determine:

```bash
git describe --tags --always
git tag --list
git log -1 --oneline
```

Then report the state and ask the user whether to create and push the
missing existing-version tag, or proceed with the next version bump. Do not
accidentally skip a release version.

### Release safety rule

A release is always an explicit user-authorized operation. Even if tests
pass, the working tree is clean, the changelog is ready, the version
appears correct, and CI appears ready — Codex must still wait for explicit
authorization before running `bump2version`, creating a release tag, pushing
release commits, pushing tags, or triggering publication.

### Release review

When the "Versioning and Release Cadence" section's Release decision
evaluation concludes a release is worth proposing, do NOT immediately run
`bump2version`. First provide a release-readiness report — this is the
"release candidate / release review" step in that section's pipeline —
containing:

- current version and proposed version
- proposed release type (patch/minor/major) and why
- user-facing changes included since the previous release (commits and,
  where relevant, the backlog items they close)
- remaining open release blockers, if any (BACKLOG.md's Part 0)
- tests and lint status (actually run, not assumed)
- `CHANGELOG.md` status (updated and accurate, or what's missing)
- working-tree status
- the expected tag and the publishing workflow that will be triggered
- the reason this is a meaningful release boundary, not merely "a task
  finished" (see Release decision, question 7)

Then wait for explicit authorization to execute the release. Never publish
merely because an engineering task or backlog item is complete.

### Important

Never add a `Co-authored-by` trailer to any EnvShield commit. All EnvShield
commits must remain authored by the repository owner. Do not modify Git
identity configuration. Do not publish anything unless explicitly instructed
by the user.
