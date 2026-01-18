"""Tests for trading layout utilities."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from apps.web_console_ng.ui import trading_layout as layout_module


class _DummyElement:
    def __init__(self, tag: str | None = None, text: str | None = None) -> None:
        self.tag = tag
        self.text = text
        self.class_calls: list[str] = []

    def classes(self, classes: str) -> "_DummyElement":
        self.class_calls.append(classes)
        return self

    def __enter__(self) -> "_DummyElement":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class _DummyUI:
    def __init__(self) -> None:
        self.labels: list[_DummyElement] = []
        self.cards: list[_DummyElement] = []
        self.rows: list[_DummyElement] = []
        self.elements: list[_DummyElement] = []

    def card(self) -> _DummyElement:
        card = _DummyElement(tag="card")
        self.cards.append(card)
        return card

    def label(self, text: str = "") -> _DummyElement:
        label = _DummyElement(tag="label", text=text)
        self.labels.append(label)
        return label

    def element(self, tag: str) -> _DummyElement:
        element = _DummyElement(tag=tag)
        self.elements.append(element)
        return element

    def row(self) -> _DummyElement:
        row = _DummyElement(tag="row")
        self.rows.append(row)
        return row

    def aggrid(self) -> _DummyElement:
        grid = _DummyElement(tag="aggrid")
        return grid


@pytest.fixture()
def dummy_ui(monkeypatch: pytest.MonkeyPatch) -> _DummyUI:
    dummy = _DummyUI()
    monkeypatch.setattr(layout_module, "ui", dummy)
    return dummy


def test_compact_card_without_title(dummy_ui: _DummyUI) -> None:
    card = layout_module.compact_card()

    assert card.tag == "card"
    assert card.class_calls == ["card-compact bg-surface-1"]
    assert dummy_ui.labels == []


def test_compact_card_with_title_adds_label(dummy_ui: _DummyUI) -> None:
    card = layout_module.compact_card("Orders")

    assert card.class_calls == ["card-compact bg-surface-1"]
    assert len(dummy_ui.labels) == 1
    label = dummy_ui.labels[0]
    assert label.text == "Orders"
    assert label.class_calls == [
        "text-xs font-semibold text-text-secondary uppercase",
    ]


def test_trading_grid_creates_dense_container(dummy_ui: _DummyUI) -> None:
    grid = layout_module.trading_grid()

    assert grid.tag == "div"
    assert grid.class_calls == ["grid-dense trading-layout"]


def test_stats_row_has_compact_spacing(dummy_ui: _DummyUI) -> None:
    row = layout_module.stats_row()

    assert row.tag == "row"
    assert row.class_calls == ["gap-2 items-center h-6"]


def test_numeric_label_formats_value(dummy_ui: _DummyUI) -> None:
    label = layout_module.numeric_label(12.5, prefix="$", suffix="k")

    assert label.text == "$12.5k"
    assert label.class_calls == ["font-mono-numbers text-sm"]


def test_apply_compact_grid_options_updates_dict() -> None:
    options = {"existing": True}

    returned = layout_module.apply_compact_grid_options(options)

    assert returned is options
    assert options["rowHeight"] == 22
    assert options["headerHeight"] == 28
    assert options["suppressCellFocus"] is True
    assert options["existing"] is True


def test_apply_compact_grid_classes_adds_class(dummy_ui: _DummyUI) -> None:
    grid = dummy_ui.aggrid()

    returned = layout_module.apply_compact_grid_classes(grid)

    assert returned is grid
    assert grid.class_calls == ["ag-grid-compact"]
