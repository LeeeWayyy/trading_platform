"""Tests for CircuitBreakerService audit and fallback behavior."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
import redis

from libs.web_console_services.cb_service import CircuitBreakerService


def _build_service(db_pool: MagicMock | None = None) -> CircuitBreakerService:
    with patch("libs.web_console_services.cb_service.CircuitBreaker") as breaker_class, patch(
        "libs.web_console_services.cb_service.CBRateLimiter"
    ) as rate_limiter_class:
        breaker = MagicMock()
        breaker_class.return_value = breaker
        rate_limiter = MagicMock()
        rate_limiter_class.return_value = rate_limiter
        service = CircuitBreakerService(MagicMock(), db_pool=db_pool)
        service.breaker = breaker
        service.rate_limiter = rate_limiter
        return service


def _mock_db_pool(rows: list[tuple[datetime, str, object, str | None]]) -> MagicMock:
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = rows
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    mock_conn.cursor.return_value.__exit__.return_value = False
    mock_pool = MagicMock()
    mock_pool.connection.return_value.__enter__.return_value = mock_conn
    mock_pool.connection.return_value.__exit__.return_value = False
    return mock_pool


@patch("libs.web_console_services.cb_service.admin_action_total")
def test_log_audit_without_db_pool_falls_back(
    admin_action_total: MagicMock, caplog: pytest.LogCaptureFixture
) -> None:
    service = _build_service(db_pool=None)

    with caplog.at_level("INFO"):
        service._log_audit(
            action="CIRCUIT_BREAKER_TRIP",
            user={"user_id": "user-1"},
            resource_type="circuit_breaker",
            resource_id="global",
            reason="MANUAL",
            details={"tripped_by": "user-1"},
            outcome="success",
        )

    admin_action_total.labels.assert_called_once_with(action="CIRCUIT_BREAKER_TRIP")
    admin_action_total.labels.return_value.inc.assert_called_once()
    assert "audit_log_fallback" in caplog.text


@patch("libs.web_console_services.cb_service.admin_action_total")
@patch("libs.web_console_services.cb_service.audit_write_latency_seconds")
def test_log_audit_with_db_pool_writes(
    audit_write_latency_seconds: MagicMock, admin_action_total: MagicMock
) -> None:
    db_pool = _mock_db_pool([])
    service = _build_service(db_pool=db_pool)

    service._log_audit(
        action="CIRCUIT_BREAKER_RESET",
        user={"user_id": "user-2", "username": "operator"},
        resource_type="circuit_breaker",
        resource_id="global",
        reason="Recovered",
        details={"reset_by": "user-2"},
        outcome="success",
    )

    admin_action_total.labels.assert_called_once_with(action="CIRCUIT_BREAKER_RESET")
    admin_action_total.labels.return_value.inc.assert_called_once()

    cursor = db_pool.connection.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
    cursor.execute.assert_called_once()
    _, params = cursor.execute.call_args[0]
    assert params[0] == "CIRCUIT_BREAKER_RESET"
    assert params[1] == "circuit_breaker"
    assert params[2] == "global"
    assert params[3] == "user-2"
    assert params[4] == "operator"
    audit_details = json.loads(params[6])
    assert audit_details["reason"] == "Recovered"
    assert audit_details["reset_by"] == "user-2"
    audit_write_latency_seconds.observe.assert_called_once()


def test_get_history_from_audit_pairs_multiple_trips() -> None:
    trip1_time = datetime(2025, 12, 18, 9, 0, 0, tzinfo=UTC)
    trip2_time = datetime(2025, 12, 18, 10, 0, 0, tzinfo=UTC)
    reset_time = datetime(2025, 12, 18, 11, 0, 0, tzinfo=UTC)
    rows = [
        (reset_time, "CIRCUIT_BREAKER_RESET", {"reason": "Recovered", "reset_by": "op-2"}, "op-2"),
        (trip2_time, "CIRCUIT_BREAKER_TRIP", {"reason": "LOSS", "tripped_by": "op-2"}, "op-2"),
        (trip1_time, "CIRCUIT_BREAKER_TRIP", {"reason": "VOLATILITY"}, "op-1"),
    ]

    service = _build_service(db_pool=_mock_db_pool(rows))

    history = service._get_history_from_audit(limit=10)

    assert len(history) == 2
    assert history[0]["reason"] == "LOSS"
    assert history[0]["reset_by"] == "op-2"
    assert history[0]["reset_reason"] == "Recovered"
    assert history[1]["reason"] == "VOLATILITY"
    assert history[1]["details"] == {"tripped_by": "op-1"}


def test_reset_clears_rate_limit_on_redis_error() -> None:
    service = _build_service(db_pool=None)
    service.breaker.get_status.return_value = {"state": "TRIPPED"}
    service.rate_limiter.check_global.return_value = True
    service.breaker.reset.side_effect = redis.exceptions.RedisError("redis down")

    user = {"user_id": "operator", "role": "operator"}
    reason = "Conditions cleared, verified system health"

    with patch("libs.web_console_services.cb_service.has_permission", return_value=True):
        with pytest.raises(redis.exceptions.RedisError):
            service.reset(reason, user, acknowledged=True)

    service.rate_limiter.clear.assert_called_once()
