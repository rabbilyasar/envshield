# envshield/tests/test_cli_consolidation.py
"""V0-2b (#42): overlapping commands folded into 'check' and 'init'.

- 'undeclared' -> 'check --since REV' and 'import' -> 'init --from FILE':
  hidden, deprecated, same result plus one stderr line.
- 'scan' reports undeclared reads only with --staged (the pre-commit hook).
- 'check --since REV': which undeclared reads are new (only those fail the
  gate) and how the contract changed (reported, never failed on). D-13.
"""

import json
import os
import re
import subprocess

import pytest
from typer.testing import CliRunner

from envshield.cli import app
from envshield.core import since

runner = CliRunner()

SECRET = "sk_live_canary_9f3b2c7d1e"


def _git(*args):
    subprocess.run(["git", *args], check=True, capture_output=True)


def _write(path, content):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


def _commit(message="c"):
    _git("add", "-A")
    _git("commit", "-q", "-m", message)


def _repo(schema='[PORT]\ndescription = "p"\n', env="PORT=8000\n"):
    _git("init", "-q")
    _git("config", "user.email", "t@example.com")
    _git("config", "user.name", "T")
    _git("config", "commit.gpgsign", "false")
    _write("envshield.yml", "services:\n  api:\n    schema: env.schema.toml\n")
    _write("env.schema.toml", schema)
    _write(".env", env)
    _write(".gitignore", ".env\n")
    _write("app.py", 'import os\nPORT = os.environ["PORT"]\n')
    _commit("base")


def _json(result):
    return json.loads(result.stdout)


def _report(payload):
    assert len(payload["reports"]) == 1
    return payload["reports"][0]


def _code_findings(report):
    return {
        f["variable"]: f for f in report["findings"] if f["kind"] == "code_reference"
    }


@pytest.fixture
def project(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _repo()
        yield


# --- --help -----------------------------------------------------------------


def test_help_hides_the_folded_commands():
    out = runner.invoke(app, ["--help"]).stdout
    listed = set(re.findall(r"^│ (\w[\w-]*) ", out, re.MULTILINE))
    assert {"init", "setup", "check", "hook", "scan"} <= listed
    assert not listed & {"undeclared", "import", "install-hook", "uninstall"}


def test_folded_commands_still_show_their_own_help():
    for command in ("undeclared", "import"):
        assert runner.invoke(app, [command, "--help"]).exit_code == 0, command


# --- undeclared -> check --since ----------------------------------------------


def test_undeclared_keeps_its_json_and_names_its_replacement(project):
    _write("new.py", 'import os\nos.getenv("NEW_FLAG")\n')

    result = runner.invoke(app, ["undeclared", "--json"])

    assert result.exit_code == 1
    payload = _json(result)
    assert payload["has_missing_declarations"] is True
    assert [c["variable"] for c in payload["changes"]] == ["NEW_FLAG"]
    assert "deprecated" in result.stderr
    assert "envshield check --since HEAD" in result.stderr
    assert "deprecated" not in result.stdout


def test_undeclared_with_two_revisions_names_the_baseline(project):
    _write("new.py", 'import os\nos.getenv("NEW_FLAG")\n')
    _commit("new")

    result = runner.invoke(app, ["undeclared", "HEAD~1", "HEAD", "--json"])

    assert result.exit_code == 1
    assert "check --since HEAD~1" in result.stderr


# --- import -> init --from ----------------------------------------------------


def test_import_keeps_working_and_names_its_replacement(project):
    _write("settings.env", "PORT=8000\nEXTRA=1\n")

    result = runner.invoke(app, ["import", "settings.env", "--force"])

    assert result.exit_code == 0
    assert "[EXTRA]" in open("env.schema.toml").read()
    assert "deprecated" in result.stderr
    assert "envshield init --from settings.env" in result.stderr


def test_init_from_on_a_new_project_uses_only_that_file(tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _git("init", "-q")
        _write("settings.env", "A=1\n")
        _write(".env", "B=2\n")  # init would otherwise merge this in

        result = runner.invoke(app, ["init", "--from", "settings.env"])

        assert result.exit_code == 0, result.stdout
        schema = open("env.schema.toml").read()
        assert "[A]" in schema
        assert "[B]" not in schema
        assert "config_source: settings.env" in open("envshield.yml").read()


def test_init_from_on_a_set_up_project_refreshes_like_import(project):
    _write("settings.env", "PORT=8000\nEXTRA=1\n")

    refused = runner.invoke(app, ["init", "--from", "settings.env"])
    assert refused.exit_code == 0
    assert "--force" in refused.stdout
    assert "[EXTRA]" not in open("env.schema.toml").read()

    result = runner.invoke(app, ["init", "--from", "settings.env", "--force"])

    assert result.exit_code == 0
    schema = open("env.schema.toml").read()
    assert "[EXTRA]" in schema
    assert "[PORT]" in schema  # never drops a declared variable
    assert "services:\n  api:" in open("envshield.yml").read()  # untouched


def test_init_from_with_service_targets_that_service(project):
    _write(
        "envshield.yml",
        "services:\n  api:\n    schema: env.schema.toml\n"
        "  worker:\n    schema: worker/env.schema.toml\n    dir: worker\n",
    )
    _write("worker/env.schema.toml", '[W]\ndescription = "w"\n')
    _write("worker.env", "W=1\nQUEUE=2\n")

    result = runner.invoke(
        app, ["init", "--from", "worker.env", "--service", "worker", "--force"]
    )

    assert result.exit_code == 0, result.stdout
    assert "[QUEUE]" in open("worker/env.schema.toml").read()
    assert "[QUEUE]" not in open("env.schema.toml").read()


@pytest.mark.parametrize(
    "flag", ["--service=api", "--interactive", "--as-local-values"]
)
def test_init_flags_that_need_from_are_refused_without_it(project, flag):
    result = runner.invoke(app, ["init", flag])

    assert result.exit_code == 2
    assert "only works with --from" in result.output


def test_init_from_a_missing_file_is_an_error(project):
    result = runner.invoke(app, ["init", "--from", "nope.env"])

    assert result.exit_code == 1
    assert "File not found" in result.stdout


# --- scan ----------------------------------------------------------------------


def test_scan_without_staged_reports_secrets_only(project):
    _write("new.py", 'import os\nos.getenv("NEW_FLAG")\n')

    result = runner.invoke(app, ["scan", "--json"])

    assert result.exit_code == 0
    payload = _json(result)
    assert payload["clean"] is True
    assert payload["undeclared_variables"] == []  # key kept for consumers
    assert "no longer reports undeclared" in result.stderr
    assert "envshield check" in result.stderr


def test_scan_without_staged_still_finds_secrets(project):
    _write("leak.py", 'api_key = "sk_live_abc123_real_looking_key"\n')

    result = runner.invoke(app, ["scan", "--json"])

    assert result.exit_code == 1
    assert _json(result)["secrets"]


def test_scan_staged_still_blocks_an_undeclared_read(project):
    """The pre-commit hook runs 'scan --staged --enforce'."""
    _write("new.py", 'import os\nos.getenv("NEW_FLAG")\n')
    _git("add", "new.py")

    result = runner.invoke(app, ["scan", "--staged", "--json"])

    assert result.exit_code == 1
    names = [u["variable_name"] for u in _json(result)["undeclared_variables"]]
    assert names == ["NEW_FLAG"]
    assert "no longer" not in result.stderr


def test_the_installed_hook_still_blocks_an_undeclared_read(project):
    assert runner.invoke(app, ["hook", "install", "--yes"]).exit_code == 0
    _write("new.py", 'import os\nos.getenv("NEW_FLAG")\n')
    _git("add", "new.py")

    commit = subprocess.run(
        ["git", "commit", "-q", "-m", "x"],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )

    assert commit.returncode != 0
    assert "NEW_FLAG" in commit.stdout + commit.stderr


# --- check --since: undeclared reads -----------------------------------------


def test_without_since_the_report_is_unchanged(project):
    _write("new.py", 'import os\nos.getenv("NEW_FLAG")\n')

    payload = _json(runner.invoke(app, ["check", "--json"]))

    report = _report(payload)
    assert "since" not in report
    assert "new" not in _code_findings(report)["NEW_FLAG"]
    assert "undeclared_before" not in payload["gate"]


def test_a_new_undeclared_read_fails(project):
    _write("new.py", 'import os\nos.getenv("NEW_FLAG")\n')

    result = runner.invoke(app, ["check", "--since", "HEAD", "--json"])

    assert result.exit_code == 1
    payload = _json(result)
    report = _report(payload)
    assert report["since"]["new_undeclared"] == ["NEW_FLAG"]
    assert _code_findings(report)["NEW_FLAG"]["new"] is True
    assert payload["gate"]["result"] == "fail"


def test_an_old_undeclared_read_is_listed_but_does_not_fail(project):
    _write("app.py", 'import os\nPORT = os.environ["PORT"]\nos.getenv("OLD_FLAG")\n')
    _commit("old read")

    result = runner.invoke(app, ["check", "--since", "HEAD", "--json"])

    assert result.exit_code == 0
    payload = _json(result)
    report = _report(payload)
    # The verdict is still reported, as with accepted unverified values.
    assert payload["verdict"] == "invalid"
    assert payload["success"] is False
    assert payload["gate"] == {
        "result": "pass",
        "accepted": {},
        "undeclared_before": ["OLD_FLAG"],
    }
    assert report["since"]["undeclared_before"] == ["OLD_FLAG"]
    assert _code_findings(report)["OLD_FLAG"]["new"] is False


def test_plain_check_still_fails_on_an_old_read(project):
    _write("app.py", 'import os\nPORT = os.environ["PORT"]\nos.getenv("OLD_FLAG")\n')
    _commit("old read")

    assert runner.invoke(app, ["check", "--json"]).exit_code == 1


def test_old_reads_pass_but_a_new_one_still_fails(project):
    _write("app.py", 'import os\nPORT = os.environ["PORT"]\nos.getenv("OLD_FLAG")\n')
    _commit("old read")
    _write("new.py", 'import os\nos.getenv("NEW_FLAG")\n')

    result = runner.invoke(app, ["check", "--since", "HEAD", "--json"])

    assert result.exit_code == 1
    since_block = _report(_json(result))["since"]
    assert since_block["new_undeclared"] == ["NEW_FLAG"]
    assert since_block["undeclared_before"] == ["OLD_FLAG"]


def test_other_failures_still_fail_with_since(project):
    _write("app.py", 'import os\nPORT = os.environ["PORT"]\nos.getenv("OLD_FLAG")\n')
    _commit("old read")
    _write(".env", "")  # PORT is required

    assert runner.invoke(app, ["check", "--since", "HEAD", "--json"]).exit_code == 1


def test_a_read_moved_to_another_file_is_old(project):
    _write("app.py", 'import os\nPORT = os.environ["PORT"]\nos.getenv("OLD_FLAG")\n')
    _commit("old read")
    os.rename("app.py", "main.py")

    result = runner.invoke(app, ["check", "--since", "HEAD", "--json"])

    assert result.exit_code == 0, result.stdout
    assert _report(_json(result))["since"]["undeclared_before"] == ["OLD_FLAG"]


SETTINGS = (
    "from pydantic_settings import BaseSettings\n\n"
    "class Settings(BaseSettings):\n    new_field: str\n"
)


def test_a_new_read_undeclared_cannot_see_is_still_new(project):
    """'undeclared's discovery doesn't see BaseSettings fields; 'check' does.
    Unproven means new: such a read must fail, never pass as old."""
    _write("settings.py", SETTINGS)

    result = runner.invoke(app, ["check", "--since", "HEAD", "--json"])

    assert result.exit_code == 1
    assert _report(_json(result))["since"]["new_undeclared"] == ["NEW_FIELD"]


def test_an_old_read_in_an_unchanged_file_is_proven_old(project):
    _write("settings.py", SETTINGS)
    _commit("settings")

    result = runner.invoke(app, ["check", "--since", "HEAD", "--json"])

    assert result.exit_code == 0, result.stdout
    assert _report(_json(result))["since"]["undeclared_before"] == ["NEW_FIELD"]


def test_read_before_fails_closed_when_git_gives_nothing(project, monkeypatch):
    _write("settings.py", SETTINGS)
    _commit("settings")
    from envshield.core import dependency_snapshot, evaluator
    from envshield.core.exceptions import EnvShieldException
    from envshield.utils import git_utils

    def broken(*args, **kwargs):
        raise EnvShieldException("git failed")

    monkeypatch.setattr(dependency_snapshot, "discover_usages_for_service", broken)
    monkeypatch.setattr(git_utils, "get_file_content_at_revision", lambda *a: None)
    undeclared = evaluator.evaluate_service("api", code=True).undeclared_references

    assert set(undeclared) == {"NEW_FIELD"}
    assert since.read_before("api", "HEAD", undeclared) == set()


def test_a_bad_revision_is_an_error(project):
    result = runner.invoke(app, ["check", "--since", "nope", "--json"])

    assert result.exit_code == 1
    assert _json(result) == {
        "success": False,
        "error": "revision 'nope' does not resolve to a commit.",
    }


def test_since_human_output_marks_new_and_old_reads(project):
    _write("app.py", 'import os\nPORT = os.environ["PORT"]\nos.getenv("OLD_FLAG")\n')
    _commit("old read")

    passing = runner.invoke(app, ["check", "--since", "HEAD"])
    assert passing.exit_code == 0
    assert "(before HEAD)" in passing.stdout
    assert "PASSED" in passing.stdout
    assert "without --since still fails" in passing.stdout

    _write("new.py", 'import os\nos.getenv("NEW_FLAG")\n')
    failing = runner.invoke(app, ["check", "--since", "HEAD"])
    assert failing.exit_code == 1
    assert "(new)" in failing.stdout
    assert "PASSED" not in failing.stdout


# --- check --since: contract changes -----------------------------------------


def test_contract_changes_are_findings_that_never_fail(project):
    _write(
        "env.schema.toml",
        '[PORT]\ndescription = "p"\n\n[API_KEY]\ndescription = "k"\nsecret = true\n',
    )
    _write(".env", f"PORT=8000\nAPI_KEY={SECRET}\n")

    result = runner.invoke(app, ["check", "--since", "HEAD", "--json"])

    assert result.exit_code == 0, result.stdout
    assert SECRET not in result.stdout
    report = _report(_json(result))
    assert report["since"]["contract"] == "compared"
    [change] = [f for f in report["findings"] if f["kind"] == "contract_change"]
    assert change["variable"] == "API_KEY"
    assert change["verdict"] is None
    assert change["reason_code"] == "breaking"
    assert change["blocking"] is True
    assert change["secret"] is True
    assert change["location"] == {
        "file": "env.schema.toml",
        "line": None,
        "container": None,
    }
    assert change["next_step"]["by"] == "human"
    # Not a verdict: never in the per-verdict splits.
    for key in ("errors", "invalid", "unverified"):
        assert change not in report[key]


def test_a_default_change_finding_names_no_value(project):
    _write("env.schema.toml", '[PORT]\ndescription = "p"\ndefaultValue = "8000"\n')
    _commit("default")
    _write("env.schema.toml", '[PORT]\ndescription = "p"\ndefaultValue = "9123"\n')

    result = runner.invoke(app, ["check", "--since", "HEAD", "--json"])

    [change] = [
        f for f in _report(_json(result))["findings"] if f["kind"] == "contract_change"
    ]
    assert change["reason_code"] == "default_changed"
    assert change["blocking"] is False
    assert change["next_step"]["by"] == "agent"
    assert "8000" not in change["detail"] and "9123" not in change["detail"]


def test_a_revision_without_a_contract_is_introduced(project):
    _write("README", "x")
    _commit("x")
    base = subprocess.run(
        ["git", "rev-list", "--max-parents=0", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    _git("checkout", "-q", "--orphan", "fresh")
    _git("rm", "-rq", "--cached", ".")
    _commit_nothing = subprocess.run(
        ["git", "commit", "-q", "--allow-empty", "-m", "empty"], capture_output=True
    )
    assert _commit_nothing.returncode == 0
    empty = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    assert empty != base

    result = runner.invoke(app, ["check", "--since", empty, "--json"])

    report = _report(_json(result))
    assert report["since"]["contract"] == "introduced"
    assert not [f for f in report["findings"] if f["kind"] == "contract_change"]


def test_contract_changes_show_in_human_output(project):
    _write("env.schema.toml", '[PORT]\ndescription = "p"\n\n[NEW]\nrequired = false\n')

    result = runner.invoke(app, ["check", "--since", "HEAD"])

    assert "Contract changes since HEAD" in result.stdout
    assert "NEW" in result.stdout


# --- check --since --sarif ------------------------------------------------------


def test_sarif_lists_only_new_undeclared_reads(project):
    _write("app.py", 'import os\nPORT = os.environ["PORT"]\nos.getenv("OLD_FLAG")\n')
    _commit("old read")
    _write("new.py", 'import os\nos.getenv("NEW_FLAG")\n')

    result = runner.invoke(app, ["check", "--since", "HEAD", "--sarif"])

    assert result.exit_code == 1
    doc = _json(result)
    assert doc["version"] == "2.1.0"
    results = doc["runs"][0]["results"]
    assert [r["properties"]["variable"] for r in results] == ["NEW_FLAG"]
    assert results[0]["locations"][0]["physicalLocation"]["artifactLocation"] == {
        "uri": "new.py"
    }


def test_sarif_exit_code_is_the_checks(project):
    _write("app.py", 'import os\nPORT = os.environ["PORT"]\nos.getenv("OLD_FLAG")\n')
    _commit("old read")

    result = runner.invoke(app, ["check", "--since", "HEAD", "--sarif"])

    assert result.exit_code == 0
    assert _json(result)["runs"][0]["results"] == []


@pytest.mark.parametrize(
    "args, message",
    [
        (["--sarif"], "only works with --since"),
        (["--since", "HEAD", "--sarif", "--json"], "not both"),
        (["--since", "HEAD", "--runtime"], "doesn't work with --runtime"),
    ],
)
def test_option_combinations_that_are_refused(project, args, message):
    result = runner.invoke(app, ["check", *args])

    assert result.exit_code == 2
    assert message in " ".join(result.output.split())


# --- check --code-only (CI, no .env) ----------------------------------------


@pytest.fixture
def ci_project(project):
    """CI: the repository as committed, no local .env."""
    os.remove(".env")
    yield


def test_plain_since_fails_without_a_local_file(ci_project):
    """Why --code-only exists: CI has no .env."""
    assert runner.invoke(app, ["check", "--since", "HEAD", "--json"]).exit_code == 1


def test_code_only_passes_a_clean_change_without_a_local_file(ci_project):
    result = runner.invoke(app, ["check", "--since", "HEAD", "--code-only", "--json"])

    assert result.exit_code == 0, result.stdout
    report = _report(_json(result))
    assert report["values"] == "not_checked"
    assert report["sources"] == []
    # No value was read, so no declared variable gets a verdict.
    assert report["variables"] == []


def test_code_only_fails_on_a_new_read_only(ci_project):
    _write("app.py", 'import os\nPORT = os.environ["PORT"]\nos.getenv("OLD_FLAG")\n')
    _commit("old read")
    _write("new.py", 'import os\nos.getenv("NEW_FLAG")\n')

    result = runner.invoke(app, ["check", "--since", "HEAD", "--code-only", "--json"])

    assert result.exit_code == 1
    report = _report(_json(result))
    assert report["since"]["new_undeclared"] == ["NEW_FLAG"]
    assert report["since"]["undeclared_before"] == ["OLD_FLAG"]
    assert {v["name"] for v in report["variables"]} == {"NEW_FLAG", "OLD_FLAG"}

    os.remove("new.py")
    assert (
        runner.invoke(app, ["check", "--since", "HEAD", "--code-only"]).exit_code == 0
    )


def test_code_only_reports_contract_changes(ci_project):
    _write("env.schema.toml", '[PORT]\ndescription = "p"\n\n[NEW]\nrequired = false\n')

    result = runner.invoke(app, ["check", "--since", "HEAD", "--code-only", "--json"])

    assert result.exit_code == 0
    kinds = [f["kind"] for f in _report(_json(result))["findings"]]
    assert kinds == ["contract_change"]


def test_code_only_without_since_fails_on_every_undeclared_read(ci_project):
    _write("app.py", 'import os\nPORT = os.environ["PORT"]\nos.getenv("OLD_FLAG")\n')
    _commit("old read")

    result = runner.invoke(app, ["check", "--code-only"])

    assert result.exit_code == 1
    assert "1 undeclared read" in result.stdout
    assert "--code-only" in result.stdout


def test_code_only_with_sarif_works_without_a_local_file(ci_project):
    _write("new.py", 'import os\nos.getenv("NEW_FLAG")\n')

    result = runner.invoke(app, ["check", "--since", "HEAD", "--code-only", "--sarif"])

    assert result.exit_code == 1
    results = _json(result)["runs"][0]["results"]
    assert [r["properties"]["variable"] for r in results] == ["NEW_FLAG"]


def test_code_only_reports_a_broken_contract(ci_project):
    _write("env.schema.toml", "[UNTERMINATED\n")

    result = runner.invoke(app, ["check", "--code-only", "--json"])

    assert result.exit_code == 1
    findings = _report(_json(result))["findings"]
    assert [f["reason_code"] for f in findings] == ["contract_error"]


@pytest.mark.parametrize(
    "args, flag",
    [
        ([".env"], "FILE"),
        (["--container", "web"], "--container"),
        (["--process-env"], "--process-env"),
        (["--runtime"], "--runtime"),
        (["--accept-unverified=host_forward"], "--accept-unverified"),
    ],
)
def test_code_only_refuses_value_options(project, args, flag):
    result = runner.invoke(app, ["check", "--code-only", *args])

    assert result.exit_code == 2
    assert f"doesn't work with {flag}" in " ".join(result.output.split())
