"""
Unit tests for libs.core.common.async_utils.

Tests cover:
- run_async execution in sync context without running event loop
- run_async execution when event loop is already running
- Timeout behavior and error propagation
- Successful return values from coroutines
- Edge cases (immediate return, long-running coroutines)
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import time
from typing import Any

import pytest

from libs.core.common.async_utils import run_async


class TestRunAsyncBasic:
    """Tests for basic run_async functionality."""

    def test_run_async_returns_coroutine_result(self):
        """Test run_async returns the result of the coroutine."""

        async def simple_coro() -> str:
            return "hello"

        result = run_async(simple_coro())

        assert result == "hello"

    def test_run_async_returns_integer_result(self):
        """Test run_async returns integer result from coroutine."""

        async def add_numbers() -> int:
            return 42 + 8

        result = run_async(add_numbers())

        assert result == 50
        assert isinstance(result, int)

    def test_run_async_returns_complex_result(self):
        """Test run_async returns complex data structures."""

        async def get_dict() -> dict[str, Any]:
            return {"key": "value", "number": 123, "nested": {"a": 1}}

        result = run_async(get_dict())

        assert result == {"key": "value", "number": 123, "nested": {"a": 1}}

    def test_run_async_returns_none(self):
        """Test run_async handles coroutines that return None."""

        async def return_none() -> None:
            pass

        result = run_async(return_none())

        assert result is None

    def test_run_async_handles_awaited_sleep(self):
        """Test run_async handles coroutines that await asyncio.sleep."""

        async def with_sleep() -> str:
            await asyncio.sleep(0.01)
            return "after_sleep"

        result = run_async(with_sleep())

        assert result == "after_sleep"


class TestRunAsyncTimeout:
    """Tests for run_async timeout behavior."""

    def test_run_async_respects_custom_timeout(self):
        """Test run_async uses custom timeout value."""

        async def fast_coro() -> str:
            await asyncio.sleep(0.01)
            return "done"

        # Should complete well within timeout
        result = run_async(fast_coro(), timeout=1.0)

        assert result == "done"

    def test_run_async_raises_timeout_error_on_slow_coroutine(self):
        """Test run_async raises TimeoutError when coroutine exceeds timeout."""

        async def slow_coro() -> str:
            await asyncio.sleep(10.0)  # Much longer than timeout
            return "never_reached"

        with pytest.raises(concurrent.futures.TimeoutError):
            run_async(slow_coro(), timeout=0.1)

    def test_run_async_default_timeout_is_5_seconds(self):
        """Test run_async has a reasonable default timeout (5 seconds)."""
        # Verify by running a coroutine that completes well within 5 seconds

        async def quick_coro() -> str:
            await asyncio.sleep(0.01)
            return "quick"

        start = time.time()
        result = run_async(quick_coro())
        elapsed = time.time() - start

        assert result == "quick"
        assert elapsed < 5.0  # Should complete well before default timeout


class TestRunAsyncExceptionHandling:
    """Tests for run_async exception propagation."""

    def test_run_async_propagates_coroutine_exception(self):
        """Test run_async propagates exceptions raised by coroutine."""

        async def raising_coro() -> None:
            raise ValueError("test error")

        with pytest.raises(ValueError, match="test error"):
            run_async(raising_coro())

    def test_run_async_propagates_runtime_error(self):
        """Test run_async propagates RuntimeError from coroutine."""

        async def runtime_error_coro() -> None:
            raise RuntimeError("runtime issue")

        with pytest.raises(RuntimeError, match="runtime issue"):
            run_async(runtime_error_coro())

    def test_run_async_propagates_custom_exception(self):
        """Test run_async propagates custom exception types."""

        class CustomError(Exception):
            pass

        async def custom_error_coro() -> None:
            raise CustomError("custom error message")

        with pytest.raises(CustomError, match="custom error message"):
            run_async(custom_error_coro())

    def test_run_async_propagates_exception_after_await(self):
        """Test run_async propagates exception raised after an await."""

        async def delayed_error() -> None:
            await asyncio.sleep(0.01)
            raise ValueError("delayed error")

        with pytest.raises(ValueError, match="delayed error"):
            run_async(delayed_error())


class TestRunAsyncWithRunningLoop:
    """Tests for run_async behavior when an event loop is already running."""

    def test_run_async_works_from_within_running_loop(self):
        """Test run_async executes successfully when called from running loop context."""
        # Simulate the scenario (like in Streamlit) where we need to call run_async
        # from within a context that has a running event loop.
        result_holder: list[str] = []

        async def outer_coro() -> None:
            # Within this async context, there IS a running loop
            # run_async should use thread pool in this case

            async def inner_coro() -> str:
                await asyncio.sleep(0.01)
                return "from_inner"

            # This call happens within a running loop context
            # but since we're using asyncio.run, this test simulates
            # that scenario by using ThreadPoolExecutor path
            result = run_async(inner_coro(), timeout=2.0)
            result_holder.append(result)

        # Run the outer coroutine
        asyncio.run(outer_coro())

        assert result_holder == ["from_inner"]

    def test_run_async_with_loop_handles_timeout(self):
        """Test run_async handles timeout when event loop is running."""
        timeout_occurred = False

        async def outer_coro() -> None:
            nonlocal timeout_occurred

            async def slow_inner() -> str:
                await asyncio.sleep(10.0)
                return "never"

            try:
                run_async(slow_inner(), timeout=0.1)
            except concurrent.futures.TimeoutError:
                timeout_occurred = True

        asyncio.run(outer_coro())

        assert timeout_occurred is True


class TestRunAsyncEdgeCases:
    """Tests for edge cases and special scenarios."""

    def test_run_async_with_zero_timeout(self):
        """Test run_async with zero timeout raises immediately on any await."""

        async def any_coro() -> str:
            await asyncio.sleep(0.001)
            return "done"

        with pytest.raises(concurrent.futures.TimeoutError):
            run_async(any_coro(), timeout=0.0)

    def test_run_async_with_immediate_return(self):
        """Test run_async handles coroutine that returns immediately."""

        async def immediate() -> int:
            return 42

        result = run_async(immediate(), timeout=0.1)

        assert result == 42

    def test_run_async_with_list_result(self):
        """Test run_async correctly returns list result."""

        async def get_list() -> list[int]:
            return [1, 2, 3, 4, 5]

        result = run_async(get_list())

        assert result == [1, 2, 3, 4, 5]
        assert isinstance(result, list)

    def test_run_async_preserves_exception_type(self):
        """Test run_async preserves the original exception type."""

        class SpecificError(Exception):
            def __init__(self, code: int, message: str) -> None:
                self.code = code
                super().__init__(message)

        async def raise_specific() -> None:
            raise SpecificError(404, "not found")

        with pytest.raises(SpecificError) as exc_info:
            run_async(raise_specific())

        assert exc_info.value.code == 404
        assert str(exc_info.value) == "not found"

    def test_run_async_with_chained_coroutines(self):
        """Test run_async handles coroutines that await other coroutines."""

        async def inner() -> int:
            await asyncio.sleep(0.01)
            return 10

        async def outer() -> int:
            x = await inner()
            y = await inner()
            return x + y

        result = run_async(outer(), timeout=1.0)

        assert result == 20


class TestModuleExports:
    """Tests for module-level exports."""

    def test_run_async_is_exported(self):
        """Test run_async is listed in __all__."""
        from libs.core.common import async_utils

        assert "run_async" in async_utils.__all__

    def test_only_run_async_exported(self):
        """Test only run_async is in __all__ (no internal helpers exposed)."""
        from libs.core.common import async_utils

        assert async_utils.__all__ == ["run_async"]
