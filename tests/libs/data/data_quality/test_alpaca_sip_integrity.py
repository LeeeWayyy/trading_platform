"""Tests for Alpaca SIP deterministic re-pull integrity checks."""

from __future__ import annotations

import datetime
from typing import Any

import pytest

from libs.data.data_quality import alpaca_sip_integrity as module
from libs.data.data_quality.alpaca_sip_integrity import (
    AlpacaSIPIntegrityChecker,
    compare_sip_integrity_responses,
)


def _ts(day: int = 24) -> datetime.datetime:
    return datetime.datetime(2024, 4, day, tzinfo=datetime.UTC)


def _bar(
    *,
    symbol: str = "AAPL",
    timestamp: datetime.datetime | None = None,
    close: float = 100.0,
    volume: float = 1000.0,
) -> dict[str, object]:
    return {
        "symbol": symbol,
        "timestamp": timestamp or _ts(),
        "open": close - 1.0,
        "high": close + 1.0,
        "low": close - 2.0,
        "close": close,
        "volume": volume,
    }


def test_compare_sip_integrity_responses_passes_for_identical_pulls() -> None:
    response = {"AAPL": [_bar()], "MSFT": [_bar(symbol="MSFT", close=200.0)]}

    report = compare_sip_integrity_responses(
        first_response=response,
        second_response=response,
        symbols=["aapl", "MSFT", "AAPL"],
        start=_ts(),
        end=_ts(),
        timeframe="1Day",
        adjustment_mode="all",
    )

    assert report.status == "passed"
    assert report.first_row_count == 2
    assert report.second_row_count == 2
    assert report.matched_row_count == 2
    assert report.mismatch_count == 0
    assert report.first_aggregate_hash == report.second_aggregate_hash
    assert len(report.content_hash) == 64


def test_compare_sip_integrity_responses_fails_when_row_hash_changes() -> None:
    first = {"AAPL": [_bar(close=100.0)]}
    second = {"AAPL": [_bar(close=101.0)]}

    report = compare_sip_integrity_responses(
        first_response=first,
        second_response=second,
        symbols=["AAPL"],
        start=_ts(),
        end=_ts(),
        timeframe="1Day",
        adjustment_mode="all",
    )

    assert report.status == "failed"
    assert report.mismatch_count == 1
    assert report.mismatches[0].reason == "hash_changed"


def test_compare_sip_integrity_responses_fails_when_bar_missing() -> None:
    first = {"AAPL": [_bar(timestamp=_ts(24)), _bar(timestamp=_ts(25))]}
    second = {"AAPL": [_bar(timestamp=_ts(24))]}

    report = compare_sip_integrity_responses(
        first_response=first,
        second_response=second,
        symbols=["AAPL"],
        start=_ts(24),
        end=_ts(25),
        timeframe="1Day",
        adjustment_mode="all",
    )

    assert report.status == "failed"
    assert report.mismatch_count == 1
    assert report.mismatches[0].reason == "missing_second_pull"


def test_compare_sip_integrity_responses_warns_for_duplicate_identical_rows() -> None:
    response = {"AAPL": [_bar(), _bar()]}

    report = compare_sip_integrity_responses(
        first_response=response,
        second_response=response,
        symbols=["AAPL"],
        start=_ts(),
        end=_ts(),
        timeframe="1Day",
        adjustment_mode="all",
    )

    assert report.status == "warning"
    assert report.duplicate_count == 2
    assert report.mismatch_count == 0


def test_checker_fetches_same_sip_window_twice(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "ALPACA_AVAILABLE", True)
    monkeypatch.setattr(module, "Adjustment", lambda value: value)
    monkeypatch.setattr(module, "DataFeed", lambda value: value)
    monkeypatch.setattr(module, "Sort", type("SortDouble", (), {"ASC": "asc"}))

    class FakeTimeFrame:
        Day = "day"

        def __init__(self, amount: int, unit: str) -> None:
            self.amount = amount
            self.unit = unit

    monkeypatch.setattr(module, "TimeFrame", FakeTimeFrame)
    monkeypatch.setattr(
        module, "TimeFrameUnit", type("UnitDouble", (), {"Minute": "minute", "Hour": "hour"})
    )

    captured_requests: list[dict[str, object]] = []

    def fake_stock_bars_request(**kwargs: object) -> dict[str, object]:
        captured_requests.append(kwargs)
        return kwargs

    monkeypatch.setattr(module, "StockBarsRequest", fake_stock_bars_request)

    class FakeClient:
        def get_stock_bars(self, request_params: Any) -> dict[str, list[dict[str, object]]]:
            return {"AAPL": [_bar()]}

    report = AlpacaSIPIntegrityChecker(client=FakeClient()).run(
        symbols=["aapl"],
        start=_ts(),
        end=_ts(),
    )

    assert report.status == "passed"
    assert len(captured_requests) == 2
    assert captured_requests[0]["feed"] == "sip"
    assert captured_requests[1]["symbol_or_symbols"] == ["AAPL"]
