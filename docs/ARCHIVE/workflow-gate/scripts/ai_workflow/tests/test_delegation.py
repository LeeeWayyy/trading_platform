"""
Tests for delegation.py module.

Tests DelegationRules for context monitoring and delegation recommendations.
"""

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from ai_workflow.constants import (
    CONTEXT_CRITICAL_PCT,
    CONTEXT_WARN_PCT,
    DEFAULT_MAX_TOKENS,
)
from ai_workflow.delegation import DelegationRules


class TestDelegationRulesInit:
    """Tests for DelegationRules initialization."""

    def test_initializes_with_callables(self):
        """Should initialize with load/save callables."""
        load_fn = MagicMock(return_value={})
        save_fn = MagicMock()

        rules = DelegationRules(load_fn, save_fn)

        assert rules._load_state is load_fn
        assert rules._save_state is save_fn

    def test_accepts_locked_modify_callable(self):
        """Should accept optional locked_modify_state callable."""
        load_fn = MagicMock(return_value={})
        save_fn = MagicMock()
        locked_fn = MagicMock()

        rules = DelegationRules(load_fn, save_fn, locked_fn)

        assert rules._locked_modify_state is locked_fn


class TestContextThresholds:
    """Tests for context threshold constants."""

    def test_warn_threshold(self):
        """Should have correct warning threshold."""
        assert DelegationRules.CONTEXT_WARN_PCT == CONTEXT_WARN_PCT

    def test_critical_threshold(self):
        """Should have correct critical threshold."""
        assert DelegationRules.CONTEXT_CRITICAL_PCT == CONTEXT_CRITICAL_PCT

    def test_default_max_tokens(self):
        """Should have correct default max tokens."""
        assert DelegationRules.DEFAULT_MAX_TOKENS == DEFAULT_MAX_TOKENS


class TestOperationCosts:
    """Tests for operation cost estimates."""

    def test_has_expected_operations(self):
        """Should have expected operation types."""
        costs = DelegationRules.OPERATION_COSTS

        assert "full_ci" in costs
        assert "deep_review" in costs
        assert "multi_file_search" in costs
        assert "test_suite" in costs
        assert "code_analysis" in costs
        assert "simple_fix" in costs

    def test_costs_are_positive(self):
        """All costs should be positive integers."""
        for op, cost in DelegationRules.OPERATION_COSTS.items():
            assert isinstance(cost, int), f"{op} cost should be int"
            assert cost > 0, f"{op} cost should be positive"


class TestGetContextSnapshot:
    """Tests for get_context_snapshot method."""

    def test_returns_snapshot_from_state(self):
        """Should return snapshot from loaded state."""
        state = {
            "context": {
                "current_tokens": 50000,
                "max_tokens": 200000,
                "last_check_timestamp": "2024-01-01T00:00:00Z",
            }
        }
        load_fn = MagicMock(return_value=state)
        save_fn = MagicMock()

        rules = DelegationRules(load_fn, save_fn)
        snapshot = rules.get_context_snapshot()

        assert snapshot["current_tokens"] == 50000
        assert snapshot["max_tokens"] == 200000
        assert snapshot["usage_pct"] == 25.0
        assert snapshot["error"] is None

    def test_uses_defaults_when_missing(self):
        """Should use defaults when context not in state."""
        state = {}
        load_fn = MagicMock(return_value=state)
        save_fn = MagicMock()

        rules = DelegationRules(load_fn, save_fn)
        snapshot = rules.get_context_snapshot()

        assert snapshot["current_tokens"] == 0
        assert snapshot["max_tokens"] == DEFAULT_MAX_TOKENS
        assert snapshot["usage_pct"] == 0.0

    def test_handles_zero_max_tokens(self):
        """Should handle zero max_tokens gracefully."""
        state = {"context": {"current_tokens": 100, "max_tokens": 0}}
        load_fn = MagicMock(return_value=state)
        save_fn = MagicMock()

        rules = DelegationRules(load_fn, save_fn)
        snapshot = rules.get_context_snapshot()

        assert snapshot["usage_pct"] == 0.0
        assert snapshot["error"] == "Invalid max_tokens"

    def test_accepts_state_parameter(self):
        """Should use provided state instead of loading."""
        state = {"context": {"current_tokens": 10000, "max_tokens": 100000}}
        load_fn = MagicMock()
        save_fn = MagicMock()

        rules = DelegationRules(load_fn, save_fn)
        snapshot = rules.get_context_snapshot(state)

        # Should not have called load_fn
        load_fn.assert_not_called()
        assert snapshot["usage_pct"] == 10.0


class TestRecordContext:
    """Tests for record_context method."""

    def test_requires_locked_modify_state(self):
        """Should raise TypeError if locked_modify_state not provided."""
        state = {"context": {"current_tokens": 0, "max_tokens": 200000}}
        save_fn = MagicMock()

        rules = DelegationRules(lambda: state, save_fn)  # No locked_modify_state

        with pytest.raises(TypeError) as exc_info:
            rules.record_context(75000)
        assert "locked_modify_state" in str(exc_info.value)

    def test_updates_context_tokens(self):
        """Should update current_tokens in state via locked_modify."""
        state = {"context": {"current_tokens": 0, "max_tokens": 200000}}
        save_fn = MagicMock()

        def locked_fn(modifier):
            modifier(state)
            return state

        rules = DelegationRules(lambda: state, save_fn, locked_fn)
        rules.record_context(75000)

        assert state["context"]["current_tokens"] == 75000

    def test_clamps_negative_tokens(self):
        """Should clamp negative tokens to 0."""
        state = {"context": {}}
        save_fn = MagicMock()

        def locked_fn(modifier):
            modifier(state)
            return state

        rules = DelegationRules(lambda: state, save_fn, locked_fn)
        rules.record_context(-100)

        assert state["context"]["current_tokens"] == 0

    def test_updates_timestamp(self):
        """Should update last_check_timestamp."""
        state = {"context": {}}
        save_fn = MagicMock()

        def locked_fn(modifier):
            modifier(state)
            return state

        rules = DelegationRules(lambda: state, save_fn, locked_fn)
        rules.record_context(50000)

        assert "last_check_timestamp" in state["context"]
        # Should be valid ISO timestamp
        datetime.fromisoformat(state["context"]["last_check_timestamp"])

    def test_uses_locked_modify_when_available(self):
        """Should use locked_modify_state when available."""
        state = {"context": {}}
        save_fn = MagicMock()
        locked_fn = MagicMock(return_value=state)

        rules = DelegationRules(lambda: state, save_fn, locked_fn)
        rules.record_context(50000)

        locked_fn.assert_called_once()
        save_fn.assert_not_called()


class TestCheckThreshold:
    """Tests for check_threshold method."""

    def test_returns_ok_below_warn(self):
        """Should return ok when below warning threshold."""
        state = {"context": {"current_tokens": 50000, "max_tokens": 200000}}

        rules = DelegationRules(lambda: state, MagicMock())
        result = rules.check_threshold()

        assert result["level"] == "ok"
        assert result["usage_pct"] == 25.0
        assert "normal" in result["recommendation"].lower()

    def test_returns_warning_at_threshold(self):
        """Should return warning at warning threshold."""
        state = {"context": {"current_tokens": 140000, "max_tokens": 200000}}  # 70%

        rules = DelegationRules(lambda: state, MagicMock())
        result = rules.check_threshold()

        assert result["level"] == "warning"
        assert result["usage_pct"] == 70.0
        assert "consider" in result["recommendation"].lower()

    def test_returns_critical_at_threshold(self):
        """Should return critical at critical threshold."""
        state = {"context": {"current_tokens": 170000, "max_tokens": 200000}}  # 85%

        rules = DelegationRules(lambda: state, MagicMock())
        result = rules.check_threshold()

        assert result["level"] == "critical"
        assert result["usage_pct"] == 85.0
        assert "mandatory" in result["recommendation"].lower()

    def test_accepts_state_parameter(self):
        """Should use provided state parameter."""
        state = {"context": {"current_tokens": 10000, "max_tokens": 100000}}

        rules = DelegationRules(MagicMock(), MagicMock())
        result = rules.check_threshold(state)

        assert result["usage_pct"] == 10.0


class TestProjectOperationCost:
    """Tests for project_operation_cost method."""

    def test_projects_operation_cost(self):
        """Should project operation cost."""
        state = {"context": {"current_tokens": 50000, "max_tokens": 200000}}

        rules = DelegationRules(lambda: state, MagicMock())
        result = rules.project_operation_cost("full_ci")

        assert result["operation"] == "full_ci"
        assert result["estimated_cost"] == DelegationRules.OPERATION_COSTS["full_ci"]
        assert result["current_tokens"] == 50000
        assert result["projected_tokens"] == 50000 + result["estimated_cost"]

    def test_uses_default_cost_for_unknown(self):
        """Should use default cost for unknown operations."""
        state = {"context": {"current_tokens": 0, "max_tokens": 200000}}

        rules = DelegationRules(lambda: state, MagicMock())
        result = rules.project_operation_cost("unknown_operation")

        assert result["estimated_cost"] == 5000  # Default

    def test_detects_would_exceed_critical(self):
        """Should detect if operation would exceed critical threshold."""
        # 80% usage, full_ci (50k) would push to 105k = 52.5%... not critical
        # Let's use a state that's already at 70%
        state = {"context": {"current_tokens": 140000, "max_tokens": 200000}}  # 70%

        rules = DelegationRules(lambda: state, MagicMock())
        result = rules.project_operation_cost("full_ci")  # +50k = 95%

        assert result["would_exceed_critical"] is True
        assert "delegate" in result["recommendation"].lower()


class TestRecordDelegation:
    """Tests for record_delegation method."""

    def test_requires_locked_modify_state(self):
        """Should raise TypeError if locked_modify_state not provided."""
        state = {
            "subagent_delegations": [],
            "context": {"current_tokens": 150000},
        }
        save_fn = MagicMock()

        rules = DelegationRules(lambda: state, save_fn)  # No locked_modify_state

        with pytest.raises(TypeError) as exc_info:
            rules.record_delegation("Delegated large search")
        assert "locked_modify_state" in str(exc_info.value)

    def test_records_delegation(self):
        """Should record delegation and reset context via locked_modify."""
        state = {
            "subagent_delegations": [],
            "context": {"current_tokens": 150000},
        }
        save_fn = MagicMock()

        def locked_fn(modifier):
            modifier(state)
            return state

        rules = DelegationRules(lambda: state, save_fn, locked_fn)
        rules.record_delegation("Delegated large search")

        assert len(state["subagent_delegations"]) == 1
        assert state["subagent_delegations"][0]["description"] == "Delegated large search"
        assert state["context"]["current_tokens"] == 0

    def test_uses_locked_modify_when_available(self):
        """Should use locked_modify_state when available."""
        state = {"subagent_delegations": [], "context": {}}
        locked_fn = MagicMock(return_value=state)

        rules = DelegationRules(lambda: state, MagicMock(), locked_fn)
        rules.record_delegation("Test delegation")

        locked_fn.assert_called_once()


class TestSuggestDelegation:
    """Tests for suggest_delegation method."""

    def test_prints_status_ok(self, capsys):
        """Should print status when ok."""
        state = {"context": {"current_tokens": 10000, "max_tokens": 200000}}

        rules = DelegationRules(lambda: state, MagicMock())
        rules.suggest_delegation()

        captured = capsys.readouterr()
        assert "10,000" in captured.out
        assert "OK" in captured.out
        assert "No delegation needed" in captured.out

    def test_prints_suggestions_when_warning(self, capsys):
        """Should print suggestions when warning."""
        state = {"context": {"current_tokens": 160000, "max_tokens": 200000}}  # 80%

        rules = DelegationRules(lambda: state, MagicMock())
        rules.suggest_delegation()

        captured = capsys.readouterr()
        assert "WARNING" in captured.out
        assert "Suggested delegations" in captured.out
        assert "Multi-file searches" in captured.out

    def test_prints_documentation_reference(self, capsys):
        """Should print documentation reference when delegation needed."""
        state = {"context": {"current_tokens": 170000, "max_tokens": 200000}}  # 85%

        rules = DelegationRules(lambda: state, MagicMock())
        rules.suggest_delegation()

        captured = capsys.readouterr()
        assert "16-subagent-delegation.md" in captured.out
