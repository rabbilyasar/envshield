# EnvShield — Engineering & Product Charter

This is the durable charter for EnvShield: identity, competitive positioning,
engineering/security principles, and target architecture. It changes rarely,
and only when reality (a shipped capability, a market shift, an audit)
requires it to.

For the current phase, open work, and the audit-derived backlog, see
[ROADMAP.md](ROADMAP.md). This charter says what EnvShield *is* and *is not*;
ROADMAP.md says what's *done*, *in progress*, and *next*.

Last revised: 2026-08-10, following a full-repository audit (see
[ROADMAP.md](ROADMAP.md) for the findings that drove this revision). Anyone
reading this charter should assume ROADMAP.md's phase-status table is the
source of truth for "does X actually exist," not this document's prose.

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

The six P0 findings from the 2026-08-10 audit are explicit release
blockers: no new feature work ships, and no release is tagged, while any of
them remains open. See ROADMAP.md's Release Blockers table for status,
file:line references, and fix direction. Secret-file permission handling
(a freshly-created local secrets file inheriting the process umask instead
of `chmod 0600`) is elevated to a **P1 security-hardening item**, tracked
alongside the P0s even though it doesn't block a release on its own.

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
unrelated issues — report them into ROADMAP.md's backlog unless fixing them
is directly related to the current change and clearly safe.

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
obvious related bug remains, product value is clear.

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

**Current task:** none assigned yet. Phase 2C being complete does not by
itself select the next phase — Phase 0C (DI-1/DI-2, hook-scoping, CI lint
restoration, coverage tooling) remains open and non-blocking per
ROADMAP.md's own gating rules, but this section does not declare it, or any
other phase, the current implementation task. The next implementation
direction is an open decision.

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
implementation → tests → self-review → user review
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

Before publishing, Codex should provide: current version, target version,
release type, changelog summary, commits included, working tree status,
tests run, expected tag, and the publishing workflow that will be
triggered. Then wait for explicit authorization to execute the release.

### Important

Never add a `Co-authored-by` trailer to any EnvShield commit. All EnvShield
commits must remain authored by the repository owner. Do not modify Git
identity configuration. Do not publish anything unless explicitly instructed
by the user.
