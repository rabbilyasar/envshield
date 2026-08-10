# envshield/tests/core/test_hook_injection.py
"""
Regression coverage for P0-4: envshield.yml's schema path, service name,
and example-file path are untrusted the moment they're interpolated into
a generated git hook script -- envshield.yml is explicitly designed to be
committed and PR-edited (see config_manager.UnsafePathError's own
docstring on this exact threat model), so a malicious repository must not
be able to turn that interpolation into shell command execution.

These tests exercise the actual boundary directly: generate the hook
script from an adversarial config value, write it to disk, and run it
through a real shell (`sh`) -- proving the injected command does NOT run,
rather than only asserting on the generated text. A harmless sentinel file
in a temporary directory stands in for "arbitrary command execution";
its absence after running the script is the proof.
"""

import os
import subprocess

from envshield.core import scanner
from envshield.core.exceptions import EnvShieldException


def _run_generated_script(script_content: str, tmp_path) -> subprocess.CompletedProcess:
    """Writes a generated hook script to disk and runs it via a real shell,
    outside any git repo -- the injected commands in every case below run
    unconditionally the moment the script is parsed (a POSIX `if list;
    then` tests the *last* command in `list`, but every earlier command in
    that same list still runs), so no repo setup is needed to prove the
    injection fires or is prevented."""
    script_path = tmp_path / "hook.sh"
    script_path.write_text(script_content)
    return subprocess.run(
        ["sh", str(script_path)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )


class TestShellInjectionIsPrevented:
    """Each test uses a distinct injection technique against a different
    interpolation point, so a fix that only closes one technique or one
    call site would still show up as a failure elsewhere in this class."""

    def test_malicious_schema_path_cannot_run_a_second_command(self, mocker, tmp_path):
        """The exact P0-4 report: a single-quote breakout in schema_path,
        interpolated into `grep -qF '<value>'`."""
        sentinel = tmp_path / "pwned_via_schema_path"
        malicious_schema = f"x'; touch {sentinel}; echo '"
        mocker.patch(
            "envshield.config.manager.load_config",
            return_value={"services": {"evil": {"schema": malicious_schema}}},
        )
        mocker.patch(
            "envshield.config.manager.get_env_paths",
            side_effect=EnvShieldException("no local file for this test"),
        )

        script = scanner._generate_pre_commit_hook_content()
        _run_generated_script(script, tmp_path)

        assert not sentinel.exists()

    def test_malicious_service_name_cannot_run_a_second_command(self, mocker, tmp_path):
        """Same technique, targeting the service *name* (a map key in
        envshield.yml) rather than the schema path -- a distinct
        interpolation point (`--service <value>`)."""
        sentinel = tmp_path / "pwned_via_service_name"
        malicious_name = f"api'; touch {sentinel}; echo '"
        mocker.patch(
            "envshield.config.manager.load_config",
            return_value={"services": {malicious_name: {"schema": "env.schema.toml"}}},
        )
        mocker.patch(
            "envshield.config.manager.get_env_paths",
            side_effect=EnvShieldException("no local file for this test"),
        )

        script = scanner._generate_pre_commit_hook_content()
        _run_generated_script(script, tmp_path)

        assert not sentinel.exists()

    def test_malicious_example_file_cannot_break_the_double_quoted_echo(
        self, mocker, tmp_path
    ):
        """
        example_file appears twice: once in a single-quoted grep argument
        (same technique as schema_path above) and once inside a
        double-quoted echo message -- a double-quote or backtick breakout
        there is a *different* technique from the single-quote breakout,
        and must be closed independently of it.
        """
        sentinel = tmp_path / "pwned_via_example_file"
        malicious_example = f'x" ; touch {sentinel} ; echo "'
        mocker.patch(
            "envshield.config.manager.load_config",
            return_value={"services": {"app": {"schema": "env.schema.toml"}}},
        )
        mocker.patch(
            "envshield.config.manager.get_env_paths",
            return_value={
                "local_file": ".env",
                "example_file": malicious_example,
            },
        )

        script = scanner._generate_pre_commit_hook_content()
        _run_generated_script(script, tmp_path)

        assert not sentinel.exists()

    def test_malicious_example_file_backtick_substitution_cannot_run(
        self, mocker, tmp_path
    ):
        """Backticks are interpreted *inside* a double-quoted shell string
        with no quote-breakout needed at all -- a distinct technique from
        the previous test's quote breakout."""
        sentinel = tmp_path / "pwned_via_backtick"
        malicious_example = f"`touch {sentinel}`"
        mocker.patch(
            "envshield.config.manager.load_config",
            return_value={"services": {"app": {"schema": "env.schema.toml"}}},
        )
        mocker.patch(
            "envshield.config.manager.get_env_paths",
            return_value={
                "local_file": ".env",
                "example_file": malicious_example,
            },
        )

        script = scanner._generate_pre_commit_hook_content()
        _run_generated_script(script, tmp_path)

        assert not sentinel.exists()

    def test_malicious_dollar_paren_substitution_cannot_run(self, mocker, tmp_path):
        """$(...) command substitution -- also interpreted inside double
        quotes, and inside an unquoted context, without any quote
        breakout."""
        sentinel = tmp_path / "pwned_via_dollar_paren"
        malicious_schema = f"$(touch {sentinel})"
        mocker.patch(
            "envshield.config.manager.load_config",
            return_value={"services": {"app": {"schema": malicious_schema}}},
        )
        mocker.patch(
            "envshield.config.manager.get_env_paths",
            side_effect=EnvShieldException("no local file for this test"),
        )

        script = scanner._generate_pre_commit_hook_content()
        _run_generated_script(script, tmp_path)

        assert not sentinel.exists()

    def test_value_containing_a_literal_newline_does_not_break_script_structure(
        self, mocker, tmp_path
    ):
        """
        A YAML scalar can contain an embedded newline (block scalars).
        shlex.quote() wraps such a value in single quotes with the
        newline preserved literally inside them -- still one shell token,
        not a script-structure break -- confirmed by running the script
        rather than just inspecting its text.
        """
        sentinel = tmp_path / "pwned_via_newline"
        malicious_schema = f"a\ntouch {sentinel}\nb"
        mocker.patch(
            "envshield.config.manager.load_config",
            return_value={"services": {"app": {"schema": malicious_schema}}},
        )
        mocker.patch(
            "envshield.config.manager.get_env_paths",
            side_effect=EnvShieldException("no local file for this test"),
        )

        script = scanner._generate_pre_commit_hook_content()
        result = _run_generated_script(script, tmp_path)

        assert not sentinel.exists()
        assert result.returncode == 0

    def test_post_merge_hook_malicious_schema_path_cannot_run_a_second_command(
        self, mocker, tmp_path
    ):
        """Same injection class, the post-merge generator's own
        interpolation points (a separate function, separate f-strings)."""
        sentinel = tmp_path / "pwned_via_post_merge"
        malicious_schema = f"x'; touch {sentinel}; echo '"
        mocker.patch(
            "envshield.config.manager.load_config",
            return_value={"services": {"evil": {"schema": malicious_schema}}},
        )

        script = scanner._generate_post_merge_hook_content()
        _run_generated_script(script, tmp_path)

        assert not sentinel.exists()

    def test_post_merge_hook_malicious_service_name_cannot_run_a_second_command(
        self, mocker, tmp_path
    ):
        sentinel = tmp_path / "pwned_via_post_merge_name"
        malicious_name = f"api'; touch {sentinel}; echo '"
        mocker.patch(
            "envshield.config.manager.load_config",
            return_value={"services": {malicious_name: {"schema": "env.schema.toml"}}},
        )

        script = scanner._generate_post_merge_hook_content()
        _run_generated_script(script, tmp_path)

        assert not sentinel.exists()


class TestLegitimateHookExecutionStillWorks:
    """Normal, non-malicious values must still produce a script that does
    exactly what it did before -- the fix must not change behavior for the
    overwhelming common case, only close the injection for the adversarial
    one."""

    def test_normal_values_produce_a_runnable_script_with_expected_exit_code(
        self, mocker, tmp_path
    ):
        mocker.patch(
            "envshield.config.manager.load_config",
            return_value={"services": {"app": {"schema": "env.schema.toml"}}},
        )
        mocker.patch(
            "envshield.config.manager.get_env_paths",
            return_value={"local_file": ".env", "example_file": ".env.example"},
        )

        script = scanner._generate_pre_commit_hook_content()
        result = _run_generated_script(script, tmp_path)

        # Outside a git repo, 'git diff'/'envshield scan' fail harmlessly --
        # the point here is that the script is syntactically well-formed
        # and runs to completion (no injected command, no shell parse
        # error), not that it reports a particular scan result.
        assert result.returncode in (0, 1)
        assert "syntax error" not in result.stderr.lower()

    def test_normal_values_still_produce_the_correct_end_to_end_hook_behavior(
        self, tmp_path, monkeypatch
    ):
        """
        End-to-end sanity check with a real git repo and a real registered
        service, run through the actual 'hook install' + a real commit --
        proves the fix didn't just avoid injection by breaking the feature
        it's protecting.
        """
        monkeypatch.chdir(tmp_path)
        os.system("git init -q")
        os.system('git config user.email "test@example.com"')
        os.system('git config user.name "Test"')

        from envshield.config import manager as config_manager

        config_manager.add_service("app", "env.schema.toml")
        with open("env.schema.toml", "w") as f:
            f.write('[API_KEY]\ndescription = "x"\n')
        with open(".env", "w") as f:
            f.write("API_KEY=abc123\n")
        with open(".env.example", "w") as f:
            f.write("API_KEY=\n")

        scanner.install_pre_commit_hook(non_interactive=True)

        os.system("git add env.schema.toml .env .env.example")
        result = subprocess.run(
            ["git", "commit", "-m", "test commit"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, result.stdout + result.stderr
