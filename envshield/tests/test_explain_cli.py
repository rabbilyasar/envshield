# envshield/tests/test_explain_cli.py
import json

from typer.testing import CliRunner

from envshield.cli import app

runner = CliRunner()


def _write(relative, content):
    with open(relative, "w") as f:
        f.write(content)


class TestExplainNotFound:
    def test_unknown_variable_is_a_clear_non_zero_error(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _write("envshield.yml", "services:\n  api:\n    schema: env.schema.toml\n")
            _write("env.schema.toml", '[X]\ndescription = "x"\n')

            result = runner.invoke(
                app, ["explain", "DOES_NOT_EXIST", "--service", "api"]
            )

            assert result.exit_code == 1
            assert "DOES_NOT_EXIST" in result.stdout
            assert "not found" in result.stdout.lower()

    def test_unknown_variable_json_error_is_not_a_silent_empty_success(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _write("envshield.yml", "services:\n  api:\n    schema: env.schema.toml\n")
            _write("env.schema.toml", '[X]\ndescription = "x"\n')

            result = runner.invoke(
                app, ["explain", "DOES_NOT_EXIST", "--service", "api", "--json"]
            )

            assert result.exit_code == 1
            payload = json.loads(result.stdout)
            assert "error" in payload
            assert "schema" not in payload

    def test_unknown_variable_json_error_matches_check_results_convention(
        self, tmp_path
    ):
        """
        Same shape schema_manager.check_result uses for its own error
        return: identity keys (variable, service), a boolean status key
        (found=False), and 'error' -- no null-padded data keys, no
        bespoke partial shape.
        """
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _write("envshield.yml", "services:\n  api:\n    schema: env.schema.toml\n")
            _write("env.schema.toml", '[X]\ndescription = "x"\n')

            result = runner.invoke(
                app, ["explain", "DOES_NOT_EXIST", "--service", "api", "--json"]
            )

            payload = json.loads(result.stdout)
            assert payload == {
                "variable": "DOES_NOT_EXIST",
                "service": "api",
                "found": False,
                "error": payload["error"],
            }
            assert "DOES_NOT_EXIST" in payload["error"]

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
