"""User management AG Grid table component (T16.2)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from nicegui import ui


def render_user_table(
    users: list[Any],
    *,
    on_role_change: Callable[[str], Awaitable[None]] | None = None,
    on_view_strategies: Callable[[str], Awaitable[None]] | None = None,
    on_view_activity: Callable[[str], Awaitable[None]] | None = None,
    on_force_logout: Callable[[str], Awaitable[None]] | None = None,
    current_user_id: str = "",
) -> Any:
    """Render user management AG Grid table.

    Args:
        users: List of UserInfo dataclass instances.
        on_role_change: Callback with user_id when role change is requested.
        on_view_strategies: Callback with user_id for strategy dialog.
        on_view_activity: Callback with user_id for activity log.
        on_force_logout: Callback with user_id for force logout.
        current_user_id: Current admin's user_id (to disable self-actions).
    """
    columns = [
        {"headerName": "User ID", "field": "user_id", "flex": 2, "sortable": True, "filter": True},
        {"headerName": "Role", "field": "role", "flex": 1, "sortable": True, "filter": True},
        {"headerName": "Strategies", "field": "strategy_count", "flex": 1, "sortable": True},
        {"headerName": "Updated", "field": "updated_at", "flex": 1.5, "sortable": True},
        {"headerName": "Updated By", "field": "updated_by", "flex": 1, "sortable": True},
    ]

    rows = []
    for u in users:
        rows.append(
            {
                "user_id": u.user_id,
                "role": u.role,
                "strategy_count": u.strategy_count,
                "updated_at": u.updated_at,
                "updated_by": u.updated_by or "-",
            }
        )

    grid = (
        ui.aggrid(
            {
                "columnDefs": columns,
                "rowData": rows,
                "rowSelection": "multiple",
                "defaultColDef": {"resizable": True},
            }
        )
        .classes("w-full")
        .style("height: 400px")
    )

    # Action buttons row
    with ui.row().classes("w-full gap-2 mt-2"):
        if on_role_change:

            async def _role_change() -> None:
                selected = await grid.get_selected_rows()
                if selected and len(selected) == 1:
                    await on_role_change(selected[0]["user_id"])
                elif selected and len(selected) > 1:
                    ui.notify("Select one user for role change", type="warning")
                else:
                    ui.notify("Select a user first", type="warning")

            ui.button("Change Role", on_click=_role_change, icon="edit").props("flat")

        if on_view_strategies:

            async def _strategies() -> None:
                selected = await grid.get_selected_rows()
                if selected and len(selected) == 1:
                    await on_view_strategies(selected[0]["user_id"])
                else:
                    ui.notify("Select one user", type="warning")

            ui.button("Strategies", on_click=_strategies, icon="key").props("flat")

        if on_view_activity:

            async def _activity() -> None:
                selected = await grid.get_selected_rows()
                if selected and len(selected) == 1:
                    await on_view_activity(selected[0]["user_id"])
                else:
                    ui.notify("Select one user", type="warning")

            ui.button("Activity", on_click=_activity, icon="history").props("flat")

        if on_force_logout:

            async def _force_logout() -> None:
                selected = await grid.get_selected_rows()
                if selected and len(selected) == 1:
                    target = selected[0]["user_id"]
                    if target == current_user_id:
                        ui.notify("Cannot force-logout yourself", type="warning")
                        return
                    await on_force_logout(target)
                else:
                    ui.notify("Select one user", type="warning")

            ui.button("Force Logout", on_click=_force_logout, icon="logout", color="orange").props(
                "flat"
            )

    return grid


__all__ = ["render_user_table"]
