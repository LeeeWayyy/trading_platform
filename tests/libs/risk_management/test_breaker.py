"""
Unit tests for CircuitBreaker state machine.

Tests cover:
- State initialization and transitions
- Trip/reset operations with atomic Redis transactions
- Quiet period expiration logic
- Status queries (get_state, is_tripped, get_trip_reason, etc.)
- Trip history tracking
- Concurrent modification handling (WatchError retry logic)
- Edge cases (already tripped, invalid reset, state consistency)
"""

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock, patch

import pytest
from redis.exceptions import WatchError

from libs.redis_client import RedisClient
from libs.risk_management.breaker import CircuitBreaker, CircuitBreakerState, TripReason
from libs.risk_management.exceptions import CircuitBreakerError


class TestCircuitBreakerInitialization:
    """Tests for CircuitBreaker initialization."""

    @pytest.fixture
    def mock_redis_client(self):
        """Create mock Redis client for testing."""
        mock_redis = Mock(spec=RedisClient)
        mock_redis._client = Mock()
        return mock_redis

    def test_initialization_with_no_existing_state(self, mock_redis_client):
        """Test initialization when no state exists in Redis."""
        # Mock: No existing state
        mock_redis_client.get.return_value = None

        breaker = CircuitBreaker(redis_client=mock_redis_client)

        # Verify state key and history key set correctly
        assert breaker.state_key == "circuit_breaker:state"
        assert breaker.history_key == "circuit_breaker:trip_history"

        # Verify default state was written
        mock_redis_client.set.assert_called_once()
        call_args = mock_redis_client.set.call_args
        assert call_args[0][0] == "circuit_breaker:state"

        state_data = json.loads(call_args[0][1])
        assert state_data["state"] == CircuitBreakerState.OPEN.value
        assert state_data["tripped_at"] is None
        assert state_data["trip_reason"] is None
        assert state_data["trip_count_today"] == 0

    def test_initialization_with_existing_state(self, mock_redis_client):
        """Test initialization when state already exists in Redis."""
        # Mock: Existing state in TRIPPED
        existing_state = {
            "state": CircuitBreakerState.TRIPPED.value,
            "tripped_at": datetime.now(UTC).isoformat(),
            "trip_reason": "DAILY_LOSS_EXCEEDED",
            "trip_details": {"daily_loss": -5000},
            "trip_count_today": 1,
        }
        mock_redis_client.get.return_value = json.dumps(existing_state)

        breaker = CircuitBreaker(redis_client=mock_redis_client)

        # Verify no initialization call (state already exists)
        mock_redis_client.set.assert_not_called()


class TestCircuitBreakerStateQueries:
    """Tests for state query methods."""

    @pytest.fixture
    def mock_redis_client(self):
        """Create mock Redis client for testing."""
        mock_redis = Mock(spec=RedisClient)
        mock_redis._client = Mock()
        return mock_redis

    def test_get_state_open(self, mock_redis_client):
        """Test get_state returns OPEN when state is OPEN."""
        state_data = {
            "state": CircuitBreakerState.OPEN.value,
            "tripped_at": None,
            "trip_reason": None,
            "trip_count_today": 0,
        }
        mock_redis_client.get.return_value = json.dumps(state_data)

        breaker = CircuitBreaker(redis_client=mock_redis_client)
        state = breaker.get_state()

        assert state == CircuitBreakerState.OPEN

    def test_get_state_tripped(self, mock_redis_client):
        """Test get_state returns TRIPPED when state is TRIPPED."""
        state_data = {
            "state": CircuitBreakerState.TRIPPED.value,
            "tripped_at": datetime.now(UTC).isoformat(),
            "trip_reason": "DAILY_LOSS_EXCEEDED",
            "trip_count_today": 1,
        }
        mock_redis_client.get.return_value = json.dumps(state_data)

        breaker = CircuitBreaker(redis_client=mock_redis_client)
        state = breaker.get_state()

        assert state == CircuitBreakerState.TRIPPED

    def test_get_state_quiet_period_not_expired(self, mock_redis_client):
        """Test get_state returns QUIET_PERIOD when period not expired."""
        # Reset 1 minute ago (still within 5-minute quiet period)
        reset_at = datetime.now(UTC) - timedelta(seconds=60)
        state_data = {
            "state": CircuitBreakerState.QUIET_PERIOD.value,
            "reset_at": reset_at.isoformat(),
            "trip_count_today": 1,
        }
        mock_redis_client.get.return_value = json.dumps(state_data)

        breaker = CircuitBreaker(redis_client=mock_redis_client)
        state = breaker.get_state()

        assert state == CircuitBreakerState.QUIET_PERIOD

    def test_get_state_quiet_period_expired_transitions_to_open(self, mock_redis_client):
        """Test get_state auto-transitions to OPEN when quiet period expired."""
        # Reset 10 minutes ago (beyond 5-minute quiet period)
        reset_at = datetime.now(UTC) - timedelta(seconds=600)
        state_data = {
            "state": CircuitBreakerState.QUIET_PERIOD.value,
            "reset_at": reset_at.isoformat(),
            "trip_count_today": 1,
        }
        mock_redis_client.get.return_value = json.dumps(state_data)

        # Mock pipeline for _transition_to_open
        mock_pipeline = Mock()
        mock_pipeline.__enter__ = Mock(return_value=mock_pipeline)
        mock_pipeline.__exit__ = Mock(return_value=False)
        mock_pipeline.watch = Mock()
        mock_pipeline.get.return_value = json.dumps(state_data)
        mock_pipeline.multi = Mock()
        mock_pipeline.set = Mock()
        mock_pipeline.execute = Mock()
        mock_pipeline.unwatch = Mock()
        mock_redis_client._client.pipeline.return_value = mock_pipeline

        breaker = CircuitBreaker(redis_client=mock_redis_client)
        state = breaker.get_state()

        # Verify transition to OPEN was triggered
        assert state == CircuitBreakerState.OPEN
        mock_pipeline.set.assert_called_once()

    def test_is_tripped_when_tripped(self, mock_redis_client):
        """Test is_tripped returns True when TRIPPED."""
        state_data = {
            "state": CircuitBreakerState.TRIPPED.value,
            "tripped_at": datetime.now(UTC).isoformat(),
            "trip_reason": "MAX_DRAWDOWN",
        }
        mock_redis_client.get.return_value = json.dumps(state_data)

        breaker = CircuitBreaker(redis_client=mock_redis_client)

        assert breaker.is_tripped() is True

    def test_is_tripped_when_open(self, mock_redis_client):
        """Test is_tripped returns False when OPEN."""
        state_data = {"state": CircuitBreakerState.OPEN.value}
        mock_redis_client.get.return_value = json.dumps(state_data)

        breaker = CircuitBreaker(redis_client=mock_redis_client)

        assert breaker.is_tripped() is False

    def test_is_tripped_when_quiet_period(self, mock_redis_client):
        """Test is_tripped returns False when QUIET_PERIOD."""
        state_data = {
            "state": CircuitBreakerState.QUIET_PERIOD.value,
            "reset_at": datetime.now(UTC).isoformat(),
        }
        mock_redis_client.get.return_value = json.dumps(state_data)

        breaker = CircuitBreaker(redis_client=mock_redis_client)

        assert breaker.is_tripped() is False

    def test_get_trip_reason(self, mock_redis_client):
        """Test get_trip_reason returns reason when TRIPPED."""
        state_data = {
            "state": CircuitBreakerState.TRIPPED.value,
            "trip_reason": "DATA_STALE",
        }
        mock_redis_client.get.return_value = json.dumps(state_data)

        breaker = CircuitBreaker(redis_client=mock_redis_client)
        reason = breaker.get_trip_reason()

        assert reason == "DATA_STALE"

    def test_get_trip_reason_when_not_tripped(self, mock_redis_client):
        """Test get_trip_reason returns None when not TRIPPED."""
        state_data = {"state": CircuitBreakerState.OPEN.value, "trip_reason": None}
        mock_redis_client.get.return_value = json.dumps(state_data)

        breaker = CircuitBreaker(redis_client=mock_redis_client)
        reason = breaker.get_trip_reason()

        assert reason is None

    def test_get_trip_details(self, mock_redis_client):
        """Test get_trip_details returns details when TRIPPED."""
        trip_details = {"daily_loss": -5234.56, "max_loss": -5000}
        state_data = {
            "state": CircuitBreakerState.TRIPPED.value,
            "trip_details": trip_details,
        }
        mock_redis_client.get.return_value = json.dumps(state_data)

        breaker = CircuitBreaker(redis_client=mock_redis_client)
        details = breaker.get_trip_details()

        assert details == trip_details

    def test_get_trip_details_when_not_tripped(self, mock_redis_client):
        """Test get_trip_details returns None when not TRIPPED."""
        state_data = {"state": CircuitBreakerState.OPEN.value, "trip_details": None}
        mock_redis_client.get.return_value = json.dumps(state_data)

        breaker = CircuitBreaker(redis_client=mock_redis_client)
        details = breaker.get_trip_details()

        assert details is None

    def test_get_status(self, mock_redis_client):
        """Test get_status returns complete state data."""
        state_data = {
            "state": CircuitBreakerState.TRIPPED.value,
            "tripped_at": "2025-10-19T15:30:00+00:00",
            "trip_reason": "DAILY_LOSS_EXCEEDED",
            "trip_details": {"daily_loss": -5234.56},
            "trip_count_today": 2,
            "reset_at": None,
            "reset_by": None,
        }
        mock_redis_client.get.return_value = json.dumps(state_data)

        breaker = CircuitBreaker(redis_client=mock_redis_client)
        status = breaker.get_status()

        assert status == state_data


class TestCircuitBreakerTripOperation:
    """Tests for trip() operation."""

    @pytest.fixture
    def mock_redis_client(self):
        """Create mock Redis client with pipeline support."""
        mock_redis = Mock(spec=RedisClient)
        mock_pipeline = Mock()
        mock_redis._client = Mock()
        mock_redis._client.pipeline.return_value = mock_pipeline
        mock_pipeline.__enter__ = Mock(return_value=mock_pipeline)
        mock_pipeline.__exit__ = Mock(return_value=False)
        return mock_redis, mock_pipeline

    def test_trip_from_open_to_tripped(self, mock_redis_client):
        """Test trip transitions from OPEN to TRIPPED."""
        mock_redis, mock_pipeline = mock_redis_client

        # Initial state: OPEN
        initial_state = {
            "state": CircuitBreakerState.OPEN.value,
            "trip_count_today": 0,
        }
        mock_pipeline.watch = Mock()
        mock_pipeline.get.return_value = json.dumps(initial_state)
        mock_pipeline.multi = Mock()
        mock_pipeline.set = Mock()
        mock_pipeline.execute = Mock()
        mock_pipeline.unwatch = Mock()

        # Mock history operations
        mock_redis._client.zadd = Mock()
        mock_redis._client.zcard = Mock(return_value=1)

        breaker = CircuitBreaker(redis_client=mock_redis)
        breaker.trip("DAILY_LOSS_EXCEEDED", details={"daily_loss": -5234.56})

        # Verify state was updated
        mock_pipeline.set.assert_called_once()
        call_args = mock_pipeline.set.call_args
        updated_state = json.loads(call_args[0][1])

        assert updated_state["state"] == CircuitBreakerState.TRIPPED.value
        assert updated_state["trip_reason"] == "DAILY_LOSS_EXCEEDED"
        assert updated_state["trip_details"] == {"daily_loss": -5234.56}
        assert updated_state["trip_count_today"] == 1

    def test_trip_increments_trip_count(self, mock_redis_client):
        """Test trip increments trip_count_today."""
        mock_redis, mock_pipeline = mock_redis_client

        # Initial state: OPEN with existing trip count
        initial_state = {
            "state": CircuitBreakerState.OPEN.value,
            "trip_count_today": 3,
        }
        mock_pipeline.watch = Mock()
        mock_pipeline.get.return_value = json.dumps(initial_state)
        mock_pipeline.multi = Mock()
        mock_pipeline.set = Mock()
        mock_pipeline.execute = Mock()

        # Mock history operations
        mock_redis._client.zadd = Mock()
        mock_redis._client.zcard = Mock(return_value=1)

        breaker = CircuitBreaker(redis_client=mock_redis)
        breaker.trip("MAX_DRAWDOWN")

        # Verify trip count incremented
        call_args = mock_pipeline.set.call_args
        updated_state = json.loads(call_args[0][1])
        assert updated_state["trip_count_today"] == 4

    def test_trip_when_already_tripped_is_idempotent(self, mock_redis_client):
        """Test trip when already TRIPPED is idempotent (doesn't error)."""
        mock_redis, mock_pipeline = mock_redis_client

        # Initial state: Already TRIPPED
        initial_state = {
            "state": CircuitBreakerState.TRIPPED.value,
            "trip_reason": "EXISTING_REASON",
            "trip_count_today": 1,
        }
        mock_pipeline.watch = Mock()
        mock_pipeline.get.return_value = json.dumps(initial_state)
        mock_pipeline.unwatch = Mock()

        breaker = CircuitBreaker(redis_client=mock_redis)
        breaker.trip("NEW_REASON")  # Should not error

        # Verify transaction was aborted (unwatch called)
        mock_pipeline.unwatch.assert_called_once()
        mock_pipeline.multi.assert_not_called()

    def test_trip_retries_on_watch_error(self, mock_redis_client):
        """Test trip retries on WatchError (concurrent modification)."""
        mock_redis, mock_pipeline = mock_redis_client

        initial_state = {"state": CircuitBreakerState.OPEN.value, "trip_count_today": 0}

        # First attempt: WatchError, second attempt: success
        mock_pipeline.watch = Mock()
        mock_pipeline.get.return_value = json.dumps(initial_state)
        mock_pipeline.multi = Mock()
        mock_pipeline.set = Mock()
        mock_pipeline.execute.side_effect = [WatchError("Concurrent modification"), None]

        # Mock history operations
        mock_redis._client.zadd = Mock()
        mock_redis._client.zcard = Mock(return_value=1)

        breaker = CircuitBreaker(redis_client=mock_redis)
        breaker.trip("DATA_STALE")

        # Verify retry occurred (execute called twice)
        assert mock_pipeline.execute.call_count == 2

    def test_trip_appends_to_history(self, mock_redis_client):
        """Test trip appends event to history log."""
        mock_redis, mock_pipeline = mock_redis_client

        initial_state = {"state": CircuitBreakerState.OPEN.value, "trip_count_today": 0}
        mock_pipeline.watch = Mock()
        mock_pipeline.get.return_value = json.dumps(initial_state)
        mock_pipeline.multi = Mock()
        mock_pipeline.set = Mock()
        mock_pipeline.execute = Mock()

        # Mock history operations
        mock_redis._client.zadd = Mock()
        mock_redis._client.zcard = Mock(return_value=1)

        breaker = CircuitBreaker(redis_client=mock_redis)
        breaker.trip("BROKER_ERRORS", details={"error": "503 Service Unavailable"})

        # Verify zadd was called to append to history
        mock_redis._client.zadd.assert_called_once()
        call_args = mock_redis._client.zadd.call_args
        assert call_args[0][0] == "circuit_breaker:trip_history"

        # Verify history entry contains trip details
        history_entry_json = list(call_args[0][1].keys())[0]
        history_entry = json.loads(history_entry_json)
        assert history_entry["reason"] == "BROKER_ERRORS"
        assert history_entry["details"] == {"error": "503 Service Unavailable"}


class TestCircuitBreakerResetOperation:
    """Tests for reset() operation."""

    @pytest.fixture
    def mock_redis_client(self):
        """Create mock Redis client with pipeline support."""
        mock_redis = Mock(spec=RedisClient)
        mock_pipeline = Mock()
        mock_redis._client = Mock()
        mock_redis._client.pipeline.return_value = mock_pipeline
        mock_pipeline.__enter__ = Mock(return_value=mock_pipeline)
        mock_pipeline.__exit__ = Mock(return_value=False)
        return mock_redis, mock_pipeline

    def test_reset_from_tripped_to_quiet_period(self, mock_redis_client):
        """Test reset transitions from TRIPPED to QUIET_PERIOD."""
        mock_redis, mock_pipeline = mock_redis_client

        # Initial state: TRIPPED
        initial_state = {
            "state": CircuitBreakerState.TRIPPED.value,
            "trip_reason": "DAILY_LOSS_EXCEEDED",
            "trip_count_today": 1,
        }
        mock_pipeline.watch = Mock()
        mock_pipeline.get.return_value = json.dumps(initial_state)
        mock_pipeline.multi = Mock()
        mock_pipeline.set = Mock()
        mock_pipeline.execute = Mock()
        mock_pipeline.unwatch = Mock()

        breaker = CircuitBreaker(redis_client=mock_redis)
        breaker.reset(reset_by="operator")

        # Verify state transitioned to QUIET_PERIOD
        call_args = mock_pipeline.set.call_args
        updated_state = json.loads(call_args[0][1])

        assert updated_state["state"] == CircuitBreakerState.QUIET_PERIOD.value
        assert updated_state["reset_by"] == "operator"
        assert "reset_at" in updated_state

    def test_reset_when_not_tripped_raises_error(self, mock_redis_client):
        """Test reset raises CircuitBreakerError when not TRIPPED."""
        mock_redis, mock_pipeline = mock_redis_client

        # Initial state: OPEN (not TRIPPED)
        initial_state = {"state": CircuitBreakerState.OPEN.value}
        mock_pipeline.watch = Mock()
        mock_pipeline.get.return_value = json.dumps(initial_state)
        mock_pipeline.unwatch = Mock()

        breaker = CircuitBreaker(redis_client=mock_redis)

        with pytest.raises(CircuitBreakerError, match="current state is OPEN"):
            breaker.reset()

    def test_reset_retries_on_watch_error(self, mock_redis_client):
        """Test reset retries on WatchError (concurrent modification)."""
        mock_redis, mock_pipeline = mock_redis_client

        initial_state = {
            "state": CircuitBreakerState.TRIPPED.value,
            "trip_reason": "DATA_STALE",
        }

        # First attempt: WatchError, second attempt: success
        mock_pipeline.watch = Mock()
        mock_pipeline.get.return_value = json.dumps(initial_state)
        mock_pipeline.multi = Mock()
        mock_pipeline.set = Mock()
        mock_pipeline.execute.side_effect = [WatchError("Concurrent modification"), None]
        mock_pipeline.unwatch = Mock()

        breaker = CircuitBreaker(redis_client=mock_redis)
        breaker.reset(reset_by="system")

        # Verify retry occurred
        assert mock_pipeline.execute.call_count == 2


class TestCircuitBreakerHistoryManagement:
    """Tests for trip history management."""

    @pytest.fixture
    def mock_redis_client(self):
        """Create mock Redis client with pipeline support."""
        mock_redis = Mock(spec=RedisClient)
        mock_pipeline = Mock()
        mock_redis._client = Mock()
        mock_redis._client.pipeline.return_value = mock_pipeline
        mock_pipeline.__enter__ = Mock(return_value=mock_pipeline)
        mock_pipeline.__exit__ = Mock(return_value=False)
        return mock_redis, mock_pipeline

    def test_history_trimmed_when_exceeds_max_entries(self, mock_redis_client):
        """Test history is trimmed when exceeding max_history_entries."""
        mock_redis, mock_pipeline = mock_redis_client

        initial_state = {"state": CircuitBreakerState.OPEN.value, "trip_count_today": 0}
        mock_pipeline.watch = Mock()
        mock_pipeline.get.return_value = json.dumps(initial_state)
        mock_pipeline.multi = Mock()
        mock_pipeline.set = Mock()
        mock_pipeline.execute = Mock()

        # Mock history operations: 1500 entries (exceeds 1000 limit)
        mock_redis._client.zadd = Mock()
        mock_redis._client.zcard = Mock(return_value=1500)
        mock_redis._client.zremrangebyrank = Mock()

        breaker = CircuitBreaker(redis_client=mock_redis)
        breaker.trip("MAX_DRAWDOWN")

        # Verify trim was triggered
        mock_redis._client.zremrangebyrank.assert_called_once()
        call_args = mock_redis._client.zremrangebyrank.call_args

        # Should remove oldest 500 entries (keep last 1000)
        assert call_args[0][0] == "circuit_breaker:trip_history"
        assert call_args[0][1] == 0  # Start rank
        assert call_args[0][2] == 499  # End rank (remove 500 entries)

    def test_history_not_trimmed_when_below_max_entries(self, mock_redis_client):
        """Test history is not trimmed when below max_history_entries."""
        mock_redis, mock_pipeline = mock_redis_client

        initial_state = {"state": CircuitBreakerState.OPEN.value, "trip_count_today": 0}
        mock_pipeline.watch = Mock()
        mock_pipeline.get.return_value = json.dumps(initial_state)
        mock_pipeline.multi = Mock()
        mock_pipeline.set = Mock()
        mock_pipeline.execute = Mock()

        # Mock history operations: 500 entries (below 1000 limit)
        mock_redis._client.zadd = Mock()
        mock_redis._client.zcard = Mock(return_value=500)
        mock_redis._client.zremrangebyrank = Mock()

        breaker = CircuitBreaker(redis_client=mock_redis)
        breaker.trip("DATA_STALE")

        # Verify trim was NOT triggered
        mock_redis._client.zremrangebyrank.assert_not_called()


class TestCircuitBreakerEdgeCases:
    """Tests for edge cases and error conditions."""

    @pytest.fixture
    def mock_redis_client(self):
        """Create mock Redis client with pipeline support."""
        mock_redis = Mock(spec=RedisClient)
        mock_pipeline = Mock()
        mock_redis._client = Mock()
        mock_redis._client.pipeline.return_value = mock_pipeline
        mock_pipeline.__enter__ = Mock(return_value=mock_pipeline)
        mock_pipeline.__exit__ = Mock(return_value=False)
        return mock_redis, mock_pipeline

    def test_get_state_initializes_when_state_missing(self, mock_redis_client):
        """Test get_state initializes state when Redis key missing."""
        mock_redis, _ = mock_redis_client
        mock_redis.get.return_value = None
        mock_redis.set = Mock()

        breaker = CircuitBreaker(redis_client=mock_redis)
        state = breaker.get_state()

        # Should initialize and return OPEN
        assert state == CircuitBreakerState.OPEN
        assert mock_redis.set.call_count >= 1  # Called during init or get_state

    def test_trip_reason_enum_values(self, mock_redis_client):
        """Test all TripReason enum values are valid."""
        assert TripReason.DAILY_LOSS_EXCEEDED.value == "DAILY_LOSS_EXCEEDED"
        assert TripReason.MAX_DRAWDOWN.value == "MAX_DRAWDOWN"
        assert TripReason.DATA_STALE.value == "DATA_STALE"
        assert TripReason.BROKER_ERRORS.value == "BROKER_ERRORS"
        assert TripReason.MANUAL.value == "MANUAL"

    def test_trip_with_custom_reason(self, mock_redis_client):
        """Test trip accepts custom reason string (not just enum)."""
        mock_redis, mock_pipeline = mock_redis_client

        initial_state = {"state": CircuitBreakerState.OPEN.value, "trip_count_today": 0}
        mock_pipeline.watch = Mock()
        mock_pipeline.get.return_value = json.dumps(initial_state)
        mock_pipeline.multi = Mock()
        mock_pipeline.set = Mock()
        mock_pipeline.execute = Mock()

        # Mock history operations
        mock_redis._client.zadd = Mock()
        mock_redis._client.zcard = Mock(return_value=1)

        breaker = CircuitBreaker(redis_client=mock_redis)
        breaker.trip("CUSTOM_VIOLATION_TYPE")

        # Verify custom reason was accepted
        call_args = mock_pipeline.set.call_args
        updated_state = json.loads(call_args[0][1])
        assert updated_state["trip_reason"] == "CUSTOM_VIOLATION_TYPE"
