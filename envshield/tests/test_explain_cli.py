# envshield/tests/test_explain_cli.py
import json

from typer.testing import CliRunner

from envshield.cli import app

runner = CliRunner()


def _write(relative, content):
    with open(relative, "w") as f:
        f.write(content)


class TestExplainNotFound:
    """
    An undeclared variable now degrades gracefully instead of only
    erroring (these three tests used to assert a bare "not found" error
    payload as correct -- rewritten, not deleted, to assert the new
    graceful-degradation report instead; still exit 1, since the variable
    genuinely isn't declared, but the CLI now reports what it *can* find
    rather than nothing at all).
    """

    def test_unknown_variable_reports_not_declared_and_stays_non_zero(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _write("envshield.yml", "services:\n  api:\n    schema: env.schema.toml\n")
            _write("env.schema.toml", '[X]\ndescription = "x"\n')

            result = runner.invoke(
                app, ["explain", "DOES_NOT_EXIST", "--service", "api"]
            )

            assert result.exit_code == 1
            assert "DOES_NOT_EXIST" in result.stdout
            assert "not declared" in result.stdout.lower()

    def test_unknown_variable_with_a_source_read_names_the_file_and_line(
        self, tmp_path
    ):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _write("envshield.yml", "services:\n  api:\n    schema: env.schema.toml\n")
            _write("env.schema.toml", '[X]\ndescription = "x"\n')
            _write("config.py", "import os\nos.getenv('DOES_NOT_EXIST')\n")

            result = runner.invoke(
                app, ["explain", "DOES_NOT_EXIST", "--service", "api"]
            )

            assert result.exit_code == 1
            assert "config.py:2" in result.stdout

    def test_unknown_variable_json_reports_found_false_not_a_silent_success(
        self, tmp_path
    ):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _write("envshield.yml", "services:\n  api:\n    schema: env.schema.toml\n")
            _write("env.schema.toml", '[X]\ndescription = "x"\n')

            result = runner.invoke(
                app, ["explain", "DOES_NOT_EXIST", "--service", "api", "--json"]
            )

            assert result.exit_code == 1
            payload = json.loads(result.stdout)
            assert payload["found"] is False
            assert payload["declared"] is False
            assert "schema" not in payload

    def test_unknown_variable_json_matches_undeclared_report_shape(self, tmp_path):
        """
        The shape UndeclaredVariableReport.to_dict() defines: identity keys
        (variable, service), two boolean status keys (found=False,
        declared=False), and the same source_usages/manifest_references
        keys a declared variable's report carries -- no bespoke partial
        shape, and no more 'error' key for this specific case (still used
        for every other explain failure, e.g. a missing envshield.yml).
        """
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _write("envshield.yml", "services:\n  api:\n    schema: env.schema.toml\n")
            _write("env.schema.toml", '[X]\ndescription = "x"\n')
            _write("config.py", "import os\nos.getenv('DOES_NOT_EXIST')\n")

            result = runner.invoke(
                app, ["explain", "DOES_NOT_EXIST", "--service", "api", "--json"]
            )

            payload = json.loads(result.stdout)
            assert payload["variable"] == "DOES_NOT_EXIST"
            assert payload["service"] == "api"
            assert payload["found"] is False
            assert payload["declared"] is False
            assert "error" not in payload
            assert len(payload["source_usages"]) == 1
            assert payload["source_usages"][0]["file_path"] == "config.py"
            assert payload["manifest_references"] == []

    def test_service_resolution_failure_reports_a_null_service(self, tmp_path):
        """
        Ambiguous multi-service project, no --service, no terminal to
        prompt on (CliRunner's stdin isn't a TTY) -- the service is
        genuinely unknown at the point of failure, which the error shape
        must say honestly rather than guessing or omitting the key.
        """
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _write(
                "envshield.yml",
                "services:\n"
                "  api:\n"
                "    schema: api.schema.toml\n"
                "  worker:\n"
                "    schema: worker.schema.toml\n",
            )
            _write("api.schema.toml", '[X]\ndescription = "x"\n')
            _write("worker.schema.toml", '[Y]\ndescription = "x"\n')

            result = runner.invoke(app, ["explain", "X", "--json"])

            assert result.exit_code == 1
            payload = json.loads(result.stdout)
            assert payload["variable"] == "X"
            assert payload["service"] is None
            assert payload["found"] is False
            assert "error" in payload


class TestExplainServiceSelection:
    def test_service_flag_selects_the_correct_service(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _write(
                "envshield.yml",
                "services:\n"
                "  api:\n"
                "    schema: api.schema.toml\n"
                "  worker:\n"
                "    schema: worker.schema.toml\n",
            )
            _write("api.schema.toml", '[API_ONLY]\ndescription = "x"\n')
            _write("worker.schema.toml", '[WORKER_ONLY]\ndescription = "x"\n')

            result = runner.invoke(app, ["explain", "API_ONLY", "--service", "api"])
            assert result.exit_code == 0

            result = runner.invoke(app, ["explain", "API_ONLY", "--service", "worker"])
            assert result.exit_code == 1

    def test_single_service_project_needs_no_service_flag(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _write("envshield.yml", "services:\n  api:\n    schema: env.schema.toml\n")
            _write("env.schema.toml", '[DATABASE_URL]\ndescription = "x"\n')

            result = runner.invoke(app, ["explain", "DATABASE_URL"])

            assert result.exit_code == 0
            assert "DATABASE_URL" in result.stdout


class TestExplainNoEvidenceIsNotAbsenceProof:
    def test_no_source_usages_says_none_discovered_with_a_caveat(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _write("envshield.yml", "services:\n  api:\n    schema: env.schema.toml\n")
            _write("env.schema.toml", '[DATABASE_URL]\ndescription = "x"\n')

            result = runner.invoke(app, ["explain", "DATABASE_URL"])

            assert "None discovered" in result.stdout
            assert "doesn't prove" in result.stdout

    def test_no_manifests_registered_is_distinguished_from_not_declared(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _write("envshield.yml", "services:\n  api:\n    schema: env.schema.toml\n")
            _write("env.schema.toml", '[DATABASE_URL]\ndescription = "x"\n')

            result = runner.invoke(app, ["explain", "DATABASE_URL"])

            assert "No deployment manifest registered" in result.stdout

    def test_manifests_registered_but_not_declaring_says_so_without_proof_claim(
        self, tmp_path
    ):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _write(
                "envshield.yml",
                "services:\n  api:\n    schema: env.schema.toml\n"
                "manifests:\n  - file: docker-compose.yml\n    containers:\n      api: api\n",
            )
            _write("env.schema.toml", '[DATABASE_URL]\ndescription = "x"\n')
            _write("docker-compose.yml", "services:\n  api:\n    image: x\n")

            result = runner.invoke(app, ["explain", "DATABASE_URL"])

            assert "Not found in any registered deployment manifest" in result.stdout
            assert "doesn't prove" in result.stdout

    def test_manifest_with_unresolved_env_from_says_cannot_confirm_not_not_declared(
        self, tmp_path
    ):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _write(
                "envshield.yml",
                "services:\n  api:\n    schema: env.schema.toml\n"
                "manifests:\n  - file: deployment.yaml\n    containers:\n      api: api\n",
            )
            _write("env.schema.toml", '[DATABASE_URL]\ndescription = "x"\n')
            _write(
                "deployment.yaml",
                "apiVersion: apps/v1\n"
                "kind: Deployment\n"
                "metadata:\n  name: api\n"
                "spec:\n"
                "  template:\n"
                "    spec:\n"
                "      containers:\n"
                "        - name: api\n"
                "          envFrom:\n"
                "            - secretRef:\n"
                "                name: externally-managed-secret\n",
            )

            result = runner.invoke(app, ["explain", "DATABASE_URL"])

            assert "Cannot confirm" in result.stdout
            assert (
                "Not found in any registered deployment manifest" not in result.stdout
            )

    def test_manifest_with_unresolved_env_from_reports_unresolved_status_in_json(
        self, tmp_path
    ):
        """Same fixture as the Rich test above, via --json -- both paths must agree."""
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _write(
                "envshield.yml",
                "services:\n  api:\n    schema: env.schema.toml\n"
                "manifests:\n  - file: deployment.yaml\n    containers:\n      api: api\n",
            )
            _write("env.schema.toml", '[DATABASE_URL]\ndescription = "x"\n')
            _write(
                "deployment.yaml",
                "apiVersion: apps/v1\n"
                "kind: Deployment\n"
                "metadata:\n  name: api\n"
                "spec:\n"
                "  template:\n"
                "    spec:\n"
                "      containers:\n"
                "        - name: api\n"
                "          envFrom:\n"
                "            - secretRef:\n"
                "                name: externally-managed-secret\n",
            )

            result = runner.invoke(app, ["explain", "DATABASE_URL", "--json"])

            payload = json.loads(result.stdout)
            manifest_ref = payload["manifest_references"][0]
            assert manifest_ref["status"] == "unresolved"


class TestExplainJsonShape:
    def test_json_shape_is_evidence_oriented_and_stable(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _write(
                "envshield.yml",
                "services:\n  api:\n    schema: env.schema.toml\n"
                "manifests:\n  - file: docker-compose.yml\n    containers:\n      api: api\n",
            )
            _write(
                "env.schema.toml",
                '[DATABASE_URL]\ndescription = "x"\ntype = "url"\nsecret = true\n\n'
                '[REPLICA_URL]\ndescription = "x"\n'
                'requiredIf = { var = "DATABASE_URL", equals = "postgres" }\n',
            )
            _write(
                "docker-compose.yml",
                "services:\n  api:\n    environment:\n      - DATABASE_URL=x\n",
            )
            _write("config.py", "import os\nos.environ['DATABASE_URL']\n")

            result = runner.invoke(app, ["explain", "DATABASE_URL", "--json"])

            assert result.exit_code == 0
            payload = json.loads(result.stdout)
            assert payload["variable"] == "DATABASE_URL"
            assert payload["service"] == "api"
            assert payload["found"] is True
            assert "error" not in payload
            assert payload["schema"]["type"] == "url"
            assert payload["schema"]["secret"] is True
            assert payload["provenance"]["schema_path"] == "env.schema.toml"
            assert payload["provenance"]["inherited"] is False
            assert payload["dependencies"]["required_by"] == [
                {
                    "variable": "REPLICA_URL",
                    "condition": {"var": "DATABASE_URL", "equals": "postgres"},
                }
            ]
            usage = payload["source_usages"][0]
            assert usage["file_path"] == "config.py"
            assert usage["line"] == 2
            manifest_ref = payload["manifest_references"][0]
            assert manifest_ref["path"] == "docker-compose.yml"
            assert manifest_ref["status"] == "declared"

    def test_json_output_has_no_rich_noise(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _write("envshield.yml", "services:\n  api:\n    schema: env.schema.toml\n")
            _write("env.schema.toml", '[X]\ndescription = "x"\n')

            result = runner.invoke(app, ["explain", "X", "--json"])

            json.loads(result.stdout)  # must be exactly one JSON document


class TestExplainInheritedField:
    def test_inherited_field_reports_provenance_in_json_and_text(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            import os

            os.makedirs("shared")
            os.makedirs("api")
            _write(
                "envshield.yml", "services:\n  api:\n    schema: api/env.schema.toml\n"
            )
            _write("shared/base.schema.toml", '[DATABASE_URL]\ndescription = "x"\n')
            _write("api/env.schema.toml", 'extends = "../shared/base.schema.toml"\n')

            result = runner.invoke(app, ["explain", "DATABASE_URL"])
            assert "inherited from shared/base.schema.toml" in result.stdout

            result = runner.invoke(app, ["explain", "DATABASE_URL", "--json"])
            payload = json.loads(result.stdout)
            assert payload["provenance"]["inherited"] is True
            assert payload["provenance"]["declared_in"] == "shared/base.schema.toml"
