#!/usr/bin/env python3
"""
Tests for Component 3: Continuation ID Verification (P1T13-F5a).

Tests the audit logging and placeholder ID detection system.

Author: Claude Code
Date: 2025-11-13
"""

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

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


class TestPlaceholderDetection:
    """Test placeholder continuation ID detection."""

    def test_empty_id_detected_as_placeholder(self):
        """Test that empty ID is detected as placeholder."""
        gate = WorkflowGate()
        assert gate._is_placeholder_id("") is True

    def test_test_prefix_detected(self):
        """Test that 'test-' prefix is detected."""
        gate = WorkflowGate()
        assert gate._is_placeholder_id("test-12345") is True
        assert gate._is_placeholder_id("TEST-ABCDE") is True  # Case insensitive

    def test_placeholder_prefix_detected(self):
        """Test that 'placeholder-' prefix is detected."""
        gate = WorkflowGate()
        assert gate._is_placeholder_id("placeholder-abc") is True
        assert gate._is_placeholder_id("PLACEHOLDER-xyz") is True

    def test_fake_prefix_detected(self):
        """Test that 'fake-' prefix is detected."""
        gate = WorkflowGate()
        assert gate._is_placeholder_id("fake-review-id") is True

    def test_dummy_and_mock_detected(self):
        """Test that 'dummy-' and 'mock-' prefixes are detected."""
        gate = WorkflowGate()
        assert gate._is_placeholder_id("dummy-id") is True
        assert gate._is_placeholder_id("mock-continuation-id") is True

    def test_real_uuid_not_placeholder(self):
        """Test that real UUIDs are not detected as placeholders."""
        gate = WorkflowGate()
        # Real continuation IDs (UUID format)
        assert gate._is_placeholder_id("3acbf143-fa76-4aad-905e-69ca6942c0ac") is False
        assert gate._is_placeholder_id("2aac186e-82d1-40e5-ba9b-6a23f7ee669c") is False

    def test_alphanumeric_real_id_not_placeholder(self):
        """Test that alphanumeric real IDs are not flagged."""
        gate = WorkflowGate()
        assert gate._is_placeholder_id("abc123def456") is False
        assert (
            gate._is_placeholder_id("review-abc123") is False
        )  # Doesn't start with blocked prefix


class TestAuditLogging:
    """Test audit log creation."""

    def test_audit_log_created(self, tmp_path):
        """Test that audit log file is created."""
        audit_file = tmp_path / "workflow-audit.log"

        with patch("scripts.workflow_gate.AUDIT_LOG_FILE", audit_file):
            gate = WorkflowGate()
            gate._log_to_audit("test-id-12345")

            assert audit_file.exists()

    def test_audit_log_contains_json(self, tmp_path):
        """Test that audit log contains valid JSON entries."""
        audit_file = tmp_path / "workflow-audit.log"

        with patch("scripts.workflow_gate.AUDIT_LOG_FILE", audit_file):
            gate = WorkflowGate()
            gate._log_to_audit("abc-123-def")

            content = audit_file.read_text()
            entry = json.loads(content.strip())

            assert "timestamp" in entry
            assert "continuation_id" in entry
            assert entry["continuation_id"] == "abc-123-def"

    def test_multiple_entries_appended(self, tmp_path):
        """Test that multiple log entries are appended."""
        audit_file = tmp_path / "workflow-audit.log"

        with patch("scripts.workflow_gate.AUDIT_LOG_FILE", audit_file):
            gate = WorkflowGate()
            gate._log_to_audit("first-id")
            gate._log_to_audit("second-id")

            lines = audit_file.read_text().strip().split("\n")
            assert len(lines) == 2

            entry1 = json.loads(lines[0])
            entry2 = json.loads(lines[1])

            assert entry1["continuation_id"] == "first-id"
            assert entry2["continuation_id"] == "second-id"


class TestAuditLogVerification:
    """Test audit log verification (Codex P1 fix)."""

    def test_continuation_id_found_in_audit_log(self, tmp_path):
        """Test that continuation ID in audit log is verified."""
        audit_file = tmp_path / "workflow-audit.log"
        audit_file.write_text(
            '{"timestamp": "2025-11-14T00:00:00Z", "continuation_id": "abc-123"}\n'
        )

        with patch("scripts.workflow_gate.AUDIT_LOG_FILE", audit_file):
            gate = WorkflowGate()
            assert gate._is_continuation_id_in_audit_log("abc-123") is True

    def test_continuation_id_not_found_in_audit_log(self, tmp_path):
        """Test that missing continuation ID returns False."""
        audit_file = tmp_path / "workflow-audit.log"
        audit_file.write_text(
            '{"timestamp": "2025-11-14T00:00:00Z", "continuation_id": "abc-123"}\n'
        )

        with patch("scripts.workflow_gate.AUDIT_LOG_FILE", audit_file):
            gate = WorkflowGate()
            assert gate._is_continuation_id_in_audit_log("different-id") is False

    def test_no_audit_log_returns_false(self, tmp_path):
        """Test that missing audit log file returns False (not error)."""
        audit_file = tmp_path / "workflow-audit.log"

        with patch("scripts.workflow_gate.AUDIT_LOG_FILE", audit_file):
            gate = WorkflowGate()
            assert gate._is_continuation_id_in_audit_log("any-id") is False

    def test_malformed_audit_log_lines_skipped(self, tmp_path):
        """Test that malformed JSON lines are gracefully skipped."""
        audit_file = tmp_path / "workflow-audit.log"
        audit_file.write_text(
            "invalid json line\n"
            '{"timestamp": "2025-11-14T00:00:00Z", "continuation_id": "valid-id"}\n'
            "another bad line\n"
        )

        with patch("scripts.workflow_gate.AUDIT_LOG_FILE", audit_file):
            gate = WorkflowGate()
            assert gate._is_continuation_id_in_audit_log("valid-id") is True
            assert gate._is_continuation_id_in_audit_log("invalid") is False

    def test_multiple_entries_searched(self, tmp_path):
        """Test that all audit log entries are searched."""
        audit_file = tmp_path / "workflow-audit.log"
        audit_file.write_text(
            '{"timestamp": "2025-11-14T00:00:00Z", "continuation_id": "first-id"}\n'
            '{"timestamp": "2025-11-14T00:01:00Z", "continuation_id": "second-id"}\n'
            '{"timestamp": "2025-11-14T00:02:00Z", "continuation_id": "third-id"}\n'
        )

        with patch("scripts.workflow_gate.AUDIT_LOG_FILE", audit_file):
            gate = WorkflowGate()
            assert gate._is_continuation_id_in_audit_log("first-id") is True
            assert gate._is_continuation_id_in_audit_log("second-id") is True
            assert gate._is_continuation_id_in_audit_log("third-id") is True
            assert gate._is_continuation_id_in_audit_log("fourth-id") is False


class TestAuditLogFailClosed:
    """Test fail-closed behavior for audit log (Gemini HIGH + Codex P1 fixes)."""

    def test_audit_log_write_failure_raises_exception(self, tmp_path):
        """Test that audit log write failures raise exception (Gemini HIGH fix)."""
        # Create a read-only directory to force write failure
        audit_dir = tmp_path / "readonly"
        audit_dir.mkdir()
        audit_file = audit_dir / "workflow-audit.log"

        # Make directory read-only
        audit_dir.chmod(0o444)

        with patch("scripts.workflow_gate.AUDIT_LOG_FILE", audit_file):
            gate = WorkflowGate()

            # Should raise exception, not warn
            with pytest.raises(PermissionError):
                gate._log_to_audit("test-id")

        # Cleanup
        audit_dir.chmod(0o755)

    def test_missing_audit_log_blocks_commit_after_first(self, temp_state_file):
        """Test that missing audit log blocks commit after first commit (Codex P1 fix)."""
        gate = WorkflowGate()

        # Setup state with first_commit_made=True
        state = gate.load_state()
        state["step"] = "review"
        state["first_commit_made"] = True  # This means audit log MUST exist
        state["zen_review"] = {
            "requested": True,
            "continuation_id": "test-id",
            "status": "APPROVED",
            "staged_hash": "dummy_hash",
        }
        state["ci_passed"] = True
        gate.save_state(state)

        # Ensure audit log does NOT exist
        with patch("scripts.workflow_gate.AUDIT_LOG_FILE") as mock_audit_path:
            mock_audit_path.exists.return_value = False
            mock_audit_path.__str__.return_value = "/path/to/audit.log"

            # check_commit should block
            with pytest.raises(SystemExit) as exc_info:
                gate.check_commit()

            assert exc_info.value.code == 1


# Mark as unit test
pytestmark = pytest.mark.unit
