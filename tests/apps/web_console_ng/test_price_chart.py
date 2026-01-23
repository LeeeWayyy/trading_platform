"""Tests for PriceChartComponent."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from apps.web_console_ng.components.price_chart import (
    REALTIME_FALLBACK_THRESHOLD_S,
    REALTIME_STALE_THRESHOLD_S,
    CandleData,
    ExecutionMarker,
    PriceChartComponent,
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

    def test_set_price_data_rejects_non_dict(
        self, component: PriceChartComponent
    ) -> None:
        """set_price_data rejects non-dict input."""
        component._current_symbol = "AAPL"

        component.set_price_data("invalid")  # type: ignore[arg-type]

        # No error raised, silently ignored
        assert component._last_realtime_update is None

    def test_set_price_data_ignores_wrong_symbol(
        self, component: PriceChartComponent
    ) -> None:
        """set_price_data ignores data for different symbol."""
        component._current_symbol = "AAPL"

        component.set_price_data({
            "symbol": "GOOGL",  # Different symbol
            "price": "100.00",
            "timestamp": "2024-01-01T12:00:00Z",
        })

        # Should not update
        assert component._last_realtime_update is None

    def test_set_price_data_rejects_missing_price(
        self, component: PriceChartComponent
    ) -> None:
        """set_price_data rejects data without price."""
        component._current_symbol = "AAPL"

        component.set_price_data({
            "symbol": "AAPL",
            "timestamp": "2024-01-01T12:00:00Z",
            # No price field
        })

        assert component._last_realtime_update is None

    def test_set_price_data_rejects_invalid_price(
        self, component: PriceChartComponent
    ) -> None:
        """set_price_data rejects invalid price values."""
        component._current_symbol = "AAPL"

        component.set_price_data({
            "symbol": "AAPL",
            "price": "not-a-number",
            "timestamp": "2024-01-01T12:00:00Z",
        })

        assert component._last_realtime_update is None

    def test_set_price_data_rejects_nan(
        self, component: PriceChartComponent
    ) -> None:
        """set_price_data rejects NaN price values."""
        component._current_symbol = "AAPL"

        component.set_price_data({
            "symbol": "AAPL",
            "price": float("nan"),
            "timestamp": "2024-01-01T12:00:00Z",
        })

        assert component._last_realtime_update is None

    def test_set_price_data_rejects_infinity(
        self, component: PriceChartComponent
    ) -> None:
        """set_price_data rejects infinity price values."""
        component._current_symbol = "AAPL"

        component.set_price_data({
            "symbol": "AAPL",
            "price": float("inf"),
            "timestamp": "2024-01-01T12:00:00Z",
        })

        assert component._last_realtime_update is None

    def test_set_price_data_rejects_negative_price(
        self, component: PriceChartComponent
    ) -> None:
        """set_price_data rejects negative price values."""
        component._current_symbol = "AAPL"

        component.set_price_data({
            "symbol": "AAPL",
            "price": -100.0,
            "timestamp": "2024-01-01T12:00:00Z",
        })

        assert component._last_realtime_update is None

    def test_set_price_data_rejects_zero_price(
        self, component: PriceChartComponent
    ) -> None:
        """set_price_data rejects zero price values."""
        component._current_symbol = "AAPL"

        component.set_price_data({
            "symbol": "AAPL",
            "price": 0,
            "timestamp": "2024-01-01T12:00:00Z",
        })

        assert component._last_realtime_update is None

    def test_set_price_data_updates_timestamp_on_valid_data(
        self, component: PriceChartComponent
    ) -> None:
        """set_price_data updates timestamp on valid data."""
        component._current_symbol = "AAPL"

        with patch("asyncio.create_task"):
            component.set_price_data({
                "symbol": "AAPL",
                "price": 150.0,
                "timestamp": "2024-01-01T12:00:00Z",
            })

        assert component._last_realtime_update is not None
        assert component._last_realtime_update.year == 2024

    def test_set_price_data_handles_missing_timestamp(
        self, component: PriceChartComponent
    ) -> None:
        """set_price_data sets timestamp to None when missing (FAIL-CLOSED)."""
        component._current_symbol = "AAPL"

        with patch("asyncio.create_task"):
            component.set_price_data({
                "symbol": "AAPL",
                "price": 150.0,
                # No timestamp
            })

        # FAIL-CLOSED: timestamp None when missing
        assert component._last_realtime_update is None

    def test_set_price_data_handles_invalid_timestamp(
        self, component: PriceChartComponent
    ) -> None:
        """set_price_data sets timestamp to None when invalid (FAIL-CLOSED)."""
        component._current_symbol = "AAPL"

        with patch("asyncio.create_task"):
            component.set_price_data({
                "symbol": "AAPL",
                "price": 150.0,
                "timestamp": "not-a-timestamp",
            })

        # FAIL-CLOSED: timestamp None when invalid
        assert component._last_realtime_update is None

    def test_set_price_data_ignored_when_disposed(
        self, component: PriceChartComponent
    ) -> None:
        """set_price_data does nothing when disposed."""
        component._current_symbol = "AAPL"
        component._disposed = True

        component.set_price_data({
            "symbol": "AAPL",
            "price": 150.0,
            "timestamp": "2024-01-01T12:00:00Z",
        })

        assert component._last_realtime_update is None


class TestPriceChartStaleness:
    """Tests for staleness detection."""

    @pytest.fixture()
    def component(self) -> PriceChartComponent:
        """Create component for testing."""
        client = MagicMock()
        return PriceChartComponent(trading_client=client)

    def test_is_data_stale_true_when_no_update(
        self, component: PriceChartComponent
    ) -> None:
        """Data is stale when no realtime update received."""
        component._last_realtime_update = None

        assert component.is_data_stale() is True

    def test_is_data_stale_false_when_fresh(
        self, component: PriceChartComponent
    ) -> None:
        """Data is fresh when recently updated."""
        component._last_realtime_update = datetime.now(UTC)

        assert component.is_data_stale() is False

    def test_is_data_stale_true_when_old(
        self, component: PriceChartComponent
    ) -> None:
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

    def test_calculate_vwap_empty_candles(
        self, component: PriceChartComponent
    ) -> None:
        """VWAP returns empty list for no candles."""
        component._candles = []

        result = component.calculate_vwap_from_candles()

        assert result == []

    def test_calculate_vwap_with_candles(
        self, component: PriceChartComponent
    ) -> None:
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

    def test_calculate_vwap_skips_zero_volume(
        self, component: PriceChartComponent
    ) -> None:
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

    def test_calculate_twap_empty_candles(
        self, component: PriceChartComponent
    ) -> None:
        """TWAP returns empty list for no candles."""
        component._candles = []

        result = component.calculate_twap_from_candles()

        assert result == []

    def test_calculate_twap_with_candles(
        self, component: PriceChartComponent
    ) -> None:
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

    def test_get_current_price_returns_last_close(
        self, component: PriceChartComponent
    ) -> None:
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
    async def test_symbol_change_resets_timestamp(
        self, component: PriceChartComponent
    ) -> None:
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
    async def test_symbol_change_to_none_clears_symbol_changed_at(
        self, component: PriceChartComponent
    ) -> None:
        """Setting symbol to None clears _symbol_changed_at."""
        component._symbol_changed_at = datetime.now(UTC)

        with patch.object(component, "_clear_chart"):
            await component.on_symbol_changed(None)

        assert component._symbol_changed_at is None

    @pytest.mark.asyncio()
    async def test_symbol_change_to_none_clears_chart(
        self, component: PriceChartComponent
    ) -> None:
        """Setting symbol to None clears the chart."""
        component._current_symbol = "AAPL"
        component._candles = [
            CandleData(time=1, open=100, high=105, low=95, close=100, volume=1000),
        ]

        with patch.object(component, "_clear_chart") as mock_clear:
            await component.on_symbol_changed(None)

        assert component._current_symbol is None
        mock_clear.assert_called_once()


class TestPriceChartDispose:
    """Tests for dispose/cleanup."""

    @pytest.fixture()
    def component(self) -> PriceChartComponent:
        """Create component for testing."""
        client = MagicMock()
        return PriceChartComponent(trading_client=client)

    @pytest.mark.asyncio()
    async def test_dispose_sets_disposed_flag(
        self, component: PriceChartComponent
    ) -> None:
        """dispose() sets disposed flag."""
        await component.dispose()

        assert component._disposed is True

    @pytest.mark.asyncio()
    async def test_dispose_clears_state(
        self, component: PriceChartComponent
    ) -> None:
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
    async def test_dispose_cancels_timer(
        self, component: PriceChartComponent
    ) -> None:
        """dispose() cancels staleness timer."""
        mock_timer = MagicMock()
        component._staleness_timer = mock_timer

        await component.dispose()

        mock_timer.cancel.assert_called_once()

