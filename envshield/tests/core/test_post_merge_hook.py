# envshield/tests/core/test_post_merge_hook.py
import subprocess

from envshield.core import hooks_manager, scanner


def _git(*args):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], check=True
    )


def _write(path, content):
    import os

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


def _merge_changing(paths, config, tmp_path, monkeypatch):
    """Two commits, so HEAD@{1}..HEAD is exactly the second one."""
    monkeypatch.chdir(tmp_path)
    _git("init", "-q")
    _write("envshield.yml", config)
    for path in paths:
        _write(path, "[A]\n")
    _write("services/api/env.schema.toml", "[A]\n")
    _write("services/web/env.schema.toml", "[A]\n")
    _git("add", "-A")
    _git("commit", "-qm", "one")
    for path in paths:
        _write(path, "[A]\n[B]\n")
    _git("commit", "-qam", "two")


TWO_SERVICES = (
    "services:\n"
    "  api:\n    schema: services/api/env.schema.toml\n"
    "  web:\n    schema: services/web/env.schema.toml\n"
)


def _record(mocker):
    calls = []
    mocker.patch.object(
        hooks_manager, "_envshield", side_effect=lambda *a, **k: calls.append(a) or 0
    )
    return calls


def test_post_merge_hook_runs_doctor_through_the_runner():
    assert (
        "envshield hook run post-merge" in scanner._generate_post_merge_hook_content()
    )


def test_post_merge_hook_calls_doctor_for_every_changed_service(
    tmp_path, monkeypatch, mocker
):
    _merge_changing(
        ["services/api/env.schema.toml", "services/web/env.schema.toml"],
        TWO_SERVICES,
        tmp_path,
        monkeypatch,
    )
    calls = _record(mocker)

    assert hooks_manager.run_post_merge() == 0
    assert calls == [("doctor", "--service", "api"), ("doctor", "--service", "web")]


def test_post_merge_hook_only_checks_the_service_whose_schema_actually_changed(
    tmp_path, monkeypatch, mocker
):
    """
    Real bug this reproduces: merging a branch that only changed 'api's
    schema still ran 'envshield doctor --service web' too.
    """
    _merge_changing(
        ["services/api/env.schema.toml"], TWO_SERVICES, tmp_path, monkeypatch
    )
    calls = _record(mocker)

    hooks_manager.run_post_merge()

    assert calls == [("doctor", "--service", "api")]


def test_post_merge_hook_is_a_clean_noop_before_any_service_is_registered(
    tmp_path, monkeypatch, mocker
):
    """Hooks can be installed standalone, before 'init'/'service add' ever registers anything."""
    _merge_changing(["README.md"], "", tmp_path, monkeypatch)
    calls = _record(mocker)

    assert hooks_manager.run_post_merge() == 0
    assert calls == []
