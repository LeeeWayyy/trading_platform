"""Legacy manual-order route redirect.

This page was consolidated into the unified trade workspace at ``/trade``.
The legacy route remains as a compatibility redirect.
"""

from __future__ import annotations

from nicegui import Client, ui

from apps.web_console_ng.auth.middleware import requires_auth
from apps.web_console_ng.ui.layout import main_layout


@ui.page("/manual-order")
@requires_auth
@main_layout
async def manual_order_page(client: Client) -> None:
    """Redirect legacy manual controls route to trade workspace."""
    del client
    ui.notify(
        "Manual Controls moved to Trade Workspace. Redirecting...",
        type="info",
    )
    ui.navigate.to("/trade")


__all__ = ["manual_order_page"]
