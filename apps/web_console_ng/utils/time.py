"""Time and symbol validation utilities for Order Entry Context.

This module provides shared utilities used across OrderTicket, MarketContext,
PriceChart, Watchlist, and OrderEntryContext components.

Key difference from formatters.parse_date_for_sort:
- parse_date_for_sort: Returns naive datetime (for sorting)
- parse_iso_timestamp: Returns tz-aware UTC datetime (for staleness calculations)

Both are needed since "aware - aware" datetime comparisons work correctly
while "aware - naive" comparisons raise TypeError.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime


def parse_iso_timestamp(timestamp_str: str) -> datetime:
    """Parse ISO 8601 timestamp string, handling 'Z' suffix and converting to UTC.

    This helper normalizes the 'Z' suffix (common in JSON payloads and API responses)
    to '+00:00' for consistent parsing. While Python 3.11+ supports 'Z' natively,
    we normalize for robustness and explicit UTC handling.

    SAFETY: Always returns a timezone-aware datetime NORMALIZED TO UTC.
    - If source has timezone offset (e.g., +05:00), converts to UTC equivalent
    - If source is naive (no timezone), assumes UTC
    This ensures consistent staleness calculations (aware - aware works correctly).

    Args:
        timestamp_str: ISO 8601 timestamp (e.g., "2024-01-15T10:30:00Z" or
            "2024-01-15T10:30:00+05:00")

    Returns:
        Timezone-aware datetime object normalized to UTC.

    Raises:
        ValueError: If timestamp_str is not a valid ISO 8601 format.
    """
    # Replace 'Z' with '+00:00' for Python compatibility
    normalized = timestamp_str.replace("Z", "+00:00")
    dt = datetime.fromisoformat(normalized)

    # SAFETY: Normalize to UTC for consistent staleness calculations
    if dt.tzinfo is None:
        # Naive timestamp: assume UTC
        dt = dt.replace(tzinfo=UTC)
    else:
        # Aware timestamp: convert to UTC (e.g., +05:00 -> UTC equivalent)
        dt = dt.astimezone(UTC)

    return dt


# Symbol validation pattern: uppercase letters, digits, with optional single dot/hyphen delimiters
# Pattern: starts with alphanumeric, optionally followed by delimiter + alphanumeric groups
# Examples: AAPL, BRK.B, SPY-W, 3M
# Rejects: .AAPL, AAPL-, BRK..B (consecutive/leading/trailing delimiters)
# Max 10 chars total to prevent abuse
VALID_SYMBOL_PATTERN = re.compile(r"^[A-Z0-9]+([.\-][A-Z0-9]+)*$")


def _check_symbol_length(symbol: str) -> bool:
    """Check if symbol is within allowed length (1-10 chars)."""
    return 1 <= len(symbol) <= 10


def validate_and_normalize_symbol(symbol: str) -> str:
    """Validate and normalize a stock symbol for channel subscription.

    SECURITY: Symbols come from user input and are used to construct Redis channel names.
    We must validate to prevent malformed/malicious channel names.

    Normalization:
    - Strip whitespace
    - Convert to uppercase

    Validation:
    - Must be 1-10 characters
    - Only alphanumeric, dots, and hyphens allowed
    - Dots allow symbols like BRK.B, hyphens for special cases

    Args:
        symbol: Raw symbol string from user input

    Returns:
        Normalized symbol (uppercase, stripped)

    Raises:
        ValueError: If symbol is invalid (empty, too long, or contains invalid chars)
    """
    if not symbol:
        raise ValueError("Symbol cannot be empty")

    normalized = symbol.strip().upper()

    # Check for empty after stripping (whitespace-only input)
    if not normalized:
        raise ValueError("Symbol cannot be empty")

    # Check length constraint (1-10 chars)
    if not _check_symbol_length(normalized):
        raise ValueError(f"Invalid symbol format: {symbol!r}")

    if not VALID_SYMBOL_PATTERN.match(normalized):
        raise ValueError(f"Invalid symbol format: {symbol!r}")

    return normalized


__all__ = [
    "parse_iso_timestamp",
    "validate_and_normalize_symbol",
    "VALID_SYMBOL_PATTERN",
]
