"""Tests for libs.platform.security.sanitization module.

Verifies formula injection protection for export functionality.
"""

from __future__ import annotations

from libs.platform.security.sanitization import sanitize_for_export


class TestSanitizeForExport:
    """Tests for sanitize_for_export function."""

    def test_regular_string_passes_through(self) -> None:
        """Regular strings pass through unchanged."""
        assert sanitize_for_export("hello") == "hello"
        assert sanitize_for_export("AAPL") == "AAPL"
        assert sanitize_for_export("order-123") == "order-123"

    def test_numbers_pass_through(self) -> None:
        """Numbers pass through unchanged."""
        assert sanitize_for_export(123) == 123
        assert sanitize_for_export(123.45) == 123.45
        assert sanitize_for_export(-50) == -50

    def test_none_passes_through(self) -> None:
        """None values pass through unchanged."""
        assert sanitize_for_export(None) is None

    def test_boolean_passes_through(self) -> None:
        """Boolean values pass through unchanged."""
        assert sanitize_for_export(True) is True
        assert sanitize_for_export(False) is False

    def test_equals_formula_sanitized(self) -> None:
        """Strings starting with = are prefixed with quote."""
        assert sanitize_for_export("=IMPORTDATA(url)") == "'=IMPORTDATA(url)"
        assert sanitize_for_export("=SUM(A1:A10)") == "'=SUM(A1:A10)"

    def test_plus_formula_sanitized(self) -> None:
        """Strings starting with + are prefixed with quote."""
        assert sanitize_for_export("+1+1") == "'+1+1"

    def test_at_formula_sanitized(self) -> None:
        """Strings starting with @ are prefixed with quote."""
        assert sanitize_for_export("@SUM(A1)") == "'@SUM(A1)"

    def test_tab_formula_sanitized(self) -> None:
        """Strings starting with tab are prefixed with quote."""
        assert sanitize_for_export("\t=FORMULA") == "'\t=FORMULA"

    def test_negative_numeric_passes(self) -> None:
        """Strictly numeric negative values pass through."""
        assert sanitize_for_export("-123") == "-123"
        assert sanitize_for_export("-123.45") == "-123.45"

    def test_scientific_notation_passes(self) -> None:
        """Scientific notation passes through (critical security fix)."""
        assert sanitize_for_export("-1.2E-5") == "-1.2E-5"
        assert sanitize_for_export("-1E+10") == "-1E+10"
        assert sanitize_for_export("-1e5") == "-1e5"
        assert sanitize_for_export("1.5E10") == "1.5E10"

    def test_negative_non_numeric_sanitized(self) -> None:
        """Non-numeric negative values are sanitized."""
        assert sanitize_for_export("-1+1") == "'-1+1"
        assert sanitize_for_export("-A1") == "'-A1"

    def test_whitespace_prefix_bypass_blocked(self) -> None:
        """Leading whitespace doesn't bypass formula detection."""
        assert sanitize_for_export(" =FORMULA") == "' =FORMULA"
        assert sanitize_for_export("  =FORMULA") == "'  =FORMULA"

    def test_empty_string_passes(self) -> None:
        """Empty string passes through unchanged."""
        assert sanitize_for_export("") == ""

    def test_all_whitespace_passes(self) -> None:
        """All-whitespace strings pass through unchanged."""
        assert sanitize_for_export("   ") == "   "
