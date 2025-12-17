"""Reusable confirmation dialog component for manual controls actions."""

from __future__ import annotations

import streamlit as st


def render_confirmation_dialog(
    key: str,
    title: str,
    body: str,
    reason_label: str = "Reason",
    min_reason_length: int = 10,
    confirm_label: str = "Confirm",
    cancel_label: str = "Cancel",
    is_submitting: bool = False,
) -> tuple[bool, bool, str]:
    """Render a confirmation dialog with reason input.

    Returns:
        (confirmed, cancelled, reason)
    """

    st.subheader(title)
    st.write(body)

    reason = st.text_area(
        reason_label,
        key=f"{key}_reason",
        placeholder=f"Enter reason (min {min_reason_length} characters)",
        help="Required for audit trail",
    )

    col1, col2 = st.columns(2)
    with col1:
        confirmed = st.button(
            confirm_label,
            key=f"{key}_confirm",
            type="primary",
            use_container_width=True,
            disabled=is_submitting,
        )
    with col2:
        cancelled = st.button(
            cancel_label,
            key=f"{key}_cancel",
            use_container_width=True,
            disabled=is_submitting,
        )

    # Enforce minimum length client-side
    if confirmed and len(reason.strip()) < min_reason_length:
        st.error(f"Reason must be at least {min_reason_length} characters")
        confirmed = False

    return confirmed, cancelled, reason.strip()


__all__ = ["render_confirmation_dialog"]
