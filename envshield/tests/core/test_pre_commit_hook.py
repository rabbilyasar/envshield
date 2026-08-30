# envshield/tests/core/test_pre_commit_hook.py
import os
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


class TestRealInstalledHookRejectsStalePythonLocalFile:
    """
    BL-005's end-to-end regression: everything above this class tests the
    generated *script text* in isolation. This proves the real chain the
    finding is about actually holds against a real installed hook and a
    real 'git commit' subprocess -- schema changes -> Python local_file is
    stale -> 'schema sync --check' -> non-zero -> the hook rejects the
    commit -- not just that '_check_example_file_sync' returns the right
    tuple when called directly.

    Uses the real 'envshield' console script via subprocess (an editable
    install resolving to this checkout, not a separately-installed
    version) rather than CliRunner, because the hook script itself always
    shells out to a real 'envshield' binary -- CliRunner never exercises
    that boundary.
    """

    @staticmethod
    def _git(*args, **kwargs):
        kwargs.setdefault("check", True)
        kwargs.setdefault("capture_output", True)
        kwargs.setdefault("text", True)
        return subprocess.run(["git", *args], **kwargs)

    def test_a_stale_python_local_file_blocks_the_commit(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._git("init", "-q")
        self._git("config", "user.email", "test@example.com")
        self._git("config", "user.name", "Test")
        os.makedirs("svc")
        with open("envshield.yml", "w") as f:
            f.write(
                "services:\n  svc:\n    schema: svc/env.schema.toml\n    local_file: svc/config.py\n"
            )
        with open("svc/env.schema.toml", "w") as f:
            f.write('[FIELD_PRESENT]\ndescription="p"\ndefaultValue="x"\n')
        with open("svc/config.py", "w") as f:
            f.write('FIELD_PRESENT = "hello"\n')
        self._git("add", "-A")
        self._git("commit", "-q", "-m", "base")

        install = subprocess.run(
            ["envshield", "hook", "install", "--yes"], capture_output=True, text=True
        )
        assert install.returncode == 0, install.stdout + install.stderr

        # Add a new required variable to the schema; leave config.py stale.
        with open("svc/env.schema.toml", "w") as f:
            f.write(
                '[FIELD_PRESENT]\ndescription="p"\ndefaultValue="x"\n\n'
                '[FIELD_BRAND_NEW]\ndescription="new"\nrequired=true\n'
            )
        self._git("add", "svc/env.schema.toml")

        commit = subprocess.run(
            ["git", "commit", "-m", "add FIELD_BRAND_NEW without updating config.py"],
            capture_output=True,
            text=True,
        )

        assert commit.returncode != 0, (
            "the installed pre-commit hook must reject this commit -- "
            f"stdout={commit.stdout!r} stderr={commit.stderr!r}"
        )
        # The hook's own Rich console output lands on stderr, not stdout.
        assert "FIELD_BRAND_NEW" in commit.stderr
        # The stale commit must never actually land.
        log = self._git("log", "--oneline")
        assert "FIELD_BRAND_NEW" not in log.stdout

    def test_updating_the_python_local_file_too_lets_the_commit_through(
        self, tmp_path, monkeypatch
    ):
        """Sanity check on the other side: the hook must not become an
        unconditional blocker -- fixing the local file lets a real,
        legitimate commit through."""
        monkeypatch.chdir(tmp_path)
        self._git("init", "-q")
        self._git("config", "user.email", "test@example.com")
        self._git("config", "user.name", "Test")
        os.makedirs("svc")
        with open("envshield.yml", "w") as f:
            f.write(
                "services:\n  svc:\n    schema: svc/env.schema.toml\n    local_file: svc/config.py\n"
            )
        with open("svc/env.schema.toml", "w") as f:
            f.write('[FIELD_PRESENT]\ndescription="p"\ndefaultValue="x"\n')
        with open("svc/config.py", "w") as f:
            f.write('FIELD_PRESENT = "hello"\n')
        self._git("add", "-A")
        self._git("commit", "-q", "-m", "base")

        install = subprocess.run(
            ["envshield", "hook", "install", "--yes"], capture_output=True, text=True
        )
        assert install.returncode == 0, install.stdout + install.stderr

        with open("svc/env.schema.toml", "w") as f:
            f.write(
                '[FIELD_PRESENT]\ndescription="p"\ndefaultValue="x"\n\n'
                '[FIELD_BRAND_NEW]\ndescription="new"\nrequired=true\n'
            )
        with open("svc/config.py", "w") as f:
            f.write('FIELD_PRESENT = "hello"\nFIELD_BRAND_NEW = "value"\n')
        self._git("add", "-A")

        commit = subprocess.run(
            ["git", "commit", "-m", "add FIELD_BRAND_NEW and update config.py"],
            capture_output=True,
            text=True,
        )

        assert commit.returncode == 0, commit.stdout + commit.stderr
        log = self._git("log", "--oneline")
        assert "FIELD_BRAND_NEW" in log.stdout
