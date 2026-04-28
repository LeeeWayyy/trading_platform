"""Helpers for web-console market-data client calls.

The web console supports both current authenticated client signatures and older
test/client stubs. This module centralizes signature probing so UI components do
not pay a TypeError fallback cost on every refresh.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Literal, TypeAlias

AuthMode: TypeAlias = Literal["auth", "legacy"]
_SIGNATURE_MODE_CACHE: dict[tuple[int, str], AuthMode] = {}


def _signature_cache_key(callable_obj: Any) -> tuple[int, str]:
    """Build a stable cache key for functions and recreated bound method objects."""
    bound_self = getattr(callable_obj, "__self__", None)
    bound_func = getattr(callable_obj, "__func__", None)
    if bound_self is not None and bound_func is not None:
        return (id(bound_self), getattr(bound_func, "__qualname__", repr(bound_func)))
    return (id(callable_obj), getattr(callable_obj, "__qualname__", repr(callable_obj)))


def _supports_auth_kwargs(callable_obj: Any) -> bool:
    """Detect whether a client method accepts web-console auth kwargs."""
    try:
        signature = inspect.signature(callable_obj)
    except (TypeError, ValueError):
        # Builtins/decorated callables may not expose a reliable signature.
        return True

    parameters = signature.parameters
    has_var_keyword = any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()
    )
    return has_var_keyword or all(key in parameters for key in ("user_id", "role", "strategies"))


async def call_market_data_client(
    callable_obj: Callable[..., Awaitable[Any]],
    *,
    request_kwargs: dict[str, Any],
    user_id: str,
    role: str | None,
    strategies: list[str],
    logger: logging.Logger,
    operation: str,
    symbol: str,
    extra: dict[str, Any] | None = None,
) -> Any | None:
    """Call a market-data client method with cached auth/legacy signature fallback."""
    auth_kwargs: dict[str, Any] = {
        **request_kwargs,
        "user_id": user_id,
        "role": role,
        "strategies": strategies,
    }
    cache_key = _signature_cache_key(callable_obj)
    preferred_mode = _SIGNATURE_MODE_CACHE.get(cache_key)
    if preferred_mode is None:
        preferred_mode = "auth" if _supports_auth_kwargs(callable_obj) else "legacy"

    attempt_modes: tuple[AuthMode, AuthMode] = (
        (preferred_mode, "legacy" if preferred_mode == "auth" else "auth")
    )

    context: dict[str, Any] = {
        "operation": operation,
        "symbol": symbol,
        "strategy_id": ",".join(str(strategy) for strategy in strategies) if strategies else None,
    }
    if extra:
        context.update(extra)

    for index, mode in enumerate(attempt_modes):
        try:
            response = await callable_obj(**(auth_kwargs if mode == "auth" else request_kwargs))
            _SIGNATURE_MODE_CACHE[cache_key] = mode
            return response
        except TypeError as exc:
            if index == 0:
                logger.debug(
                    "market_data_client_signature_fallback",
                    extra={**context, "attempt_mode": mode, "error_type": type(exc).__name__},
                )
                continue
            logger.debug(
                "market_data_client_call_failed",
                extra={**context, "attempt_mode": mode, "error_type": type(exc).__name__},
            )
            if index == 0:
                continue
            raise
        except Exception as exc:
            logger.debug(
                "market_data_client_call_failed",
                extra={**context, "attempt_mode": mode, "error_type": type(exc).__name__},
            )
            if index == 0:
                continue
            raise

    return None
