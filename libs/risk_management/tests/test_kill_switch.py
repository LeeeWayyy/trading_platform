"""
Integration tests for kill-switch functionality.

These tests require a real Redis connection (localhost:6379).

Tests cover:
- Kill-switch state management (ACTIVE ↔ ENGAGED)
- Engagement/disengagement operations
- Operator audit trail
- History tracking
- Error handling

Run with: pytest -m integration

NOTE: These tests are SKIPPED - they expect old behavior (get_status() auto-reinitializes).
See test_kill_switch_fail_closed.py for comprehensive tests of new fail-closed behavior.
"""

import json

import pytest

from libs.redis_client import RedisClient
from libs.risk_management.kill_switch import KillSwitch, KillSwitchState

# Mark all tests in this module as integration tests (require Redis)
# AND skip them - they expect old behavior (get_status() auto-reinitializes to ACTIVE)
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skip(
        reason="Tests outdated after fail-closed fix. "
        "See test_kill_switch_fail_closed.py for new comprehensive tests."
    ),
]


@pytest.fixture
def redis_client():
    """Create Redis client for testing."""
    client = RedisClient(host="localhost", port=6379, db=1)  # Use test DB
    yield client
    # Cleanup
    client.delete("kill_switch:state")
    client.delete("kill_switch:history")


@pytest.fixture
def kill_switch(redis_client):
    """Create KillSwitch instance for testing."""
    return KillSwitch(redis_client=redis_client)


class TestKillSwitchInitialization:
    """Test kill-switch initialization."""

    def test_initialization_creates_active_state(self, kill_switch):
        """Test kill-switch starts in ACTIVE state."""
        assert kill_switch.get_state() == KillSwitchState.ACTIVE
        assert not kill_switch.is_engaged()

    def test_initialization_idempotent(self, redis_client):
        """Test multiple initializations don't reset state."""
        ks1 = KillSwitch(redis_client=redis_client)
        ks1.engage(reason="Test", operator="test_user")

        # Second initialization should preserve state
        ks2 = KillSwitch(redis_client=redis_client)
        assert ks2.is_engaged()
        assert ks2.get_state() == KillSwitchState.ENGAGED


class TestKillSwitchEngagement:
    """Test kill-switch engagement operations."""

    def test_engage_from_active(self, kill_switch):
        """Test engaging kill-switch from ACTIVE state."""
        kill_switch.engage(
            reason="Market anomaly",
            operator="ops_team",
            details={"anomaly_type": "flash_crash"},
        )

        assert kill_switch.is_engaged()
        assert kill_switch.get_state() == KillSwitchState.ENGAGED

        status = kill_switch.get_status()
        assert status["state"] == "ENGAGED"
        assert status["engaged_by"] == "ops_team"
        assert status["engagement_reason"] == "Market anomaly"
        assert status["engagement_details"] == {"anomaly_type": "flash_crash"}
        assert status["engaged_at"] is not None

    def test_engage_already_engaged_raises_error(self, kill_switch):
        """Test engaging already-engaged kill-switch raises ValueError."""
        kill_switch.engage(reason="First engagement", operator="user1")

        with pytest.raises(ValueError, match="already engaged"):
            kill_switch.engage(reason="Second engagement", operator="user2")

    def test_engage_records_history(self, kill_switch):
        """Test engagement is recorded in history."""
        kill_switch.engage(reason="Test engagement", operator="test_user")

        history = kill_switch.get_history(limit=10)
        assert len(history) >= 1

        latest = history[0]
        assert latest["event"] == "ENGAGED"
        assert latest["operator"] == "test_user"
        assert latest["reason"] == "Test engagement"
        assert "timestamp" in latest

    def test_engage_increments_count(self, kill_switch):
        """Test engagement count increments."""
        status1 = kill_switch.get_status()
        initial_count = status1.get("engagement_count_today", 0)

        kill_switch.engage(reason="Test", operator="user")
        kill_switch.disengage(operator="user")
        kill_switch.engage(reason="Test 2", operator="user")

        status2 = kill_switch.get_status()
        assert status2["engagement_count_today"] == initial_count + 2


class TestKillSwitchDisengagement:
    """Test kill-switch disengagement operations."""

    def test_disengage_from_engaged(self, kill_switch):
        """Test disengaging kill-switch from ENGAGED state."""
        kill_switch.engage(reason="Test", operator="user")
        kill_switch.disengage(operator="ops_team", notes="Issue resolved")

        assert not kill_switch.is_engaged()
        assert kill_switch.get_state() == KillSwitchState.ACTIVE

        status = kill_switch.get_status()
        assert status["state"] == "ACTIVE"
        assert status["disengaged_by"] == "ops_team"
        assert status["disengagement_notes"] == "Issue resolved"
        assert status["disengaged_at"] is not None

    def test_disengage_already_active_raises_error(self, kill_switch):
        """Test disengaging already-active kill-switch raises ValueError."""
        with pytest.raises(ValueError, match="not engaged"):
            kill_switch.disengage(operator="user")

    def test_disengage_records_history(self, kill_switch):
        """Test disengagement is recorded in history."""
        kill_switch.engage(reason="Test", operator="user1")
        kill_switch.disengage(operator="user2", notes="Resolved")

        history = kill_switch.get_history(limit=10)
        assert len(history) >= 2

        # Most recent should be DISENGAGED
        latest = history[0]
        assert latest["event"] == "DISENGAGED"
        assert latest["operator"] == "user2"
        assert latest["notes"] == "Resolved"


class TestKillSwitchStatus:
    """Test kill-switch status retrieval."""

    def test_get_status_active(self, kill_switch):
        """Test status when kill-switch is ACTIVE."""
        status = kill_switch.get_status()

        assert status["state"] == "ACTIVE"
        assert status["engaged_at"] is None
        assert status["engaged_by"] is None
        assert status["engagement_reason"] is None

    def test_get_status_engaged(self, kill_switch):
        """Test status when kill-switch is ENGAGED."""
        kill_switch.engage(reason="Test reason", operator="test_operator")

        status = kill_switch.get_status()

        assert status["state"] == "ENGAGED"
        assert status["engaged_by"] == "test_operator"
        assert status["engagement_reason"] == "Test reason"
        assert isinstance(status["engaged_at"], str)
        assert status["disengaged_at"] is None


class TestKillSwitchHistory:
    """Test kill-switch history tracking."""

    def test_history_empty_initially(self, kill_switch):
        """Test history is empty when kill-switch just initialized."""
        history = kill_switch.get_history()
        # May have 0 or more depending on whether state existed before
        assert isinstance(history, list)

    def test_history_tracks_multiple_events(self, kill_switch):
        """Test history tracks multiple engage/disengage cycles."""
        kill_switch.engage(reason="Cycle 1", operator="user1")
        kill_switch.disengage(operator="user1")
        kill_switch.engage(reason="Cycle 2", operator="user2")

        history = kill_switch.get_history(limit=10)
        assert len(history) >= 3

        # Check events are in reverse chronological order (most recent first)
        assert history[0]["event"] == "ENGAGED"
        assert history[1]["event"] == "DISENGAGED"
        assert history[2]["event"] == "ENGAGED"

    def test_history_respects_limit(self, kill_switch):
        """Test history limit parameter works."""
        # Create multiple events
        for i in range(5):
            kill_switch.engage(reason=f"Event {i}", operator="user")
            kill_switch.disengage(operator="user")

        history = kill_switch.get_history(limit=3)
        assert len(history) <= 3


class TestKillSwitchAuditTrail:
    """Test kill-switch audit trail requirements."""

    def test_engagement_requires_operator(self, kill_switch):
        """Test engagement requires operator identification."""
        # Should work with operator
        kill_switch.engage(reason="Test", operator="ops_team")
        assert kill_switch.is_engaged()

    def test_engagement_requires_reason(self, kill_switch):
        """Test engagement requires reason."""
        # Should work with reason
        kill_switch.engage(reason="Emergency halt", operator="ops")
        status = kill_switch.get_status()
        assert status["engagement_reason"] == "Emergency halt"

    def test_disengagement_requires_operator(self, kill_switch):
        """Test disengagement requires operator identification."""
        kill_switch.engage(reason="Test", operator="user1")
        kill_switch.disengage(operator="user2")

        status = kill_switch.get_status()
        assert status["disengaged_by"] == "user2"

    def test_audit_trail_preserved_in_redis(self, redis_client, kill_switch):
        """Test audit trail persists in Redis."""
        kill_switch.engage(reason="Audit test", operator="auditor")

        # Read directly from Redis
        state_json = redis_client.get("kill_switch:state")
        state_data = json.loads(state_json)

        assert state_data["engaged_by"] == "auditor"
        assert state_data["engagement_reason"] == "Audit test"


class TestKillSwitchEdgeCases:
    """Test edge cases and error handling."""

    def test_state_survives_reconnection(self, redis_client):
        """Test kill-switch state survives Redis reconnection."""
        ks1 = KillSwitch(redis_client=redis_client)
        ks1.engage(reason="Before disconnect", operator="user")

        # Simulate reconnection with new instance
        ks2 = KillSwitch(redis_client=redis_client)
        assert ks2.is_engaged()
        assert ks2.get_status()["engagement_reason"] == "Before disconnect"

    def test_optional_details_and_notes(self, kill_switch):
        """Test engagement details and disengagement notes are optional."""
        # Engage without details
        kill_switch.engage(reason="Test", operator="user1")

        # Disengage without notes
        kill_switch.disengage(operator="user2")

        # Should not raise errors
        status = kill_switch.get_status()
        assert status["state"] == "ACTIVE"
