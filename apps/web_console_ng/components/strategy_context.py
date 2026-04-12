"""Compact strategy/model context widget for unified execution workspace."""

from __future__ import annotations

from nicegui import ui

from apps.web_console_ng.components.execution_gate import (
    is_model_execution_safe,
    is_strategy_execution_safe,
    normalize_execution_status,
)


def resolve_execution_gate_state(
    *,
    strategy_status: str | None,
    model_status: str | None,
    gate_enabled: bool,
    gate_reason: str | None = None,
) -> tuple[str, str, str]:
    """Return gate badge text/tone and default banner message."""
    strategy = normalize_execution_status(strategy_status)
    model = normalize_execution_status(model_status)
    strategy_safe = is_strategy_execution_safe(strategy)
    model_safe = is_model_execution_safe(model)

    if not gate_enabled:
        return ("GATE OFF", "warning", "Execution gate is disabled by feature flag.")

    if strategy_safe and model_safe:
        return ("GATE CLEAR", "positive", "Execution context healthy.")

    if gate_reason:
        return ("GATE BLOCKED", "negative", f"Execution gated: {gate_reason}")

    if not strategy_safe:
        return ("GATE BLOCKED", "negative", f"Execution gated: strategy is {strategy.upper()}")
    if not model_safe:
        return ("GATE BLOCKED", "negative", f"Execution gated: model is {model.upper()}")
    return ("GATE BLOCKED", "negative", "Execution gated: strategy/model context unavailable")


def resolve_context_links(
    *,
    show_strategy_link: bool,
    show_model_link: bool,
) -> list[tuple[str, str]]:
    """Return compact context links for strategy/model management surfaces."""
    links: list[tuple[str, str]] = []
    if show_strategy_link:
        links.append(("Strategies", "/strategies"))
    if show_model_link:
        links.append(("Models", "/models"))
    return links


class StrategyContextWidget:
    """Render current strategy/model safety context near the order ticket."""

    def __init__(
        self,
        strategies: list[str] | None = None,
        *,
        show_strategy_link: bool = True,
        show_model_link: bool = True,
    ) -> None:
        self._strategies = [str(item) for item in (strategies or []) if str(item).strip()]
        self._symbol: str | None = None
        self._show_strategy_link = show_strategy_link
        self._show_model_link = show_model_link

        self._symbol_label: ui.label | None = None
        self._strategy_label: ui.label | None = None
        self._strategy_status: ui.label | None = None
        self._model_label: ui.label | None = None
        self._model_status: ui.label | None = None
        self._gate_status: ui.label | None = None
        self._banner_label: ui.label | None = None

    def create(self) -> ui.card:
        """Create context widget card."""
        with ui.card().classes("workspace-v2-panel workspace-v2-strategy-context") as card:
            with ui.row().classes("w-full items-center justify-between"):
                ui.label("Strategy Context").classes("workspace-v2-panel-title")
                self._gate_status = ui.label("GATE UNKNOWN").classes(
                    "workspace-v2-pill workspace-v2-pill-warning"
                )

            with ui.row().classes("w-full items-center justify-between mt-1"):
                self._symbol_label = ui.label("SYMBOL: --").classes(
                    "workspace-v2-kv workspace-v2-data-mono"
                )

            strategy_scope = ", ".join(self._strategies[:2]) if self._strategies else "No scope"
            if len(self._strategies) > 2:
                strategy_scope += f" (+{len(self._strategies) - 2})"

            with ui.row().classes("w-full items-center justify-between mt-1 gap-2"):
                self._strategy_label = ui.label(f"Strategy: {strategy_scope}").classes(
                    "workspace-v2-kv"
                )
                self._strategy_status = ui.label("UNKNOWN").classes(
                    "workspace-v2-pill workspace-v2-pill-warning"
                )

            with ui.row().classes("w-full items-center justify-between mt-1 gap-2"):
                self._model_label = ui.label("Model: --").classes("workspace-v2-kv")
                self._model_status = ui.label("UNKNOWN").classes(
                    "workspace-v2-pill workspace-v2-pill-warning"
                )

            context_links = resolve_context_links(
                show_strategy_link=self._show_strategy_link,
                show_model_link=self._show_model_link,
            )
            if context_links:
                with ui.row().classes("w-full items-center gap-2 mt-2"):
                    for label, path in context_links:
                        with ui.link(target=path).classes("workspace-v2-context-link"):
                            ui.label(label).classes("workspace-v2-kv")

            self._banner_label = ui.label(
                "Select a symbol to resolve strategy/model execution context."
            ).classes("workspace-v2-banner workspace-v2-banner-warning mt-2")

        return card

    def set_symbol(self, symbol: str | None) -> None:
        """Update the selected symbol displayed in widget."""
        self._symbol = symbol.strip().upper() if symbol else None
        if self._symbol_label is not None:
            self._symbol_label.text = f"SYMBOL: {self._symbol or '--'}"

    def set_status(
        self,
        *,
        strategy_status: str,
        model_status: str,
        gate_enabled: bool = True,
        gate_reason: str | None = None,
        strategy_label: str | None = None,
        model_label: str | None = None,
        banner: str | None = None,
    ) -> None:
        """Update status fields from strategy/model context resolver."""
        self._set_status_badge(self._strategy_status, strategy_status)
        self._set_status_badge(self._model_status, model_status)

        gate_text, gate_tone, default_banner = resolve_execution_gate_state(
            strategy_status=strategy_status,
            model_status=model_status,
            gate_enabled=gate_enabled,
            gate_reason=gate_reason,
        )
        self._set_tone_badge(self._gate_status, tone=gate_tone, text=gate_text)
        self._set_banner_tone(gate_tone)

        if strategy_label is not None and self._strategy_label is not None:
            self._strategy_label.text = strategy_label
        if model_label is not None and self._model_label is not None:
            self._model_label.text = model_label
        if self._banner_label is not None:
            self._banner_label.text = banner if banner is not None else default_banner

    def _set_tone_badge(self, badge: ui.label | None, *, tone: str, text: str) -> None:
        if badge is None:
            return
        badge.text = text
        badge.classes(
            remove="workspace-v2-pill-positive workspace-v2-pill-negative workspace-v2-pill-warning"
        )
        if tone == "positive":
            badge.classes(add="workspace-v2-pill-positive")
        elif tone == "negative":
            badge.classes(add="workspace-v2-pill-negative")
        else:
            badge.classes(add="workspace-v2-pill-warning")

    def _set_status_badge(self, badge: ui.label | None, status: str) -> None:
        if badge is None:
            return
        normalized = status.strip().upper() if status else "UNKNOWN"
        tone: str
        if normalized in {"ACTIVE", "READY", "IDLE", "TESTING"}:
            tone = "positive"
        elif normalized in {"FAILED", "INACTIVE", "TRIPPED"}:
            tone = "negative"
        else:
            tone = "warning"
        self._set_tone_badge(badge, tone=tone, text=normalized)

    def _set_banner_tone(self, tone: str) -> None:
        if self._banner_label is None:
            return
        self._banner_label.classes(
            remove="workspace-v2-banner-positive workspace-v2-banner-warning workspace-v2-banner-negative"
        )
        if tone == "positive":
            self._banner_label.classes(add="workspace-v2-banner-positive")
        elif tone == "negative":
            self._banner_label.classes(add="workspace-v2-banner-negative")
        else:
            self._banner_label.classes(add="workspace-v2-banner-warning")


__all__ = ["StrategyContextWidget", "resolve_context_links", "resolve_execution_gate_state"]
