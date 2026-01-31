"""Tests for order utility functions.

Tests cover:
- is_cancellable_order_id() validation
- validate_symbol() validation and normalization
"""

from __future__ import annotations

from apps.web_console_ng.utils.orders import (
    FALLBACK_ID_PREFIX,
    SYNTHETIC_ID_PREFIX,
    UNCANCELLABLE_PREFIXES,
    is_cancellable_order_id,
    validate_symbol,
)


class TestIsCancellableOrderId:
    """Tests for is_cancellable_order_id function."""

    def test_valid_order_id_returns_true(self) -> None:
        assert is_cancellable_order_id("abc123") is True

    def test_valid_uuid_returns_true(self) -> None:
        assert is_cancellable_order_id("550e8400-e29b-41d4-a716-446655440000") is True

    def test_synthetic_prefix_returns_false(self) -> None:
        assert is_cancellable_order_id(f"{SYNTHETIC_ID_PREFIX}123") is False

    def test_fallback_prefix_returns_false(self) -> None:
        assert is_cancellable_order_id(f"{FALLBACK_ID_PREFIX}456") is False

    def test_none_returns_false(self) -> None:
        assert is_cancellable_order_id(None) is False

    def test_empty_string_returns_false(self) -> None:
        assert is_cancellable_order_id("") is False

    def test_integer_returns_false(self) -> None:
        assert is_cancellable_order_id(12345) is False

    def test_list_returns_false(self) -> None:
        assert is_cancellable_order_id(["abc"]) is False

    def test_dict_returns_false(self) -> None:
        assert is_cancellable_order_id({"id": "abc"}) is False


class TestValidateSymbol:
    """Tests for validate_symbol function."""

    def test_valid_symbol_returns_normalized(self) -> None:
        symbol, error = validate_symbol("AAPL")
        assert symbol == "AAPL"
        assert error == ""

    def test_lowercase_symbol_normalized_to_uppercase(self) -> None:
        symbol, error = validate_symbol("aapl")
        assert symbol == "AAPL"
        assert error == ""

    def test_symbol_with_dot_valid(self) -> None:
        """BRK.B style symbols are valid."""
        symbol, error = validate_symbol("BRK.B")
        assert symbol == "BRK.B"
        assert error == ""

    def test_symbol_with_hyphen_valid(self) -> None:
        """Symbols with hyphens are valid."""
        symbol, error = validate_symbol("BF-B")
        assert symbol == "BF-B"
        assert error == ""

    def test_path_traversal_blocked(self) -> None:
        """Path traversal attempts should be blocked."""
        symbol, error = validate_symbol("../admin")
        assert symbol is None
        assert "Invalid symbol format" in error

    def test_double_dot_blocked(self) -> None:
        symbol, error = validate_symbol("..")
        assert symbol is None
        assert "Invalid symbol format" in error

    def test_slash_blocked(self) -> None:
        symbol, error = validate_symbol("AAPL/admin")
        assert symbol is None
        assert "Invalid symbol format" in error

    def test_backslash_blocked(self) -> None:
        symbol, error = validate_symbol("AAPL\\admin")
        assert symbol is None
        assert "Invalid symbol format" in error

    def test_none_returns_error(self) -> None:
        symbol, error = validate_symbol(None)
        assert symbol is None
        assert "non-empty string" in error

    def test_empty_string_returns_error(self) -> None:
        symbol, error = validate_symbol("")
        assert symbol is None
        assert "non-empty string" in error

    def test_integer_returns_error(self) -> None:
        symbol, error = validate_symbol(12345)
        assert symbol is None
        assert "non-empty string" in error

    def test_whitespace_stripped(self) -> None:
        symbol, error = validate_symbol("  AAPL  ")
        assert symbol == "AAPL"
        assert error == ""

    def test_too_long_symbol_blocked(self) -> None:
        """Symbols longer than 15 chars should be blocked."""
        symbol, error = validate_symbol("A" * 20)
        assert symbol is None
        assert "Invalid symbol format" in error

    def test_special_chars_blocked(self) -> None:
        symbol, error = validate_symbol("AAPL!@#")
        assert symbol is None
        assert "Invalid symbol format" in error


class TestConstants:
    """Tests for module constants."""

    def test_uncancellable_prefixes_contains_both(self) -> None:
        assert SYNTHETIC_ID_PREFIX in UNCANCELLABLE_PREFIXES
        assert FALLBACK_ID_PREFIX in UNCANCELLABLE_PREFIXES

    def test_uncancellable_prefixes_is_tuple(self) -> None:
        assert isinstance(UNCANCELLABLE_PREFIXES, tuple)
