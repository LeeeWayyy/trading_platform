"""
P0 Coverage Tests for KillSwitch - Additional branch coverage to reach 95%+ target.

Missing branches from coverage report (88% → 95%):
- Lines 278-279: engage() exception handling (else branch for unexpected errors)
- Lines 386-387: disengage() exception handling (else branch for unexpected errors)
- Lines 464-466: get_history() with entries (json.loads/reversed)
"""

import json
from datetime import UTC, datetime
from unittest.mock import Mock

import pytest

from libs.core.redis_client import RedisClient
from libs.trading.risk_management.kill_switch import KillSwitch, KillSwitchState


class TestKillSwitchExceptionHandling:
    """Tests for exception handling in engage/disengage operations."""

    @pytest.fixture()
    def mock_redis_client(self):
        """Create mock Redis client for testing."""
        return Mock(spec=RedisClient)

    def test_engage_unexpected_exception(self, mock_redis_client):
        """Test engage() handles unexpected exceptions (not already-engaged or state-missing)."""
        # Mock state exists (so __init__ doesn't raise)
        state_data = {
            "state": KillSwitchState.ACTIVE.value,
            "engaged_at": None,
            "engaged_by": None,
            "engagement_reason": None,
        }
        mock_redis_client.get.return_value = json.dumps(state_data)

        # Mock eval() to raise unexpected exception
        mock_redis_client.eval.side_effect = RuntimeError("Redis connection lost")

        kill_switch = KillSwitch(redis_client=mock_redis_client)

        # Attempt to engage should raise the unexpected exception
        with pytest.raises(RuntimeError, match="Redis connection lost"):
            kill_switch.engage(reason="Test", operator="test_op")

    def test_disengage_unexpected_exception(self, mock_redis_client):
        """Test disengage() handles unexpected exceptions (not not-engaged or state-missing)."""
        # Mock state exists and ENGAGED
        state_data = {
            "state": KillSwitchState.ENGAGED.value,
            "engaged_at": datetime.now(UTC).isoformat(),
            "engaged_by": "test_op",
            "engagement_reason": "Test",
        }
        mock_redis_client.get.return_value = json.dumps(state_data)

        # Mock eval() to raise unexpected exception
        mock_redis_client.eval.side_effect = ConnectionError("Network failure")

        kill_switch = KillSwitch(redis_client=mock_redis_client)

        # Attempt to disengage should raise the unexpected exception
        with pytest.raises(ConnectionError, match="Network failure"):
            kill_switch.disengage(operator="test_op")


class TestKillSwitchGetHistory:
    """Tests for get_history() method edge cases."""

    @pytest.fixture()
    def mock_redis_client(self):
        """Create mock Redis client for testing."""
        return Mock(spec=RedisClient)

    def test_get_history_empty(self, mock_redis_client):
        """Test get_history() when no history exists."""
        # Mock state exists
        state_data = {
            "state": KillSwitchState.ACTIVE.value,
            "engaged_at": None,
        }
        mock_redis_client.get.return_value = json.dumps(state_data)

        # Mock empty history
        mock_redis_client.lrange.return_value = []

        kill_switch = KillSwitch(redis_client=mock_redis_client)
        history = kill_switch.get_history()

        # Verify empty list returned
        assert history == []
        mock_redis_client.lrange.assert_called_once()

    def test_get_history_with_entries(self, mock_redis_client):
        """Test get_history() with multiple entries (reversed for most-recent-first)."""
        # Mock state exists
        state_data = {
            "state": KillSwitchState.ACTIVE.value,
            "engaged_at": None,
        }
        mock_redis_client.get.return_value = json.dumps(state_data)

        # Mock history with 3 entries (oldest to newest in Redis)
        entry1 = {
            "event": "ENGAGED",
            "timestamp": "2026-01-15T10:00:00Z",
            "operator": "ops1",
            "reason": "Test1",
        }
        entry2 = {
            "event": "DISENGAGED",
            "timestamp": "2026-01-15T10:05:00Z",
            "operator": "ops1",
            "notes": "Resolved",
        }
        entry3 = {
            "event": "ENGAGED",
            "timestamp": "2026-01-15T10:10:00Z",
            "operator": "ops2",
            "reason": "Test2",
        }

        # Redis stores oldest to newest, but get_history reverses for most-recent-first
        mock_redis_client.lrange.return_value = [
            json.dumps(entry1),
            json.dumps(entry2),
            json.dumps(entry3),
        ]

        kill_switch = KillSwitch(redis_client=mock_redis_client)
        history = kill_switch.get_history(limit=10)

        # Verify entries returned in reversed order (newest first)
        assert len(history) == 3
        assert history[0] == entry3  # Most recent
        assert history[1] == entry2
        assert history[2] == entry1  # Oldest

        # Verify lrange called with correct limit
        mock_redis_client.lrange.assert_called_once_with("kill_switch:history", -10, -1)


class TestKillSwitchGetStatusEdgeCases:
    """Tests for get_status() edge cases."""

    @pytest.fixture()
    def mock_redis_client(self):
        """Create mock Redis client for testing."""
        return Mock(spec=RedisClient)

    def test_get_status_returns_full_state_data(self, mock_redis_client):
        """Test get_status() returns complete state dictionary."""
        # Mock comprehensive state data
        state_data = {
            "state": KillSwitchState.ENGAGED.value,
            "engaged_at": "2026-01-15T10:00:00Z",
            "engaged_by": "test_operator",
            "engagement_reason": "Emergency test",
            "engagement_details": {"severity": "high"},
            "disengaged_at": None,
            "disengaged_by": None,
            "engagement_count_today": 3,
        }

        # First call for __init__, second for get_status()
        mock_redis_client.get.side_effect = [
            json.dumps(state_data),  # __init__ call
            json.dumps(state_data),  # get_status() call
        ]

        kill_switch = KillSwitch(redis_client=mock_redis_client)
        status = kill_switch.get_status()

        # Verify all fields returned
        assert status == state_data
        assert status["state"] == "ENGAGED"
        assert status["engaged_by"] == "test_operator"
        assert status["engagement_count_today"] == 3
        assert status["disengaged_at"] is None
