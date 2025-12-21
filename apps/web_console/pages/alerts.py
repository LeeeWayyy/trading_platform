"""Alert configuration page (T7.3)."""

from __future__ import annotations

import logging
from typing import Any, cast

import streamlit as st

from apps.web_console.auth.audit_log import AuditLogger
from apps.web_console.auth.operations_auth import operations_requires_auth
from apps.web_console.components.alert_history import render_alert_history
from apps.web_console.components.alert_rule_editor import render_alert_rule_editor
from apps.web_console.config import FEATURE_ALERTS
from apps.web_console.services.alert_service import (
    AlertConfigService,
    AlertRuleCreate,
    AlertRuleUpdate,
)
from apps.web_console.utils.async_helpers import run_async
from libs.alerts.models import AlertEvent
from libs.alerts.pii import mask_recipient
from libs.web_console_auth.permissions import Permission, has_permission

logger = logging.getLogger(__name__)


def _get_alert_service(db_pool: Any) -> AlertConfigService:
    if "alert_service" not in st.session_state:
        st.session_state["alert_service"] = AlertConfigService(
            db_pool=db_pool,
            audit_logger=AuditLogger(db_pool),
        )
    return cast(AlertConfigService, st.session_state["alert_service"])


def _render_alert_rules_section(user: dict[str, Any], service: AlertConfigService) -> None:
    st.subheader("Alert Rules")
    if not has_permission(user, Permission.VIEW_ALERTS):
        st.error("Permission denied: VIEW_ALERTS required")
        return

    try:
        rules = run_async(service.get_rules())
    except Exception as exc:  # pragma: no cover - UI feedback
        st.error(f"Failed to load rules: {exc}")
        return
    can_delete = has_permission(user, Permission.DELETE_ALERT_RULE)
    for rule in rules:
        with st.expander(f"{rule.name} ({rule.condition_type})", expanded=False):
            st.json(
                {
                    "threshold_value": str(rule.threshold_value),
                    "comparison": rule.comparison,
                    "enabled": rule.enabled,
                    "channels": [
                        {
                            "type": c.type.value,
                            "recipient": mask_recipient(c.recipient, c.type.value),
                            "enabled": c.enabled,
                        }
                        for c in rule.channels
                    ],
                }
            )
            cols = st.columns(2)
            if can_delete and cols[0].button(
                "Delete Rule",
                key=f"delete_rule_{rule.id}",
                type="secondary",
                help="Remove this rule and its configured channels",
            ):
                try:
                    run_async(service.delete_rule(str(rule.id), user))
                    st.success("Rule deleted")
                    st.rerun()
                except Exception as exc:  # pragma: no cover - UI feedback
                    st.error(f"Failed to delete rule: {exc}")
            if has_permission(user, Permission.UPDATE_ALERT_RULE) and cols[1].button(
                "Edit",
                key=f"edit_rule_{rule.id}",
                type="primary",
            ):
                st.session_state["editing_rule_id"] = str(rule.id)
                st.session_state["editing_rule"] = rule
                st.rerun()

    st.divider()

    editing_rule = st.session_state.get("editing_rule")
    if editing_rule:
        st.markdown(f"### Edit Rule: {editing_rule.name}")
    elif has_permission(user, Permission.CREATE_ALERT_RULE):
        st.markdown("### Create Rule")

    if not editing_rule and not has_permission(user, Permission.CREATE_ALERT_RULE):
        st.info("You do not have permission to create rules.")
        return

    saved, rule_data = render_alert_rule_editor(
        rule=editing_rule,
        user=user,
        alert_service=service,
    )

    if saved:
        try:
            if editing_rule and isinstance(rule_data, AlertRuleUpdate):
                run_async(service.update_rule(str(editing_rule.id), rule_data, user))
                st.success("Rule updated")
            elif isinstance(rule_data, AlertRuleCreate):
                run_async(service.create_rule(rule_data, user))
                st.success("Rule created")
            st.session_state.pop("editing_rule", None)
            st.session_state.pop("editing_rule_id", None)
            st.rerun()
        except Exception as exc:  # pragma: no cover - UI feedback
            st.error(f"Failed to save rule: {exc}")


def _render_alert_history_section(user: dict[str, Any], service: AlertConfigService) -> None:
    st.subheader("Alert History")
    events: list[AlertEvent] = []
    try:
        events = run_async(service.get_alert_events())
    except Exception as exc:  # pragma: no cover - fallback
        logger.warning("alert_history_fetch_failed", exc_info=exc)
        st.info("Alert history unavailable.")

    can_ack = has_permission(user, Permission.ACKNOWLEDGE_ALERT)

    def _on_ack(alert_id: str, note: str) -> None:
        run_async(service.acknowledge_alert(alert_id, note, user))

    render_alert_history(
        events, can_acknowledge=can_ack, on_acknowledge=_on_ack if can_ack else None
    )


def _render_channels_section(user: dict[str, Any], service: AlertConfigService) -> None:
    st.subheader("Notification Channels")
    try:
        rules = run_async(service.get_rules())
    except Exception as exc:  # pragma: no cover - UI feedback
        st.error(f"Failed to load channels: {exc}")
        return
    if not rules:
        st.info("No rules configured.")
        return

    for rule in rules:
        st.markdown(f"**{rule.name}**")
        if not rule.channels:
            st.text("No channels configured.")
            continue

        for channel in rule.channels:
            st.text(
                f"- {channel.type.value}: {mask_recipient(channel.recipient, channel.type.value)} "
                f"({'enabled' if channel.enabled else 'disabled'})"
            )


@operations_requires_auth
def render_alerts_page(user: dict[str, Any], db_pool: Any) -> None:
    """Render alert configuration page."""
    if not FEATURE_ALERTS:
        st.info("Alert configuration is disabled.")
        return

    if not has_permission(user, Permission.VIEW_ALERTS):
        st.error("Permission denied: VIEW_ALERTS required")
        st.stop()

    st.title("Alert Configuration")

    alert_service = _get_alert_service(db_pool)

    tab1, tab2, tab3 = st.tabs(["Alert Rules", "Alert History", "Channels"])

    with tab1:
        _render_alert_rules_section(user, alert_service)

    with tab2:
        _render_alert_history_section(user, alert_service)

    with tab3:
        _render_channels_section(user, alert_service)


__all__ = ["render_alerts_page"]
