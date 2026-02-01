"""Export sanitization utilities for formula injection protection.

This module provides a SINGLE SOURCE OF TRUTH for sanitizing values
before exporting to CSV, Excel, or clipboard. Centralizing this logic
prevents security vulnerabilities from inconsistent implementations.

Formula injection attacks exploit spreadsheet formulas to execute
malicious code or exfiltrate data when users open exported files.

CRITICAL: Both Python (server-side Excel) and JavaScript (client-side
CSV/clipboard) implementations MUST be kept in sync. See:
- apps/web_console_ng/static/js/grid_export.js (JavaScript version)
"""

from __future__ import annotations

import re
from typing import Any


def sanitize_for_export(value: Any) -> Any:
    """Sanitize a cell value for export to prevent formula injection.

    This is the canonical Python implementation used for:
    1. Server-side Excel export (via openpyxl)
    2. Parity verification tests

    IMPORTANT: The JavaScript version in grid_export.js MUST produce
    IDENTICAL output. Changes here MUST be mirrored in JavaScript.

    Security coverage:
    - Blocks formulas starting with =, +, @
    - Blocks tab/CR/LF prefix bypass attempts
    - Blocks non-numeric negatives (e.g., "-1+1", "-A1")
    - Handles whitespace/control char prefix bypass attempts
    - Supports scientific notation (e.g., "-1.2E-5", "-1E+10")

    Args:
        value: Cell value to sanitize

    Returns:
        Sanitized value (strings may be prefixed with single quote)

    Examples:
        >>> sanitize_for_export("hello")
        'hello'
        >>> sanitize_for_export(123)
        123
        >>> sanitize_for_export("=SUM(A1:A10)")
        "'=SUM(A1:A10)"
        >>> sanitize_for_export("-123.45")
        '-123.45'
        >>> sanitize_for_export("-1+1")
        "'-1+1"
    """
    # Only sanitize strings - numbers, booleans, None pass through unchanged
    if not isinstance(value, str):
        return value

    # Strip leading whitespace and control characters to find first meaningful char
    # This prevents bypass via " =FORMULA" or "\t=FORMULA"
    # Using regex for consistent behavior with JavaScript version
    trimmed = re.sub(r"^[\s\x00-\x1f]+", "", value)
    if not trimmed:
        return value  # All whitespace - safe

    first_char = trimmed[0]
    dangerous = {"=", "+", "@", "\t", "\r", "\n"}

    # Check if first meaningful character is dangerous
    if first_char in dangerous:
        return "'" + value  # Prepend quote to ORIGINAL value

    # For '-', only allow if STRICTLY numeric (e.g., "-123.45", "-1.2E-5")
    # Block "-1+1", "-A1", etc. which could be formulas
    if first_char == "-":
        # Support standard decimals and scientific notation (e.g., -1E+10, -1.2e-5)
        strict_numeric_regex = re.compile(r"^-?\d+(\.\d+)?([eE][+-]?\d+)?$")
        if not strict_numeric_regex.match(trimmed):
            return "'" + value  # Non-numeric negative - sanitize

    return value  # Safe value


__all__ = ["sanitize_for_export"]
