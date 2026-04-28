"""Tests for MarketDataProvider helper logic."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from libs.data.market_data.provider import MarketDataProvider


def test_compute_bars_window_5min_uses_sufficient_lookback() -> None:
    start, end = MarketDataProvider._compute_bars_window("5Min", 240)
    assert end > start
    # 240 x 5-minute bars should request a multi-day window with holiday/weekend buffer.
    assert (end - start).days >= 12


def test_compute_bars_window_daily_scales_for_large_limit() -> None:
    start, end = MarketDataProvider._compute_bars_window("1Day", 120)
    assert end > start
    # 120 daily bars with 3x buffer should cover nearly a year.
    assert (end - start).days >= 360


def test_compute_bars_window_enforces_minimum_window() -> None:
    start, end = MarketDataProvider._compute_bars_window("1Min", 1)
    assert end > start
    # Small requests still need enough lookback to avoid empty payloads around market closures.
    assert (end - start).days >= 6


def test_get_bars_sync_preserves_value_error_for_bad_timeframe() -> None:
    provider = MarketDataProvider.__new__(MarketDataProvider)
    provider._data_feed = None
    provider._client = MagicMock()

    with pytest.raises(ValueError, match="Unsupported timeframe"):
        provider._get_bars_sync("AAPL", "2Min", 10)


def test_extract_bar_list_reads_symbol_from_data_dict() -> None:
    bars = SimpleNamespace(data={"AAPL": [1, 2], "MSFT": [3]})
    extracted = MarketDataProvider._extract_bar_list(bars, "AAPL")
    assert extracted == [1, 2]


def test_get_bars_sync_requests_latest_window_with_desc_sort(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = MarketDataProvider.__new__(MarketDataProvider)
    provider._data_feed = "iex"

    captured_kwargs: dict[str, object] = {}

    def fake_stock_bars_request(**kwargs: object) -> dict[str, object]:
        captured_kwargs.update(kwargs)
        return kwargs

    class FakeClient:
        def get_stock_bars(self, request: object) -> dict[str, list[dict[str, object]]]:
            return {
                "AAPL": [
                    {
                        "timestamp": "2026-04-20T15:00:00+00:00",
                        "open": 1.0,
                        "high": 2.0,
                        "low": 0.5,
                        "close": 1.5,
                        "volume": 10,
                    }
                ]
            }

    provider._client = FakeClient()
    monkeypatch.setattr(
        "libs.data.market_data.provider.StockBarsRequest",
        fake_stock_bars_request,
    )

    bars = provider._get_bars_sync("AAPL", "5Min", 10)

    assert captured_kwargs["sort"] == "desc"
    assert bars[0]["timestamp"] == "2026-04-20T15:00:00+00:00"


def test_get_latest_quote_sync_normalizes_top_of_book(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = MarketDataProvider.__new__(MarketDataProvider)
    provider._data_feed = "iex"

    captured_kwargs: dict[str, object] = {}

    def fake_latest_quote_request(**kwargs: object) -> dict[str, object]:
        captured_kwargs.update(kwargs)
        return kwargs

    class FakeClient:
        def get_stock_latest_quote(self, request: object) -> dict[str, SimpleNamespace]:
            return {
                "AAPL": SimpleNamespace(
                    bp=180.1,
                    ap=180.2,
                    bs=100,
                    **{"as": 200},
                    timestamp="2026-04-20T15:00:00+00:00",
                )
            }

    provider._client = FakeClient()
    monkeypatch.setattr(
        "libs.data.market_data.provider.StockLatestQuoteRequest",
        fake_latest_quote_request,
    )

    quote = provider._get_latest_quote_sync("aapl")

    assert captured_kwargs["symbol_or_symbols"] == "AAPL"
    assert captured_kwargs["feed"] == "iex"
    assert quote == {
        "symbol": "AAPL",
        "bid_price": 180.1,
        "ask_price": 180.2,
        "bid_size": 100,
        "ask_size": 200,
        "timestamp": "2026-04-20T15:00:00+00:00",
    }


def test_get_bars_sync_sorts_results_chronologically(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = MarketDataProvider.__new__(MarketDataProvider)
    provider._data_feed = None

    def fake_stock_bars_request(**kwargs: object) -> dict[str, object]:
        return kwargs

    class FakeClient:
        def get_stock_bars(self, request: object) -> dict[str, list[dict[str, object]]]:
            return {
                "AAPL": [
                    {
                        "timestamp": "2026-04-20T15:05:00+00:00",
                        "open": 1.2,
                        "high": 1.3,
                        "low": 1.1,
                        "close": 1.25,
                        "volume": 8,
                    },
                    {
                        "timestamp": "2026-04-20T15:00:00+00:00",
                        "open": 1.0,
                        "high": 1.2,
                        "low": 0.9,
                        "close": 1.1,
                        "volume": 10,
                    },
                ]
            }

    provider._client = FakeClient()
    monkeypatch.setattr(
        "libs.data.market_data.provider.StockBarsRequest",
        fake_stock_bars_request,
    )

    bars = provider._get_bars_sync("AAPL", "5Min", 10)

    assert [bar["timestamp"] for bar in bars] == [
        "2026-04-20T15:00:00+00:00",
        "2026-04-20T15:05:00+00:00",
    ]


def test_normalize_bar_rejects_non_positive_ohlc_values() -> None:
    provider = MarketDataProvider.__new__(MarketDataProvider)
    bar = {
        "timestamp": "2026-04-20T15:00:00+00:00",
        "open": 100.0,
        "high": 100.5,
        "low": 0.0,
        "close": 100.2,
        "volume": 12,
    }

    assert provider._normalize_bar(bar) is None
