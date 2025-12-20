"""Async utilities for running coroutines from sync contexts."""

from __future__ import annotations

import asyncio
import concurrent.futures
from collections.abc import Coroutine
from typing import Any, TypeVar

_T = TypeVar("_T")


def run_async(coro: Coroutine[Any, Any, _T], timeout: float = 5.0) -> _T:
    """Execute a coroutine from a synchronous context.

    Handles the case where an event loop may or may not be running (e.g., Streamlit).
    If a loop is running, uses a thread pool to run the coroutine in a new loop.
    Otherwise, uses asyncio.run() directly with a timeout guard.

    Args:
        coro: The coroutine to execute.
        timeout: Maximum time to wait for completion (seconds).

    Returns:
        The result of the coroutine.

    Raises:
        concurrent.futures.TimeoutError: If execution exceeds timeout.
    """

    def _run_in_thread() -> _T:
        # Always enforce the timeout, even when we spawn a new loop.
        try:
            return asyncio.run(asyncio.wait_for(coro, timeout))
        except TimeoutError as exc:  # pragma: no cover - surfaced to caller
            raise concurrent.futures.TimeoutError() from exc

    try:
        _ = asyncio.get_running_loop()
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(_run_in_thread)
            return future.result(timeout=timeout)
    except RuntimeError:
        # No running loop; run coroutine in this thread while still enforcing timeout.
        try:
            return asyncio.run(asyncio.wait_for(coro, timeout))
        except TimeoutError as exc:
            raise concurrent.futures.TimeoutError() from exc


__all__ = ["run_async"]
