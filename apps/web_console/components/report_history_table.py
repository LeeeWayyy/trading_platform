"""Table component for scheduled report run history."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

import streamlit as st

from apps.web_console.services.scheduled_reports_service import ReportRun


def render_report_history_table(
    runs: list[ReportRun],
    *,
    on_download: Callable[[str], bytes | None] | None = None,
) -> None:
    """Render report run history with optional download action."""

    st.subheader("Run History")

    if not runs:
        st.info("No runs recorded yet.")
        return

    header_cols = st.columns([2, 2, 2, 2, 3, 2])
    header_cols[0].markdown("**Run Key**")
    header_cols[1].markdown("**Status**")
    header_cols[2].markdown("**Started**")
    header_cols[3].markdown("**Completed**")
    header_cols[4].markdown("**Error**")
    header_cols[5].markdown("**Download**")

    for run in runs:
        cols = st.columns([2, 2, 2, 2, 3, 2])
        cols[0].write(run.run_key)
        cols[1].write(run.status)
        cols[2].write(_format_dt(run.started_at))
        cols[3].write(_format_dt(run.completed_at))
        cols[4].write(run.error_message or "-")

        if on_download is None or run.status.lower() != "completed":
            cols[5].write("-")
            continue

        archive_bytes = on_download(run.id)
        if not archive_bytes:
            cols[5].write("Unavailable")
            continue

        # Determine file format from run data or default to PDF
        file_format = run.__dict__.get("format", "pdf").lower()
        if file_format == "html":
            file_name = f"report_{run.run_key}.html"
            mime_type = "text/html"
        elif file_format == "pdf":
            file_name = f"report_{run.run_key}.pdf"
            mime_type = "application/pdf"
        else:
            file_name = f"report_{run.run_key}.{file_format}"
            mime_type = "application/octet-stream"

        cols[5].download_button(
            "Download",
            data=archive_bytes,
            file_name=file_name,
            mime=mime_type,
            key=f"download_{run.id}",
        )


def _format_dt(value: datetime | None) -> str:
    if value is None:
        return "-"
    return value.strftime("%Y-%m-%d %H:%M:%S")


__all__ = ["render_report_history_table"]
