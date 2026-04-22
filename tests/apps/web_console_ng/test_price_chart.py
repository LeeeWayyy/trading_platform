"""Tests for PriceChartComponent."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.web_console_ng.components.price_chart import (
    MAX_FUTURE_TICK_SKEW_S,
    MAX_PAST_TICK_SKEW_S,
    REALTIME_STALE_THRESHOLD_S,
    CandleData,
    ExecutionMarker,
    PriceChartComponent,
    _ensure_utc_datetime,
)


class TestCandleData:
    """Tests for CandleData dataclass."""

    def test_candle_data_creation(self) -> None:
        """CandleData stores all fields correctly."""
        candle = CandleData(
            time=1704067200,
            open=100.0,
            high=105.0,
            low=99.0,
            close=103.0,
            volume=10000,
        )

        assert candle.time == 1704067200
        assert candle.open == 100.0
        assert candle.high == 105.0
        assert candle.low == 99.0
        assert candle.close == 103.0
        assert candle.volume == 10000

    def test_candle_data_optional_volume(self) -> None:
        """CandleData volume is optional."""
        candle = CandleData(
            time=1704067200,
            open=100.0,
            high=105.0,
            low=99.0,
            close=103.0,
        )

        assert candle.volume is None


class TestExecutionMarker:
    """Tests for ExecutionMarker dataclass."""

    def test_execution_marker_buy(self) -> None:
        """ExecutionMarker stores buy order correctly."""
        marker = ExecutionMarker(
            time=1704067200,
            price=100.0,
            side="buy",
            quantity=100,
            order_id="order123",
        )

        assert marker.time == 1704067200
        assert marker.price == 100.0
        assert marker.side == "buy"
        assert marker.quantity == 100
        assert marker.order_id == "order123"

    def test_execution_marker_sell(self) -> None:
        """ExecutionMarker stores sell order correctly."""
        marker = ExecutionMarker(
            time=1704067200,
            price=105.0,
            side="sell",
            quantity=50,
            order_id="order456",
        )

        assert marker.side == "sell"
        assert marker.quantity == 50


class TestPriceChartTimeNormalization:
    """Tests for internal datetime normalization helpers."""

    def test_ensure_utc_datetime_assumes_naive_utc(self) -> None:
        """Naive datetimes are treated as UTC for candle bucketing."""
        naive = datetime(2026, 4, 22, 13, 5, 0)
        normalized = _ensure_utc_datetime(naive)

        assert normalized.tzinfo == UTC
        assert normalized.hour == 13
        assert normalized.minute == 5

    def test_ensure_utc_datetime_converts_aware_offsets(self) -> None:
        """Offset-aware datetimes are converted to equivalent UTC instants."""
        offset_dt = datetime.fromisoformat("2026-04-22T13:05:00+02:00")
        normalized = _ensure_utc_datetime(offset_dt)

        assert normalized.tzinfo == UTC
        assert normalized.hour == 11
        assert normalized.minute == 5


class TestPriceChartInit:
    """Tests for PriceChartComponent initialization."""

    def test_initial_state(self) -> None:
        """Component initializes with empty state."""
        client = MagicMock()

        comp = PriceChartComponent(trading_client=client)

        assert comp._current_symbol is None
        assert comp._candles == []
        assert comp._markers == []
        assert comp._disposed is False
        assert comp._last_realtime_update is None
        assert comp._chart_init_lock is None
        assert comp._price_update_lock is None
        assert comp._price_update_task is None
        assert comp._pending_price_update is None

    def test_unique_ids_generated(self) -> None:
        """Each component gets unique chart and container IDs."""
        client = MagicMock()

        comp1 = PriceChartComponent(trading_client=client)
        comp2 = PriceChartComponent(trading_client=client)

        assert comp1._chart_id != comp2._chart_id
        assert comp1._container_id != comp2._container_id


class TestPriceChartPriceData:
    """Tests for price data handling."""

    @pytest.fixture()
    def component(self) -> PriceChartComponent:
        """Create component for testing."""
        client = MagicMock()
        return PriceChartComponent(trading_client=client)

    def test_set_price_data_rejects_non_dict(self, component: PriceChartComponent) -> None:
        """set_price_data rejects non-dict input."""
        component._current_symbol = "AAPL"

        component.set_price_data("invalid")  # type: ignore[arg-type]

        # No error raised, silently ignored
        assert component._last_realtime_update is None

    def test_set_price_data_ignores_wrong_symbol(self, component: PriceChartComponent) -> None:
        """set_price_data ignores data for different symbol."""
        component._current_symbol = "AAPL"

        component.set_price_data(
            {
                "symbol": "GOOGL",  # Different symbol
                "price": "100.00",
                "timestamp": "2024-01-01T12:00:00Z",
            }
        )

        # Should not update
        assert component._last_realtime_update is None

    def test_set_price_data_rejects_missing_price(self, component: PriceChartComponent) -> None:
        """set_price_data rejects data without price."""
        component._current_symbol = "AAPL"

        component.set_price_data(
            {
                "symbol": "AAPL",
                "timestamp": "2024-01-01T12:00:00Z",
                # No price field
            }
        )

        assert component._last_realtime_update is None

    def test_set_price_data_rejects_invalid_price(self, component: PriceChartComponent) -> None:
        """set_price_data rejects invalid price values."""
        component._current_symbol = "AAPL"

        component.set_price_data(
            {
                "symbol": "AAPL",
                "price": "not-a-number",
                "timestamp": "2024-01-01T12:00:00Z",
            }
        )

        assert component._last_realtime_update is None

    def test_set_price_data_rejects_nan(self, component: PriceChartComponent) -> None:
        """set_price_data rejects NaN price values."""
        component._current_symbol = "AAPL"

        component.set_price_data(
            {
                "symbol": "AAPL",
                "price": float("nan"),
                "timestamp": "2024-01-01T12:00:00Z",
            }
        )

        assert component._last_realtime_update is None

    def test_set_price_data_rejects_infinity(self, component: PriceChartComponent) -> None:
        """set_price_data rejects infinity price values."""
        component._current_symbol = "AAPL"

        component.set_price_data(
            {
                "symbol": "AAPL",
                "price": float("inf"),
                "timestamp": "2024-01-01T12:00:00Z",
            }
        )

        assert component._last_realtime_update is None

    def test_set_price_data_rejects_negative_price(self, component: PriceChartComponent) -> None:
        """set_price_data rejects negative price values."""
        component._current_symbol = "AAPL"

        component.set_price_data(
            {
                "symbol": "AAPL",
                "price": -100.0,
                "timestamp": "2024-01-01T12:00:00Z",
            }
        )

        assert component._last_realtime_update is None

    def test_set_price_data_rejects_zero_price(self, component: PriceChartComponent) -> None:
        """set_price_data rejects zero price values."""
        component._current_symbol = "AAPL"

        component.set_price_data(
            {
                "symbol": "AAPL",
                "price": 0,
                "timestamp": "2024-01-01T12:00:00Z",
            }
        )

        assert component._last_realtime_update is None

    def test_set_price_data_updates_timestamp_on_valid_data(
        self, component: PriceChartComponent
    ) -> None:
        """set_price_data updates timestamp on valid data."""
        component._current_symbol = "AAPL"
        now = datetime.now(UTC)

        with patch.object(component, "_schedule_price_update") as schedule_update:
            component.set_price_data(
                {
                    "symbol": "AAPL",
                    "price": 150.0,
                    "timestamp": now.isoformat(),
                }
            )

        assert component._last_realtime_update is not None
        assert abs((component._last_realtime_update - now).total_seconds()) < 1
        schedule_update.assert_called_once()

    def test_set_price_data_handles_missing_timestamp(self, component: PriceChartComponent) -> None:
        """set_price_data sets timestamp to None when missing (FAIL-CLOSED)."""
        component._current_symbol = "AAPL"

        with patch.object(component, "_schedule_price_update") as schedule_update:
            component.set_price_data(
                {
                    "symbol": "AAPL",
                    "price": 150.0,
                    # No timestamp
                }
            )

        # FAIL-CLOSED: timestamp None when missing
        assert component._last_realtime_update is None
        schedule_update.assert_not_called()

    def test_set_price_data_handles_invalid_timestamp(self, component: PriceChartComponent) -> None:
        """set_price_data sets timestamp to None when invalid (FAIL-CLOSED)."""
        component._current_symbol = "AAPL"

        with patch.object(component, "_schedule_price_update") as schedule_update:
            component.set_price_data(
                {
                    "symbol": "AAPL",
                    "price": 150.0,
                    "timestamp": "not-a-timestamp",
                }
            )

        # FAIL-CLOSED: timestamp None when invalid
        assert component._last_realtime_update is None
        schedule_update.assert_not_called()

    def test_set_price_data_rejects_future_skew_timestamp(
        self, component: PriceChartComponent
    ) -> None:
        """set_price_data drops future-skewed ticks to avoid chart freeze."""
        component._current_symbol = "AAPL"
        future_ts = datetime.now(UTC) + timedelta(seconds=MAX_FUTURE_TICK_SKEW_S + 5)

        with patch.object(component, "_schedule_price_update") as schedule_update:
            component.set_price_data(
                {
                    "symbol": "AAPL",
                    "price": 150.0,
                    "timestamp": future_ts.isoformat(),
                }
            )

        assert component._last_realtime_update is None
        schedule_update.assert_not_called()

    def test_set_price_data_rejects_past_skew_timestamp(self, component: PriceChartComponent) -> None:
        """set_price_data drops stale delayed ticks outside allowed past skew."""
        component._current_symbol = "AAPL"
        stale_ts = datetime.now(UTC) - timedelta(seconds=MAX_PAST_TICK_SKEW_S + 5)

        with patch.object(component, "_schedule_price_update") as schedule_update:
            component.set_price_data(
                {
                    "symbol": "AAPL",
                    "price": 150.0,
                    "timestamp": stale_ts.isoformat(),
                }
            )

        assert component._last_realtime_update is None
        schedule_update.assert_not_called()

    def test_set_price_data_ignored_when_disposed(self, component: PriceChartComponent) -> None:
        """set_price_data does nothing when disposed."""
        component._current_symbol = "AAPL"
        component._disposed = True

        component.set_price_data(
            {
                "symbol": "AAPL",
                "price": 150.0,
                "timestamp": "2024-01-01T12:00:00Z",
            }
        )

        assert component._last_realtime_update is None

    @pytest.mark.asyncio()
    async def test_set_price_data_coalesces_updates_while_inflight(
        self, component: PriceChartComponent
    ) -> None:
        """Only latest pending tick is kept while one update task is running."""
        component._current_symbol = "AAPL"
        started = asyncio.Event()
        release = asyncio.Event()
        handled_prices: list[float] = []

        async def fake_handle(price: float, tick_time: datetime, symbol: str | None = None) -> None:
            handled_prices.append(price)
            if len(handled_prices) == 1:
                started.set()
                await release.wait()

        component._handle_price_update = fake_handle  # type: ignore[method-assign]
        now = datetime.now(UTC)

        component.set_price_data(
            {"symbol": "AAPL", "price": 100.0, "timestamp": now.isoformat()}
        )
        await started.wait()

        component.set_price_data(
            {
                "symbol": "AAPL",
                "price": 101.0,
                "timestamp": (now + timedelta(seconds=1)).isoformat(),
            }
        )
        component.set_price_data(
            {
                "symbol": "AAPL",
                "price": 102.0,
                "timestamp": (now + timedelta(seconds=2)).isoformat(),
            }
        )

        assert component._pending_price_update is not None
        assert component._pending_price_update[0] == 102.0

        release.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        if component._price_update_task is not None:
            await component._price_update_task

        assert handled_prices == [100.0, 102.0]


class TestPriceChartStaleness:
    """Tests for staleness detection."""

    @pytest.fixture()
    def component(self) -> PriceChartComponent:
        """Create component for testing."""
        client = MagicMock()
        return PriceChartComponent(trading_client=client)

    def test_is_data_stale_true_when_no_update(self, component: PriceChartComponent) -> None:
        """Data is stale when no realtime update received."""
        component._last_realtime_update = None

        assert component.is_data_stale() is True

    def test_is_data_stale_false_when_fresh(self, component: PriceChartComponent) -> None:
        """Data is fresh when recently updated."""
        component._last_realtime_update = datetime.now(UTC)

        assert component.is_data_stale() is False

    def test_is_data_stale_true_when_old(self, component: PriceChartComponent) -> None:
        """Data is stale when update is too old."""
        old_time = datetime.now(UTC) - timedelta(seconds=REALTIME_STALE_THRESHOLD_S + 10)
        component._last_realtime_update = old_time

        assert component.is_data_stale() is True


class TestPriceChartVWAPTWAP:
    """Tests for VWAP/TWAP calculations."""

    @pytest.fixture()
    def component(self) -> PriceChartComponent:
        """Create component for testing."""
        client = MagicMock()
        return PriceChartComponent(trading_client=client)

    def test_calculate_vwap_empty_candles(self, component: PriceChartComponent) -> None:
        """VWAP returns empty list for no candles."""
        component._candles = []

        result = component.calculate_vwap_from_candles()

        assert result == []

    def test_calculate_vwap_with_candles(self, component: PriceChartComponent) -> None:
        """VWAP is calculated correctly from candles."""
        component._candles = [
            CandleData(time=1, open=100, high=105, low=95, close=100, volume=1000),
            CandleData(time=2, open=100, high=110, low=98, close=105, volume=2000),
        ]

        result = component.calculate_vwap_from_candles()

        assert len(result) == 2
        # First candle: typical = (105+95+100)/3 = 100, VWAP = 100*1000/1000 = 100
        assert result[0]["time"] == 1
        assert abs(result[0]["vwap"] - 100.0) < 0.01

    def test_calculate_vwap_skips_zero_volume(self, component: PriceChartComponent) -> None:
        """VWAP handles candles with zero volume."""
        component._candles = [
            CandleData(time=1, open=100, high=105, low=95, close=100, volume=0),
            CandleData(time=2, open=100, high=110, low=98, close=105, volume=1000),
        ]

        result = component.calculate_vwap_from_candles()

        # First candle has 0 volume, no VWAP entry
        # Second candle has volume
        assert len(result) == 1
        assert result[0]["time"] == 2

    def test_calculate_twap_empty_candles(self, component: PriceChartComponent) -> None:
        """TWAP returns empty list for no candles."""
        component._candles = []

        result = component.calculate_twap_from_candles()

        assert result == []

    def test_calculate_twap_with_candles(self, component: PriceChartComponent) -> None:
        """TWAP is calculated correctly from candles."""
        component._candles = [
            CandleData(time=1, open=100, high=105, low=95, close=100, volume=1000),
            CandleData(time=2, open=100, high=110, low=98, close=110, volume=2000),
        ]

        result = component.calculate_twap_from_candles()

        assert len(result) == 2
        # First: TWAP = 100/1 = 100
        assert result[0]["time"] == 1
        assert result[0]["twap"] == 100.0
        # Second: TWAP = (100+110)/2 = 105
        assert result[1]["time"] == 2
        assert result[1]["twap"] == 105.0


class TestPriceChartGetters:
    """Tests for getter methods."""

    @pytest.fixture()
    def component(self) -> PriceChartComponent:
        """Create component for testing."""
        client = MagicMock()
        return PriceChartComponent(trading_client=client)

    def test_get_current_price_returns_last_close(self, component: PriceChartComponent) -> None:
        """get_current_price returns last candle's close price."""
        component._candles = [
            CandleData(time=1, open=100, high=105, low=95, close=100, volume=1000),
            CandleData(time=2, open=100, high=110, low=98, close=108, volume=2000),
        ]

        assert component.get_current_price() == 108

    def test_get_current_price_returns_none_when_empty(
        self, component: PriceChartComponent
    ) -> None:
        """get_current_price returns None when no candles."""
        component._candles = []

        assert component.get_current_price() is None


class TestPriceChartSymbolChange:
    """Tests for symbol change handling."""

    @pytest.fixture()
    def component(self) -> PriceChartComponent:
        """Create component for testing."""
        client = MagicMock()
        return PriceChartComponent(trading_client=client)

    @pytest.mark.asyncio()
    async def test_symbol_change_resets_timestamp(self, component: PriceChartComponent) -> None:
        """Symbol change resets realtime update timestamp."""
        component._last_realtime_update = datetime.now(UTC)

        with patch.object(component, "_fetch_candle_data", return_value=[]):
            with patch.object(component, "_fetch_execution_markers", return_value=[]):
                with patch.object(component, "_update_chart_data"):
                    await component.on_symbol_changed("AAPL")

        # Timestamp reset on symbol change
        assert component._last_realtime_update is None
        assert component._current_symbol == "AAPL"

    @pytest.mark.asyncio()
    async def test_symbol_change_sets_symbol_changed_at(
        self, component: PriceChartComponent
    ) -> None:
        """Symbol change sets _symbol_changed_at for dead feed detection."""
        assert component._symbol_changed_at is None

        with patch.object(component, "_fetch_candle_data", return_value=[]):
            with patch.object(component, "_fetch_execution_markers", return_value=[]):
                with patch.object(component, "_update_chart_data"):
                    await component.on_symbol_changed("AAPL")

        # _symbol_changed_at is set for staleness tracking
        assert component._symbol_changed_at is not None
        assert (datetime.now(UTC) - component._symbol_changed_at).total_seconds() < 1

    @pytest.mark.asyncio()
    async def test_symbol_change_without_candles_shows_no_data_overlay(
        self, component: PriceChartComponent
    ) -> None:
        """No historical candles should show waiting overlay."""
        with (
            patch.object(component, "_ensure_chart_initialized", new_callable=AsyncMock),
            patch.object(component, "_fetch_candle_data", return_value=[]),
            patch.object(component, "_fetch_execution_markers", return_value=[]),
            patch.object(component, "_update_chart_data", new_callable=AsyncMock),
            patch.object(component, "_clear_chart_series", new_callable=AsyncMock) as mock_clear_series,
            patch.object(component, "_show_no_data_overlay", new_callable=AsyncMock) as mock_show,
            patch.object(component, "_hide_no_data_overlay", new_callable=AsyncMock) as mock_hide,
        ):
            await component.on_symbol_changed("AAPL")

        mock_clear_series.assert_awaited_once()
        mock_show.assert_awaited_once_with("AAPL")
        mock_hide.assert_not_called()

    @pytest.mark.asyncio()
    async def test_symbol_change_with_candles_hides_no_data_overlay(
        self, component: PriceChartComponent
    ) -> None:
        """Historical candles should hide waiting overlay."""
        candles = [CandleData(time=1, open=100, high=100, low=100, close=100, volume=1)]
        with (
            patch.object(component, "_ensure_chart_initialized", new_callable=AsyncMock),
            patch.object(component, "_fetch_candle_data", return_value=candles),
            patch.object(component, "_fetch_execution_markers", return_value=[]),
            patch.object(component, "_update_chart_data", new_callable=AsyncMock),
            patch.object(component, "_show_no_data_overlay", new_callable=AsyncMock) as mock_show,
            patch.object(component, "_hide_no_data_overlay", new_callable=AsyncMock) as mock_hide,
        ):
            await component.on_symbol_changed("AAPL")

        mock_hide.assert_awaited_once()
        mock_show.assert_not_called()

    @pytest.mark.asyncio()
    async def test_symbol_change_to_none_clears_symbol_changed_at(
        self, component: PriceChartComponent
    ) -> None:
        """Setting symbol to None clears _symbol_changed_at."""
        component._symbol_changed_at = datetime.now(UTC)

        with patch.object(component, "_clear_chart"):
            await component.on_symbol_changed(None)

        assert component._symbol_changed_at is None

    @pytest.mark.asyncio()
    async def test_symbol_change_to_none_clears_chart(self, component: PriceChartComponent) -> None:
        """Setting symbol to None clears the chart."""
        component._current_symbol = "AAPL"
        component._candles = [
            CandleData(time=1, open=100, high=105, low=95, close=100, volume=1000),
        ]

        with patch.object(component, "_clear_chart") as mock_clear:
            await component.on_symbol_changed(None)

        assert component._current_symbol is None
        mock_clear.assert_called_once()


class TestPriceChartHistoricalBars:
    """Tests for historical bar fetch integration."""

    @pytest.mark.asyncio()
    async def test_fetch_candle_data_uses_authenticated_client_signature(self) -> None:
        client = MagicMock()
        client.fetch_historical_bars = AsyncMock(
            return_value={
                "bars": [
                    {
                        "timestamp": "2026-04-20T13:30:00Z",
                        "open": 180.1,
                        "high": 181.0,
                        "low": 179.9,
                        "close": 180.6,
                        "volume": 1200,
                    },
                    {
                        "timestamp": "2026-04-20T13:35:00Z",
                        "open": 180.6,
                        "high": 181.2,
                        "low": 180.4,
                        "close": 181.1,
                        "volume": 900,
                    },
                ]
            }
        )
        component = PriceChartComponent(
            trading_client=client,
            user_id="user-1",
            role="trader",
            strategies=["alpha"],
        )

        candles = await component._fetch_candle_data("AAPL")

        assert len(candles) == 2
        assert candles[0].open == 180.1
        assert candles[1].close == 181.1
        client.fetch_historical_bars.assert_awaited_once_with(
            symbol="AAPL",
            user_id="user-1",
            role="trader",
            strategies=["alpha"],
            timeframe="5Min",
            limit=240,
        )

    @pytest.mark.asyncio()
    async def test_fetch_candle_data_falls_back_to_legacy_signature(self) -> None:
        class LegacyClient:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str, int]] = []

            async def fetch_historical_bars(
                self, *, symbol: str, timeframe: str, limit: int
            ) -> dict[str, list[dict[str, object]]]:
                self.calls.append((symbol, timeframe, limit))
                return {"bars": []}

        component = PriceChartComponent(
            trading_client=LegacyClient(),  # type: ignore[arg-type]
            user_id="user-1",
            role="trader",
            strategies=["alpha"],
        )

        candles = await component._fetch_candle_data("MSFT")

        assert candles == []
        assert component._client.calls == [  # type: ignore[attr-defined]
            ("MSFT", "5Min", 240),
            ("MSFT", "15Min", 240),
            ("MSFT", "1Hour", 200),
            ("MSFT", "1Day", 120),
        ]


class TestPriceChartExecutionMarkers:
    """Tests for execution-marker fetch compatibility."""

    @pytest.mark.asyncio()
    async def test_fetch_execution_markers_uses_authenticated_signature(self) -> None:
        client = MagicMock()
        client.fetch_recent_fills = AsyncMock(return_value={"fills": []})
        component = PriceChartComponent(
            trading_client=client,
            user_id="user-1",
            role="trader",
            strategies=["alpha"],
        )

        markers = await component._fetch_execution_markers("AAPL")

        assert markers == []
        client.fetch_recent_fills.assert_awaited_once_with(
            user_id="user-1",
            role="trader",
            strategies=["alpha"],
            limit=100,
        )

    @pytest.mark.asyncio()
    async def test_fetch_execution_markers_falls_back_to_legacy_signature(self) -> None:
        class LegacyClient:
            def __init__(self) -> None:
                self.calls: list[int] = []

            async def fetch_recent_fills(self, limit: int = 50) -> dict[str, list[dict[str, object]]]:
                self.calls.append(limit)
                return {"fills": []}

        client = LegacyClient()
        component = PriceChartComponent(
            trading_client=client,  # type: ignore[arg-type]
            user_id="user-1",
            role="trader",
            strategies=["alpha"],
        )

        markers = await component._fetch_execution_markers("AAPL")

        assert markers == []
        assert client.calls == [100]


class TestPriceChartRealtimeUpdates:
    """Tests for live candle updates."""

    @pytest.mark.asyncio()
    async def test_handle_price_update_hides_no_data_overlay(self) -> None:
        """First live tick should hide no-data overlay and seed first candle."""
        component = PriceChartComponent(trading_client=MagicMock())
        component._chart_initialized = True
        component._run_javascript = AsyncMock()  # type: ignore[method-assign]
        component._hide_no_data_overlay = AsyncMock()  # type: ignore[method-assign]

        await component._handle_price_update(101.25, datetime.now(UTC))

        component._hide_no_data_overlay.assert_awaited_once()
        assert len(component._candles) == 1
        assert component._candles[0].close == 101.25

    @pytest.mark.asyncio()
    async def test_handle_price_update_ignores_out_of_order_tick(self) -> None:
        """Out-of-order ticks should not mutate the latest candle."""
        component = PriceChartComponent(trading_client=MagicMock())
        component._chart_initialized = True
        component._candles = [
            CandleData(time=1200, open=100.0, high=101.0, low=99.5, close=100.5, volume=None),
        ]
        component._run_javascript = AsyncMock()  # type: ignore[method-assign]
        component._hide_no_data_overlay = AsyncMock()  # type: ignore[method-assign]
        component._hide_stale_overlay = AsyncMock()  # type: ignore[method-assign]

        before = component._candles.copy()
        await component._handle_price_update(99.0, datetime.fromtimestamp(900, UTC))

        assert component._candles == before
        component._run_javascript.assert_not_awaited()

    @pytest.mark.asyncio()
    async def test_handle_price_update_trim_syncs_chart_data(self) -> None:
        """When Python history is trimmed, JS chart should be reset with setData."""
        component = PriceChartComponent(trading_client=MagicMock())
        component._chart_initialized = True
        component._candles = [
            CandleData(
                time=300 * i,
                open=100.0 + i,
                high=101.0 + i,
                low=99.0 + i,
                close=100.5 + i,
                volume=None,
            )
            for i in range(component.CHART_TRIM_HIGH_WATER_MARK)
        ]
        component._run_javascript = AsyncMock()  # type: ignore[method-assign]
        component._hide_no_data_overlay = AsyncMock()  # type: ignore[method-assign]
        component._hide_stale_overlay = AsyncMock()  # type: ignore[method-assign]

        last_time = component._candles[-1].time
        await component._handle_price_update(900.0, datetime.fromtimestamp(last_time + 300, UTC))

        assert len(component._candles) == component.MAX_CHART_CANDLES
        assert component._candles[-1].close == 900.0
        component._run_javascript.assert_awaited()
        js_payload = component._run_javascript.await_args.args[0]
        assert "setData(" in js_payload

    @pytest.mark.asyncio()
    async def test_handle_price_update_new_bucket_uses_tick_as_open(self) -> None:
        """New candles should use the first tick in the bucket as open/high/low/close."""
        component = PriceChartComponent(trading_client=MagicMock())
        component._chart_initialized = True
        component._candles = [
            CandleData(time=1200, open=100.0, high=101.0, low=99.0, close=100.5, volume=None),
        ]
        component._run_javascript = AsyncMock()  # type: ignore[method-assign]
        component._hide_no_data_overlay = AsyncMock()  # type: ignore[method-assign]
        component._hide_stale_overlay = AsyncMock()  # type: ignore[method-assign]

        await component._handle_price_update(108.0, datetime.fromtimestamp(1500, UTC))

        assert len(component._candles) == 2
        newest = component._candles[-1]
        assert newest.open == 108.0
        assert newest.high == 108.0
        assert newest.low == 108.0
        assert newest.close == 108.0


class TestPriceChartOverlays:
    """Tests for chart overlays."""

    @pytest.mark.asyncio()
    async def test_show_no_data_overlay_updates_symbol_text(self) -> None:
        """Existing overlay subtitle should be refreshed when symbol changes."""
        component = PriceChartComponent(trading_client=MagicMock())
        component._run_javascript = AsyncMock()  # type: ignore[method-assign]

        await component._show_no_data_overlay("AAPL")

        js_payload = component._run_javascript.await_args.args[0]
        assert ".no-data-overlay-subtitle" in js_payload
        assert "Selected symbol:" in js_payload
        assert component._no_data_overlay_visible is True

    @pytest.mark.asyncio()
    async def test_hide_no_data_overlay_skips_when_already_hidden(self) -> None:
        """Hide should not trigger JS when overlay is already hidden."""
        component = PriceChartComponent(trading_client=MagicMock())
        component._run_javascript = AsyncMock()  # type: ignore[method-assign]

        await component._hide_no_data_overlay()

        component._run_javascript.assert_not_awaited()

    @pytest.mark.asyncio()
    async def test_hide_stale_overlay_skips_when_not_visible(self) -> None:
        """Hide stale overlay should no-op when nothing is visible."""
        component = PriceChartComponent(trading_client=MagicMock())
        component._run_javascript = AsyncMock()  # type: ignore[method-assign]

        await component._hide_stale_overlay()

        component._run_javascript.assert_not_awaited()

    @pytest.mark.asyncio()
    async def test_show_stale_overlay_sets_visibility_flag(self) -> None:
        """Stale overlay flag is tracked for high-frequency update optimization."""
        component = PriceChartComponent(trading_client=MagicMock())
        component._run_javascript = AsyncMock()  # type: ignore[method-assign]

        await component._show_stale_overlay(72)

        assert component._stale_overlay_visible is True
        assert component._fallback_overlay_visible is False


class TestPriceChartDispose:
    """Tests for dispose/cleanup."""

    @pytest.fixture()
    def component(self) -> PriceChartComponent:
        """Create component for testing."""
        client = MagicMock()
        return PriceChartComponent(trading_client=client)

    @pytest.mark.asyncio()
    async def test_dispose_sets_disposed_flag(self, component: PriceChartComponent) -> None:
        """dispose() sets disposed flag."""
        await component.dispose()

        assert component._disposed is True

    @pytest.mark.asyncio()
    async def test_dispose_clears_state(self, component: PriceChartComponent) -> None:
        """dispose() clears internal state."""
        component._current_symbol = "AAPL"
        component._candles = [
            CandleData(time=1, open=100, high=105, low=95, close=100, volume=1000),
        ]
        component._markers = [
            ExecutionMarker(time=1, price=100, side="buy", quantity=100, order_id="o1"),
        ]

        await component.dispose()

        assert component._current_symbol is None
        assert component._candles == []
        assert component._markers == []

    @pytest.mark.asyncio()
    async def test_dispose_cancels_timer(self, component: PriceChartComponent) -> None:
        """dispose() cancels staleness timer."""
        mock_timer = MagicMock()
        component._staleness_timer = mock_timer

        await component.dispose()

        mock_timer.cancel.assert_called_once()
