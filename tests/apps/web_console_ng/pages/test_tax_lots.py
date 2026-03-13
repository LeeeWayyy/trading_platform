"""Tests for tax lots page (P6T16.1)."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest

import apps.web_console_ng.pages.tax_lots as tax_lots_module

# =============================================================================
# Dummy UI stubs for page-level tests
# =============================================================================


class _DummyElement:
    def __init__(self, kind: str, **kwargs: Any) -> None:
        self.kind = kind
        self.kwargs = kwargs
        self.value = kwargs.get("value")
        self._on_change: Callable[..., Any] | None = kwargs.get("on_change")

    def __enter__(self) -> _DummyElement:
        return self

    def __exit__(self, *a: Any) -> bool:
        return False

    def classes(self, *_: Any, **__: Any) -> _DummyElement:
        return self

    def props(self, *_: Any, **__: Any) -> _DummyElement:
        return self

    def refresh(self) -> None:
        pass

    def open(self) -> None:
        pass

    def close(self) -> None:
        pass


class _DummyUI:
    def __init__(self) -> None:
        self.labels: list[_DummyElement] = []
        self.notifications: list[dict[str, Any]] = []
        self.buttons: list[_DummyElement] = []
        self.downloads: list[tuple[Any, str]] = []

    def label(self, text: str = "") -> _DummyElement:
        el = _DummyElement("label", text=text)
        self.labels.append(el)
        return el

    def button(
        self,
        label: str = "",
        on_click: Callable[..., Any] | None = None,
        icon: str | None = None,
    ) -> _DummyElement:
        el = _DummyElement("button", label=label)
        el._on_click = on_click  # type: ignore[attr-defined]
        self.buttons.append(el)
        return el

    def notify(self, text: str, type: str | None = None) -> None:
        self.notifications.append({"text": text, "type": type})

    def download(self, data: Any, filename: str) -> None:
        self.downloads.append((data, filename))

    def row(self) -> _DummyElement:
        return _DummyElement("row")

    def column(self) -> _DummyElement:
        return _DummyElement("column")

    def card(self) -> _DummyElement:
        return _DummyElement("card")

    def separator(self) -> _DummyElement:
        return _DummyElement("separator")

    def icon(self, name: str) -> _DummyElement:
        return _DummyElement("icon")

    def dialog(self) -> _DummyElement:
        return _DummyElement("dialog")

    def table(self, **kwargs: Any) -> _DummyElement:
        return _DummyElement("table", **kwargs)

    def switch(self, label: str = "", **kwargs: Any) -> _DummyElement:
        return _DummyElement("switch", label=label, **kwargs)

    def select(self, **kwargs: Any) -> _DummyElement:
        return _DummyElement("select", **kwargs)

    def refreshable(self, fn: Callable[..., Any]) -> Callable[..., Any]:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return fn(*args, **kwargs)

        wrapper.refresh = lambda: None  # type: ignore[attr-defined]
        return wrapper


_raw_page = inspect.unwrap(tax_lots_module.tax_lots_page)


# =============================================================================
# Tests — pure helper functions (no NiceGUI needed)
# =============================================================================


def test_rows_to_report_rows_basic() -> None:
    """_rows_to_report_rows maps DB dict to TaxReportRow."""
    from datetime import UTC, datetime

    rows = [
        {
            "symbol": "AAPL",
            "acquired_at": datetime(2025, 1, 1, tzinfo=UTC),
            "disposed_at": datetime(2025, 6, 1, tzinfo=UTC),
            "quantity": Decimal("50"),
            "cost_basis": Decimal("7500"),
            "total_proceeds": Decimal("8000"),
            "realized_gain_loss": Decimal("500"),
            "holding_period": "short",
            "wash_sale_disallowed": None,
        }
    ]
    result = tax_lots_module._rows_to_report_rows(rows)
    assert len(result) == 1
    assert result[0].symbol == "AAPL"
    assert result[0].gain_loss == Decimal("500")


def test_rows_to_report_rows_with_wash_sale() -> None:
    """Wash sale disallowed amount is preserved."""
    from datetime import UTC, datetime

    rows = [
        {
            "symbol": "TSLA",
            "acquired_at": datetime(2025, 3, 1, tzinfo=UTC),
            "disposed_at": datetime(2025, 4, 1, tzinfo=UTC),
            "quantity": Decimal("10"),
            "cost_basis": Decimal("2000"),
            "total_proceeds": Decimal("1800"),
            "realized_gain_loss": Decimal("-200"),
            "holding_period": "short",
            "wash_sale_disallowed": Decimal("150"),
        }
    ]
    result = tax_lots_module._rows_to_report_rows(rows)
    assert result[0].wash_sale_adjustment == Decimal("150")


@pytest.mark.asyncio()
async def test_fetch_wash_sale_lot_ids_empty_lots() -> None:
    """Empty lots list returns empty set."""
    result = await tax_lots_module._fetch_wash_sale_lot_ids(None, [])
    assert result == set()


@pytest.mark.asyncio()
async def test_fetch_current_prices_empty_lots() -> None:
    """No lots returns empty price dict."""
    result = await tax_lots_module._fetch_current_prices(
        [], {"user_id": "u1", "role": "admin"}
    )
    assert result == {}


def test_cost_basis_methods_defined() -> None:
    """Cost basis methods constant contains expected values."""
    assert "fifo" in tax_lots_module._COST_BASIS_METHODS
    assert "lifo" in tax_lots_module._COST_BASIS_METHODS
    assert "specific_id" in tax_lots_module._COST_BASIS_METHODS


def test_normalize_holding_period_short() -> None:
    """'short' normalizes to 'short_term'."""
    assert tax_lots_module._normalize_holding_period("short") == "short_term"


def test_normalize_holding_period_long() -> None:
    """'long' normalizes to 'long_term'."""
    assert tax_lots_module._normalize_holding_period("long") == "long_term"


def test_normalize_holding_period_passthrough() -> None:
    """Already-normalized values pass through unchanged."""
    assert tax_lots_module._normalize_holding_period("short_term") == "short_term"
    assert tax_lots_module._normalize_holding_period("long_term") == "long_term"


def test_normalize_holding_period_none() -> None:
    """None defaults to 'short_term'."""
    assert tax_lots_module._normalize_holding_period(None) == "short_term"


# =============================================================================
# Page-level permission flow tests
# =============================================================================


@pytest.fixture()
def dummy_ui(monkeypatch: pytest.MonkeyPatch) -> _DummyUI:
    dui = _DummyUI()
    monkeypatch.setattr(tax_lots_module, "ui", dui)
    return dui


@pytest.mark.asyncio()
async def test_page_permission_denied(
    dummy_ui: _DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-authorized user sees permission denied label and early return."""
    monkeypatch.setattr(
        tax_lots_module, "get_current_user", lambda: {"user_id": "u1", "role": "viewer"}
    )
    monkeypatch.setattr(tax_lots_module, "has_permission", lambda user, perm: False)

    await _raw_page()

    label_texts = [el.kwargs.get("text", "") for el in dummy_ui.labels]
    assert any("Permission denied" in t for t in label_texts)
    assert len(dummy_ui.buttons) == 0


@pytest.mark.asyncio()
async def test_page_db_unavailable(
    dummy_ui: _DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When DB pool is None, page shows 'Database unavailable'."""
    monkeypatch.setattr(
        tax_lots_module, "get_current_user", lambda: {"user_id": "u1", "role": "admin"}
    )
    monkeypatch.setattr(tax_lots_module, "has_permission", lambda user, perm: True)
    monkeypatch.setattr(tax_lots_module, "get_db_pool", lambda: None)

    await _raw_page()

    label_texts = [el.kwargs.get("text", "") for el in dummy_ui.labels]
    assert any("Database unavailable" in t for t in label_texts)


@pytest.mark.asyncio()
async def test_form_8949_export_permission_denied(
    dummy_ui: _DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_handle_form_8949_export denies when user lacks EXPORT_DATA."""
    monkeypatch.setattr(
        tax_lots_module, "get_current_user", lambda: {"user_id": "u1", "role": "viewer"}
    )
    monkeypatch.setattr(tax_lots_module, "has_permission", lambda user, perm: False)

    await tax_lots_module._handle_form_8949_export(object(), {"user_id": "u1"})

    assert any("Permission denied" in n["text"] for n in dummy_ui.notifications)


@pytest.mark.asyncio()
async def test_form_8949_preview_permission_denied(
    dummy_ui: _DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_handle_form_8949_preview denies when user lacks VIEW_TAX_REPORTS."""
    monkeypatch.setattr(
        tax_lots_module, "get_current_user", lambda: {"user_id": "u1", "role": "viewer"}
    )
    monkeypatch.setattr(tax_lots_module, "has_permission", lambda user, perm: False)

    await tax_lots_module._handle_form_8949_preview(object(), {"user_id": "u1"})

    assert any("Permission denied" in n["text"] for n in dummy_ui.notifications)
