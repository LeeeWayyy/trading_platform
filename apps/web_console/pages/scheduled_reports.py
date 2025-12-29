"""Scheduled reports management page."""

from __future__ import annotations

import streamlit as st

from apps.web_console.auth import get_current_user
from apps.web_console.auth.permissions import Permission, has_permission
from apps.web_console.auth.streamlit_helpers import requires_auth
from apps.web_console.components.report_history_table import render_report_history_table
from apps.web_console.components.report_schedule_form import render_report_schedule_form
from apps.web_console.services.scheduled_reports_service import (
    ReportSchedule,
    ScheduledReportsService,
)
from apps.web_console.utils.async_helpers import run_async
from apps.web_console.utils.db_pool import get_db_pool


def _get_service(user: dict[str, object]) -> ScheduledReportsService | None:
    db_pool = get_db_pool()
    if db_pool is None:
        return None
    return ScheduledReportsService(db_pool=db_pool, user=dict(user))


def _select_schedule(schedules: list[ReportSchedule]) -> ReportSchedule | None:
    if not schedules:
        return None
    options = {f"{schedule.name} ({schedule.report_type})": schedule for schedule in schedules}
    selection = st.selectbox("Select Schedule", list(options.keys()))
    return options[selection]


@requires_auth
def main() -> None:
    st.set_page_config(page_title="Scheduled Reports", page_icon="R", layout="wide")
    st.title("Scheduled Reports")

    user = get_current_user()

    if not has_permission(user, Permission.VIEW_REPORTS):
        st.error("Permission denied: VIEW_REPORTS required.")
        st.stop()

    service = _get_service(user)
    if service is None:
        st.error("Database connection unavailable.")
        return

    try:
        schedules = run_async(service.list_schedules(user_id=user.get("user_id")))
    except Exception as exc:
        st.error(f"Failed to load schedules: {exc}")
        return

    can_manage = has_permission(user, Permission.MANAGE_REPORTS)

    st.subheader("Schedules")

    selected_schedule = _select_schedule(schedules)

    if not schedules:
        st.info("No schedules configured yet.")

    if can_manage:
        with st.expander("Create New Schedule", expanded=not schedules):
            submitted, payload = render_report_schedule_form(
                form_key="create_schedule_form",
            )
            if submitted and payload:
                try:
                    run_async(
                        service.create_schedule(
                            name=payload["name"],
                            report_type=payload["report_type"],
                            cron=payload["cron"],
                            params=payload["params"],
                            user_id=user.get("user_id", "unknown"),
                        )
                    )
                except Exception as exc:
                    st.error(f"Failed to create schedule: {exc}")
                else:
                    st.success("Schedule created.")
                    st.rerun()

    if selected_schedule:
        st.divider()
        st.markdown("### Schedule Details")
        st.json(
            {
                "id": selected_schedule.id,
                "name": selected_schedule.name,
                "report_type": selected_schedule.report_type,
                "cron": selected_schedule.cron,
                "enabled": selected_schedule.enabled,
                "last_run_at": selected_schedule.last_run_at,
                "next_run_at": selected_schedule.next_run_at,
            }
        )

        if can_manage:
            with st.expander("Edit Schedule", expanded=False):
                submitted, payload = render_report_schedule_form(
                    schedule=selected_schedule,
                    form_key=f"edit_schedule_form_{selected_schedule.id}",
                )
                if submitted and payload:
                    try:
                        run_async(
                            service.update_schedule(
                                selected_schedule.id,
                                updates=payload,
                            )
                        )
                    except Exception as exc:
                        st.error(f"Failed to update schedule: {exc}")
                    else:
                        st.success("Schedule updated.")
                        st.rerun()

            if st.button(
                "Delete Schedule",
                type="secondary",
                key=f"delete_schedule_{selected_schedule.id}",
            ):
                try:
                    deleted = run_async(service.delete_schedule(selected_schedule.id))
                except Exception as exc:
                    st.error(f"Failed to delete schedule: {exc}")
                else:
                    if deleted:
                        st.success("Schedule deleted.")
                        st.rerun()
                    else:
                        st.warning("Schedule not found.")

        st.divider()

        try:
            runs = run_async(service.get_run_history(selected_schedule.id))
        except Exception as exc:
            st.error(f"Failed to load run history: {exc}")
            runs = []

        def _download(run_id: str) -> bytes | None:
            path = run_async(service.download_archive(run_id))
            return path.read_bytes() if path else None

        render_report_history_table(runs, on_download=_download)


if __name__ == "__main__":
    main()
