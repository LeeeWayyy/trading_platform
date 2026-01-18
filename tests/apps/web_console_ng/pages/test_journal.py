from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from apps.web_console_ng.pages import journal as journal_module


class DummyElement:
    def __init__(self, *, text: str | None = None, value: Any = None) -> None:
        self.text = text or ""
        self.value = value
        self.visible = True
        self.on_click_cb = None
        self.on_value_change_cb = None

    def __enter__(self) -> "DummyElement":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def classes(self, *args, **kwargs) -> "DummyElement":
        return self

    def props(self, *args, **kwargs) -> "DummyElement":
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


class DummyUI:
    def __init__(self) -> None:
        self.labels: list[str] = []
        self.tables: list[dict[str, Any]] = []
        self.buttons: list[DummyElement] = []
        self.notifications: list[tuple[str, str | None]] = []
        self.downloads: list[tuple[bytes, str]] = []

    def card(self, *args, **kwargs) -> DummyElement:
        return DummyElement()

    def row(self, *args, **kwargs) -> DummyElement:
        return DummyElement()

    def column(self, *args, **kwargs) -> DummyElement:
        return DummyElement()

    def label(self, text: str = "", *args, **kwargs) -> DummyElement:
        self.labels.append(text)
        return DummyElement(text=text)

    def table(self, *, columns: list[dict[str, Any]], rows: list[dict[str, Any]], **kwargs) -> DummyElement:
        self.tables.append({"columns": columns, "rows": rows})
        return DummyElement()

    def button(self, text: str = "", icon: str | None = None, on_click=None, **kwargs) -> DummyElement:
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

    def link(self, *args, **kwargs) -> DummyElement:
        return DummyElement()

    def separator(self, *args, **kwargs) -> DummyElement:
        return DummyElement()


@pytest.fixture()
def dummy_ui(monkeypatch: pytest.MonkeyPatch) -> DummyUI:
    ui = DummyUI()
    monkeypatch.setattr(journal_module, "ui", ui)
    return ui


@pytest.mark.asyncio()
async def test_render_trade_table_empty_shows_message(dummy_ui: DummyUI) -> None:
    journal_module._render_trade_table([], page_size=50, page=0)

    assert "No trades found matching filters." in dummy_ui.labels


@pytest.mark.asyncio()
async def test_render_trade_table_formats_naive_datetime(dummy_ui: DummyUI) -> None:
    trade = {
        "executed_at": datetime(2026, 1, 1, 12, 0, 0),
        "symbol": "AAPL",
        "side": "buy",
        "qty": 10,
        "price": 101.25,
        "realized_pnl": 5.0,
        "strategy_id": "alpha",
    }

    journal_module._render_trade_table([trade], page_size=50, page=0)

    assert dummy_ui.tables, "Expected trade table to be rendered"
    rows = dummy_ui.tables[0]["rows"]
    assert rows[0]["date"].endswith("Z")
    assert rows[0]["date"].startswith("2026-01-01 12:00:00")


@pytest.mark.asyncio()
async def test_export_csv_streams_rows() -> None:
    trades = [
        {"executed_at": "2026-01-01", "symbol": "AAPL", "side": "buy", "qty": 1, "price": 10, "realized_pnl": 2, "strategy_id": "s1"},
        {"executed_at": "2026-01-02", "symbol": "MSFT", "side": "sell", "qty": 2, "price": 20, "realized_pnl": -1, "strategy_id": "s2"},
    ]

    class DummyAccess:
        async def stream_trades_for_export(self, **kwargs):
            for trade in trades:
                yield trade

    content, row_count = await journal_module._export_csv(
        DummyAccess(), date(2026, 1, 1), date(2026, 1, 2), {}
    )

    assert row_count == 2
    text = content.decode("utf-8")
    assert "Date,Symbol,Side,Qty,Price,Realized P&L,Strategy" in text
    assert "AAPL" in text
    assert "MSFT" in text


@pytest.mark.asyncio()
async def test_do_export_csv_success_calls_download(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_export_csv(*args, **kwargs):
        return b"csv-bytes", 3

    monkeypatch.setattr(journal_module, "_export_csv", fake_export_csv)
    monkeypatch.setattr(journal_module, "get_db_pool", lambda: None)
    monkeypatch.setattr(journal_module, "get_authorized_strategies", lambda user: ["s1"])

    user = {"user_id": "u1"}

    await journal_module._do_export(
        data_access=SimpleNamespace(),
        user=user,
        export_type="csv",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 2),
        symbol_filter=None,
        side_filter=None,
    )

    assert dummy_ui.downloads
    content, filename = dummy_ui.downloads[0]
    assert content == b"csv-bytes"
    assert filename == "trades_2026-01-01_2026-01-02.csv"
    assert any("Exported 3 trades" in msg for msg, _ in dummy_ui.notifications)


@pytest.mark.asyncio()
async def test_render_export_section_wires_buttons(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(journal_module, "has_permission", lambda user, perm: True)
    do_export = AsyncMock()
    monkeypatch.setattr(journal_module, "_do_export", do_export)

    await journal_module._render_export_section(
        data_access=SimpleNamespace(),
        user={"user_id": "u1"},
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 2),
        symbol_filter="AAPL",
        side_filter="buy",
    )

    button_texts = [btn.text for btn in dummy_ui.buttons]
    assert "Export to CSV" in button_texts
    assert "Export to Excel" in button_texts

    csv_button = next(btn for btn in dummy_ui.buttons if btn.text == "Export to CSV")
    assert csv_button.on_click_cb is not None
    await csv_button.on_click_cb()

    do_export.assert_awaited()


@pytest.mark.asyncio()
async def test_render_export_section_permission_denied(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(journal_module, "has_permission", lambda user, perm: False)

    await journal_module._render_export_section(
        data_access=SimpleNamespace(),
        user={"user_id": "u1"},
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 2),
        symbol_filter=None,
        side_filter=None,
    )

    assert "Export permission required. Contact administrator." in dummy_ui.labels
