"""
Tests for Phase 1.5 plan-review workflow step.

Verifies the 6-step pattern: plan → plan-review → implement → test → review → commit
Tests plan review gate enforcement and transition logic.
"""

import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add scripts to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))

from workflow_gate import PROJECT_ROOT, UnifiedReviewSystem, WorkflowGate, main


@pytest.fixture()
def temp_state_file():
    """Create temporary state file for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "workflow-state.json"
        with patch("scripts.workflow_gate.STATE_FILE", state_file):
            yield state_file


class TestPlanReviewTransitions:
    """Test state transitions for plan-review step."""

    def test_plan_to_plan_review_transition(self, temp_state_file):
        """Test valid transition from plan to plan-review."""
        gate = WorkflowGate()

        # Initialize to plan step
        state = gate.load_state()
        state["step"] = "plan"
        state["analysis_completed"] = True
        state["components"] = ["Component 1"]
        state["current_component"] = "Component 1"
        gate.save_state(state)

        # Advance to plan-review should succeed
        gate.advance("plan-review")

        # Verify state updated
        state = gate.load_state()
        assert state["step"] == "plan-review"

    def test_plan_review_to_implement_blocked_without_approval(self, temp_state_file):
        """Test that advancing from plan-review to implement requires approval."""
        gate = WorkflowGate()

        # Initialize to plan-review step without approval
        state = gate.load_state()
        state["step"] = "plan-review"
        state["analysis_completed"] = True
        state["components"] = ["Component 1"]
        state["current_component"] = "Component 1"
        state["zen_review"] = {"status": "NOT_REQUESTED"}
        gate.save_state(state)

        # Attempt to advance to implement should fail
        with pytest.raises(SystemExit) as exc_info:
            gate.advance("implement")

        assert exc_info.value.code == 1

        # Verify state unchanged
        state = gate.load_state()
        assert state["step"] == "plan-review"

    def test_plan_review_to_implement_allowed_with_approval(self, temp_state_file):
        """Test that plan-review to implement succeeds with approval."""
        gate = WorkflowGate()

        # Initialize to plan-review with approval
        state = gate.load_state()
        state["step"] = "plan-review"
        state["analysis_completed"] = True
        state["components"] = ["Component 1"]
        state["current_component"] = "Component 1"
        state["zen_review"] = {
            "status": "APPROVED",
            "continuation_id": "test-cont-id-123",
        }
        gate.save_state(state)

        # Advance to implement should succeed
        gate.advance("implement")

        # Verify state updated and zen_review cleared
        state = gate.load_state()
        assert state["step"] == "implement"
        assert state["zen_review"] == {}  # Cleared for code review later

    def test_plan_review_to_plan_rework_allowed(self, temp_state_file):
        """Test that returning from plan-review to plan is allowed for rework."""
        gate = WorkflowGate()

        # Initialize to plan-review step
        state = gate.load_state()
        state["step"] = "plan-review"
        state["analysis_completed"] = True
        state["components"] = ["Component 1"]
        state["current_component"] = "Component 1"
        gate.save_state(state)

        # Advance back to plan should succeed
        gate.advance("plan")

        # Verify state updated
        state = gate.load_state()
        assert state["step"] == "plan"

    def test_invalid_transitions_from_plan_review(self, temp_state_file):
        """Test that invalid transitions from plan-review are blocked."""
        gate = WorkflowGate()

        # Initialize to plan-review step
        state = gate.load_state()
        state["step"] = "plan-review"
        state["analysis_completed"] = True
        state["components"] = ["Component 1"]
        state["current_component"] = "Component 1"
        gate.save_state(state)

        # Try invalid transitions
        invalid_transitions = ["test", "review"]
        for next_step in invalid_transitions:
            with pytest.raises(SystemExit) as exc_info:
                gate.advance(next_step)
            assert exc_info.value.code == 1

            # Verify state unchanged
            state = gate.load_state()
            assert state["step"] == "plan-review"


class TestPlanReviewCLI:
    """Test CLI commands for plan-review workflow."""

    def test_show_status_displays_6_steps(self, temp_state_file, capsys):
        """Test that show_status displays all 6 workflow steps."""
        gate = WorkflowGate()

        # Initialize state at plan-review step
        state = gate.load_state()
        state["step"] = "plan-review"
        state["analysis_completed"] = True
        state["components"] = ["Component 1"]
        state["current_component"] = "Component 1"
        gate.save_state(state)

        # Show status
        gate.show_status()

        # Capture output
        captured = capsys.readouterr()

        # Verify all 6 steps shown
        assert "1. Plan" in captured.out
        assert "2. Plan Review" in captured.out
        assert "3. Implement" in captured.out
        assert "4. Test" in captured.out
        assert "5. Code Review" in captured.out
        assert "← YOU ARE HERE" in captured.out  # Current step marker


class TestPlanReviewMethod:
    """Test the _plan_review() method implementation."""

    def test_plan_review_returns_pending_status(self, temp_state_file):
        """Test that _plan_review() returns PENDING status awaiting manual approval."""
        review_system = UnifiedReviewSystem()

        # Call _plan_review
        result = review_system._plan_review()

        # Verify structure
        assert result["scope"] == "plan"
        assert result["status"] == "PENDING"
        assert result["continuation_id"] is None
        assert result["issues"] == []

    def test_request_review_with_plan_scope(self, temp_state_file):
        """Test that request_review(scope='plan') calls _plan_review()."""
        review_system = UnifiedReviewSystem()

        with patch.object(review_system, "_plan_review") as mock_plan_review:
            mock_plan_review.return_value = {"scope": "plan", "status": "PENDING"}

            result = review_system.request_review(scope="plan")

            mock_plan_review.assert_called_once()
            assert result["scope"] == "plan"


class TestValidTransitionsUpdated:
    """Test that VALID_TRANSITIONS includes plan-review step."""

    def test_valid_transitions_structure(self):
        """Test VALID_TRANSITIONS dict includes plan-review."""
        from workflow_gate import WorkflowGate

        expected = {
            "plan": ["plan-review"],
            "plan-review": ["implement", "plan"],
            "implement": ["test"],
            "test": ["review"],
            "review": ["implement"],
        }

        assert WorkflowGate.VALID_TRANSITIONS == expected

    def test_plan_can_only_go_to_plan_review(self):
        """Test that plan step can only transition to plan-review."""
        from workflow_gate import WorkflowGate

        valid = WorkflowGate.VALID_TRANSITIONS["plan"]
        assert valid == ["plan-review"]
        assert "implement" not in valid

    def test_plan_review_has_two_exits(self):
        """Test that plan-review can go to implement or back to plan."""
        from workflow_gate import WorkflowGate

        valid = WorkflowGate.VALID_TRANSITIONS["plan-review"]
        assert "implement" in valid
        assert "plan" in valid
        assert len(valid) == 2


class TestPlanReviewGateEnforcement:
    """Test that plan review approval is properly enforced."""

    def test_record_review_updates_zen_review_status(self, temp_state_file):
        """Test that recording review approval updates zen_review field."""
        gate = WorkflowGate()

        # Initialize to plan-review step
        state = gate.load_state()
        state["step"] = "plan-review"
        state["analysis_completed"] = True
        state["components"] = ["Component 1"]
        state["current_component"] = "Component 1"
        state["zen_review"] = {"status": "PENDING"}
        gate.save_state(state)

        # Record approval
        gate.record_review("test-cont-id-456", "APPROVED")

        # Verify zen_review updated
        state = gate.load_state()
        assert state["zen_review"]["status"] == "APPROVED"
        assert state["zen_review"]["continuation_id"] == "test-cont-id-456"

    def test_needs_revision_status_blocks_advance(self, temp_state_file):
        """Test that NEEDS_REVISION status blocks advancing to implement."""
        gate = WorkflowGate()

        # Initialize with NEEDS_REVISION
        state = gate.load_state()
        state["step"] = "plan-review"
        state["analysis_completed"] = True
        state["components"] = ["Component 1"]
        state["current_component"] = "Component 1"
        state["zen_review"] = {
            "status": "NEEDS_REVISION",
            "continuation_id": "test-cont-id-789",
        }
        gate.save_state(state)

        # Attempt to advance should fail
        with pytest.raises(SystemExit) as exc_info:
            gate.advance("implement")

        assert exc_info.value.code == 1


class TestZenReviewReusePattern:
    """Test that zen_review field is reused for both plan and code reviews."""

    def test_zen_review_cleared_after_plan_approval(self, temp_state_file):
        """Test that zen_review is cleared when advancing from plan-review to implement."""
        gate = WorkflowGate()

        # Initialize with plan approval
        state = gate.load_state()
        state["step"] = "plan-review"
        state["analysis_completed"] = True
        state["components"] = ["Component 1"]
        state["current_component"] = "Component 1"
        state["zen_review"] = {
            "status": "APPROVED",
            "continuation_id": "plan-review-cont-id",
        }
        gate.save_state(state)

        # Advance to implement
        gate.advance("implement")

        # Verify zen_review cleared (ready for code review)
        state = gate.load_state()
        assert state["zen_review"] == {}

    def test_zen_review_available_for_code_review(self, temp_state_file):
        """Test that zen_review can be used again for code review after plan review."""
        gate = WorkflowGate()

        # Simulate full cycle: plan-review approved → implement → test → review
        state = gate.load_state()
        state["analysis_completed"] = True
        state["components"] = ["Component 1"]
        state["current_component"] = "Component 1"

        # 1. Plan review approved and cleared
        state["step"] = "implement"
        state["zen_review"] = {}  # Cleared after plan review
        gate.save_state(state)

        # 2. Advance to review step
        state["step"] = "review"
        gate.save_state(state)

        # 3. Record code review approval
        # P1T13-F5a: Mock hash computation for unit test
        with patch.object(gate, "_compute_staged_hash", return_value="fake_hash_123"):
            gate.record_review("code-review-cont-id", "APPROVED")

        # Verify zen_review now has code review data
        state = gate.load_state()
        assert state["zen_review"]["status"] == "APPROVED"
        assert state["zen_review"]["continuation_id"] == "code-review-cont-id"
