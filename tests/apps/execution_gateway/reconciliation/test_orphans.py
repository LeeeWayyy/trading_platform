"""Unit tests for orphans.py - orphan order detection and handling."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock, patch

import redis

from apps.execution_gateway.reconciliation.orphans import (
    QUARANTINE_STRATEGY_SENTINEL,
    detect_orphans,
    handle_orphan_order,
    set_quarantine,
    sync_orphan_exposure,
)


class TestHandleOrphanOrder:
    """Tests for handle_orphan_order function."""

    def test_returns_false_if_no_symbol(self) -> None:
        """Handle returns False if broker_order has no symbol."""
        broker_order: dict[str, Any] = {"id": "abc123", "side": "buy"}
        db_client = MagicMock()
        redis_client = MagicMock()

        result = handle_orphan_order(broker_order, db_client, redis_client)

        assert result is False
        db_client.create_orphan_order.assert_not_called()

    def test_returns_false_if_no_broker_order_id(self) -> None:
        """Handle returns False if broker_order has no id."""
        broker_order: dict[str, Any] = {"symbol": "AAPL", "side": "buy"}
        db_client = MagicMock()
        redis_client = MagicMock()

        result = handle_orphan_order(broker_order, db_client, redis_client)

        assert result is False
        db_client.create_orphan_order.assert_not_called()

    def test_creates_orphan_order_record(self) -> None:
        """Handle creates orphan order record in database."""
        broker_order = {
            "id": "broker123",
            "client_order_id": "client456",
            "symbol": "AAPL",
            "side": "buy",
            "qty": "100",
            "status": "filled",
        }
        db_client = MagicMock()
        redis_client = MagicMock()

        result = handle_orphan_order(broker_order, db_client, redis_client)

        assert result is True
        db_client.create_orphan_order.assert_called_once()
        call_kwargs = db_client.create_orphan_order.call_args.kwargs
        assert call_kwargs["broker_order_id"] == "broker123"
        assert call_kwargs["client_order_id"] == "client456"
        assert call_kwargs["symbol"] == "AAPL"
        assert call_kwargs["side"] == "buy"
        assert call_kwargs["qty"] == 100
        assert call_kwargs["strategy_id"] == QUARANTINE_STRATEGY_SENTINEL

    def test_sets_quarantine_for_symbol(self) -> None:
        """Handle sets quarantine for the symbol."""
        broker_order = {
            "id": "broker123",
            "symbol": "AAPL",
            "side": "buy",
            "qty": "50",
        }
        db_client = MagicMock()
        redis_client = MagicMock()

        handle_orphan_order(broker_order, db_client, redis_client)

        redis_client.set.assert_called()

    def test_handles_missing_optional_fields(self) -> None:
        """Handle works with minimal required fields."""
        broker_order = {
            "id": "broker123",
            "symbol": "TSLA",
        }
        db_client = MagicMock()
        redis_client = MagicMock()

        result = handle_orphan_order(broker_order, db_client, redis_client)

        assert result is True
        call_kwargs = db_client.create_orphan_order.call_args.kwargs
        assert call_kwargs["side"] == "unknown"
        assert call_kwargs["qty"] == 0
        assert call_kwargs["status"] == "untracked"

    def test_resolves_terminal_orders_when_flag_set(self) -> None:
        """Handle resolves terminal orders when resolve_terminal=True."""
        broker_order = {
            "id": "broker123",
            "symbol": "AAPL",
            "status": "filled",
        }
        db_client = MagicMock()
        redis_client = MagicMock()

        handle_orphan_order(
            broker_order, db_client, redis_client, resolve_terminal=True
        )

        update_call = db_client.update_orphan_order_status.call_args
        assert update_call.kwargs["resolved_at"] is not None

    def test_does_not_resolve_non_terminal_orders(self) -> None:
        """Handle does not resolve non-terminal orders even with flag."""
        broker_order = {
            "id": "broker123",
            "symbol": "AAPL",
            "status": "pending_new",  # Non-terminal
        }
        db_client = MagicMock()
        redis_client = MagicMock()

        handle_orphan_order(
            broker_order, db_client, redis_client, resolve_terminal=True
        )

        update_call = db_client.update_orphan_order_status.call_args
        assert update_call.kwargs["resolved_at"] is None

    def test_syncs_orphan_exposure_to_redis(self) -> None:
        """Handle syncs orphan exposure to Redis cache."""
        broker_order = {
            "id": "broker123",
            "symbol": "GOOG",
        }
        db_client = MagicMock()
        db_client.get_orphan_exposure.return_value = Decimal("5000")
        redis_client = MagicMock()

        handle_orphan_order(broker_order, db_client, redis_client)

        db_client.get_orphan_exposure.assert_called_once_with(
            "GOOG", QUARANTINE_STRATEGY_SENTINEL
        )

    def test_handles_none_redis_client(self) -> None:
        """Handle works when redis_client is None."""
        broker_order = {
            "id": "broker123",
            "symbol": "AAPL",
        }
        db_client = MagicMock()

        result = handle_orphan_order(broker_order, db_client, None)

        assert result is True
        db_client.create_orphan_order.assert_called_once()


class TestSetQuarantine:
    """Tests for set_quarantine function."""

    def test_returns_false_if_redis_client_none(self) -> None:
        """Set quarantine returns False when redis_client is None."""
        result = set_quarantine("AAPL", "*", None)

        assert result is False

    def test_sets_quarantine_key_in_redis(self) -> None:
        """Set quarantine sets the correct key in Redis."""
        redis_client = MagicMock()

        result = set_quarantine("AAPL", "*", redis_client)

        assert result is True
        redis_client.set.assert_called_once()

    def test_increments_prometheus_counter(self) -> None:
        """Set quarantine increments Prometheus counter."""
        redis_client = MagicMock()

        with patch(
            "apps.execution_gateway.reconciliation.orphans.symbols_quarantined_total"
        ) as mock_counter:
            set_quarantine("AAPL", "strategy1", redis_client)
            mock_counter.labels.assert_called()

    def test_handles_redis_error_gracefully(self) -> None:
        """Set quarantine handles Redis errors and returns False."""
        redis_client = MagicMock()
        redis_client.set.side_effect = redis.RedisError("Connection failed")

        result = set_quarantine("AAPL", "*", redis_client)

        assert result is False

    def test_handles_validation_error_gracefully(self) -> None:
        """Set quarantine handles validation errors and returns False."""
        redis_client = MagicMock()
        redis_client.set.side_effect = ValueError("Invalid key")

        result = set_quarantine("AAPL", "*", redis_client)

        assert result is False


class TestSyncOrphanExposure:
    """Tests for sync_orphan_exposure function."""

    def test_returns_false_if_redis_client_none(self) -> None:
        """Sync exposure returns False when redis_client is None."""
        db_client = MagicMock()

        result = sync_orphan_exposure("AAPL", "strategy1", db_client, None)

        assert result is False

    def test_fetches_exposure_from_db(self) -> None:
        """Sync exposure fetches current exposure from database."""
        db_client = MagicMock()
        db_client.get_orphan_exposure.return_value = Decimal("10000")
        redis_client = MagicMock()

        result = sync_orphan_exposure("AAPL", "strategy1", db_client, redis_client)

        assert result is True
        db_client.get_orphan_exposure.assert_called_once_with("AAPL", "strategy1")

    def test_sets_exposure_in_redis(self) -> None:
        """Sync exposure updates Redis with exposure value."""
        db_client = MagicMock()
        db_client.get_orphan_exposure.return_value = Decimal("7500")
        redis_client = MagicMock()

        sync_orphan_exposure("AAPL", QUARANTINE_STRATEGY_SENTINEL, db_client, redis_client)

        redis_client.set.assert_called_once()

    def test_handles_db_error_gracefully(self) -> None:
        """Sync exposure handles database errors and returns False."""
        import psycopg

        db_client = MagicMock()
        db_client.get_orphan_exposure.side_effect = psycopg.OperationalError(
            "DB connection lost"
        )
        redis_client = MagicMock()

        result = sync_orphan_exposure("AAPL", "strategy1", db_client, redis_client)

        assert result is False

    def test_handles_redis_error_gracefully(self) -> None:
        """Sync exposure handles Redis errors and returns False."""
        db_client = MagicMock()
        db_client.get_orphan_exposure.return_value = Decimal("1000")
        redis_client = MagicMock()
        redis_client.set.side_effect = redis.RedisError("Connection failed")

        result = sync_orphan_exposure("AAPL", "strategy1", db_client, redis_client)

        assert result is False


class TestDetectOrphans:
    """Tests for detect_orphans function."""

    def test_detects_orphan_in_open_orders(self) -> None:
        """Detect orphans identifies orders not in DB from open orders."""
        open_orders = [
            {"id": "broker1", "client_order_id": "client1", "symbol": "AAPL"},
            {"id": "broker2", "client_order_id": "unknown", "symbol": "TSLA"},
        ]
        recent_orders: list[dict[str, Any]] = []
        db_known_ids = {"client1"}  # Only client1 is known
        db_client = MagicMock()
        redis_client = MagicMock()

        count = detect_orphans(
            open_orders, recent_orders, db_known_ids, db_client, redis_client
        )

        assert count == 1
        # broker2 should be handled as orphan
        call_kwargs = db_client.create_orphan_order.call_args.kwargs
        assert call_kwargs["broker_order_id"] == "broker2"

    def test_detects_orphan_in_recent_orders_with_resolve(self) -> None:
        """Detect orphans resolves terminal orders in recent orders."""
        open_orders: list[dict[str, Any]] = []
        recent_orders = [
            {
                "id": "broker1",
                "client_order_id": "unknown",
                "symbol": "GOOG",
                "status": "filled",
            },
        ]
        db_known_ids: set[str] = set()
        db_client = MagicMock()
        redis_client = MagicMock()

        count = detect_orphans(
            open_orders, recent_orders, db_known_ids, db_client, redis_client
        )

        assert count == 1
        # Should resolve terminal order
        update_call = db_client.update_orphan_order_status.call_args
        assert update_call.kwargs["resolved_at"] is not None

    def test_skips_known_orders(self) -> None:
        """Detect orphans skips orders that are known in DB."""
        open_orders = [
            {"id": "broker1", "client_order_id": "known1", "symbol": "AAPL"},
        ]
        recent_orders = [
            {"id": "broker2", "client_order_id": "known2", "symbol": "TSLA"},
        ]
        db_known_ids = {"known1", "known2"}
        db_client = MagicMock()
        redis_client = MagicMock()

        count = detect_orphans(
            open_orders, recent_orders, db_known_ids, db_client, redis_client
        )

        assert count == 0
        db_client.create_orphan_order.assert_not_called()

    def test_handles_empty_lists(self) -> None:
        """Detect orphans handles empty order lists."""
        db_client = MagicMock()
        redis_client = MagicMock()

        count = detect_orphans([], [], set(), db_client, redis_client)

        assert count == 0

    def test_handles_orders_without_client_order_id(self) -> None:
        """Detect orphans handles orders missing client_order_id."""
        open_orders = [
            {"id": "broker1", "symbol": "AAPL"},  # No client_order_id
        ]
        db_known_ids: set[str] = set()
        db_client = MagicMock()
        redis_client = MagicMock()

        count = detect_orphans(open_orders, [], db_known_ids, db_client, redis_client)

        assert count == 1  # Should still be detected as orphan

    def test_counts_multiple_orphans(self) -> None:
        """Detect orphans correctly counts multiple orphans."""
        open_orders = [
            {"id": "broker1", "client_order_id": "orphan1", "symbol": "AAPL"},
            {"id": "broker2", "client_order_id": "orphan2", "symbol": "TSLA"},
        ]
        recent_orders = [
            {"id": "broker3", "client_order_id": "orphan3", "symbol": "GOOG"},
        ]
        db_known_ids: set[str] = set()
        db_client = MagicMock()
        redis_client = MagicMock()

        count = detect_orphans(
            open_orders, recent_orders, db_known_ids, db_client, redis_client
        )

        assert count == 3
