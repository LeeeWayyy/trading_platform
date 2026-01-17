"""Tests for the Execution Gateway app factory helpers."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from apps.execution_gateway.app_factory import (
    create_app,
    create_mock_context,
    create_test_app,
    create_test_config,
    initialize_app_context,
    shutdown_app_context,
)


def test_create_app_sets_context_and_config_in_test_mode():
    ctx = create_mock_context()
    config = create_test_config(dry_run=False)

    app = create_app(test_mode=True, test_context=ctx, test_config=config)

    with TestClient(app) as client:
        response = client.get("/metrics")
        assert response.status_code == 200
        assert app.state.context is ctx
        assert app.state.config is config


def test_create_test_app_wrapper():
    ctx = create_mock_context()
    config = create_test_config()

    app = create_test_app(context=ctx, config=config)
    with TestClient(app):
        assert app.state.context is ctx
        assert app.state.config is config


def test_create_app_production_mode_runs_placeholder_lifespan():
    app = create_app()
    with TestClient(app) as client:
        response = client.get("/metrics")
        assert response.status_code == 200


def test_create_mock_context_overrides():
    custom_db = MagicMock()
    ctx = create_mock_context(db=custom_db, webhook_secret="override")

    assert ctx.db is custom_db
    assert ctx.webhook_secret == "override"


def test_create_test_config_overrides():
    config = create_test_config(dry_run=False, strategy_id="alt_strategy")
    assert config.dry_run is False
    assert config.strategy_id == "alt_strategy"


def test_initialize_app_context_not_implemented():
    with pytest.raises(NotImplementedError):
        _ = asyncio.run(initialize_app_context(MagicMock()))


def test_shutdown_app_context_not_implemented():
    with pytest.raises(NotImplementedError):
        asyncio.run(shutdown_app_context(MagicMock()))
