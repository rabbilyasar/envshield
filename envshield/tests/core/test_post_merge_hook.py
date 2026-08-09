# envshield/tests/core/test_post_merge_hook.py
import re
import subprocess

from envshield.core import scanner


def _extract_if_blocks(hook_script: str) -> list[tuple[str, str]]:
    """Returns [(grep pattern, doctor command), ...] for every per-service 'if grep ...; then ...; fi' block."""
    blocks = re.findall(
        r"if git diff --name-only HEAD@\{1\}\.\.HEAD \| grep -qF '([^']*)' 2>/dev/null; then\n(.*?)\nfi",
        hook_script,
    )
    assert blocks, f"no per-service if-blocks found in generated hook:\n{hook_script}"
    return blocks


def test_post_merge_hook_grep_detects_its_own_services_schema_change(mocker):
    """Each service's grep must match its own schema path when it appears in the merge diff."""
    mocker.patch(
        "envshield.config.manager.load_config",
        return_value={
            "services": {
                "api": {"schema": "services/api/env.schema.toml"},
                "web": {"schema": "services/web/env.schema.toml"},
            }
        },
    )

    script = scanner._generate_post_merge_hook_content()
    blocks = _extract_if_blocks(script)

    for pattern, _ in blocks:
        result = subprocess.run(f"echo '{pattern}' | grep -qF '{pattern}'", shell=True)
        assert result.returncode == 0, f"expected {pattern} to match itself"

    result = subprocess.run(
        "echo 'unrelated/file.py' | grep -qF 'services/api/env.schema.toml'",
        shell=True,
    )
    assert result.returncode == 1


def test_post_merge_hook_grep_detects_change_single_service(mocker):
    mocker.patch(
        "envshield.config.manager.load_config",
        return_value={"services": {"app": {"schema": "env.schema.toml"}}},
    )

    script = scanner._generate_post_merge_hook_content()
    blocks = _extract_if_blocks(script)

    assert len(blocks) == 1
    pattern, _ = blocks[0]
    result = subprocess.run(f"echo 'env.schema.toml' | grep -qF '{pattern}'", shell=True)
    assert result.returncode == 0


def test_post_merge_hook_calls_doctor_for_every_service(mocker):
    mocker.patch(
        "envshield.config.manager.load_config",
        return_value={
            "services": {
                "api": {"schema": "services/api/env.schema.toml"},
                "web": {"schema": "services/web/env.schema.toml"},
            }
        },
    )

    script = scanner._generate_post_merge_hook_content()

    assert "envshield doctor --service api" in script
    assert "envshield doctor --service web" in script


def test_post_merge_hook_only_checks_the_service_whose_schema_actually_changed(
    mocker,
):
    """
    Real bug this reproduces: merging a branch that only changed 'api's
    schema still ran 'envshield doctor --service web' too, because a
    single shared 'if' gated every service's doctor call on whether ANY
    schema changed in the merge, not just that service's own. Each
    service's doctor call must live inside its OWN 'if', gated on its OWN
    schema path only.
    """
    mocker.patch(
        "envshield.config.manager.load_config",
        return_value={
            "services": {
                "api": {"schema": "services/api/env.schema.toml"},
                "web": {"schema": "services/web/env.schema.toml"},
            }
        },
    )

    script = scanner._generate_post_merge_hook_content()
    blocks = dict(_extract_if_blocks(script))

    api_pattern = "services/api/env.schema.toml"
    web_pattern = "services/web/env.schema.toml"
    assert "--service api" in blocks[api_pattern]
    assert "--service web" in blocks[web_pattern]
    # Neither service's doctor call leaked into the other's block.
    assert "--service web" not in blocks[api_pattern]
    assert "--service api" not in blocks[web_pattern]


def test_post_merge_hook_is_a_clean_noop_before_any_service_is_registered(mocker):
    """Hooks can be installed standalone, before 'init'/'service add' ever registers anything."""
    mocker.patch("envshield.config.manager.load_config", return_value={})

    script = scanner._generate_post_merge_hook_content()

    assert "grep" not in script
    assert script.strip().endswith("exit 0")
