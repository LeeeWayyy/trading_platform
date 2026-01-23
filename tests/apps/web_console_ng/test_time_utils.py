"""Tests for time utilities."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from apps.web_console_ng.utils.time import (
    VALID_SYMBOL_PATTERN,
    parse_iso_timestamp,
    validate_and_normalize_symbol,
)


class TestParseIsoTimestamp:
    """Tests for parse_iso_timestamp function."""

    def test_parses_z_suffix(self) -> None:
        """Handles 'Z' suffix (ISO 8601 UTC indicator)."""
        result = parse_iso_timestamp("2024-01-15T10:30:00Z")
        assert result == datetime(2024, 1, 15, 10, 30, 0, tzinfo=UTC)

    def test_parses_plus_offset(self) -> None:
        """Handles positive timezone offset, converts to UTC."""
        result = parse_iso_timestamp("2024-01-15T15:30:00+05:00")
        # 15:30 +05:00 = 10:30 UTC
        assert result == datetime(2024, 1, 15, 10, 30, 0, tzinfo=UTC)

    def test_parses_minus_offset(self) -> None:
        """Handles negative timezone offset, converts to UTC."""
        result = parse_iso_timestamp("2024-01-15T05:30:00-05:00")
        # 05:30 -05:00 = 10:30 UTC
        assert result == datetime(2024, 1, 15, 10, 30, 0, tzinfo=UTC)

    def test_parses_utc_explicit(self) -> None:
        """Handles explicit +00:00 offset."""
        result = parse_iso_timestamp("2024-01-15T10:30:00+00:00")
        assert result == datetime(2024, 1, 15, 10, 30, 0, tzinfo=UTC)

    def test_naive_timestamp_assumes_utc(self) -> None:
        """Naive timestamps (no timezone) are assumed UTC."""
        result = parse_iso_timestamp("2024-01-15T10:30:00")
        assert result == datetime(2024, 1, 15, 10, 30, 0, tzinfo=UTC)

    def test_with_microseconds(self) -> None:
        """Handles microseconds in timestamp."""
        result = parse_iso_timestamp("2024-01-15T10:30:00.123456Z")
        assert result == datetime(2024, 1, 15, 10, 30, 0, 123456, tzinfo=UTC)

    def test_always_returns_utc(self) -> None:
        """Result is always in UTC timezone."""
        result = parse_iso_timestamp("2024-01-15T15:30:00+05:00")
        assert result.tzinfo == UTC

    def test_invalid_format_raises_value_error(self) -> None:
        """Invalid timestamp format raises ValueError."""
        with pytest.raises(ValueError, match="Invalid isoformat string"):
            parse_iso_timestamp("not-a-timestamp")

    def test_empty_string_raises_value_error(self) -> None:
        """Empty string raises ValueError."""
        with pytest.raises(ValueError, match="Invalid isoformat string"):
            parse_iso_timestamp("")


class TestValidateAndNormalizeSymbol:
    """Tests for validate_and_normalize_symbol function."""

    def test_uppercase_alphanumeric(self) -> None:
        """Accepts uppercase alphanumeric symbols."""
        assert validate_and_normalize_symbol("AAPL") == "AAPL"
        assert validate_and_normalize_symbol("SPY") == "SPY"

    def test_normalizes_to_uppercase(self) -> None:
        """Converts lowercase to uppercase."""
        assert validate_and_normalize_symbol("aapl") == "AAPL"
        assert validate_and_normalize_symbol("Msft") == "MSFT"

    def test_strips_whitespace(self) -> None:
        """Strips leading and trailing whitespace."""
        assert validate_and_normalize_symbol("  AAPL  ") == "AAPL"
        assert validate_and_normalize_symbol("\tTSLA\n") == "TSLA"

    def test_allows_dots(self) -> None:
        """Allows dots for symbols like BRK.B."""
        assert validate_and_normalize_symbol("BRK.B") == "BRK.B"
        assert validate_and_normalize_symbol("brk.a") == "BRK.A"

    def test_allows_hyphens(self) -> None:
        """Allows hyphens for special symbols."""
        assert validate_and_normalize_symbol("SPY-W") == "SPY-W"

    def test_allows_digits(self) -> None:
        """Allows digits in symbols."""
        assert validate_and_normalize_symbol("3M") == "3M"

    def test_max_length_10(self) -> None:
        """Accepts symbols up to 10 characters."""
        assert validate_and_normalize_symbol("ABCDEFGHIJ") == "ABCDEFGHIJ"

    def test_rejects_too_long(self) -> None:
        """Rejects symbols longer than 10 characters."""
        with pytest.raises(ValueError, match="Invalid symbol format"):
            validate_and_normalize_symbol("ABCDEFGHIJK")  # 11 chars

    def test_rejects_empty(self) -> None:
        """Rejects empty symbols."""
        with pytest.raises(ValueError, match="Symbol cannot be empty"):
            validate_and_normalize_symbol("")

    def test_rejects_whitespace_only(self) -> None:
        """Rejects whitespace-only symbols."""
        with pytest.raises(ValueError, match="Symbol cannot be empty"):
            validate_and_normalize_symbol("   ")

    def test_rejects_special_characters(self) -> None:
        """Rejects special characters that could be used in attacks."""
        with pytest.raises(ValueError, match="Invalid symbol format"):
            validate_and_normalize_symbol("AAPL*")
        with pytest.raises(ValueError, match="Invalid symbol format"):
            validate_and_normalize_symbol("AAPL:SELL")
        with pytest.raises(ValueError, match="Invalid symbol format"):
            validate_and_normalize_symbol("SPY/QQQ")

    def test_rejects_newlines(self) -> None:
        """Rejects newlines that could be used in injection attacks."""
        with pytest.raises(ValueError, match="Invalid symbol format"):
            validate_and_normalize_symbol("AAPL\nMSFT")

    def test_rejects_redis_pattern_chars(self) -> None:
        """Rejects characters used in Redis pattern matching."""
        with pytest.raises(ValueError, match="Invalid symbol format"):
            validate_and_normalize_symbol("*")
        with pytest.raises(ValueError, match="Invalid symbol format"):
            validate_and_normalize_symbol("?")
        with pytest.raises(ValueError, match="Invalid symbol format"):
            validate_and_normalize_symbol("[")

    def test_rejects_leading_delimiter(self) -> None:
        """Rejects symbols with leading dots or hyphens."""
        with pytest.raises(ValueError, match="Invalid symbol format"):
            validate_and_normalize_symbol(".AAPL")
        with pytest.raises(ValueError, match="Invalid symbol format"):
            validate_and_normalize_symbol("-SPY")

    def test_rejects_trailing_delimiter(self) -> None:
        """Rejects symbols with trailing dots or hyphens."""
        with pytest.raises(ValueError, match="Invalid symbol format"):
            validate_and_normalize_symbol("AAPL.")
        with pytest.raises(ValueError, match="Invalid symbol format"):
            validate_and_normalize_symbol("SPY-")

    def test_rejects_consecutive_delimiters(self) -> None:
        """Rejects symbols with consecutive delimiters."""
        with pytest.raises(ValueError, match="Invalid symbol format"):
            validate_and_normalize_symbol("BRK..B")
        with pytest.raises(ValueError, match="Invalid symbol format"):
            validate_and_normalize_symbol("A--B")


class TestValidSymbolPattern:
    """Tests for VALID_SYMBOL_PATTERN regex.

    Note: Length validation is done separately by _check_symbol_length().
    The regex validates format (alphanumeric with proper delimiter placement).
    """

    def test_matches_valid_symbols(self) -> None:
        """Pattern matches valid symbol formats."""
        assert VALID_SYMBOL_PATTERN.match("AAPL")
        assert VALID_SYMBOL_PATTERN.match("BRK.B")
        assert VALID_SYMBOL_PATTERN.match("SPY-W")
        assert VALID_SYMBOL_PATTERN.match("3M")

    def test_rejects_invalid_format(self) -> None:
        """Pattern rejects invalid symbol formats."""
        assert not VALID_SYMBOL_PATTERN.match("")  # empty
        assert not VALID_SYMBOL_PATTERN.match("aapl")  # lowercase
        assert not VALID_SYMBOL_PATTERN.match("A*B")  # special char
        assert not VALID_SYMBOL_PATTERN.match(".AAPL")  # leading delimiter
        assert not VALID_SYMBOL_PATTERN.match("AAPL.")  # trailing delimiter
        assert not VALID_SYMBOL_PATTERN.match("BRK..B")  # consecutive delimiters
