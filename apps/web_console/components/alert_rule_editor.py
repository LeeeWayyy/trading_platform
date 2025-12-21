"""Alert rule editor form component."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import streamlit as st
from pydantic import ValidationError

from apps.web_console.components.notification_channels import render_notification_channels
from apps.web_console.components.threshold_config import render_threshold_config
from apps.web_console.services.alert_service import AlertRuleCreate, AlertRuleUpdate
from libs.alerts.models import AlertRule, ChannelConfig

CONDITION_TYPES = ["drawdown", "position_limit", "latency"]


def render_alert_rule_editor(
    rule: AlertRule | None = None,
    user: dict[str, Any] | None = None,
    alert_service: Any | None = None,
    on_save: Callable[[AlertRuleCreate | AlertRuleUpdate], None] | None = None,
) -> tuple[bool, AlertRuleCreate | AlertRuleUpdate | None]:
    """Render alert rule editor form."""

    channel_state_key = f"alert_rule_channels_{rule.id if rule else 'new'}"
    if channel_state_key not in st.session_state:
        st.session_state[channel_state_key] = rule.channels if rule else []

    with st.form("alert_rule_form"):
        name = st.text_input("Rule Name", value=rule.name if rule else "")

        condition_type = st.selectbox(
            "Condition Type",
            options=CONDITION_TYPES,
            index=0 if not rule else CONDITION_TYPES.index(rule.condition_type),
        )

        threshold_value, comparison = render_threshold_config(condition_type)

        enabled = st.checkbox("Enabled", value=rule.enabled if rule else True)

        submitted = st.form_submit_button("Save Rule")

    existing_channels = st.session_state.get(channel_state_key, [])
    channels: list[ChannelConfig]
    if user is not None and alert_service is not None:
        channels = render_notification_channels(
            existing_channels, user, alert_service, state_key=channel_state_key
        )
    else:
        channels = existing_channels

    st.session_state[channel_state_key] = channels

    if submitted:
        try:
            rule_data: AlertRuleCreate | AlertRuleUpdate
            if rule:
                rule_data = AlertRuleUpdate(
                    name=name,
                    condition_type=condition_type,
                    threshold_value=threshold_value,
                    comparison=comparison,
                    channels=channels,
                    enabled=enabled,
                )
            else:
                rule_data = AlertRuleCreate(
                    name=name,
                    condition_type=condition_type,
                    threshold_value=threshold_value,
                    comparison=comparison,
                    channels=channels,
                    enabled=enabled,
                )
            if on_save:
                on_save(rule_data)
            return True, rule_data
        except ValidationError as exc:
            st.error(f"Validation error: {exc}")
            return False, None

    return False, None


__all__ = ["render_alert_rule_editor"]
