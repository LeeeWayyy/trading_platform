"""Notification channel configuration component."""

from __future__ import annotations

from typing import Any

import streamlit as st

from apps.web_console.services.alert_service import AlertConfigService
from apps.web_console.utils.async_helpers import run_async
from libs.alerts.models import ChannelConfig, ChannelType
from libs.alerts.pii import mask_recipient
from libs.web_console_auth.permissions import Permission, has_permission


def render_notification_channels(
    channels: list[ChannelConfig],
    user: dict[str, Any],
    alert_service: AlertConfigService,
    state_key: str | None = None,
) -> list[ChannelConfig]:
    """Render notification channel configuration with masking, test buttons, and add flow."""

    # Work on a copy to avoid mutating input
    working_channels = list(channels)
    updated_channels: list[ChannelConfig] = []
    can_test = has_permission(user, Permission.TEST_NOTIFICATION)

    # Allow adding a new channel when none exist (or to append more)
    st.markdown("**Add Channel**")
    channel_type = st.selectbox(
        "Channel Type",
        options=[c.value for c in ChannelType],
        key=f"{state_key}_type" if state_key else None,
    )
    new_recipient = st.text_input(
        "Recipient",
        key=f"{state_key}_recipient" if state_key else None,
        help="Email address, Slack webhook URL, or phone number",
    )
    if st.button("Add Channel", key=f"{state_key}_add" if state_key else None):
        if new_recipient:
            new_channel = ChannelConfig(
                type=ChannelType(channel_type), recipient=new_recipient, enabled=True
            )
            if state_key:
                # Persist to session_state so it survives Streamlit reruns
                existing = st.session_state.get(state_key, [])
                st.session_state[state_key] = existing + [new_channel]
                st.rerun()
            else:
                working_channels.append(new_channel)
                st.success("Channel added. Adjust below if needed.")
        else:
            st.error("Recipient is required to add a channel.")

    for i, channel in enumerate(working_channels):
        with st.expander(f"{channel.type.value.title()} Channel", expanded=False):
            masked = mask_recipient(channel.recipient, channel.type.value)
            st.text(f"Recipient: {masked}")

            new_recipient = st.text_input(
                "Update recipient",
                key=f"channel_{i}_recipient",
                type="password",
            )

            enabled = st.checkbox(
                "Enabled",
                value=channel.enabled,
                key=f"channel_{i}_enabled",
            )

            if can_test:
                if st.button(f"Test {channel.type.value}", key=f"test_{i}"):
                    # Build a temporary channel reflecting any edits before testing
                    test_channel = ChannelConfig(
                        type=channel.type,
                        recipient=new_recipient or channel.recipient,
                        enabled=enabled,
                    )
                    try:
                        result = run_async(alert_service.test_notification(test_channel, user))
                        if result.success:
                            st.success(
                                "Test notification sent to "
                                f"{mask_recipient(test_channel.recipient, test_channel.type.value)}"
                            )
                        else:
                            st.error(f"Test failed: {result.error or 'unknown error'}")
                    except Exception as exc:  # pragma: no cover - UI feedback
                        st.error(f"Test failed: {exc}")
            else:
                st.button(
                    f"Test {channel.type.value}",
                    key=f"test_{i}",
                    disabled=True,
                    help="Requires Operator or Admin role",
                )

            updated_channels.append(
                ChannelConfig(
                    type=channel.type,
                    recipient=new_recipient or channel.recipient,
                    enabled=enabled,
                )
            )

    return updated_channels


__all__ = ["render_notification_channels"]
