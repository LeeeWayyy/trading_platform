"""Webhook isolation tests for signature verification flow."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from apps.execution_gateway.dependencies import get_config, get_context
from apps.execution_gateway.main import app


def test_webhook_processing_runs_after_signature_verification() -> None:
    """Verify processing executes after signature verification when secret is set."""
    call_order: list[str] = []

    def verify_side_effect(_body: bytes, _signature: str, _secret: str) -> bool:
        call_order.append("verify")
        return True

    def update_status_side_effect(*_args, **_kwargs):
        call_order.append("process")
        return SimpleNamespace(status="new")

    mock_db = Mock()
    mock_db.update_order_status_cas.side_effect = update_status_side_effect

    ctx = SimpleNamespace(
        db=mock_db,
        webhook_secret="super-secret",
    )

    config = SimpleNamespace(environment="production")

    app.dependency_overrides[get_context] = lambda: ctx
    app.dependency_overrides[get_config] = lambda: config

    payload = {
        "event": "new",
        "order": {
            "client_order_id": "abc123",
            "id": "broker-1",
            "status": "new",
        },
    }

    with (
        patch(
            "apps.execution_gateway.routes.webhooks.extract_signature_from_header",
            return_value="sig",
        ),
        patch(
            "apps.execution_gateway.routes.webhooks.verify_webhook_signature",
            side_effect=verify_side_effect,
        ),
    ):
        client = TestClient(app)
        response = client.post(
            "/api/v1/webhooks/orders",
            json=payload,
            headers={"X-Alpaca-Signature": "sig"},
        )

    assert response.status_code == 200
    assert "verify" in call_order
    assert "process" in call_order
    assert call_order.index("verify") < call_order.index("process")
    mock_db.update_order_status_cas.assert_called_once()
