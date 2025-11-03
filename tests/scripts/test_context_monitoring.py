#!/usr/bin/env python3
"""
Comprehensive tests for context monitoring and delegation detection.

Tests cover:
- Context percentage calculation accuracy
- Threshold detection (70% WARN, 85% CRITICAL)
- Legacy state file migration (backward compatibility)
- Division-by-zero guard
- Delegation history tracking
- Context reset after delegation

Author: Claude Code
Date: 2025-11-02
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


# Import the WorkflowGate class
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from scripts.workflow_gate import WorkflowGate


@pytest.fixture
def temp_state_file():
    """Create temporary state file for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "workflow-state.json"

        # Patch the STATE_FILE constant
        with patch('scripts.workflow_gate.STATE_FILE', state_file):
            yield state_file


class TestContextPercentageCalculation:
    """Test context usage percentage calculation accuracy."""

    def test_percentage_calculation_50_percent(self):
        """Test 50% usage calculation."""
        current = 100000
        max_tokens = 200000
        usage_pct = (current / max_tokens) * 100
        assert usage_pct == 50.0

    def test_percentage_calculation_70_percent_threshold(self):
        """Test 70% threshold calculation."""
        current = 140000
        max_tokens = 200000
        usage_pct = (current / max_tokens) * 100
        assert usage_pct == 70.0

    def test_percentage_calculation_85_percent_threshold(self):
        """Test 85% threshold calculation."""
        current = 170000
        max_tokens = 200000
        usage_pct = (current / max_tokens) * 100
        assert usage_pct == 85.0

    def test_percentage_calculation_near_full(self):
        """Test near-full usage (95%)."""
        current = 190000
        max_tokens = 200000
        usage_pct = (current / max_tokens) * 100
        assert usage_pct == 95.0

    def test_percentage_calculation_empty(self):
        """Test empty usage (0%)."""
        current = 0
        max_tokens = 200000
        usage_pct = (current / max_tokens) * 100
        assert usage_pct == 0.0


class TestThresholdDetection:
    """Test delegation threshold detection logic."""

    def test_threshold_below_warn(self, temp_state_file):
        """Test usage below 70% threshold - no delegation needed."""
        gate = WorkflowGate()
        state = gate._init_state()
        state["context"]["current_tokens"] = 130000  # 65%
        state["context"]["max_tokens"] = 200000

        should_del, reason = gate.should_delegate(state)

        assert should_del is False
        assert "OK" in reason
        assert "65.0%" in reason

    def test_threshold_at_warn(self, temp_state_file):
        """Test usage exactly at 70% threshold - delegation recommended."""
        gate = WorkflowGate()
        state = gate._init_state()
        state["context"]["current_tokens"] = 140000  # 70%
        state["context"]["max_tokens"] = 200000

        should_del, reason = gate.should_delegate(state)

        assert should_del is True
        assert "WARNING" in reason
        assert "70.0%" in reason
        assert "RECOMMENDED" in reason

    def test_threshold_between_warn_and_critical(self, temp_state_file):
        """Test usage between 70% and 85% - delegation recommended."""
        gate = WorkflowGate()
        state = gate._init_state()
        state["context"]["current_tokens"] = 150000  # 75%
        state["context"]["max_tokens"] = 200000

        should_del, reason = gate.should_delegate(state)

        assert should_del is True
        assert "WARNING" in reason
        assert "75.0%" in reason

    def test_threshold_at_critical(self, temp_state_file):
        """Test usage exactly at 85% threshold - delegation mandatory."""
        gate = WorkflowGate()
        state = gate._init_state()
        state["context"]["current_tokens"] = 170000  # 85%
        state["context"]["max_tokens"] = 200000

        should_del, reason = gate.should_delegate(state)

        assert should_del is True
        assert "CRITICAL" in reason
        assert "85.0%" in reason
        assert "MANDATORY" in reason

    def test_threshold_above_critical(self, temp_state_file):
        """Test usage above 85% threshold - delegation mandatory."""
        gate = WorkflowGate()
        state = gate._init_state()
        state["context"]["current_tokens"] = 180000  # 90%
        state["context"]["max_tokens"] = 200000

        should_del, reason = gate.should_delegate(state)

        assert should_del is True
        assert "CRITICAL" in reason
        assert "90.0%" in reason


class TestDivisionByZeroGuard:
    """Test guard against division by zero when max_tokens invalid."""

    def test_max_tokens_zero(self, temp_state_file):
        """Test max_tokens = 0 doesn't crash."""
        gate = WorkflowGate()
        state = gate._init_state()
        state["context"]["current_tokens"] = 100
        state["context"]["max_tokens"] = 0  # Invalid

        should_del, reason = gate.should_delegate(state)

        assert should_del is False
        assert "ERROR" in reason
        assert "Invalid" in reason or "max_tokens" in reason

    def test_max_tokens_negative(self, temp_state_file):
        """Test negative max_tokens doesn't crash."""
        gate = WorkflowGate()
        state = gate._init_state()
        state["context"]["current_tokens"] = 100
        state["context"]["max_tokens"] = -1000  # Invalid

        should_del, reason = gate.should_delegate(state)

        assert should_del is False
        assert "ERROR" in reason


class TestLegacyStateMigration:
    """Test backward compatibility with old state files (regression tests)."""

    def test_legacy_state_without_context_field(self, temp_state_file):
        """Test loading legacy state file without context field."""
        # Create legacy state file (no context field)
        legacy_state = {
            "current_component": "Test Component",
            "step": "implement",
            "zen_review": {},
            "ci_passed": False,
            "last_commit_hash": None,
            "subagent_delegations": [],
        }
        temp_state_file.parent.mkdir(parents=True, exist_ok=True)
        temp_state_file.write_text(json.dumps(legacy_state, indent=2))

        # Load state - should add context defaults
        gate = WorkflowGate()
        state = gate.load_state()

        # Verify context field was added
        assert "context" in state
        assert state["context"]["current_tokens"] == 0
        assert state["context"]["max_tokens"] == 200000
        assert "last_check_timestamp" in state["context"]

        # Verify original fields preserved
        assert state["current_component"] == "Test Component"
        assert state["step"] == "implement"

    def test_legacy_state_with_context_field(self, temp_state_file):
        """Test loading state file that already has context field."""
        # Create state file with context field
        modern_state = {
            "current_component": "Test Component",
            "step": "test",
            "zen_review": {},
            "ci_passed": True,
            "last_commit_hash": "abc123",
            "subagent_delegations": [],
            "context": {
                "current_tokens": 50000,
                "max_tokens": 200000,
                "last_check_timestamp": "2025-11-02T10:00:00Z",
            },
        }
        temp_state_file.parent.mkdir(parents=True, exist_ok=True)
        temp_state_file.write_text(json.dumps(modern_state, indent=2))

        # Load state - should preserve existing context
        gate = WorkflowGate()
        state = gate.load_state()

        # Verify context field preserved
        assert state["context"]["current_tokens"] == 50000
        assert state["context"]["max_tokens"] == 200000
        assert state["context"]["last_check_timestamp"] == "2025-11-02T10:00:00Z"


class TestDelegationTracking:
    """Test delegation history tracking using existing subagent_delegations field."""

    def test_record_delegation_appends_to_existing_field(self, temp_state_file):
        """Test record_delegation uses existing subagent_delegations field."""
        gate = WorkflowGate()
        state = gate._init_state()

        # Simulate existing delegation
        state["subagent_delegations"].append({
            "timestamp": "2025-11-02T09:00:00Z",
            "task_description": "Previous task",
            "current_step": "implement",
        })
        state["context"]["current_tokens"] = 150000
        gate.save_state(state)

        # Record new delegation
        gate.record_delegation("Search for test files")

        # Load state and verify
        new_state = gate.load_state()
        assert len(new_state["subagent_delegations"]) == 2
        assert new_state["subagent_delegations"][1]["task_description"] == "Search for test files"
        assert "timestamp" in new_state["subagent_delegations"][1]

    def test_record_delegation_resets_context(self, temp_state_file):
        """Test record_delegation resets context to 0."""
        gate = WorkflowGate()
        state = gate._init_state()
        state["context"]["current_tokens"] = 150000
        gate.save_state(state)

        # Record delegation
        gate.record_delegation("Analyze codebase structure")

        # Verify context reset
        new_state = gate.load_state()
        assert new_state["context"]["current_tokens"] == 0

    def test_multiple_delegations_tracked(self, temp_state_file):
        """Test multiple delegations are tracked correctly."""
        gate = WorkflowGate()
        state = gate._init_state()
        gate.save_state(state)

        # Record multiple delegations
        gate.record_delegation("Task 1")
        gate.record_delegation("Task 2")
        gate.record_delegation("Task 3")

        # Verify all tracked
        state = gate.load_state()
        assert len(state["subagent_delegations"]) == 3
        assert state["subagent_delegations"][0]["task_description"] == "Task 1"
        assert state["subagent_delegations"][1]["task_description"] == "Task 2"
        assert state["subagent_delegations"][2]["task_description"] == "Task 3"


class TestContextRecording:
    """Test manual context recording functionality."""

    def test_record_context_updates_tokens(self, temp_state_file):
        """Test record_context updates current_tokens."""
        gate = WorkflowGate()
        state = gate._init_state()
        gate.save_state(state)

        # Record context
        gate.record_context(120000)

        # Verify updated
        new_state = gate.load_state()
        assert new_state["context"]["current_tokens"] == 120000

    def test_record_context_updates_timestamp(self, temp_state_file):
        """Test record_context updates last_check_timestamp."""
        gate = WorkflowGate()
        state = gate._init_state()
        old_timestamp = state["context"]["last_check_timestamp"]
        gate.save_state(state)

        # Wait briefly and record context
        import time
        time.sleep(0.01)
        gate.record_context(100000)

        # Verify timestamp changed
        new_state = gate.load_state()
        assert new_state["context"]["last_check_timestamp"] != old_timestamp

    def test_record_context_successive_updates(self, temp_state_file):
        """Test successive context recordings update correctly."""
        gate = WorkflowGate()
        state = gate._init_state()
        gate.save_state(state)

        # Record multiple times
        gate.record_context(50000)
        gate.record_context(100000)
        gate.record_context(150000)

        # Verify latest value
        state = gate.load_state()
        assert state["context"]["current_tokens"] == 150000


class TestContextResetOnCommit:
    """Test context reset after successful commit (Gemini review fix)."""

    def test_context_resets_after_commit(self, temp_state_file):
        """Test record_commit resets context to 0 for next component."""
        gate = WorkflowGate()
        state = gate._init_state()

        # Simulate high context usage before commit
        state["context"]["current_tokens"] = 150000
        gate.save_state(state)

        # Create a mock git repo for record_commit to work
        import subprocess
        repo_dir = temp_state_file.parent
        subprocess.run(["git", "init"], cwd=repo_dir, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_dir, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_dir, capture_output=True)

        # Create a dummy file and commit
        dummy_file = repo_dir / "test.txt"
        dummy_file.write_text("test")
        subprocess.run(["git", "add", "."], cwd=repo_dir, capture_output=True)
        subprocess.run(["git", "commit", "-m", "test"], cwd=repo_dir, capture_output=True)

        # Patch PROJECT_ROOT to use temp directory
        with patch('scripts.workflow_gate.PROJECT_ROOT', repo_dir):
            # Record commit (should reset context)
            gate.record_commit()

        # Verify context was reset
        new_state = gate.load_state()
        assert new_state["context"]["current_tokens"] == 0, \
            "Context current_tokens should reset to 0 after commit"
        assert "last_check_timestamp" in new_state["context"], \
            "Context should have updated last_check_timestamp"


# Mark all tests as unit tests for infrastructure
pytestmark = pytest.mark.unit
