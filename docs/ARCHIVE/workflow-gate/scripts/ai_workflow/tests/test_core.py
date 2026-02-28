"""
Tests for core.py module (V2 Schema).

Tests WorkflowGate class for state management, transitions, and review tracking.
Updated for V2 nested schema.
"""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from ai_workflow.constants import (
    REVIEW_APPROVED,
)
from ai_workflow.core import (
    WorkflowGate,
    WorkflowGateBlockedError,
    WorkflowTransitionError,
    WorkflowValidationError,
)


class TestWorkflowGateInit:
    """Tests for WorkflowGate initialization."""

    def test_init_with_default_path(self, temp_dir):
        """Should use default state file path."""
        with patch("ai_workflow.core.STATE_FILE", temp_dir / "state.json"):
            gate = WorkflowGate()
            assert gate._state_file == temp_dir / "state.json"

    def test_init_with_custom_path(self, temp_dir):
        """Should use custom state file path."""
        custom_path = temp_dir / "custom-state.json"
        gate = WorkflowGate(state_file=custom_path)
        assert gate._state_file == custom_path


class TestInitStateV2:
    """Tests for _init_state method - V2 schema."""

    def test_init_state_returns_v2_dict(self, temp_dir):
        """Should return a V2 state dictionary."""
        gate = WorkflowGate(state_file=temp_dir / "state.json")
        state = gate._init_state()

        assert isinstance(state, dict)
        assert state["version"] == "2.0"
        assert state["phase"] == "component"
        assert state["component"]["step"] == "plan"

    def test_init_state_has_v2_fields(self, temp_dir):
        """Initial state should have all V2 required fields."""
        gate = WorkflowGate(state_file=temp_dir / "state.json")
        state = gate._init_state()

        # V2 schema structure
        assert "version" in state
        assert "phase" in state
        assert "component" in state
        assert "reviews" in state
        assert "ci" in state
        assert "git" in state
        assert "subtasks" in state

        # Component nested structure
        assert state["component"]["current"] == ""
        assert state["component"]["step"] == "plan"
        assert state["component"]["list"] == []

        # Reviews nested structure
        assert "gemini" in state["reviews"]
        assert "codex" in state["reviews"]

        # CI nested structure
        assert state["ci"]["component_passed"] is False

        # Git nested structure
        assert state["git"]["commits"] == []
        assert state["git"]["base_branch"] == "master"

    def test_init_state_context_defaults(self, temp_dir):
        """Context should have proper default values."""
        gate = WorkflowGate(state_file=temp_dir / "state.json")
        state = gate._init_state()

        assert state["context"]["current_tokens"] == 0
        assert state["context"]["max_tokens"] > 0


class TestLoadStateV2:
    """Tests for load_state method - V2 schema."""

    def test_load_state_creates_v2_default_when_missing(self, temp_dir):
        """Should create V2 default state when file doesn't exist."""
        state_file = temp_dir / "state.json"
        gate = WorkflowGate(state_file=state_file)

        state = gate.load_state()

        assert state["version"] == "2.0"
        assert state["component"]["step"] == "plan"
        assert state["component"]["current"] == ""

    def test_load_state_reads_existing_v2_file(self, temp_dir):
        """Should read existing V2 state file."""
        state_file = temp_dir / "state.json"
        existing_state = {
            "version": "2.0",
            "phase": "component",
            "component": {
                "current": "TestComponent",
                "step": "implement",
                "list": [],
            },
            "reviews": {"gemini": {}, "codex": {}},
            "ci": {"component_passed": False},
            "git": {"commits": []},
        }
        with open(state_file, "w") as f:
            json.dump(existing_state, f)

        gate = WorkflowGate(state_file=state_file)
        state = gate.load_state()

        assert state["component"]["current"] == "TestComponent"
        assert state["component"]["step"] == "implement"

    def test_load_state_migrates_v1_to_v2(self, temp_dir):
        """Should migrate V1 state to V2 schema."""
        state_file = temp_dir / "state.json"
        v1_state = {
            "current_component": "OldComponent",
            "step": "test",
            "ci_passed": True,
            "gemini_review": {"status": "APPROVED"},
            "codex_review": {},
        }
        with open(state_file, "w") as f:
            json.dump(v1_state, f)

        gate = WorkflowGate(state_file=state_file)
        state = gate.load_state()

        # Should be migrated to V2
        assert state["version"] == "2.0"
        assert state["component"]["current"] == "OldComponent"
        assert state["component"]["step"] == "test"
        assert state["ci"]["component_passed"] is True
        assert state["reviews"]["gemini"]["status"] == "APPROVED"

    def test_load_state_handles_corrupted_file(self, temp_dir, capsys):
        """Should handle corrupted JSON gracefully."""
        state_file = temp_dir / "state.json"
        state_file.write_text("invalid json {{{")

        gate = WorkflowGate(state_file=state_file)
        state = gate.load_state()

        # Should return fresh V2 state
        assert state["version"] == "2.0"
        assert state["component"]["step"] == "plan"
        captured = capsys.readouterr()
        assert "Warning" in captured.err

    def test_load_state_ensures_v2_defaults(self, temp_dir):
        """Should add missing V2 fields from defaults."""
        state_file = temp_dir / "state.json"
        # Minimal V2 state missing most fields
        with open(state_file, "w") as f:
            json.dump({"version": "2.0", "component": {"step": "test"}}, f)

        gate = WorkflowGate(state_file=state_file)
        state = gate.load_state()

        assert state["component"]["step"] == "test"  # Preserved
        assert "context" in state  # Added from defaults
        assert "reviews" in state  # Added from defaults


class TestSaveStateV2:
    """Tests for save_state method."""

    def test_save_state_creates_file(self, temp_dir):
        """Should create state file."""
        state_file = temp_dir / "state.json"
        gate = WorkflowGate(state_file=state_file)

        state = {
            "version": "2.0",
            "component": {"step": "implement", "current": "Test"},
        }
        gate.save_state(state)

        assert state_file.exists()
        with open(state_file) as f:
            saved = json.load(f)
        assert saved["component"]["step"] == "implement"

    def test_save_state_creates_parent_dirs(self, temp_dir):
        """Should create parent directories if needed."""
        state_file = temp_dir / "nested" / "dir" / "state.json"
        gate = WorkflowGate(state_file=state_file)

        state = {"version": "2.0", "component": {"step": "plan"}}
        gate.save_state(state)

        assert state_file.exists()


class TestCanTransition:
    """Tests for can_transition method."""

    def test_valid_transitions(self, temp_dir):
        """Should allow valid transitions."""
        gate = WorkflowGate(state_file=temp_dir / "state.json")

        can, msg = gate.can_transition("plan", "plan-review")
        assert can is True
        assert msg == ""

        can, msg = gate.can_transition("plan-review", "implement")
        assert can is True

        can, msg = gate.can_transition("implement", "test")
        assert can is True

        can, msg = gate.can_transition("test", "review")
        assert can is True

    def test_invalid_transitions(self, temp_dir):
        """Should reject invalid transitions."""
        gate = WorkflowGate(state_file=temp_dir / "state.json")

        can, msg = gate.can_transition("plan", "implement")
        assert can is False
        assert "Cannot transition" in msg

        can, msg = gate.can_transition("plan", "review")
        assert can is False

        can, msg = gate.can_transition("review", "plan")
        assert can is False

    def test_unknown_current_step(self, temp_dir):
        """Should reject unknown current step."""
        gate = WorkflowGate(state_file=temp_dir / "state.json")

        can, msg = gate.can_transition("unknown", "plan")
        assert can is False


class TestAdvanceV2:
    """Tests for advance method - V2 schema."""

    def test_advance_updates_step_v2(self, temp_dir):
        """Should update step in V2 state."""
        state_file = temp_dir / "state.json"
        with open(state_file, "w") as f:
            json.dump(
                {
                    "version": "2.0",
                    "component": {"step": "plan", "current": "Test", "list": []},
                    "reviews": {"gemini": {}, "codex": {}},
                },
                f,
            )

        gate = WorkflowGate(state_file=state_file)
        gate.advance("plan-review")

        state = gate.load_state()
        assert state["component"]["step"] == "plan-review"

    def test_advance_blocks_invalid_transition(self, temp_dir):
        """Should raise WorkflowTransitionError on invalid transition."""
        state_file = temp_dir / "state.json"
        with open(state_file, "w") as f:
            json.dump(
                {
                    "version": "2.0",
                    "component": {"step": "plan", "current": "Test", "list": []},
                },
                f,
            )

        gate = WorkflowGate(state_file=state_file)

        with pytest.raises(WorkflowTransitionError):
            gate.advance("implement")

    def test_advance_to_implement_requires_approval_v2(self, temp_dir):
        """Should raise WorkflowGateBlockedError when plan review not approved - V2 schema."""
        state_file = temp_dir / "state.json"
        with open(state_file, "w") as f:
            json.dump(
                {
                    "version": "2.0",
                    "component": {"step": "plan-review", "current": "Test", "list": []},
                    "reviews": {
                        "gemini": {"status": "NOT_REQUESTED"},
                        "codex": {"status": "NOT_REQUESTED"},
                    },
                },
                f,
            )

        gate = WorkflowGate(state_file=state_file)

        with pytest.raises(WorkflowGateBlockedError) as exc_info:
            gate.advance("implement")
        assert "Plan review not approved" in str(exc_info.value)

    def test_advance_to_implement_with_approval_v2(self, temp_dir):
        """Should allow implement with plan review approval - V2 schema."""
        state_file = temp_dir / "state.json"
        with open(state_file, "w") as f:
            json.dump(
                {
                    "version": "2.0",
                    "component": {"step": "plan-review", "current": "Test", "list": []},
                    "reviews": {
                        "gemini": {"status": "APPROVED", "continuation_id": "abc"},
                        "codex": {},
                    },
                },
                f,
            )

        gate = WorkflowGate(state_file=state_file)
        gate.advance("implement")

        state = gate.load_state()
        assert state["component"]["step"] == "implement"
        # Reviews should be cleared for code review later (now empty dict, not nested keys)
        assert state["reviews"] == {}
        assert "gemini" not in state["reviews"]


class TestPlaceholderDetectionV2:
    """Tests for _is_placeholder_id method - V2 enhancements."""

    def test_detects_test_prefix(self, temp_dir):
        """Should detect 'test-' prefix."""
        gate = WorkflowGate(state_file=temp_dir / "state.json")
        assert gate._is_placeholder_id("test-123") is True

    def test_detects_test_prefix_case_insensitive(self, temp_dir):
        """Should detect 'TEST-' prefix (case-insensitive)."""
        gate = WorkflowGate(state_file=temp_dir / "state.json")
        assert gate._is_placeholder_id("TEST-123") is True
        assert gate._is_placeholder_id("Test-ABC") is True

    def test_detects_placeholder_prefix(self, temp_dir):
        """Should detect 'placeholder-' prefix."""
        gate = WorkflowGate(state_file=temp_dir / "state.json")
        assert gate._is_placeholder_id("placeholder-abc") is True
        assert gate._is_placeholder_id("PLACEHOLDER-XYZ") is True

    def test_detects_fake_prefix(self, temp_dir):
        """Should detect 'fake-' prefix."""
        gate = WorkflowGate(state_file=temp_dir / "state.json")
        assert gate._is_placeholder_id("fake-id") is True

    def test_detects_empty_id(self, temp_dir):
        """Should detect empty ID."""
        gate = WorkflowGate(state_file=temp_dir / "state.json")
        assert gate._is_placeholder_id("") is True
        assert gate._is_placeholder_id(None) is True

    def test_detects_blank_id(self, temp_dir):
        """Should detect blank/whitespace-only IDs."""
        gate = WorkflowGate(state_file=temp_dir / "state.json")
        assert gate._is_placeholder_id("   ") is True
        assert gate._is_placeholder_id("\t\n") is True

    def test_allows_valid_ids(self, temp_dir):
        """Should allow valid continuation IDs."""
        gate = WorkflowGate(state_file=temp_dir / "state.json")
        assert gate._is_placeholder_id("abc123def456") is False
        assert gate._is_placeholder_id("zenreview-abc123") is False
        assert gate._is_placeholder_id("bbe1ce85-67f6-40d2-9baa-9e391638599d") is False


class TestRecordReviewV2:
    """Tests for record_review method - V2 schema."""

    def test_records_review_with_status_v2(self, temp_dir):
        """Should record review status in V2 format."""
        state_file = temp_dir / "state.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        audit_log = temp_dir / "workflow-audit.log"

        with open(state_file, "w") as f:
            json.dump(
                {
                    "version": "2.0",
                    "component": {"step": "plan-review", "current": "Test", "list": []},
                    "reviews": {"gemini": {}, "codex": {}},
                },
                f,
            )

        gate = WorkflowGate(state_file=state_file)

        with patch("ai_workflow.core.AUDIT_LOG_FILE", audit_log):
            gate.record_review("review-123", REVIEW_APPROVED, "gemini")

        state = gate.load_state()
        assert state["reviews"]["gemini"]["status"] == REVIEW_APPROVED
        assert state["reviews"]["gemini"]["continuation_id"] == "review-123"

    def test_rejects_placeholder_continuation_id(self, temp_dir):
        """Should raise WorkflowValidationError for placeholder continuation IDs."""
        state_file = temp_dir / "state.json"
        with open(state_file, "w") as f:
            json.dump(
                {
                    "version": "2.0",
                    "component": {"step": "plan-review", "current": "Test", "list": []},
                    "reviews": {"gemini": {}, "codex": {}},
                },
                f,
            )

        gate = WorkflowGate(state_file=state_file)

        with pytest.raises(WorkflowValidationError) as exc_info:
            gate.record_review("test-123", REVIEW_APPROVED, "gemini")
        assert "Invalid continuation ID" in str(exc_info.value)

    def test_rejects_invalid_cli_name(self, temp_dir):
        """Should raise WorkflowValidationError for invalid CLI names."""
        state_file = temp_dir / "state.json"
        with open(state_file, "w") as f:
            json.dump(
                {
                    "version": "2.0",
                    "component": {"step": "plan-review"},
                },
                f,
            )

        gate = WorkflowGate(state_file=state_file)

        with pytest.raises(WorkflowValidationError) as exc_info:
            gate.record_review("id-123", REVIEW_APPROVED, "invalid_cli")
        assert "Invalid CLI name" in str(exc_info.value)


class TestRecordCIV2:
    """Tests for record_ci method - V2 schema."""

    def test_records_ci_passed_v2(self, temp_dir):
        """Should record CI passed status in V2 format."""
        state_file = temp_dir / "state.json"
        with open(state_file, "w") as f:
            json.dump(
                {
                    "version": "2.0",
                    "phase": "component",
                    "component": {"step": "test"},
                    "ci": {"component_passed": False},
                },
                f,
            )

        gate = WorkflowGate(state_file=state_file)
        gate.record_ci(True)

        state = gate.load_state()
        assert state["ci"]["component_passed"] is True

    def test_records_ci_failed_v2(self, temp_dir):
        """Should record CI failed status in V2 format."""
        state_file = temp_dir / "state.json"
        with open(state_file, "w") as f:
            json.dump(
                {
                    "version": "2.0",
                    "phase": "component",
                    "component": {"step": "test"},
                    "ci": {"component_passed": True},
                },
                f,
            )

        gate = WorkflowGate(state_file=state_file)
        gate.record_ci(False)

        state = gate.load_state()
        assert state["ci"]["component_passed"] is False

    def test_records_pr_ci_in_pr_phase(self, temp_dir):
        """Should record PR CI when in pr-review phase."""
        state_file = temp_dir / "state.json"
        with open(state_file, "w") as f:
            json.dump(
                {
                    "version": "2.0",
                    "phase": "pr-review",
                    "ci": {"component_passed": False, "pr_ci_passed": False},
                },
                f,
            )

        gate = WorkflowGate(state_file=state_file)
        gate.record_ci(True)

        state = gate.load_state()
        assert state["ci"]["pr_ci_passed"] is True


class TestSetComponentV2:
    """Tests for set_component method - V2 schema."""

    def test_sets_component_name_v2(self, temp_dir):
        """Should set current component name in V2 format."""
        state_file = temp_dir / "state.json"
        with open(state_file, "w") as f:
            json.dump(
                {
                    "version": "2.0",
                    "component": {"current": "", "list": []},
                },
                f,
            )

        gate = WorkflowGate(state_file=state_file)
        gate.set_component("NewComponent")

        state = gate.load_state()
        assert state["component"]["current"] == "NewComponent"
        assert "NewComponent" in state["component"]["list"]


class TestCheckCommitV2:
    """Tests for check_commit method - V2 schema."""

    def test_blocks_when_no_component_set_v2(self, temp_dir):
        """Should raise WorkflowGateBlockedError when no component set (Codex P1 fix)."""
        state_file = temp_dir / "state.json"
        with open(state_file, "w") as f:
            json.dump(
                {
                    "version": "2.0",
                    "component": {"current": "", "step": "review"},
                },
                f,
            )

        gate = WorkflowGate(state_file=state_file)

        with pytest.raises(WorkflowGateBlockedError) as exc_info:
            gate.check_commit()
        assert "No component set" in str(exc_info.value)
        assert exc_info.value.details["reason"] == "no_component"

    def test_blocks_when_not_in_review_v2(self, temp_dir):
        """Should raise WorkflowGateBlockedError when not in review step."""
        state_file = temp_dir / "state.json"
        with open(state_file, "w") as f:
            json.dump(
                {
                    "version": "2.0",
                    "component": {"current": "TestComp", "step": "implement"},
                },
                f,
            )

        gate = WorkflowGate(state_file=state_file)

        with pytest.raises(WorkflowGateBlockedError) as exc_info:
            gate.check_commit()
        assert "must be 'review'" in str(exc_info.value)
        assert exc_info.value.details["reason"] == "wrong_step"

    def test_blocks_without_dual_approval_v2(self, temp_dir):
        """Should raise WorkflowGateBlockedError without both reviews approved - V2."""
        state_file = temp_dir / "state.json"
        with open(state_file, "w") as f:
            json.dump(
                {
                    "version": "2.0",
                    "component": {"step": "review", "current": "Test"},
                    "reviews": {
                        "gemini": {"status": "APPROVED", "continuation_id": "abc"},
                        "codex": {"status": "NOT_REQUESTED"},
                    },
                    "ci": {"component_passed": True},
                },
                f,
            )

        gate = WorkflowGate(state_file=state_file)

        with pytest.raises(WorkflowGateBlockedError) as exc_info:
            gate.check_commit()
        assert "Insufficient review approvals" in str(exc_info.value)
        assert exc_info.value.details["reason"] == "insufficient_approvals"

    def test_blocks_without_ci_v2(self, temp_dir):
        """Should raise WorkflowGateBlockedError when CI not passed - V2."""
        state_file = temp_dir / "state.json"
        with open(state_file, "w") as f:
            json.dump(
                {
                    "version": "2.0",
                    "component": {"step": "review", "current": "Test"},
                    "reviews": {
                        "gemini": {"status": "APPROVED", "continuation_id": "abc123"},
                        "codex": {"status": "APPROVED", "continuation_id": "def456"},
                    },
                    "ci": {"component_passed": False},
                },
                f,
            )

        gate = WorkflowGate(state_file=state_file)

        with pytest.raises(WorkflowGateBlockedError) as exc_info:
            gate.check_commit()
        assert "CI not passed" in str(exc_info.value)
        assert exc_info.value.details["reason"] == "ci_not_passed"

    def test_allows_override_with_auditing(self, temp_dir, capsys):
        """Should return True with ZEN_REVIEW_OVERRIDE and log to audit."""
        state_file = temp_dir / "state.json"
        audit_log = temp_dir / "workflow-audit.log"

        with open(state_file, "w") as f:
            json.dump(
                {
                    "version": "2.0",
                    "component": {"step": "implement"},  # Wrong step
                },
                f,
            )

        gate = WorkflowGate(state_file=state_file)

        with patch.dict(os.environ, {"ZEN_REVIEW_OVERRIDE": "true"}):
            with patch("ai_workflow.core.AUDIT_LOG_FILE", audit_log):
                result = gate.check_commit()
                assert result is True  # Override allowed

        # Check warning was printed
        captured = capsys.readouterr()
        assert "EMERGENCY OVERRIDE" in captured.err
        assert "LOGGED" in captured.err


class TestRecordCommitV2:
    """Tests for record_commit method - V2 schema."""

    def test_records_commit_hash_v2(self, temp_dir):
        """Should record commit hash and reset state - V2."""
        state_file = temp_dir / "state.json"
        with open(state_file, "w") as f:
            json.dump(
                {
                    "version": "2.0",
                    "component": {"step": "review", "current": "Test", "list": []},
                    "reviews": {
                        "gemini": {"status": "APPROVED"},
                        "codex": {"status": "APPROVED"},
                    },
                    "ci": {"component_passed": True},
                    "git": {"commits": []},
                },
                f,
            )

        gate = WorkflowGate(state_file=state_file)

        # Mock git command
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="abc123def456\n", stderr="")
            with patch("ai_workflow.core.PROJECT_ROOT", temp_dir):
                gate.record_commit()

        state = gate.load_state()
        # V2: commits in git.commits with component info
        assert len(state["git"]["commits"]) == 1
        assert state["git"]["commits"][0]["hash"] == "abc123def456"
        assert state["git"]["commits"][0]["component"] == "Test"
        assert state["component"]["step"] == "plan"  # Reset
        # Reviews should be cleared (now empty dict, not nested keys)
        assert state["reviews"] == {}


class TestShowStatusV2:
    """Tests for show_status method - V2 schema."""

    def test_displays_status_v2(self, temp_dir, capsys):
        """Should display current workflow status - V2."""
        state_file = temp_dir / "state.json"
        with open(state_file, "w") as f:
            json.dump(
                {
                    "version": "2.0",
                    "component": {"step": "implement", "current": "TestComponent"},
                    "reviews": {"gemini": {}, "codex": {}},
                    "ci": {"component_passed": False},
                },
                f,
            )

        gate = WorkflowGate(state_file=state_file)
        gate.show_status()

        captured = capsys.readouterr()
        assert "TestComponent" in captured.out
        assert "implement" in captured.out or "Implement" in captured.out
        assert "V2" in captured.out  # Should indicate V2


class TestResetV2:
    """Tests for reset method - V2 schema."""

    def test_resets_to_v2_initial_state(self, temp_dir):
        """Should reset to V2 initial state."""
        state_file = temp_dir / "state.json"
        with open(state_file, "w") as f:
            json.dump(
                {
                    "version": "2.0",
                    "component": {"step": "review", "current": "SomeComponent"},
                    "ci": {"component_passed": True},
                },
                f,
            )

        gate = WorkflowGate(state_file=state_file)
        gate.reset()

        state = gate.load_state()
        assert state["version"] == "2.0"
        assert state["component"]["step"] == "plan"
        assert state["component"]["current"] == ""
        assert state["ci"]["component_passed"] is False


class TestSetComponentsListV2:
    """Tests for set_components_list method - V2 schema."""

    def test_sets_components_list_v2(self, temp_dir):
        """Should set list of components - V2."""
        state_file = temp_dir / "state.json"
        with open(state_file, "w") as f:
            json.dump(
                {
                    "version": "2.0",
                    "component": {"list": [], "total": 0, "completed": 0},
                },
                f,
            )

        gate = WorkflowGate(state_file=state_file)
        gate.set_components_list(["Component1", "Component2"])

        state = gate.load_state()
        # V2: list is array of strings, not dicts
        assert len(state["component"]["list"]) == 2
        assert state["component"]["list"][0] == "Component1"
        assert state["component"]["list"][1] == "Component2"
        assert state["component"]["total"] == 2

    def test_rejects_single_component(self, temp_dir):
        """Should raise WorkflowValidationError for lists with fewer than 2 components."""
        state_file = temp_dir / "state.json"
        with open(state_file, "w") as f:
            json.dump({"version": "2.0"}, f)

        gate = WorkflowGate(state_file=state_file)

        with pytest.raises(WorkflowValidationError) as exc_info:
            gate.set_components_list(["SingleComponent"])
        assert "at least 2 components" in str(exc_info.value)


class TestFileLocking:
    """Tests for file locking mechanism."""

    def test_locked_state_context(self, temp_dir):
        """Should provide locked context manager."""
        state_file = temp_dir / "state.json"
        with open(state_file, "w") as f:
            json.dump(
                {
                    "version": "2.0",
                    "component": {"step": "plan"},
                    "value": 1,
                },
                f,
            )

        gate = WorkflowGate(state_file=state_file)

        with gate.locked_state_context() as state:
            state["value"] = 2

        saved = gate.load_state()
        assert saved["value"] == 2

    def test_locked_modify_state(self, temp_dir):
        """Should provide locked modifier function."""
        state_file = temp_dir / "state.json"
        with open(state_file, "w") as f:
            json.dump(
                {
                    "version": "2.0",
                    "component": {"step": "plan"},
                    "counter": 0,
                },
                f,
            )

        gate = WorkflowGate(state_file=state_file)

        def increment(state):
            state["counter"] += 1

        gate.locked_modify_state(increment)

        saved = gate.load_state()
        assert saved["counter"] == 1
