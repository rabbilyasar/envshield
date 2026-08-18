# envshield/tests/core/test_pre_commit_hook.py
import re
import subprocess

from envshield.core import scanner


def _extract_if_blocks(hook_script: str) -> list[tuple[str, str, str]]:
    """
    Returns [(schema_path, service_name, block_body), ...] for every
    top-level per-service 'if grep ...; then ...; fi' block.

    schema_path/service_name are read from the _ENVSHIELD_SCHEMA_PATH /
    _ENVSHIELD_SERVICE_NAME shell-variable assignments that now precede
    the grep/--service usage (see P0-4: shlex.quote()'d at generation
    time, referenced afterward only as "$VAR", never re-embedded as
    literal text) -- a plain capture is enough here because every fixture
    value these tests use is already shell-safe, so shlex.quote() leaves
    it unquoted. Adversarial values that DO need quoting are exercised
    separately in TestShellInjectionIsPrevented, by actually running the
    generated script through a real shell.

    Matches on an unindented '^fi$' line to find the OUTER closing 'fi'
    specifically -- a block's body can itself contain a nested, indented
    '  fi' (the unstaged-template check), which a naive '.*?fi' match
    would stop at instead.
    """
    blocks = re.findall(
        r"_ENVSHIELD_SCHEMA_PATH=(\S+)\n"
        r"_ENVSHIELD_SERVICE_NAME=(\S+)\n"
        r'if git diff --cached --name-only \| grep -qxF "\$_ENVSHIELD_SCHEMA_PATH"; then\n'
        r"(.*?)\n^fi$",
        hook_script,
        re.DOTALL | re.MULTILINE,
    )
    assert blocks, f"no per-service if-blocks found in generated hook:\n{hook_script}"
    return blocks


def test_pre_commit_hook_always_scans_staged_files():
    script = scanner._generate_pre_commit_hook_content()

    assert "envshield scan --staged" in script


def test_pre_commit_hook_is_scan_only_when_no_services_registered(mocker):
    mocker.patch("envshield.config.manager.load_config", return_value={})

    script = scanner._generate_pre_commit_hook_content()

    assert "grep" not in script
    assert "envshield scan --staged" in script


def test_pre_commit_hook_grep_detects_its_own_services_schema_change(mocker):
    """Each service's grep must match its own staged schema path."""
    mocker.patch(
        "envshield.config.manager.load_config",
        return_value={
            "services": {
                "api": {"schema": "services/api/env.schema.toml"},
                "web": {"schema": "services/web/env.schema.toml"},
            }
        },
    )

    script = scanner._generate_pre_commit_hook_content()
    blocks = _extract_if_blocks(script)

    for pattern, _name, _body in blocks:
        result = subprocess.run(f"echo '{pattern}' | grep -qxF '{pattern}'", shell=True)
        assert result.returncode == 0, f"expected {pattern} to match itself"

    result = subprocess.run(
        "echo 'unrelated/file.py' | grep -qxF 'services/api/env.schema.toml'",
        shell=True,
    )
    assert result.returncode == 1


def test_pre_commit_hook_only_checks_the_service_whose_schema_was_actually_staged(
    mocker,
):
    """
    Real bug this reproduces: staging only 'web's schema change still ran
    'api's template-sync check too (and failed the commit over it), because
    a single shared 'if' gated every service's check on whether ANY schema
    changed, not just that service's own. Each service's sync check must
    live inside its OWN 'if', gated on its OWN schema path only.

    The sync command text itself is now generic (`--service
    "$_ENVSHIELD_SERVICE_NAME"`, see P0-4) rather than the literal service
    name, so "did the wrong service's name leak into this block" is
    checked via the shell variable each block actually binds, not via a
    substring of the command text.
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

    script = scanner._generate_pre_commit_hook_content()
    blocks = {path: (name, body) for path, name, body in _extract_if_blocks(script)}

    api_pattern = "services/api/env.schema.toml"
    web_pattern = "services/web/env.schema.toml"
    api_name, api_body = blocks[api_pattern]
    web_name, web_body = blocks[web_pattern]

    assert api_name == "api"
    assert web_name == "web"
    assert (
        'envshield schema sync --service "$_ENVSHIELD_SERVICE_NAME" --check' in api_body
    )
    assert (
        'envshield schema sync --service "$_ENVSHIELD_SERVICE_NAME" --check' in web_body
    )


def test_pre_commit_hook_runs_a_sync_check_per_service_and_blocks_on_failure(mocker):
    mocker.patch(
        "envshield.config.manager.load_config",
        return_value={
            "services": {
                "api": {"schema": "services/api/env.schema.toml"},
                "web": {"schema": "services/web/env.schema.toml"},
            }
        },
    )

    script = scanner._generate_pre_commit_hook_content()
    blocks = _extract_if_blocks(script)

    assert {name for _path, name, _body in blocks} == {"api", "web"}
    for _path, _name, body in blocks:
        assert (
            'envshield schema sync --service "$_ENVSHIELD_SERVICE_NAME" --check' in body
        )
    # A failing sync check must actually fail the commit, not just print.
    assert "|| STATUS=1" in script
    assert "exit $STATUS" in script


def test_pre_commit_hook_flags_a_template_with_unstaged_changes_per_service(mocker):
    """
    'schema sync --check' below only ever reads '.env.example' off disk,
    not what's actually staged -- syncing the template (updating disk) and
    forgetting to 'git add' it would otherwise pass that check while the
    commit itself still lands with the stale, pre-sync template. Each
    service's own unstaged-diff check must reference only its own
    template file, scoped inside its own block.
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
    mocker.patch(
        "envshield.config.manager.get_env_paths",
        side_effect=lambda service_name: {
            "local_file": f"services/{service_name}/.env",
            "example_file": f"services/{service_name}/.env.example",
        },
    )

    script = scanner._generate_pre_commit_hook_content()
    blocks = {path: body for path, _name, body in _extract_if_blocks(script)}

    api_block = blocks["services/api/env.schema.toml"]
    web_block = blocks["services/web/env.schema.toml"]
    assert "services/api/.env.example" in api_block
    assert "services/web/.env.example" in web_block
    # Neither service's template check leaked into the other's block.
    assert "services/web/.env.example" not in api_block
    assert "services/api/.env.example" not in web_block

    result = subprocess.run(
        "echo 'services/api/.env.example' | grep -qxF 'services/api/.env.example'",
        shell=True,
    )
    assert result.returncode == 0


def test_pre_commit_hook_skips_unstaged_template_check_for_a_python_target(mocker):
    """A Python-module local_file has no separate template file to check (see _check_example_file_sync)."""
    mocker.patch(
        "envshield.config.manager.load_config",
        return_value={"services": {"app": {"schema": "env.schema.toml"}}},
    )
    mocker.patch(
        "envshield.config.manager.get_env_paths",
        return_value={
            "local_file": "config/env_config.local.py",
            "example_file": "config/env_config.local.py",
        },
    )

    script = scanner._generate_pre_commit_hook_content()
    blocks = _extract_if_blocks(script)

    assert len(blocks) == 1
    _path, name, body = blocks[0]
    assert name == "app"
    assert "unstaged changes" not in body
    assert 'envshield schema sync --service "$_ENVSHIELD_SERVICE_NAME" --check' in body
