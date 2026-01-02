from __future__ import annotations

from nicegui import ui


@ui.page("/forgot-password")
def forgot_password_page() -> None:
    """Placeholder page for password reset flow."""
    with ui.card().classes("absolute-center w-96 p-8"):
        ui.label("Forgot Password").classes("text-2xl font-bold text-center mb-2 w-full")
        ui.label("Password reset is not available yet.").classes(
            "text-gray-500 text-center mb-6 w-full"
        )
        ui.link("Back to login", target="/login").classes(
            "text-sm text-blue-600 hover:underline text-center w-full"
        )
