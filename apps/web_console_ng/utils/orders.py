"""Order utility functions for web console.

This module provides shared utilities for order validation and identification.
"""

from __future__ import annotations

import re
from typing import Any

# ID prefixes that indicate uncancellable orders
SYNTHETIC_ID_PREFIX = "SYNTH-"
FALLBACK_ID_PREFIX = "FALLBACK-"

# Combined tuple for startswith checks
UNCANCELLABLE_PREFIXES = (SYNTHETIC_ID_PREFIX, FALLBACK_ID_PREFIX)

# Default concurrency limit for bulk order cancellation
# Used across components to avoid overwhelming the backend
DEFAULT_CONCURRENT_CANCELS = 5

# Symbol validation pattern (alphanumeric, dots, hyphens - typical stock symbols)
SYMBOL_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,14}$", re.IGNORECASE)


def is_cancellable_order_id(order_id: Any) -> bool:
    """Check if an order ID is cancellable.

    Args:
        order_id: The order ID to check (can be any type).

    Returns:
        True if the order ID is valid and cancellable, False otherwise.

    Examples:
        >>> is_cancellable_order_id("abc123")
        True
        >>> is_cancellable_order_id("SYNTH-123")
        False
        >>> is_cancellable_order_id("FALLBACK-456")
        False
        >>> is_cancellable_order_id(None)
        False
        >>> is_cancellable_order_id("")
        False
    """
    if not isinstance(order_id, str) or not order_id:
        return False
    return not order_id.startswith(UNCANCELLABLE_PREFIXES)


def validate_symbol(symbol: Any) -> tuple[str | None, str]:
    """Validate and normalize a trading symbol.

    Args:
        symbol: The symbol to validate (can be any type).

    Returns:
        Tuple of (normalized_symbol, error_message).
        If valid, returns (uppercase_symbol, "").
        If invalid, returns (None, error_description).

    Examples:
        >>> validate_symbol("AAPL")
        ('AAPL', '')
        >>> validate_symbol("aapl")
        ('AAPL', '')
        >>> validate_symbol("BRK.B")
        ('BRK.B', '')
        >>> validate_symbol("../admin")
        (None, 'Invalid symbol format')
        >>> validate_symbol(None)
        (None, 'Symbol must be a non-empty string')
    """
    if not isinstance(symbol, str) or not symbol:
        return None, "Symbol must be a non-empty string"

    symbol = symbol.strip().upper()

    if not SYMBOL_PATTERN.match(symbol):
        return None, "Invalid symbol format"

    return symbol, ""


__all__ = [
    "SYNTHETIC_ID_PREFIX",
    "FALLBACK_ID_PREFIX",
    "UNCANCELLABLE_PREFIXES",
    "DEFAULT_CONCURRENT_CANCELS",
    "is_cancellable_order_id",
    "validate_symbol",
]
