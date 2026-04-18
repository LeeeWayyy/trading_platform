"""Compatibility redirects for retired trade page routes."""

from __future__ import annotations

from nicegui import Client, ui

from apps.web_console_ng.auth.middleware import requires_auth


def _redirect_to_trade(*, message: str) -> None:
    """Notify user and redirect to canonical Trade workspace."""
    ui.notify(message, type="info")
    ui.navigate.to("/trade")


@ui.page("/manual-order")
@requires_auth
async def legacy_manual_order_redirect(client: Client) -> None:
    """Retired route compatibility: Manual Order -> Trade."""
    # Intentional: skip main_layout for immediate compatibility redirect and avoid full layout paint.
    del client
    _redirect_to_trade(message="Manual Controls moved to Trade Workspace. Redirecting...")


@ui.page("/position-management")
@requires_auth
async def legacy_position_management_redirect(client: Client) -> None:
    """Retired route compatibility: Position Management -> Trade."""
    # Intentional: skip main_layout for immediate compatibility redirect and avoid full layout paint.
    del client
    _redirect_to_trade(message="Position Management moved to Trade Workspace. Redirecting...")


__all__ = [
    "legacy_manual_order_redirect",
    "legacy_position_management_redirect",
]
