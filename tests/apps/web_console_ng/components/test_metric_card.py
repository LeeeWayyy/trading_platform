from __future__ import annotations

import types

import pytest

from apps.web_console_ng.components import metric_card as metric_module


class DummyLabel:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self._classes: set[str] = set()

    def set_text(self, text: str) -> None:
        self.text = text

    def classes(self, add: str | None = None, remove: str | None = None):
        if remove:
            for cls in remove.split():
                self._classes.discard(cls)
        if add:
            for cls in add.split():
                self._classes.add(cls)
        return self


class DummyContainer:
    def __init__(self) -> None:
        self._classes: set[str] = set()

    def classes(self, add: str | None = None, remove: str | None = None):
        if remove:
            for cls in remove.split():
                self._classes.discard(cls)
        if add:
            for cls in add.split():
                self._classes.add(cls)
        return self

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


@pytest.fixture()
def dummy_ui(monkeypatch: pytest.MonkeyPatch) -> None:
    def card() -> DummyContainer:
        return DummyContainer()

    def label(text: str = "") -> DummyLabel:
        return DummyLabel(text)

    dummy = types.SimpleNamespace(card=card, label=label)
    monkeypatch.setattr(metric_module, "ui", dummy)


def test_metric_card_color_updates_and_cleanup(
    dummy_ui: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(metric_module.time, "time", lambda: 1000.0)

    card = metric_module.MetricCard(
        title="PnL",
        format_fn=lambda v: f"{v:.2f}",
        color_fn=lambda v: "text-green-600" if v >= 0 else "text-red-600",
    )

    card.update(5)
    assert card._value_label is not None
    assert card._value_label.text == "5.00"
    assert "text-green-600" in card._value_label._classes
    assert "text-red-600" not in card._value_label._classes

    card.update(-2)
    assert "text-red-600" in card._value_label._classes
    assert "text-green-600" not in card._value_label._classes


def test_metric_card_stale_detection(dummy_ui: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(metric_module.time, "time", lambda: 1000.0)

    card = metric_module.MetricCard(title="Positions")
    assert card.is_stale() is False

    card.update(1)

    monkeypatch.setattr(metric_module.time, "time", lambda: 1031.0)
    assert card.is_stale(threshold=30.0) is True

    card.mark_stale()
    assert card._value_label is not None
    assert "opacity-50" in card._value_label._classes
