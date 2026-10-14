# EnvShield Roadmap

EnvShield makes sure every service and every deploy gets the configuration
it needs, and tells you before something breaks. It does that from one
committed contract, `env.schema.toml`, checked against your code, your local
files and your deployment manifests. Development and deployment are where it
leads; onboarding works with the secret tool your team already uses.

This page describes direction, not a schedule. Items under **Now** are
shipped and in real use today. **v5** is the release in progress, in the
order it is being built. Items under **After v5** are directions we
intend to pursue, but not on committed dates — sequencing can shift based
on what we learn from real usage. Items under **Exploring** depend on
actual user demand showing up before we'd build them at all. For the exact
history of what shipped in which release, see [CHANGELOG.md](CHANGELOG.md).

---

## Now

What you can already do with EnvShield:

- **Define a configuration contract** (`env.schema.toml`) — types,
  descriptions, defaults, conditional requirements (`requiredIf`), and
  per-field secret classification, in plain committed TOML.
- **Build that contract from a real project** (`init`, `import`) — from an
  existing `.env`, a Python config module, or a deployment manifest,
  instead of writing one from scratch.
- **Validate configuration against the contract** (`check`, `doctor`,
  `setup`) — for a local file, and for Docker Compose or Kubernetes
  deployment manifests. `check` also reports code that reads a variable
  the contract doesn't declare, and `check --process-env` counts variables
  set in the shell. `check --json` includes a versioned, value-free report
  per service.
- **Discover configuration usage in source code** (`undeclared`) — catch a
  variable your code just started reading that the contract doesn't know
  about yet. Python is AST-based; JavaScript/TypeScript is pattern-based.
  `undeclared --sarif` annotates each finding on its file and line in GitHub
  Code Scanning or any other SARIF consumer.
- **Review how the contract itself changes** (`schema diff`) — compare two
  git revisions and classify what changed (breaking, security-sensitive,
  informational), so a configuration change is reviewable like any other
  code change.
- **Generate typed configuration code from the contract** (`generate`) — a
  Python (`pydantic-settings`) or TypeScript (`zod`) module, once the
  contract exists.
- **Catch a hardcoded secret before it's committed** (`scan`, git hooks) —
  a supporting safety check alongside the contract, not the product's main
  job.
- **Run this across a monorepo** — one schema shared by several services,
  with each variable scoped to the services that use it (`services = [...]`),
  or one schema per service composed via `extends`. Every command is
  service-aware, and registered Compose files map each container to its
  service.

---

## v5 — in progress

v5 makes EnvShield trustworthy on real projects and gives it one clear job:
**every service and every deploy gets the configuration it needs, and you
find out before something breaks.** It was shaped by running v4.8.0 on nine
real repositories (2026-10-14), which showed both what works (real drift
caught in production code) and what doesn't yet (first-run setup, CI output,
multi-container deployments, per-environment requirements).

The steps below are built in this order and merged as they finish; v5.0.0
is released once, at the end. Each step links to its tracking issue.

1. **Secret-safety fix first** — no command ever prints a line of your
   source code because Python warned while parsing it. Today a warning can
   echo a hardcoded secret past `scan`'s redaction.
   ([#47](https://github.com/rabbilyasar/envshield/issues/47))
2. **Decisions and public story** — the v5 decisions recorded in the
   repository; README, website and docs rewritten to describe what v4.8.0
   actually does, led by the story above, with a recorded demo.
   ([#14](https://github.com/rabbilyasar/envshield/issues/14),
   [#43](https://github.com/rabbilyasar/envshield/issues/43))
3. **Honest verdicts** — `check` reports one of five verdicts per variable
   and never calls a value valid unless it actually read and validated it.
   Values EnvShield can't see (a Kubernetes Secret, a Compose `${VAR}`)
   fail the gate unless you accept that specific reason explicitly with
   `--accept-unverified`.
   ([#15](https://github.com/rabbilyasar/envshield/issues/15),
   [#16](https://github.com/rabbilyasar/envshield/issues/16),
   [#17](https://github.com/rabbilyasar/envshield/issues/17))
4. **Environments** — a variable can be optional on a laptop and required
   in production (`requiredIn`, `--env`), and `schema diff`, `explain` and
   `generate` show which environment a requirement applies to.
   ([#23](https://github.com/rabbilyasar/envshield/issues/23),
   [#24](https://github.com/rabbilyasar/envshield/issues/24))
5. **Deploy-time check** — `check --runtime --env production` checks the
   environment it runs in, so a deploy missing a required variable fails at
   that step instead of at startup.
   ([#25](https://github.com/rabbilyasar/envshield/issues/25))
6. **One project contract** — one schema for the whole project by default,
   which `init` and the service commands add to. `init` builds the first
   contract from what the code actually reads, the manifests that deploy
   it and the services it finds, and never invents a requirement.
   ([#18](https://github.com/rabbilyasar/envshield/issues/18),
   [#19](https://github.com/rabbilyasar/envshield/issues/19),
   [#44](https://github.com/rabbilyasar/envshield/issues/44))
7. **A smaller, consistent CLI** — commands grouped by stage, overlapping
   ones deprecated (they keep working through v5); the same answer in a
   terminal and in CI; output that stays readable in CI logs; the
   pull request that adopts EnvShield doesn't fail its own check.
   ([#42](https://github.com/rabbilyasar/envshield/issues/42),
   [#45](https://github.com/rabbilyasar/envshield/issues/45))
8. **Onboarding that works with your vault** — `setup` reports exactly what
   is ready, what needs access and what you must set yourself, never hangs
   without a terminal, and never overwrites a value without asking. Secret
   provider support (1Password first) is being redesigned to work with what
   providers already offer rather than copying secret values to disk.
   ([#20](https://github.com/rabbilyasar/envshield/issues/20),
   [#21](https://github.com/rabbilyasar/envshield/issues/21),
   [#22](https://github.com/rabbilyasar/envshield/issues/22))
9. **Moving existing projects** — `schema merge` turns several schemas into
   one project contract and refuses any merge that would change a result.
   ([#26](https://github.com/rabbilyasar/envshield/issues/26))
10. **Verify and release** — an end-to-end lifecycle suite, a re-run of the
    real-repository trial, a GitHub Action and pre-commit hooks for one-line
    CI adoption, v5 documentation with a migration guide, a beta with teams
    outside the project, then v5.0.0.
    ([#27](https://github.com/rabbilyasar/envshield/issues/27),
    [#46](https://github.com/rabbilyasar/envshield/issues/46),
    [#28](https://github.com/rabbilyasar/envshield/issues/28),
    [#29](https://github.com/rabbilyasar/envshield/issues/29))

**For AI coding agents:** v5 positions EnvShield as the deterministic
configuration check in an agent's loop — hooks and `--json` output an agent
can act on when it adds code that needs configuration. No MCP server is
planned for v5.

**Compatibility:** v5.0.0 is a major release. Verdicts, the gate, `setup`
behavior and the JSON report change; exit codes (0/1/2) don't. Existing
layouts (one schema per service, `extends`) keep working, and v5.0.0 ships
with a migration guide.

---

## After v5

Directions we intend to pursue after v5.0.0, roughly in this order. None
of them is scheduled.

1. **Configuration Graph & Impact Analysis** — connect the schema to where
   variables are actually used in source, which services depend on them,
   and what's affected when one changes.
2. **Deployment Integrity** — continue closing gaps in how EnvShield
   validates real deployment manifests (Docker Compose, Kubernetes) as
   they're found.
3. **CI / PR Integration beyond v5** — v4.7.2 shipped `undeclared --sarif`
   and v5 adds a GitHub Action and pre-commit hooks. A PR bot, hosted
   dashboard or GitHub API integration would come only from real demand.
4. **AI-agent interfaces** — a direct interface for agents (for example
   MCP), once the questions about what an agent may safely ask are
   answered.
5. **Product-Market Validation** — continue learning from real adoption:
   which capability creates the strongest "this is useful" moment, and what
   real users ask for next.

---

## Exploring

Ideas we're open to, but won't build until real usage demonstrates the
need. None of these exist in any form today, and none are committed
(except where an item says it moved into v5):

- **External secret providers** — moved out of Exploring into v5 (step 8),
  where provider support is being redesigned to work with what providers
  such as 1Password already offer. The boundary is permanent: **EnvShield
  owns the contract describing a secret; the provider owns the value.**
  EnvShield never stores, caches, logs or prints a secret value, and never
  sends one anywhere but the developer's own machine. Vault, AWS Secrets
  Manager and platform-native Kubernetes Secrets remain exploratory.
- **Deployment targets beyond Docker Compose and Kubernetes** — nothing
  specific is planned; we'd add one when real usage asks for it, held to
  the same accuracy bar as the two we already support.
- **Source-language discovery beyond Python and JavaScript/TypeScript** —
  more languages, only if they can be added without sacrificing accuracy.
- **Framework-aware configuration discovery within Python, beyond Flask's
  `current_app` (`BL-113`)** — Flask's `current_app.config[...]`/`.get(...)`
  reads (including the common `from flask import current_app as <alias>`
  import form) are now recognized by `discover_python_usages`, surfaced only
  through `explain` at "medium" confidence, and validated against two
  independent real Flask codebases before shipping (see `BACKLOG.md`'s
  `BL-113` entry for the full evidence and false-positive analysis). Still
  open, deliberately unimplemented, and not the same claim as the shipped
  Flask work: a locally constructed `Flask()`/subclass instance's own
  `app.config`/`self.config` reads (evidence found this catches almost no
  real reads on its own, once `current_app`-alias tracking exists), Django's
  `settings.X` (attribute access, not a call/subscript — structurally
  different, and still zero real-world evidence behind it, ever), and
  framework configuration *population* (Flask's `app.config.from_pyfile`/
  `from_object`/`from_envvar`, Django's settings-module loading) — a
  separate, harder, cross-file problem, not bundled into this direction.
  Any of these would need its own evidence-gathering pass before
  authorization, the same way the shipped Flask work did.
- **Team and organizational features** (shared visibility, drift
  monitoring, policy, audit history) and a **hosted offering** — only if
  real teams demonstrate they need them. The CLI itself stays open source
  regardless of what happens here.
- **Optional runtime attestation** — a possible future signal from an
  already-running process confirming which resolved contract version it
  actually started with, purely as an observation layer. Everything
  EnvShield validates today is a repository artifact (schema, source,
  deployment manifests) — never the state of a running process, which is a
  real, currently open gap this direction would address, not a settled
  non-goal. Unexplored, not designed, and not committed: no attestation
  mechanism, SDK, or wrapper exists today. The boundary is permanent,
  matching the external-secret-provider boundary above: this must never
  become a runtime config resolver, injector, proxy, secret store, or a
  dependency any EnvShield-managed application needs at runtime to
  function.

---

## What We're Deliberately Not Building

A few boundaries that aren't going to move, regardless of what else changes
above:

- **Not a secret vault or secrets manager.** EnvShield does not store,
  retrieve, or rotate secret values — not today, and not as part of the
  external-provider direction above. If you need one of those, use
  1Password, HashiCorp Vault, AWS Secrets Manager, Infisical, or Doppler —
  EnvShield is a natural complement to any of them, not a replacement.
- **Not trying to out-detect dedicated secret-scanning tools.** GitHub
  Secret Scanning, Gitleaks, and GitGuardian specialize in detection
  breadth and depth. EnvShield's built-in `scan` is a supporting safety
  check alongside the configuration contract — not a competing product.
- **Not a generic `.env` editor, and not a generic AI chatbot for
  configuration.**
- **Not trying to replace typed config libraries** (`pydantic-settings`,
  `zod`, `t3-env`, `envalid`, and similar) inside a single language and
  framework — those already solve that problem well. EnvShield's schema is
  valuable specifically because it also drives validation, discovery, and
  review across languages, services, and environments — not as a
  standalone typed-loader replacement.

---

Have a use case that doesn't fit anywhere above, or a direction you'd like
to see prioritized differently? Open a
[discussion](https://github.com/rabbilyasar/envshield/discussions) or an
[issue](https://github.com/rabbilyasar/envshield/issues) — real usage is
exactly what moves an item from Exploring to After v5.
