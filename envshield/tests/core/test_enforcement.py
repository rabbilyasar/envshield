"""
Tests for M7 enforcement layer.

Validates:
- Classification-based policy enforcement
- Interactive override flow
- Non-interactive blocking
- Output safety (no raw values)
- Multiple findings grouping
"""

import io
from unittest.mock import patch

from envshield.core import enforcement


class TestEnforcementPolicy:
    """Tests for basic enforcement policy decisions."""

    def test_no_findings_allow(self):
        """No findings → allow."""
        allowed = enforcement.enforce_findings([], [], interactive=True)
        assert allowed is True

    def test_no_findings_allow_non_interactive(self):
        """No findings → allow (non-interactive)."""
        allowed = enforcement.enforce_findings([], [], interactive=False)
        assert allowed is True

    def test_undeclared_always_blocks(self):
        """Undeclared variables always block (no override offered)."""
        undeclared = [
            {
                "file_path": "src/app.py",
                "line_num": 42,
                "variable_name": "UNDECLARED_VAR",
            }
        ]
        allowed = enforcement.enforce_findings([], undeclared, interactive=True)
        assert allowed is False

    def test_undeclared_blocks_non_interactive(self):
        """Undeclared variables block in non-interactive mode."""
        undeclared = [
            {
                "file_path": "src/app.py",
                "line_num": 42,
                "variable_name": "UNDECLARED_VAR",
            }
        ]
        allowed = enforcement.enforce_findings([], undeclared, interactive=False)
        assert allowed is False

    def test_ambiguous_blocks_interactive(self):
        """AMBIGUOUS findings block (existing policy, no override in M7)."""
        secrets = [
            {
                "file_path": "src/config.py",
                "line_num": 10,
                "secret_type": "Generic API Key",
                "redacted_preview": "[REDACTED (12 chars)]",
                "classification": "ambiguous",
                "classification_confidence": "LOW",
            }
        ]
        allowed = enforcement.enforce_findings(secrets, [], interactive=True)
        assert allowed is False

    def test_ambiguous_blocks_non_interactive(self):
        """AMBIGUOUS findings block in non-interactive mode."""
        secrets = [
            {
                "file_path": "src/config.py",
                "line_num": 10,
                "secret_type": "Generic API Key",
                "redacted_preview": "[REDACTED (12 chars)]",
                "classification": "ambiguous",
                "classification_confidence": "LOW",
            }
        ]
        allowed = enforcement.enforce_findings(secrets, [], interactive=False)
        assert allowed is False

    def test_unclassified_blocks(self):
        """Findings without classification (detectors where classifier doesn't apply) block."""
        secrets = [
            {
                "file_path": "creds.json",
                "line_num": 5,
                "secret_type": "AWS Access Key",
                "redacted_preview": "[REDACTED (20 chars)]",
                # No classification field - classifier not applied
            }
        ]
        allowed = enforcement.enforce_findings(secrets, [], interactive=True)
        assert allowed is False


class TestInteractiveOverride:
    """Tests for interactive LIKELY_SECRET override flow."""

    def test_likely_secret_blocks_without_override(self):
        """LIKELY_SECRET + interactive → displays warning, blocks if user aborts."""
        secrets = [
            {
                "file_path": "src/auth.py",
                "line_num": 15,
                "secret_type": "Generic API Key",
                "redacted_preview": "[REDACTED (30 chars)]",
                "classification": "likely_secret",
                "classification_confidence": "HIGH",
            }
        ]
        # Simulate user choosing option "1" (abort)
        with patch("envshield.core.enforcement.console.input", return_value="1"):
            allowed = enforcement.enforce_findings(secrets, [], interactive=True)
        assert allowed is False

    def test_likely_secret_override_with_exact_confirmation(self):
        """LIKELY_SECRET + interactive + valid override → allow."""
        secrets = [
            {
                "file_path": "src/auth.py",
                "line_num": 15,
                "secret_type": "Generic API Key",
                "redacted_preview": "[REDACTED (30 chars)]",
                "classification": "likely_secret",
                "classification_confidence": "HIGH",
            }
        ]
        # Simulate user choosing "2" then "COMMIT ANYWAY"
        with patch(
            "envshield.core.enforcement.console.input",
            side_effect=["2", "COMMIT ANYWAY"],
        ):
            allowed = enforcement.enforce_findings(secrets, [], interactive=True)
        assert allowed is True

    def test_likely_secret_override_invalid_confirmation(self):
        """LIKELY_SECRET + interactive + invalid confirmation → block."""
        secrets = [
            {
                "file_path": "src/auth.py",
                "line_num": 15,
                "secret_type": "Generic API Key",
                "redacted_preview": "[REDACTED (30 chars)]",
                "classification": "likely_secret",
                "classification_confidence": "HIGH",
            }
        ]
        # Simulate user choosing "2" but providing "yes" instead of exact text
        with patch(
            "envshield.core.enforcement.console.input", side_effect=["2", "yes"]
        ):
            allowed = enforcement.enforce_findings(secrets, [], interactive=True)
        assert allowed is False

    def test_likely_secret_override_empty_confirmation(self):
        """LIKELY_SECRET + interactive + empty confirmation → block."""
        secrets = [
            {
                "file_path": "src/auth.py",
                "line_num": 15,
                "secret_type": "Generic API Key",
                "redacted_preview": "[REDACTED (30 chars)]",
                "classification": "likely_secret",
                "classification_confidence": "HIGH",
            }
        ]
        # Simulate user choosing "2" but pressing Enter without text
        with patch(
            "envshield.core.enforcement.console.input", side_effect=["2", ""]
        ):
            allowed = enforcement.enforce_findings(secrets, [], interactive=True)
        assert allowed is False

    def test_likely_secret_eof_aborts(self):
        """LIKELY_SECRET + EOF during choice → block."""
        secrets = [
            {
                "file_path": "src/auth.py",
                "line_num": 15,
                "secret_type": "Generic API Key",
                "redacted_preview": "[REDACTED (30 chars)]",
                "classification": "likely_secret",
                "classification_confidence": "HIGH",
            }
        ]
        # Simulate EOF
        with patch(
            "envshield.core.enforcement.console.input", side_effect=EOFError()
        ):
            allowed = enforcement.enforce_findings(secrets, [], interactive=True)
        assert allowed is False

    def test_likely_secret_ctrl_c_aborts(self):
        """LIKELY_SECRET + Ctrl+C during choice → block."""
        secrets = [
            {
                "file_path": "src/auth.py",
                "line_num": 15,
                "secret_type": "Generic API Key",
                "redacted_preview": "[REDACTED (30 chars)]",
                "classification": "likely_secret",
                "classification_confidence": "HIGH",
            }
        ]
        # Simulate Ctrl+C
        with patch(
            "envshield.core.enforcement.console.input",
            side_effect=KeyboardInterrupt(),
        ):
            allowed = enforcement.enforce_findings(secrets, [], interactive=True)
        assert allowed is False

    def test_likely_secret_eof_during_confirmation_aborts(self):
        """LIKELY_SECRET + EOF during confirmation → block."""
        secrets = [
            {
                "file_path": "src/auth.py",
                "line_num": 15,
                "secret_type": "Generic API Key",
                "redacted_preview": "[REDACTED (30 chars)]",
                "classification": "likely_secret",
                "classification_confidence": "HIGH",
            }
        ]
        # Simulate user choosing "2" but then EOF during confirmation
        with patch(
            "envshield.core.enforcement.console.input",
            side_effect=["2", EOFError()],
        ):
            allowed = enforcement.enforce_findings(secrets, [], interactive=True)
        assert allowed is False


class TestNonInteractive:
    """Tests for non-interactive enforcement (CI/automation)."""

    def test_likely_secret_blocks_non_interactive(self):
        """LIKELY_SECRET without TTY → no prompt, block."""
        secrets = [
            {
                "file_path": "src/auth.py",
                "line_num": 15,
                "secret_type": "Generic API Key",
                "redacted_preview": "[REDACTED (30 chars)]",
                "classification": "likely_secret",
                "classification_confidence": "HIGH",
            }
        ]
        # No prompting should occur
        with patch("envshield.core.enforcement.console.input") as mock_input:
            allowed = enforcement.enforce_findings(secrets, [], interactive=False)
            mock_input.assert_not_called()
        assert allowed is False

    def test_multiple_likely_secret_blocks_non_interactive(self):
        """Multiple LIKELY_SECRET findings without TTY → block."""
        secrets = [
            {
                "file_path": "src/auth.py",
                "line_num": 15,
                "secret_type": "Generic API Key",
                "redacted_preview": "[REDACTED (30 chars)]",
                "classification": "likely_secret",
                "classification_confidence": "HIGH",
            },
            {
                "file_path": "src/db.py",
                "line_num": 8,
                "secret_type": "Database Connection String",
                "redacted_preview": "[REDACTED (45 chars)]",
                "classification": "likely_secret",
                "classification_confidence": "HIGH",
            },
        ]
        allowed = enforcement.enforce_findings(secrets, [], interactive=False)
        assert allowed is False


class TestMultipleFindings:
    """Tests for multiple findings grouping and display."""

    def test_mixed_classifications_group_correctly(self):
        """HIGH CONFIDENCE and AMBIGUOUS findings are grouped separately."""
        secrets = [
            {
                "file_path": "src/auth.py",
                "line_num": 15,
                "secret_type": "Generic API Key",
                "redacted_preview": "[REDACTED (30 chars)]",
                "classification": "likely_secret",
                "classification_confidence": "HIGH",
            },
            {
                "file_path": "src/config.py",
                "line_num": 42,
                "secret_type": "Generic API Key",
                "redacted_preview": "[REDACTED (12 chars)]",
                "classification": "ambiguous",
                "classification_confidence": "LOW",
            },
            {
                "file_path": "tests/mock.py",
                "line_num": 11,
                "secret_type": "AWS Access Key",
                "redacted_preview": "[REDACTED (20 chars)]",
                # No classification - unclassified
            },
        ]

        grouped = enforcement._group_findings_by_classification(secrets)

        assert len(grouped["high_confidence"]) == 1
        assert grouped["high_confidence"][0]["file_path"] == "src/auth.py"

        assert len(grouped["ambiguous"]) == 1
        assert grouped["ambiguous"][0]["file_path"] == "src/config.py"

        assert len(grouped["unclassified"]) == 1
        assert grouped["unclassified"][0]["file_path"] == "tests/mock.py"


class TestOutputSafety:
    """Tests that no raw secret values are exposed."""

    def test_warning_does_not_contain_raw_value(self):
        """Warning display must not contain the actual secret value."""
        secrets = [
            {
                "file_path": "src/auth.py",
                "line_num": 15,
                "secret_type": "Generic API Key",
                "redacted_preview": "[REDACTED (30 chars)]",
                "classification": "likely_secret",
                "classification_confidence": "HIGH",
                # Simulating a situation where the actual value exists in memory
                # but should NEVER be displayed
                "_test_raw_value_never_display": "sk_live_actual_secret_12345",
            }
        ]

        # Capture console output
        with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            with patch("envshield.core.enforcement.console.input", return_value="1"):
                enforcement.enforce_findings(secrets, [], interactive=True)

            output = mock_stdout.getvalue()

            # SECURITY: Raw value must never appear in output
            assert "sk_live_actual_secret_12345" not in output

            # Safe metadata should appear
            assert "src/auth.py:15" in output
            assert "Generic API Key" in output

    def test_grouped_display_safe(self):
        """Multiple findings display must not leak raw values."""
        secrets = [
            {
                "file_path": "src/auth.py",
                "line_num": 15,
                "secret_type": "Generic API Key",
                "redacted_preview": "[REDACTED (30 chars)]",
                "classification": "likely_secret",
            },
            {
                "file_path": "src/db.py",
                "line_num": 8,
                "secret_type": "Database Connection String",
                "redacted_preview": "[REDACTED (45 chars)]",
                "classification": "likely_secret",
            },
        ]

        with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            with patch("envshield.core.enforcement.console.input", return_value="1"):
                enforcement.enforce_findings(secrets, [], interactive=True)

            output = mock_stdout.getvalue()

            # Only safe metadata should appear
            assert "src/auth.py:15" in output
            assert "src/db.py:8" in output
            assert "Generic API Key" in output
            assert "Database Connection String" in output

            # No REDACTED previews should appear (we're not showing them in enforcement mode)
            # but if they did, they must be the redacted form only
            if "REDACTED" in output:
                # Redacted form is safe
                assert "[REDACTED" in output
