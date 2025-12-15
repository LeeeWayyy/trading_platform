#!/usr/bin/env python3
"""
Test suite for DelegationRules class in scripts/workflow_gate.py.

Tests context monitoring, delegation recommendations, and operation cost projections.

Author: Claude Code
Date: 2025-11-08
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Add project root to Python path for imports
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

# Import class under test
from scripts.workflow_gate import DelegationRules


class TestDelegationRulesInitialization:
    """Test DelegationRules.__init__() method."""

    def test_init_with_callables(self) -> None:
        """Test successful initialization with state management callables."""
        load_state = MagicMock(return_value={})
        save_state = MagicMock()

        rules = DelegationRules(load_state=load_state, save_state=save_state)

        assert rules._load_state is load_state
        assert rules._save_state is save_state

    def test_constants_defined(self) -> None:
        """Test that class constants are properly defined."""
        assert DelegationRules.CONTEXT_WARN_PCT == 70
        assert DelegationRules.CONTEXT_CRITICAL_PCT == 85
        assert DelegationRules.DEFAULT_MAX_TOKENS == 200000  # From environment

        # Verify operation costs
        assert "full_ci" in DelegationRules.OPERATION_COSTS
        assert "deep_review" in DelegationRules.OPERATION_COSTS
        assert DelegationRules.OPERATION_COSTS["full_ci"] == 50000


class TestGetContextSnapshot:
    """Test DelegationRules.get_context_snapshot() method."""

    def test_snapshot_with_valid_state(self) -> None:
        """Test snapshot with complete valid state."""
        state = {
            "context": {
                "current_tokens": 50000,
                "max_tokens": 200000,
                "last_check_timestamp": "2025-11-08T12:00:00Z",
            }
        }
        load_state = MagicMock(return_value=state)
        save_state = MagicMock()

        rules = DelegationRules(load_state=load_state, save_state=save_state)
        snapshot = rules.get_context_snapshot()

        assert snapshot["current_tokens"] == 50000
        assert snapshot["max_tokens"] == 200000
        assert snapshot["usage_pct"] == 25.0  # 50k / 200k = 25%
        assert snapshot["last_check"] == "2025-11-08T12:00:00Z"
        assert snapshot["error"] is None

    def test_snapshot_with_empty_state(self) -> None:
        """Test snapshot with empty state (returns fail-safe defaults)."""
        load_state = MagicMock(return_value={})
        save_state = MagicMock()

        rules = DelegationRules(load_state=load_state, save_state=save_state)
        snapshot = rules.get_context_snapshot()

        assert snapshot["current_tokens"] == 0
        assert snapshot["max_tokens"] == 200000  # DEFAULT_MAX_TOKENS
        assert snapshot["usage_pct"] == 0.0
        assert snapshot["last_check"] == "never"
        assert snapshot["error"] is None

    def test_snapshot_with_invalid_max_tokens(self) -> None:
        """Test snapshot with invalid max_tokens (zero or negative)."""
        state = {
            "context": {
                "current_tokens": 50000,
                "max_tokens": 0,  # Invalid
            }
        }
        load_state = MagicMock(return_value=state)
        save_state = MagicMock()

        rules = DelegationRules(load_state=load_state, save_state=save_state)
        snapshot = rules.get_context_snapshot()

        assert snapshot["usage_pct"] == 0.0
        assert snapshot["error"] == "Invalid max_tokens - please export CLAUDE_MAX_TOKENS"

    def test_snapshot_with_load_error(self, capsys: pytest.CaptureFixture) -> None:
        """Test snapshot when load_state fails (graceful fallback)."""
        load_state = MagicMock(side_effect=Exception("Load failed"))
        save_state = MagicMock()

        rules = DelegationRules(load_state=load_state, save_state=save_state)
        snapshot = rules.get_context_snapshot()

        # Should return fail-safe defaults
        assert snapshot["current_tokens"] == 0
        assert snapshot["max_tokens"] == 200000
        assert snapshot["usage_pct"] == 0.0

        # Should print warning
        captured = capsys.readouterr()
        assert "Warning: Could not load state" in captured.out
        assert "Using fail-safe defaults" in captured.out

    def test_snapshot_with_provided_state(self) -> None:
        """Test snapshot with state dict provided (doesn't call load_state)."""
        load_state = MagicMock()  # Should NOT be called
        save_state = MagicMock()

        state = {
            "context": {
                "current_tokens": 100000,
                "max_tokens": 200000,
            }
        }

        rules = DelegationRules(load_state=load_state, save_state=save_state)
        snapshot = rules.get_context_snapshot(state=state)

        assert snapshot["current_tokens"] == 100000
        assert snapshot["usage_pct"] == 50.0
        load_state.assert_not_called()


class TestRecordContext:
    """Test DelegationRules.record_context() method."""

    def test_record_context_success(self) -> None:
        """Test successful context recording."""
        state = {"context": {"max_tokens": 200000}}
        load_state = MagicMock(return_value=state)
        save_state = MagicMock()

        rules = DelegationRules(load_state=load_state, save_state=save_state)
        snapshot = rules.record_context(50000)

        assert snapshot["current_tokens"] == 50000
        assert state["context"]["current_tokens"] == 50000
        assert "last_check_timestamp" in state["context"]

        # Verify save was called
        save_state.assert_called_once()

    def test_record_context_negative_tokens(self) -> None:
        """Test recording negative tokens (clamped to 0)."""
        state = {"context": {"max_tokens": 200000}}
        load_state = MagicMock(return_value=state)
        save_state = MagicMock()

        rules = DelegationRules(load_state=load_state, save_state=save_state)
        snapshot = rules.record_context(-5000)

        # Should be clamped to 0
        assert snapshot["current_tokens"] == 0
        assert state["context"]["current_tokens"] == 0

    def test_record_context_exceeds_max(self, capsys: pytest.CaptureFixture) -> None:
        """Test warning when tokens exceed max."""
        state = {"context": {"max_tokens": 200000}}
        load_state = MagicMock(return_value=state)
        save_state = MagicMock()

        rules = DelegationRules(load_state=load_state, save_state=save_state)
        rules.record_context(250000)  # Exceeds max

        captured = capsys.readouterr()
        assert "Warning: Token usage (250000) exceeds max (200000)" in captured.out
        assert "delegating to subagent" in captured.out

    def test_record_context_initializes_missing_context(self) -> None:
        """Test that missing context dict is initialized."""
        state = {}  # No context key
        load_state = MagicMock(return_value=state)
        save_state = MagicMock()

        rules = DelegationRules(load_state=load_state, save_state=save_state)
        rules.record_context(10000)

        assert "context" in state
        assert state["context"]["current_tokens"] == 10000
        assert state["context"]["max_tokens"] == 200000  # DEFAULT

    def test_record_context_save_error(self, capsys: pytest.CaptureFixture) -> None:
        """Test graceful handling when save_state fails."""
        state = {"context": {"max_tokens": 200000}}
        load_state = MagicMock(return_value=state)
        save_state = MagicMock(side_effect=Exception("Save failed"))

        rules = DelegationRules(load_state=load_state, save_state=save_state)
        snapshot = rules.record_context(10000)

        # Should still return snapshot
        assert snapshot["current_tokens"] == 10000

        # Should print warning
        captured = capsys.readouterr()
        assert "Warning: Could not save state" in captured.out
        assert "State changes not persisted" in captured.out


class TestShouldDelegateContext:
    """Test DelegationRules.should_delegate_context() method."""

    def test_below_warning_threshold(self) -> None:
        """Test usage < 70% (OK, no delegation needed)."""
        state = {"context": {"current_tokens": 50000, "max_tokens": 200000}}  # 25%
        load_state = MagicMock(return_value=state)
        save_state = MagicMock()

        rules = DelegationRules(load_state=load_state, save_state=save_state)
        should_delegate, reason, pct = rules.should_delegate_context()

        assert should_delegate is False
        assert reason == "OK - Continue normal workflow"
        assert pct == 25.0

    def test_warning_threshold(self) -> None:
        """Test usage 70-84% (WARNING - delegation recommended)."""
        state = {"context": {"current_tokens": 150000, "max_tokens": 200000}}  # 75%
        load_state = MagicMock(return_value=state)
        save_state = MagicMock()

        rules = DelegationRules(load_state=load_state, save_state=save_state)
        should_delegate, reason, pct = rules.should_delegate_context()

        assert should_delegate is True
        assert reason == "WARNING - Delegation RECOMMENDED"
        assert pct == 75.0

    def test_critical_threshold(self) -> None:
        """Test usage >= 85% (CRITICAL - delegation mandatory)."""
        state = {"context": {"current_tokens": 180000, "max_tokens": 200000}}  # 90%
        load_state = MagicMock(return_value=state)
        save_state = MagicMock()

        rules = DelegationRules(load_state=load_state, save_state=save_state)
        should_delegate, reason, pct = rules.should_delegate_context()

        assert should_delegate is True
        assert reason == "CRITICAL - Delegation MANDATORY"
        assert pct == 90.0

    def test_exact_warning_boundary(self) -> None:
        """Test exact 70% boundary."""
        state = {"context": {"current_tokens": 140000, "max_tokens": 200000}}  # 70%
        load_state = MagicMock(return_value=state)
        save_state = MagicMock()

        rules = DelegationRules(load_state=load_state, save_state=save_state)
        should_delegate, reason, pct = rules.should_delegate_context()

        # At 70%, should trigger WARNING
        assert should_delegate is True
        assert reason == "WARNING - Delegation RECOMMENDED"
        assert pct == 70.0

    def test_exact_critical_boundary(self) -> None:
        """Test exact 85% boundary."""
        state = {"context": {"current_tokens": 170000, "max_tokens": 200000}}  # 85%
        load_state = MagicMock(return_value=state)
        save_state = MagicMock()

        rules = DelegationRules(load_state=load_state, save_state=save_state)
        should_delegate, reason, pct = rules.should_delegate_context()

        # At 85%, should trigger CRITICAL
        assert should_delegate is True
        assert reason == "CRITICAL - Delegation MANDATORY"
        assert pct == 85.0


class TestShouldDelegateOperation:
    """Test DelegationRules.should_delegate_operation() method."""

    def test_always_delegate_expensive_operation(self) -> None:
        """Test that >= 50k token operations are always delegated."""
        state = {"context": {"current_tokens": 10000, "max_tokens": 200000}}  # 5%
        load_state = MagicMock(return_value=state)
        save_state = MagicMock()

        rules = DelegationRules(load_state=load_state, save_state=save_state)
        should_delegate, reason = rules.should_delegate_operation("full_ci")

        assert should_delegate is True
        assert "50000 tokens" in reason
        assert "always delegate" in reason

    def test_delegate_when_projection_exceeds_threshold(self) -> None:
        """Test delegation when current + operation would exceed 85%."""
        state = {
            "context": {
                "current_tokens": 150000,  # 75% (safe)
                "max_tokens": 200000,
            }
        }
        load_state = MagicMock(return_value=state)
        save_state = MagicMock()

        rules = DelegationRules(load_state=load_state, save_state=save_state)
        # deep_review = 30k tokens, 150k + 30k = 180k = 90% (exceeds 85%)
        should_delegate, reason = rules.should_delegate_operation("deep_review")

        assert should_delegate is True
        assert "90.0%" in reason
        assert "≥85% critical" in reason

    def test_ok_when_projection_within_threshold(self) -> None:
        """Test OK when current + operation stays below 85%."""
        state = {
            "context": {
                "current_tokens": 100000,  # 50%
                "max_tokens": 200000,
            }
        }
        load_state = MagicMock(return_value=state)
        save_state = MagicMock()

        rules = DelegationRules(load_state=load_state, save_state=save_state)
        # simple_fix = 5k tokens, 100k + 5k = 105k = 52.5% (safe)
        should_delegate, reason = rules.should_delegate_operation("simple_fix")

        assert should_delegate is False
        assert "OK" in reason
        assert "52.5%" in reason

    def test_unknown_operation_default_cost(self) -> None:
        """Test unknown operation uses default 10k token cost."""
        state = {
            "context": {
                "current_tokens": 100000,
                "max_tokens": 200000,
            }
        }
        load_state = MagicMock(return_value=state)
        save_state = MagicMock()

        rules = DelegationRules(load_state=load_state, save_state=save_state)
        should_delegate, reason = rules.should_delegate_operation("unknown_op")

        assert should_delegate is False
        # 100k + 10k = 110k = 55%
        assert "55.0%" in reason


class TestSuggestDelegation:
    """Test DelegationRules.suggest_delegation() method."""

    def test_suggest_delegation_below_threshold(self) -> None:
        """Test suggestion when usage is below 70% (no delegation needed)."""
        state = {"context": {"current_tokens": 50000, "max_tokens": 200000}}  # 25%
        load_state = MagicMock(return_value=state)
        save_state = MagicMock()

        rules = DelegationRules(load_state=load_state, save_state=save_state)
        message = rules.suggest_delegation()

        assert "CONTEXT STATUS" in message
        assert "✅" in message or "25.0%" in message
        assert "OK - Continue normal workflow" in message
        # Should NOT have recommendation section
        assert "RECOMMENDATION" not in message

    def test_suggest_delegation_above_threshold(self) -> None:
        """Test suggestion when usage is >= 70% (delegation recommended)."""
        state = {"context": {"current_tokens": 150000, "max_tokens": 200000}}  # 75%
        load_state = MagicMock(return_value=state)
        save_state = MagicMock()

        rules = DelegationRules(load_state=load_state, save_state=save_state)
        message = rules.suggest_delegation()

        assert "CONTEXT STATUS" in message
        assert "75.0%" in message
        assert "WARNING - Delegation RECOMMENDED" in message
        assert "RECOMMENDATION" in message
        assert "16-subagent-delegation.md" in message

    def test_suggest_delegation_with_operation(self) -> None:
        """Test suggestion with specific operation."""
        state = {"context": {"current_tokens": 150000, "max_tokens": 200000}}  # 75%
        load_state = MagicMock(return_value=state)
        save_state = MagicMock()

        rules = DelegationRules(load_state=load_state, save_state=save_state)
        message = rules.suggest_delegation(operation="full_ci")

        assert "CONTEXT STATUS" in message
        assert "Operation Guidance" in message
        assert "full_ci" in message
        assert "Example Task delegation" in message
        assert "subagent_type=" in message  # Template included


class TestRecordDelegation:
    """Test DelegationRules.record_delegation() method."""

    def test_record_delegation_success(self) -> None:
        """Test successful delegation recording."""
        state = {
            "context": {
                "current_tokens": 150000,
                "max_tokens": 200000,
            },
            "subagent_delegations": [],
        }
        load_state = MagicMock(return_value=state)
        save_state = MagicMock()

        rules = DelegationRules(load_state=load_state, save_state=save_state)
        result = rules.record_delegation("Run full CI suite")

        # Verify delegation recorded
        assert len(state["subagent_delegations"]) == 1
        delegation = state["subagent_delegations"][0]
        assert delegation["task_description"] == "Run full CI suite"
        assert delegation["context_before_delegation"] == 150000
        assert "timestamp" in delegation

        # Verify context reset
        assert state["context"]["current_tokens"] == 0
        assert "last_delegation_timestamp" in state["context"]

        # Verify result
        assert result["count"] == 1
        assert result["reset_tokens"] == 0

        # Verify save called
        save_state.assert_called_once()

    def test_record_delegation_initializes_list(self) -> None:
        """Test that missing subagent_delegations list is initialized."""
        state = {
            "context": {"current_tokens": 100000}
            # No subagent_delegations key
        }
        load_state = MagicMock(return_value=state)
        save_state = MagicMock()

        rules = DelegationRules(load_state=load_state, save_state=save_state)
        rules.record_delegation("Test task")

        assert "subagent_delegations" in state
        assert len(state["subagent_delegations"]) == 1

    def test_record_delegation_multiple(self) -> None:
        """Test multiple delegations accumulate."""
        state = {"context": {"current_tokens": 100000}, "subagent_delegations": []}
        load_state = MagicMock(return_value=state)
        save_state = MagicMock()

        rules = DelegationRules(load_state=load_state, save_state=save_state)

        # Record first delegation
        result1 = rules.record_delegation("Task 1")
        assert result1["count"] == 1

        # Update state for second call
        state["context"]["current_tokens"] = 120000  # Simulate usage

        # Record second delegation
        result2 = rules.record_delegation("Task 2")
        assert result2["count"] == 2
        assert len(state["subagent_delegations"]) == 2


class TestGetDelegationTemplate:
    """Test DelegationRules.get_delegation_template() method."""

    def test_template_for_full_ci(self) -> None:
        """Test template for full_ci operation."""
        load_state = MagicMock(return_value={})
        save_state = MagicMock()

        rules = DelegationRules(load_state=load_state, save_state=save_state)
        template = rules.get_delegation_template("full_ci")

        assert "Task(" in template
        assert "general-purpose" in template
        assert "make ci-local" in template

    def test_template_for_deep_review(self) -> None:
        """Test template for deep_review operation."""
        load_state = MagicMock(return_value={})
        save_state = MagicMock()

        rules = DelegationRules(load_state=load_state, save_state=save_state)
        template = rules.get_delegation_template("deep_review")

        assert "Task(" in template
        assert "general-purpose" in template
        assert "review" in template.lower()

    def test_template_for_multi_file_search(self) -> None:
        """Test template for multi_file_search operation."""
        load_state = MagicMock(return_value={})
        save_state = MagicMock()

        rules = DelegationRules(load_state=load_state, save_state=save_state)
        template = rules.get_delegation_template("multi_file_search")

        assert "Task(" in template
        assert "Explore" in template
        assert "thoroughness" in template

    def test_template_for_unknown_operation(self) -> None:
        """Test template for unknown operation (returns empty string)."""
        load_state = MagicMock(return_value={})
        save_state = MagicMock()

        rules = DelegationRules(load_state=load_state, save_state=save_state)
        template = rules.get_delegation_template("unknown_operation")

        assert template == ""


class TestFormatStatus:
    """Test DelegationRules.format_status() method."""

    def test_format_status_ok(self) -> None:
        """Test status formatting for OK status (<70%)."""
        load_state = MagicMock(return_value={})
        save_state = MagicMock()

        snapshot = {
            "current_tokens": 50000,
            "max_tokens": 200000,
            "usage_pct": 25.0,
            "last_check": "2025-11-08T12:00:00Z",
        }

        rules = DelegationRules(load_state=load_state, save_state=save_state)
        message = rules.format_status(snapshot, "OK - Continue normal workflow")

        assert "CONTEXT STATUS" in message
        assert "✅" in message
        assert "25.0%" in message
        assert "50,000" in message  # Thousands separator
        assert "200,000" in message
        assert "OK - Continue normal workflow" in message
        assert "2025-11-08T12:00:00Z" in message

    def test_format_status_warning(self) -> None:
        """Test status formatting for WARNING status (70-84%)."""
        load_state = MagicMock(return_value={})
        save_state = MagicMock()

        snapshot = {
            "current_tokens": 150000,
            "max_tokens": 200000,
            "usage_pct": 75.0,
            "last_check": "never",
        }

        rules = DelegationRules(load_state=load_state, save_state=save_state)
        message = rules.format_status(snapshot, "WARNING - Delegation RECOMMENDED")

        assert "⚠️" in message
        assert "75.0%" in message
        assert "WARNING - Delegation RECOMMENDED" in message

    def test_format_status_critical(self) -> None:
        """Test status formatting for CRITICAL status (>=85%)."""
        load_state = MagicMock(return_value={})
        save_state = MagicMock()

        snapshot = {
            "current_tokens": 180000,
            "max_tokens": 200000,
            "usage_pct": 90.0,
            "last_check": "never",
        }

        rules = DelegationRules(load_state=load_state, save_state=save_state)
        message = rules.format_status(snapshot, "CRITICAL - Delegation MANDATORY")

        assert "🚨" in message
        assert "90.0%" in message
        assert "CRITICAL - Delegation MANDATORY" in message

    def test_format_status_custom_heading(self) -> None:
        """Test status formatting with custom heading."""
        load_state = MagicMock(return_value={})
        save_state = MagicMock()

        snapshot = {
            "current_tokens": 50000,
            "max_tokens": 200000,
            "usage_pct": 25.0,
            "last_check": "never",
        }

        rules = DelegationRules(load_state=load_state, save_state=save_state)
        message = rules.format_status(snapshot, "OK", heading="Custom Heading")

        assert "CUSTOM HEADING" in message
        assert "CONTEXT STATUS" not in message


class TestDelegationRulesIntegration:
    """Integration tests for DelegationRules workflows."""

    def test_complete_workflow_below_threshold(self) -> None:
        """Test complete workflow when usage is below threshold."""
        state = {}
        load_state = MagicMock(return_value=state)
        save_state = MagicMock()

        rules = DelegationRules(load_state=load_state, save_state=save_state)

        # 1. Record context
        snapshot = rules.record_context(50000)
        assert snapshot["usage_pct"] == 25.0

        # 2. Check if delegation needed
        should_delegate, reason, pct = rules.should_delegate_context()
        assert should_delegate is False

        # 3. Get suggestion (should not recommend delegation)
        message = rules.suggest_delegation()
        assert "RECOMMENDATION" not in message

    def test_complete_workflow_above_threshold(self) -> None:
        """Test complete workflow when usage exceeds threshold."""
        state = {}
        load_state = MagicMock(return_value=state)
        save_state = MagicMock()

        rules = DelegationRules(load_state=load_state, save_state=save_state)

        # 1. Record high context usage
        snapshot = rules.record_context(150000)
        assert snapshot["usage_pct"] == 75.0

        # 2. Check if delegation needed
        should_delegate, reason, pct = rules.should_delegate_context()
        assert should_delegate is True
        assert reason == "WARNING - Delegation RECOMMENDED"

        # 3. Check operation projection
        should_delegate_op, reason_op = rules.should_delegate_operation("deep_review", snapshot)
        assert should_delegate_op is True  # 75% + 15% = 90% > 85%

        # 4. Record delegation
        result = rules.record_delegation("Deep review task")
        assert result["count"] == 1

        # 5. Verify context reset
        new_snapshot = rules.get_context_snapshot()
        assert new_snapshot["current_tokens"] == 0
