"""Legacy position-management route redirect.

Position controls are now part of the trade workspace at ``/trade``.
The legacy route remains as a compatibility redirect.
"""

from __future__ import annotations

from nicegui import Client, ui

from apps.web_console_ng.auth.middleware import requires_auth
from apps.web_console_ng.ui.layout import main_layout


@ui.page("/position-management")
@requires_auth
@main_layout
async def position_management_page(client: Client) -> None:
    """Redirect legacy position management route to trade workspace."""
    del client
    ui.notify(
        "Position Management moved to Trade Workspace. Redirecting...",
        type="info",
    )
    ui.navigate.to("/trade")


__all__ = ["position_management_page"]
