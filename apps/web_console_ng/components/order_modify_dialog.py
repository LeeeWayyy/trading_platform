"""Order modification dialog for the NiceGUI trading console."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from typing import Any

from nicegui import ui

from apps.web_console_ng.core.client import AsyncTradingClient

logger = logging.getLogger(__name__)


class OrderModifyDialog:
    """Dialog to modify working orders via atomic replace."""

    def __init__(
        self,
        *,
        trading_client: AsyncTradingClient,
        user_id: str,
        user_role: str,
        on_success: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self._client = trading_client
        self._user_id = user_id
        self._user_role = user_role
        self._on_success = on_success

        self._order: dict[str, Any] | None = None
        self._dialog = ui.dialog()
        self._submit_btn: ui.button | None = None
        self._qty_input: ui.number | None = None
        self._limit_input: ui.number | None = None
        self._stop_input: ui.number | None = None
        self._tif_select: ui.select | None = None
        self._reason_input: ui.textarea | None = None
        self._status_label: ui.label | None = None

        with self._dialog:
            with ui.card().classes("p-6 min-w-[420px]"):
                ui.label("Modify Order").classes("text-xl font-bold mb-3")
                self._status_label = ui.label("").classes("text-sm text-gray-500 mb-2")

                self._qty_input = ui.number(
                    "New Total Quantity",
                    value=None,
                    min=1,
                    step=1,
                    format="%d",
                ).classes("w-full mb-2")

                self._limit_input = ui.number(
                    "New Limit Price",
                    value=None,
                    min=0.01,
                    step=0.01,
                    format="%.2f",
                ).classes("w-full mb-2")

                self._stop_input = ui.number(
                    "New Stop Price",
                    value=None,
                    min=0.01,
                    step=0.01,
                    format="%.2f",
                ).classes("w-full mb-2")

                self._tif_select = ui.select(
                    options=["day", "gtc"],
                    label="Time in Force",
                    value=None,
                ).classes("w-full mb-2")

                self._reason_input = ui.textarea(
                    "Reason (optional)",
                    placeholder="Why are you modifying this order?",
                ).classes("w-full mb-2")

                ui.label(
                    "Note: Modifications use Alpaca's atomic replace (cancel+replace)."
                ).classes("text-xs text-gray-500 mb-3")

                with ui.row().classes("gap-3 justify-end"):
                    self._submit_btn = ui.button(
                        "Submit Modification",
                        on_click=self._handle_submit,
                        color="primary",
                    )
                    ui.button("Cancel", on_click=self._dialog.close)

    def open(self, order: dict[str, Any]) -> None:
        self._order = order
        if self._status_label:
            symbol = order.get("symbol", "")
            status = order.get("status", "")
            self._status_label.set_text(f"{symbol} · {status}")

        if self._qty_input:
            self._qty_input.value = order.get("qty")
        if self._limit_input:
            self._limit_input.value = order.get("limit_price")
        if self._stop_input:
            self._stop_input.value = order.get("stop_price")
        if self._tif_select:
            self._tif_select.value = order.get("time_in_force")
        if self._reason_input:
            self._reason_input.value = ""

        self._apply_field_visibility(order)
        self._dialog.open()

    def _apply_field_visibility(self, order: dict[str, Any]) -> None:
        order_type = str(order.get("order_type") or order.get("type") or "").lower()
        show_limit = order_type in ("limit", "stop_limit")
        show_stop = order_type in ("stop", "stop_limit")
        if self._limit_input:
            self._limit_input.set_visibility(show_limit)
        if self._stop_input:
            self._stop_input.set_visibility(show_stop)

    async def _handle_submit(self) -> None:
        if not self._order:
            return
        if not self._submit_btn:
            return

        self._submit_btn.disable()

        order_id = str(self._order.get("client_order_id") or "").strip()
        if not order_id:
            ui.notify("Cannot modify order: missing client_order_id", type="negative")
            self._submit_btn.enable()
            return

        payload: dict[str, Any] = {"idempotency_key": str(uuid.uuid4())}
        changes = 0

        if self._qty_input and self._qty_input.value is not None:
            try:
                qty_value = int(self._qty_input.value)
            except (TypeError, ValueError):
                ui.notify("Quantity must be a whole number", type="negative")
                self._submit_btn.enable()
                return
            payload["qty"] = qty_value
            if qty_value != int(self._order.get("qty") or 0):
                changes += 1

        if self._limit_input and self._limit_input.value is not None:
            payload["limit_price"] = float(self._limit_input.value)
            if payload["limit_price"] != self._order.get("limit_price"):
                changes += 1

        if self._stop_input and self._stop_input.value is not None:
            payload["stop_price"] = float(self._stop_input.value)
            if payload["stop_price"] != self._order.get("stop_price"):
                changes += 1

        if self._tif_select and self._tif_select.value:
            payload["time_in_force"] = self._tif_select.value
            if payload["time_in_force"] != self._order.get("time_in_force"):
                changes += 1

        if self._reason_input and self._reason_input.value:
            payload["reason"] = str(self._reason_input.value).strip()

        if changes == 0:
            ui.notify("No changes detected for modification", type="warning")
            self._submit_btn.enable()
            return

        try:
            response = await self._client.modify_order(
                order_id,
                payload,
                user_id=self._user_id,
                role=self._user_role,
            )
            ui.notify("Order modified", type="positive")
            if self._on_success:
                self._on_success(response)
            self._dialog.close()
        except ValueError:
            ui.notify("Invalid price value", type="negative")
        except Exception as exc:  # httpx errors are already logged by client
            logger.warning("modify_order_failed", extra={"error": str(exc)})
            ui.notify("Modify failed - see logs", type="negative")
        finally:
            self._submit_btn.enable()


__all__ = ["OrderModifyDialog"]
