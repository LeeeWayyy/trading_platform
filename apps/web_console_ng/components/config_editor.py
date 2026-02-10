"""JSON Config Editor for backtest form (P6T12.1).

Provides an "Advanced Mode" toggle that replaces the standard form with a
JSON editor (``ui.codemirror``).  Power users can submit backtest
configurations as raw JSON, which is validated via
``BacktestJobConfig.from_dict()`` before enqueueing.

The editor maintains a single source of truth with the form:

* Form → JSON: serialize current field values via ``form_state_to_json``.
* JSON → Form: parse JSON and populate field values via ``json_to_form_state``.

Provider values use display labels in the form (e.g. "CRSP (production)")
but enum values in JSON (e.g. "crsp").  The ``PROVIDER_DISPLAY`` dict
handles bidirectional mapping.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from nicegui import ui

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Provider display ↔ enum mapping
# ---------------------------------------------------------------------------
PROVIDER_DISPLAY: dict[str, str] = {
    "crsp": "CRSP (production)",
    "yfinance": "Yahoo Finance (dev only)",
}
PROVIDER_DISPLAY_INVERSE: dict[str, str] = {v: k for k, v in PROVIDER_DISPLAY.items()}

# Known config keys – derived once from the dataclass to stay in sync
# Lazy-initialised to avoid import-time side-effects.
_KNOWN_CONFIG_KEYS: set[str] | None = None


def _get_known_config_keys() -> set[str]:
    """Return the set of known ``BacktestJobConfig`` field names."""
    global _KNOWN_CONFIG_KEYS  # noqa: PLW0603
    if _KNOWN_CONFIG_KEYS is None:
        from libs.trading.backtest.job_queue import BacktestJobConfig

        _KNOWN_CONFIG_KEYS = set(BacktestJobConfig.__dataclass_fields__.keys())
    return _KNOWN_CONFIG_KEYS


# Symbol validation (same regex as backtest.py – imported for reuse)
SYMBOL_PATTERN = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")

# Date bounds shared with form validation
MIN_BACKTEST_PERIOD_DAYS = 30


# ---------------------------------------------------------------------------
# Validation result
# ---------------------------------------------------------------------------
@dataclass
class ValidationResult:
    """Outcome of ``validate_backtest_params``.

    *errors* block submission.  *warnings* are advisory and shown to the user
    but do not prevent the job from being enqueued.
    """

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0


# ---------------------------------------------------------------------------
# Shared validation (used by both form and JSON modes)
# ---------------------------------------------------------------------------
def validate_backtest_params(config_dict: dict[str, Any]) -> ValidationResult:
    """Validate a backtest config dict and return errors / warnings.

    This function enforces the same rules regardless of whether the config
    originated from the standard form or the JSON editor.

    Args:
        config_dict: Dict in ``BacktestJobConfig.to_dict()`` format.
            Must already have string dates (ISO 8601).

    Returns:
        A ``ValidationResult`` with errors (blocking) and warnings
        (non-blocking).
    """
    errors: list[str] = []
    warnings: list[str] = []

    # --- Required fields ------------------------------------------------
    for req in ("alpha_name", "start_date", "end_date"):
        if req not in config_dict:
            errors.append(f"Missing required field: {req}")

    if errors:
        return ValidationResult(errors=errors, warnings=warnings)

    # --- Date validation ------------------------------------------------
    try:
        start_dt = date.fromisoformat(str(config_dict["start_date"]))
        end_dt = date.fromisoformat(str(config_dict["end_date"]))
    except (ValueError, TypeError) as exc:
        errors.append(f"Invalid date format: {exc}")
        return ValidationResult(errors=errors, warnings=warnings)

    if end_dt <= start_dt:
        errors.append("End date must be after start date")

    if (end_dt - start_dt).days < MIN_BACKTEST_PERIOD_DAYS:
        errors.append(f"Backtest period must be at least {MIN_BACKTEST_PERIOD_DAYS} days")

    today = date.today()
    if end_dt > today + timedelta(days=1):
        errors.append("End date cannot be in the future")

    if start_dt.year < 1990 or end_dt.year > 2100:
        errors.append("Dates must be between 1990 and 2100")

    # --- Provider validation --------------------------------------------
    provider = config_dict.get("provider", "crsp")
    valid_providers = set(PROVIDER_DISPLAY.keys())
    if str(provider).lower().strip() not in valid_providers:
        errors.append(
            f"Unrecognised provider: '{provider}'. "
            f"Must be one of: {sorted(valid_providers)}"
        )

    # --- Universe symbol validation -------------------------------------
    extra = config_dict.get("extra_params", {})
    if isinstance(extra, dict):
        universe = extra.get("universe")
        if universe and isinstance(universe, list):
            invalid_symbols = [s for s in universe if not SYMBOL_PATTERN.match(str(s).upper())]
            if invalid_symbols:
                sanitized = [str(s).replace("\n", "").replace("\r", "")[:10] for s in invalid_symbols[:5]]
                errors.append(
                    f"Invalid universe symbols: {', '.join(sanitized)}. "
                    "Symbols must start with a letter, be 1-10 characters "
                    "(alphanumeric/dots/hyphens)."
                )

    # --- Provider-specific warnings ------------------------------------
    if str(provider).lower().strip() == "yfinance":
        cost_model = extra.get("cost_model") if isinstance(extra, dict) else None
        if isinstance(cost_model, dict) and cost_model.get("enabled"):
            warnings.append(
                "Yahoo Finance provider with cost model enabled - "
                "cost estimates may be inaccurate (no PIT ADV data)"
            )

    return ValidationResult(errors=errors, warnings=warnings)


# ---------------------------------------------------------------------------
# Form ↔ JSON conversion helpers
# ---------------------------------------------------------------------------
def form_state_to_json(
    *,
    alpha_name: str,
    start_date: str,
    end_date: str,
    weight_method: str,
    provider_display_label: str,
    universe_csv: str | None,
    cost_config: dict[str, Any] | None,
    extra_params_hidden: dict[str, Any] | None = None,
) -> str:
    """Serialise current form state to a JSON string.

    ``provider_display_label`` is the UI select value (e.g. "CRSP (production)").
    It is mapped to the enum value ("crsp") for the JSON representation.
    """
    provider_value = PROVIDER_DISPLAY_INVERSE.get(provider_display_label, provider_display_label)

    extra: dict[str, Any] = {}
    if extra_params_hidden:
        extra.update(extra_params_hidden)

    if universe_csv:
        symbols = [s.strip().upper() for s in universe_csv.split(",") if s.strip()]
        if symbols:
            extra["universe"] = symbols

    if cost_config is not None:
        extra["cost_model"] = cost_config

    data: dict[str, Any] = {
        "alpha_name": alpha_name,
        "start_date": str(start_date),
        "end_date": str(end_date),
        "weight_method": weight_method,
        "provider": provider_value,
    }
    if extra:
        data["extra_params"] = extra

    return json.dumps(data, indent=2)


@dataclass
class FormState:
    """Values extracted from JSON for populating form controls."""

    alpha_name: str
    start_date: str
    end_date: str
    weight_method: str
    provider_display_label: str
    universe_csv: str
    cost_config: dict[str, Any] | None
    extra_params_hidden: dict[str, Any]


def json_to_form_state(json_str: str) -> FormState:
    """Parse a JSON string and return a ``FormState``.

    Raises ``ValueError`` if the JSON is syntactically invalid or cannot
    be meaningfully converted to form state.
    """
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError("JSON root must be an object")

    provider_raw = str(data.get("provider", "crsp")).lower().strip()
    display_label = PROVIDER_DISPLAY.get(provider_raw)
    if display_label is None:
        raise ValueError(
            f"Unrecognised provider '{provider_raw}'. "
            f"Must be one of: {sorted(PROVIDER_DISPLAY.keys())}"
        )

    extra = data.get("extra_params", {})
    if not isinstance(extra, dict):
        extra = {}

    universe_list = extra.get("universe")
    universe_csv = ""
    if isinstance(universe_list, list):
        universe_csv = ", ".join(str(s) for s in universe_list)

    cost_model = extra.get("cost_model")
    if not isinstance(cost_model, dict):
        cost_model = None

    # Any keys in extra_params beyond universe/cost_model are hidden
    hidden = {k: v for k, v in extra.items() if k not in ("universe", "cost_model")}

    return FormState(
        alpha_name=str(data.get("alpha_name", "")),
        start_date=str(data.get("start_date", "")),
        end_date=str(data.get("end_date", "")),
        weight_method=str(data.get("weight_method", "zscore")),
        provider_display_label=display_label,
        universe_csv=universe_csv,
        cost_config=cost_model,
        extra_params_hidden=hidden,
    )


def detect_unknown_keys(config_dict: dict[str, Any]) -> list[str]:
    """Return top-level keys not recognised by ``BacktestJobConfig``."""
    return sorted(set(config_dict.keys()) - _get_known_config_keys())


# ---------------------------------------------------------------------------
# UI component
# ---------------------------------------------------------------------------
def render_config_editor(
    *,
    on_submit: Any,
    get_form_json: Any,
    get_priority: Any,
    alpha_options: list[str],
    on_deactivate: Any = None,
) -> dict[str, Any]:
    """Render the Advanced Mode toggle and JSON editor.

    Returns a dict of handles the caller uses to interact with the editor:

    * ``container`` – the card that wraps the editor (visibility-bound)
    * ``editor`` – the ``ui.codemirror`` element
    * ``switch`` – the ``ui.switch`` element
    * ``error_label`` – label for inline validation errors
    * ``warning_label`` – label for non-blocking warnings

    Args:
        on_submit: Async callback ``(config_dict, priority) -> None``
            invoked when the user clicks "Run Backtest (JSON)".
        get_form_json: Callable ``() -> str`` returning the current
            form state serialised as JSON.
        get_priority: Callable ``() -> str`` returning the current
            priority select value.
        alpha_options: Available alpha names (for basic validation).
        on_deactivate: Optional callback ``(FormState) -> None`` invoked
            when Advanced Mode is toggled OFF.  Receives a ``FormState``
            parsed from the current editor content so the caller can
            sync form controls with any changes made in the JSON editor.
    """
    handles: dict[str, Any] = {}

    switch = ui.switch("Advanced Mode (JSON)", value=False).classes("mb-2")
    handles["switch"] = switch

    container = ui.column().classes("w-full")
    container.set_visibility(False)
    handles["container"] = container

    with container:
        ui.label(
            "Edit backtest configuration as JSON. "
            "Priority selector above remains active."
        ).classes("text-xs text-gray-500 mb-1")

        editor = ui.codemirror("", language="JSON").classes("w-full").style("min-height: 300px")
        handles["editor"] = editor

        error_label = ui.label("").classes("text-red-600 text-sm")
        error_label.set_visibility(False)
        handles["error_label"] = error_label

        warning_label = ui.label("").classes("text-amber-600 text-sm")
        warning_label.set_visibility(False)
        handles["warning_label"] = warning_label

        with ui.row().classes("gap-2 mt-2"):
            async def _copy_json() -> None:
                val = editor.value or ""
                await ui.run_javascript(
                    f"navigator.clipboard.writeText({json.dumps(val)})"
                )
                ui.notify("Copied to clipboard", type="positive")

            ui.button("Copy Config", on_click=_copy_json, icon="content_copy").props("flat")

            async def _submit_json() -> None:
                error_label.set_visibility(False)
                warning_label.set_visibility(False)
                raw = editor.value or ""

                # 1. Parse JSON
                try:
                    config_dict = json.loads(raw)
                except json.JSONDecodeError as exc:
                    error_label.set_text(f"Invalid JSON: {exc}")
                    error_label.set_visibility(True)
                    return

                if not isinstance(config_dict, dict):
                    error_label.set_text("JSON root must be an object")
                    error_label.set_visibility(True)
                    return

                # 2. Warn on unknown keys
                unknown = detect_unknown_keys(config_dict)
                if unknown:
                    ui.notify(
                        f"Ignored keys: {', '.join(unknown)}",
                        type="warning",
                    )

                # 3. Shared validation
                result = validate_backtest_params(config_dict)
                if result.warnings:
                    warning_label.set_text("; ".join(result.warnings))
                    warning_label.set_visibility(True)

                if not result.is_valid:
                    error_label.set_text("; ".join(result.errors))
                    error_label.set_visibility(True)
                    return

                # 4. Validate via from_dict (catches enum/date errors)
                try:
                    from libs.trading.backtest.job_queue import BacktestJobConfig

                    BacktestJobConfig.from_dict(config_dict)
                except (KeyError, ValueError, TypeError) as exc:
                    msg = str(exc)
                    if isinstance(exc, KeyError):
                        msg = f"Missing required field: {exc}"
                    error_label.set_text(msg)
                    error_label.set_visibility(True)
                    return

                # 5. Delegate to caller
                priority = get_priority()
                await on_submit(config_dict, priority)

            ui.button("Run Backtest (JSON)", on_click=_submit_json, color="primary")

    # --- Toggle handler ---
    def _on_toggle(e: Any) -> None:
        is_advanced = e.value if hasattr(e, "value") else switch.value
        container.set_visibility(is_advanced)
        if is_advanced:
            try:
                editor.value = get_form_json()
                error_label.set_visibility(False)
                warning_label.set_visibility(False)
            except Exception:
                logger.warning("config_editor_form_serialize_failed", exc_info=True)
        else:
            # Sync JSON editor changes back to form on deactivate
            if on_deactivate is not None and editor.value:
                try:
                    state = json_to_form_state(editor.value)
                    on_deactivate(state)
                except (ValueError, KeyError):
                    # Invalid JSON in editor – silently keep current form state
                    pass

    switch.on("update:model-value", _on_toggle)

    return handles


__all__ = [
    "FormState",
    "PROVIDER_DISPLAY",
    "PROVIDER_DISPLAY_INVERSE",
    "ValidationResult",
    "detect_unknown_keys",
    "form_state_to_json",
    "json_to_form_state",
    "render_config_editor",
    "validate_backtest_params",
]
