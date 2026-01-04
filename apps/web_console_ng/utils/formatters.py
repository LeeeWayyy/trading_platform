"""Shared formatting and conversion utilities for NiceGUI components.

This module consolidates common helper functions used across chart components
to avoid code duplication (DRY principle).
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any


def parse_date_for_sort(date_str: str) -> datetime:
    """Parse date string to datetime for proper chronological sorting.

    Handles ISO format dates (YYYY-MM-DD) and datetime strings.
    Converts timezone-aware datetimes to UTC before stripping tzinfo to
    ensure correct ordering across different timezones.
    Falls back to datetime.min if parsing fails, placing invalid dates first.

    Args:
        date_str: Date string in ISO format (YYYY-MM-DD or ISO datetime)

    Returns:
        Parsed datetime for sorting, or datetime.min on parse failure
    """
    try:
        # Try ISO date format first (most common)
        if len(date_str) == 10 and date_str[4] == "-" and date_str[7] == "-":
            return datetime.strptime(date_str, "%Y-%m-%d")
        # Try ISO datetime format (may be timezone-aware)
        if "T" in date_str:
            parsed = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            # Convert to UTC then strip tzinfo for consistent naive comparison
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone(UTC).replace(tzinfo=None)
            return parsed
        # Fallback: try parsing as date
        parsed = datetime.strptime(date_str[:10], "%Y-%m-%d")
        return parsed
    except (ValueError, TypeError):
        # If parsing fails, use epoch to place invalid dates first
        return datetime.min


def safe_float(value: Any, default: float | None = None) -> float | None:
    """Safely convert value to float, returning default on failure.

    Used for converting risk metrics from API responses that may contain
    None, invalid strings, NaN/inf, or other non-numeric values.

    Args:
        value: Value to convert (can be None, str, int, float, or any type)
        default: Value to return on conversion failure (default: None)

    Returns:
        Converted float value, or default if conversion fails or value is NaN/inf
    """
    if value is None:
        return default
    try:
        result = float(value)
        if not math.isfinite(result):
            return default  # Reject NaN/inf as invalid
        return result
    except (ValueError, TypeError):
        return default


__all__ = [
    "parse_date_for_sort",
    "safe_float",
]
