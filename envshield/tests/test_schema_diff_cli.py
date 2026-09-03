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

    def test_no_arguments_compares_head_against_the_working_tree(self, tmp_path):
        """
        Name corrected alongside the direction fix: this test only ever
        asserted that both variable names appear, which is true in either
        direction -- see TestNoArgumentDiffDirection for the assertions
        that actually pin the direction down.
        """
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _init_repo()
            _write("envshield.yml", "services:\n  api:\n    schema: env.schema.toml\n")
            _write("env.schema.toml", '[OLD]\ndescription = "x"\n')
            _commit("v1")
            _write("env.schema.toml", '[NEW]\ndescription = "x"\n')  # uncommitted

            result = runner.invoke(app, ["schema", "diff"])

            assert "NEW" in result.stdout
            assert "OLD" in result.stdout


class TestNoArgumentDiffDirection:
    """
    Regression: the no-argument form used to pass the *working tree* as
    diff_schemas' left (baseline) operand and HEAD as its right,
    reporting every uncommitted change inside out -- a newly added
    variable came back as "removed / informational" and a deleted one as
    "added". Because a required addition is the single change the
    command most needs to classify as breaking, the inversion also made
    'has_breaking_changes' false and let --fail-on pass on a genuinely
    breaking uncommitted change: a silent false-clean, in exactly the
    pre-commit position this form is meant for.

    The no-argument form must mean HEAD -> working tree, matching both
    the documented wording and 'undeclared's identical default.
    """

    @staticmethod
    def _repo_with_uncommitted_required_addition():
        """HEAD declares OLD_VAR (defaulted); the working tree replaces it with NEW_VAR (required)."""
        _init_repo()
        _write("envshield.yml", "services:\n  api:\n    schema: env.schema.toml\n")
        _write(
            "env.schema.toml",
            '[OLD_VAR]\ndescription = "old"\ntype = "string"\ndefaultValue = "x"\n',
        )
        _commit("v1")
        _write(
            "env.schema.toml",
            '[NEW_VAR]\ndescription = "new"\ntype = "string"\nrequired = true\n',
        )

    def test_an_uncommitted_required_addition_is_breaking(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            self._repo_with_uncommitted_required_addition()

            result = runner.invoke(app, ["schema", "diff", "--json"])
            payload = json.loads(result.stdout)

            assert payload["has_breaking_changes"] is True
            changes = {c["variable"]: c for c in payload["results"][0]["changes"]}
            assert changes["NEW_VAR"]["category"] == "breaking"
            assert "was added" in changes["NEW_VAR"]["description"]

    def test_an_uncommitted_removal_is_reported_as_a_removal(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            self._repo_with_uncommitted_required_addition()

            result = runner.invoke(app, ["schema", "diff", "--json"])
            payload = json.loads(result.stdout)

            changes = {c["variable"]: c for c in payload["results"][0]["changes"]}
            assert changes["OLD_VAR"]["category"] == "informational"
            assert changes["OLD_VAR"]["detail"] == {"removed": True}

    def test_json_labels_head_as_the_baseline_side(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            self._repo_with_uncommitted_required_addition()

            result = runner.invoke(app, ["schema", "diff", "--json"])
            payload = json.loads(result.stdout)

            assert payload["results"][0]["revision_a"] == "HEAD"
            assert payload["results"][0]["revision_b"] == "working tree"

    def test_human_output_labels_head_as_the_baseline_side(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            self._repo_with_uncommitted_required_addition()

            result = runner.invoke(app, ["schema", "diff"])

            assert "'HEAD' -> 'working tree'" in result.stdout

    def test_fail_on_blocks_an_uncommitted_breaking_change(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            self._repo_with_uncommitted_required_addition()

            result = runner.invoke(app, ["schema", "diff"])

            assert result.exit_code == 1

    def test_an_uncommitted_defaulted_addition_stays_non_breaking(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _init_repo()
            _write("envshield.yml", "services:\n  api:\n    schema: env.schema.toml\n")
            _write("env.schema.toml", '[KEPT]\ndescription = "k"\n')
            _commit("v1")
            _write(
                "env.schema.toml",
                '[KEPT]\ndescription = "k"\n\n'
                '[ADDED]\ndescription = "a"\ntype = "string"\ndefaultValue = "d"\n',
            )

            result = runner.invoke(app, ["schema", "diff", "--json"])
            payload = json.loads(result.stdout)

            assert payload["has_breaking_changes"] is False
            changes = {c["variable"]: c for c in payload["results"][0]["changes"]}
            assert changes["ADDED"]["category"] == "non_breaking"
            assert result.exit_code == 0

    def test_it_agrees_with_the_equivalent_explicit_two_revision_form(self, tmp_path):
        """
        The two forms must classify the same underlying change
        identically -- the explicit form was already correct, so
        committing the working tree and diffing HEAD~1..HEAD must yield
        the same categories the no-argument form reported beforehand.
        """
        with runner.isolated_filesystem(temp_dir=tmp_path):
            self._repo_with_uncommitted_required_addition()

            implicit = json.loads(
                runner.invoke(app, ["schema", "diff", "--json"]).stdout
            )
            _commit("v2")
            explicit = json.loads(
                runner.invoke(
                    app, ["schema", "diff", "HEAD~1", "HEAD", "--json"]
                ).stdout
            )

            def categories(payload):
                return {
                    c["variable"]: c["category"]
                    for c in payload["results"][0]["changes"]
                }

            assert categories(implicit) == categories(explicit)
            assert implicit["has_breaking_changes"] == explicit["has_breaking_changes"]

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


class TestSchemaDiffAdoptionBoundary:
    """
    Regression: diffing across the revision where EnvShield was first
    adopted (no envshield.yml/schema at the older revision) used to hard-
    error identically to a genuine typo'd revision -- 'envshield.yml does
    not exist at revision X' either way, with no way to tell them apart.
    The older side is now treated as an implicit empty contract (every
    variable in the newer schema reports as "added"); a genuinely bad
    revision string is now rejected by its own, distinct error, checked
    before any schema is loaded.
    """

    def _adoption_boundary_repo(self):
        _init_repo()
        _write("app.py", "import os\nos.environ.get('DATABASE_URL')\n")
        _commit("commit A: existing project, no EnvShield")
        _write("envshield.yml", "services:\n  app:\n    schema: env.schema.toml\n")
        _write(
            "env.schema.toml",
            '[DATABASE_URL]\ndescription = "x"\n\n'
            '[PORT]\ndescription = "y"\ndefaultValue = "8000"\n',
        )
        _commit("commit B: EnvShield adopted")
        _write(
            "env.schema.toml",
            '[DATABASE_URL]\ndescription = "x"\n\n'
            '[PORT]\ndescription = "y"\ndefaultValue = "8000"\n\n'
            '[LOG_LEVEL]\ndescription = "z"\ndefaultValue = "info"\n',
        )
        _commit("commit C: schema gains LOG_LEVEL")

    def test_a_to_b_shows_the_initial_contract_as_added_not_an_error(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            self._adoption_boundary_repo()

            result = runner.invoke(app, ["schema", "diff", "HEAD~2", "HEAD~1"])

            assert (
                result.exit_code == 1
            )  # DATABASE_URL added with no default -> breaking
            assert "does not exist at revision" not in result.stdout
            assert "DATABASE_URL" in result.stdout
            assert "PORT" in result.stdout
            assert "No EnvShield contract existed" in result.stdout

    def test_a_to_c_also_shows_the_full_contract_as_added(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            self._adoption_boundary_repo()

            result = runner.invoke(app, ["schema", "diff", "HEAD~2", "HEAD"])

            assert "DATABASE_URL" in result.stdout
            assert "PORT" in result.stdout
            assert "LOG_LEVEL" in result.stdout

    def test_b_to_c_is_unaffected_by_the_adoption_boundary_leniency(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            self._adoption_boundary_repo()

            result = runner.invoke(app, ["schema", "diff", "HEAD~1", "HEAD"])

            assert "LOG_LEVEL" in result.stdout
            assert "DATABASE_URL" not in result.stdout
            assert "PORT" not in result.stdout
            assert "No EnvShield contract existed" not in result.stdout

    def test_json_marks_the_adoption_boundary_explicitly(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            self._adoption_boundary_repo()

            result = runner.invoke(
                app, ["schema", "diff", "HEAD~2", "HEAD~1", "--json"]
            )

            payload = json.loads(result.stdout)
            assert payload["results"][0]["revision_a_predates_adoption"] is True
            variables = {c["variable"] for c in payload["results"][0]["changes"]}
            assert variables == {"DATABASE_URL", "PORT"}

    def test_json_does_not_mark_a_normal_two_schema_comparison(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            self._adoption_boundary_repo()

            result = runner.invoke(app, ["schema", "diff", "HEAD~1", "HEAD", "--json"])

            payload = json.loads(result.stdout)
            assert payload["results"][0]["revision_a_predates_adoption"] is False

    def test_the_reverse_direction_still_errors_instead_of_silently_emptying(
        self, tmp_path
    ):
        """
        The leniency only ever applies to the OLDER (left) side. A target
        revision with no contract at all -- e.g. arguments swapped by
        mistake -- must still raise, not silently report every variable as
        "removed" against an implicit empty target.
        """
        with runner.isolated_filesystem(temp_dir=tmp_path):
            self._adoption_boundary_repo()

            result = runner.invoke(app, ["schema", "diff", "HEAD~1", "HEAD~2"])

            assert result.exit_code == 1
            assert "does not exist at revision" in result.stdout

    def test_a_genuinely_bad_revision_gets_its_own_distinct_error(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            self._adoption_boundary_repo()

            result = runner.invoke(
                app, ["schema", "diff", "not-a-real-revision", "HEAD"]
            )

            assert result.exit_code == 1
            assert "does not resolve to a commit" in result.stdout
            assert "does not exist at revision" not in result.stdout

    def test_default_working_tree_form_is_not_affected_by_this_leniency(self, tmp_path):
        """
        The adoption-boundary leniency is scoped to the explicit two-
        revision form only -- the default (no-args) working-tree-vs-HEAD
        comparison keeps its existing, unrelated behavior for a project
        with no envshield.yml at all yet.
        """
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _init_repo()
            _write("app.py", "import os\n")
            _commit("no envshield.yml at all, anywhere in history")

            result = runner.invoke(app, ["schema", "diff"])

            assert result.exit_code == 1
            assert "No services configured" in result.stdout


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

    def test_secret_weakened_exits_nonzero_under_the_default_fail_on_set(
        self, tmp_path
    ):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _init_repo()
            _write("envshield.yml", "services:\n  api:\n    schema: env.schema.toml\n")
            _write("env.schema.toml", '[API_KEY]\ndescription = "x"\nsecret = true\n')
            _commit("v1")
            _write("env.schema.toml", '[API_KEY]\ndescription = "x"\nsecret = false\n')
            _commit("v2")

            result = runner.invoke(app, ["schema", "diff", "HEAD~1", "HEAD"])

            assert result.exit_code == 1

    def test_secret_tightened_exits_zero(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _init_repo()
            _write("envshield.yml", "services:\n  api:\n    schema: env.schema.toml\n")
            _write("env.schema.toml", '[API_KEY]\ndescription = "x"\nsecret = false\n')
            _commit("v1")
            _write("env.schema.toml", '[API_KEY]\ndescription = "x"\nsecret = true\n')
            _commit("v2")

            result = runner.invoke(app, ["schema", "diff", "HEAD~1", "HEAD"])

            assert result.exit_code == 0


class TestSchemaDiffFailOn:
    def test_fail_on_can_narrow_out_security(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _init_repo()
            _write("envshield.yml", "services:\n  api:\n    schema: env.schema.toml\n")
            _write("env.schema.toml", '[API_KEY]\ndescription = "x"\nsecret = true\n')
            _commit("v1")
            _write("env.schema.toml", '[API_KEY]\ndescription = "x"\nsecret = false\n')
            _commit("v2")

            result = runner.invoke(
                app, ["schema", "diff", "HEAD~1", "HEAD", "--fail-on", "breaking"]
            )

            assert result.exit_code == 0

    def test_fail_on_rejects_an_unknown_category(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _init_repo()
            _write("envshield.yml", "services:\n  api:\n    schema: env.schema.toml\n")
            _write("env.schema.toml", '[X]\ndescription = "x"\n')
            _commit("v1")

            result = runner.invoke(
                app, ["schema", "diff", "HEAD", "HEAD", "--fail-on", "bogus"]
            )

            assert result.exit_code == 1
            assert "unknown --fail-on category" in result.stdout

    def test_requires_review_blocks_by_default(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _init_repo()
            _write("envshield.yml", "services:\n  api:\n    schema: env.schema.toml\n")
            _write("env.schema.toml", '[V]\ndescription = "x"\npattern = "^v[0-9]+$"\n')
            _commit("v1")
            _write("env.schema.toml", '[V]\ndescription = "x"\npattern = "^[0-9]+$"\n')
            _commit("v2")

            result = runner.invoke(app, ["schema", "diff", "HEAD~1", "HEAD"])

            assert result.exit_code == 1

    def test_requires_review_can_be_excluded_via_fail_on(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _init_repo()
            _write("envshield.yml", "services:\n  api:\n    schema: env.schema.toml\n")
            _write("env.schema.toml", '[V]\ndescription = "x"\npattern = "^v[0-9]+$"\n')
            _commit("v1")
            _write("env.schema.toml", '[V]\ndescription = "x"\npattern = "^[0-9]+$"\n')
            _commit("v2")

            result = runner.invoke(
                app, ["schema", "diff", "HEAD~1", "HEAD", "--fail-on", "breaking"]
            )

            assert result.exit_code == 0


class TestSchemaDiffBlockingJson:
    def test_json_includes_has_blocking_changes_and_per_change_blocking_flag(
        self, tmp_path
    ):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _init_repo()
            _write("envshield.yml", "services:\n  api:\n    schema: env.schema.toml\n")
            _write("env.schema.toml", '[API_KEY]\ndescription = "x"\nsecret = true\n')
            _commit("v1")
            _write("env.schema.toml", '[API_KEY]\ndescription = "x"\nsecret = false\n')
            _commit("v2")

            result = runner.invoke(app, ["schema", "diff", "HEAD~1", "HEAD", "--json"])

            payload = json.loads(result.stdout)
            assert payload["has_blocking_changes"] is True
            entry = payload["results"][0]
            assert entry["has_blocking_changes"] is True
            change = entry["changes"][0]
            assert change["blocking"] is True

    def test_json_blocking_flag_respects_fail_on(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _init_repo()
            _write("envshield.yml", "services:\n  api:\n    schema: env.schema.toml\n")
            _write("env.schema.toml", '[API_KEY]\ndescription = "x"\nsecret = true\n')
            _commit("v1")
            _write("env.schema.toml", '[API_KEY]\ndescription = "x"\nsecret = false\n')
            _commit("v2")

            result = runner.invoke(
                app,
                [
                    "schema",
                    "diff",
                    "HEAD~1",
                    "HEAD",
                    "--json",
                    "--fail-on",
                    "breaking",
                ],
            )

            payload = json.loads(result.stdout)
            assert payload["has_blocking_changes"] is False
            assert payload["results"][0]["changes"][0]["blocking"] is False


class TestSchemaDiffExplainHint:
    def test_blocking_change_prints_an_explain_hint_naming_the_variable(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _init_repo()
            _write("envshield.yml", "services:\n  api:\n    schema: env.schema.toml\n")
            _write("env.schema.toml", "")
            _commit("v1")
            _write("env.schema.toml", '[NEW_REQUIRED]\ndescription = "x"\n')
            _commit("v2")

            result = runner.invoke(app, ["schema", "diff", "HEAD~1", "HEAD"])

            assert "envshield explain <VAR>" in result.stdout
            assert "NEW_REQUIRED" in result.stdout.split("Inspect with")[-1]

    def test_single_service_hint_omits_the_service_flag(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _init_repo()
            _write("envshield.yml", "services:\n  api:\n    schema: env.schema.toml\n")
            _write("env.schema.toml", "")
            _commit("v1")
            _write("env.schema.toml", '[NEW_REQUIRED]\ndescription = "x"\n')
            _commit("v2")

            result = runner.invoke(app, ["schema", "diff", "HEAD~1", "HEAD"])

            assert "--service" not in result.stdout

    def test_multi_service_hint_includes_the_service_flag(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _init_repo()
            _write(
                "envshield.yml",
                "services:\n"
                "  api:\n    schema: api.schema.toml\n"
                "  worker:\n    schema: worker.schema.toml\n",
            )
            _write("api.schema.toml", "")
            _write("worker.schema.toml", "")
            _commit("v1")
            _write("api.schema.toml", '[NEW_REQUIRED]\ndescription = "x"\n')
            _commit("v2")

            result = runner.invoke(app, ["schema", "diff", "HEAD~1", "HEAD"])

            assert "--service api" in result.stdout

    def test_non_blocking_change_has_no_explain_hint(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _init_repo()
            _write("envshield.yml", "services:\n  api:\n    schema: env.schema.toml\n")
            _write("env.schema.toml", '[X]\ndescription = "old text"\n')
            _commit("v1")
            _write("env.schema.toml", '[X]\ndescription = "new text"\n')
            _commit("v2")

            result = runner.invoke(app, ["schema", "diff", "HEAD~1", "HEAD"])

            assert "Inspect with" not in result.stdout

    def test_json_output_is_unaffected_by_the_hint(self, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            _init_repo()
            _write("envshield.yml", "services:\n  api:\n    schema: env.schema.toml\n")
            _write("env.schema.toml", "")
            _commit("v1")
            _write("env.schema.toml", '[NEW_REQUIRED]\ndescription = "x"\n')
            _commit("v2")

            result = runner.invoke(app, ["schema", "diff", "HEAD~1", "HEAD", "--json"])

            json.loads(result.stdout)  # must still be exactly one JSON document
