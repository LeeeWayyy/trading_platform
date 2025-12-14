"""Async execution helpers for Streamlit pages.

This module provides utilities for running async coroutines from synchronous
Streamlit context, which doesn't have a native event loop.

The ThreadPoolExecutor pattern is used to avoid event loop conflicts that occur
when trying to use asyncio.run() directly in Streamlit.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Module-level executor for reuse across calls
# 4 workers is sufficient for typical Streamlit page loads
_executor = ThreadPoolExecutor(max_workers=4)

# Default timeout for async operations (seconds)
DEFAULT_ASYNC_TIMEOUT = 30


def run_async(
    coro: Coroutine[Any, Any, T],
    timeout: float = DEFAULT_ASYNC_TIMEOUT,
) -> T:
    """Execute async coroutine from sync Streamlit context.

    Uses ThreadPoolExecutor to avoid event loop conflicts in Streamlit.
    Each execution creates a fresh event loop in a worker thread.

    Args:
        coro: The coroutine to execute
        timeout: Maximum time to wait for result (seconds)

    Returns:
        The result from the coroutine

    Raises:
        TimeoutError: If execution exceeds timeout
        Exception: Any exception raised by the coroutine
    """

    def _run() -> T:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    future = _executor.submit(_run)
    try:
        return future.result(timeout=timeout)
    except FuturesTimeoutError:
        logger.warning(
            "async_operation_timeout",
            extra={"timeout": timeout},
        )
        raise TimeoutError(f"Async operation timed out after {timeout}s") from None


def shutdown_executor() -> None:
    """Shutdown the thread pool executor.

    Should be called during application shutdown to clean up resources.
    Not typically needed for Streamlit apps as they manage their own lifecycle.
    """
    _executor.shutdown(wait=False)


__all__ = [
    "run_async",
    "shutdown_executor",
    "DEFAULT_ASYNC_TIMEOUT",
]
