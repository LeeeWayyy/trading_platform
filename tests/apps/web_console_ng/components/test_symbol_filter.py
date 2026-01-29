from __future__ import annotations

import types

import pytest

from apps.web_console_ng.components import symbol_filter as filter_module


class DummySelect:
    def __init__(self, options, label=None, value=None, **kwargs) -> None:
        self.options = list(options)
        self.label = label
        self.value = value
        self._handlers: list = []
        self._classes: set[str] = set()

    def classes(self, add: str | None = None, remove: str | None = None):
        if remove:
            for cls in remove.split():
                self._classes.discard(cls)
        if add:
            for cls in add.split():
                self._classes.add(cls)
        return self

    def update(self) -> None:
        return None

    def on_value_change(self, handler) -> None:
        self._handlers.append(handler)

    def trigger_change(self, value):
        event = types.SimpleNamespace(value=value)
        for handler in self._handlers:
            handler(event)


@pytest.fixture()
def dummy_ui(monkeypatch: pytest.MonkeyPatch):
    def select(*, options, label=None, value=None, **kwargs):
        return DummySelect(options=options, label=label, value=value, **kwargs)

    dummy = types.SimpleNamespace(select=select)
    monkeypatch.setattr(filter_module, "ui", dummy)


def test_normalize_symbol() -> None:
    assert filter_module.normalize_symbol(None) is None
    assert filter_module.normalize_symbol("") is None
    assert filter_module.normalize_symbol("all") is None
    assert filter_module.normalize_symbol("AAPL") == "AAPL"
    assert filter_module.normalize_symbol(" msft ") == "MSFT"


def test_build_symbol_options() -> None:
    options = filter_module.build_symbol_options(["aapl", "MSFT", "", None, "msft"])
    assert options[0] == filter_module.ALL_SYMBOL_LABEL
    assert "AAPL" in options
    assert "MSFT" in options
    assert len(options) == 3


def test_create_symbol_filter_updates_state(dummy_ui) -> None:
    state = filter_module.create_symbol_filter(["AAPL"], value="msft")
    assert state.value == "MSFT"
    assert state.select is not None
    assert state.select.value == "MSFT"

    state.select.trigger_change("aapl")
    assert state.value == "AAPL"
    assert "AAPL" in state.select.options

    state.select.trigger_change(filter_module.ALL_SYMBOL_LABEL)
    assert state.value is None
    assert state.select.value == filter_module.ALL_SYMBOL_LABEL


def test_symbol_filter_state_updates_options() -> None:
    state = filter_module.SymbolFilterState(value=None, select=None)
    state.update_options(["AAPL"])
    assert state.value is None

    select = DummySelect(options=["All"], label="Symbol", value="All")
    state = filter_module.SymbolFilterState(value=None, select=select)
    state.update_options(["AAPL", "MSFT"])
    assert "AAPL" in select.options
    state.set_value("tsla")
    assert "TSLA" in select.options
    assert select.value == "TSLA"
