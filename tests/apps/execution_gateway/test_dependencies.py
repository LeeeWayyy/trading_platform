"""Tests for dependency providers and test overrides."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from starlette.requests import Request

from apps.execution_gateway.app_context import AppContext
from apps.execution_gateway.dependencies import (
    TestConfigOverride,
    TestContextOverride,
    get_alpaca_client,
    get_config,
    get_context,
    get_db_client,
    get_fat_finger_validator,
    get_liquidity_service,
    get_metrics,
    get_reconciliation_service,
    get_recovery_manager,
    get_redis_client,
    get_twap_slicer,
    get_version,
)


def _make_request(app: FastAPI) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [],
        "query_string": b"",
        "server": ("test", 80),
        "client": ("test", 123),
        "scheme": "http",
        "app": app,
    }
    return Request(scope)


def _make_context(redis_client: object | None = None) -> AppContext:
    return AppContext(
        db=MagicMock(),
        redis=redis_client,
        alpaca=MagicMock(),
        liquidity_service=MagicMock(),
        reconciliation_service=MagicMock(),
        recovery_manager=MagicMock(),
        risk_config=MagicMock(),
        fat_finger_validator=MagicMock(),
        twap_slicer=MagicMock(),
        webhook_secret="secret",
    )


def test_get_context_missing_raises():
    app = FastAPI()
    request = _make_request(app)
    with pytest.raises(RuntimeError):
        get_context(request)


def test_get_config_missing_raises():
    app = FastAPI()
    request = _make_request(app)
    with pytest.raises(RuntimeError):
        get_config(request)


def test_get_version_missing_raises():
    app = FastAPI()
    request = _make_request(app)
    with pytest.raises(RuntimeError):
        get_version(request)


def test_get_metrics_missing_raises():
    app = FastAPI()
    request = _make_request(app)
    with pytest.raises(RuntimeError):
        get_metrics(request)


def test_get_context_config_version_metrics_return_values():
    app = FastAPI()
    app.state.context = _make_context()
    app.state.config = SimpleNamespace(dry_run=True)
    app.state.version = "1.2.3"
    app.state.metrics = {"db": MagicMock()}

    request = _make_request(app)

    assert get_context(request) is app.state.context
    assert get_config(request) is app.state.config
    assert get_version(request) == "1.2.3"
    assert get_metrics(request) is app.state.metrics


def test_component_accessors():
    redis_client = MagicMock()
    ctx = _make_context(redis_client=redis_client)

    assert get_db_client(ctx) is ctx.db
    assert get_redis_client(ctx) is redis_client
    assert get_alpaca_client(ctx) is ctx.alpaca
    assert get_recovery_manager(ctx) is ctx.recovery_manager
    assert get_reconciliation_service(ctx) is ctx.reconciliation_service
    assert get_fat_finger_validator(ctx) is ctx.fat_finger_validator
    assert get_liquidity_service(ctx) is ctx.liquidity_service
    assert get_twap_slicer(ctx) is ctx.twap_slicer


def test_component_accessors_with_missing_redis():
    ctx = _make_context(redis_client=None)
    assert get_redis_client(ctx) is None


def test_test_context_override_sync_and_async():
    app = FastAPI()
    ctx = _make_context()
    app.state.context = "original"

    with TestContextOverride(app, ctx):
        assert app.state.context is ctx

    assert app.state.context == "original"


@pytest.mark.asyncio()
async def test_test_context_override_async():
    app = FastAPI()
    ctx = _make_context()
    app.state.context = "original"

    async with TestContextOverride(app, ctx):
        assert app.state.context is ctx

    assert app.state.context == "original"


def test_test_config_override_sync_and_async():
    app = FastAPI()
    config = SimpleNamespace(dry_run=True)
    app.state.config = "original"

    with TestConfigOverride(app, config):
        assert app.state.config is config

    assert app.state.config == "original"


@pytest.mark.asyncio()
async def test_test_config_override_async():
    app = FastAPI()
    config = SimpleNamespace(dry_run=True)
    app.state.config = "original"

    async with TestConfigOverride(app, config):
        assert app.state.config is config

    assert app.state.config == "original"
