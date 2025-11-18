"""
Tests for verify_branch_protection.py script

Component A2.1 (P1T13-F5 Phase A.2): Branch Protection Setup and Verification
Tests the branch protection verification script that checks GitHub API for required status checks.

Author: Claude Code
Date: 2025-11-16
Task: P1T13-F5 Phase A.2 Component 1
"""

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Import the function we're testing (Gemini LOW fix: remove unnecessary sys.path)
from scripts.verify_branch_protection import check_branch_protection


class TestBranchProtectionVerification:
    """Test suite for branch protection verification script."""

    def test_branch_not_protected(self):
        """Test handling of unprotected branch (404 response)."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "Branch not protected"
        mock_result.stdout = ""

        with patch("subprocess.run", return_value=mock_result):
            result = check_branch_protection()

        assert result == 1  # Should exit with error code 1

    def test_branch_protection_correct(self):
        """Test successful verification when branch protection is correctly configured."""
        protection_data = {
            "required_status_checks": {
                "strict": True,
                "contexts": ["Run tests and check coverage"],
            },
            "enforce_admins": {"enabled": True},
        }

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(protection_data)
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result):
            result = check_branch_protection()

        assert result == 0  # Should exit successfully

    def test_branch_protection_missing_required_check(self):
        """Test detection when required check is not in the list."""
        protection_data = {
            "required_status_checks": {
                "strict": True,
                "contexts": ["Some other check", "Another check"],
            }
        }

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(protection_data)
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result):
            result = check_branch_protection()

        assert result == 1  # Should exit with error

    def test_branch_protection_no_status_checks(self):
        """Test detection when status checks are not required at all."""
        protection_data = {"required_status_checks": None}

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(protection_data)
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result):
            result = check_branch_protection()

        assert result == 1  # Should exit with error

    def test_github_api_error(self):
        """Test handling of GitHub API errors (not 404)."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "HTTP 500: Internal Server Error"
        mock_result.stdout = ""

        with patch("subprocess.run", return_value=mock_result):
            result = check_branch_protection()

        assert result == 2  # Should exit with API error code

    def test_invalid_json_response(self):
        """Test handling of malformed JSON from GitHub API."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "not valid json {"
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result):
            result = check_branch_protection()

        assert result == 2  # Should exit with error code

    def test_gh_cli_not_found(self):
        """Test handling when gh CLI is not installed."""
        with patch("subprocess.run", side_effect=FileNotFoundError()):
            result = check_branch_protection()

        assert result == 2  # Should exit with error code

    def test_newer_api_format_with_checks(self):
        """Test handling of newer GitHub API format using 'checks' field."""
        protection_data = {
            "required_status_checks": {
                "strict": True,
                "contexts": [],  # Empty in newer format
                "checks": [
                    {"context": "Run tests and check coverage"},
                    {"context": "Another check"},
                ],
            }
        }

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(protection_data)
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result):
            result = check_branch_protection()

        assert result == 0  # Should exit successfully

    def test_branch_protection_without_strict_mode(self):
        """Test that script still passes even if 'strict' mode is disabled."""
        protection_data = {
            "required_status_checks": {
                "strict": False,  # Not required to be up-to-date
                "contexts": ["Run tests and check coverage"],
            }
        }

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(protection_data)
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result):
            result = check_branch_protection()

        assert result == 0  # Should still pass (strict mode is recommended, not required)


class TestBranchProtectionScript:
    """Integration tests for the verify_branch_protection.py script."""

    def test_script_runs_without_errors(self):
        """Test that the script can be executed (will show actual protection status)."""
        # This is a smoke test - it will actually call GitHub API
        # It should not crash, though it may return 0, 1, or 2 depending on actual state
        result = subprocess.run(
            [sys.executable, "scripts/verify_branch_protection.py"],
            capture_output=True,
            text=True,
        )

        # Should exit with valid code (0, 1, or 2)
        assert result.returncode in [0, 1, 2]

        # Should produce output
        assert len(result.stdout) > 0

    def test_script_has_executable_permissions(self):
        """Test that the script has executable permissions."""
        script_path = Path("scripts/verify_branch_protection.py")
        assert script_path.exists()

        # Check if executable bit is set
        import stat

        st = script_path.stat()
        is_executable = bool(st.st_mode & stat.S_IXUSR)
        assert is_executable, "Script should have executable permissions"


# Acceptance criteria validation tests
class TestAcceptanceCriteria:
    """Tests that verify acceptance criteria from Component A2.1 plan."""

    def test_ac_script_checks_protection_via_api(self):
        """AC: Script checks protection via GitHub API."""
        # Verify the script calls gh API command
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1, stderr="Branch not protected", stdout=""
            )
            check_branch_protection()

            # Verify gh API was called
            assert mock_run.called
            call_args = mock_run.call_args[0][0]
            assert "gh" in call_args
            assert "api" in call_args
            assert "branches/master/protection" in " ".join(call_args)

    def test_ac_clear_error_messages(self):
        """AC: Clear error messages if misconfigured."""
        # Test that error output contains helpful information
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "Branch not protected"
        mock_result.stdout = ""

        with patch("subprocess.run", return_value=mock_result):
            with patch("builtins.print") as mock_print:
                check_branch_protection()

                # Verify helpful error messages were printed
                # Extract all print calls - handle both positional and keyword args
                printed_text = " ".join(
                    str(call.args[0]) if call.args else str(call.kwargs.get("", ""))
                    for call in mock_print.call_args_list
                )
                assert "branch protection" in printed_text.lower()
                assert "action required" in printed_text.lower() or "settings" in printed_text.lower()

    def test_ac_returns_correct_exit_codes(self):
        """AC: Returns exit code 0/1 appropriately."""
        # Test exit code 0 (success)
        protection_data = {
            "required_status_checks": {
                "contexts": ["Run tests and check coverage"]
            }
        }
        mock_result = MagicMock(
            returncode=0, stdout=json.dumps(protection_data), stderr=""
        )

        with patch("subprocess.run", return_value=mock_result):
            assert check_branch_protection() == 0

        # Test exit code 1 (misconfigured)
        mock_result = MagicMock(
            returncode=1, stderr="Branch not protected", stdout=""
        )

        with patch("subprocess.run", return_value=mock_result):
            assert check_branch_protection() == 1

    def test_ac_handles_api_errors_gracefully(self):
        """AC: Handles API errors gracefully."""
        # Test various error conditions
        test_cases = [
            (FileNotFoundError(), 2, "gh CLI not found"),
            (Exception("Unexpected error"), 2, "Unexpected error"),
            (
                MagicMock(returncode=1, stderr="HTTP 500", stdout=""),
                2,
                "API error",
            ),
        ]

        for error, expected_code, description in test_cases:
            with patch("subprocess.run") as mock_run:
                if isinstance(error, Exception):
                    mock_run.side_effect = error
                else:
                    mock_run.return_value = error

                result = check_branch_protection()
                assert result == expected_code, f"Failed for case: {description}"


if __name__ == "__main__":
    # Run tests with pytest
    pytest.main([__file__, "-v"])
