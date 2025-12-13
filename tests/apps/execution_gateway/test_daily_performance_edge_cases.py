from __future__ import annotations

from datetime import date, timedelta

import pytest

fastapi = pytest.importorskip("fastapi")
TestClient = pytest.importorskip("fastapi.testclient").TestClient
Request = pytest.importorskip("starlette.requests").Request

from apps.execution_gateway import main


def _make_user_context_override(user_ctx: dict) -> callable:
    """Create a dependency override with proper Request signature.

    FastAPI inspects function parameters for dependency resolution.
    Using lambda *_, **__: causes FastAPI to treat _ and __ as required
    query parameters, resulting in 422 errors.
    """
    def override(request: Request) -> dict:
        return user_ctx
    return override


@pytest.fixture()
def test_client():
    return TestClient(main.app)


def test_daily_performance_future_date_rejected(monkeypatch, test_client):
    tomorrow = date.today() + timedelta(days=1)
    user_ctx = {
        "role": "viewer",
        "strategies": ["alpha"],
        "requested_strategies": ["alpha"],
        "user_id": "u1",
        "user": {"role": "viewer", "strategies": ["alpha"], "user_id": "u1"},
    }
    main.app.dependency_overrides[main._build_user_context] = _make_user_context_override(user_ctx)
    monkeypatch.setattr(main, "FEATURE_PERFORMANCE_DASHBOARD", True)

    resp = test_client.get(
        "/api/v1/performance/daily",
        params={"start_date": "2024-01-01", "end_date": tomorrow.isoformat(), "strategies": ["alpha"]},
        headers={"X-User-Role": "viewer", "X-User-Id": "u1"},
    )

    assert resp.status_code == 422


def test_daily_performance_flag_disabled_returns_404(monkeypatch, test_client):
    user_ctx = {
        "role": "viewer",
        "strategies": ["alpha"],
        "requested_strategies": ["alpha"],
        "user_id": "u1",
        "user": {"role": "viewer", "strategies": ["alpha"], "user_id": "u1"},
    }
    main.app.dependency_overrides[main._build_user_context] = _make_user_context_override(user_ctx)
    monkeypatch.setattr(main, "FEATURE_PERFORMANCE_DASHBOARD", False)

    resp = test_client.get(
        "/api/v1/performance/daily",
        params={"strategies": ["alpha"]},
        headers={"X-User-Role": "viewer", "X-User-Id": "u1"},
    )

    assert resp.status_code == 404
