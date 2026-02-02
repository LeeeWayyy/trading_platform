"""Tests for WatchlistComponent."""

from __future__ import annotations

import asyncio
from collections import deque
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.web_console_ng.components.watchlist import (
    WatchlistComponent,
    WatchlistItem,
)

# Note: Symbol validation tests are in test_time_utils.py since the
# shared validator from utils.time is now used by all components.


class TestWatchlistItem:
    """Tests for WatchlistItem dataclass."""

    def test_default_values(self) -> None:
        """WatchlistItem has None defaults for optional fields."""
        item = WatchlistItem(symbol="AAPL")

        assert item.symbol == "AAPL"
        assert item.last_price is None
        assert item.prev_close is None
        assert item.change is None
        assert item.change_pct is None
        # sparkline_data is a deque with maxlen=20 for O(1) bounded appends
        assert isinstance(item.sparkline_data, deque)
        assert len(item.sparkline_data) == 0
        assert item.timestamp is None

    def test_sparkline_data_mutable(self) -> None:
        """Each item has its own sparkline_data deque."""
        item1 = WatchlistItem(symbol="AAPL")
        item2 = WatchlistItem(symbol="MSFT")

        item1.sparkline_data.append(100.0)

        assert list(item1.sparkline_data) == [100.0]
        assert len(item2.sparkline_data) == 0


class TestWatchlistInit:
    """Tests for WatchlistComponent initialization."""

    def test_initial_state(self) -> None:
        """Component initializes with empty state."""
        client = MagicMock()

        comp = WatchlistComponent(trading_client=client)

        assert comp._items == {}
        assert comp._symbol_order == []
        assert comp._selected_symbol is None
        assert comp._disposed is False

    def test_callbacks_stored(self) -> None:
        """Callbacks are stored correctly."""
        client = MagicMock()
        on_selected = AsyncMock()
        on_sub = AsyncMock()
        on_unsub = AsyncMock()

        comp = WatchlistComponent(
            trading_client=client,
            on_symbol_selected=on_selected,
            on_subscribe_symbol=on_sub,
            on_unsubscribe_symbol=on_unsub,
        )

        assert comp._on_symbol_selected == on_selected
        assert comp._on_subscribe_symbol == on_sub
        assert comp._on_unsubscribe_symbol == on_unsub


class TestWatchlistInitialize:
    """Tests for initialize method."""

    @pytest.fixture()
    def component(self) -> WatchlistComponent:
        """Create component for testing."""
        client = MagicMock()
        return WatchlistComponent(
            trading_client=client,
            on_subscribe_symbol=AsyncMock(),
        )

    @pytest.mark.asyncio()
    async def test_initialize_default_symbols(self, component: WatchlistComponent) -> None:
        """Initialize with default symbols when none provided."""
        tracker = MagicMock()

        await component.initialize(timer_tracker=tracker)

        assert "SPY" in component._items
        assert "QQQ" in component._items
        assert "AAPL" in component._items
        assert "MSFT" in component._items
        assert "TSLA" in component._items
        assert len(component._items) == 5

    @pytest.mark.asyncio()
    async def test_initialize_custom_symbols(self, component: WatchlistComponent) -> None:
        """Initialize with custom symbols."""
        tracker = MagicMock()

        await component.initialize(
            timer_tracker=tracker,
            initial_symbols=["GOOG", "META"],
        )

        assert "GOOG" in component._items
        assert "META" in component._items
        assert len(component._items) == 2

    @pytest.mark.asyncio()
    async def test_initialize_deduplicates_symbols(self, component: WatchlistComponent) -> None:
        """Initialize removes duplicate symbols."""
        tracker = MagicMock()

        await component.initialize(
            timer_tracker=tracker,
            initial_symbols=["AAPL", "aapl", "AAPL"],
        )

        assert len(component._items) == 1
        assert "AAPL" in component._items

    @pytest.mark.asyncio()
    async def test_initialize_skips_invalid_symbols(self, component: WatchlistComponent) -> None:
        """Initialize skips invalid symbols."""
        tracker = MagicMock()

        await component.initialize(
            timer_tracker=tracker,
            initial_symbols=["AAPL", "INVALID-SYM", "MSFT"],
        )

        assert "AAPL" in component._items
        assert "MSFT" in component._items
        assert "INVALID-SYM" not in component._items
        assert len(component._items) == 2

    @pytest.mark.asyncio()
    async def test_initialize_calls_subscribe_callback(self, component: WatchlistComponent) -> None:
        """Initialize calls subscribe callback for each symbol."""
        tracker = MagicMock()

        await component.initialize(
            timer_tracker=tracker,
            initial_symbols=["AAPL", "MSFT"],
        )

        assert component._on_subscribe_symbol.call_count == 2

    @pytest.mark.asyncio()
    async def test_initialize_renders_items_when_container_exists(
        self, component: WatchlistComponent
    ) -> None:
        """Initialize renders items when list_container exists (create() called first)."""
        tracker = MagicMock()
        # Simulate create() was called first by setting _list_container
        component._list_container = MagicMock()

        with patch.object(component, "_render_items") as mock_render:
            await component.initialize(
                timer_tracker=tracker,
                initial_symbols=["AAPL", "MSFT"],
            )

            # Verify _render_items was called after initialization
            mock_render.assert_called_once()


class TestWatchlistPriceData:
    """Tests for price data handling."""

    @pytest.fixture()
    def component(self) -> WatchlistComponent:
        """Create component with items for testing."""
        client = MagicMock()
        comp = WatchlistComponent(trading_client=client)
        comp._items = {"AAPL": WatchlistItem(symbol="AAPL")}
        comp._symbol_order = ["AAPL"]
        comp._list_container = MagicMock()
        return comp

    def test_set_price_data_updates_item(self, component: WatchlistComponent) -> None:
        """set_symbol_price_data updates the item."""
        component.set_symbol_price_data(
            "AAPL",
            {
                "price": "150.50",
                "timestamp": "2024-01-01T12:00:00Z",
            },
        )

        item = component._items["AAPL"]
        assert item.last_price == Decimal("150.50")
        assert item.timestamp is not None

    def test_set_price_data_rejects_non_dict(self, component: WatchlistComponent) -> None:
        """set_symbol_price_data rejects non-dict input."""
        component.set_symbol_price_data("AAPL", "invalid")  # type: ignore[arg-type]

        item = component._items["AAPL"]
        assert item.last_price is None

    def test_set_price_data_ignores_unknown_symbol(self, component: WatchlistComponent) -> None:
        """set_symbol_price_data ignores unknown symbols."""
        component.set_symbol_price_data("UNKNOWN", {"price": "100.00"})

        # No error, just ignored
        assert "UNKNOWN" not in component._items

    def test_set_price_data_handles_invalid_price(self, component: WatchlistComponent) -> None:
        """set_symbol_price_data handles invalid price."""
        component.set_symbol_price_data("AAPL", {"price": "not-a-number"})

        item = component._items["AAPL"]
        assert item.last_price is None

    def test_set_price_data_handles_nan_price(self, component: WatchlistComponent) -> None:
        """set_symbol_price_data handles NaN price."""
        component.set_symbol_price_data("AAPL", {"price": "NaN"})

        item = component._items["AAPL"]
        assert item.last_price is None

    def test_set_price_data_handles_negative_price(self, component: WatchlistComponent) -> None:
        """set_symbol_price_data handles negative price."""
        component.set_symbol_price_data("AAPL", {"price": "-100"})

        item = component._items["AAPL"]
        assert item.last_price is None

    def test_set_price_data_updates_sparkline(self, component: WatchlistComponent) -> None:
        """set_symbol_price_data appends to sparkline."""
        for i in range(5):
            component.set_symbol_price_data("AAPL", {"price": str(100 + i)})

        item = component._items["AAPL"]
        assert len(item.sparkline_data) == 5
        assert item.sparkline_data[0] == 100.0
        assert item.sparkline_data[4] == 104.0

    def test_set_price_data_limits_sparkline_points(self, component: WatchlistComponent) -> None:
        """set_symbol_price_data limits sparkline to SPARKLINE_POINTS."""
        for i in range(30):
            component.set_symbol_price_data("AAPL", {"price": str(100 + i)})

        item = component._items["AAPL"]
        assert len(item.sparkline_data) == component.SPARKLINE_POINTS

    def test_set_price_data_calculates_change_with_prev_close(
        self, component: WatchlistComponent
    ) -> None:
        """set_symbol_price_data calculates change when prev_close exists."""
        item = component._items["AAPL"]
        item.prev_close = Decimal("100.00")

        component.set_symbol_price_data("AAPL", {"price": "105.00"})

        assert item.change == Decimal("5.00")
        assert item.change_pct == Decimal("5.00")

    def test_set_price_data_parses_prev_close(self, component: WatchlistComponent) -> None:
        """set_symbol_price_data parses prev_close from data."""
        component.set_symbol_price_data(
            "AAPL",
            {
                "price": "105.00",
                "prev_close": "100.00",
            },
        )

        item = component._items["AAPL"]
        assert item.prev_close == Decimal("100.00")
        assert item.change == Decimal("5.00")
        assert item.change_pct == Decimal("5.00")

    def test_set_price_data_parses_previous_close_alias(
        self, component: WatchlistComponent
    ) -> None:
        """set_symbol_price_data parses previous_close as alias."""
        component.set_symbol_price_data(
            "AAPL",
            {
                "price": "110.00",
                "previous_close": "100.00",
            },
        )

        item = component._items["AAPL"]
        assert item.prev_close == Decimal("100.00")
        assert item.change == Decimal("10.00")

    def test_set_price_data_ignores_when_disposed(self, component: WatchlistComponent) -> None:
        """set_symbol_price_data does nothing when disposed."""
        component._disposed = True

        component.set_symbol_price_data("AAPL", {"price": "150.00"})

        item = component._items["AAPL"]
        assert item.last_price is None


class TestWatchlistSelection:
    """Tests for symbol selection."""

    @pytest.fixture()
    def component(self) -> WatchlistComponent:
        """Create component for testing."""
        client = MagicMock()
        comp = WatchlistComponent(
            trading_client=client,
            on_symbol_selected=AsyncMock(),
        )
        comp._items = {"AAPL": WatchlistItem(symbol="AAPL")}
        comp._symbol_order = ["AAPL"]
        comp._list_container = MagicMock()
        return comp

    @pytest.mark.asyncio()
    async def test_select_symbol_updates_state(self, component: WatchlistComponent) -> None:
        """_select_symbol updates selected_symbol."""
        with patch.object(component, "_render_items"):
            component._select_symbol("AAPL")

        assert component._selected_symbol == "AAPL"

    @pytest.mark.asyncio()
    async def test_select_symbol_creates_task(self, component: WatchlistComponent) -> None:
        """_select_symbol creates async task for callback."""
        with patch.object(component, "_render_items"):
            component._select_symbol("AAPL")

        assert component._pending_selection_task is not None

    def test_get_selected_symbol(self, component: WatchlistComponent) -> None:
        """get_selected_symbol returns current selection."""
        assert component.get_selected_symbol() is None

        component._selected_symbol = "AAPL"

        assert component.get_selected_symbol() == "AAPL"


class TestWatchlistGetters:
    """Tests for getter methods."""

    @pytest.fixture()
    def component(self) -> WatchlistComponent:
        """Create component for testing."""
        client = MagicMock()
        comp = WatchlistComponent(trading_client=client)
        comp._items = {
            "AAPL": WatchlistItem(symbol="AAPL"),
            "MSFT": WatchlistItem(symbol="MSFT"),
        }
        comp._symbol_order = ["AAPL", "MSFT"]
        return comp

    def test_get_symbols_returns_order(self, component: WatchlistComponent) -> None:
        """get_symbols returns symbols in order."""
        symbols = component.get_symbols()

        assert symbols == ["AAPL", "MSFT"]

    def test_get_symbols_returns_copy(self, component: WatchlistComponent) -> None:
        """get_symbols returns a copy, not the internal list."""
        symbols = component.get_symbols()
        symbols.append("TSLA")

        assert component._symbol_order == ["AAPL", "MSFT"]


class TestWatchlistDispose:
    """Tests for dispose/cleanup."""

    @pytest.fixture()
    def component(self) -> WatchlistComponent:
        """Create component for testing."""
        client = MagicMock()
        comp = WatchlistComponent(trading_client=client)
        comp._items = {"AAPL": WatchlistItem(symbol="AAPL")}
        comp._symbol_order = ["AAPL"]
        return comp

    @pytest.mark.asyncio()
    async def test_dispose_sets_disposed_flag(self, component: WatchlistComponent) -> None:
        """dispose() sets disposed flag."""
        await component.dispose()

        assert component._disposed is True

    @pytest.mark.asyncio()
    async def test_dispose_clears_state(self, component: WatchlistComponent) -> None:
        """dispose() clears internal state."""
        component._selected_symbol = "AAPL"
        component._pending_row_renders.add("AAPL")
        component._last_row_render["AAPL"] = 123.0

        await component.dispose()

        assert component._items == {}
        assert component._symbol_order == []
        assert component._selected_symbol is None
        assert component._pending_row_renders == set()
        assert component._last_row_render == {}

    @pytest.mark.asyncio()
    async def test_dispose_cancels_pending_task(self, component: WatchlistComponent) -> None:
        """dispose() cancels pending selection task."""

        # Create a real task that we can cancel
        async def never_finish() -> None:
            await asyncio.sleep(100)

        task = asyncio.create_task(never_finish())
        component._pending_selection_task = task

        await component.dispose()

        # Task should be cancelled
        assert task.cancelled()

    @pytest.mark.asyncio()
    async def test_dispose_cancels_render_batch(self, component: WatchlistComponent) -> None:
        """dispose() cancels pending render batch."""
        mock_handle = MagicMock()
        component._render_batch_handle = mock_handle

        await component.dispose()

        mock_handle.cancel.assert_called_once()
        assert component._render_batch_handle is None
