"""Form component for scheduled report creation/editing."""

from __future__ import annotations

import json
from typing import Any

import streamlit as st

from apps.web_console.components.cron_expression_input import render_cron_expression_input
from apps.web_console.services.scheduled_reports_service import ReportSchedule

_DEFAULT_REPORT_TYPES = [
    "daily_summary",
    "weekly_performance",
    "risk_snapshot",
    "custom",
]


def render_report_schedule_form(
    *,
    schedule: ReportSchedule | None = None,
    form_key: str = "report_schedule_form",
    report_type_options: list[str] | None = None,
) -> tuple[bool, dict[str, Any] | None]:
    """Render the schedule form and return a payload on submit."""

    report_types = list(report_type_options or _DEFAULT_REPORT_TYPES)
    if schedule and schedule.report_type not in report_types:
        report_types.insert(0, schedule.report_type)

    default_params = schedule.params if schedule else {}
    params_text = json.dumps(default_params, indent=2)

    button_label = "Update Schedule" if schedule else "Create Schedule"

    with st.form(form_key):
        name = st.text_input("Schedule Name", value=schedule.name if schedule else "")
        report_type = st.selectbox(
            "Report Type",
            options=report_types,
            index=0 if not schedule else report_types.index(schedule.report_type),
        )
        cron_expression = render_cron_expression_input(
            "Cron Expression",
            schedule.cron if schedule and schedule.cron else "0 6 * * *",
            key=f"cron_{form_key}",
        )
        enabled = st.checkbox("Enabled", value=schedule.enabled if schedule else True)
        params_raw = st.text_area(
            "Report Parameters (JSON)",
            value=params_text,
            height=160,
            help="Optional key/value parameters passed to report generation.",
        )
        submitted = st.form_submit_button(button_label, type="primary")

    if not submitted:
        return False, None

    try:
        params = json.loads(params_raw) if params_raw.strip() else {}
    except json.JSONDecodeError as exc:
        st.error(f"Invalid JSON in parameters: {exc}")
        return False, None

    payload = {
        "name": name.strip(),
        "report_type": report_type,
        "cron": cron_expression,
        "params": params,
        "enabled": enabled,
    }
    return True, payload


__all__ = ["render_report_schedule_form"]
