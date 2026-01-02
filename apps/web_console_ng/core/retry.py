"""Retry utilities for async HTTP calls."""

from __future__ import annotations

import asyncio
import functools
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import httpx

IDEMPOTENT_METHODS = {"GET", "HEAD"}

_T = TypeVar("_T")


def with_retry(
    max_attempts: int = 3,
    backoff_base: float = 1.0,
    method: str = "GET",
) -> Callable[[Callable[..., Awaitable[_T]]], Callable[..., Awaitable[_T]]]:
    """Return an idempotency-aware async retry decorator.

    - Idempotent methods (GET, HEAD): retry on transport errors and 5xx.
    - Non-idempotent methods: retry on transport errors only.
    - Never retry on 4xx.
    """

    method_upper = method.upper()

    def decorator(func: Callable[..., Awaitable[_T]]) -> Callable[..., Awaitable[_T]]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> _T:
            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except httpx.TransportError:
                    if attempt == max_attempts - 1:
                        raise
                    await asyncio.sleep(backoff_base * (2**attempt))
                except httpx.HTTPStatusError as exc:
                    status_code = exc.response.status_code
                    if status_code >= 500 and method_upper in IDEMPOTENT_METHODS:
                        if attempt == max_attempts - 1:
                            raise
                        await asyncio.sleep(backoff_base * (2**attempt))
                    else:
                        raise

            raise RuntimeError("Retry exhausted")

        return wrapper

    return decorator
