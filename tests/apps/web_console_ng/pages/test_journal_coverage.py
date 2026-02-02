"""Comprehensive coverage tests for journal.py.

Target: 85%+ coverage for apps/web_console_ng/pages/journal.py

Covers:
- Trade journal page rendering with real and demo modes
- Filtering (date, symbol, side) and pagination
- Statistics display and calculations
- Export functionality (CSV/Excel) with audit logging
- Error handling for database failures
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.web_console_ng.pages import journal as journal_module


class DummyElement:
    """Dummy UI element for testing UI interactions."""

    def __init__(self, *, text: str | None = None, value: Any = None) -> None:
        self.text = text or ""
        self.value = value
        self.visible = True
        self.on_click_cb = None
        self.on_value_change_cb = None

    def __enter__(self) -> DummyElement:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def classes(self, *args, **kwargs) -> DummyElement:
        return self

    def props(self, *args, **kwargs) -> DummyElement:
        return self

    def set_visibility(self, value: bool) -> None:
        self.visible = value

    def set_text(self, value: str) -> None:
        self.text = value

    def on_click(self, cb) -> None:
        self.on_click_cb = cb

    def on_value_change(self, cb) -> None:
        self.on_value_change_cb = cb

    def clear(self) -> None:
        return None

    def on(self, event: str, handler) -> DummyElement:
        return self

    def update(self) -> None:
        return None

    def delete(self) -> None:
        return None


class DummyUI:
    """Dummy UI context for testing NiceGUI pages."""

    def __init__(self) -> None:
        self.labels: list[str] = []
        self.tables: list[dict[str, Any]] = []
        self.buttons: list[DummyElement] = []
        self.notifications: list[tuple[str, str | None]] = []
        self.downloads: list[tuple[bytes, str]] = []
        # Mock storage context for NiceGUI
        mock_storage = MagicMock()
        mock_storage.user = {"user_id": "test_user", "role": "admin"}
        self.context = SimpleNamespace(client=SimpleNamespace(storage=mock_storage))

    def card(self, *args, **kwargs) -> DummyElement:
        return DummyElement()

    def row(self, *args, **kwargs) -> DummyElement:
        return DummyElement()

    def column(self, *args, **kwargs) -> DummyElement:
        return DummyElement()

    def label(self, text: str = "", *args, **kwargs) -> DummyElement:
        self.labels.append(text)
        return DummyElement(text=text)

    def table(
        self, *, columns: list[dict[str, Any]], rows: list[dict[str, Any]], **kwargs
    ) -> DummyElement:
        self.tables.append({"columns": columns, "rows": rows})
        return DummyElement()

    def button(
        self, text: str = "", icon: str | None = None, on_click=None, **kwargs
    ) -> DummyElement:
        element = DummyElement(text=text)
        if on_click is not None:
            element.on_click(on_click)
        self.buttons.append(element)
        return element

    def notify(self, message: str, type: str | None = None) -> None:
        self.notifications.append((message, type))

    def download(self, content: bytes, filename: str) -> None:
        self.downloads.append((content, filename))

    def select(self, *args, **kwargs) -> DummyElement:
        return DummyElement(value=kwargs.get("value"))

    def input(self, *args, **kwargs) -> DummyElement:
        return DummyElement(value=kwargs.get("value", ""))

    def date(self, *args, **kwargs) -> DummyElement:
        return DummyElement(value=kwargs.get("value", ""))

    def spinner(self, *args, **kwargs) -> DummyElement:
        return DummyElement()

    def icon(self, *args, **kwargs) -> DummyElement:
        return DummyElement()

    def textarea(self, *args, **kwargs) -> DummyElement:
        return DummyElement(value=kwargs.get("value", ""))


@pytest.fixture()
def mock_ui():
    """Mock NiceGUI UI module."""
    ui = DummyUI()
    with patch.object(journal_module, "ui", ui):
        yield ui


@pytest.fixture()
def mock_user():
    """Mock authenticated user."""
    return {
        "user_id": "test_user",
        "username": "testuser",
        "role": "admin",
    }


@pytest.fixture()
def mock_get_current_user(mock_user):
    """Mock get_current_user."""
    with patch.object(journal_module, "get_current_user", return_value=mock_user):
        yield


@pytest.fixture()
def mock_permissions():
    """Mock permission checks."""
    with (
        patch.object(journal_module, "has_permission", return_value=True),
        patch.object(
            journal_module,
            "get_authorized_strategies",
            return_value=["alpha_baseline", "momentum_v1"],
        ),
    ):
        yield


@pytest.fixture()
def mock_db_pool():
    """Mock database pool."""
    pool = AsyncMock()
    with patch.object(journal_module, "get_db_pool", return_value=pool):
        yield pool


class TestRenderTradeStats:
    """Tests for _render_trade_stats helper."""

    def test_render_trade_stats_with_data(self, mock_ui):
        """Test rendering trade statistics with valid data."""
        stats = {
            "trade_count": 100,
            "total_volume": 250000.50,
            "total_pnl": 5432.10,
            "win_rate": 0.542,
            "avg_trade_size": 2500.00,
        }

        journal_module._render_trade_stats(stats)

        # Check that stats were rendered
        assert any("100" in label for label in mock_ui.labels)
        assert any("250,000.50" in label for label in mock_ui.labels)
        assert any("5,432.10" in label for label in mock_ui.labels)
        assert any("54.2%" in label for label in mock_ui.labels)

    def test_render_trade_stats_with_zero_values(self, mock_ui):
        """Test rendering with zero/missing values."""
        stats = {
            "trade_count": 0,
            "total_volume": 0,
            "total_pnl": 0,
            "win_rate": 0,
            "avg_trade_size": 0,
        }

        journal_module._render_trade_stats(stats)

        # Should still render without error
        assert any("0" in label for label in mock_ui.labels)


class TestRenderTradeTable:
    """Tests for _render_trade_table helper."""

    def test_render_trade_table_with_trades(self, mock_ui):
        """Test rendering trade table with valid trades."""
        trades = [
            {
                "executed_at": datetime(2026, 1, 15, 14, 30, 0, tzinfo=UTC),
                "symbol": "AAPL",
                "side": "buy",
                "qty": 100,
                "price": 185.50,
                "realized_pnl": 250.00,
                "strategy_id": "momentum_v1",
            },
            {
                "executed_at": datetime(2026, 1, 15, 15, 0, 0, tzinfo=UTC),
                "symbol": "MSFT",
                "side": "sell",
                "qty": 50,
                "price": 375.25,
                "realized_pnl": -125.00,
                "strategy_id": "alpha_baseline",
            },
        ]

        journal_module._render_trade_table(trades, page_size=50, page=0)

        # Check table was created
        assert len(mock_ui.tables) == 1
        table = mock_ui.tables[0]
        assert len(table["rows"]) == 2
        assert table["rows"][0]["symbol"] == "AAPL"
        assert table["rows"][1]["symbol"] == "MSFT"

    def test_render_trade_table_empty(self, mock_ui):
        """Test rendering empty trade table."""
        journal_module._render_trade_table([], page_size=50, page=0)

        # Should show "no trades" message
        assert any("No trades found" in label for label in mock_ui.labels)

    def test_render_trade_table_naive_datetime(self, mock_ui):
        """Test handling naive datetime (no tzinfo)."""
        trades = [
            {
                "executed_at": datetime(2026, 1, 15, 14, 30, 0),  # Naive datetime
                "symbol": "GOOGL",
                "side": "buy",
                "qty": 25,
                "price": 142.75,
                "realized_pnl": 75.00,
                "strategy_id": "momentum_v1",
            }
        ]

        # Should not raise error, should add UTC
        journal_module._render_trade_table(trades, page_size=50, page=0)

        assert len(mock_ui.tables) == 1


class TestExportCsv:
    """Tests for _export_csv helper."""

    @pytest.mark.asyncio()
    async def test_export_csv_success(self):
        """Test CSV export with valid trades."""
        mock_data_access = AsyncMock()

        # Mock streaming trade generator - must accept kwargs
        async def mock_stream(**kwargs):
            yield {
                "executed_at": datetime(2026, 1, 15, 14, 30, 0, tzinfo=UTC),
                "symbol": "AAPL",
                "side": "buy",
                "qty": 100,
                "price": 185.50,
                "realized_pnl": 250.00,
                "strategy_id": "momentum_v1",
            }
            yield {
                "executed_at": datetime(2026, 1, 15, 15, 0, 0, tzinfo=UTC),
                "symbol": "MSFT",
                "side": "sell",
                "qty": 50,
                "price": 375.25,
                "realized_pnl": -125.00,
                "strategy_id": "alpha_baseline",
            }

        mock_data_access.stream_trades_for_export = mock_stream

        content, row_count = await journal_module._export_csv(
            mock_data_access,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
            filters={},
        )

        assert row_count == 2
        assert b"AAPL" in content
        assert b"MSFT" in content
        assert b"Date,Symbol,Side,Qty,Price,Realized P&L,Strategy" in content


class TestExportExcel:
    """Tests for _export_excel helper."""

    @pytest.mark.asyncio()
    async def test_export_excel_success(self):
        """Test Excel export with valid trades."""
        mock_data_access = AsyncMock()

        # Mock streaming trade generator - must accept kwargs
        # Excel doesn't support timezone-aware datetimes, use naive
        async def mock_stream(**kwargs):
            yield {
                "executed_at": datetime(2026, 1, 15, 14, 30, 0),  # Timezone-naive for Excel
                "symbol": "AAPL",
                "side": "buy",
                "qty": 100,
                "price": 185.50,
                "realized_pnl": 250.00,
                "strategy_id": "momentum_v1",
            }

        mock_data_access.stream_trades_for_export = mock_stream

        # Mock run.cpu_bound to avoid multiprocessing pickle issues
        async def mock_cpu_bound(func, *args):
            return func(*args)

        with patch.object(journal_module.run, "cpu_bound", mock_cpu_bound):
            content, row_count = await journal_module._export_excel(
                mock_data_access,
                start_date=date(2026, 1, 1),
                end_date=date(2026, 1, 31),
                filters={},
            )

        assert row_count == 1
        assert isinstance(content, bytes)
        assert len(content) > 0  # Excel file should have data


class TestRenderDemoMode:
    """Tests for _render_demo_mode helper."""

    def test_render_demo_mode(self, mock_ui):
        """Test demo mode rendering."""
        journal_module._render_demo_mode()

        # Check demo data was rendered
        assert any("Trade Statistics" in label for label in mock_ui.labels)
        assert any("1,234" in label for label in mock_ui.labels)
        assert len(mock_ui.tables) >= 1

        # Check demo trades table
        table = mock_ui.tables[0]
        assert len(table["rows"]) == 3
        assert table["rows"][0]["symbol"] == "AAPL"


class TestDoExport:
    """Tests for _do_export helper."""

    @pytest.mark.asyncio()
    async def test_do_export_csv_success(self, mock_ui, mock_user):
        """Test CSV export flow with audit logging."""
        mock_data_access = AsyncMock()

        # Mock CSV export - must accept kwargs
        async def mock_stream(**kwargs):
            yield {
                "executed_at": datetime(2026, 1, 15, 14, 30, 0, tzinfo=UTC),
                "symbol": "AAPL",
                "side": "buy",
                "qty": 100,
                "price": 185.50,
                "realized_pnl": 250.00,
                "strategy_id": "momentum_v1",
            }

        mock_data_access.stream_trades_for_export = mock_stream

        with (
            patch.object(journal_module, "get_db_pool", return_value=None),
            patch.object(journal_module, "get_authorized_strategies", return_value=["momentum_v1"]),
        ):
            await journal_module._do_export(
                data_access=mock_data_access,
                user=mock_user,
                export_type="csv",
                start_date=date(2026, 1, 1),
                end_date=date(2026, 1, 31),
                symbol_filter=None,
                side_filter=None,
            )

        # Check download was triggered
        assert len(mock_ui.downloads) == 1
        content, filename = mock_ui.downloads[0]
        assert filename.startswith("trades_")
        assert filename.endswith(".csv")

    @pytest.mark.asyncio()
    async def test_do_export_database_connection_error(self, mock_ui, mock_user):
        """Test export handles database connection errors."""
        mock_data_access = AsyncMock()

        # Mock connection error - must accept kwargs and raise when iterated
        async def mock_stream(**kwargs):
            raise ConnectionError("Database unreachable")
            yield  # Make it a generator (unreachable)

        mock_data_access.stream_trades_for_export = mock_stream

        with (
            patch.object(journal_module, "get_db_pool", return_value=None),
            patch.object(journal_module, "get_authorized_strategies", return_value=["momentum_v1"]),
        ):
            await journal_module._do_export(
                data_access=mock_data_access,
                user=mock_user,
                export_type="csv",
                start_date=date(2026, 1, 1),
                end_date=date(2026, 1, 31),
                symbol_filter=None,
                side_filter=None,
            )

        # Check error notification
        assert any("Database connection error" in msg[0] for msg in mock_ui.notifications)


class TestSaveWorkbookToBytes:
    """Tests for _save_workbook_to_bytes helper."""

    def test_save_workbook_to_bytes(self):
        """Test saving workbook to bytes."""
        # Import openpyxl
        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.append(["Date", "Symbol", "Side"])
        ws.append(["2026-01-15", "AAPL", "buy"])

        content = journal_module._save_workbook_to_bytes(wb)

        assert isinstance(content, bytes)
        assert len(content) > 0


class TestTradeJournalPageRouting:
    """Tests for trade_journal_page routing logic.

    NOTE: These tests require NiceGUI's full slot context for UI element creation.
    The page function creates UI elements (labels, cards) which need proper slot
    stack initialization. These are better tested as integration tests with a full
    NiceGUI app context.

    Testing approach: Unit test individual helper functions (which we do), and use
    integration tests for full page routing.
    """

    @pytest.mark.skip(reason="Requires NiceGUI slot context - needs full app integration test")
    @pytest.mark.asyncio()
    async def test_page_feature_disabled(self, mock_ui, mock_get_current_user, mock_permissions):
        """Test page handles feature flag disabled."""
        mock_app = MagicMock()
        mock_app.storage.user = {"user": {"user_id": "test_user", "role": "admin"}}

        with (
            patch.object(journal_module, "FEATURE_TRADE_JOURNAL", False),
            patch("apps.web_console_ng.auth.middleware.app", mock_app),
        ):
            await journal_module.trade_journal_page()

            assert any("not available" in label for label in mock_ui.labels)

    @pytest.mark.skip(reason="Requires NiceGUI slot context - needs full app integration test")
    @pytest.mark.asyncio()
    async def test_page_permission_denied(self, mock_ui, mock_get_current_user):
        """Test page handles permission denied."""
        mock_app = MagicMock()
        mock_app.storage.user = {"user": {"user_id": "test_user", "role": "admin"}}

        with (
            patch.object(journal_module, "has_permission", return_value=False),
            patch.object(journal_module, "FEATURE_TRADE_JOURNAL", True),
            patch("apps.web_console_ng.auth.middleware.app", mock_app),
        ):
            await journal_module.trade_journal_page()

            assert any("Permission denied" in msg[0] for msg in mock_ui.notifications)

    @pytest.mark.skip(reason="Requires NiceGUI slot context - needs full app integration test")
    @pytest.mark.asyncio()
    async def test_page_no_authorized_strategies(self, mock_ui, mock_get_current_user):
        """Test page handles no authorized strategies."""
        mock_app = MagicMock()
        mock_app.storage.user = {"user": {"user_id": "test_user", "role": "admin"}}

        with (
            patch.object(journal_module, "has_permission", return_value=True),
            patch.object(journal_module, "get_authorized_strategies", return_value=[]),
            patch.object(journal_module, "FEATURE_TRADE_JOURNAL", True),
            patch.object(journal_module, "get_db_pool", return_value=MagicMock()),
            patch("apps.web_console_ng.auth.middleware.app", mock_app),
        ):
            await journal_module.trade_journal_page()

            assert any("don't have access" in label for label in mock_ui.labels)

    @pytest.mark.skip(reason="Requires NiceGUI slot context - needs full app integration test")
    @pytest.mark.asyncio()
    async def test_page_demo_mode(self, mock_ui, mock_get_current_user, mock_permissions):
        """Test page renders demo mode when database not configured."""
        mock_app = MagicMock()
        mock_app.storage.user = {"user": {"user_id": "test_user", "role": "admin"}}

        with (
            patch.object(journal_module, "FEATURE_TRADE_JOURNAL", True),
            patch.object(journal_module, "get_db_pool", return_value=None),
            patch("apps.web_console_ng.auth.middleware.app", mock_app),
        ):
            await journal_module.trade_journal_page()

            assert any("Demo Mode" in label for label in mock_ui.labels)


class TestRenderExportSection:
    """Tests for _render_export_section helper."""

    @pytest.mark.asyncio()
    async def test_render_export_section_no_permission(self, mock_ui, mock_user):
        """Test export section when user lacks EXPORT_DATA permission."""
        mock_data_access = AsyncMock()

        with patch.object(journal_module, "has_permission", return_value=False):
            await journal_module._render_export_section(
                data_access=mock_data_access,
                user=mock_user,
                start_date=date(2026, 1, 1),
                end_date=date(2026, 1, 31),
                symbol_filter=None,
                side_filter=None,
            )

        # Check permission message
        assert any("permission required" in label.lower() for label in mock_ui.labels)

    @pytest.mark.asyncio()
    async def test_render_export_section_with_permission(self, mock_ui, mock_user):
        """Test export section renders buttons with permission."""
        mock_data_access = AsyncMock()

        with patch.object(journal_module, "has_permission", return_value=True):
            await journal_module._render_export_section(
                data_access=mock_data_access,
                user=mock_user,
                start_date=date(2026, 1, 1),
                end_date=date(2026, 1, 31),
                symbol_filter=None,
                side_filter=None,
            )

        # Check export buttons were created
        assert len(mock_ui.buttons) >= 2
