# envshield/tests/test_check_golden.py
"""
Golden tests: 'check' must keep producing exactly what the pre-evaluator
implementation produced -- exit code, every '--json' key that existed then
(all but 'reports'), and the Rich output -- for every
scenario in check_golden_scenarios.py. See that module for regeneration.
"""

import json
import os
import subprocess
import sys

import pytest
from typer.testing import CliRunner

from envshield.cli import app
from envshield.tests.check_golden_scenarios import SCENARIOS

runner = CliRunner()
GOLDEN_PATH = os.path.join(os.path.dirname(__file__), "fixtures", "check_golden.json")
# 'reports' is the evaluator's addition (evaluator-decisions.md D-5);
# every other key is the pre-evaluator contract.
NEW_JSON_KEYS = ("reports",)


def _materialize(files):
    subprocess.run(["git", "init", "-q"], check=True)
    for path, content in files.items():
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(path, "w") as f:
            f.write(content)


def run_scenario(name, tmp_path):
    scenario = SCENARIOS[name]
    args = scenario.get("args", [])
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _materialize(scenario["files"])
        env = {"COLUMNS": "200"}
        as_json = runner.invoke(app, ["check", *args, "--json"], env=env)
        as_text = runner.invoke(app, ["check", *args], env=env)
    payload = json.loads(as_json.stdout)
    return {
        "json_exit": as_json.exit_code,
        "json": {k: v for k, v in payload.items() if k not in NEW_JSON_KEYS},
        "text_exit": as_text.exit_code,
        "text": as_text.stdout,
    }


def _golden():
    with open(GOLDEN_PATH) as f:
        return json.load(f)


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_check_matches_golden(name, tmp_path):
    expected = _golden()[name]
    actual = run_scenario(name, tmp_path)

    assert actual["json_exit"] == expected["json_exit"]
    assert actual["json"] == expected["json"]
    assert actual["text_exit"] == expected["text_exit"]
    assert actual["text"] == expected["text"]


def test_every_scenario_has_a_golden_record():
    assert set(_golden()) == set(SCENARIOS)


if __name__ == "__main__" and "--regenerate" in sys.argv:
    import tempfile

    import envshield.tests.conftest  # noqa: F401 -- adds isolated_filesystem

    golden = {}
    for scenario_name in sorted(SCENARIOS):
        with tempfile.TemporaryDirectory() as tmp:
            golden[scenario_name] = run_scenario(scenario_name, tmp)
    os.makedirs(os.path.dirname(GOLDEN_PATH), exist_ok=True)
    with open(GOLDEN_PATH, "w") as f:
        json.dump(golden, f, indent=2, sort_keys=True)
        f.write("\n")
    print(f"wrote {len(golden)} scenarios to {GOLDEN_PATH}")
