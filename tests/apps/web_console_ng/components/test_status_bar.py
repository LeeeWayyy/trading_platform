"""Unit tests for StatusBar component."""

from __future__ import annotations

import types
from typing import Any

import pytest

from apps.web_console_ng.components import status_bar as status_bar_module
from apps.web_console_ng.components.status_bar import StatusBar


class DummyLabel:
    """Mock NiceGUI label for testing."""

    def __init__(self, text: str = "") -> None:
        self.text = text

    def set_text(self, text: str) -> None:
        self.text = text

    def classes(self, _add: str | None = None, _remove: str | None = None) -> DummyLabel:
        return self


class DummyElement:
    """Mock NiceGUI element for testing."""

    def __init__(self) -> None:
        self._classes: set[str] = set()

    def classes(self, add: str | None = None, remove: str | None = None) -> DummyElement:
        if remove:
            for cls in remove.split():
                self._classes.discard(cls)
        if add:
            for cls in add.split():
                self._classes.add(cls)
        return self

    def __enter__(self) -> DummyElement:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        pass


@pytest.fixture()
def dummy_ui(monkeypatch: pytest.MonkeyPatch) -> None:
    def element(_tag: str) -> DummyElement:
        return DummyElement()

    def label(text: str) -> DummyLabel:
        return DummyLabel(text)

    dummy = types.SimpleNamespace(element=element, label=label)
    monkeypatch.setattr(status_bar_module, "ui", dummy)


def test_status_bar_engaged(dummy_ui: None) -> None:
    bar = StatusBar()
    bar.update_state("ENGAGED")
    assert bar._label is not None
    assert bar._label.text == "TRADING HALTED"


def test_status_bar_disengaged(dummy_ui: None) -> None:
    bar = StatusBar()
    bar.update_state("DISENGAGED")
    assert bar._label is not None
    assert bar._label.text == "TRADING ACTIVE"


def test_status_bar_unknown(dummy_ui: None) -> None:
    bar = StatusBar()
    bar.update_state("UNKNOWN")
    assert bar._label is not None
    assert "UNKNOWN" in bar._label.text
