"""Role change dialog component (T16.2)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from nicegui import ui

from libs.platform.web_console_auth.permissions import Role

_CANONICAL_ROLES = [r.value for r in Role]


def render_role_change_dialog(
    user_id: str,
    current_role: str,
    *,
    on_confirm: Callable[[str, str, str], Awaitable[None]],
) -> None:
    """Show role change dialog with confirmation.

    Args:
        user_id: Target user's ID.
        current_role: Current role string.
        on_confirm: Callback(user_id, new_role, reason) on confirmation.
    """
    with ui.dialog() as dialog, ui.card().classes("w-96"):
        ui.label(f"Change Role: {user_id}").classes("text-lg font-bold mb-2")
        ui.label(f"Current role: {current_role}").classes("text-gray-500 text-sm mb-2")

        role_select = ui.select(
            options=_CANONICAL_ROLES,
            value=current_role,
            label="New Role",
        ).classes("w-full")

        reason_input = ui.input("Reason for change").classes("w-full").props("outlined")

        # Downgrade warning
        warning_label = ui.label("").classes("text-orange-500 text-sm")

        def _check_downgrade() -> None:
            new = role_select.value
            if new == current_role:
                warning_label.set_text("")
                return
            role_order = {"viewer": 0, "researcher": 1, "operator": 2, "admin": 3}
            old_rank = role_order.get(current_role, 0)
            new_rank = role_order.get(new, 0)
            if new_rank < old_rank:
                warning_label.set_text(f"Warning: Demoting from {current_role} to {new}")
            else:
                warning_label.set_text("")

        role_select.on("update:model-value", lambda _: _check_downgrade())

        with ui.row().classes("w-full justify-end gap-2 mt-4"):
            ui.button("Cancel", on_click=dialog.close).props("flat")

            async def _do_confirm() -> None:
                new_role = role_select.value
                reason = reason_input.value or ""
                if new_role == current_role:
                    ui.notify("No change selected", type="info")
                    return
                if not reason.strip():
                    ui.notify("Reason is required", type="warning")
                    return
                dialog.close()
                await on_confirm(user_id, new_role, reason)

            ui.button("Confirm", on_click=_do_confirm, color="primary")

    dialog.open()


__all__ = ["render_role_change_dialog"]
