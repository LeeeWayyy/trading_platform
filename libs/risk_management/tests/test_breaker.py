"""Tests for circuit breaker state machine."""

import json
import pytest
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from libs.risk_management.breaker import (
    CircuitBreaker,
    CircuitBreakerState,
    TripReason,
)
from libs.risk_management.exceptions import CircuitBreakerError


@pytest.fixture
def mock_redis():
    """Mock Redis client for testing."""
    redis = MagicMock()
    redis._state = {}  # Internal state storage for testing

    def mock_get(key):
        return redis._state.get(key)

    def mock_set(key, value, ttl=None):
        redis._state[key] = value

    redis.get = MagicMock(side_effect=mock_get)
    redis.set = MagicMock(side_effect=mock_set)

    return redis


@pytest.fixture
def breaker(mock_redis):
    """Circuit breaker instance with mock Redis."""
    return CircuitBreaker(redis_client=mock_redis)


class TestCircuitBreakerInitialization:
    """Test circuit breaker initialization."""

    def test_initialization_creates_default_state(self, mock_redis):
        """Test initialization creates OPEN state in Redis."""
        breaker = CircuitBreaker(redis_client=mock_redis)

        # Verify state created in Redis
        state_json = mock_redis.get("circuit_breaker:state")
        assert state_json is not None

        state = json.loads(state_json)
        assert state["state"] == CircuitBreakerState.OPEN.value
        assert state["tripped_at"] is None
        assert state["trip_reason"] is None
        assert state["trip_count_today"] == 0

    def test_initialization_preserves_existing_state(self, mock_redis):
        """Test initialization doesn't overwrite existing state."""
        # Set existing state
        existing_state = {
            "state": CircuitBreakerState.TRIPPED.value,
            "tripped_at": "2025-10-19T15:30:00+00:00",
            "trip_reason": "TEST",
            "trip_details": None,
            "reset_at": None,
            "reset_by": None,
            "trip_count_today": 1,
        }
        mock_redis._state["circuit_breaker:state"] = json.dumps(existing_state)

        # Initialize breaker
        breaker = CircuitBreaker(redis_client=mock_redis)

        # Verify state unchanged
        state_json = mock_redis.get("circuit_breaker:state")
        state = json.loads(state_json)
        assert state["state"] == CircuitBreakerState.TRIPPED.value
        assert state["trip_count_today"] == 1


class TestCircuitBreakerState:
    """Test circuit breaker state queries."""

    def test_get_state_returns_open_initially(self, breaker):
        """Test get_state returns OPEN for new breaker."""
        assert breaker.get_state() == CircuitBreakerState.OPEN

    def test_is_tripped_false_when_open(self, breaker):
        """Test is_tripped returns False when OPEN."""
        assert breaker.is_tripped() is False

    def test_is_tripped_true_when_tripped(self, breaker):
        """Test is_tripped returns True when TRIPPED."""
        breaker.trip("TEST_REASON")
        assert breaker.is_tripped() is True

    def test_is_tripped_false_when_quiet_period(self, breaker):
        """Test is_tripped returns False during QUIET_PERIOD."""
        breaker.trip("TEST_REASON")
        breaker.reset()
        assert breaker.is_tripped() is False


class TestCircuitBreakerTrip:
    """Test circuit breaker trip operation."""

    def test_trip_transitions_from_open_to_tripped(self, breaker):
        """Test trip() transitions state from OPEN to TRIPPED."""
        assert breaker.get_state() == CircuitBreakerState.OPEN

        breaker.trip("DAILY_LOSS_EXCEEDED")

        assert breaker.get_state() == CircuitBreakerState.TRIPPED
        assert breaker.is_tripped() is True

    def test_trip_stores_reason(self, breaker):
        """Test trip() stores trip reason."""
        breaker.trip("DAILY_LOSS_EXCEEDED")

        reason = breaker.get_trip_reason()
        assert reason == "DAILY_LOSS_EXCEEDED"

    def test_trip_stores_details(self, breaker):
        """Test trip() stores optional details."""
        details = {"daily_loss": -5234.56, "threshold": -5000.00}
        breaker.trip("DAILY_LOSS_EXCEEDED", details=details)

        stored_details = breaker.get_trip_details()
        assert stored_details == details

    def test_trip_without_details(self, breaker):
        """Test trip() works without details."""
        breaker.trip("MANUAL")

        assert breaker.is_tripped() is True
        assert breaker.get_trip_details() is None

    def test_trip_increments_count(self, breaker):
        """Test trip() increments trip_count_today."""
        breaker.trip("TEST_1")
        status1 = breaker.get_status()
        assert status1["trip_count_today"] == 1

        # Reset and trip again
        breaker.reset()
        time.sleep(1)  # Wait for quiet period to not immediately auto-reset
        breaker.trip("TEST_2")
        status2 = breaker.get_status()
        assert status2["trip_count_today"] == 2

    def test_trip_idempotent_when_already_tripped(self, breaker):
        """Test trip() is idempotent (safe to call when already TRIPPED)."""
        breaker.trip("FIRST_REASON")
        initial_status = breaker.get_status()

        # Trip again (should be no-op)
        breaker.trip("SECOND_REASON")
        final_status = breaker.get_status()

        # State unchanged (still TRIPPED with first reason)
        assert final_status["state"] == CircuitBreakerState.TRIPPED.value
        assert final_status["trip_reason"] == "FIRST_REASON"  # Original reason preserved
        assert final_status["trip_count_today"] == 1  # Not incremented

    def test_trip_from_quiet_period(self, breaker):
        """Test trip() works when transitioning from QUIET_PERIOD."""
        breaker.trip("FIRST_REASON")
        breaker.reset()
        assert breaker.get_state() == CircuitBreakerState.QUIET_PERIOD

        # Trip again from QUIET_PERIOD
        breaker.trip("SECOND_REASON")

        assert breaker.is_tripped() is True
        assert breaker.get_trip_reason() == "SECOND_REASON"


class TestCircuitBreakerReset:
    """Test circuit breaker reset operation."""

    def test_reset_transitions_to_quiet_period(self, breaker):
        """Test reset() transitions from TRIPPED to QUIET_PERIOD."""
        breaker.trip("TEST_REASON")
        assert breaker.get_state() == CircuitBreakerState.TRIPPED

        breaker.reset()

        assert breaker.get_state() == CircuitBreakerState.QUIET_PERIOD

    def test_reset_raises_error_when_not_tripped(self, breaker):
        """Test reset() raises error if not TRIPPED."""
        # Try to reset when OPEN
        with pytest.raises(CircuitBreakerError) as exc_info:
            breaker.reset()

        assert "current state is OPEN" in str(exc_info.value)
        assert "must be TRIPPED" in str(exc_info.value)

    def test_reset_stores_reset_by(self, breaker):
        """Test reset() stores reset_by identifier."""
        breaker.trip("TEST_REASON")
        breaker.reset(reset_by="operator_alice")

        status = breaker.get_status()
        assert status["reset_by"] == "operator_alice"

    def test_reset_default_reset_by(self, breaker):
        """Test reset() uses 'system' as default reset_by."""
        breaker.trip("TEST_REASON")
        breaker.reset()  # No reset_by argument

        status = breaker.get_status()
        assert status["reset_by"] == "system"

    def test_reset_raises_error_when_quiet_period(self, breaker):
        """Test reset() raises error if already in QUIET_PERIOD."""
        breaker.trip("TEST_REASON")
        breaker.reset()

        # Try to reset again
        with pytest.raises(CircuitBreakerError) as exc_info:
            breaker.reset()

        assert "current state is QUIET_PERIOD" in str(exc_info.value)


class TestCircuitBreakerQuietPeriod:
    """Test circuit breaker quiet period auto-transition."""

    def test_quiet_period_auto_transitions_to_open(self, breaker, mock_redis):
        """Test QUIET_PERIOD auto-transitions to OPEN after duration."""
        # Trip and reset
        breaker.trip("TEST_REASON")
        breaker.reset()
        assert breaker.get_state() == CircuitBreakerState.QUIET_PERIOD

        # Manually expire quiet period by modifying reset_at timestamp
        state_json = mock_redis.get("circuit_breaker:state")
        state = json.loads(state_json)
        old_reset_time = datetime.now(timezone.utc) - timedelta(seconds=301)  # 1 second past duration
        state["reset_at"] = old_reset_time.isoformat()
        mock_redis.set("circuit_breaker:state", json.dumps(state))

        # Check state (should auto-transition)
        current_state = breaker.get_state()
        assert current_state == CircuitBreakerState.OPEN

    def test_quiet_period_does_not_transition_early(self, breaker):
        """Test QUIET_PERIOD doesn't transition before duration."""
        breaker.trip("TEST_REASON")
        breaker.reset()
        assert breaker.get_state() == CircuitBreakerState.QUIET_PERIOD

        # Immediately check again (should still be QUIET_PERIOD)
        assert breaker.get_state() == CircuitBreakerState.QUIET_PERIOD

    def test_quiet_period_clears_trip_reason_on_transition(self, breaker, mock_redis):
        """Test transition to OPEN clears trip_reason and trip_details."""
        breaker.trip("TEST_REASON", details={"test": "data"})
        breaker.reset()

        # Force transition to OPEN
        state_json = mock_redis.get("circuit_breaker:state")
        state = json.loads(state_json)
        old_reset_time = datetime.now(timezone.utc) - timedelta(seconds=301)
        state["reset_at"] = old_reset_time.isoformat()
        mock_redis.set("circuit_breaker:state", json.dumps(state))

        breaker.get_state()  # Trigger auto-transition

        # Verify trip_reason cleared
        assert breaker.get_trip_reason() is None
        assert breaker.get_trip_details() is None


class TestCircuitBreakerStatus:
    """Test circuit breaker status queries."""

    def test_get_status_returns_complete_state(self, breaker):
        """Test get_status() returns all state fields."""
        status = breaker.get_status()

        assert "state" in status
        assert "tripped_at" in status
        assert "trip_reason" in status
        assert "trip_details" in status
        assert "reset_at" in status
        assert "reset_by" in status
        assert "trip_count_today" in status

    def test_get_status_when_tripped(self, breaker):
        """Test get_status() reflects TRIPPED state."""
        details = {"daily_loss": -5234.56}
        breaker.trip("DAILY_LOSS_EXCEEDED", details=details)

        status = breaker.get_status()

        assert status["state"] == CircuitBreakerState.TRIPPED.value
        assert status["trip_reason"] == "DAILY_LOSS_EXCEEDED"
        assert status["trip_details"] == details
        assert status["tripped_at"] is not None
        assert status["reset_at"] is None

    def test_get_status_when_reset(self, breaker):
        """Test get_status() reflects QUIET_PERIOD after reset."""
        breaker.trip("TEST_REASON")
        breaker.reset(reset_by="operator")

        status = breaker.get_status()

        assert status["state"] == CircuitBreakerState.QUIET_PERIOD.value
        assert status["reset_by"] == "operator"
        assert status["reset_at"] is not None


class TestCircuitBreakerTripReasons:
    """Test different trip reasons."""

    def test_trip_with_enum_reason(self, breaker):
        """Test trip with TripReason enum."""
        breaker.trip(TripReason.DAILY_LOSS_EXCEEDED.value)

        assert breaker.get_trip_reason() == TripReason.DAILY_LOSS_EXCEEDED.value

    def test_trip_with_custom_reason(self, breaker):
        """Test trip with custom string reason."""
        breaker.trip("CUSTOM_REASON_FOR_TESTING")

        assert breaker.get_trip_reason() == "CUSTOM_REASON_FOR_TESTING"

    def test_all_predefined_reasons(self, breaker):
        """Test all predefined TripReason enums."""
        reasons = [
            TripReason.DAILY_LOSS_EXCEEDED,
            TripReason.MAX_DRAWDOWN,
            TripReason.DATA_STALE,
            TripReason.BROKER_ERRORS,
            TripReason.MANUAL,
        ]

        for reason in reasons:
            # Create fresh breaker for each test
            breaker._initialize_state()
            breaker.trip(reason.value)

            assert breaker.is_tripped() is True
            assert breaker.get_trip_reason() == reason.value


class TestCircuitBreakerHistory:
    """Test circuit breaker trip history logging."""

    def test_history_entry_created_on_trip(self, breaker, mock_redis):
        """Test trip creates history entry in Redis."""
        breaker.trip("TEST_REASON", details={"test": "data"})

        # Check for history entry (key pattern: circuit_breaker:trip_history:*)
        history_keys = [
            k
            for k in mock_redis._state.keys()
            if k.startswith("circuit_breaker:trip_history:")
        ]
        assert len(history_keys) == 1

        # Verify history entry contents
        history_json = mock_redis._state[history_keys[0]]
        history_entry = json.loads(history_json)
        assert history_entry["reason"] == "TEST_REASON"
        assert history_entry["details"] == {"test": "data"}
        assert history_entry["tripped_at"] is not None

    def test_multiple_trips_create_multiple_history_entries(self, breaker, mock_redis):
        """Test multiple trips create separate history entries."""
        breaker.trip("REASON_1")
        breaker.reset()
        time.sleep(0.01)  # Ensure different timestamps
        breaker.trip("REASON_2")

        history_keys = [
            k
            for k in mock_redis._state.keys()
            if k.startswith("circuit_breaker:trip_history:")
        ]
        assert len(history_keys) == 2
