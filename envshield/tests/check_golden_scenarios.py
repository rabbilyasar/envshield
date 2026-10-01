# envshield/tests/check_golden_scenarios.py
"""
Project fixtures for the 'check' golden tests (test_check_golden.py).

Each scenario is a set of files plus a 'check' invocation. Its expected
exit code, '--json' payload, and Rich output were recorded from the
pre-evaluator implementation of 'check' (fixtures/check_golden.json), so
the evaluator-backed 'check' is compared against what 'check' actually
did, not against what it's believed to do. The fixtures contain no code
that reads the environment, so code-reference findings never enter them.

Regenerate (only when a deliberate, recorded behavior change happens):
    python -m envshield.tests.test_check_golden --regenerate
"""

ONE = "services:\n  app:\n    schema: env.schema.toml\n"

SCENARIOS = {
    "clean": {
        "files": {
            "envshield.yml": ONE,
            "env.schema.toml": '[A]\ndescription="a"\ntype="int"\n',
            ".env": "A=1\n",
        },
    },
    "missing_blank_invalid_extra": {
        "files": {
            "envshield.yml": ONE,
            "env.schema.toml": (
                '[A]\ndescription="a"\n\n[B]\ndescription="b"\n\n'
                '[C]\ntype="port"\n\n[S]\nsecret=true\ntype="url"\n'
            ),
            ".env": "B=\nC=99999\nS=not-a-url\nEXTRA=1\n",
        },
    },
    "defaulted_variable_absent": {
        "files": {
            "envshield.yml": ONE,
            "env.schema.toml": '[PORT]\ntype="port"\ndefaultValue="8000"\n',
            ".env": "",
        },
    },
    "requiredif_met_unmet_and_default_wins": {
        "files": {
            "envshield.yml": ONE,
            "env.schema.toml": (
                '[FLAG]\nenum=["true","false"]\n\n'
                '[NEEDED]\nrequiredIf={var="FLAG", equals="true"}\n\n'
                '[UNMET]\nrequiredIf={var="OTHER", equals="yes"}\n\n'
                '[LEVEL]\nrequiredIf={var="OTHER", equals="yes"}\ndefaultValue="info"\n'
            ),
            ".env": "FLAG=true\n",
        },
    },
    "required_false_absent": {
        "files": {
            "envshield.yml": ONE,
            "env.schema.toml": '[OPT]\nrequired=false\ntype="int"\n\n[BAD]\nrequired=false\ntype="int"\n',
            ".env": "BAD=x\n",
        },
    },
    "local_file_missing": {
        "files": {
            "envshield.yml": ONE,
            "env.schema.toml": '[A]\ndescription="a"\n',
        },
    },
    "malformed_schema": {
        "files": {
            "envshield.yml": ONE,
            "env.schema.toml": "[A\n",
            ".env": "A=1\n",
        },
    },
    "secret_with_default": {
        "files": {
            "envshield.yml": ONE,
            "env.schema.toml": '[TOKEN]\nsecret=true\ndefaultValue="SYNTHETIC_NOT_A_SECRET"\n',
            ".env": "TOKEN=x\n",
        },
    },
    "unknown_schema_key": {
        "files": {
            "envshield.yml": ONE,
            "env.schema.toml": '[PORT]\ndefault="8000"\n',
            ".env": "PORT=1\n",
        },
    },
    "explicit_file_argument": {
        "files": {
            "envshield.yml": ONE,
            "env.schema.toml": '[A]\ndescription="a"\n',
            ".env": "A=1\n",
            ".env.staging": "B=2\n",
        },
        "args": [".env.staging"],
    },
    "python_local_file": {
        "files": {
            "envshield.yml": (
                "services:\n  app:\n    schema: env.schema.toml\n"
                "    local_file: config/settings.py\n"
            ),
            "env.schema.toml": '[A]\ndescription="a"\n\n[B]\ntype="int"\n',
            "config/settings.py": 'A = "x"\nB = "nope"\n',
        },
    },
    "compose_manifest": {
        "files": {
            "envshield.yml": (
                ONE + "manifests:\n  - file: docker-compose.yml\n"
                "    containers:\n      web: app\n"
            ),
            "env.schema.toml": '[A]\ndescription="a"\n\n[B]\ntype="int"\n',
            ".env": "A=1\nB=2\n",
            "docker-compose.yml": (
                "services:\n  web:\n    image: x\n    environment:\n"
                "      A: '1'\n      B: two\n"
            ),
        },
    },
    "compose_layers_and_container_option": {
        "files": {
            "envshield.yml": (
                ONE
                + "manifests:\n  - files: [docker-compose.yml, docker-compose.override.yml]\n"
                "    containers:\n      web: app\n"
            ),
            "env.schema.toml": '[A]\ndescription="a"\n\n[B]\ndescription="b"\n',
            ".env": "A=1\nB=2\n",
            "docker-compose.yml": (
                "services:\n  web:\n    image: x\n    environment:\n      A: '1'\n"
                "  worker:\n    image: x\n    environment:\n      A: '1'\n"
            ),
            "docker-compose.override.yml": (
                "services:\n  web:\n    environment:\n      B: '2'\n"
            ),
        },
    },
    "kubernetes_unresolved_envfrom": {
        "files": {
            "envshield.yml": (
                ONE + "manifests:\n  - file: k8s.yml\n    containers:\n      app: app\n"
            ),
            "env.schema.toml": '[A]\ndescription="a"\n\n[B]\ndescription="b"\n',
            ".env": "A=1\nB=2\n",
            "k8s.yml": (
                "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: app\n"
                "spec:\n  template:\n    spec:\n      containers:\n"
                "        - name: app\n          image: x\n          env:\n"
                "            - name: A\n              value: '1'\n"
                "          envFrom:\n            - secretRef:\n"
                "                name: external-secret\n"
            ),
        },
    },
    "union_satisfied_across_sources": {
        "files": {
            "envshield.yml": (
                "services:\n  app:\n    schema: env.schema.toml\n"
                "    completeness: union\n"
                "manifests:\n  - file: docker-compose.yml\n"
                "    containers:\n      app: app\n"
            ),
            "env.schema.toml": '[LOCAL]\ndescription="l"\n\n[FROM_COMPOSE]\ndescription="c"\n',
            ".env": "LOCAL=1\n",
            "docker-compose.yml": (
                "services:\n  app:\n    image: x\n    environment:\n"
                "      FROM_COMPOSE: '1'\n"
            ),
        },
    },
    "union_missing_everywhere": {
        "files": {
            "envshield.yml": (
                "services:\n  app:\n    schema: env.schema.toml\n"
                "    completeness: union\n"
                "manifests:\n  - file: docker-compose.yml\n"
                "    containers:\n      app: app\n"
            ),
            "env.schema.toml": '[LOCAL]\ndescription="l"\n\n[NOWHERE]\ndescription="n"\n',
            ".env": "LOCAL=1\n",
            "docker-compose.yml": (
                "services:\n  app:\n    image: x\n    environment:\n      LOCAL: '1'\n"
            ),
        },
    },
    "union_with_a_failing_source": {
        "files": {
            "envshield.yml": (
                "services:\n  app:\n    schema: env.schema.toml\n"
                "    completeness: union\n"
                "manifests:\n  - file: docker-compose.yml\n"
                "    containers:\n      app: app\n"
            ),
            "env.schema.toml": '[LOCAL]\ndescription="l"\n',
            ".env": "LOCAL=1\n",
            "docker-compose.yml": "services: [\n",
        },
    },
    "multi_service_one_broken": {
        "files": {
            "envshield.yml": (
                "services:\n  api:\n    schema: api/env.schema.toml\n"
                "  web:\n    schema: web/env.schema.toml\n"
            ),
            "api/env.schema.toml": '[A]\ndescription="a"\n',
            "api/.env": "A=1\n",
            "web/env.schema.toml": "[W\n",
            "web/.env": "W=1\n",
        },
    },
    "multi_service_explicit_file_is_ignored": {
        "files": {
            "envshield.yml": (
                "services:\n  api:\n    schema: api/env.schema.toml\n"
                "  web:\n    schema: web/env.schema.toml\n"
            ),
            "api/env.schema.toml": '[A]\ndescription="a"\n',
            "api/.env": "A=1\n",
            "web/env.schema.toml": '[W]\ndescription="w"\n',
            "web/.env": "",
        },
        "args": ["api/.env"],
    },
    "shared_schema_out_of_scope_extra": {
        "files": {
            "envshield.yml": (
                "services:\n  api:\n    schema: env.schema.toml\n    dir: api\n"
                "  worker:\n    schema: env.schema.toml\n    dir: worker\n"
            ),
            "env.schema.toml": (
                '[SHARED]\ndescription="s"\n\n'
                '[API_ONLY]\nsecret=true\nservices=["api"]\n\n'
                '[WORKER_ONLY]\nservices=["worker"]\n'
            ),
            "api/.env": "SHARED=1\nAPI_ONLY=x\nWORKER_ONLY=1\n",
            "worker/.env": "SHARED=1\n",
        },
    },
    "shared_physical_file": {
        "files": {
            "envshield.yml": (
                "services:\n  api:\n    schema: env.schema.toml\n    dir: api\n"
                "    local_file: .env\n"
                "  worker:\n    schema: env.schema.toml\n    dir: worker\n"
                "    local_file: .env\n"
            ),
            "env.schema.toml": (
                '[SHARED]\ndescription="s"\n\n'
                '[API_ONLY]\nservices=["api"]\n\n'
                '[WORKER_ONLY]\nservices=["worker"]\n'
            ),
            "api/.keep": "",
            "worker/.keep": "",
            ".env": "SHARED=1\nAPI_ONLY=1\nWORKER_ONLY=1\n",
        },
    },
    "unknown_service": {
        "files": {
            "envshield.yml": ONE,
            "env.schema.toml": '[A]\ndescription="a"\n',
            ".env": "A=1\n",
        },
        "args": ["--service", "nope"],
    },
}
