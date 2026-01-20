"""Tests for formatters utilities."""

from __future__ import annotations

from datetime import datetime

import pytest

from apps.web_console_ng.utils import formatters


def test_parse_date_for_sort_date_only() -> None:
    parsed = formatters.parse_date_for_sort("2024-01-05")
    assert parsed == datetime(2024, 1, 5)


def test_parse_date_for_sort_datetime_with_tz() -> None:
    parsed = formatters.parse_date_for_sort("2024-01-05T10:00:00+02:00")
    assert parsed == datetime(2024, 1, 5, 8, 0, 0)


def test_parse_date_for_sort_invalid_returns_min() -> None:
    parsed = formatters.parse_date_for_sort("not-a-date")
    assert parsed == datetime.min


def test_safe_float_accepts_numeric_strings() -> None:
    assert formatters.safe_float("12.5") == pytest.approx(12.5)


def test_safe_float_rejects_nan_inf() -> None:
    assert formatters.safe_float(float("nan")) is None
    assert formatters.safe_float(float("inf")) is None
    assert formatters.safe_float(float("-inf")) is None
    assert formatters.safe_float(float("nan"), default=0.0) == 0.0
