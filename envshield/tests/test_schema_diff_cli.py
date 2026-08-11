# envshield/tests/test_schema_diff_cli.py
import json
import subprocess

from typer.testing import CliRunner

from envshield.cli import app

runner = CliRunner()


def _init_repo():
    subprocess.run(["git", "init", "-q"], check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "config", "user.name", "Test"], check=True)


def _commit(message):
    subprocess.run(["git", "add", "-A"], check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], check=True)


def _write(relative, content):
    with open(relative, "w") as f:
        f.write(content)


class TestSchemaDiffRevisionPairing:
    def test_one_argument_is_a_clean_error_not_a_guess(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _init_repo()
            _write("envshield.yml", "services:\n  api:\n    schema: env.schema.toml\n")
            _write("env.schema.toml", '[X]\ndescription = "x"\n')
            _commit("v1")

            result = runner.invoke(app, ["schema", "diff", "HEAD"])

            assert result.exit_code == 1
            assert "both revisions, or neither" in result.stdout

    def test_no_arguments_compares_working_tree_against_head(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _init_repo()
            _write("envshield.yml", "services:\n  api:\n    schema: env.schema.toml\n")
            _write("env.schema.toml", '[OLD]\ndescription = "x"\n')
            _commit("v1")
            _write("env.schema.toml", '[NEW]\ndescription = "x"\n')  # uncommitted

            result = runner.invoke(app, ["schema", "diff"])

            assert "NEW" in result.stdout
            assert "OLD" in result.stdout

    def test_two_explicit_revisions_are_compared(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _init_repo()
            _write("envshield.yml", "services:\n  api:\n    schema: env.schema.toml\n")
            _write("env.schema.toml", '[OLD]\ndescription = "x"\n')
            _commit("v1")
            _write("env.schema.toml", '[NEW]\ndescription = "x"\n')
            _commit("v2")

            result = runner.invoke(app, ["schema", "diff", "HEAD~1", "HEAD"])

            assert "NEW" in result.stdout
            assert "OLD" in result.stdout


class TestSchemaDiffExitCodes:
    def test_no_changes_exits_zero(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _init_repo()
            _write("envshield.yml", "services:\n  api:\n    schema: env.schema.toml\n")
            _write("env.schema.toml", '[X]\ndescription = "x"\n')
            _commit("v1")

            result = runner.invoke(app, ["schema", "diff", "HEAD", "HEAD"])

            assert result.exit_code == 0

    def test_breaking_change_exits_nonzero(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _init_repo()
            _write("envshield.yml", "services:\n  api:\n    schema: env.schema.toml\n")
            _write("env.schema.toml", "")
            _commit("v1")
            _write("env.schema.toml", '[NEW_REQUIRED]\ndescription = "x"\n')
            _commit("v2")

            result = runner.invoke(app, ["schema", "diff", "HEAD~1", "HEAD"])

            assert result.exit_code == 1
            assert "breaking" in result.stdout.lower()

    def test_non_breaking_change_exits_zero(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _init_repo()
            _write("envshield.yml", "services:\n  api:\n    schema: env.schema.toml\n")
            _write("env.schema.toml", '[X]\ndescription = "old text"\n')
            _commit("v1")
            _write("env.schema.toml", '[X]\ndescription = "new text"\n')
            _commit("v2")

            result = runner.invoke(app, ["schema", "diff", "HEAD~1", "HEAD"])

            assert result.exit_code == 0

    def test_unresolvable_revision_exits_nonzero_with_clear_error(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _init_repo()
            _write("envshield.yml", "services:\n  api:\n    schema: env.schema.toml\n")
            _write("env.schema.toml", '[X]\ndescription = "x"\n')
            _commit("v1")

            result = runner.invoke(
                app, ["schema", "diff", "not-a-real-revision", "HEAD"]
            )

            assert result.exit_code == 1


class TestSchemaDiffJsonOutput:
    def test_json_shape_matches_check_doctor_convention(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _init_repo()
            _write("envshield.yml", "services:\n  api:\n    schema: env.schema.toml\n")
            _write("env.schema.toml", "")
            _commit("v1")
            _write("env.schema.toml", '[NEW_REQUIRED]\ndescription = "x"\n')
            _commit("v2")

            result = runner.invoke(app, ["schema", "diff", "HEAD~1", "HEAD", "--json"])

            payload = json.loads(result.stdout)
            assert payload["has_breaking_changes"] is True
            assert isinstance(payload["results"], list)
            entry = payload["results"][0]
            assert entry["service"] == "api"
            assert entry["revision_a"] == "HEAD~1"
            assert entry["revision_b"] == "HEAD"
            change = next(
                c for c in entry["changes"] if c["variable"] == "NEW_REQUIRED"
            )
            assert change["category"] == "breaking"

    def test_json_output_has_no_rich_table_noise(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _init_repo()
            _write("envshield.yml", "services:\n  api:\n    schema: env.schema.toml\n")
            _write("env.schema.toml", '[X]\ndescription = "x"\n')
            _commit("v1")

            result = runner.invoke(app, ["schema", "diff", "HEAD", "HEAD", "--json"])

            # The entire stdout must be exactly one JSON document -- no Rich
            # table/header text mixed in around it.
            json.loads(result.stdout)


class TestSchemaDiffSecretReclassification:
    def test_secret_weakened_is_reported_as_security_not_breaking(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _init_repo()
            _write("envshield.yml", "services:\n  api:\n    schema: env.schema.toml\n")
            _write("env.schema.toml", '[API_KEY]\ndescription = "x"\nsecret = true\n')
            _commit("v1")
            _write("env.schema.toml", '[API_KEY]\ndescription = "x"\nsecret = false\n')
            _commit("v2")

            result = runner.invoke(app, ["schema", "diff", "HEAD~1", "HEAD", "--json"])

            payload = json.loads(result.stdout)
            assert payload["has_breaking_changes"] is False
            change = payload["results"][0]["changes"][0]
            assert change["category"] == "security"
            assert change["detail"]["severity"] == "weakened"
