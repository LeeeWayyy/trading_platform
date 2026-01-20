"""Tests for webhook endpoints in apps/execution_gateway/routes/webhooks.py."""

from __future__ import annotations

import json
from contextlib import contextmanager
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.execution_gateway.app_factory import create_mock_context, create_test_config
from apps.execution_gateway.dependencies import get_config, get_context
from apps.execution_gateway.routes import webhooks
from apps.execution_gateway.webhook_security import generate_webhook_signature


def _build_app(ctx: Any, config: Any) -> FastAPI:
    app = FastAPI()
    app.include_router(webhooks.router)
    app.dependency_overrides[get_context] = lambda: ctx
    app.dependency_overrides[get_config] = lambda: config
    return app


def _signed_request(client: TestClient, payload: dict[str, Any] | list[Any], secret: str):
    body = json.dumps(payload, separators=(",", ":"), default=str).encode("utf-8")
    signature = generate_webhook_signature(body, secret)
    headers = {
        "X-Alpaca-Signature": signature,
        "Content-Type": "application/json",
    }
    return client.post("/api/v1/webhooks/orders", data=body, headers=headers)


@contextmanager
def _transaction_context(conn: MagicMock):
    yield conn


class TestOrderWebhooks:
    def test_missing_signature_returns_401(self) -> None:
        ctx = create_mock_context(webhook_secret="secret")
        config = create_test_config(environment="test")
        app = _build_app(ctx, config)

        client = TestClient(app)
        response = client.post(
            "/api/v1/webhooks/orders",
            json={"event": "fill", "order": {"client_order_id": "abc"}},
        )

        assert response.status_code == 401
        assert response.json()["detail"] == "Missing webhook signature"

    def test_invalid_signature_returns_401(self) -> None:
        ctx = create_mock_context(webhook_secret="secret")
        config = create_test_config(environment="test")
        app = _build_app(ctx, config)

        client = TestClient(app)
        response = client.post(
            "/api/v1/webhooks/orders",
            json={"event": "fill", "order": {"client_order_id": "abc"}},
            headers={"X-Alpaca-Signature": "deadbeef"},
        )

        assert response.status_code == 401
        assert response.json()["detail"] == "Invalid webhook signature"

    def test_missing_secret_in_production_returns_503(self) -> None:
        ctx = create_mock_context(webhook_secret=None)
        config = create_test_config(environment="production")
        app = _build_app(ctx, config)

        client = TestClient(app)
        response = client.post(
            "/api/v1/webhooks/orders",
            json={"event": "fill", "order": {"client_order_id": "abc"}},
        )

        assert response.status_code == 503
        assert response.json()["detail"].startswith("Webhook verification unavailable")

    def test_payload_not_object_returns_400(self) -> None:
        ctx = create_mock_context(webhook_secret="secret")
        config = create_test_config(environment="test")
        app = _build_app(ctx, config)

        client = TestClient(app)
        response = _signed_request(client, ["not", "a", "dict"], "secret")

        assert response.status_code == 400
        assert response.json()["detail"] == "Webhook payload must be a JSON object"

    def test_fast_path_updates_status_without_fill(self) -> None:
        mock_db = MagicMock()
        mock_db.update_order_status_cas.return_value = MagicMock()
        ctx = create_mock_context(db=mock_db, webhook_secret="secret")
        config = create_test_config(environment="test")
        app = _build_app(ctx, config)

        payload = {
            "event": "new",
            "timestamp": "2025-01-01T00:00:00Z",
            "order": {
                "client_order_id": "order-123",
                "id": "broker-1",
                "status": "new",
                "filled_qty": "0",
                "filled_avg_price": None,
                "updated_at": "2025-01-01T00:00:00Z",
            },
        }

        client = TestClient(app)
        response = _signed_request(client, payload, "secret")

        assert response.status_code == 200
        assert response.json() == {"status": "ok", "client_order_id": "order-123"}
        assert mock_db.update_order_status_cas.called

    def test_fill_updates_position_and_order(self) -> None:
        mock_db = MagicMock()
        conn = MagicMock()
        mock_db.transaction.return_value = _transaction_context(conn)

        order = MagicMock()
        order.symbol = "AAPL"
        order.side = "buy"
        order.filled_qty = Decimal("1")
        mock_db.get_order_for_update.return_value = order

        position_locked = MagicMock()
        position_locked.realized_pl = Decimal("5")
        mock_db.get_position_for_update.return_value = position_locked

        updated_position = MagicMock()
        updated_position.realized_pl = Decimal("8")
        mock_db.update_position_on_fill_with_conn.return_value = updated_position

        ctx = create_mock_context(db=mock_db, redis=MagicMock(), webhook_secret="secret")
        config = create_test_config(environment="test")
        app = _build_app(ctx, config)

        payload = {
            "event": "fill",
            "timestamp": "2025-01-01T00:00:00Z",
            "execution_id": "exec-1",
            "price": "",
            "order": {
                "client_order_id": "order-123",
                "id": "broker-1",
                "status": "filled",
                "filled_qty": "2",
                "filled_avg_price": "100.5",
                "updated_at": "2025-01-01T00:00:00Z",
                "filled_at": "2025-01-01T00:00:00Z",
            },
        }

        with patch(
            "apps.execution_gateway.routes.webhooks.invalidate_performance_cache"
        ) as mock_invalidate:
            client = TestClient(app)
            response = _signed_request(client, payload, "secret")

        assert response.status_code == 200
        assert response.json() == {"status": "ok", "client_order_id": "order-123"}

        mock_db.update_position_on_fill_with_conn.assert_called_once()
        mock_db.append_fill_to_order_metadata.assert_called_once()
        mock_db.update_order_status_with_conn.assert_called_once()
        mock_invalidate.assert_called_once()

    def test_fill_with_no_incremental_does_not_update_position(self) -> None:
        mock_db = MagicMock()
        conn = MagicMock()
        mock_db.transaction.return_value = _transaction_context(conn)

        order = MagicMock()
        order.symbol = "AAPL"
        order.side = "buy"
        order.filled_qty = Decimal("2")
        mock_db.get_order_for_update.return_value = order

        ctx = create_mock_context(db=mock_db, redis=MagicMock(), webhook_secret="secret")
        config = create_test_config(environment="test")
        app = _build_app(ctx, config)

        payload = {
            "event": "partial_fill",
            "timestamp": "2025-01-01T00:00:00Z",
            "execution_id": "exec-2",
            "price": "101.0",
            "order": {
                "client_order_id": "order-456",
                "id": "broker-2",
                "status": "partially_filled",
                "filled_qty": "2",
                "filled_avg_price": "101.0",
                "updated_at": "2025-01-01T00:00:00Z",
                "filled_at": "2025-01-01T00:00:00Z",
            },
        }

        client = TestClient(app)
        response = _signed_request(client, payload, "secret")

        assert response.status_code == 200
        assert response.json() == {"status": "ok", "client_order_id": "order-456"}
        mock_db.update_position_on_fill_with_conn.assert_not_called()
        mock_db.append_fill_to_order_metadata.assert_not_called()
        mock_db.update_order_status_with_conn.assert_called_once()
