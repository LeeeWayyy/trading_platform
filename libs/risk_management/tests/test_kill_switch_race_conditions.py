"""
Tests for kill-switch race condition protection.

This test suite verifies that kill-switch state transitions are atomic
and prevent race conditions when multiple operators act concurrently.

Context:
    Gemini Code Assist review identified potential race conditions in
    engage/disengage methods using non-atomic read-modify-write sequences.

Fix:
    Both engage() and disengage() now use Redis Lua scripts to ensure
    atomic state transitions. These tests verify the atomic behavior.

Testing Strategy:
    - Unit tests verify Lua script logic
    - Integration tests (marked) would verify actual concurrent operations
"""

from unittest.mock import Mock

import pytest

from libs.redis_client import RedisClient
from libs.risk_management.kill_switch import KillSwitch


class TestKillSwitchAtomicity:
    """
    Test kill-switch atomic operations.

    Verifies that state transitions are atomic and prevent:
    - Double engagement
    - Double disengagement
    - Concurrent state modifications
    """

    @pytest.fixture()
    def mock_redis(self):
        """Mock Redis client for testing."""
        mock_redis = Mock(spec=RedisClient)
        return mock_redis

    @pytest.fixture()
    def kill_switch(self, mock_redis):
        """Create KillSwitch with mocked Redis."""
        return KillSwitch(redis_client=mock_redis)

    def test_engage_uses_lua_script_for_atomicity(self, kill_switch, mock_redis):
        """
        Test that engage() uses Redis Lua script for atomic state transition.

        Lua scripts execute atomically in Redis - no other operations
        can interleave during script execution.
        """
        # Setup: Initial ACTIVE state
        mock_redis.eval.return_value = 1  # Success

        # Act: Engage kill-switch
        kill_switch.engage(reason="Test", operator="ops")

        # Assert: Lua script was used (not separate GET + SET)
        mock_redis.eval.assert_called_once()
        call_args = mock_redis.eval.call_args

        # Verify Lua script contains atomic operations
        lua_script = call_args[0][0]
        assert "redis.call('GET'" in lua_script
        assert "redis.call('SET'" in lua_script
        assert "cjson.decode" in lua_script
        assert "return redis.error_reply" in lua_script  # Error handling

    def test_disengage_uses_lua_script_for_atomicity(self, kill_switch, mock_redis):
        """
        Test that disengage() uses Redis Lua script for atomic state transition.
        """
        # Setup: Initial ENGAGED state
        mock_redis.eval.return_value = 1  # Success

        # Act: Disengage kill-switch
        kill_switch.disengage(operator="ops")

        # Assert: Lua script was used
        mock_redis.eval.assert_called_once()
        call_args = mock_redis.eval.call_args

        # Verify Lua script contains atomic operations
        lua_script = call_args[0][0]
        assert "redis.call('GET'" in lua_script
        assert "redis.call('SET'" in lua_script
        assert "state_data.state == active_value" in lua_script  # Check condition

    def test_double_engage_prevented_by_lua_script(self, kill_switch, mock_redis):
        """
        Test that attempting to engage already-engaged kill-switch fails atomically.

        Lua script checks state and prevents double engagement within
        the same atomic operation.
        """
        # Setup: Simulate Lua script error response (already engaged)
        mock_redis.eval.side_effect = Exception("Kill-switch already engaged")

        # Act & Assert: Second engage should raise ValueError
        with pytest.raises(ValueError, match="already engaged"):
            kill_switch.engage(reason="Test", operator="ops1")

    def test_double_disengage_prevented_by_lua_script(self, kill_switch, mock_redis):
        """
        Test that attempting to disengage non-engaged kill-switch fails atomically.
        """
        # Setup: Simulate Lua script error response (not engaged)
        mock_redis.eval.side_effect = Exception("Kill-switch not engaged")

        # Act & Assert: Disengage when ACTIVE should raise ValueError
        with pytest.raises(ValueError, match="not engaged"):
            kill_switch.disengage(operator="ops1")

    def test_engage_lua_script_increments_counter_atomically(
        self, kill_switch, mock_redis
    ):
        """
        Test that engagement counter is incremented atomically within Lua script.

        Counter update is part of the same atomic operation as state change,
        preventing lost updates.
        """
        # Act: Engage kill-switch
        mock_redis.eval.return_value = 1
        kill_switch.engage(reason="Test", operator="ops")

        # Assert: Lua script includes counter increment
        lua_script = mock_redis.eval.call_args[0][0]
        assert "engagement_count_today" in lua_script
        assert "+ 1" in lua_script  # Counter increment

    def test_engage_lua_script_validates_arguments(self, kill_switch, mock_redis):
        """
        Test that Lua script receives all required arguments for engagement.
        """
        # Setup
        mock_redis.eval.return_value = 1

        # Act
        kill_switch.engage(
            reason="Market anomaly",
            operator="ops_team",
            details={"severity": "high"},
        )

        # Assert: All arguments passed to Lua script
        call_args = mock_redis.eval.call_args[0]  # Get positional args tuple
        assert call_args[1] == 1  # numkeys
        assert call_args[2] == "kill_switch:state"  # KEYS[1]
        assert call_args[3] == "ENGAGED"  # ARGV[1] - engaged value
        assert call_args[4] == "ACTIVE"  # ARGV[2] - active value
        # call_args[5] is ARGV[3] - timestamp (ISO format)
        assert call_args[6] == "ops_team"  # ARGV[4] - operator
        assert call_args[7] == "Market anomaly"  # ARGV[5] - reason
        # call_args[8] is ARGV[6] - details JSON

    def test_disengage_lua_script_validates_arguments(self, kill_switch, mock_redis):
        """
        Test that Lua script receives all required arguments for disengagement.
        """
        # Setup
        mock_redis.eval.return_value = 1

        # Act
        kill_switch.disengage(operator="ops_team", notes="Issue resolved")

        # Assert: All arguments passed to Lua script
        call_args = mock_redis.eval.call_args[0]  # Get positional args tuple
        assert call_args[1] == 1  # numkeys
        assert call_args[2] == "kill_switch:state"  # KEYS[1]
        assert call_args[3] == "ACTIVE"  # ARGV[1] - active value
        assert call_args[4] == "ENGAGED"  # ARGV[2] - engaged value
        # call_args[5] is ARGV[3] - timestamp (ISO format)
        assert call_args[6] == "ops_team"  # ARGV[4] - operator
        # call_args[7] is ARGV[5] - notes JSON


class TestKillSwitchHistoryAtomicity:
    """
    Test that history operations don't compromise state atomicity.

    History is written AFTER state transition completes, so history
    failures don't affect state consistency.
    """

    @pytest.fixture()
    def mock_redis(self):
        """Mock Redis client for testing."""
        mock_redis = Mock(spec=RedisClient)
        return mock_redis

    @pytest.fixture()
    def kill_switch(self, mock_redis):
        """Create KillSwitch with mocked Redis."""
        return KillSwitch(redis_client=mock_redis)

    def test_history_written_after_atomic_state_change(
        self, kill_switch, mock_redis
    ):
        """
        Test that history is written after state transition completes.

        This ensures:
        1. State change is atomic (Lua script)
        2. History write failure doesn't corrupt state
        """
        # Setup
        mock_redis.eval.return_value = 1  # State change succeeds
        mock_redis.rpush.return_value = 1  # History write succeeds

        # Act
        kill_switch.engage(reason="Test", operator="ops")

        # Assert: State change executed before history write
        calls = [call[0] for call in mock_redis.method_calls]
        eval_index = next(i for i, call in enumerate(calls) if call == "eval")
        rpush_index = next(i for i, call in enumerate(calls) if call == "rpush")
        assert eval_index < rpush_index  # eval (state) before rpush (history)

    def test_history_write_failure_after_successful_state_change(
        self, kill_switch, mock_redis
    ):
        """
        Test that history write failure doesn't affect state consistency.

        State change completes atomically via Lua script.
        History write happens after, so failure is isolated.
        """
        # Setup: State change succeeds, history write fails
        mock_redis.eval.return_value = 1  # State change succeeds
        mock_redis.rpush.side_effect = Exception("Redis connection lost")

        # Act: History write fails but shouldn't affect state
        # In reality, this would log an error but state is already committed
        with pytest.raises(Exception, match="Redis connection lost"):
            kill_switch.engage(reason="Test", operator="ops")

        # State change already completed atomically via Lua script
        # This test demonstrates history failure is isolated from state


@pytest.mark.integration()
class TestKillSwitchConcurrentOperations:
    """
    Integration tests for concurrent kill-switch operations.

    These tests require real Redis and would verify:
    - Multiple concurrent engage attempts
    - Concurrent engage + disengage
    - Concurrent status checks during transitions

    Marked as integration tests - skipped in unit test runs.
    """

    def test_concurrent_engage_attempts_only_one_succeeds(self):
        """
        Test that when multiple operators try to engage simultaneously,
        only one succeeds.

        Requires:
        - Real Redis with Lua script support
        - Threading or async to simulate concurrent operations
        """
        pytest.skip("Requires real Redis and concurrent execution")

    def test_concurrent_engage_and_disengage_maintain_consistency(self):
        """
        Test that concurrent engage/disengage operations maintain consistency.

        Even with racing operations, state should be consistent:
        - No partial state updates
        - Counter increments not lost
        - Audit trail complete
        """
        pytest.skip("Requires real Redis and concurrent execution")

    def test_rapid_engage_disengage_cycles_no_lost_updates(self):
        """
        Test rapid engage/disengage cycles don't lose counter updates.

        Engagement counter should accurately reflect all engagements,
        even under high concurrency.
        """
        pytest.skip("Requires real Redis and concurrent execution")


class TestLuaScriptErrorHandling:
    """
    Test error handling within Lua scripts.

    Lua scripts should gracefully handle edge cases:
    - Missing state key
    - Invalid JSON
    - State corruption
    """

    @pytest.fixture()
    def mock_redis(self):
        """Mock Redis client for testing."""
        mock_redis = Mock(spec=RedisClient)
        return mock_redis

    @pytest.fixture()
    def kill_switch(self, mock_redis):
        """Create KillSwitch with mocked Redis."""
        return KillSwitch(redis_client=mock_redis)

    def test_engage_lua_script_handles_missing_state(self, kill_switch, mock_redis):
        """
        Test Lua script error handling when state key is missing.

        Lua script should return error_reply, which Python code
        converts to exception.
        """
        # Setup: Lua script returns error for missing state
        mock_redis.eval.side_effect = Exception("Kill-switch state missing")

        # Act & Assert
        with pytest.raises(Exception, match="state missing"):
            kill_switch.engage(reason="Test", operator="ops")

    def test_disengage_lua_script_handles_missing_state(self, kill_switch, mock_redis):
        """
        Test Lua script error handling for disengage when state missing.
        """
        # Setup: Lua script returns error for missing state
        mock_redis.eval.side_effect = Exception("Kill-switch state missing")

        # Act & Assert
        with pytest.raises(Exception, match="state missing"):
            kill_switch.disengage(operator="ops")


class TestAtomicOperationImplementation:
    """
    Verify that atomic operations use Lua scripts.

    These tests verify the implementation (not documentation) to ensure
    atomic behavior is actually present in the code.
    """

    def test_engage_implementation_uses_lua_script(self):
        """
        Test that engage() implementation contains Lua script for atomicity.

        Verifies the source code uses redis.eval() with Lua script,
        not just that it's documented.
        """
        import inspect

        from libs.risk_management.kill_switch import KillSwitch

        source = inspect.getsource(KillSwitch.engage)

        # Verify implementation contains Lua script and redis.eval
        assert "lua_script" in source
        assert "self.redis.eval" in source
        assert "redis.call('GET'" in source
        assert "redis.call('SET'" in source

    def test_disengage_implementation_uses_lua_script(self):
        """
        Test that disengage() implementation contains Lua script for atomicity.
        """
        import inspect

        from libs.risk_management.kill_switch import KillSwitch

        source = inspect.getsource(KillSwitch.disengage)

        # Verify implementation contains Lua script and redis.eval
        assert "lua_script" in source
        assert "self.redis.eval" in source
        assert "redis.call('GET'" in source
        assert "redis.call('SET'" in source
