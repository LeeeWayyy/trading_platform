"""Tests for CircuitBreakerService."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import psycopg
import pytest

from libs.web_console_services.cb_service import (
    CircuitBreakerService,
    RateLimitExceeded,
    RBACViolation,
    ValidationError,
)
from libs.web_console_services.config import MIN_CIRCUIT_BREAKER_RESET_REASON_LENGTH


class TestCircuitBreakerServiceGetStatus:
    """Tests for get_status method."""

    @pytest.fixture()
    def mock_redis(self) -> MagicMock:
        """Create mock Redis client."""
        return MagicMock()

    @pytest.fixture()
    def cb_service(self, mock_redis: MagicMock) -> CircuitBreakerService:
        """Create service with mocked dependencies."""
        with patch("libs.web_console_services.cb_service.CircuitBreaker") as mock_breaker_class:
            mock_breaker = MagicMock()
            mock_breaker_class.return_value = mock_breaker
            service = CircuitBreakerService(mock_redis, db_pool=None)
            service.breaker = mock_breaker
            return service

    def test_get_status_returns_breaker_status(self, cb_service: CircuitBreakerService) -> None:
        """get_status should return status from breaker."""
        expected_status = {"state": "OPEN", "trip_count_today": 0}
        cb_service.breaker.get_status.return_value = expected_status

        result = cb_service.get_status()

        assert result == expected_status
        cb_service.breaker.get_status.assert_called_once()

    def test_get_status_increments_metric(self, cb_service: CircuitBreakerService) -> None:
        """get_status should increment Prometheus counter."""
        cb_service.breaker.get_status.return_value = {"state": "OPEN"}

        with patch("libs.web_console_services.cb_service.CB_STATUS_CHECKS") as mock_counter:
            cb_service.get_status()
            mock_counter.inc.assert_called_once()


class TestCircuitBreakerServiceTrip:
    """Tests for trip method."""

    @pytest.fixture()
    def mock_redis(self) -> MagicMock:
        """Create mock Redis client."""
        return MagicMock()

    @pytest.fixture()
    def cb_service(self, mock_redis: MagicMock) -> CircuitBreakerService:
        """Create service with mocked dependencies."""
        with patch("libs.web_console_services.cb_service.CircuitBreaker") as mock_breaker_class:
            mock_breaker = MagicMock()
            mock_breaker_class.return_value = mock_breaker
            service = CircuitBreakerService(mock_redis, db_pool=None)
            service.breaker = mock_breaker
            return service

    def test_trip_succeeds_with_permission(self, cb_service: CircuitBreakerService) -> None:
        """trip should succeed for user with TRIP_CIRCUIT permission."""
        user = {"user_id": "test_user", "role": "operator"}

        result = cb_service.trip("MANUAL", user, acknowledged=True)

        assert result is True
        cb_service.breaker.trip.assert_called_once()

    def test_trip_fails_without_permission(self, cb_service: CircuitBreakerService) -> None:
        """trip should raise RBACViolation for user without permission."""
        user = {"user_id": "test_user", "role": "viewer"}

        with pytest.raises(RBACViolation, match="lacks TRIP_CIRCUIT permission"):
            cb_service.trip("MANUAL", user, acknowledged=True)

        cb_service.breaker.trip.assert_not_called()

    def test_trip_increments_metric(self, cb_service: CircuitBreakerService) -> None:
        """trip should increment Prometheus counter."""
        user = {"user_id": "test_user", "role": "admin"}

        with patch("libs.web_console_services.cb_service.CB_TRIP_TOTAL") as mock_counter:
            cb_service.trip("MANUAL", user, acknowledged=True)
            mock_counter.inc.assert_called_once()

    def test_trip_fails_without_acknowledgment(self, cb_service: CircuitBreakerService) -> None:
        """trip should raise ValidationError without acknowledgment."""
        user = {"user_id": "test_user", "role": "operator"}

        with pytest.raises(ValidationError, match="must be explicitly acknowledged"):
            cb_service.trip("MANUAL", user, acknowledged=False)

        cb_service.breaker.trip.assert_not_called()


class TestCircuitBreakerServiceReset:
    """Tests for reset method."""

    @pytest.fixture()
    def mock_redis(self) -> MagicMock:
        """Create mock Redis client."""
        return MagicMock()

    @pytest.fixture()
    def cb_service(self, mock_redis: MagicMock) -> CircuitBreakerService:
        """Create service with mocked dependencies."""
        with patch("libs.web_console_services.cb_service.CircuitBreaker") as mock_breaker_class:
            mock_breaker = MagicMock()
            mock_breaker_class.return_value = mock_breaker
            # Mock get_status to return TRIPPED (required for reset validation)
            mock_breaker.get_status.return_value = {"state": "TRIPPED"}
            service = CircuitBreakerService(mock_redis, db_pool=None)
            service.breaker = mock_breaker
            service.rate_limiter = MagicMock()
            service.rate_limiter.check_global.return_value = True
            return service

    def test_reset_succeeds_with_valid_inputs(self, cb_service: CircuitBreakerService) -> None:
        """reset should succeed with valid reason, acknowledgment, and permission."""
        user = {"user_id": "test_user", "role": "operator"}
        reason = "Conditions cleared, verified system health"

        result = cb_service.reset(reason, user, acknowledged=True)

        assert result is True
        cb_service.breaker.reset.assert_called_once()
        cb_service.breaker.update_history_with_reset.assert_called_once()

    def test_reset_fails_without_permission(self, cb_service: CircuitBreakerService) -> None:
        """reset should raise RBACViolation for user without permission."""
        user = {"user_id": "test_user", "role": "viewer"}
        reason = "Conditions cleared, verified system health"

        with pytest.raises(RBACViolation, match="lacks RESET_CIRCUIT permission"):
            cb_service.reset(reason, user, acknowledged=True)

        cb_service.breaker.reset.assert_not_called()

    def test_reset_fails_with_short_reason(self, cb_service: CircuitBreakerService) -> None:
        """reset should raise ValidationError if reason too short."""
        user = {"user_id": "test_user", "role": "operator"}
        reason = "short"  # Less than MIN_CIRCUIT_BREAKER_RESET_REASON_LENGTH

        with pytest.raises(
            ValidationError,
            match=f"at least {MIN_CIRCUIT_BREAKER_RESET_REASON_LENGTH} characters",
        ):
            cb_service.reset(reason, user, acknowledged=True)

        cb_service.breaker.reset.assert_not_called()

    def test_reset_fails_without_acknowledgment(self, cb_service: CircuitBreakerService) -> None:
        """reset should raise ValidationError without acknowledgment."""
        user = {"user_id": "test_user", "role": "operator"}
        reason = "Conditions cleared, verified system health"

        with pytest.raises(ValidationError, match="must be explicitly acknowledged"):
            cb_service.reset(reason, user, acknowledged=False)

        cb_service.breaker.reset.assert_not_called()

    def test_reset_fails_when_rate_limited(self, cb_service: CircuitBreakerService) -> None:
        """reset should raise RateLimitExceeded when rate limit hit."""
        user = {"user_id": "test_user", "role": "operator"}
        reason = "Conditions cleared, verified system health"
        cb_service.rate_limiter.check_global.return_value = False

        with pytest.raises(RateLimitExceeded, match="Max 1 reset per minute"):
            cb_service.reset(reason, user, acknowledged=True)

        cb_service.breaker.reset.assert_not_called()

    def test_reset_fails_when_not_tripped(self, cb_service: CircuitBreakerService) -> None:
        """reset should fail without consuming rate limit if not TRIPPED."""
        user = {"user_id": "test_user", "role": "operator"}
        reason = "Conditions cleared, verified system health"
        # Mock breaker as OPEN (not TRIPPED)
        cb_service.breaker.get_status.return_value = {"state": "OPEN"}

        with pytest.raises(ValidationError, match="Cannot reset.*not TRIPPED"):
            cb_service.reset(reason, user, acknowledged=True)

        # Rate limiter should NOT be called (token not consumed)
        cb_service.rate_limiter.check_global.assert_not_called()
        cb_service.breaker.reset.assert_not_called()

    def test_reset_clears_rate_limit_on_breaker_failure(
        self, cb_service: CircuitBreakerService
    ) -> None:
        """reset should clear rate limit token if breaker.reset() fails."""
        from libs.trading.risk_management.breaker import CircuitBreakerError

        user = {"user_id": "test_user", "role": "operator"}
        reason = "Conditions cleared, verified system health"
        # Mock breaker.reset to fail (e.g., WatchError/race condition)
        cb_service.breaker.reset.side_effect = CircuitBreakerError("State changed")

        with pytest.raises(CircuitBreakerError):
            cb_service.reset(reason, user, acknowledged=True)

        # Rate limit should be cleared to allow retry
        cb_service.rate_limiter.clear.assert_called_once()

    def test_reset_increments_metric(self, cb_service: CircuitBreakerService) -> None:
        """reset should increment Prometheus counter."""
        user = {"user_id": "test_user", "role": "admin"}
        reason = "Conditions cleared, verified system health"

        with patch("libs.web_console_services.cb_service.CB_RESET_TOTAL") as mock_counter:
            cb_service.reset(reason, user, acknowledged=True)
            mock_counter.inc.assert_called_once()


class TestCircuitBreakerServiceGetHistory:
    """Tests for get_history method."""

    @pytest.fixture()
    def mock_redis(self) -> MagicMock:
        """Create mock Redis client."""
        return MagicMock()

    @pytest.fixture()
    def cb_service(self, mock_redis: MagicMock) -> CircuitBreakerService:
        """Create service with mocked dependencies."""
        with patch("libs.web_console_services.cb_service.CircuitBreaker") as mock_breaker_class:
            mock_breaker = MagicMock()
            mock_breaker_class.return_value = mock_breaker
            service = CircuitBreakerService(mock_redis, db_pool=None)
            service.breaker = mock_breaker
            return service

    def test_get_history_returns_breaker_history(self, cb_service: CircuitBreakerService) -> None:
        """get_history should return history from breaker."""
        expected_history = [{"tripped_at": "2025-12-18T10:00:00", "reason": "MANUAL"}]
        cb_service.breaker.get_history.return_value = expected_history

        result = cb_service.get_history(limit=50)

        assert result == expected_history
        cb_service.breaker.get_history.assert_called_once_with(limit=50)

    def test_get_history_falls_back_on_redis_error(self, cb_service: CircuitBreakerService) -> None:
        """get_history should fall back to audit log on Redis error."""
        cb_service.breaker.get_history.side_effect = Exception("Redis error")

        # Without db_pool, fallback returns empty list
        result = cb_service.get_history(limit=50)

        assert result == []


class TestCircuitBreakerServiceAuditFallback:
    """Tests for audit log fallback when Redis is unavailable."""

    @pytest.fixture()
    def mock_redis(self) -> MagicMock:
        """Create mock Redis client."""
        return MagicMock()

    @pytest.fixture()
    def mock_db_pool(self) -> MagicMock:
        """Create mock database pool."""
        return MagicMock()

    @pytest.fixture()
    def cb_service_with_db(
        self, mock_redis: MagicMock, mock_db_pool: MagicMock
    ) -> CircuitBreakerService:
        """Create service with mocked dependencies including db_pool."""
        with patch("libs.web_console_services.cb_service.CircuitBreaker") as mock_breaker_class:
            mock_breaker = MagicMock()
            mock_breaker_class.return_value = mock_breaker
            service = CircuitBreakerService(mock_redis, db_pool=mock_db_pool)
            service.breaker = mock_breaker
            return service

    def test_audit_fallback_maps_trip_to_history_shape(
        self, cb_service_with_db: CircuitBreakerService, mock_db_pool: MagicMock
    ) -> None:
        """Fallback should map unpaired TRIP to Redis history shape (no reset info)."""
        # Make Redis history fail
        cb_service_with_db.breaker.get_history.side_effect = Exception("Redis error")

        # Mock audit log rows - single TRIP without RESET (currently tripped)
        # Query returns DESC order (newest first), but single row doesn't matter
        mock_timestamp = datetime(2025, 12, 18, 10, 0, 0, tzinfo=UTC)
        mock_row = (
            mock_timestamp,
            "CIRCUIT_BREAKER_TRIP",
            {"reason": "DAILY_LOSS_EXCEEDED"},
            "test_user",
        )
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [mock_row]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_db_pool.connection.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_db_pool.connection.return_value.__exit__ = MagicMock(return_value=False)

        result = cb_service_with_db.get_history(limit=10)

        # Unpaired trip should have tripped_at, reason, and details
        assert len(result) == 1
        assert result[0]["tripped_at"] == "2025-12-18T10:00:00+00:00"
        assert result[0]["reason"] == "DAILY_LOSS_EXCEEDED"
        assert result[0]["details"] == {"tripped_by": "test_user"}
        assert "reset_at" not in result[0]
        assert "reset_by" not in result[0]

    def test_audit_fallback_pairs_trip_and_reset(
        self, cb_service_with_db: CircuitBreakerService, mock_db_pool: MagicMock
    ) -> None:
        """Fallback should pair TRIP and RESET events to match Redis history shape."""
        # Make Redis history fail
        cb_service_with_db.breaker.get_history.side_effect = Exception("Redis error")

        # Mock audit log rows - query returns DESC order (newest first)
        # So RESET comes before TRIP in the raw result
        trip_timestamp = datetime(2025, 12, 18, 10, 0, 0, tzinfo=UTC)
        reset_timestamp = datetime(2025, 12, 18, 11, 0, 0, tzinfo=UTC)
        mock_rows = [
            # DESC order: newest (reset) first, then older (trip)
            (
                reset_timestamp,
                "CIRCUIT_BREAKER_RESET",
                {"reason": "Conditions cleared"},
                "operator",
            ),
            (
                trip_timestamp,
                "CIRCUIT_BREAKER_TRIP",
                {"reason": "DAILY_LOSS_EXCEEDED"},
                "trip_user",
            ),
        ]
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = mock_rows
        mock_conn = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_db_pool.connection.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_db_pool.connection.return_value.__exit__ = MagicMock(return_value=False)

        result = cb_service_with_db.get_history(limit=10)

        # Paired TRIP+RESET should be merged into single entry like Redis
        assert len(result) == 1
        assert result[0]["tripped_at"] == "2025-12-18T10:00:00+00:00"
        assert result[0]["reason"] == "DAILY_LOSS_EXCEEDED"
        assert result[0]["details"] == {"tripped_by": "trip_user"}
        assert result[0]["reset_at"] == "2025-12-18T11:00:00+00:00"
        assert result[0]["reset_by"] == "operator"
        assert result[0]["reset_reason"] == "Conditions cleared"

    def test_audit_fallback_handles_db_error_gracefully(
        self, cb_service_with_db: CircuitBreakerService, mock_db_pool: MagicMock
    ) -> None:
        """Fallback should return empty list on database error."""
        # Make Redis history fail
        cb_service_with_db.breaker.get_history.side_effect = Exception("Redis error")

        # Make DB also fail with psycopg.Error (specific exception type)
        mock_db_pool.connection.side_effect = psycopg.Error("DB connection failed")

        result = cb_service_with_db.get_history(limit=10)

        assert result == []


class TestCircuitBreakerServiceRBAC:
    """RBAC-specific tests."""

    @pytest.fixture()
    def mock_redis(self) -> MagicMock:
        """Create mock Redis client."""
        return MagicMock()

    @pytest.fixture()
    def cb_service(self, mock_redis: MagicMock) -> CircuitBreakerService:
        """Create service with mocked dependencies."""
        with patch("libs.web_console_services.cb_service.CircuitBreaker") as mock_breaker_class:
            mock_breaker = MagicMock()
            mock_breaker_class.return_value = mock_breaker
            # Mock get_status to return TRIPPED (required for reset validation)
            mock_breaker.get_status.return_value = {"state": "TRIPPED"}
            service = CircuitBreakerService(mock_redis, db_pool=None)
            service.breaker = mock_breaker
            service.rate_limiter = MagicMock()
            service.rate_limiter.check_global.return_value = True
            return service

    @pytest.mark.parametrize("role", ["operator", "admin"])
    def test_trip_allowed_for_authorized_roles(
        self, cb_service: CircuitBreakerService, role: str
    ) -> None:
        """trip should succeed for operator and admin roles."""
        user = {"user_id": "test_user", "role": role}

        result = cb_service.trip("MANUAL", user, acknowledged=True)

        assert result is True

    def test_trip_denied_for_viewer(self, cb_service: CircuitBreakerService) -> None:
        """trip should be denied for viewer role."""
        user = {"user_id": "test_user", "role": "viewer"}

        with pytest.raises(RBACViolation):
            cb_service.trip("MANUAL", user, acknowledged=True)

    @pytest.mark.parametrize("role", ["operator", "admin"])
    def test_reset_allowed_for_authorized_roles(
        self, cb_service: CircuitBreakerService, role: str
    ) -> None:
        """reset should succeed for operator and admin roles."""
        user = {"user_id": "test_user", "role": role}
        reason = "Conditions cleared, verified system health"

        result = cb_service.reset(reason, user, acknowledged=True)

        assert result is True

    def test_reset_denied_for_viewer(self, cb_service: CircuitBreakerService) -> None:
        """reset should be denied for viewer role."""
        user = {"user_id": "test_user", "role": "viewer"}
        reason = "Conditions cleared, verified system health"

        with pytest.raises(RBACViolation):
            cb_service.reset(reason, user, acknowledged=True)
