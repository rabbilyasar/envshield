# EnvShield Roadmap

EnvShield is built around one idea: environment variables should be a
version-controlled configuration contract, not a folder of `.env` files and
tribal knowledge. `env.schema.toml` is that contract — everything else
follows from it.

This page describes direction, not a schedule. Items under **Now** are
shipped and in real use today. Items under **Next** are directions we
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
  deployment manifests.
- **Discover configuration usage in source code** (`undeclared`) — catch a
  variable your code just started reading that the contract doesn't know
  about yet. Python is AST-based; JavaScript/TypeScript is pattern-based.
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
- **Run this across a monorepo** — one schema per service, shared
  variables composed via `extends`, every command service-aware.

---

## Next

Directions we intend to pursue after the current release, roughly in this
order:

1. **Configuration Graph & Impact Analysis** — connect the schema to where
   variables are actually used in source, which services depend on them,
   and what's affected when one changes.
2. **Deployment Integrity** — continue closing gaps in how EnvShield
   validates real deployment manifests (Docker Compose, Kubernetes) as
   they're found.
3. **CI / PR Integration** — surface a configuration-contract change or a
   newly undeclared variable directly on a pull request, not just from the
   CLI.
4. **AI-Agent Configuration Safety** — let a coding agent inspect, validate,
   and reason about a project's configuration contract without ever
   exposing a secret value to it.
5. **Product-Market Validation** — before building further, learn from real
   adoption: does the configuration-contract idea land, which capability
   creates the strongest "this is useful" moment, and what do real users
   actually ask for next.

None of the above exist today. This is the order we currently expect to
work through them, not a release schedule.

---

## Exploring

Ideas we're open to, but won't build until real usage demonstrates the
need. None of these exist in any form today, and none are committed:

- **References to external secret providers** — a schema field that
  describes *where* a secret is expected to come from (a provider like
  1Password, HashiCorp Vault, AWS Secrets Manager, or a platform-native
  Kubernetes Secret), without EnvShield ever storing, retrieving, or
  transmitting the value itself. The boundary here is permanent, not just
  a current limitation: **EnvShield owns the contract describing the
  secret; the external provider owns the secret value.** This will never
  turn EnvShield into a place secret values pass through.
- **A more direct interface for AI agents** (e.g. MCP) to query the
  configuration contract, once the underlying agent-safety work above is
  real.
- **Deployment targets beyond Docker Compose and Kubernetes** — nothing
  specific is planned; we'd add one when real usage asks for it, held to
  the same accuracy bar as the two we already support.
- **Source-language discovery beyond Python and JavaScript/TypeScript** —
  more languages, only if they can be added without sacrificing accuracy.
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
exactly what moves an item from Exploring to Next.
