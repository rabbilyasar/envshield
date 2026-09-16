"""
Tests for M7 Git hook enforcement with staged content.

Validates staged content correctness:
- New file with secret → detected
- Modified file with secret → detected
- Partially staged file (unstaged secret) → does not block
- Unchanged file with secret → does not block
- Deleted secret content → does not produce finding
- Multiple staged files → correctly mapped
"""

import os
import subprocess
from pathlib import Path

from envshield.core import scanner


def _init_test_repo(tmpdir):
    """Initialize a test Git repository."""
    repo_path = Path(tmpdir)
    subprocess.run(["git", "init"], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )
    return repo_path


class TestStagedContentCorrectness:
    """Tests that enforcement evaluates only staged content."""

    def test_new_file_staged_secret_detected(self, tmp_path):
        """New file with staged secret → detected and blocks."""
        repo = _init_test_repo(tmp_path)
        test_file = repo / "test.py"

        # Create file with secret
        test_file.write_text('api_key = "sk_live_test123456789secret"\n')

        # Stage the file
        subprocess.run(["git", "add", "test.py"], cwd=repo, check=True)

        # Change to repo directory for scan
        original_cwd = os.getcwd()
        try:
            os.chdir(repo)
            secrets, _ = scanner._scan_single_file(
                str(test_file),
                set(),
                content=subprocess.run(
                    ["git", "show", ":test.py"],
                    cwd=repo,
                    capture_output=True,
                    text=True,
                    check=True,
                ).stdout,
            )
            assert len(secrets) == 1
            assert secrets[0]["secret_type"] == "Generic API Key"
        finally:
            os.chdir(original_cwd)

    def test_modified_file_staged_secret_detected(self, tmp_path):
        """Modified file with staged secret → detected."""
        repo = _init_test_repo(tmp_path)
        test_file = repo / "test.py"

        # Create and commit initial safe version
        test_file.write_text("# Safe code\n")
        subprocess.run(["git", "add", "test.py"], cwd=repo, check=True)
        subprocess.run(
            ["git", "commit", "-m", "Initial"],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        # Modify with secret and stage
        test_file.write_text('api_key = "sk_live_test123456789secret"\n')
        subprocess.run(["git", "add", "test.py"], cwd=repo, check=True)

        original_cwd = os.getcwd()
        try:
            os.chdir(repo)
            secrets, _ = scanner._scan_single_file(
                str(test_file),
                set(),
                content=subprocess.run(
                    ["git", "show", ":test.py"],
                    cwd=repo,
                    capture_output=True,
                    text=True,
                    check=True,
                ).stdout,
            )
            assert len(secrets) == 1
        finally:
            os.chdir(original_cwd)

    def test_partially_staged_file_unstaged_secret_not_detected(self, tmp_path):
        """Partially staged file: safe change staged, secret unstaged → does not block."""
        repo = _init_test_repo(tmp_path)
        test_file = repo / "test.py"

        # Create and commit initial version
        test_file.write_text("# Line 1\n# Line 2\n")
        subprocess.run(["git", "add", "test.py"], cwd=repo, check=True)
        subprocess.run(
            ["git", "commit", "-m", "Initial"],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        # Modify line 1 (safe) and stage it
        test_file.write_text("# Modified line 1\n# Line 2\n")
        subprocess.run(["git", "add", "test.py"], cwd=repo, check=True)

        # Now add a secret to line 2 WITHOUT staging
        test_file.write_text(
            '# Modified line 1\napi_key = "sk_live_test123456789secret"\n'
        )

        # Scan staged content only
        original_cwd = os.getcwd()
        try:
            os.chdir(repo)
            staged_content = subprocess.run(
                ["git", "show", ":test.py"],
                cwd=repo,
                capture_output=True,
                text=True,
                check=True,
            ).stdout

            secrets, _ = scanner._scan_single_file(
                str(test_file), set(), content=staged_content
            )
            # Staged content is safe - secret is only in working tree
            assert len(secrets) == 0
        finally:
            os.chdir(original_cwd)

    def test_unchanged_file_does_not_block(self, tmp_path):
        """Unchanged file with secret → does not block unrelated commit."""
        repo = _init_test_repo(tmp_path)
        secret_file = repo / "old_secret.py"
        other_file = repo / "other.py"

        # Commit file with secret
        secret_file.write_text('api_key = "sk_live_test123456789secret"\n')
        other_file.write_text("# Safe code\n")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(
            ["git", "commit", "-m", "Initial"],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        # Modify ONLY other_file and stage it
        other_file.write_text("# Modified safe code\n")
        subprocess.run(["git", "add", "other.py"], cwd=repo, check=True)

        # Get only staged files
        staged_files = (
            subprocess.run(
                ["git", "diff", "--cached", "--name-only"],
                cwd=repo,
                capture_output=True,
                text=True,
                check=True,
            )
            .stdout.strip()
            .split("\n")
        )

        # old_secret.py should NOT be in staged files
        assert "other.py" in staged_files
        assert "old_secret.py" not in staged_files

    def test_deleted_secret_content_no_new_finding(self, tmp_path):
        """Deleting a line with a secret → does not create a new finding."""
        repo = _init_test_repo(tmp_path)
        test_file = repo / "test.py"

        # Commit file with secret
        test_file.write_text('api_key = "sk_live_test123456789secret"\n')
        subprocess.run(["git", "add", "test.py"], cwd=repo, check=True)
        subprocess.run(
            ["git", "commit", "-m", "Initial"],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        # Delete the secret line
        test_file.write_text("# Secret removed\n")
        subprocess.run(["git", "add", "test.py"], cwd=repo, check=True)

        # Scan staged content
        original_cwd = os.getcwd()
        try:
            os.chdir(repo)
            staged_content = subprocess.run(
                ["git", "show", ":test.py"],
                cwd=repo,
                capture_output=True,
                text=True,
                check=True,
            ).stdout

            secrets, _ = scanner._scan_single_file(
                str(test_file), set(), content=staged_content
            )
            # Deletion should not create a finding
            assert len(secrets) == 0
        finally:
            os.chdir(original_cwd)

    def test_multiple_staged_files_correctly_mapped(self, tmp_path):
        """Multiple staged files with secrets → findings correctly mapped."""
        repo = _init_test_repo(tmp_path)
        file1 = repo / "src" / "auth.py"
        file2 = repo / "src" / "db.py"

        os.makedirs(repo / "src", exist_ok=True)

        # Create files with secrets
        file1.write_text('api_key = "sk_live_test123456789secret1"\n')
        file2.write_text('db_password = "secret_password_12345"\n')

        # Stage both
        subprocess.run(["git", "add", "."], cwd=repo, check=True)

        original_cwd = os.getcwd()
        try:
            os.chdir(repo)

            # Scan file1
            staged_content1 = subprocess.run(
                ["git", "show", ":src/auth.py"],
                cwd=repo,
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            secrets1, _ = scanner._scan_single_file(
                str(file1), set(), content=staged_content1
            )

            # Scan file2
            staged_content2 = subprocess.run(
                ["git", "show", ":src/db.py"],
                cwd=repo,
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            secrets2, _ = scanner._scan_single_file(
                str(file2), set(), content=staged_content2
            )

            # Both should have findings
            assert len(secrets1) == 1
            assert len(secrets2) == 1

            # Findings should be mapped to correct files
            assert "auth.py" in secrets1[0]["file_path"]
            assert "db.py" in secrets2[0]["file_path"]
        finally:
            os.chdir(original_cwd)


class TestEnforcementWithStagedContent:
    """Integration tests combining enforcement with Git staging."""

    def test_ordinary_code_pattern_allows_commit(self, tmp_path):
        """Code pattern (classifier suppresses) → commit allowed."""
        repo = _init_test_repo(tmp_path)
        test_file = repo / "test.py"

        # Pattern that classifier suppresses (keyword argument)
        test_file.write_text(
            "response.set_cookie(\n    key=SESSION_COOKIE_NAME,\n    secure=True\n)\n"
        )

        subprocess.run(["git", "add", "test.py"], cwd=repo, check=True)

        original_cwd = os.getcwd()
        try:
            os.chdir(repo)
            staged_content = subprocess.run(
                ["git", "show", ":test.py"],
                cwd=repo,
                capture_output=True,
                text=True,
                check=True,
            ).stdout

            secrets, _ = scanner._scan_single_file(
                str(test_file), set(), content=staged_content
            )
            # Classifier should suppress this
            assert len(secrets) == 0
        finally:
            os.chdir(original_cwd)

    def test_string_literal_blocks_in_enforcement_mode(self, tmp_path):
        """String literal (LIKELY_SECRET) → blocks in enforcement mode."""
        repo = _init_test_repo(tmp_path)
        test_file = repo / "test.py"

        # String literal - high confidence secret
        test_file.write_text('api_key = "sk_live_test123456789secret"\n')

        subprocess.run(["git", "add", "test.py"], cwd=repo, check=True)

        original_cwd = os.getcwd()
        try:
            os.chdir(repo)
            staged_content = subprocess.run(
                ["git", "show", ":test.py"],
                cwd=repo,
                capture_output=True,
                text=True,
                check=True,
            ).stdout

            secrets, _ = scanner._scan_single_file(
                str(test_file), set(), content=staged_content
            )
            # Should have a finding with LIKELY_SECRET classification
            assert len(secrets) == 1
            assert secrets[0].get("classification") == "likely_secret"
        finally:
            os.chdir(original_cwd)
