from unittest.mock import patch

import pytest
from fastapi import HTTPException

from apps.execution_gateway.main import _enforce_reduce_only_order
from apps.execution_gateway.schemas import OrderRequest


class StubAlpaca:
    def __init__(self, position_qty, open_orders):
        self._position_qty = position_qty
        self._open_orders = open_orders

    def get_open_position(self, symbol):
        if self._position_qty == 0:
            return None
        return {"symbol": symbol, "qty": self._position_qty}

    def get_orders(self, status, limit, after):
        return self._open_orders


class StubLock:
    def __init__(self):
        self._locked = False

    def acquire(self, blocking=True):
        self._locked = True
        return True

    def release(self):
        self._locked = False

    def locked(self):
        return self._locked


class StubRedis:
    def lock(self, name, timeout, blocking_timeout=None):
        return StubLock()


@pytest.mark.asyncio()
async def test_reduce_only_blocks_increasing_order():
    alpaca = StubAlpaca(
        position_qty=10,
        open_orders=[],
    )
    order = OrderRequest(symbol="AAPL", side="buy", qty=5, order_type="market")

    with (
        patch("apps.execution_gateway.main.alpaca_client", alpaca),
        patch("apps.execution_gateway.main.redis_client", StubRedis()),
    ):
        with pytest.raises(HTTPException):
            await _enforce_reduce_only_order(order)


@pytest.mark.asyncio()
async def test_reduce_only_allows_reducing_order():
    alpaca = StubAlpaca(
        position_qty=10,
        open_orders=[],
    )
    order = OrderRequest(symbol="AAPL", side="sell", qty=5, order_type="market")

    with (
        patch("apps.execution_gateway.main.alpaca_client", alpaca),
        patch("apps.execution_gateway.main.redis_client", StubRedis()),
    ):
        await _enforce_reduce_only_order(order)
