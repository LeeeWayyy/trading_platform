"""Tests for LogTailPanel chronology handling."""

from __future__ import annotations

from apps.web_console_ng.components.log_tail_panel import LogTailPanel


class _FakeLog:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def push(self, line: str) -> None:
        self.lines.append(line)


async def test_add_items_preserves_oldest_to_newest_log_tail_order() -> None:
    """Batch inserts should print older events before newer ones."""
    panel = LogTailPanel()
    fake_log = _FakeLog()
    panel._log = fake_log

    # Input batch is newest->oldest, matching descending bootstrap queries.
    events = [
        {"timestamp": "2026-04-12T10:00:02Z", "status": "filled", "symbol": "AAPL"},
        {"timestamp": "2026-04-12T10:00:01Z", "status": "filled", "symbol": "AAPL"},
    ]

    await panel.add_items(events, highlight=False)

    assert fake_log.lines[0].startswith("10:00:01")
    assert fake_log.lines[1].startswith("10:00:02")
