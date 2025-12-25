"""Sync schedule configuration editor."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd
import streamlit as st

from apps.web_console.schemas.data_management import SyncScheduleUpdateDTO
from apps.web_console.services.data_sync_service import DataSyncService
from libs.common.async_utils import run_async

_FETCH_TIMEOUT_SECONDS = 10.0
_UPDATE_TIMEOUT_SECONDS = 10.0


def render_sync_schedule_editor(service: DataSyncService, user: Any) -> None:
    """Render schedule configuration forms for datasets."""

    st.subheader("Sync Schedule")

    try:
        with st.spinner("Loading schedules..."):
            schedules = run_async(service.get_sync_schedule(user), timeout=_FETCH_TIMEOUT_SECONDS)
    except Exception as exc:
        st.error(f"Failed to load schedules: {exc}")
        return

    if not schedules:
        st.info("No schedules available for your datasets.")
        return

    summary_rows = [
        {
            "Dataset": schedule.dataset,
            "Enabled": "Yes" if schedule.enabled else "No",
            "Cron": schedule.cron_expression,
            "Last Run": _format_dt(schedule.last_scheduled_run),
            "Next Run": _format_dt(schedule.next_scheduled_run),
        }
        for schedule in schedules
    ]
    st.dataframe(pd.DataFrame(summary_rows), use_container_width=True)

    st.caption("Update schedule settings per dataset.")

    for schedule in schedules:
        with st.expander(f"Edit {schedule.dataset}"):
            with st.form(f"schedule_form_{schedule.dataset}"):
                enabled = st.checkbox(
                    "Enabled",
                    value=schedule.enabled,
                    help="Disable to pause scheduled syncs",
                )
                cron_expression = st.text_input(
                    "Cron Expression",
                    value=schedule.cron_expression,
                    help="Use standard 5-field cron format",
                )
                submitted = st.form_submit_button("Save", type="primary")

            if submitted:
                payload = SyncScheduleUpdateDTO(
                    enabled=enabled,
                    cron_expression=cron_expression.strip() or None,
                )
                try:
                    with st.spinner("Updating schedule..."):
                        updated = run_async(
                            service.update_sync_schedule(
                                user=user,
                                dataset=schedule.dataset,
                                schedule=payload,
                            ),
                            timeout=_UPDATE_TIMEOUT_SECONDS,
                        )
                except Exception as exc:
                    st.error(f"Failed to update schedule: {exc}")
                else:
                    st.success("Schedule updated.")
                    st.caption(
                        f"Next run: {_format_dt(updated.next_scheduled_run)} · "
                        f"Cron: {updated.cron_expression}"
                    )


def _format_dt(value: datetime | None) -> str:
    if value is None:
        return "-"
    return value.strftime("%Y-%m-%d %H:%M:%S")


__all__ = ["render_sync_schedule_editor"]
