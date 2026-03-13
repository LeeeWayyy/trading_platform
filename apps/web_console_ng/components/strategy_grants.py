"""Strategy grants dialog component (T16.2)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from nicegui import ui


def render_strategy_grants_dialog(
    user_id: str,
    assigned: list[str],
    available: list[Any],
    *,
    on_grant: Callable[[str, str], Awaitable[None]],
    on_revoke: Callable[[str, str], Awaitable[None]],
) -> None:
    """Show strategy grants dialog with checkbox list.

    Args:
        user_id: Target user's ID.
        assigned: List of strategy IDs currently assigned to the user.
        available: List of StrategyInfo dataclass instances (strategy_id, name, description).
        on_grant: Callback(user_id, strategy_id) to grant access.
        on_revoke: Callback(user_id, strategy_id) to revoke access.
    """
    assigned_set = set(assigned)

    with ui.dialog() as dialog, ui.card().classes("w-96 max-h-[80vh]"):
        ui.label(f"Strategy Access: {user_id}").classes("text-lg font-bold mb-2")
        ui.label(f"{len(assigned_set)} of {len(available)} strategies assigned").classes(
            "text-gray-500 text-sm mb-4"
        )

        if not available:
            ui.label("No strategies available").classes("text-gray-400")
        else:
            with ui.scroll_area().classes("max-h-96"):
                for strategy in available:
                    sid = strategy.strategy_id
                    name = strategy.name
                    desc = strategy.description or ""
                    is_assigned = sid in assigned_set

                    with ui.row().classes("w-full items-center py-1"):
                        cb = ui.checkbox(
                            f"{name} ({sid})",
                            value=is_assigned,
                        ).classes("flex-1")

                        if desc:
                            cb.tooltip(desc)

                        async def _toggle(e: Any, _sid: str = sid) -> None:
                            if e.value:
                                await on_grant(user_id, _sid)
                            else:
                                await on_revoke(user_id, _sid)

                        cb.on("update:model-value", _toggle)

        with ui.row().classes("w-full justify-end mt-4"):
            ui.button("Close", on_click=dialog.close)

    dialog.open()


__all__ = ["render_strategy_grants_dialog"]
