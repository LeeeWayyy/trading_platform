"""
Unit tests for position reservation (atomic position limit checking).

Tests cover:
- Basic reservation success/failure
- Release (rollback) functionality
- Confirm functionality
- Concurrent reservation race condition prevention
- TTL and cleanup behavior
"""

from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

import pytest

from libs.risk_management.position_reservation import (
    RESERVATION_KEY_PREFIX,
    PositionReservation,
    ReleaseResult,
    ReservationResult,
)


class TestPositionReservation:
    """Tests for PositionReservation class."""

    @pytest.fixture()
    def mock_redis(self) -> MagicMock:
        """Create a mock Redis client."""
        mock = MagicMock()
        # Default eval returns success
        mock.eval.return_value = [1, "test_token", 0, 100]
        mock.get.return_value = None
        return mock

    @pytest.fixture()
    def reservation(self, mock_redis: MagicMock) -> PositionReservation:
        """Create PositionReservation with mock Redis."""
        return PositionReservation(redis=mock_redis, ttl=60)

    def test_reserve_success_buy(
        self, reservation: PositionReservation, mock_redis: MagicMock
    ) -> None:
        """Reserve should succeed when position is within limit."""
        mock_redis.eval.return_value = [1, "abc123", 0, 100]

        result = reservation.reserve(symbol="AAPL", side="buy", qty=100, max_limit=1000)

        assert result.success is True
        assert result.token is not None
        assert result.reason == ""
        assert result.previous_position == 0
        assert result.new_position == 100

        # Verify Lua script was called
        mock_redis.eval.assert_called_once()
        call_args = mock_redis.eval.call_args
        assert call_args[0][1] == 1  # numkeys
        assert call_args[0][2] == f"{RESERVATION_KEY_PREFIX}:AAPL"
        assert call_args[0][3] == "100"  # delta (buy = positive)
        assert call_args[0][4] == "1000"  # max_limit

    def test_reserve_success_sell(
        self, reservation: PositionReservation, mock_redis: MagicMock
    ) -> None:
        """Reserve should work for sell orders (negative delta)."""
        mock_redis.eval.return_value = [1, "abc123", 100, 0]

        result = reservation.reserve(symbol="AAPL", side="sell", qty=100, max_limit=1000)

        assert result.success is True
        call_args = mock_redis.eval.call_args
        assert call_args[0][3] == "-100"  # delta (sell = negative)

    def test_reserve_blocked_limit_exceeded(
        self, reservation: PositionReservation, mock_redis: MagicMock
    ) -> None:
        """Reserve should fail when position would exceed limit."""
        mock_redis.eval.return_value = [0, "LIMIT_EXCEEDED", 900, 1100]

        result = reservation.reserve(symbol="AAPL", side="buy", qty=200, max_limit=1000)

        assert result.success is False
        assert result.token is None
        assert "exceeded" in result.reason.lower()
        assert result.previous_position == 900
        assert result.new_position == 1100

    def test_release_success(self, reservation: PositionReservation, mock_redis: MagicMock) -> None:
        """Release should return reserved position to pool."""
        mock_redis.eval.return_value = [1, "RELEASED", 100, 0]

        result = reservation.release(symbol="AAPL", token="test_token")

        assert result.success is True
        assert result.reason == "RELEASED"
        assert result.previous_position == 100
        assert result.new_position == 0

    def test_release_token_not_found(
        self, reservation: PositionReservation, mock_redis: MagicMock
    ) -> None:
        """Release should fail if token already released/expired."""
        mock_redis.eval.return_value = [0, "TOKEN_NOT_FOUND"]

        result = reservation.release(symbol="AAPL", token="invalid_token")

        assert result.success is False
        assert result.reason == "TOKEN_NOT_FOUND"

    def test_confirm_success(self, reservation: PositionReservation, mock_redis: MagicMock) -> None:
        """Confirm should delete token while keeping position."""
        mock_redis.eval.return_value = [1, "CONFIRMED"]

        result = reservation.confirm(symbol="AAPL", token="test_token")

        assert result.success is True
        assert result.reason == "CONFIRMED"

    def test_confirm_token_not_found(
        self, reservation: PositionReservation, mock_redis: MagicMock
    ) -> None:
        """Confirm should fail if token already confirmed/released."""
        mock_redis.eval.return_value = [0, "TOKEN_NOT_FOUND"]

        result = reservation.confirm(symbol="AAPL", token="invalid_token")

        assert result.success is False
        assert result.reason == "TOKEN_NOT_FOUND"

    def test_get_reserved_position_exists(
        self, reservation: PositionReservation, mock_redis: MagicMock
    ) -> None:
        """Should return current reserved position."""
        mock_redis.get.return_value = "500"

        position = reservation.get_reserved_position("AAPL")

        assert position == 500
        mock_redis.get.assert_called_once_with(f"{RESERVATION_KEY_PREFIX}:AAPL")

    def test_get_reserved_position_none(
        self, reservation: PositionReservation, mock_redis: MagicMock
    ) -> None:
        """Should return 0 if no reservation exists."""
        mock_redis.get.return_value = None

        position = reservation.get_reserved_position("AAPL")

        assert position == 0

    def test_sync_position(self, reservation: PositionReservation, mock_redis: MagicMock) -> None:
        """Sync should set reserved position to actual position (no TTL).

        NOTE: Aggregate position key intentionally has NO TTL to prevent
        position limits from resetting to 0 after expiry. Only token keys
        (for individual reservations) have TTL.
        """
        reservation.sync_position("AAPL", 500)

        # Codex MEDIUM fix: sync_position intentionally omits TTL
        # to prevent aggregate position from expiring and resetting to 0
        mock_redis.set.assert_called_once_with(f"{RESERVATION_KEY_PREFIX}:AAPL", "500")

    def test_clear_all(self, reservation: PositionReservation, mock_redis: MagicMock) -> None:
        """Clear should delete all reservations for symbol."""
        reservation.clear_all("AAPL")

        mock_redis.delete.assert_called_once_with(f"{RESERVATION_KEY_PREFIX}:AAPL")

    def test_unique_token_per_reservation(
        self, reservation: PositionReservation, mock_redis: MagicMock
    ) -> None:
        """Each reservation should get a unique token."""
        # Track tokens passed to eval
        tokens: list[str] = []

        def capture_token(*args: str) -> list[int | str]:
            tokens.append(args[5])  # Token is 6th arg (after key, delta, max, token, ttl)
            return [1, args[5], 0, 100]

        mock_redis.eval.side_effect = capture_token

        # Make multiple reservations
        reservation.reserve("AAPL", "buy", 100, 1000)
        reservation.reserve("AAPL", "buy", 100, 1000)
        reservation.reserve("AAPL", "buy", 100, 1000)

        # All tokens should be different
        assert len(tokens) == 3
        assert len(set(tokens)) == 3  # All unique


class TestPositionReservationRaceCondition:
    """Tests verifying race condition prevention."""

    @pytest.fixture()
    def real_redis_mock(self) -> MagicMock:
        """Create a Redis mock that simulates atomic behavior."""
        mock = MagicMock()
        current_position = 0
        reserved_tokens: dict[str, int] = {}

        def simulate_eval(script: str, numkeys: int, *args: str) -> list[int | str]:
            nonlocal current_position
            key = args[0]

            if "position_reservation" in key and "token" not in args[0]:
                # Reserve script
                delta = int(args[1])
                max_limit = int(args[2])
                token = args[3]

                new_position = current_position + delta

                if abs(new_position) > max_limit:
                    return [0, "LIMIT_EXCEEDED", current_position, new_position]

                # Atomic update
                old_position = current_position
                current_position = new_position
                reserved_tokens[token] = delta

                return [1, token, old_position, new_position]

            return [1, "OK"]

        mock.eval.side_effect = simulate_eval
        mock.get.return_value = None
        return mock

    def test_concurrent_orders_blocked(self, real_redis_mock: MagicMock) -> None:
        """Concurrent orders should be serialized by atomic reservation."""
        reservation = PositionReservation(redis=real_redis_mock, ttl=60)

        # Simulate 10 concurrent buy orders of 200 shares each
        # With max_limit=1000, only 5 should succeed (5 * 200 = 1000)
        results: list[ReservationResult] = []

        def make_reservation() -> ReservationResult:
            return reservation.reserve("AAPL", "buy", 200, max_limit=1000)

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(make_reservation) for _ in range(10)]
            results = [f.result() for f in futures]

        # Count successes
        successes = sum(1 for r in results if r.success)
        failures = sum(1 for r in results if not r.success)

        # Exactly 5 should succeed (5 * 200 = 1000 = limit)
        assert successes == 5
        assert failures == 5

    def test_race_condition_without_reservation(self) -> None:
        """Demonstrate the race condition without reservation (documentation test)."""
        # This test documents what WOULD happen without atomic reservation
        # Both threads would read current_position=0 and both would pass the check

        current_position = 0
        max_limit = 1000
        # Note: This is a documentation test showing the problem we're solving.
        # No actual concurrent test here - that's in test_concurrent_orders_blocked.

        def check_without_atomicity(qty: int) -> bool:
            # Simulate non-atomic read-check-write
            nonlocal current_position
            new_position = current_position + qty
            if abs(new_position) <= max_limit:
                # Both threads pass here simultaneously
                current_position = new_position  # Race condition!
                return True
            return False

        # Without synchronization, both could pass
        # (This is what we're preventing with Redis Lua scripts)
        # In reality, the threads would interleave and both could succeed

        # This test just documents the problem - actual prevention is tested above
        # The function exists to document the race condition pattern
        assert check_without_atomicity(500)  # First call succeeds
        assert current_position == 500
        assert max_limit == 1000  # Unused var suppression


class TestPositionReservationDataclasses:
    """Tests for result dataclasses."""

    def test_reservation_result_success(self) -> None:
        """ReservationResult should capture success details."""
        result = ReservationResult(
            success=True,
            token="abc123",
            reason="",
            previous_position=0,
            new_position=100,
        )

        assert result.success is True
        assert result.token == "abc123"
        assert result.reason == ""
        assert result.previous_position == 0
        assert result.new_position == 100

    def test_reservation_result_failure(self) -> None:
        """ReservationResult should capture failure details."""
        result = ReservationResult(
            success=False,
            token=None,
            reason="Position limit exceeded: 1100 > 1000",
            previous_position=900,
            new_position=1100,
        )

        assert result.success is False
        assert result.token is None
        assert "exceeded" in result.reason

    def test_release_result_success(self) -> None:
        """ReleaseResult should capture release details."""
        result = ReleaseResult(
            success=True,
            reason="RELEASED",
            previous_position=100,
            new_position=0,
        )

        assert result.success is True
        assert result.reason == "RELEASED"
        assert result.previous_position == 100
        assert result.new_position == 0

    def test_release_result_failure(self) -> None:
        """ReleaseResult should capture failure details."""
        result = ReleaseResult(
            success=False,
            reason="TOKEN_NOT_FOUND",
        )

        assert result.success is False
        assert result.reason == "TOKEN_NOT_FOUND"
        assert result.previous_position is None
        assert result.new_position is None


class TestPositionReservationEdgeCases:
    """Tests for edge cases and error handling."""

    @pytest.fixture()
    def mock_redis(self) -> MagicMock:
        """Create a mock Redis client."""
        mock = MagicMock()
        mock.eval.return_value = [1, "test_token", 0, 100]
        mock.get.return_value = None
        return mock

    def test_reserve_with_existing_position(self, mock_redis: MagicMock) -> None:
        """Reserve should work with existing reserved position."""
        reservation = PositionReservation(redis=mock_redis, ttl=60)
        mock_redis.eval.return_value = [1, "abc123", 500, 600]

        result = reservation.reserve("AAPL", "buy", 100, max_limit=1000)

        assert result.success is True
        assert result.previous_position == 500
        assert result.new_position == 600

    def test_reserve_short_position(self, mock_redis: MagicMock) -> None:
        """Reserve should handle short positions correctly."""
        reservation = PositionReservation(redis=mock_redis, ttl=60)
        mock_redis.eval.return_value = [1, "abc123", 0, -100]

        result = reservation.reserve("AAPL", "sell", 100, max_limit=1000)

        assert result.success is True
        assert result.new_position == -100

    def test_reserve_blocked_short_limit(self, mock_redis: MagicMock) -> None:
        """Reserve should block shorts that exceed limit."""
        reservation = PositionReservation(redis=mock_redis, ttl=60)
        mock_redis.eval.return_value = [0, "LIMIT_EXCEEDED", -900, -1100]

        result = reservation.reserve("AAPL", "sell", 200, max_limit=1000)

        assert result.success is False
        assert result.new_position == -1100

    def test_ttl_passed_to_script(self, mock_redis: MagicMock) -> None:
        """TTL should be passed to Lua script."""
        reservation = PositionReservation(redis=mock_redis, ttl=120)
        mock_redis.eval.return_value = [1, "abc123", 0, 100]

        reservation.reserve("AAPL", "buy", 100, max_limit=1000)

        call_args = mock_redis.eval.call_args
        assert call_args[0][6] == "120"  # TTL is 7th arg

    def test_reserve_zero_qty_succeeds(self, mock_redis: MagicMock) -> None:
        """Reserve with zero qty should succeed (no-op)."""
        reservation = PositionReservation(redis=mock_redis, ttl=60)
        mock_redis.eval.return_value = [1, "abc123", 500, 500]

        result = reservation.reserve("AAPL", "buy", 0, max_limit=1000)

        assert result.success is True
        assert result.previous_position == result.new_position

    def test_different_symbols_independent(self, mock_redis: MagicMock) -> None:
        """Reservations for different symbols should be independent."""
        reservation = PositionReservation(redis=mock_redis, ttl=60)
        mock_redis.eval.return_value = [1, "abc123", 0, 100]

        reservation.reserve("AAPL", "buy", 100, max_limit=1000)
        reservation.reserve("MSFT", "buy", 100, max_limit=1000)

        calls = mock_redis.eval.call_args_list
        assert f"{RESERVATION_KEY_PREFIX}:AAPL" in str(calls[0])
        assert f"{RESERVATION_KEY_PREFIX}:MSFT" in str(calls[1])
