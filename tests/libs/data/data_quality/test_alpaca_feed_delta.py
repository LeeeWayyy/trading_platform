"""Tests for Alpaca IEX-vs-SIP feed delta monitoring."""

from __future__ import annotations

import datetime
from types import SimpleNamespace
from typing import Any

import pytest

from libs.data.data_quality import alpaca_feed_delta as module
from libs.data.data_quality.alpaca_feed_delta import (
    AlpacaFeedDeltaComparator,
    AlpacaFeedDeltaTolerances,
    compare_feed_bar_responses,
    normalize_feed_bars_response,
)


def _ts(hour: int = 15, minute: int = 0) -> datetime.datetime:
    return datetime.datetime(2026, 4, 20, hour, minute, tzinfo=datetime.UTC)


def _bar(
    *,
    symbol: str = "AAPL",
    timestamp: datetime.datetime | None = None,
    open_price: float = 100.0,
    high: float = 101.0,
    low: float = 99.0,
    close: float = 100.5,
    volume: float = 10_000.0,
) -> dict[str, object]:
    return {
        "symbol": symbol,
        "timestamp": timestamp or _ts(),
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


def _report(
    left: dict[str, list[dict[str, object]]],
    right: dict[str, list[dict[str, object]]],
) -> module.FeedDeltaReport:
    return compare_feed_bar_responses(
        left_response=left,
        right_response=right,
        symbols=["AAPL"],
        start=_ts(14),
        end=_ts(21),
        timeframe="5Min",
        adjustment_mode="all",
    )


def test_compare_feed_bar_responses_passes_for_aligned_expected_iex_volume_ratio() -> None:
    left = {"AAPL": [_bar(volume=1_000.0)]}
    right = {"AAPL": [_bar(open_price=100.01, close=100.49, volume=10_000.0)]}

    report = _report(left, right)

    assert report.status == "passed"
    assert report.issue_counts["price_delta"] == 0
    assert report.issue_counts["volume_ratio"] == 0
    assert report.summary["matched_bar_count"] == 1
    assert len(report.content_hash) == 64


def test_compare_feed_bar_responses_reports_timestamp_alignment_without_equality() -> None:
    left = {"AAPL": [_bar(timestamp=_ts(15, 5))]}
    right = {"AAPL": [_bar(timestamp=_ts(15, 0))]}

    report = _report(left, right)

    assert report.status == "failed"
    assert report.issue_counts["timestamp_alignment"] == 2
    assert report.issue_counts["coverage_gap"] == 0
    assert report.summary["matched_bar_count"] == 0


def test_compare_feed_bar_responses_flags_ohlc_sanity_as_failed() -> None:
    left = {"AAPL": [_bar(high=99.5, close=100.5)]}
    right = {"AAPL": [_bar()]}

    report = _report(left, right)

    assert report.status == "failed"
    assert report.issue_counts["ohlc_sanity"] == 1
    assert report.issues[0].severity == "error"


def test_compare_feed_bar_responses_flags_price_and_liquid_volume_outliers() -> None:
    left = {"AAPL": [_bar(close=110.0, high=111.0, volume=900_000.0)]}
    right = {"AAPL": [_bar(close=100.0, high=101.0, volume=1_000_000.0)]}

    report = _report(left, right)

    assert report.status == "warning"
    assert report.issue_counts["price_delta"] == 1
    assert report.issue_counts["volume_ratio"] == 1
    assert report.symbol_summaries[0].liquidity_bucket == "liquid"


def test_normalize_feed_bars_response_accepts_sdk_data_shape_and_short_fields() -> None:
    response = SimpleNamespace(
        data={
            "AAPL": [
                {
                    "S": "aapl",
                    "t": "2026-04-20T15:00:00Z",
                    "o": 100.0,
                    "h": 101.0,
                    "l": 99.0,
                    "c": 100.5,
                    "v": 1234,
                }
            ]
        }
    )

    bars = normalize_feed_bars_response(response)

    assert len(bars) == 1
    assert bars[0].symbol == "AAPL"
    assert bars[0].timestamp == _ts()
    assert bars[0].volume == 1234.0


def test_report_hash_is_deterministic_and_excludes_self_hash() -> None:
    left = {"AAPL": [_bar(volume=1_000.0)]}
    right = {"AAPL": [_bar(volume=10_000.0)]}

    report = _report(left, right)
    first_payload = report.to_dict()
    second_payload = report.to_dict()

    assert first_payload["content_hash"] == second_payload["content_hash"]
    assert first_payload["content_hash"] not in str(report.to_dict(include_hash=False))


def test_comparator_fetches_both_feeds_with_feed_specific_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(module, "ALPACA_AVAILABLE", True)
    monkeypatch.setattr(module, "Adjustment", lambda value: value)
    monkeypatch.setattr(module, "DataFeed", lambda value: value)
    monkeypatch.setattr(module, "Sort", SimpleNamespace(ASC="asc"))

    class FakeTimeFrame:
        Day = "day"

        def __init__(self, amount: int, unit: str) -> None:
            self.amount = amount
            self.unit = unit

    monkeypatch.setattr(module, "TimeFrame", FakeTimeFrame)
    monkeypatch.setattr(module, "TimeFrameUnit", SimpleNamespace(Minute="minute", Hour="hour"))

    captured_requests: list[dict[str, object]] = []

    def fake_stock_bars_request(**kwargs: object) -> dict[str, object]:
        captured_requests.append(kwargs)
        return kwargs

    monkeypatch.setattr(module, "StockBarsRequest", fake_stock_bars_request)

    class FakeClient:
        def get_stock_bars(self, request_params: Any) -> dict[str, list[dict[str, object]]]:
            request = dict(request_params)
            if request["feed"] == "iex":
                return {"AAPL": [_bar(volume=1_000.0)]}
            return {"AAPL": [_bar(volume=10_000.0)]}

    comparator = AlpacaFeedDeltaComparator(client=FakeClient())

    report = comparator.compare(
        symbols=["aapl"],
        start=_ts(14),
        end=_ts(21),
        timeframe="5Min",
    )

    assert report.status == "passed"
    assert [request["feed"] for request in captured_requests] == ["iex", "sip"]
    assert captured_requests[0]["symbol_or_symbols"] == ["AAPL"]


def test_custom_tolerance_version_is_recorded_in_report() -> None:
    tolerances = AlpacaFeedDeltaTolerances(version="custom-v1")

    report = compare_feed_bar_responses(
        left_response={"AAPL": [_bar(volume=1_000.0)]},
        right_response={"AAPL": [_bar(volume=10_000.0)]},
        symbols=["AAPL"],
        start=_ts(14),
        end=_ts(21),
        timeframe="5Min",
        adjustment_mode="all",
        tolerances=tolerances,
    )

    assert report.to_dict()["tolerances"]["version"] == "custom-v1"
