# envshield/tests/core/test_dependency_snapshot.py
import os
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


class TestDiscoveredUsageFilePathIsCwdRelative:
    def test_file_path_is_not_absolute(self, tmp_path, monkeypatch):
        """
        Regression: _changed_source_files returns absolute paths (git_utils'
        documented contract) -- reported verbatim on a DiscoveredVariableUsage,
        this produced an unreadable, Rich-table-truncated absolute path in
        both 'undeclared's table and its --json output. The recorded
        usage's file_path must be cwd-relative; only the internal disk/git
        read still needs the real absolute path.
        """
        _single_service_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        _write(tmp_path, "app.py", "import os\n")
        _commit(tmp_path, "no usages yet")
        _write(tmp_path, "app.py", "import os\nx = os.environ.get('FOO')\n")

        _, usages_b = dependency_snapshot.discover_usages_for_service("api")

        assert [u.file_path for u in usages_b] == ["app.py"]

    def test_file_path_is_cwd_relative_for_the_explicit_two_revision_form_too(
        self, tmp_path, monkeypatch
    ):
        _single_service_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        _write(tmp_path, "app.py", "import os\n")
        _commit(tmp_path, "no usages yet")
        _write(tmp_path, "app.py", "import os\nx = os.environ.get('FOO')\n")
        _commit(tmp_path, "adds FOO")

        _, usages_b = dependency_snapshot.discover_usages_for_service(
            "api", revision_a="HEAD~1", revision_b="HEAD"
        )

        assert [u.file_path for u in usages_b] == ["app.py"]


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


class TestAdditionalSourceRoots:
    """BL-106: additional_source_roots widens a service's discovery scope
    to directories outside its own (e.g. a shared internal library)."""

    def _repo_with_shared_lib(self, tmp_path, roots_yaml):
        _init_repo(tmp_path)
        _write(
            tmp_path,
            "envshield.yml",
            "services:\n"
            "  api:\n"
            "    schema: services/api/env.schema.toml\n"
            f"{roots_yaml}",
        )
        _write(tmp_path, "services/api/env.schema.toml", "")
        _commit(tmp_path, "init")

    def test_one_additional_root_is_discovered(self, tmp_path, monkeypatch):
        self._repo_with_shared_lib(
            tmp_path,
            "    additional_source_roots:\n      - shared/lib\n",
        )
        monkeypatch.chdir(tmp_path)
        _write(
            tmp_path,
            "shared/lib/util.py",
            "import os\nx = os.environ.get('SHARED_FLAG')\n",
        )

        usages_a, usages_b = dependency_snapshot.discover_usages_for_service("api")

        assert usages_a == []
        assert {u.variable for u in usages_b} == {"SHARED_FLAG"}

    def test_multiple_additional_roots_are_all_discovered(self, tmp_path, monkeypatch):
        self._repo_with_shared_lib(
            tmp_path,
            "    additional_source_roots:\n      - shared/lib\n      - vendor/other\n",
        )
        monkeypatch.chdir(tmp_path)
        _write(
            tmp_path, "shared/lib/util.py", "import os\nx = os.environ.get('A')\n"
        )
        _write(
            tmp_path, "vendor/other/mod.py", "import os\nx = os.environ.get('B')\n"
        )

        usages_a, usages_b = dependency_snapshot.discover_usages_for_service("api")

        assert {u.variable for u in usages_b} == {"A", "B"}

    def test_nonexistent_root_is_a_silent_no_op(self, tmp_path, monkeypatch):
        self._repo_with_shared_lib(
            tmp_path,
            "    additional_source_roots:\n      - does/not/exist\n",
        )
        monkeypatch.chdir(tmp_path)
        _write(
            tmp_path,
            "services/api/app.py",
            "import os\nx = os.environ.get('API_ONLY')\n",
        )

        usages_a, usages_b = dependency_snapshot.discover_usages_for_service("api")

        assert {u.variable for u in usages_b} == {"API_ONLY"}

    def test_a_root_nested_inside_the_service_directory_produces_no_duplicate(
        self, tmp_path, monkeypatch
    ):
        """An additional root that overlaps the service's own directory
        must not cause the same changed file to be reported twice."""
        self._repo_with_shared_lib(
            tmp_path,
            "    additional_source_roots:\n      - services/api/vendored\n",
        )
        monkeypatch.chdir(tmp_path)
        _write(
            tmp_path,
            "services/api/vendored/util.py",
            "import os\nx = os.environ.get('NESTED')\n",
        )

        usages_a, usages_b = dependency_snapshot.discover_usages_for_service("api")

        matches = [u for u in usages_b if u.variable == "NESTED"]
        assert len(matches) == 1

    def test_the_same_root_may_be_shared_by_two_services(self, tmp_path, monkeypatch):
        _init_repo(tmp_path)
        _write(
            tmp_path,
            "envshield.yml",
            "services:\n"
            "  api:\n"
            "    schema: services/api/env.schema.toml\n"
            "    additional_source_roots:\n      - shared/lib\n"
            "  web:\n"
            "    schema: services/web/env.schema.toml\n"
            "    additional_source_roots:\n      - shared/lib\n",
        )
        _write(tmp_path, "services/api/env.schema.toml", "")
        _write(tmp_path, "services/web/env.schema.toml", "")
        _commit(tmp_path, "init")
        monkeypatch.chdir(tmp_path)
        _write(
            tmp_path,
            "shared/lib/util.py",
            "import os\nx = os.environ.get('SHARED_FLAG')\n",
        )

        _, api_usages_b = dependency_snapshot.discover_usages_for_service("api")
        _, web_usages_b = dependency_snapshot.discover_usages_for_service("web")

        assert {u.variable for u in api_usages_b} == {"SHARED_FLAG"}
        assert {u.variable for u in web_usages_b} == {"SHARED_FLAG"}

    def test_absent_key_preserves_existing_behavior(self, tmp_path, monkeypatch):
        """Regression: a shared-looking directory outside the service's own
        directory is NOT discovered when additional_source_roots isn't set --
        the pre-BL-106 behavior is unchanged."""
        self._repo_with_shared_lib(tmp_path, "")
        monkeypatch.chdir(tmp_path)
        _write(
            tmp_path,
            "shared/lib/util.py",
            "import os\nx = os.environ.get('SHARED_FLAG')\n",
        )

        usages_a, usages_b = dependency_snapshot.discover_usages_for_service("api")

        assert usages_a == []
        assert usages_b == []


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


class TestSymlinkNeverFollowed:
    """
    Regression coverage for a reproduced trust-boundary violation: a
    symlink (untracked, or a tracked one whose target changed) inside the
    working tree must never cause dependency_snapshot to read -- and then
    report a "dependency" sourced from -- content outside the project.
    This is the same vulnerability class as P0-2 (scanner.py's
    _collect_files_to_scan/_open_for_scan), reproduced here independently
    against dependency_snapshot's own disk-read path before being fixed.
    """

    OUTSIDE_VARIABLE = "SUPER_SECRET_OUTSIDE_VAR"

    def _make_outside_source_file(self, tmp_path, name="leak.py"):
        outside_dir = tmp_path.parent / f"{tmp_path.name}_outside"
        outside_dir.mkdir(exist_ok=True)
        target = outside_dir / name
        target.write_text(
            f"import os\n{self.OUTSIDE_VARIABLE} = os.environ.get('{self.OUTSIDE_VARIABLE}')\n"
        )
        return target

    def test_untracked_symlink_contributes_no_usages(self, tmp_path, monkeypatch):
        _single_service_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        target = self._make_outside_source_file(tmp_path)
        os.symlink(target, tmp_path / "evil.py")

        usages_a, usages_b = dependency_snapshot.discover_usages_for_service("api")

        assert usages_a == []
        assert usages_b == []

    def test_untracked_symlink_is_excluded_from_the_changed_file_list(
        self, tmp_path, monkeypatch
    ):
        """Unit-level proof of the collection-time layer specifically,
        independent of the disk-read layer -- the symlink must not even
        reach _read_source in the first place."""
        _single_service_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        target = self._make_outside_source_file(tmp_path)
        symlink_path = tmp_path / "evil.py"
        os.symlink(target, symlink_path)

        files = dependency_snapshot._changed_source_files("HEAD", None, quiet=True)

        assert str(symlink_path) not in files

    def test_read_source_refuses_a_symlink_independent_of_collection(
        self, tmp_path, monkeypatch
    ):
        """
        Models the moment after collection where the filesystem object at
        this path is (or has become) a symlink -- calling the read-time
        function directly, with no collection step involved at all, proves
        it refuses the read on its own rather than trusting an earlier
        check that may be stale by the time this runs (the same TOCTOU
        argument scanner.py's own _open_for_scan docstring makes).
        """
        target = self._make_outside_source_file(tmp_path)
        symlink_path = tmp_path / "evil.py"
        os.symlink(target, symlink_path)

        content = dependency_snapshot._read_source(str(symlink_path), None)

        assert content is None

    def test_open_disk_source_raises_on_a_symlink(self, tmp_path):
        """Unit-level proof of the mechanism itself, at the smallest
        granularity: the open call raises, it doesn't silently succeed."""
        target = self._make_outside_source_file(tmp_path)
        symlink_path = tmp_path / "evil.py"
        os.symlink(target, symlink_path)

        assert dependency_snapshot._CAN_USE_O_NOFOLLOW, (
            "this test environment is expected to support O_NOFOLLOW; "
            "see TestONofollowFallback for the platform without it"
        )
        with pytest.raises(OSError):
            dependency_snapshot._open_disk_source(str(symlink_path))

    def test_open_disk_source_reads_a_regular_file_normally(self, tmp_path):
        real_file = tmp_path / "real.py"
        real_file.write_text("import os\nx = os.environ.get('FOO')\n")

        with dependency_snapshot._open_disk_source(str(real_file)) as f:
            content = f.read()

        assert content == "import os\nx = os.environ.get('FOO')\n"

    def test_a_tracked_symlink_whose_target_changed_is_also_skipped(
        self, tmp_path, monkeypatch
    ):
        """
        Not just untracked files: a symlink already committed (its target
        is what's tracked, per Git's own symlink-as-blob model) still
        shows up as a *changed* file if its target path string changes --
        and the working-tree side must still refuse to follow it, exactly
        like a brand-new untracked one.
        """
        _single_service_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        inside_target = tmp_path / "inside.py"
        inside_target.write_text("import os\nx = os.environ.get('INSIDE_FOO')\n")
        os.symlink(inside_target, tmp_path / "link.py")
        _commit(tmp_path, "commit a symlink pointing inside the repo")

        outside_target = self._make_outside_source_file(tmp_path)
        os.remove(tmp_path / "link.py")
        os.symlink(outside_target, tmp_path / "link.py")

        usages_a, usages_b = dependency_snapshot.discover_usages_for_service("api")

        assert usages_b == []

    def test_symlink_skip_is_silent_when_quiet(self, tmp_path, monkeypatch, capsys):
        _single_service_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        target = self._make_outside_source_file(tmp_path)
        os.symlink(target, tmp_path / "evil.py")

        dependency_snapshot.discover_usages_for_service("api", quiet=True)

        assert capsys.readouterr().out == ""

    def test_symlink_skip_warns_by_default(self, tmp_path, monkeypatch, capsys):
        _single_service_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        target = self._make_outside_source_file(tmp_path)
        symlink_path = tmp_path / "evil.py"
        os.symlink(target, symlink_path)

        dependency_snapshot.discover_usages_for_service("api")

        out = capsys.readouterr().out
        assert "Skipping 1 symlink" in out
        assert str(symlink_path) in out

    def test_explicit_two_revision_form_does_not_check_for_symlinks(
        self, tmp_path, monkeypatch
    ):
        """
        The collection-time filter is deliberately skipped for the
        explicit two-revision form: neither side is a disk read there
        (both go through git show), so a symlink's live disk state is
        irrelevant -- checking for it would just be dead code, not an
        additional protection.
        """
        _single_service_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        target = self._make_outside_source_file(tmp_path)
        os.symlink(target, tmp_path / "evil.py")

        files = dependency_snapshot._changed_source_files("HEAD", "HEAD", quiet=True)

        # 'evil.py' is untracked, so it never appears in a plain git diff
        # between two concrete revisions in the first place -- this
        # asserts the *reason* is "not a changed file," not "filtered as
        # a symlink," by confirming quiet=True produced no skip decision
        # either way (nothing to skip).
        assert files == []


class TestONofollowFallback:
    """
    Coverage for platforms without os.O_NOFOLLOW (Windows). Patches the
    module-level capability flag rather than mocking any stdlib function,
    mirroring scanner.py's own TestONofollowFallback exactly.
    """

    def test_fallback_still_reads_regular_files_normally(self, tmp_path, mocker):
        mocker.patch.object(dependency_snapshot, "_CAN_USE_O_NOFOLLOW", False)
        real_file = tmp_path / "real.py"
        real_file.write_text("import os\n")

        with dependency_snapshot._open_disk_source(str(real_file)) as f:
            content = f.read()

        assert content == "import os\n"

    def test_fallback_has_the_documented_residual_symlink_gap(self, tmp_path, mocker):
        """
        Proves the accepted limitation explicitly: without O_NOFOLLOW, a
        symlink IS followed. A test failure here would mean the fallback
        became *more* dangerous than documented, not less.
        """
        mocker.patch.object(dependency_snapshot, "_CAN_USE_O_NOFOLLOW", False)
        outside = tmp_path.parent / f"{tmp_path.name}_outside_fallback"
        outside.mkdir(exist_ok=True)
        target = outside / "target.py"
        target.write_text("import os\nx = os.environ.get('FOO')\n")
        symlink_path = tmp_path / "linked.py"
        os.symlink(target, symlink_path)

        with dependency_snapshot._open_disk_source(str(symlink_path)) as f:
            content = f.read()

        assert content == "import os\nx = os.environ.get('FOO')\n"


class TestServiceDirIsNotRevisionAware:
    """
    Locks in the accepted Milestone-1 limitation documented on
    discover_usages_for_service: service-directory resolution always
    reads the *live* envshield.yml, never a revision-specific one. This
    test doesn't assert correct behavior -- it asserts the CURRENT,
    documented, accepted-for-now behavior, so a future change can't
    silently regress it further without this test forcing a conscious
    decision. If this test ever needs updating, that's a deliberate design
    change, not a bug fix landing unnoticed.
    """

    def test_a_file_that_moved_with_its_service_directory_is_wrongly_reported_as_new(
        self, tmp_path, monkeypatch
    ):
        _init_repo(tmp_path)
        _write(
            tmp_path,
            "envshield.yml",
            "services:\n  api:\n    schema: old_location/env.schema.toml\n",
        )
        _write(tmp_path, "old_location/env.schema.toml", "")
        _write(
            tmp_path,
            "old_location/app.py",
            "import os\nx = os.environ.get('FOO')\n",
        )
        _commit(tmp_path, "v1: FOO lives under old_location")

        (tmp_path / "old_location" / "app.py").unlink()
        (tmp_path / "old_location" / "env.schema.toml").unlink()
        _write(
            tmp_path,
            "new_location/app.py",
            "import os\nx = os.environ.get('FOO')\n",
        )
        _write(tmp_path, "new_location/env.schema.toml", "")
        _write(
            tmp_path,
            "envshield.yml",
            "services:\n  api:\n    schema: new_location/env.schema.toml\n",
        )
        _commit(tmp_path, "v2: api's directory moves to new_location")
        monkeypatch.chdir(tmp_path)

        usages_a, usages_b = dependency_snapshot.discover_usages_for_service(
            "api", "HEAD~1", "HEAD"
        )

        # The documented limitation: FOO already existed before the move
        # (under old_location, which the *live* service_dir no longer
        # matches), so it's invisible on the 'a' side and looks new on
        # the 'b' side -- even though nothing new was actually introduced.
        assert usages_a == []
        assert {u.variable for u in usages_b} == {"FOO"}
