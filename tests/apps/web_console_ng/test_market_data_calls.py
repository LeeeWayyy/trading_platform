"""Tests for shared web-console market data call helpers."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from apps.web_console_ng.components.market_data_calls import call_market_data_client


@pytest.mark.asyncio()
async def test_call_market_data_client_retries_type_error_with_legacy_signature() -> None:
    """TypeError signature fallback retries legacy mode and logs strategy_id context."""

    async def client_call(**kwargs: object) -> dict[str, str]:
        if "user_id" in kwargs:
            raise TypeError("legacy client")
        return {"symbol": str(kwargs["symbol"])}

    logger = MagicMock(spec=logging.Logger)

    response = await call_market_data_client(
        client_call,
        request_kwargs={"symbol": "AAPL"},
        user_id="user-1",
        role="admin",
        strategies=["alpha", "beta"],
        logger=logger,
        operation="test_operation",
        symbol="AAPL",
    )

    assert response == {"symbol": "AAPL"}
    logger.debug.assert_called_once()
    _, kwargs = logger.debug.call_args
    assert kwargs["extra"]["strategy_id"] == "alpha,beta"


@pytest.mark.asyncio()
async def test_call_market_data_client_reraises_final_type_error() -> None:
    """A final TypeError is logged and re-raised instead of being masked as no data."""

    async def client_call(**kwargs: object) -> None:
        raise TypeError("internal client failure")

    logger = MagicMock(spec=logging.Logger)

    with pytest.raises(TypeError, match="internal client failure"):
        await call_market_data_client(
            client_call,
            request_kwargs={"symbol": "AAPL"},
            user_id="user-1",
            role="admin",
            strategies=["alpha"],
            logger=logger,
            operation="test_operation",
            symbol="AAPL",
        )

    assert logger.debug.call_count == 2
    _, kwargs = logger.debug.call_args
    assert kwargs["extra"]["strategy_id"] == "alpha"
    assert kwargs["extra"]["attempt_mode"] == "legacy"


@pytest.mark.asyncio()
async def test_call_market_data_client_reraises_first_unexpected_error() -> None:
    """Non-signature client failures should not be retried as legacy calls."""

    async def client_call(**kwargs: object) -> dict[str, str]:
        if "user_id" in kwargs:
            raise RuntimeError("auth wrapper unavailable")
        return {"symbol": str(kwargs["symbol"])}

    logger = MagicMock(spec=logging.Logger)

    with pytest.raises(RuntimeError, match="auth wrapper unavailable"):
        await call_market_data_client(
            client_call,
            request_kwargs={"symbol": "AAPL"},
            user_id="user-1",
            role="admin",
            strategies=["alpha"],
            logger=logger,
            operation="test_operation",
            symbol="AAPL",
        )

    logger.debug.assert_called_once()
    _, kwargs = logger.debug.call_args
    assert kwargs["extra"]["strategy_id"] == "alpha"
    assert kwargs["extra"]["attempt_mode"] == "auth"
