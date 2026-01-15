"""
Tests for kill-switch fail-closed behavior.

This test suite verifies that kill-switch NEVER auto-resumes trading when
Redis state is missing. This is a critical safety property: if Redis is
flushed/restarted while kill-switch is ENGAGED, the system must remain
closed (blocked) until an operator explicitly verifies safety.

Context:
    P0 issue identified in PR review: get_status() was silently reinitializing
    to ACTIVE when Redis state was missing, potentially resuming trading after
    a Redis flush while the system was halted.

Fix:
    Both get_state() and get_status() now raise RuntimeError when state is
    missing (fail-closed). Status queries NEVER mutate state.

Testing Strategy:
    - Unit tests verify RuntimeError is raised when state missing
    - Verify error messages are clear and actionable
    - Verify no auto-reinitialization occurs
    - Test both get_state() and get_status() methods
"""

from unittest.mock import Mock

import pytest

from libs.core.redis_client import RedisClient
from libs.trading.risk_management.kill_switch import KillSwitch


class TestFailClosedBehavior:
    """
    Test fail-closed behavior when Redis state is missing.

    Critical safety requirement: NEVER auto-resume trading when state is unknown.
    """

    @pytest.fixture()
    def mock_redis(self):
        """Mock Redis client for testing."""
        mock_redis = Mock(spec=RedisClient)
        # Simulate initial state exists (normal startup)
        mock_redis.get.return_value = b'{"state": "ACTIVE", "engagement_count_today": 0}'
        return mock_redis

    @pytest.fixture()
    def kill_switch(self, mock_redis):
        """Create KillSwitch with mocked Redis."""
        return KillSwitch(redis_client=mock_redis)

    def test_get_state_fails_closed_when_state_missing(self, kill_switch, mock_redis):
        """
        Test that get_state() raises RuntimeError when state is missing.

        Scenario: Redis flushed/restarted while kill-switch was ENGAGED.
        Expected: System fails closed (raises error) rather than resuming trading.
        """
        # Simulate Redis flush - state now missing
        mock_redis.get.return_value = None

        # Act & Assert: get_state() must raise RuntimeError (fail-closed)
        with pytest.raises(RuntimeError) as exc_info:
            kill_switch.get_state()

        # Verify error message is clear and actionable
        error_msg = str(exc_info.value)
        assert "Kill-switch state missing from Redis" in error_msg
        assert "fail closed" in error_msg.lower()
        assert "operator" in error_msg.lower()

    def test_get_status_fails_closed_when_state_missing(self, kill_switch, mock_redis):
        """
        Test that get_status() raises RuntimeError when state is missing.

        CRITICAL: get_status() must NOT auto-reinitialize to ACTIVE.
        This was the P0 bug - status queries were resuming trading.
        """
        # Simulate Redis flush - state now missing
        mock_redis.get.return_value = None

        # Act & Assert: get_status() must raise RuntimeError (fail-closed)
        with pytest.raises(RuntimeError) as exc_info:
            kill_switch.get_status()

        # Verify error message is clear and actionable
        error_msg = str(exc_info.value)
        assert "Kill-switch state missing from Redis" in error_msg
        assert "fail closed" in error_msg.lower()
        assert "operator" in error_msg.lower()

    def test_is_engaged_fails_closed_when_state_missing(self, kill_switch, mock_redis):
        """
        Test that is_engaged() fails closed when state is missing.

        is_engaged() delegates to get_state(), so it should also raise.
        """
        # Simulate Redis flush - state now missing
        mock_redis.get.return_value = None

        # Act & Assert: is_engaged() must raise RuntimeError (fail-closed)
        with pytest.raises(RuntimeError) as exc_info:
            kill_switch.is_engaged()

        assert "Kill-switch state missing" in str(exc_info.value)

    def test_get_status_does_not_call_initialize_state(self, kill_switch, mock_redis):
        """
        Test that get_status() NEVER calls _initialize_state().

        Status queries must be read-only - no state mutation.
        """
        # Simulate Redis flush - state now missing
        mock_redis.get.return_value = None

        # Act: Call get_status() and expect it to fail
        with pytest.raises(RuntimeError):
            kill_switch.get_status()

        # Assert: Should NOT have called set() to initialize state
        mock_redis.set.assert_not_called()

    def test_get_state_does_not_call_initialize_state(self, kill_switch, mock_redis):
        """
        Test that get_state() NEVER calls _initialize_state() after init.

        After __init__, state should exist. If missing, it's a critical error.
        """
        # Record initial set() call count (from __init__ if state was missing)
        initial_set_count = mock_redis.set.call_count

        # Simulate Redis flush - state now missing
        mock_redis.get.return_value = None

        # Act: Call get_state() and expect it to fail
        with pytest.raises(RuntimeError):
            kill_switch.get_state()

        # Assert: set() should NOT be called again (no reinitialization)
        assert mock_redis.set.call_count == initial_set_count


class TestFailClosedScenarios:
    """
    Test realistic fail-closed scenarios.

    These tests simulate real-world failure conditions that require fail-closed.
    """

    @pytest.fixture()
    def mock_redis(self):
        """Mock Redis client for testing."""
        mock_redis = Mock(spec=RedisClient)
        # Start with state existing
        mock_redis.get.return_value = b'{"state": "ACTIVE", "engagement_count_today": 0}'
        return mock_redis

    @pytest.fixture()
    def kill_switch(self, mock_redis):
        """Create KillSwitch with mocked Redis."""
        return KillSwitch(redis_client=mock_redis)

    def test_scenario_redis_flush_while_engaged(self, kill_switch, mock_redis):
        """
        Scenario: Operator engaged kill-switch, then Redis was flushed.

        1. Kill-switch is ENGAGED (trading halted)
        2. Redis flushes (e.g., FLUSHALL command or restart)
        3. Service calls get_status() to check if trading allowed
        4. MUST fail closed - do not resume trading

        This was the P0 bug: get_status() would auto-reinit to ACTIVE.
        """
        # Setup: Kill-switch was ENGAGED
        mock_redis.get.return_value = b'{"state": "ENGAGED", "engaged_by": "ops_team"}'

        # Verify currently engaged
        assert kill_switch.is_engaged()

        # Event: Redis flush - state now missing
        mock_redis.get.return_value = None

        # Act: Service checks status (e.g., health check, pre-order check)
        # Expected: MUST raise RuntimeError (fail-closed)
        with pytest.raises(RuntimeError) as exc_info:
            kill_switch.get_status()

        # Verify fail-closed behavior
        assert "Kill-switch state missing" in str(exc_info.value)

        # Verify trading would be blocked
        with pytest.raises(RuntimeError):
            kill_switch.is_engaged()  # Can't even check - fail closed

    def test_scenario_redis_restart_during_trading_halt(self, kill_switch, mock_redis):
        """
        Scenario: Redis restarted while kill-switch was ENGAGED.

        This is similar to flush scenario but more realistic - Redis pod
        restart in Kubernetes, Redis crash and restart, etc.
        """
        # Setup: Kill-switch ENGAGED (all trading halted)
        mock_redis.get.return_value = (
            b'{"state": "ENGAGED", "engaged_by": "ops_team", '
            b'"engagement_reason": "Market anomaly"}'
        )

        # Verify engaged
        assert kill_switch.is_engaged()

        # Event: Redis restarts - all data lost
        mock_redis.get.return_value = None

        # Act: Any status check must fail closed
        with pytest.raises(RuntimeError):
            kill_switch.get_state()

        with pytest.raises(RuntimeError):
            kill_switch.get_status()

        with pytest.raises(RuntimeError):
            kill_switch.is_engaged()

    def test_scenario_status_endpoint_during_redis_outage(self, kill_switch, mock_redis):
        """
        Scenario: /status endpoint called during Redis outage.

        Health checks and status endpoints often call get_status().
        These MUST NOT auto-resume trading if state is missing.
        """
        # Simulate Redis outage - get() returns None
        mock_redis.get.return_value = None

        # Act: Status endpoint calls get_status()
        # Expected: Raises RuntimeError (fail-closed)
        with pytest.raises(RuntimeError) as exc_info:
            kill_switch.get_status()

        # Verify error is clear for operators
        error_msg = str(exc_info.value)
        assert "state missing" in error_msg.lower()
        assert "operator" in error_msg.lower()


class TestFailClosedErrorMessages:
    """
    Test that fail-closed error messages are clear and actionable.

    Operators need to understand:
    1. What happened (state missing)
    2. Why we're failing closed (safety)
    3. What to do (verify safety, manually reinitialize)
    """

    @pytest.fixture()
    def mock_redis(self):
        """Mock Redis client for testing."""
        mock_redis = Mock(spec=RedisClient)
        mock_redis.get.return_value = b'{"state": "ACTIVE", "engagement_count_today": 0}'
        return mock_redis

    @pytest.fixture()
    def kill_switch(self, mock_redis):
        """Create KillSwitch with mocked Redis."""
        return KillSwitch(redis_client=mock_redis)

    def test_error_message_mentions_fail_closed(self, kill_switch, mock_redis):
        """Test that error message explicitly mentions fail-closed behavior."""
        mock_redis.get.return_value = None

        with pytest.raises(RuntimeError) as exc_info:
            kill_switch.get_status()

        assert "fail closed" in str(exc_info.value).lower()

    def test_error_message_mentions_operator_action_required(self, kill_switch, mock_redis):
        """Test that error message tells operator what to do."""
        mock_redis.get.return_value = None

        with pytest.raises(RuntimeError) as exc_info:
            kill_switch.get_status()

        error_msg = str(exc_info.value)
        assert "operator" in error_msg.lower()
        assert "verify" in error_msg.lower() or "manually" in error_msg.lower()

    def test_error_message_mentions_state_missing(self, kill_switch, mock_redis):
        """Test that error message clearly states what the problem is."""
        mock_redis.get.return_value = None

        with pytest.raises(RuntimeError) as exc_info:
            kill_switch.get_status()

        assert "state missing" in str(exc_info.value).lower()


class TestInitializationVsStatusCheck:
    """
    Test distinction between initialization and status checks.

    __init__() is allowed to initialize state (first time setup).
    get_status() and get_state() must NEVER initialize (fail-closed).
    """

    def test_init_creates_state_if_missing(self):
        """
        Test that __init__ creates initial state if missing.

        This is the ONLY time auto-initialization is allowed.
        """
        mock_redis = Mock(spec=RedisClient)
        mock_redis.get.return_value = None  # State doesn't exist yet

        # Act: Initialize kill-switch (first time setup)
        _kill_switch = KillSwitch(redis_client=mock_redis)

        # Assert: __init__ should have called set() to create initial state
        mock_redis.set.assert_called_once()
        set_call_args = mock_redis.set.call_args[0]
        assert set_call_args[0] == "kill_switch:state"  # Key
        assert "ACTIVE" in set_call_args[1]  # Initial state is ACTIVE

    def test_init_does_not_overwrite_existing_state(self):
        """
        Test that __init__ does NOT overwrite existing state.

        If state exists, __init__ should leave it alone.
        """
        mock_redis = Mock(spec=RedisClient)
        # State already exists (e.g., ENGAGED)
        existing_state = b'{"state": "ENGAGED", "engaged_by": "ops_team"}'
        mock_redis.get.return_value = existing_state

        # Act: Initialize kill-switch
        _kill_switch = KillSwitch(redis_client=mock_redis)

        # Assert: __init__ should NOT have called set() (state already exists)
        mock_redis.set.assert_not_called()

    def test_status_check_after_init_never_initializes(self):
        """
        Test that after __init__, status checks never initialize state.

        After successful initialization, if state goes missing, it's an error.
        """
        mock_redis = Mock(spec=RedisClient)
        # Initial state exists
        mock_redis.get.return_value = b'{"state": "ACTIVE", "engagement_count_today": 0}'

        # Initialize (this calls _initialize_state if needed)
        kill_switch = KillSwitch(redis_client=mock_redis)

        # Reset mock to track subsequent calls
        mock_redis.reset_mock()

        # Simulate state missing after init (Redis flush)
        mock_redis.get.return_value = None

        # Act: Status check after state goes missing
        with pytest.raises(RuntimeError):
            kill_switch.get_status()

        # Assert: Should NOT have called set() to reinitialize
        mock_redis.set.assert_not_called()


class TestLogMessagesForFailClosed:
    """
    Test that fail-closed conditions are logged appropriately.

    Operators need clear logs to diagnose issues.
    """

    @pytest.fixture()
    def mock_redis(self):
        """Mock Redis client for testing."""
        mock_redis = Mock(spec=RedisClient)
        mock_redis.get.return_value = b'{"state": "ACTIVE", "engagement_count_today": 0}'
        return mock_redis

    @pytest.fixture()
    def kill_switch(self, mock_redis):
        """Create KillSwitch with mocked Redis."""
        return KillSwitch(redis_client=mock_redis)

    def test_fail_closed_logs_critical_error(self, kill_switch, mock_redis, caplog):
        """
        Test that fail-closed condition logs CRITICAL/ERROR level message.

        This should alert operators immediately.
        """
        import logging

        caplog.set_level(logging.ERROR)

        # Simulate state missing
        mock_redis.get.return_value = None

        # Act: Trigger fail-closed
        with pytest.raises(RuntimeError):
            kill_switch.get_status()

        # Assert: Should have logged critical error
        assert len(caplog.records) > 0
        log_record = caplog.records[0]
        assert log_record.levelname in ["ERROR", "CRITICAL"]
        assert "state missing" in log_record.message.lower()
        assert "fail" in log_record.message.lower()
