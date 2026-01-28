"""DOM ladder UI component for Level 2 order book."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any

from nicegui import ui

from apps.web_console_ng.components.depth_visualizer import DepthVisualizer
from apps.web_console_ng.core.level2_websocket import Level2WebSocketService

logger = logging.getLogger(__name__)


class DOMLadderComponent:
    """Real-time DOM ladder renderer (client-side via JS)."""

    def __init__(
        self,
        *,
        levels: int = 10,
        large_multiplier: float = 2.0,
    ) -> None:
        self._visualizer = DepthVisualizer(
            levels=levels,
            history_size=200,
            large_multiplier=large_multiplier,
        )
        self._container_id = f"dom_ladder_{uuid.uuid4().hex[:12]}"
        self._symbol_label: ui.label | None = None
        self._status_label: ui.label | None = None
        self._entitled, self._entitlement_reason = Level2WebSocketService.entitlement_status()
        self._min_interval = 1.0 / 30.0
        self._last_emit = 0.0
        self._pending_payload: dict[str, Any] | None = None
        self._flush_handle: asyncio.Handle | None = None

    def is_enabled(self) -> bool:
        return self._entitled

    def _is_mock_mode(self) -> bool:
        """Check if L2 data is synthetic/mock."""
        return "Mock" in self._entitlement_reason

    def create(self) -> ui.card:
        with ui.card().classes("p-3 w-full bg-surface-1") as card:
            header = ui.row().classes("w-full items-center justify-between mb-2")
            with header:
                ui.label("Order Book (L2)").classes("text-sm font-semibold text-text-secondary")
                self._symbol_label = ui.label("--").classes("text-sm font-mono-numbers")

            self._status_label = ui.label("").classes("text-xs text-text-secondary mb-2")
            ui.element("div").classes("dom-ladder").props(f'id="{self._container_id}"')

        if not self._entitled:
            self._set_status(self._entitlement_reason, is_warning=True)
            self.clear("Level 2 data unavailable")
        elif self._is_mock_mode():
            # Surface mock mode warning even when "entitled" so traders know data is synthetic
            self._set_status("⚠️ MOCK DATA - Select a symbol", is_warning=True)
        else:
            self._set_status("Select a symbol to view depth", is_warning=False)

        return card

    def set_symbol(self, symbol: str | None) -> None:
        if self._symbol_label is not None:
            self._symbol_label.text = symbol or "--"
        if not self._entitled:
            self.clear("Level 2 data unavailable")
            return
        if symbol:
            if self._is_mock_mode():
                self._set_status(f"⚠️ MOCK DATA - {symbol}", is_warning=True)
            else:
                self._set_status(f"Streaming {symbol}", is_warning=False)
            self.clear("Waiting for depth updates")
        else:
            if self._is_mock_mode():
                self._set_status("⚠️ MOCK DATA - Select a symbol", is_warning=True)
            else:
                self._set_status("Select a symbol to view depth", is_warning=False)
            self.clear("No symbol selected")

    def handle_orderbook_update(self, data: dict[str, Any]) -> None:
        if not self._entitled:
            return
        payload = self._visualizer.build_payload(data)
        if payload is None:
            return
        self._schedule_emit(payload)

    def clear(self, message: str) -> None:
        try:
            ui.run_javascript(
                f"window.DOMLadder.clear('{self._container_id}', {json.dumps(message)})"
            )
        except Exception as exc:
            logger.debug("dom_ladder_clear_failed", extra={"error": str(exc)})

    def dispose(self) -> None:
        if self._flush_handle is not None:
            self._flush_handle.cancel()
            self._flush_handle = None

    def _set_status(self, message: str, *, is_warning: bool) -> None:
        if self._status_label is None:
            return
        self._status_label.text = message
        if is_warning:
            self._status_label.classes(add="text-warning", remove="text-text-secondary")
        else:
            self._status_label.classes(add="text-text-secondary", remove="text-warning")

    def _schedule_emit(self, payload: dict[str, Any]) -> None:
        now = time.time()
        elapsed = now - self._last_emit
        if elapsed >= self._min_interval:
            self._emit(payload)
            return

        self._pending_payload = payload
        if self._flush_handle is not None:
            return

        delay = max(0.0, self._min_interval - elapsed)
        loop = asyncio.get_running_loop()
        self._flush_handle = loop.call_later(delay, self._flush_pending)

    def _flush_pending(self) -> None:
        self._flush_handle = None
        if self._pending_payload is None:
            return
        payload = self._pending_payload
        self._pending_payload = None
        self._emit(payload)

    def _emit(self, payload: dict[str, Any]) -> None:
        self._last_emit = time.time()
        try:
            ui.run_javascript(
                f"window.DOMLadder.update('{self._container_id}', {json.dumps(payload)})"
            )
        except Exception as exc:
            logger.debug("dom_ladder_update_failed", extra={"error": str(exc)})
