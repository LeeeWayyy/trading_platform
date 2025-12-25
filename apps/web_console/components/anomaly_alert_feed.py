"""Anomaly alert feed component."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd
import streamlit as st

from apps.web_console.services.data_quality_service import DataQualityService
from libs.common.async_utils import run_async

_FETCH_TIMEOUT_SECONDS = 10.0


def render_anomaly_alert_feed(service: DataQualityService, user: Any) -> None:
    """Render recent anomaly alerts with filters."""

    st.subheader("Anomaly Alerts")

    with st.form("anomaly_filters"):
        severity = st.selectbox("Severity", options=["All", "warning", "critical"])
        acknowledged = st.selectbox("Acknowledged", options=["All", "No", "Yes"])
        submitted = st.form_submit_button("Load Alerts", type="primary")

    if not submitted:
        st.info("Apply filters to load anomaly alerts.")
        return

    severity_filter = None if severity == "All" else severity
    if acknowledged == "All":
        ack_filter = None
    else:
        ack_filter = acknowledged == "Yes"

    try:
        with st.spinner("Loading anomaly alerts..."):
            alerts = run_async(
                service.get_anomaly_alerts(
                    user=user,
                    severity=severity_filter,
                    acknowledged=ack_filter,
                ),
                timeout=_FETCH_TIMEOUT_SECONDS,
            )
    except Exception as exc:
        st.error(f"Failed to load alerts: {exc}")
        return

    if not alerts:
        st.info("No anomaly alerts found for the selected filters.")
        return

    rows = [
        {
            "Time": _format_dt(alert.created_at),
            "Dataset": alert.dataset,
            "Severity": alert.severity,
            "Metric": alert.metric,
            "Deviation %": alert.deviation_pct if alert.deviation_pct is not None else "-",
            "Acknowledged": "Yes" if alert.acknowledged else "No",
            "Message": alert.message,
        }
        for alert in alerts
    ]
    st.dataframe(pd.DataFrame(rows), use_container_width=True)

    with st.expander("Alert details"):
        for alert in alerts:
            st.markdown(
                f"**{alert.dataset}** · {alert.severity.upper()} · {_format_dt(alert.created_at)}"
            )
            st.write(alert.message)
            st.caption(
                f"Metric: {alert.metric} · Current: {alert.current_value} · "
                f"Expected: {alert.expected_value if alert.expected_value is not None else '-'}"
            )
            st.divider()


def _format_dt(value: datetime | None) -> str:
    if value is None:
        return "-"
    return value.strftime("%Y-%m-%d %H:%M:%S")


__all__ = ["render_anomaly_alert_feed"]
