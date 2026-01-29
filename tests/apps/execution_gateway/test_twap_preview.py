"""Tests for TWAP preview endpoint."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from apps.execution_gateway import main
from apps.execution_gateway.order_slicer import TWAPSlicer


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # httpx>=0.28 removed the 'app' kwarg that Starlette's TestClient still passes.
    import httpx

    original_client_init = httpx.Client.__init__

    def _patched_client_init(self, *args, **kwargs):
        kwargs.pop("app", None)
        return original_client_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.Client, "__init__", _patched_client_init)

    main.app.state.context.twap_slicer = TWAPSlicer()
    main.app.state.context.alpaca = SimpleNamespace(get_latest_quotes=lambda _symbols: {})
    return TestClient(main.app)


def _preview_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "symbol": "AAPL",
        "side": "buy",
        "qty": 100,
        "order_type": "market",
        "duration_minutes": 5,
        "interval_seconds": 60,
        "strategy_id": "alpha_baseline",
        "timezone": "UTC",
    }
    payload.update(overrides)
    return payload


def test_twap_preview_422_min_slices_error_shape(client: TestClient) -> None:
    payload = _preview_payload(duration_minutes=5, interval_seconds=300)
    response = client.post("/api/v1/orders/twap-preview", json=payload)
    assert response.status_code == 422
    body = response.json()
    assert body.get("error") == "validation_error"
    assert isinstance(body.get("errors"), list)


def test_twap_preview_422_min_slice_size_error(client: TestClient) -> None:
    payload = _preview_payload(qty=15, duration_minutes=10, interval_seconds=60)
    response = client.post("/api/v1/orders/twap-preview", json=payload)
    assert response.status_code == 422
    body = response.json()
    assert body.get("error") == "validation_error"
    assert isinstance(body.get("errors"), list)


def test_twap_preview_success_returns_slicing_plan(client: TestClient) -> None:
    payload = _preview_payload(order_type="limit", limit_price="100.00")
    response = client.post("/api/v1/orders/twap-preview", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["slice_count"] == 5
    assert body["base_slice_qty"] == 20
    assert body["remainder_distribution"] == []
    assert len(body["scheduled_times"]) == len(body["display_times"])
    assert body["notional_warning"] is None


def test_twap_preview_notional_warning_when_no_quote(client: TestClient) -> None:
    payload = _preview_payload(order_type="market")
    response = client.post("/api/v1/orders/twap-preview", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["notional_warning"] is not None
    assert body["slice_notional"] is None


def test_twap_preview_start_time_validation(client: TestClient) -> None:
    past_time = datetime.now(UTC) - timedelta(hours=1)
    payload = _preview_payload(start_time=past_time.isoformat())
    response = client.post("/api/v1/orders/twap-preview", json=payload)
    assert response.status_code == 422
    body = response.json()
    assert body.get("error") == "validation_error"
    assert isinstance(body.get("errors"), list)
