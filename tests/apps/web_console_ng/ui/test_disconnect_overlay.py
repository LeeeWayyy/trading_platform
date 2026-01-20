"""Tests for disconnect overlay injection."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from apps.web_console_ng.ui import disconnect_overlay as overlay_module


@pytest.fixture(autouse=True)
def reset_injected_flag() -> None:
    overlay_module._injected = False
    yield
    overlay_module._injected = False


@pytest.fixture()
def dummy_ui(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    dummy = SimpleNamespace(add_head_html=MagicMock())
    monkeypatch.setattr(overlay_module, "ui", dummy)
    return dummy


def test_inject_disconnect_overlay_reads_and_injects_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dummy_ui: SimpleNamespace
) -> None:
    script = "console.log('overlay');"
    js_path = tmp_path / "disconnect_overlay.js"
    js_path.write_text(script, encoding="utf-8")
    monkeypatch.setattr(overlay_module, "_JS_PATH", js_path)

    overlay_module.inject_disconnect_overlay()
    overlay_module.inject_disconnect_overlay()

    dummy_ui.add_head_html.assert_called_once_with(f"<script>{script}</script>")


def test_inject_disconnect_overlay_noop_when_already_injected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dummy_ui: SimpleNamespace
) -> None:
    monkeypatch.setattr(overlay_module, "_injected", True)
    js_path = tmp_path / "disconnect_overlay.js"
    js_path.write_text("ignored", encoding="utf-8")
    monkeypatch.setattr(overlay_module, "_JS_PATH", js_path)

    overlay_module.inject_disconnect_overlay()

    dummy_ui.add_head_html.assert_not_called()
