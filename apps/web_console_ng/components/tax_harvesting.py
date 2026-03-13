"""Tax-loss harvesting suggestions component."""

from __future__ import annotations

from typing import Any

from nicegui import ui


def render_harvesting_suggestions(suggestions: Any | None) -> None:
    """Render tax-loss harvesting suggestions as ranked cards.

    Args:
        suggestions: HarvestingRecommendation from TaxLossHarvester.find_opportunities(),
                     or None if prices unavailable.
    """
    ui.label("Tax-Loss Harvesting").classes("text-lg font-bold mb-2")

    if suggestions is None:
        with ui.card().classes("w-full p-3"):
            ui.label("Current prices unavailable — harvesting suggestions hidden.").classes(
                "text-gray-500 text-sm"
            )
            ui.label("Price data requires VIEW_PNL permission.").classes("text-gray-400 text-xs")
        return

    opportunities = getattr(suggestions, "opportunities", [])
    if not opportunities:
        with ui.card().classes("w-full p-3"):
            ui.label("No harvesting opportunities found.").classes("text-gray-500 text-sm")
        return

    # Aggregate estimated savings is on HarvestingRecommendation, not per-opportunity
    total_savings = getattr(suggestions, "estimated_tax_savings", 0)
    if total_savings:
        ui.label(f"Est. total tax savings: ${float(total_savings):,.2f}").classes(
            "text-green-500 text-sm mb-2"
        )

    for opp in opportunities:
        symbol = getattr(opp, "symbol", "?")
        unrealized_loss = getattr(opp, "unrealized_loss", 0)
        with ui.card().classes("w-full p-3 mb-2"):
            with ui.row().classes("items-center justify-between"):
                ui.label(symbol).classes("font-bold text-lg")
                ui.badge(f"-${abs(float(unrealized_loss)):,.2f}", color="red")


__all__ = ["render_harvesting_suggestions"]
