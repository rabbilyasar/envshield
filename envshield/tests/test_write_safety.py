# envshield/tests/test_write_safety.py
"""
BL-154: no EnvShield command may write through a repository-controlled
symlink, or outside the project.

Every case runs the real CLI in a project next to an "outside" directory,
snapshots every byte under "outside" first, and asserts afterwards that it
is unchanged and never received the planted secret. Covers each writer:
setup (dotenv and Python local file), schema sync (.env.example, and the
Python local file it creates or patches), generate, import, init
(schema, envshield.yml, .gitignore), service add (envshield.yml), and
hook install.
"""

import os
import subprocess

import pytest
from typer.testing import CliRunner

from envshield.cli import app
from envshield.core import file_updater
from envshield.core.exceptions import UnsafeWriteTargetError

runner = CliRunner()
SECRET = "SECRET_DO_NOT_LEAK_7c1e"
ONE_SERVICE = "services:\n  app:\n    schema: env.schema.toml\n"
SCHEMA = '[API_KEY]\nsecret = true\n\n[PORT]\ndefaultValue = "8000"\n'


def _snapshot(root):
    found = {}
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            path = os.path.join(dirpath, name)
            with open(path, "rb") as f:
                found[path] = f.read()
    return found


@pytest.fixture
def world(tmp_path, monkeypatch):
    """(project dir, outside dir); cwd is the project, a Git repository."""
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    (outside / "victim").write_text("ORIGINAL VICTIM CONTENT\n")
    monkeypatch.chdir(project)
    subprocess.run(["git", "init", "-q"], check=True)
    return project, outside


def _write(path, content):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


def _invoke(args, answers=None):
    # Wide enough that Rich never wraps an error message mid-assertion.
    return runner.invoke(app, args, input=answers, env={"COLUMNS": "400"})


def _assert_refused(result, outside, before):
    after = _snapshot(outside)
    assert after == before
    assert not any(SECRET.encode() in content for content in after.values())
    assert result.exit_code != 0, result.stdout
    # "Refusing to write": the write primitive (BL-154). "Refusing to use":
    # envshield.yml path containment, which rejects an override resolving
    # outside the project before anything is opened.
    assert "Refusing to write" in result.stdout or "Refusing to use" in result.stdout


# --- Each writer x an existing symlinked target pointing outside -----------


def _setup_dotenv(project, outside):
    _write("envshield.yml", ONE_SERVICE)
    _write("env.schema.toml", SCHEMA)
    _write(".env.example", f"API_KEY={SECRET}\nPORT=8000\n")
    os.symlink(outside / "victim", ".env")
    return ["setup"], f"{SECRET}\n"


def _setup_python_local_file(project, outside):
    _write(
        "envshield.yml",
        ONE_SERVICE + "    local_file: settings.py\n",
    )
    _write("env.schema.toml", SCHEMA)
    (outside / "victim").write_text("PORT = '8000'\n")
    os.symlink(outside / "victim", "settings.py")
    return ["setup"], f"{SECRET}\n"


def _sync_env_example(project, outside):
    _write("envshield.yml", ONE_SERVICE)
    _write("env.schema.toml", SCHEMA)
    os.symlink(outside / "victim", ".env.example")
    return ["schema", "sync"], None


def _sync_python_local_file_patch(project, outside):
    _write("envshield.yml", ONE_SERVICE + "    local_file: settings.py\n")
    _write("env.schema.toml", SCHEMA)
    (outside / "victim").write_text("API_KEY = 'x'\n")
    os.symlink(outside / "victim", "settings.py")
    return ["schema", "sync"], None


def _generate(project, outside):
    _write("env.schema.toml", SCHEMA)
    os.symlink(outside / "victim", "config.py")
    return ["generate", "config.py", "--lang", "python", "--force"], None


def _import(project, outside):
    _write("source.env", f"API_KEY={SECRET}\n")
    os.symlink(outside / "victim", "env.schema.toml")
    return ["import", "source.env", "--force"], None


def _init_gitignore(project, outside):
    _write(".env", f"API_KEY={SECRET}\n")
    os.symlink(outside / "victim", ".gitignore")
    return ["init"], "n\nn\n"


def _init_config(project, outside):
    # Dangling: nothing to read, so the write itself is what's tested.
    _write(".env", f"API_KEY={SECRET}\n")
    os.symlink(outside / "new-envshield.yml", "envshield.yml")
    return ["init"], "n\nn\n"


def _init_schema(project, outside):
    _write(".env", f"API_KEY={SECRET}\n")
    os.symlink(outside / "new-schema.toml", "env.schema.toml")
    return ["init"], "n\nn\n"


def _import_to_another_path(project, outside):
    _write("source.env", f"API_KEY={SECRET}\n")
    os.symlink(outside / "victim", "other.schema.toml")
    return ["import", "source.env", "--output", "other.schema.toml", "--force"], None


def _service_add(project, outside):
    _write("api/.env", f"API_KEY={SECRET}\n")
    (outside / "victim").write_text(ONE_SERVICE)
    _write("env.schema.toml", SCHEMA)
    os.symlink(outside / "victim", "envshield.yml")
    return ["service", "add", "api", "api"], None


def _hook_install(project, outside):
    hooks = os.path.join(".git", "hooks")
    os.makedirs(hooks, exist_ok=True)
    os.symlink(outside / "victim", os.path.join(hooks, "pre-commit"))
    return ["hook", "install", "--yes"], None


WRITERS = {
    "setup .env": _setup_dotenv,
    "setup python local_file": _setup_python_local_file,
    "schema sync .env.example": _sync_env_example,
    "schema sync python local_file": _sync_python_local_file_patch,
    "generate": _generate,
    "import --output": _import,
    "init .gitignore": _init_gitignore,
    "init envshield.yml": _init_config,
    "init env.schema.toml": _init_schema,
    "import --output (non-service path)": _import_to_another_path,
    "service add envshield.yml": _service_add,
}


@pytest.mark.parametrize("writer", sorted(WRITERS))
def test_writer_refuses_a_symlink_to_outside(world, writer):
    project, outside = world
    args, answers = WRITERS[writer](project, outside)
    before = _snapshot(outside)

    result = _invoke(args, answers)

    _assert_refused(result, outside, before)


def test_hook_install_refuses_a_symlinked_hook(world):
    """--yes on a foreign hook only warns; --force (doctor --fix) overwrites
    without asking, so the refusal has to come from the write itself."""
    project, outside = world
    _hook_install(project, outside)
    before = _snapshot(outside)

    with pytest.raises(UnsafeWriteTargetError):
        from envshield.core import hooks_manager

        hooks_manager.install_pre_commit_hook(force=True)

    assert _snapshot(outside) == before


# --- Symlinks inside the project are refused too ---------------------------


def test_setup_refuses_a_symlink_that_stays_inside_the_project(world):
    project, outside = world
    _write("envshield.yml", ONE_SERVICE)
    _write("env.schema.toml", SCHEMA)
    _write("real/.env.real", "PORT=8000\n")
    os.symlink(os.path.join("real", ".env.real"), ".env")

    result = _invoke(["setup"], f"{SECRET}\n")

    assert result.exit_code == 1
    assert "it is a symlink" in result.stdout
    with open("real/.env.real") as f:
        assert f.read() == "PORT=8000\n"


# --- Symlinked parent directories -----------------------------------------


def test_setup_refuses_a_symlinked_parent_directory_outside(world):
    project, outside = world
    _write("envshield.yml", ONE_SERVICE + "    local_file: config/.env\n")
    _write("env.schema.toml", SCHEMA)
    os.symlink(outside, "config")
    before = _snapshot(outside)

    result = _invoke(["setup"], f"{SECRET}\n")

    _assert_refused(result, outside, before)
    assert not (outside / ".env").exists()


def test_sync_refuses_a_symlinked_parent_directory_inside(world):
    """An in-project directory symlink passes the realpath containment
    check envshield.yml paths get, so only the write itself can refuse."""
    project, outside = world
    os.makedirs("real_config")
    os.symlink("real_config", "config")
    _write("envshield.yml", ONE_SERVICE + "    example_file: config/.env.example\n")
    _write("env.schema.toml", SCHEMA)

    result = _invoke(["schema", "sync"])

    assert result.exit_code == 1
    assert "'config' is a symlink or not a directory" in result.stdout
    assert os.listdir("real_config") == []


def test_sync_refuses_a_python_local_file_under_a_symlinked_parent(world):
    project, outside = world
    os.symlink(outside, "conf")
    _write("envshield.yml", ONE_SERVICE + "    local_file: conf/settings.py\n")
    _write("env.schema.toml", SCHEMA)
    before = _snapshot(outside)

    result = _invoke(["schema", "sync"])

    assert result.exit_code == 1
    assert _snapshot(outside) == before
    assert not (outside / "settings.py").exists()


# --- Directory and special-file targets -----------------------------------


def test_setup_refuses_a_directory_target(world):
    _write("envshield.yml", ONE_SERVICE)
    _write("env.schema.toml", SCHEMA)
    os.makedirs(".env")

    result = _invoke(["setup"], f"{SECRET}\n")

    assert result.exit_code == 1
    assert "it is a directory" in result.stdout
    assert os.path.isdir(".env")


def test_generate_refuses_a_fifo_without_blocking(world):
    _write("env.schema.toml", SCHEMA)
    os.mkfifo("config.py")

    result = _invoke(["generate", "config.py", "--lang", "python", "--force"])

    assert result.exit_code == 1
    assert "not a regular file" in result.stdout


def test_generate_refuses_an_explicit_path_outside_the_project(world):
    project, outside = world
    _write("env.schema.toml", SCHEMA)
    before = _snapshot(outside)

    result = _invoke(["generate", str(outside / "config.py"), "--lang", "python"])

    assert result.exit_code == 1
    assert "outside the project directory" in result.stdout
    assert _snapshot(outside) == before


# --- Legitimate writes keep working ----------------------------------------


def test_setup_creates_a_new_env_as_0600(world, mocker):
    _write("envshield.yml", ONE_SERVICE)
    _write("env.schema.toml", SCHEMA)
    # A hidden prompt reads /dev/tty when one exists, not CliRunner's input.
    prompt = mocker.patch(
        "envshield.core.setup_manager.Prompt.ask", return_value=SECRET
    )

    result = _invoke(["setup"])

    assert result.exit_code == 0, result.stdout
    assert prompt.call_args.kwargs["password"] is True
    with open(".env") as f:
        assert f"API_KEY={SECRET}" in f.read()
    assert os.stat(".env").st_mode & 0o777 == 0o600


def test_setup_overwrites_an_existing_regular_env(world, mocker):
    _write("envshield.yml", ONE_SERVICE)
    _write("env.schema.toml", SCHEMA)
    _write(".env", "PORT=9000\n")
    os.chmod(".env", 0o644)
    mocker.patch("questionary.confirm").return_value.ask.return_value = True
    # A hidden prompt reads /dev/tty when one exists, not CliRunner's input.
    prompt = mocker.patch(
        "envshield.core.setup_manager.Prompt.ask", return_value=SECRET
    )

    result = _invoke(["setup"])

    assert result.exit_code == 0, result.stdout
    assert prompt.call_args.kwargs["password"] is True
    with open(".env") as f:
        content = f.read()
    assert "PORT=9000" in content and f"API_KEY={SECRET}" in content
    assert os.stat(".env").st_mode & 0o777 == 0o600


def test_sync_creates_missing_parent_directories(world):
    _write(
        "envshield.yml",
        ONE_SERVICE + "    example_file: deploy/templates/.env.example\n",
    )
    _write("env.schema.toml", SCHEMA)

    result = _invoke(["schema", "sync"])

    assert result.exit_code == 0, result.stdout
    with open("deploy/templates/.env.example") as f:
        assert "PORT=8000" in f.read()


def test_setup_patches_a_regular_python_local_file(world, mocker):
    _write("envshield.yml", ONE_SERVICE + "    local_file: settings.py\n")
    _write("env.schema.toml", SCHEMA)
    _write("settings.py", "# keep me\nPORT = '8000'\n")
    # A hidden prompt reads /dev/tty when one exists, not CliRunner's input.
    prompt = mocker.patch(
        "envshield.core.setup_manager.Prompt.ask", return_value=SECRET
    )

    result = _invoke(["setup"])

    assert result.exit_code == 0, result.stdout
    assert prompt.call_args.kwargs["password"] is True
    with open("settings.py") as f:
        content = f.read()
    assert "# keep me" in content and SECRET in content
    assert os.stat("settings.py").st_mode & 0o777 == 0o600


def test_hook_install_writes_an_executable_regular_file(world):
    result = _invoke(["hook", "install", "--yes"])

    assert result.exit_code == 0, result.stdout
    path = os.path.join(".git", "hooks", "pre-commit")
    assert os.path.isfile(path) and not os.path.islink(path)
    assert os.stat(path).st_mode & 0o111


# --- The primitive itself ---------------------------------------------------


def test_assert_safe_write_target_changes_nothing(world):
    project, outside = world
    os.symlink(outside / "victim", "link")
    before = _snapshot(outside)

    with pytest.raises(UnsafeWriteTargetError):
        file_updater.assert_safe_write_target("link")
    file_updater.assert_safe_write_target("new/dir/file")  # missing parents: fine

    assert _snapshot(outside) == before
    assert not os.path.exists("new")


def test_update_variables_in_file_refuses_instead_of_silently_skipping(world):
    """A refusal raises, as any read or write failure now does."""
    project, outside = world
    (outside / "victim").write_text("API_KEY=old\n")
    os.symlink(outside / "victim", ".env")

    with pytest.raises(UnsafeWriteTargetError):
        file_updater.update_variables_in_file(
            ".env", [{"key": "API_KEY", "value": SECRET}], secret=True
        )

    assert (outside / "victim").read_text() == "API_KEY=old\n"


@pytest.mark.parametrize("command", [["setup"], ["schema", "sync"]])
def test_python_local_file_symlink_inside_the_project_is_refused(world, command):
    """Passes envshield.yml's realpath containment check (it stays inside),
    so only the write itself can refuse it -- in-place patching included."""
    _write("envshield.yml", ONE_SERVICE + "    local_file: settings.py\n")
    _write("env.schema.toml", SCHEMA)
    _write("real/settings_real.py", "# keep\nAPI_KEY = 'x'\n")
    os.symlink(os.path.join("real", "settings_real.py"), "settings.py")

    result = _invoke(command, f"{SECRET}\n")

    assert result.exit_code == 1, result.stdout
    assert "Refusing to write 'settings.py': it is a symlink" in result.stdout
    with open("real/settings_real.py") as f:
        assert f.read() == "# keep\nAPI_KEY = 'x'\n"


# --- Symlinked parent directories: generate, import --output, init ---------


def _out_dir_outside(project, outside):
    os.symlink(outside, "out")


def _out_dir_inside(project, outside):
    os.makedirs("real_out")
    os.symlink("real_out", "out")


@pytest.mark.parametrize("make_parent", [_out_dir_outside, _out_dir_inside])
def test_generate_refuses_a_symlinked_parent_directory(world, make_parent):
    project, outside = world
    _write("env.schema.toml", SCHEMA)
    make_parent(project, outside)
    before = _snapshot(outside)

    result = _invoke(["generate", "out/config.py", "--lang", "python"])

    assert result.exit_code == 1, result.stdout
    assert "'out' is a symlink or not a directory" in result.stdout
    assert _snapshot(outside) == before
    assert not os.path.exists(os.path.join("real_out", "config.py"))


@pytest.mark.parametrize("make_parent", [_out_dir_outside, _out_dir_inside])
def test_import_output_refuses_a_symlinked_parent_directory(world, make_parent):
    project, outside = world
    _write("source.env", f"API_KEY={SECRET}\nPORT=8000\n")
    make_parent(project, outside)
    before = _snapshot(outside)

    result = _invoke(["import", "source.env", "--output", "out/env.schema.toml"])

    assert result.exit_code == 1, result.stdout
    assert "'out' is a symlink or not a directory" in result.stdout
    assert _snapshot(outside) == before
    assert not os.path.exists(os.path.join("real_out", "env.schema.toml"))


def test_generate_and_import_write_through_a_regular_parent(world):
    _write("env.schema.toml", SCHEMA)
    _write("source.env", "PORT=8000\n")
    os.makedirs("out")

    generated = _invoke(["generate", "out/config.py", "--lang", "python"])
    imported = _invoke(["import", "source.env", "--output", "out/other.schema.toml"])

    assert generated.exit_code == 0, generated.stdout
    assert imported.exit_code == 0, imported.stdout
    assert os.path.isfile("out/config.py") and os.path.isfile("out/other.schema.toml")


def test_init_works_when_the_project_itself_is_reached_through_a_symlink(
    world, monkeypatch, tmp_path
):
    """
    init writes only files directly in the project root, so no directory
    below the root can be a symlink. The root itself is trusted: a project
    reached through a symlinked path (e.g. /home -> /var/home) must still
    work, writing into the real directory and nothing else.
    """
    project, outside = world
    _write(".env", "PORT=8000\n")
    alias = tmp_path / "alias"
    os.symlink(project, alias)
    monkeypatch.chdir(alias)
    before = _snapshot(outside)

    result = _invoke(["init"], "n\nn\n")

    assert result.exit_code == 0, result.stdout
    for name in ("env.schema.toml", "envshield.yml", ".gitignore", ".env.example"):
        assert os.path.isfile(project / name), name
        assert not os.path.islink(project / name), name
    assert _snapshot(outside) == before


# --- A failed permission change is a failed setup (BL-154 regression) -------


def test_setup_fails_and_keeps_the_python_file_when_permissions_fail(world, mocker):
    _write("envshield.yml", ONE_SERVICE + "    local_file: settings.py\n")
    _write("env.schema.toml", SCHEMA)
    _write("settings.py", "# keep me\nPORT = '8000'\n")
    mocker.patch("envshield.core.setup_manager.Prompt.ask", return_value=SECRET)

    def eperm(fd, mode):
        raise PermissionError(1, "Operation not permitted")

    mocker.patch("os.fchmod", eperm)

    result = _invoke(["setup"])

    assert result.exit_code != 0
    assert "Could not write to 'settings.py'" in result.stdout
    assert "Updated" not in result.stdout
    assert "Configuration complete" not in result.stdout
    with open("settings.py") as f:
        assert f.read() == "# keep me\nPORT = '8000'\n"


def test_hook_install_without_fchmod_writes_the_hook(world, monkeypatch):
    monkeypatch.delattr(os, "fchmod")

    result = _invoke(["hook", "install", "--yes"])

    assert result.exit_code == 0, result.stdout
    path = os.path.join(".git", "hooks", "pre-commit")
    assert os.path.isfile(path) and not os.path.islink(path)
    with open(path) as f:
        assert "envshield hook run pre-commit" in f.read()
