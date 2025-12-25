"""Data Quality Reports page (T8.3)."""

from __future__ import annotations

from typing import cast

import streamlit as st

from apps.web_console.auth import get_current_user
from apps.web_console.auth.permissions import Permission, has_permission
from apps.web_console.auth.streamlit_helpers import requires_auth
from apps.web_console.components.anomaly_alert_feed import render_anomaly_alert_feed
from apps.web_console.components.coverage_chart import render_coverage_chart
from apps.web_console.components.quality_trend_chart import render_quality_trend_chart
from apps.web_console.components.validation_results_table import render_validation_results_table
from apps.web_console.services.data_quality_service import DataQualityService


def _get_data_quality_service() -> DataQualityService:
    if "data_quality_service" not in st.session_state:
        st.session_state["data_quality_service"] = DataQualityService()
    return cast(DataQualityService, st.session_state["data_quality_service"])


def render_data_quality_reports(user: dict[str, object]) -> None:
    st.title("Data Quality Reports")

    if not has_permission(user, Permission.VIEW_DATA_QUALITY):
        st.error("Permission denied: VIEW_DATA_QUALITY required")
        st.stop()

    service = _get_data_quality_service()

    tab1, tab2, tab3, tab4 = st.tabs(["Validation Results", "Anomaly Alerts", "Trends", "Coverage"])

    with tab1:
        render_validation_results_table(service, user)

    with tab2:
        render_anomaly_alert_feed(service, user)

    with tab3:
        render_quality_trend_chart(service, user)

    with tab4:
        render_coverage_chart(service, user)


@requires_auth
def main() -> None:
    st.set_page_config(page_title="Data Quality Reports", page_icon="✅", layout="wide")
    user = get_current_user()
    render_data_quality_reports(user)


if __name__ == "__main__":
    main()
