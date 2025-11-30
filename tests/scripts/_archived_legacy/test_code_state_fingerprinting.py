#!/usr/bin/env python3
"""
Tests for Component 1: Code State Fingerprinting (P1T13-F5a).

Tests the code fingerprinting system that prevents code changes after review approval.
Uses mocking for fast, reliable tests.

Author: Claude Code
Date: 2025-11-13
"""

import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from scripts.workflow_gate import WorkflowGate


@pytest.fixture()
def temp_state_file():
    """Create temporary state file for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "workflow-state.json"
        with patch("scripts.workflow_gate.STATE_FILE", state_file):
            yield state_file


class TestComputeStagedHash:
    """Test _compute_staged_hash() method."""

    def test_hash_calculation_returns_consistent_value(self):
        """Test that hash calculation returns the same value for same changes."""
        gate = WorkflowGate()

        # Mock subprocess to return consistent diff output
        mock_result = MagicMock()
        mock_result.stdout = b"diff --git a/test.txt b/test.txt\n+new line\n"

        with patch("subprocess.run", return_value=mock_result):
            hash1 = gate._compute_staged_hash()
            hash2 = gate._compute_staged_hash()

            assert hash1 == hash2
            assert len(hash1) == 64  # SHA256 hex digest length

    def test_same_changes_produce_same_hash(self):
        """Test that same staged changes produce same hash."""
        gate = WorkflowGate()

        mock_result = MagicMock()
        mock_result.stdout = b"diff content"

        with patch("subprocess.run", return_value=mock_result):
            hash1 = gate._compute_staged_hash()
            hash2 = gate._compute_staged_hash()

            assert hash1 == hash2

    def test_different_changes_produce_different_hash(self):
        """Test that different staged changes produce different hash."""
        gate = WorkflowGate()

        # First change
        mock_result1 = MagicMock()
        mock_result1.stdout = b"diff change 1"

        with patch("subprocess.run", return_value=mock_result1):
            hash1 = gate._compute_staged_hash()

        # Second change
        mock_result2 = MagicMock()
        mock_result2.stdout = b"diff change 2"

        with patch("subprocess.run", return_value=mock_result2):
            hash2 = gate._compute_staged_hash()

        assert hash1 != hash2

    def test_empty_staged_changes_returns_empty_string(self):
        """Test that no staged changes returns empty string."""
        gate = WorkflowGate()

        mock_result = MagicMock()
        mock_result.stdout = b""  # No diff output

        with patch("subprocess.run", return_value=mock_result):
            hash_value = gate._compute_staged_hash()

            assert hash_value == ""

    def test_binary_files_included_in_hash(self):
        """Test that binary files are included in hash calculation."""
        gate = WorkflowGate()

        mock_result = MagicMock()
        mock_result.stdout = b"Binary files differ\x00\x01\x02"

        with patch("subprocess.run", return_value=mock_result):
            hash_value = gate._compute_staged_hash()

            assert hash_value != ""
            assert len(hash_value) == 64

    def test_git_flags_used_correctly(self):
        """Test that git command uses correct flags (Gemini HIGH fix)."""
        gate = WorkflowGate()

        mock_result = MagicMock()
        mock_result.stdout = b"diff"

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            gate._compute_staged_hash()

            # Verify correct flags were used
            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            assert "--no-pager" in args
            assert "--binary" in args
            assert "--no-color" in args
            assert "--no-ext-diff" in args


class TestRecordReviewFingerprinting:
    """Test record_review() stores hash correctly."""

    def test_hash_stored_in_zen_review(self, temp_state_file):
        """Test that hash is stored in zen_review on record_review()."""
        gate = WorkflowGate()

        # Set step to 'review' so fingerprinting is required
        state = gate.load_state()
        state["step"] = "review"
        gate.save_state(state)

        # Mock _compute_staged_hash to return a known hash
        with patch.object(gate, "_compute_staged_hash", return_value="abc123def456"):
            gate.record_review("test-cont-id", "APPROVED")

            state = gate.load_state()
            assert "staged_hash" in state["zen_review"]
            assert state["zen_review"]["staged_hash"] == "abc123def456"

    def test_hash_computed_before_persisting_approval(self, temp_state_file):
        """Test that hash failure prevents approval (Codex HIGH fix)."""
        gate = WorkflowGate()

        # Set step to 'review' so fingerprinting is required
        state = gate.load_state()
        state["step"] = "review"
        gate.save_state(state)

        # Mock _compute_staged_hash to fail
        with patch.object(gate, "_compute_staged_hash", side_effect=Exception("Git error")):
            with pytest.raises(SystemExit) as exc_info:
                gate.record_review("test-cont-id", "APPROVED")

            assert exc_info.value.code == 1

            # Approval should NOT be persisted
            state = gate.load_state()
            assert state["zen_review"].get("status") != "APPROVED"

    def test_empty_hash_blocks_approval(self, temp_state_file):
        """Test that empty hash (no staged files) blocks approval (Codex HIGH fix)."""
        gate = WorkflowGate()

        # Set step to 'review' so fingerprinting is required
        state = gate.load_state()
        state["step"] = "review"
        gate.save_state(state)

        # Mock _compute_staged_hash to return empty string
        with patch.object(gate, "_compute_staged_hash", return_value=""):
            with pytest.raises(SystemExit) as exc_info:
                gate.record_review("test-cont-id", "APPROVED")

            assert exc_info.value.code == 1

            # Approval should NOT be persisted
            state = gate.load_state()
            assert "zen_review" not in state or state["zen_review"].get("status") != "APPROVED"

    def test_plan_review_allowed_without_staged_changes(self, temp_state_file):
        """Test that plan reviews don't require staged changes (bug fix)."""
        gate = WorkflowGate()

        # Set step to 'plan-review' - no fingerprinting required
        state = gate.load_state()
        state["step"] = "plan-review"
        gate.save_state(state)

        # Should succeed even with no staged changes (no mock needed)
        gate.record_review("plan-review-cont-id", "APPROVED")

        # Approval should be persisted without hash
        state = gate.load_state()
        assert state["zen_review"]["status"] == "APPROVED"
        assert state["zen_review"]["continuation_id"] == "plan-review-cont-id"
        assert state["zen_review"]["staged_hash"] == ""  # Empty hash is OK for plan reviews


class TestCheckCommitFingerprinting:
    """Test check_commit() gate for fingerprinting."""

    def test_commit_blocked_when_hash_mismatch(self, temp_state_file):
        """Test that commit is blocked when hash doesn't match."""
        gate = WorkflowGate()

        # Setup state with stored hash
        state = gate.load_state()
        state["step"] = "review"
        state["zen_review"] = {
            "requested": True,
            "continuation_id": "test-cont-id",
            "status": "APPROVED",
            "staged_hash": "original_hash_abc123",
        }
        state["ci_passed"] = True
        gate.save_state(state)

        # Mock current hash to be different
        with patch.object(gate, "_compute_staged_hash", return_value="modified_hash_def456"):
            with pytest.raises(SystemExit) as exc_info:
                gate.check_commit()

            assert exc_info.value.code == 1

    def test_commit_allowed_when_hash_matches(self, temp_state_file, tmp_path):
        """Test that commit is allowed when hash matches."""
        # Mock audit log file with the test continuation IDs
        # Use UUID-like IDs to avoid placeholder detection
        audit_file = tmp_path / "audit.log"
        audit_file.write_text(
            '{"timestamp": "2025-11-14T00:00:00Z", "continuation_id": "abc123-gemini-cont-id"}\n'
            '{"timestamp": "2025-11-14T00:00:00Z", "continuation_id": "def456-codex-cont-id"}\n'
        )

        with patch("scripts.workflow_gate.AUDIT_LOG_FILE", audit_file):
            gate = WorkflowGate()

            # Setup state with stored hash and planning artifacts (NEW DUAL REVIEW FORMAT)
            state = gate.load_state()
            state["step"] = "review"
            state["first_commit_made"] = True  # Bypass planning artifact gate
            state["gemini_review"] = {
                "requested": True,
                "continuation_id": "abc123-gemini-cont-id",
                "status": "APPROVED",
                "staged_hash": "matching_hash_abc123",
            }
            state["codex_review"] = {
                "requested": True,
                "continuation_id": "def456-codex-cont-id",
                "status": "APPROVED",
                "staged_hash": "matching_hash_abc123",
            }
            state["ci_passed"] = True
            gate.save_state(state)

            # Mock current hash to match
            with patch.object(gate, "_compute_staged_hash", return_value="matching_hash_abc123"):
                with pytest.raises(SystemExit) as exc_info:
                    gate.check_commit()

                assert exc_info.value.code == 0

    def test_backwards_compatibility_no_stored_hash(self, temp_state_file, tmp_path):
        """Test backwards compatibility: allow commit if no hash stored (Gemini LOW)."""
        # Mock audit log file with the test continuation IDs
        audit_file = tmp_path / "audit.log"
        audit_file.write_text(
            '{"timestamp": "2025-11-14T00:00:00Z", "continuation_id": "old-gemini-review-id"}\n'
            '{"timestamp": "2025-11-14T00:00:00Z", "continuation_id": "old-codex-review-id"}\n'
        )

        with patch("scripts.workflow_gate.AUDIT_LOG_FILE", audit_file):
            gate = WorkflowGate()

            # Setup state without staged_hash (old review) - NEW DUAL REVIEW FORMAT
            state = gate.load_state()
            state["step"] = "review"
            state["first_commit_made"] = True  # Bypass planning artifact gate
            state["gemini_review"] = {
                "requested": True,
                "continuation_id": "old-gemini-review-id",
                "status": "APPROVED",
                # No staged_hash field
            }
            state["codex_review"] = {
                "requested": True,
                "continuation_id": "old-codex-review-id",
                "status": "APPROVED",
                # No staged_hash field
            }
            state["ci_passed"] = True
            gate.save_state(state)

            # check_commit should succeed (backwards compat)
            with pytest.raises(SystemExit) as exc_info:
                gate.check_commit()

            assert exc_info.value.code == 0

    def test_git_command_failure_handled(self, temp_state_file):
        """Test that git command failure is handled gracefully (Gemini LOW)."""
        gate = WorkflowGate()

        # Setup state with stored hash
        state = gate.load_state()
        state["step"] = "review"
        state["first_commit_made"] = True  # Bypass planning artifact gate
        state["zen_review"] = {
            "requested": True,
            "continuation_id": "test-id",
            "status": "APPROVED",
            "staged_hash": "dummy_hash",
        }
        state["ci_passed"] = True
        gate.save_state(state)

        # Mock _compute_staged_hash to fail
        with patch.object(gate, "_compute_staged_hash", side_effect=Exception("Git not found")):
            with pytest.raises(SystemExit) as exc_info:
                gate.check_commit()

            assert exc_info.value.code == 1

    def test_empty_hash_defensive_check(self, temp_state_file):
        """Test that check_commit rejects reviews with empty hash (Codex HIGH fix)."""
        gate = WorkflowGate()

        # Setup state with empty staged_hash (defensive check)
        state = gate.load_state()
        state["step"] = "review"
        state["first_commit_made"] = True  # Bypass planning artifact gate
        state["zen_review"] = {
            "requested": True,
            "continuation_id": "test-id",
            "status": "APPROVED",
            "staged_hash": "",  # Empty hash (should be blocked)
        }
        state["ci_passed"] = True
        gate.save_state(state)

        # check_commit should block
        with pytest.raises(SystemExit) as exc_info:
            gate.check_commit()

        assert exc_info.value.code == 1


class TestHelperScript:
    """Test compute_review_hash.py helper script."""

    def test_helper_script_exists(self):
        """Test that helper script exists and is executable."""
        script_path = Path(__file__).parent.parent.parent / "scripts" / "compute_review_hash.py"
        assert script_path.exists()
        assert script_path.stat().st_mode & 0o111  # Check executable bit

    def test_helper_script_single_source_of_truth(self):
        """Test that helper imports from WorkflowGate (Codex MEDIUM fix)."""
        script_path = Path(__file__).parent.parent.parent / "scripts" / "compute_review_hash.py"
        script_content = script_path.read_text()

        # Verify it imports WorkflowGate (single source of truth)
        assert "from scripts.workflow_gate import WorkflowGate" in script_content
        assert "gate._compute_staged_hash()" in script_content


# Mark as unit test
pytestmark = pytest.mark.unit
