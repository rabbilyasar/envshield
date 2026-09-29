# envshield/tests/core/test_pre_commit_hook.py
import os
import subprocess

from envshield.core import hooks_manager, scanner


def _record_subcommands(mocker):
    calls = []
    mocker.patch.object(
        hooks_manager, "_envshield", side_effect=lambda *a, **k: calls.append(a) or 0
    )
    return calls


def _repo_with(files, config):
    subprocess.run(["git", "init", "-q"], check=True)
    for path, content in {"envshield.yml": config, **files}.items():
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            f.write(content)


TWO_SERVICES = (
    "services:\n"
    "  api:\n    schema: services/api/env.schema.toml\n"
    "  web:\n    schema: services/web/env.schema.toml\n"
)


def test_pre_commit_hook_always_scans_staged_files(tmp_path, monkeypatch, mocker):
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "-q"], check=True)
    calls = _record_subcommands(mocker)

    hooks_manager.run_pre_commit()

    assert calls[0] == ("scan", "--staged", "--enforce")
    assert (
        "envshield hook run pre-commit" in scanner._generate_pre_commit_hook_content()
    )


def test_pre_commit_hook_only_checks_the_service_whose_schema_was_actually_staged(
    tmp_path, monkeypatch, mocker
):
    """
    Real bug this reproduces: staging only 'web's schema change still ran
    'api's template-sync check too (and failed the commit over it). Only
    the service whose own schema was staged is checked.
    """
    monkeypatch.chdir(tmp_path)
    _repo_with(
        {
            "services/api/env.schema.toml": "[A]\n",
            "services/web/env.schema.toml": "[B]\n",
        },
        TWO_SERVICES,
    )
    subprocess.run(["git", "add", "-A"], check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base"],
        check=True,
    )
    with open("services/web/env.schema.toml", "w") as f:
        f.write("[B]\n[C]\n")
    subprocess.run(["git", "add", "services/web/env.schema.toml"], check=True)
    calls = _record_subcommands(mocker)

    hooks_manager.run_pre_commit()

    assert calls[1:] == [("schema", "sync", "--service", "web", "--check")]


def test_pre_commit_hook_runs_a_sync_check_per_service_and_blocks_on_failure(
    tmp_path, monkeypatch, mocker
):
    monkeypatch.chdir(tmp_path)
    _repo_with(
        {
            "services/api/env.schema.toml": "[A]\n",
            "services/web/env.schema.toml": "[B]\n",
        },
        TWO_SERVICES,
    )
    subprocess.run(["git", "add", "-A"], check=True)
    mocker.patch.object(
        hooks_manager,
        "_envshield",
        side_effect=lambda *a, **k: 1 if a[:2] == ("schema", "sync") else 0,
    )

    assert hooks_manager.run_pre_commit() == 1


def test_pre_commit_hook_skips_unstaged_template_check_for_a_python_target(
    tmp_path, monkeypatch, mocker, capsys
):
    """A Python-module local_file has no separate template file to check (see _check_example_file_sync)."""
    monkeypatch.chdir(tmp_path)
    _repo_with(
        {"env.schema.toml": "[A]\n", "config/env_config.local.py": "A = 1\n"},
        "services:\n  app:\n    schema: env.schema.toml\n"
        "    local_file: config/env_config.local.py\n"
        "    example_file: config/env_config.local.py\n",
    )
    subprocess.run(["git", "add", "-A"], check=True)
    with open("config/env_config.local.py", "w") as f:
        f.write("A = 2\n")  # unstaged, but it's the local file, not a template
    calls = _record_subcommands(mocker)

    assert hooks_manager.run_pre_commit() == 0
    assert "unstaged changes" not in capsys.readouterr().out
    assert calls[1:] == [("schema", "sync", "--service", "app", "--check")]


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
