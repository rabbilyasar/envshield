# envshield/tests/core/test_dependency_snapshot.py
import subprocess

import pytest

from envshield.core import dependency_snapshot
from envshield.core.exceptions import SchemaNotFoundError


def _init_repo(path):
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=path, check=True
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)


def _commit(path, message):
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=path, check=True)


def _write(path, relative, content):
    full = path / relative
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content)


def _single_service_repo(tmp_path):
    _init_repo(tmp_path)
    _write(
        tmp_path,
        "envshield.yml",
        "services:\n  api:\n    schema: env.schema.toml\n",
    )
    _write(tmp_path, "env.schema.toml", "")
    _commit(tmp_path, "init")


class TestDefaultRevisionsAreHeadVsWorkingTree:
    def test_a_new_call_added_uncommitted_is_found_against_head(
        self, tmp_path, monkeypatch
    ):
        _single_service_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        _write(tmp_path, "app.py", "import os\n")
        _commit(tmp_path, "no usages yet")
        _write(tmp_path, "app.py", "import os\nx = os.environ.get('FOO')\n")

        usages_a, usages_b = dependency_snapshot.discover_usages_for_service("api")

        assert usages_a == []
        assert {u.variable for u in usages_b} == {"FOO"}

    def test_a_call_present_at_both_head_and_working_tree_is_in_both(
        self, tmp_path, monkeypatch
    ):
        """
        The file must actually differ between HEAD and the working tree
        to appear in the changed-file list at all -- an unrelated edit
        (adding a second, unrelated call) exercises that while keeping
        FOO present on both sides.
        """
        _single_service_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        _write(tmp_path, "app.py", "import os\nx = os.environ.get('FOO')\n")
        _commit(tmp_path, "has FOO")
        _write(
            tmp_path,
            "app.py",
            "import os\nx = os.environ.get('FOO')\ny = os.environ.get('BAR')\n",
        )

        usages_a, usages_b = dependency_snapshot.discover_usages_for_service("api")

        assert {u.variable for u in usages_a} == {"FOO"}
        assert {u.variable for u in usages_b} == {"FOO", "BAR"}


class TestUntrackedFilesAreIncludedOnlyForTheWorkingTreeSide:
    def test_a_brand_new_untracked_file_is_discovered_against_head(
        self, tmp_path, monkeypatch
    ):
        """
        The correctness point this guards: 'git diff' alone never reports
        an untracked file, so without merging in list_untracked_files, a
        fresh (never 'git add'-ed) file with a new dependency would be
        invisible to the default HEAD-vs-working-tree comparison --
        defeating the "catch it before you commit" purpose.
        """
        _single_service_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        _write(tmp_path, "new_file.py", "import os\nx = os.environ.get('UNTRACKED')\n")

        usages_a, usages_b = dependency_snapshot.discover_usages_for_service("api")

        assert usages_a == []
        assert {u.variable for u in usages_b} == {"UNTRACKED"}

    def test_untracked_files_are_not_considered_for_an_explicit_two_revision_comparison(
        self, tmp_path, monkeypatch
    ):
        _single_service_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        _write(tmp_path, "new_file.py", "import os\nx = os.environ.get('UNTRACKED')\n")

        usages_a, usages_b = dependency_snapshot.discover_usages_for_service(
            "api", "HEAD", "HEAD"
        )

        assert usages_a == []
        assert usages_b == []


class TestDeletedFilesContributeNothingNew:
    def test_a_deleted_file_produces_no_new_usages(self, tmp_path, monkeypatch):
        _single_service_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        _write(tmp_path, "app.py", "import os\nx = os.environ.get('FOO')\n")
        _commit(tmp_path, "has FOO")
        (tmp_path / "app.py").unlink()

        usages_a, usages_b = dependency_snapshot.discover_usages_for_service("api")

        assert {u.variable for u in usages_a} == {"FOO"}
        assert usages_b == []


class TestMultiServiceFileOwnershipIsRespected:
    """
    Correctness point: --service alone doesn't restrict a repo-wide
    changed-file list to that service's own files. discover_usages_for_service
    must filter changed/untracked files down to the resolved service's own
    directory (via config_manager.get_service_dir + service_dir_contains)
    before any content is read -- otherwise a change in service B's files
    would be checked against service A's schema.
    """

    def _multi_service_repo(self, tmp_path):
        _init_repo(tmp_path)
        _write(
            tmp_path,
            "envshield.yml",
            "services:\n"
            "  api:\n    schema: services/api/env.schema.toml\n"
            "  web:\n    schema: services/web/env.schema.toml\n",
        )
        _write(tmp_path, "services/api/env.schema.toml", "")
        _write(tmp_path, "services/web/env.schema.toml", "")
        _commit(tmp_path, "init")

    def test_a_change_in_another_service_directory_is_not_picked_up(
        self, tmp_path, monkeypatch
    ):
        self._multi_service_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        _write(
            tmp_path,
            "services/web/app.py",
            "import os\nx = os.environ.get('WEB_ONLY')\n",
        )

        usages_a, usages_b = dependency_snapshot.discover_usages_for_service("api")

        assert usages_a == []
        assert usages_b == []

    def test_a_change_in_the_targeted_service_directory_is_picked_up(
        self, tmp_path, monkeypatch
    ):
        self._multi_service_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        _write(
            tmp_path,
            "services/api/app.py",
            "import os\nx = os.environ.get('API_ONLY')\n",
        )

        usages_a, usages_b = dependency_snapshot.discover_usages_for_service("api")

        assert usages_a == []
        assert {u.variable for u in usages_b} == {"API_ONLY"}

    def test_an_unknown_service_raises(self, tmp_path, monkeypatch):
        self._multi_service_repo(tmp_path)
        monkeypatch.chdir(tmp_path)

        with pytest.raises(SchemaNotFoundError):
            dependency_snapshot.discover_usages_for_service("does-not-exist")


class TestNonDiscoverableFilesAreIgnored:
    def test_a_changed_non_source_file_contributes_nothing(self, tmp_path, monkeypatch):
        _single_service_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        _write(tmp_path, "README.md", "# hi\n")

        usages_a, usages_b = dependency_snapshot.discover_usages_for_service("api")

        assert usages_a == []
        assert usages_b == []


class TestJsAndTsFilesAreDiscovered:
    def test_jsx_and_tsx_are_covered(self, tmp_path, monkeypatch):
        _single_service_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        _write(tmp_path, "a.jsx", "const x = process.env.FOO;\n")
        _write(tmp_path, "b.tsx", "const y = process.env.BAR;\n")

        _, usages_b = dependency_snapshot.discover_usages_for_service("api")

        assert {u.variable for u in usages_b} == {"FOO", "BAR"}


class TestTwoExplicitRevisions:
    def test_a_dependency_added_between_two_historical_commits_is_found(
        self, tmp_path, monkeypatch
    ):
        _single_service_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        _write(tmp_path, "app.py", "import os\n")
        _commit(tmp_path, "v1: no usages")
        _write(tmp_path, "app.py", "import os\nx = os.environ.get('FOO')\n")
        _commit(tmp_path, "v2: adds FOO")

        usages_a, usages_b = dependency_snapshot.discover_usages_for_service(
            "api", "HEAD~1", "HEAD"
        )

        assert usages_a == []
        assert {u.variable for u in usages_b} == {"FOO"}
