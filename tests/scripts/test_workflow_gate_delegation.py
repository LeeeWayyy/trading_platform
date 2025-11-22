#!/usr/bin/env python3
"""
Tests for Phase C1 planned delegation commands in workflow_gate.py.

These tests verify:
- plan-delegation: Creating planned delegations
- cancel-delegation: Cancelling planned delegations
- capture-summary: Marking delegations complete with summary
- Commit gate: Blocking commits with pending delegations

Author: Claude Code
Date: 2025-11-17
Component: P1T13-F5 Phase C1 - Core Delegation Commands
"""

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.workflow_gate import DelegationRules, WorkflowGate


@pytest.fixture()
def temp_state_file():
    """Create temporary state file for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "workflow-state.json"
        with patch("scripts.workflow_gate.STATE_FILE", state_file):
            yield state_file


@pytest.fixture()
def delegation_rules(temp_state_file):
    """Create DelegationRules instance with temp state file."""
    gate = WorkflowGate(state_file=temp_state_file)
    # Initialize DelegationRules with gate's state management callables
    rules = DelegationRules(
        load_state=gate.load_state,
        save_state=gate.save_state,
        locked_modify_state=gate.locked_modify_state,
    )
    return rules


def test_plan_delegation_creates_pending_delegation(delegation_rules):
    """Verify plan-delegation creates a pending delegation."""
    result = delegation_rules.plan_delegation(
        description="Search for retry patterns",
        reason="Large codebase search",
    )

    assert "error" not in result
    assert result["status"] == "pending"
    assert "id" in result
    assert result["description"] == "Search for retry patterns"

    # Verify in state file
    state = delegation_rules._load_state()
    assert "planned_delegations" in state
    assert len(state["planned_delegations"]) == 1
    delegation = state["planned_delegations"][0]
    assert delegation["status"] == "pending"
    assert delegation["description"] == "Search for retry patterns"
    assert delegation["reason"] == "Large codebase search"
    assert delegation["summary"] is None


def test_capture_summary_marks_delegation_complete(delegation_rules):
    """Verify capture-summary marks delegation as completed."""
    # Create delegation first
    result = delegation_rules.plan_delegation(
        description="Check PR reviews",
        reason="GitHub API calls",
    )
    delegation_id = result["id"]

    # Capture summary
    result = delegation_rules.capture_summary(
        delegation_id=delegation_id,
        summary="Found 3 open PRs, all approved",
    )

    assert "error" not in result
    assert result["status"] == "completed"
    assert result["summary"] == "Found 3 open PRs, all approved"

    # Verify in state file
    state = delegation_rules._load_state()
    delegation = state["planned_delegations"][0]
    assert delegation["status"] == "completed"
    assert delegation["summary"] == "Found 3 open PRs, all approved"
    assert "completed_at" in delegation


def test_cancel_delegation_marks_delegation_cancelled(delegation_rules):
    """Verify cancel-delegation marks delegation as cancelled."""
    # Create delegation first
    result = delegation_rules.plan_delegation(
        description="Obsolete task",
        reason="Was needed but became obsolete",
    )
    delegation_id = result["id"]

    # Cancel delegation
    result = delegation_rules.cancel_delegation(delegation_id=delegation_id)

    assert "error" not in result
    assert result["status"] == "cancelled"
    assert result["description"] == "Obsolete task"

    # Verify in state file
    state = delegation_rules._load_state()
    delegation = state["planned_delegations"][0]
    assert delegation["status"] == "cancelled"
    assert "cancelled_at" in delegation


def test_cancel_nonexistent_delegation_returns_error(delegation_rules):
    """Verify cancelling nonexistent delegation returns error."""
    result = delegation_rules.cancel_delegation(delegation_id="del-fake-123")

    assert "error" in result
    assert "not found" in result["error"].lower()


def test_capture_summary_nonexistent_delegation_returns_error(delegation_rules):
    """Verify capturing summary for nonexistent delegation returns error."""
    result = delegation_rules.capture_summary(
        delegation_id="del-fake-123",
        summary="This should fail",
    )

    assert "error" in result
    assert "not found" in result["error"].lower()


def test_multiple_delegations_tracked_independently(delegation_rules):
    """Verify multiple delegations are tracked independently."""
    # Create 3 delegations
    del1 = delegation_rules.plan_delegation("Task 1", "Reason 1")
    del2 = delegation_rules.plan_delegation("Task 2", "Reason 2")
    del3 = delegation_rules.plan_delegation("Task 3", "Reason 3")

    # Complete first, cancel second, leave third pending
    delegation_rules.capture_summary(del1["id"], "Task 1 done")
    delegation_rules.cancel_delegation(del2["id"])

    # Verify state
    state = delegation_rules._load_state()
    assert len(state["planned_delegations"]) == 3
    assert state["planned_delegations"][0]["status"] == "completed"
    assert state["planned_delegations"][1]["status"] == "cancelled"
    assert state["planned_delegations"][2]["status"] == "pending"


def test_commit_gate_blocks_pending_delegations(temp_state_file):
    """Verify check_commit() actually blocks when there are pending delegations."""
    gate = WorkflowGate(state_file=temp_state_file)
    rules = DelegationRules(
        load_state=gate.load_state,
        save_state=gate.save_state,
        locked_modify_state=gate.locked_modify_state,
    )

    # Create pending delegation
    rules.plan_delegation("Test delegation", "Testing commit gate")

    # Set up minimal workflow state to reach delegation gate
    # (Other gates will be checked first, so we need to pass them)
    gate.set_component("Test Component")

    # Create temporary task file for planning gate
    task_file = temp_state_file.parent / "test_task.md"
    task_file.write_text("# Test Task\n\nMinimal task content for test")

    # Modify state to bypass earlier gates for this test
    def bypass_earlier_gates(state):
        # Bypass planning gates (Gate 0)
        state["task_file"] = str(task_file)
        state["analysis_completed"] = True
        state["components"] = ["Component 1", "Component 2"]
        # Bypass workflow gates (NEW DUAL REVIEW FORMAT)
        state["step"] = "review"  # Bypass step check
        state["gemini_review"] = {
            "continuation_id": "test-gemini-real-id-12345",
            "status": "APPROVED",
            "staged_hash": "abc123",  # Non-empty hash
        }
        state["codex_review"] = {
            "continuation_id": "test-codex-real-id-54321",
            "status": "APPROVED",
            "staged_hash": "abc123",  # Non-empty hash
        }
        state["ci_passed"] = True  # Bypass CI check
        state["first_commit_made"] = False  # Skip audit log check (first commit)

    gate.locked_modify_state(bypass_earlier_gates)

    # Mock to bypass other checks and reach delegation gate
    with patch.object(WorkflowGate, "_compute_staged_hash", return_value="abc123"):
        with patch.object(WorkflowGate, "_is_placeholder_id", return_value=False):
            # Now try check_commit() - should be blocked by pending delegation
            with pytest.raises(SystemExit) as excinfo:
                gate.check_commit()

            # Verify it exits with error code (not 0)
            assert excinfo.value.code == 1


def test_commit_gate_passes_with_completed_delegations(temp_state_file):
    """Verify check_commit() passes when delegations are completed/cancelled."""
    gate = WorkflowGate(state_file=temp_state_file)
    rules = DelegationRules(
        load_state=gate.load_state,
        save_state=gate.save_state,
        locked_modify_state=gate.locked_modify_state,
    )

    # Create and complete delegation
    result = rules.plan_delegation("Test delegation", "Testing commit gate")
    rules.capture_summary(result["id"], "Completed successfully")

    # Set up workflow state to reach delegation gate
    gate.set_component("Test Component")

    # Create temporary task file for planning gate
    task_file = temp_state_file.parent / "test_task.md"
    task_file.write_text("# Test Task\n\nMinimal task content for test")

    # Modify state to bypass earlier gates for this test
    def bypass_earlier_gates(state):
        # Bypass planning gates (Gate 0)
        state["task_file"] = str(task_file)
        state["analysis_completed"] = True
        state["components"] = ["Component 1", "Component 2"]
        # Bypass workflow gates (NEW DUAL REVIEW FORMAT)
        state["step"] = "review"
        state["gemini_review"] = {
            "continuation_id": "test-gemini-real-id-67890",
            "status": "APPROVED",
            "staged_hash": "def456",
        }
        state["codex_review"] = {
            "continuation_id": "test-codex-real-id-09876",
            "status": "APPROVED",
            "staged_hash": "def456",
        }
        state["ci_passed"] = True
        state["first_commit_made"] = False  # Skip audit log check (first commit)

    gate.locked_modify_state(bypass_earlier_gates)

    # Mock the code fingerprint, placeholder check, and audit log check to pass
    with patch.object(WorkflowGate, "_compute_staged_hash", return_value="def456"):
        with patch.object(WorkflowGate, "_is_placeholder_id", return_value=False):
            with patch.object(WorkflowGate, "_is_continuation_id_in_audit_log", return_value=True):
                # Now try check_commit() - should pass (delegation is completed)
                with pytest.raises(SystemExit) as excinfo:
                    gate.check_commit()

                # Verify it exits with success code (0)
                assert excinfo.value.code == 0


def test_delegation_id_format_includes_date_and_uuid(delegation_rules):
    """Verify delegation IDs follow the expected format."""
    result = delegation_rules.plan_delegation("Test", "Reason")

    delegation_id = result["id"]
    assert delegation_id.startswith("del-")
    # Format: del-YYYYMMDD-xxxxxx (where x is hex)
    parts = delegation_id.split("-")
    assert len(parts) == 3
    assert parts[0] == "del"
    assert len(parts[1]) == 8  # YYYYMMDD
    assert len(parts[2]) == 6  # UUID prefix
