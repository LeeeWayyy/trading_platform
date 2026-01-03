from __future__ import annotations

import types

import pytest

from apps.web_console_ng.components import activity_feed as feed_module


class DummyElement:
    def __init__(self) -> None:
        self._classes: set[str] = set()
        self._children: list[DummyElement] = []
        self.calls: list[tuple[str, object]] = []
        self._deleted: bool = False

    def classes(self, add: str | None = None, remove: str | None = None):
        if remove:
            for cls in remove.split():
                self._classes.discard(cls)
        if add:
            for cls in add.split():
                self._classes.add(cls)
        return self

    def clear(self) -> None:
        self._children.clear()

    def move(self, target_index: int = 0) -> None:
        """Mock move method for NiceGUI element repositioning."""
        pass

    def delete(self) -> None:
        """Mock delete method for NiceGUI element removal."""
        self._deleted = True

    async def run_method(self, method: str, payload: object) -> None:
        self.calls.append((method, payload))

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class DummyLabel(DummyElement):
    def __init__(self, text: str = "") -> None:
        super().__init__()
        self.text = text


@pytest.fixture()
def dummy_ui(monkeypatch: pytest.MonkeyPatch) -> DummyElement:
    card = DummyElement()

    def label(text: str = "") -> DummyLabel:
        return DummyLabel(text)

    def column() -> DummyElement:
        return DummyElement()

    def row() -> DummyElement:
        return DummyElement()

    dummy = types.SimpleNamespace(card=lambda: card, label=label, column=column, row=row)
    monkeypatch.setattr(feed_module, "ui", dummy)
    return card


@pytest.mark.asyncio()
async def test_activity_feed_add_item_and_max_items(dummy_ui: DummyElement) -> None:
    feed = feed_module.ActivityFeed()

    for i in range(feed.MAX_ITEMS + 5):
        await feed.add_item(
            {
                "time": f"2025-01-01T00:00:{i:02d}Z",
                "symbol": "AAPL",
                "side": "buy",
                "qty": i,
                "price": 100 + i,
                "status": "filled",
            }
        )

    assert len(feed.items) == feed.MAX_ITEMS
    assert feed.items[0]["qty"] == feed.MAX_ITEMS + 4

    assert dummy_ui.calls
    method, payload = dummy_ui.calls[-1]
    assert method == "scrollTo"
    assert payload["top"] == 0


def test_activity_feed_malformed_event_handled(dummy_ui: DummyElement, caplog: pytest.LogCaptureFixture) -> None:
    feed = feed_module.ActivityFeed()

    feed._render_item("bad-event", highlight=False)  # type: ignore[arg-type]

    assert any(
        record.message == "activity_feed_malformed_event" for record in caplog.records
    )
