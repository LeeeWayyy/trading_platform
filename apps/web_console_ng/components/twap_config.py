"""TWAP configuration panel for manual order entry."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo

from nicegui import ui

TWAP_MIN_DURATION_MINUTES = 5
TWAP_MAX_DURATION_MINUTES = 480
TWAP_MIN_INTERVAL_SECONDS = 30
TWAP_MAX_INTERVAL_SECONDS = 300


@dataclass
class TWAPConfigState:
    """Current TWAP input state."""

    duration_minutes: int | None
    interval_seconds: int | None
    start_time: datetime | None
    start_time_error: str | None
    notional_acknowledged: bool
    start_time_enabled: bool


class TWAPConfig:
    """Component for TWAP configuration + preview display."""

    def __init__(
        self, *, on_change: Callable[[], None], on_ack_change: Callable[[bool], None]
    ) -> None:
        self._on_change = on_change
        self._on_ack_change = on_ack_change

        self._container: ui.column | None = None
        self._duration_input: ui.number | None = None
        self._interval_input: ui.number | None = None
        self._start_toggle: ui.checkbox | None = None
        self._start_date_input: ui.date | None = None
        self._start_time_input: ui.input | None = None
        self._start_time_row: ui.row | None = None
        self._start_time_error: ui.label | None = None

        self._preview_details: ui.column | None = None
        self._preview_errors: ui.column | None = None
        self._preview_warning: ui.label | None = None

        self._notional_warning: ui.label | None = None
        self._notional_ack: ui.checkbox | None = None

    def create(self) -> ui.column:
        """Create TWAP config UI."""
        with ui.column().classes("w-full p-3 border rounded bg-slate-900/30") as container:
            ui.label("TWAP Configuration").classes("text-sm font-semibold")

            with ui.row().classes("w-full gap-4"):
                self._duration_input = ui.number(
                    "Duration (min)",
                    value=30,
                    min=TWAP_MIN_DURATION_MINUTES,
                    max=TWAP_MAX_DURATION_MINUTES,
                    step=1,
                    format="%d",
                ).classes("w-40")
                self._interval_input = ui.number(
                    "Interval (sec)",
                    value=60,
                    min=TWAP_MIN_INTERVAL_SECONDS,
                    max=TWAP_MAX_INTERVAL_SECONDS,
                    step=1,
                    format="%d",
                ).classes("w-40")

            if self._duration_input:
                self._duration_input.on_value_change(lambda _e: self._on_change())
            if self._interval_input:
                self._interval_input.on_value_change(lambda _e: self._on_change())

            self._start_toggle = ui.checkbox("Schedule start time", value=False)
            self._start_toggle.on_value_change(lambda _e: self._toggle_start_time())

            with ui.row().classes("w-full gap-4") as start_row:
                self._start_date_input = ui.date(value=str(date.today())).classes("w-40")
                self._start_time_input = ui.input(
                    "Start Time (HH:MM)",
                    placeholder="09:30",
                ).classes("w-40")
            self._start_time_row = start_row
            self._start_time_row.set_visibility(False)

            self._start_time_error = ui.label("").classes("text-xs text-red-500 hidden")

            ui.label("Preview").classes("text-xs font-semibold mt-2")
            self._preview_warning = ui.label("").classes("text-xs text-amber-600 hidden")
            self._preview_errors = ui.column().classes("gap-1")
            self._preview_details = ui.column().classes("gap-1")

            self._notional_warning = ui.label("").classes("text-xs text-amber-600 hidden")
            self._notional_ack = ui.checkbox(
                "I understand this order may fail notional validation",
                value=False,
            ).classes("text-xs hidden")
            self._notional_ack.on_value_change(
                lambda e: self._on_ack_change(bool(e.value))
            )

        self._container = container
        return container

    def set_visibility(self, visible: bool) -> None:
        if self._container:
            self._container.set_visibility(visible)

    def _toggle_start_time(self) -> None:
        enabled = bool(self._start_toggle.value) if self._start_toggle else False
        if self._start_time_row:
            self._start_time_row.set_visibility(enabled)
        if self._start_time_error:
            self._start_time_error.set_text("")
            self._start_time_error.classes(add="hidden")
        self._on_change()

    def set_preview(self, preview: dict[str, Any] | None) -> None:
        if self._preview_details is None or self._preview_warning is None:
            return

        if hasattr(self._preview_details, "clear"):
            self._preview_details.clear()

        if preview is None:
            self._preview_warning.set_text("Preview unavailable")
            self._preview_warning.classes(remove="hidden")
            return

        self._preview_warning.set_text("")
        self._preview_warning.classes(add="hidden")

        slice_count = preview.get("slice_count")
        base_slice_qty = preview.get("base_slice_qty")
        first_slice_at = preview.get("first_slice_at")
        last_slice_at = preview.get("last_slice_at")

        with self._preview_details:
            ui.label(f"Slices: {slice_count} slices of ~{base_slice_qty} shares").classes(
                "text-xs"
            )
            ui.label(f"First: {first_slice_at}").classes("text-xs")
            ui.label(f"Last: {last_slice_at}").classes("text-xs")

        market_warning = preview.get("market_hours_warning")
        if market_warning:
            self._preview_warning.set_text(str(market_warning))
            self._preview_warning.classes(remove="hidden")

    def set_preview_errors(self, errors: list[str] | None) -> None:
        if self._preview_errors is None:
            return

        if hasattr(self._preview_errors, "clear"):
            self._preview_errors.clear()

        if not errors:
            return

        with self._preview_errors:
            for error in errors:
                ui.label(f"ERROR: {error}").classes("text-xs text-red-500")

    def set_notional_warning(self, warning: str | None) -> None:
        if self._notional_warning is None or self._notional_ack is None:
            return

        if warning:
            self._notional_warning.set_text(warning)
            self._notional_warning.classes(remove="hidden")
            self._notional_ack.classes(remove="hidden")
        else:
            self._notional_warning.set_text("")
            self._notional_warning.classes(add="hidden")
            self._notional_ack.value = False
            self._notional_ack.classes(add="hidden")
            self._on_ack_change(False)

    def get_state(self, timezone_name: str) -> TWAPConfigState:
        duration = int(self._duration_input.value) if self._duration_input else None
        interval = int(self._interval_input.value) if self._interval_input else None

        start_time_enabled = bool(self._start_toggle.value) if self._start_toggle else False
        start_time, start_error = self._parse_start_time(timezone_name) if start_time_enabled else (None, None)

        notional_ack = bool(self._notional_ack.value) if self._notional_ack else False

        return TWAPConfigState(
            duration_minutes=duration,
            interval_seconds=interval,
            start_time=start_time,
            start_time_error=start_error,
            notional_acknowledged=notional_ack,
            start_time_enabled=start_time_enabled,
        )

    def _parse_start_time(self, timezone_name: str) -> tuple[datetime | None, str | None]:
        if not self._start_date_input or not self._start_time_input:
            return None, None

        raw_date = str(self._start_date_input.value or "").strip()
        raw_time = str(self._start_time_input.value or "").strip()

        if not raw_date or not raw_time:
            return None, "Start date and time are required"

        try:
            parsed_date = date.fromisoformat(raw_date)
        except ValueError:
            return None, "Start date format is invalid"

        try:
            parsed_time = datetime.strptime(raw_time, "%H:%M").time()
        except ValueError:
            return None, "Start time must be HH:MM"

        try:
            tz = ZoneInfo(timezone_name)
        except Exception:
            tz = UTC

        local_dt = datetime.combine(parsed_date, parsed_time).replace(tzinfo=tz)
        start_time = local_dt.astimezone(UTC)
        return start_time, None

    def set_start_time_error(self, message: str | None) -> None:
        if self._start_time_error is None:
            return
        if message:
            self._start_time_error.set_text(message)
            self._start_time_error.classes(remove="hidden")
        else:
            self._start_time_error.set_text("")
            self._start_time_error.classes(add="hidden")
