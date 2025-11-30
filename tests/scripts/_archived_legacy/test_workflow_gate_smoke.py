#!/usr/bin/env python3
"""
Smoke tests for workflow_gate.py - validates basic functionality.

These tests verify that the workflow gate system can:
- Initialize state
- Perform state transitions
- Record approvals and CI results
- Block invalid transitions

Author: Claude Code
Date: 2025-11-02
"""

# Import the WorkflowGate class
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


def test_initial_state(temp_state_file):
    """Verify the initial state is 'plan' (Phase 1)."""
    gate = WorkflowGate()
    state = gate.load_state()
    assert state["step"] == "plan"  # Phase 1: starts with planning step
    assert state["current_component"] == ""
    assert state["ci_passed"] is False
    assert not state["zen_review"]
    # Phase 1: Verify planning fields exist
    assert "task_file" in state
    assert "analysis_completed" in state
    assert "components" in state
    assert "first_commit_made" in state
    assert "context_cache" in state


def test_set_component(temp_state_file):
    """Verify setting the component name."""
    gate = WorkflowGate()
    gate.set_component("Test Component")
    state = gate.load_state()
    assert state["current_component"] == "Test Component"


def test_valid_state_transitions(temp_state_file):
    """Verify valid state transitions in the workflow using advance()."""
    gate = WorkflowGate()
    gate.set_component("Test Component")

    # Phase 1.5: Start from "plan" step, advance to "plan-review"
    gate.advance("plan-review")
    state = gate.load_state()
    assert state["step"] == "plan-review"

    # Approve plan review to allow advancement to implement
    gate.record_review("test-plan-cont-id", "APPROVED")
    gate.advance("implement")
    state = gate.load_state()
    assert state["step"] == "implement"

    # Mock _has_tests to allow transition to review
    with patch.object(WorkflowGate, "_has_tests", return_value=True):
        # implement → test
        gate.advance("test")
        state = gate.load_state()
        assert state["step"] == "test"

        # test → review
        gate.advance("review")
        state = gate.load_state()
        assert state["step"] == "review"


def test_invalid_state_transition_is_blocked(temp_state_file):
    """Verify that an invalid transition (e.g., plan to review) is blocked."""
    gate = WorkflowGate()
    gate.set_component("Test Component")

    # Phase 1: Initial state is "plan"
    with pytest.raises(SystemExit) as excinfo:
        gate.advance("review")  # Invalid transition from "plan"

    assert excinfo.value.code == 1
    state = gate.load_state()
    assert state["step"] == "plan"  # State should not have changed


def test_record_review_and_ci(temp_state_file):
    """Verify recording review and CI status."""
    gate = WorkflowGate()
    gate.record_review("test-id-123", "APPROVED")
    gate.record_ci(True)

    state = gate.load_state()
    assert state["zen_review"]["status"] == "APPROVED"
    assert state["zen_review"]["continuation_id"] == "test-id-123"
    assert state["ci_passed"] is True


def test_check_commit_blocks_when_wrong_step(temp_state_file):
    """Verify check_commit blocks when not in review step."""
    gate = WorkflowGate()
    gate.set_component("Test Component")
    # Phase 1: Still in "plan" step

    with pytest.raises(SystemExit) as excinfo:
        gate.check_commit()

    assert excinfo.value.code == 1


def test_check_commit_blocks_when_review_not_approved(temp_state_file):
    """Verify check_commit blocks when review is not approved."""
    gate = WorkflowGate()
    gate.set_component("Test Component")

    # Phase 1: Set first_commit_made to bypass planning gate
    state = gate.load_state()
    state["first_commit_made"] = True
    gate.save_state(state)

    # Phase 1.5: Transition through plan-review to implement
    gate.advance("plan-review")
    gate.record_review("test-plan-cont-id", "APPROVED")
    gate.advance("implement")

    # Mock _has_tests to allow transition to review
    with patch.object(WorkflowGate, "_has_tests", return_value=True):
        gate.advance("test")
        gate.advance("review")

    # Record review as NEEDS_REVISION
    # P1T13-F5a: Mock hash computation for unit test
    with patch.object(gate, "_compute_staged_hash", return_value="fake_hash_123"):
        gate.record_review("test-id-123", "NEEDS_REVISION")
    gate.record_ci(True)

    with pytest.raises(SystemExit) as excinfo:
        gate.check_commit()

    assert excinfo.value.code == 1


def test_check_commit_blocks_when_ci_failed(temp_state_file):
    """Verify check_commit blocks when CI has not passed."""
    gate = WorkflowGate()
    gate.set_component("Test Component")

    # Phase 1: Set first_commit_made to bypass planning gate
    state = gate.load_state()
    state["first_commit_made"] = True
    gate.save_state(state)

    # Phase 1.5: Transition through plan-review to implement
    gate.advance("plan-review")
    gate.record_review("test-plan-cont-id", "APPROVED")
    gate.advance("implement")

    # Mock _has_tests to allow transition to review
    with patch.object(WorkflowGate, "_has_tests", return_value=True):
        gate.advance("test")
        gate.advance("review")

    # P1T13-F5a: Mock hash computation for unit test
    with patch.object(gate, "_compute_staged_hash", return_value="fake_hash_123"):
        gate.record_review("test-id-123", "APPROVED")
    gate.record_ci(False)

    with pytest.raises(SystemExit) as excinfo:
        gate.check_commit()

    assert excinfo.value.code == 1


def test_check_commit_success_when_all_prerequisites_met(temp_state_file, tmp_path):
    """Verify check_commit succeeds when all prerequisites are met (DUAL REVIEW FORMAT)."""
    # P1T13-F5a: Mock audit log for continuation ID verification (DUAL REVIEW)
    audit_file = tmp_path / "audit.log"
    audit_file.write_text(
        '{"timestamp": "2025-11-14T00:00:00Z", "continuation_id": "smoke-test-gemini-123"}\n'
        '{"timestamp": "2025-11-14T00:00:00Z", "continuation_id": "smoke-test-codex-456"}\n'
    )

    with patch("scripts.workflow_gate.AUDIT_LOG_FILE", audit_file):
        gate = WorkflowGate()
        gate.set_component("Test Component")

        # Phase 1: Set first_commit_made to bypass planning gate
        state = gate.load_state()
        state["first_commit_made"] = True
        gate.save_state(state)

        # Phase 1.5: Transition through plan-review to implement
        gate.advance("plan-review")
        gate.record_review("test-plan-cont-id", "APPROVED")
        gate.advance("implement")

        # Mock _has_tests to allow transition to review
        with patch.object(WorkflowGate, "_has_tests", return_value=True):
            gate.advance("test")
            gate.advance("review")

        # P1T13-F5a: Mock hash computation for unit test (DUAL REVIEW - record both gemini and codex)
        with patch.object(gate, "_compute_staged_hash", return_value="fake_hash_123"):
            gate.record_review("smoke-test-gemini-123", "APPROVED", "gemini")
            gate.record_review("smoke-test-codex-456", "APPROVED", "codex")
        gate.record_ci(True)

        # P1T13-F5a: Mock hash computation for check_commit too
        with patch.object(gate, "_compute_staged_hash", return_value="fake_hash_123"):
            with pytest.raises(SystemExit) as excinfo:
                gate.check_commit()

        assert excinfo.value.code == 0


def test_record_commit_resets_state_and_appends_history(temp_state_file):
    """Verify record_commit resets state and manages commit history."""
    gate = WorkflowGate()
    gate.set_component("Test Component")

    # Mock git rev-parse to return a fake commit hash
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.stdout = "abc123def456\n"
        mock_run.return_value.returncode = 0

        gate.record_commit(update_task_state=False)

    state = gate.load_state()

    # Verify state was reset (Phase 1.5: resets to "plan" not "implement")
    assert state["step"] == "plan"
    assert state["zen_review"] == {}
    assert state["ci_passed"] is False

    # Verify commit was recorded in history
    assert "abc123def456" in state["commit_history"]
    # Verify deprecated last_commit_hash field was removed (state hygiene)
    assert "last_commit_hash" not in state


def test_record_commit_prunes_history_beyond_100_commits(temp_state_file):
    """Verify record_commit prunes history to last 100 commits."""
    gate = WorkflowGate()

    # Pre-populate with 105 commits
    state = gate.load_state()
    state["commit_history"] = [f"commit{i}" for i in range(105)]
    gate.save_state(state)

    # Record one more commit
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.stdout = "newest_commit\n"
        mock_run.return_value.returncode = 0

        gate.record_commit(update_task_state=False)

    state = gate.load_state()

    # Should have exactly 100 commits (oldest 6 pruned)
    assert len(state["commit_history"]) == 100
    assert state["commit_history"][-1] == "newest_commit"
    assert "commit0" not in state["commit_history"]  # Oldest ones pruned


# Marker to indicate these are smoke tests for infrastructure
pytestmark = pytest.mark.unit
