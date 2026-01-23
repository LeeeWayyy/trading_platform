"""Tests for MarketContextComponent."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from apps.web_console_ng.components.market_context import (
    STALE_THRESHOLD_S,
    MarketContextComponent,
    MarketDataSnapshot,
)


class TestMarketDataSnapshot:
    """Tests for MarketDataSnapshot dataclass."""

    def test_default_values(self) -> None:
        """Snapshot has None defaults for optional fields."""
        snap = MarketDataSnapshot(symbol="AAPL")

        assert snap.symbol == "AAPL"
        assert snap.bid_price is None
        assert snap.ask_price is None
        assert snap.last_price is None
        assert snap.timestamp is None

    def test_mid_price_calculation(self) -> None:
        """Mid price is average of bid and ask."""
        snap = MarketDataSnapshot(
            symbol="AAPL",
            bid_price=Decimal("100.00"),
            ask_price=Decimal("100.10"),
        )

        assert snap.mid_price == Decimal("100.05")

    def test_mid_price_none_when_missing_bid(self) -> None:
        """Mid price is None when bid is missing."""
        snap = MarketDataSnapshot(symbol="AAPL", ask_price=Decimal("100.10"))

        assert snap.mid_price is None

    def test_mid_price_none_when_missing_ask(self) -> None:
        """Mid price is None when ask is missing."""
        snap = MarketDataSnapshot(symbol="AAPL", bid_price=Decimal("100.00"))

        assert snap.mid_price is None

    def test_spread_bps_calculation(self) -> None:
        """Spread is calculated in basis points."""
        snap = MarketDataSnapshot(
            symbol="AAPL",
            bid_price=Decimal("100.00"),
            ask_price=Decimal("100.10"),
        )

        # spread = 0.10, mid = 100.05
        # spread_bps = (0.10 / 100.05) * 10000 = ~9.995
        assert snap.spread_bps is not None
        assert abs(snap.spread_bps - Decimal("9.995")) < Decimal("0.01")

    def test_spread_bps_none_when_missing_prices(self) -> None:
        """Spread is None when bid/ask missing."""
        snap = MarketDataSnapshot(symbol="AAPL")

        assert snap.spread_bps is None

    def test_change_calculation(self) -> None:
        """Change is difference from previous close."""
        snap = MarketDataSnapshot(
            symbol="AAPL",
            last_price=Decimal("105.00"),
            prev_close=Decimal("100.00"),
        )

        assert snap.change == Decimal("5.00")

    def test_change_pct_calculation(self) -> None:
        """Change percent is calculated correctly."""
        snap = MarketDataSnapshot(
            symbol="AAPL",
            last_price=Decimal("105.00"),
            prev_close=Decimal("100.00"),
        )

        assert snap.change_pct == Decimal("5.00")

    def test_change_none_when_no_prev_close(self) -> None:
        """Change is None when previous close is missing."""
        snap = MarketDataSnapshot(
            symbol="AAPL",
            last_price=Decimal("105.00"),
        )

        assert snap.change is None
        assert snap.change_pct is None

    def test_change_none_when_prev_close_zero(self) -> None:
        """Change is None when previous close is zero (avoid division by zero)."""
        snap = MarketDataSnapshot(
            symbol="AAPL",
            last_price=Decimal("105.00"),
            prev_close=Decimal("0"),
        )

        assert snap.change is None
        assert snap.change_pct is None


class TestMarketContextInit:
    """Tests for MarketContextComponent initialization."""

    def test_initial_state(self) -> None:
        """Component initializes with empty state."""
        client = MagicMock()

        comp = MarketContextComponent(trading_client=client)

        assert comp._current_symbol is None
        assert comp._data is None
        assert comp._disposed is False

    def test_callback_stored(self) -> None:
        """Price update callback is stored."""
        client = MagicMock()
        callback = MagicMock()

        comp = MarketContextComponent(
            trading_client=client,
            on_price_updated=callback,
        )

        assert comp._on_price_updated == callback


class TestMarketContextPriceData:
    """Tests for price data handling."""

    @pytest.fixture()
    def component(self) -> MarketContextComponent:
        """Create component for testing."""
        client = MagicMock()
        return MarketContextComponent(trading_client=client)

    def test_set_price_data_updates_snapshot(self, component: MarketContextComponent) -> None:
        """set_price_data creates snapshot from data."""
        component._current_symbol = "AAPL"
        component._last_ui_update = 0  # Allow immediate update

        component.set_price_data({
            "symbol": "AAPL",
            "bid": "100.00",
            "ask": "100.10",
            "price": "100.05",
            "bid_size": 100,
            "ask_size": 200,
        })

        assert component._data is not None
        assert component._data.symbol == "AAPL"
        assert component._data.bid_price == Decimal("100.00")
        assert component._data.ask_price == Decimal("100.10")
        assert component._data.last_price == Decimal("100.05")
        assert component._data.bid_size == 100
        assert component._data.ask_size == 200

    def test_set_price_data_ignores_wrong_symbol(
        self, component: MarketContextComponent
    ) -> None:
        """set_price_data ignores data for different symbol."""
        component._current_symbol = "AAPL"
        component._data = None

        component.set_price_data({
            "symbol": "GOOGL",  # Different symbol
            "price": "100.00",
        })

        # Data should not be updated
        assert component._data is None

    def test_set_price_data_rejects_non_dict(
        self, component: MarketContextComponent
    ) -> None:
        """set_price_data rejects non-dict input."""
        component._current_symbol = "AAPL"
        component._data = None

        component.set_price_data("invalid")  # type: ignore[arg-type]

        assert component._data is None

    def test_set_price_data_handles_invalid_decimal(
        self, component: MarketContextComponent
    ) -> None:
        """Invalid decimal values become None."""
        component._current_symbol = "AAPL"
        component._last_ui_update = 0

        component.set_price_data({
            "symbol": "AAPL",
            "bid": "not-a-number",
            "ask": "100.10",
            "price": "100.05",
        })

        assert component._data is not None
        assert component._data.bid_price is None  # Invalid value becomes None
        assert component._data.ask_price == Decimal("100.10")

    def test_set_price_data_handles_nan(self, component: MarketContextComponent) -> None:
        """NaN values become None."""
        component._current_symbol = "AAPL"
        component._last_ui_update = 0

        component.set_price_data({
            "symbol": "AAPL",
            "bid": "NaN",
            "price": "100.05",
        })

        assert component._data is not None
        assert component._data.bid_price is None

    def test_set_price_data_throttled(self, component: MarketContextComponent) -> None:
        """UI updates are throttled but data is always updated."""
        import time

        component._current_symbol = "AAPL"
        initial_update_time = time.time()
        component._last_ui_update = initial_update_time  # Just updated

        # This update should have UI throttled but data still updated
        component.set_price_data({
            "symbol": "AAPL",
            "price": "100.00",
        })

        # Data IS updated (fix for dropped last update in burst)
        assert component._data is not None
        assert component._data.last_price == Decimal("100.00")

        # UI update timestamp was NOT changed (UI was skipped)
        assert component._last_ui_update == initial_update_time


class TestMarketContextStaleness:
    """Tests for staleness checks."""

    @pytest.fixture()
    def component(self) -> MarketContextComponent:
        """Create component for testing."""
        client = MagicMock()
        return MarketContextComponent(trading_client=client)

    def test_is_data_stale_true_when_no_data(
        self, component: MarketContextComponent
    ) -> None:
        """Data is stale when no data exists."""
        component._data = None

        assert component.is_data_stale() is True

    def test_is_data_stale_true_when_no_timestamp(
        self, component: MarketContextComponent
    ) -> None:
        """Data is stale when timestamp is missing."""
        component._data = MarketDataSnapshot(symbol="AAPL", timestamp=None)

        assert component.is_data_stale() is True

    def test_is_data_stale_false_when_fresh(
        self, component: MarketContextComponent
    ) -> None:
        """Data is fresh when timestamp is recent."""
        component._data = MarketDataSnapshot(
            symbol="AAPL",
            timestamp=datetime.now(UTC),
        )

        assert component.is_data_stale() is False

    def test_is_data_stale_true_when_old(
        self, component: MarketContextComponent
    ) -> None:
        """Data is stale when timestamp is old."""
        old_time = datetime.now(UTC) - timedelta(seconds=STALE_THRESHOLD_S + 10)
        component._data = MarketDataSnapshot(
            symbol="AAPL",
            timestamp=old_time,
        )

        assert component.is_data_stale() is True


class TestMarketContextGetters:
    """Tests for getter methods."""

    @pytest.fixture()
    def component(self) -> MarketContextComponent:
        """Create component for testing."""
        client = MagicMock()
        return MarketContextComponent(trading_client=client)

    def test_get_current_price_returns_last_price(
        self, component: MarketContextComponent
    ) -> None:
        """get_current_price returns the last price."""
        component._data = MarketDataSnapshot(
            symbol="AAPL",
            last_price=Decimal("150.00"),
        )

        assert component.get_current_price() == Decimal("150.00")

    def test_get_current_price_returns_none_when_no_data(
        self, component: MarketContextComponent
    ) -> None:
        """get_current_price returns None when no data."""
        component._data = None

        assert component.get_current_price() is None

    def test_get_price_timestamp_returns_timestamp(
        self, component: MarketContextComponent
    ) -> None:
        """get_price_timestamp returns the timestamp."""
        now = datetime.now(UTC)
        component._data = MarketDataSnapshot(
            symbol="AAPL",
            timestamp=now,
        )

        assert component.get_price_timestamp() == now

    def test_get_price_timestamp_returns_none_when_no_data(
        self, component: MarketContextComponent
    ) -> None:
        """get_price_timestamp returns None when no data."""
        component._data = None

        assert component.get_price_timestamp() is None


class TestMarketContextSymbolChange:
    """Tests for symbol change handling."""

    @pytest.fixture()
    def component(self) -> MarketContextComponent:
        """Create component for testing."""
        client = MagicMock()
        return MarketContextComponent(trading_client=client)

    @pytest.mark.asyncio()
    async def test_symbol_change_clears_data(
        self, component: MarketContextComponent
    ) -> None:
        """Changing to None symbol clears data."""
        component._data = MarketDataSnapshot(
            symbol="AAPL",
            last_price=Decimal("150.00"),
        )

        await component.on_symbol_changed(None)

        assert component._data is None
        assert component._current_symbol is None

    @pytest.mark.asyncio()
    async def test_symbol_change_updates_current_symbol(
        self, component: MarketContextComponent
    ) -> None:
        """Symbol change updates current symbol."""
        await component.on_symbol_changed("AAPL")

        assert component._current_symbol == "AAPL"


class TestMarketContextDispose:
    """Tests for dispose/cleanup."""

    @pytest.fixture()
    def component(self) -> MarketContextComponent:
        """Create component for testing."""
        client = MagicMock()
        return MarketContextComponent(trading_client=client)

    @pytest.mark.asyncio()
    async def test_dispose_sets_disposed_flag(
        self, component: MarketContextComponent
    ) -> None:
        """dispose() sets disposed flag."""
        await component.dispose()

        assert component._disposed is True

    @pytest.mark.asyncio()
    async def test_dispose_cancels_timer(
        self, component: MarketContextComponent
    ) -> None:
        """dispose() cancels staleness timer."""
        mock_timer = MagicMock()
        component._staleness_timer = mock_timer

        await component.dispose()

        mock_timer.cancel.assert_called_once()
