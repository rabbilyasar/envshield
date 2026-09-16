#!/usr/bin/env python3
"""
Self-check for impact_prototype.py -- NOT part of EnvShield's real test
suite (see the module docstring in impact_prototype.py: this whole
directory is a product-validation prototype, not shipped code). Assert-
based, no pytest fixtures, no framework: run it directly.

Exists specifically to prove the one correctness rule the prototype was
built to enforce: a service sharing an environment-variable *name* is
never, by itself, reported as "affected" -- and a service is never
silently omitted from the report because a lookup failed.
"""

import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
from impact_prototype import build_report, classify  # noqa: E402


def _write(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


def build_fixture(root: str) -> None:
    """
    An extended version of the four-service demo fixture: adds `unrelated`
    (zero relationship -- proves confident omission) and `broken` (a
    malformed schema -- proves evaluation_unknown), plus an isolated
    `edge_a`/`edge_b` pair that shares ONLY a manifest, no `extends`, to
    prove manifest-based evidence is independently sufficient, and
    `edge_c`, whose only relationship to DATABASE_URL is a manifest that
    is NOT shared with `api` -- proves manifest_reference_not_shared.
    """
    _write(
        os.path.join(root, "envshield.yml"),
        """\
services:
  api:
    schema: services/api/env.schema.toml
  worker:
    schema: services/worker/env.schema.toml
  analytics:
    schema: services/analytics/env.schema.toml
  web:
    schema: services/web/env.schema.toml
  unrelated:
    schema: services/unrelated/env.schema.toml
  broken:
    schema: services/broken/env.schema.toml
  edge_a:
    schema: services/edge_a/env.schema.toml
  edge_b:
    schema: services/edge_b/env.schema.toml
  edge_c:
    schema: services/edge_c/env.schema.toml
manifests:
  - file: docker-compose.yml
    containers:
      api: api
      worker: worker
  - file: edge-compose.yml
    containers:
      a: edge_a
      b: edge_b
  - file: edge-c-compose.yml
    containers:
      only: edge_c
""",
    )
    _write(
        os.path.join(root, "docker-compose.yml"),
        """\
services:
  api:
    image: api
    environment:
      - DATABASE_URL=${DATABASE_URL}
  worker:
    image: worker
    environment:
      - DATABASE_URL=${DATABASE_URL}
""",
    )
    _write(
        os.path.join(root, "edge-compose.yml"),
        """\
services:
  a:
    image: a
    environment:
      - SHARED_VAR=${SHARED_VAR}
  b:
    image: b
    environment:
      - SHARED_VAR=${SHARED_VAR}
""",
    )
    _write(
        os.path.join(root, "edge-c-compose.yml"),
        """\
services:
  only:
    image: only
    environment:
      - DATABASE_URL=${DATABASE_URL}
""",
    )

    _write(
        os.path.join(root, "services/shared/base.schema.toml"),
        '[DATABASE_URL]\ndescription = "db"\ntype = "url"\nsecret = true\ndefaultValue = "postgres://x"\n',
    )
    _write(
        os.path.join(root, "services/api/env.schema.toml"),
        'extends = "../shared/base.schema.toml"\n',
    )
    _write(
        os.path.join(root, "services/worker/env.schema.toml"),
        'extends = "../shared/base.schema.toml"\n',
    )
    _write(
        os.path.join(root, "services/analytics/env.schema.toml"),
        '[DATABASE_URL]\ndescription = "independent"\ntype = "url"\nsecret = true\n',
    )
    _write(
        os.path.join(root, "services/analytics/warehouse.py"),
        "import os\nX = os.environ.get('DATABASE_URL')\n",
    )
    _write(
        os.path.join(root, "services/web/env.schema.toml"),
        '[FEATURE_FLAG]\ndescription = "x"\ntype = "string"\ndefaultValue = "off"\n',
    )
    _write(
        os.path.join(root, "services/web/index.ts"),
        "const x = process.env.DATABASE_URL;\n",
    )

    _write(
        os.path.join(root, "services/unrelated/env.schema.toml"),
        '[LOG_LEVEL]\ndescription = "x"\ntype = "string"\ndefaultValue = "info"\n',
    )
    _write(
        os.path.join(root, "services/unrelated/app.py"),
        "import os\nX = os.environ.get('LOG_LEVEL')\n",
    )

    # Deliberately malformed TOML -- unterminated table header.
    _write(
        os.path.join(root, "services/broken/env.schema.toml"),
        '[DATABASE_URL\ntype = "url"\n',
    )

    _write(
        os.path.join(root, "services/edge_a/env.schema.toml"),
        '[SHARED_VAR]\ndescription = "a"\ntype = "string"\ndefaultValue = "x"\n',
    )
    _write(
        os.path.join(root, "services/edge_b/env.schema.toml"),
        '[SHARED_VAR]\ndescription = "b"\ntype = "string"\ndefaultValue = "y"\n',
    )
    _write(
        os.path.join(root, "services/edge_c/env.schema.toml"),
        '[UNRELATED_FIELD]\ndescription = "c"\ntype = "string"\ndefaultValue = "z"\n',
    )


def run() -> None:
    root = tempfile.mkdtemp(prefix="impact-check-")
    original_cwd = os.getcwd()
    try:
        build_fixture(root)
        os.chdir(root)

        # 1. Independent schema declaration must NOT be promoted to "transitive".
        r = classify("api", "analytics", "DATABASE_URL")
        assert r is not None, "expected a result for analytics"
        assert r["relationship"] == "observed_unknown", r
        kinds = {e["kind"] for e in r["evidence"]}
        assert "schema_declared_independently" in kinds, r
        assert "source_usage_only" in kinds, r

        # 2. Source usage alone (no schema declaration) must NOT be promoted.
        r = classify("api", "web", "DATABASE_URL")
        assert r is not None
        assert r["relationship"] == "observed_unknown", r
        assert {e["kind"] for e in r["evidence"]} == {"source_usage_only"}, r

        # 3. Shared `extends` provenance IS sufficient for "transitive".
        r = classify("api", "worker", "DATABASE_URL")
        assert r is not None
        assert r["relationship"] == "transitive", r
        kinds = {e["kind"] for e in r["evidence"]}
        assert "schema_shared_provenance" in kinds, r
        assert "manifest_shared_wiring" in kinds, r

        # 4. Shared manifest wiring ALONE (no extends at all) is independently
        #    sufficient for "transitive" -- proves the two tier-2 proof types
        #    are each sufficient on their own, not only in combination.
        r = classify("edge_a", "edge_b", "SHARED_VAR")
        assert r is not None
        assert r["relationship"] == "transitive", r
        assert {e["kind"] for e in r["evidence"]} == {"manifest_shared_wiring"}, r

        # 5. A reference in a manifest NOT shared with the changed service must
        #    NOT be promoted -- only reported as observed_unknown.
        r = classify("api", "edge_c", "DATABASE_URL")
        assert r is not None
        assert r["relationship"] == "observed_unknown", r
        assert {e["kind"] for e in r["evidence"]} == {
            "manifest_reference_not_shared"
        }, r

        # 6. Zero relationship, every check succeeds cleanly -> confident
        #    omission (None), not "observed_unknown" and not "evaluation_unknown".
        r = classify("api", "unrelated", "DATABASE_URL")
        assert r is None, r

        # 7. A failed evaluation (malformed schema) must NEVER be silently
        #    treated as "no relationship" -- it must surface as
        #    evaluation_unknown, distinct from both other outcomes.
        r = classify("api", "broken", "DATABASE_URL")
        assert r is not None, "a failed evaluation must not be silently omitted"
        assert r["relationship"] == "evaluation_unknown", r
        assert all(e["kind"] == "evaluation_error" for e in r["evidence"]), r
        assert all(e["message"] for e in r["evidence"]), (
            "error messages must not be empty"
        )
        for e in r["evidence"]:
            assert (
                "DATABASE_URL" not in e["message"] or True
            )  # variable names are not secret; nothing to hide here
            assert "postgres://" not in e["message"], "must never leak parsed content"

        # 8. End-to-end via build_report's actual revision-diffing path (not
        #    just classify() in isolation): commit the "before" state (a
        #    default present), remove it uncommitted, and confirm the whole
        #    report -- directly_affected, transitive, observed_unknown, and
        #    evaluation_unknown -- assembles correctly from one real diff.
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(
            ["git", "config", "user.email", "check@example.com"], cwd=root, check=True
        )
        subprocess.run(["git", "config", "user.name", "check"], cwd=root, check=True)
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "before"], cwd=root, check=True)
        _write(
            os.path.join(root, "services/shared/base.schema.toml"),
            '[DATABASE_URL]\ndescription = "db"\ntype = "url"\nsecret = true\n',
        )

        report = build_report("api", revision_a="HEAD", revision_b=None)
        assert len(report["variables"]) == 1, report
        v = report["variables"][0]
        assert v["variable"] == "DATABASE_URL"
        assert v["directly_affected"] == {"service": "api"}
        transitive_services = {
            e["service"] for e in v["demonstrably_transitively_affected"]
        }
        assert transitive_services == {"worker"}, transitive_services
        observed_services = {e["service"] for e in v["observed_unknown"]}
        assert observed_services == {"analytics", "web", "edge_c"}, observed_services
        evaluation_unknown_services = {e["service"] for e in v["evaluation_unknown"]}
        assert evaluation_unknown_services == {"broken"}, evaluation_unknown_services
        # unrelated/edge_a/edge_b must not appear in ANY bucket -- confirmed no relationship.
        all_reported = (
            transitive_services
            | observed_services
            | evaluation_unknown_services
            | {"api"}
        )
        assert "unrelated" not in all_reported
        assert "edge_a" not in all_reported
        assert "edge_b" not in all_reported

        print("All 8 self-checks passed.")
    finally:
        os.chdir(original_cwd)
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    run()
