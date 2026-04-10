"""Compact strategy/model context widget for unified execution workspace."""

from __future__ import annotations

from nicegui import ui


class StrategyContextWidget:
    """Render current strategy/model safety context near the order ticket."""

    def __init__(self, strategies: list[str] | None = None) -> None:
        self._strategies = [str(item) for item in (strategies or []) if str(item).strip()]
        self._symbol: str | None = None

        self._symbol_label: ui.label | None = None
        self._strategy_label: ui.label | None = None
        self._strategy_status: ui.label | None = None
        self._model_label: ui.label | None = None
        self._model_status: ui.label | None = None
        self._banner_label: ui.label | None = None

    def create(self) -> ui.card:
        """Create context widget card."""
        with ui.card().classes("workspace-v2-panel workspace-v2-strategy-context") as card:
            with ui.row().classes("w-full items-center justify-between"):
                ui.label("Strategy Context").classes("workspace-v2-panel-title")
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
                self._model_label = ui.label("Model: pending data contract").classes("workspace-v2-kv")
                self._model_status = ui.label("UNKNOWN").classes(
                    "workspace-v2-pill workspace-v2-pill-warning"
                )

            self._banner_label = ui.label(
                "Execution gating by strategy/model is pending backend status feed."
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
        strategy_label: str | None = None,
        model_label: str | None = None,
        banner: str | None = None,
    ) -> None:
        """Update status fields when backend context feed becomes available."""
        self._set_status_badge(self._strategy_status, strategy_status)
        self._set_status_badge(self._model_status, model_status)

        if strategy_label is not None and self._strategy_label is not None:
            self._strategy_label.text = strategy_label
        if model_label is not None and self._model_label is not None:
            self._model_label.text = model_label
        if banner is not None and self._banner_label is not None:
            self._banner_label.text = banner

    def _set_status_badge(self, badge: ui.label | None, status: str) -> None:
        if badge is None:
            return
        normalized = status.strip().upper() if status else "UNKNOWN"
        badge.text = normalized
        badge.classes(remove="workspace-v2-pill-positive workspace-v2-pill-negative workspace-v2-pill-warning")
        if normalized in {"ACTIVE", "READY"}:
            badge.classes(add="workspace-v2-pill-positive")
        elif normalized in {"FAILED", "INACTIVE", "TRIPPED"}:
            badge.classes(add="workspace-v2-pill-negative")
        else:
            badge.classes(add="workspace-v2-pill-warning")


__all__ = ["StrategyContextWidget"]
