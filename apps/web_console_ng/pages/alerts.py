"""Alert Configuration page for NiceGUI web console (P5T7).

Provides alert rule management, alert history, and notification channels display.

Features:
    - Tab layout (Alert Rules, Alert History, Channels)
    - Alert rules list with expandable JSON details
    - Rule create/edit form (with permissions)
    - Rule delete button (with permission)
    - Alert history table with acknowledgment
    - Notification channels display with PII masking

PARITY: Mirrors apps/web_console/pages/alerts.py functionality
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import psycopg
from nicegui import app, ui

from apps.web_console_ng import config
from apps.web_console_ng.auth.middleware import get_current_user, requires_auth
from apps.web_console_ng.core.database import get_db_pool
from apps.web_console_ng.ui.layout import main_layout
from libs.platform.alerts.models import ChannelConfig, ChannelType
from libs.platform.alerts.pii import mask_recipient
from libs.platform.web_console_auth.permissions import Permission, has_permission

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

    from libs.platform.alerts.models import AlertEvent, AlertRule
    from libs.web_console_services.alert_service import AlertConfigService

logger = logging.getLogger(__name__)

# Condition types for alert rules
# PARITY: Must match apps/web_console/components/alert_rule_editor.py:CONDITION_TYPES
CONDITION_TYPES = [
    "drawdown",
    "position_limit",
    "latency",
]

# Comparison operators
COMPARISONS = [">=", "<=", ">", "<", "=="]

# Minimum acknowledgment note length
MIN_ACK_NOTE_LENGTH = 15


def _get_alert_service(db_pool: AsyncConnectionPool) -> AlertConfigService:
    """Get AlertConfigService with async pool (global cache)."""
    if not hasattr(app.storage, "_alert_service"):
        from libs.platform.web_console_auth.audit_log import AuditLogger
        from libs.web_console_services.alert_service import AlertConfigService

        audit_logger = AuditLogger(db_pool)
        app.storage._alert_service = AlertConfigService(db_pool, audit_logger)  # noqa: B010

    service: AlertConfigService = getattr(app.storage, "_alert_service")  # noqa: B009
    return service


@ui.page("/alerts")
@requires_auth
@main_layout
async def alerts_page() -> None:
    """Alert Configuration page."""
    user = get_current_user()

    # Feature flag check
    if not config.FEATURE_ALERTS:
        ui.label("Alert Configuration feature is disabled.").classes("text-lg")
        ui.label("Set FEATURE_ALERTS=true to enable.").classes("text-gray-500")
        return

    # Permission check
    if not has_permission(user, Permission.VIEW_ALERTS):
        ui.label("Permission denied: VIEW_ALERTS required").classes("text-red-500 text-lg")
        return

    # Get async db pool
    async_pool = get_db_pool()
    if async_pool is None:
        ui.label("Database not configured. Contact administrator.").classes("text-red-500")
        return

    alert_service = _get_alert_service(async_pool)

    # Page title
    ui.label("Alert Configuration").classes("text-2xl font-bold mb-4")

    # Tabs
    with ui.tabs().classes("w-full") as tabs:
        tab_rules = ui.tab("Alert Rules")
        tab_history = ui.tab("Alert History")
        tab_channels = ui.tab("Channels")

    with ui.tab_panels(tabs, value=tab_rules).classes("w-full"):
        with ui.tab_panel(tab_rules):
            await _render_alert_rules(user, alert_service)

        with ui.tab_panel(tab_history):
            await _render_alert_history(user, alert_service)

        with ui.tab_panel(tab_channels):
            await _render_channels(user, alert_service)


async def _render_alert_rules(user: dict[str, Any], alert_service: AlertConfigService) -> None:
    """Render alert rules section."""
    rules_data: list[AlertRule] = []

    async def fetch_rules() -> None:
        nonlocal rules_data
        try:
            rules_data = await alert_service.get_rules()
        except psycopg.OperationalError as e:
            logger.warning(
                "rules_fetch_db_error",
                extra={"error": str(e), "operation": "fetch_rules"},
            )
            rules_data = []
        except ValueError as e:
            logger.warning(
                "rules_fetch_validation_error",
                extra={"error": str(e), "operation": "fetch_rules"},
            )
            rules_data = []

    await fetch_rules()

    can_delete = has_permission(user, Permission.DELETE_ALERT_RULE)
    can_update = has_permission(user, Permission.UPDATE_ALERT_RULE)
    can_create = has_permission(user, Permission.CREATE_ALERT_RULE)

    @ui.refreshable
    def rules_list() -> None:
        ui.label("Existing Rules").classes("text-xl font-bold mb-2")

        if not rules_data:
            ui.label("No alert rules configured.").classes("text-gray-500")
            return

        for rule in rules_data:
            with ui.expansion(f"{rule.name} ({rule.condition_type})").classes("w-full"):
                # Rule details
                ui.json_editor(
                    {
                        "content": {
                            "json": {
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
                        }
                    },
                    on_change=lambda e: None,
                ).classes("w-full")

                with ui.row().classes("gap-2 mt-2"):
                    if can_delete:

                        async def delete_rule(rule_id: str = str(rule.id)) -> None:
                            try:
                                await alert_service.delete_rule(rule_id, user)
                                ui.notify("Rule deleted", type="positive")
                                await fetch_rules()
                                rules_list.refresh()
                            except PermissionError as e:
                                logger.exception(
                                    "rule_delete_permission_denied",
                                    extra={
                                        "rule_id": rule_id,
                                        "error": str(e),
                                        "operation": "delete_rule",
                                    },
                                )
                                ui.notify(f"Permission denied: {e}", type="negative")
                            except psycopg.OperationalError as e:
                                logger.exception(
                                    "rule_delete_db_error",
                                    extra={
                                        "rule_id": rule_id,
                                        "error": str(e),
                                        "operation": "delete_rule",
                                    },
                                )
                                ui.notify("Database error. Please try again.", type="negative")
                            except ValueError as e:
                                logger.exception(
                                    "rule_delete_validation_error",
                                    extra={
                                        "rule_id": rule_id,
                                        "error": str(e),
                                        "operation": "delete_rule",
                                    },
                                )
                                ui.notify(f"Invalid rule ID: {e}", type="negative")

                        ui.button("Delete", on_click=delete_rule, color="red").props("flat")

                    if can_update:
                        ui.label("Edit functionality available in full version").classes(
                            "text-sm text-gray-500"
                        )

    rules_list()

    ui.separator().classes("my-4")

    # Create rule form
    if can_create:
        ui.label("Create New Rule").classes("text-xl font-bold mb-2")

        with ui.card().classes("w-full p-4"):
            name_input = ui.input(
                label="Rule Name", placeholder="e.g., High Drawdown Alert"
            ).classes("w-full")

            with ui.row().classes("gap-4"):
                condition_select = ui.select(
                    label="Condition Type",
                    options=CONDITION_TYPES,
                    value=CONDITION_TYPES[0],
                ).classes("w-48")

                comparison_select = ui.select(
                    label="Comparison",
                    options=COMPARISONS,
                    value=">=",
                ).classes("w-24")

                threshold_input = ui.number(
                    label="Threshold Value",
                    value=0.05,
                    format="%.4f",
                ).classes("w-32")

            enabled_checkbox = ui.checkbox("Enabled", value=True)

            ui.label("Notification Channels").classes("font-medium mt-4")

            # Simple channel configuration
            with ui.row().classes("gap-4"):
                email_input = ui.input(
                    label="Email (optional)", placeholder="alert@company.com"
                ).classes("w-64")
                slack_input = ui.input(
                    label="Slack Webhook (optional)", placeholder="https://hooks.slack.com/..."
                ).classes("w-64")

            async def create_rule() -> None:
                from libs.web_console_services.alert_service import AlertRuleCreate

                name = name_input.value
                if not name or len(name.strip()) < 3:
                    ui.notify("Rule name must be at least 3 characters", type="negative")
                    return

                channels = []
                if email_input.value:
                    channels.append(
                        ChannelConfig(
                            type=ChannelType.EMAIL,
                            recipient=email_input.value.strip(),
                            enabled=True,
                        )
                    )
                if slack_input.value:
                    channels.append(
                        ChannelConfig(
                            type=ChannelType.SLACK,
                            recipient=slack_input.value.strip(),
                            enabled=True,
                        )
                    )

                if not channels:
                    ui.notify("At least one notification channel is required", type="negative")
                    return

                rule_create = AlertRuleCreate(
                    name=name.strip(),
                    condition_type=condition_select.value,
                    threshold_value=Decimal(str(threshold_input.value)),
                    comparison=comparison_select.value,
                    channels=channels,
                    enabled=enabled_checkbox.value,
                )

                try:
                    await alert_service.create_rule(rule_create, user)
                    ui.notify("Rule created successfully", type="positive")
                    # Clear form
                    name_input.value = ""
                    email_input.value = ""
                    slack_input.value = ""
                    await fetch_rules()
                    rules_list.refresh()
                except PermissionError as e:
                    ui.notify(f"Permission denied: {e}", type="negative")
                except psycopg.OperationalError as e:
                    logger.exception(
                        "rule_create_db_error",
                        extra={"error": str(e), "operation": "create_rule"},
                    )
                    ui.notify("Database error. Please try again.", type="negative")
                except ValueError as e:
                    logger.exception(
                        "rule_create_validation_error",
                        extra={"error": str(e), "operation": "create_rule"},
                    )
                    ui.notify(f"Invalid input: {e}", type="negative")

            ui.button("Create Rule", on_click=create_rule, color="primary").classes("mt-4")
    else:
        ui.label("You do not have permission to create rules.").classes("text-gray-500")


async def _render_alert_history(user: dict[str, Any], alert_service: AlertConfigService) -> None:
    """Render alert history section."""
    events_data: list[AlertEvent] = []

    async def fetch_events() -> None:
        nonlocal events_data
        try:
            events_data = await alert_service.get_alert_events()
        except psycopg.OperationalError as e:
            logger.warning(
                "events_fetch_db_error",
                extra={"error": str(e), "operation": "fetch_events"},
            )
            events_data = []
        except ValueError as e:
            logger.warning(
                "events_fetch_validation_error",
                extra={"error": str(e), "operation": "fetch_events"},
            )
            events_data = []

    await fetch_events()

    can_acknowledge = has_permission(user, Permission.ACKNOWLEDGE_ALERT)

    @ui.refreshable
    def history_display() -> None:
        ui.label("Alert History").classes("text-xl font-bold mb-2")

        if not events_data:
            ui.label("No alert events recorded.").classes("text-gray-500")
            return

        # Events table
        columns: list[dict[str, Any]] = [
            {"name": "timestamp", "label": "Timestamp", "field": "timestamp", "sortable": True},
            {"name": "rule_name", "label": "Rule", "field": "rule_name"},
            {"name": "severity", "label": "Severity", "field": "severity"},
            {"name": "status", "label": "Status", "field": "status"},
            {"name": "message", "label": "Message", "field": "message"},
        ]

        rows: list[dict[str, Any]] = []
        for event in events_data:
            status = "Acknowledged" if event.acknowledged_at else "Pending"
            # Use getattr for fields that may not exist on all event models
            severity = getattr(event, "severity", "medium") or "medium"
            message = getattr(event, "message", "") or ""
            rows.append(
                {
                    "timestamp": event.triggered_at.isoformat() if event.triggered_at else "-",
                    "rule_name": event.rule_name or "-",
                    "severity": severity,
                    "status": status,
                    "message": message[:50] + "..." if len(message) > 50 else message,
                }
            )

        ui.table(columns=columns, rows=rows).classes("w-full")

        # Acknowledgment section
        if can_acknowledge:
            ui.separator().classes("my-4")
            ui.label("Acknowledge Alert").classes("font-bold mb-2")

            pending_events = [e for e in events_data if not e.acknowledged_at]
            if not pending_events:
                ui.label("No pending alerts to acknowledge.").classes("text-gray-500")
                return

            event_options = {
                str(
                    e.id
                ): f"{e.rule_name} @ {e.triggered_at.isoformat() if e.triggered_at else 'Unknown'}"
                for e in pending_events
            }

            event_select = ui.select(
                label="Select Alert",
                options=event_options,
                value=list(event_options.keys())[0] if event_options else None,
            ).classes("w-full max-w-md")

            note_input = ui.textarea(
                label=f"Acknowledgment Note (min {MIN_ACK_NOTE_LENGTH} characters)",
                placeholder="Describe the action taken...",
            ).classes("w-full max-w-md")

            async def acknowledge() -> None:
                if not event_select.value:
                    ui.notify("Select an alert to acknowledge", type="negative")
                    return

                note = note_input.value
                if not note or len(note.strip()) < MIN_ACK_NOTE_LENGTH:
                    ui.notify(
                        f"Note must be at least {MIN_ACK_NOTE_LENGTH} characters", type="negative"
                    )
                    return

                try:
                    await alert_service.acknowledge_alert(event_select.value, note.strip(), user)
                    ui.notify("Alert acknowledged", type="positive")
                    note_input.value = ""
                    await fetch_events()
                    history_display.refresh()
                except PermissionError as e:
                    logger.exception(
                        "alert_acknowledge_permission_denied",
                        extra={
                            "event_id": event_select.value,
                            "error": str(e),
                            "operation": "acknowledge_alert",
                        },
                    )
                    ui.notify(f"Permission denied: {e}", type="negative")
                except psycopg.OperationalError as e:
                    logger.exception(
                        "alert_acknowledge_db_error",
                        extra={
                            "event_id": event_select.value,
                            "error": str(e),
                            "operation": "acknowledge_alert",
                        },
                    )
                    ui.notify("Database error. Please try again.", type="negative")
                except ValueError as e:
                    logger.exception(
                        "alert_acknowledge_validation_error",
                        extra={
                            "event_id": event_select.value,
                            "error": str(e),
                            "operation": "acknowledge_alert",
                        },
                    )
                    ui.notify(f"Invalid input: {e}", type="negative")

            ui.button("Acknowledge", on_click=acknowledge, color="primary").classes("mt-2")

    history_display()


async def _render_channels(user: dict[str, Any], alert_service: AlertConfigService) -> None:
    """Render notification channels section."""
    rules_data: list[AlertRule] = []

    try:
        rules_data = await alert_service.get_rules()
    except psycopg.OperationalError as e:
        logger.warning(
            "channels_fetch_db_error",
            extra={"error": str(e), "operation": "get_rules_for_channels"},
        )
    except ValueError as e:
        logger.warning(
            "channels_fetch_validation_error",
            extra={"error": str(e), "operation": "get_rules_for_channels"},
        )

    ui.label("Notification Channels").classes("text-xl font-bold mb-2")
    ui.label("Channels configured for each alert rule.").classes("text-gray-500 text-sm mb-4")

    if not rules_data:
        ui.label("No rules configured.").classes("text-gray-500")
        return

    for rule in rules_data:
        with ui.card().classes("w-full p-4 mb-2"):
            ui.label(rule.name).classes("font-bold text-lg")

            if not rule.channels:
                ui.label("No channels configured.").classes("text-gray-500 text-sm")
                continue

            for channel in rule.channels:
                status_icon = "check_circle" if channel.enabled else "cancel"
                status_color = "text-green-600" if channel.enabled else "text-red-600"
                masked_recipient = mask_recipient(channel.recipient, channel.type.value)

                with ui.row().classes("items-center gap-2"):
                    ui.icon(status_icon).classes(f"{status_color}")
                    ui.label(f"{channel.type.value.upper()}:").classes("font-medium")
                    ui.label(masked_recipient).classes("text-gray-600")
                    ui.label("enabled" if channel.enabled else "disabled").classes(
                        f"text-sm {status_color}"
                    )


__all__ = ["alerts_page"]
