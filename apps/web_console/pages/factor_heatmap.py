"""Factor exposure heatmap page."""

from __future__ import annotations

import os
from datetime import date, timedelta
from pathlib import Path

import streamlit as st

from apps.web_console.auth import get_current_user
from apps.web_console.auth.permissions import Permission, has_permission
from apps.web_console.auth.streamlit_helpers import requires_auth
from apps.web_console.components.exposure_timeseries import render_exposure_timeseries
from apps.web_console.components.heatmap_chart import render_heatmap
from apps.web_console.components.stock_exposure_table import render_stock_exposure_table
from apps.web_console.services.factor_exposure_service import FactorExposureService
from apps.web_console.utils.db_pool import get_db_pool, get_redis_client
from libs.data_providers.compustat_local_provider import CompustatLocalProvider
from libs.data_providers.crsp_local_provider import CRSPLocalProvider
from libs.data_quality.manifest import ManifestManager
from libs.factors.factor_builder import FactorBuilder

FEATURE_FACTOR_HEATMAP = os.getenv("FEATURE_FACTOR_HEATMAP", "false").lower() in {
    "1",
    "true",
    "yes",
    "on",
}


def _init_service(user: dict[str, object]) -> FactorExposureService:
    """Build the factor exposure service with data providers.

    Args:
        user: Authenticated user dict from get_current_user().

    Returns:
        FactorExposureService wired with FactorBuilder and DB adapters.

    Example:
        >>> service = _init_service({"role": "admin"})
        >>> isinstance(service, FactorExposureService)
        True
    """

    manifest_manager = ManifestManager(Path("data/manifests"))
    crsp_provider = CRSPLocalProvider(
        storage_path=Path("data/wrds/crsp/daily"),
        manifest_manager=manifest_manager,
    )
    compustat_provider = CompustatLocalProvider(
        storage_path=Path("data/wrds/compustat"),
        manifest_manager=manifest_manager,
    )
    factor_builder = FactorBuilder(
        crsp_provider=crsp_provider,
        compustat_provider=compustat_provider,
        manifest_manager=manifest_manager,
    )

    db_adapter = get_db_pool()
    redis_client = get_redis_client()

    return FactorExposureService(
        factor_builder=factor_builder,
        db_adapter=db_adapter,
        redis_client=redis_client,
        user=dict(user),
    )


@requires_auth
def main() -> None:
    """Render the Factor Exposure Heatmap page in Streamlit.

    Returns:
        None. Displays controls, heatmap, time-series chart, and drill-down table.

    Example:
        >>> main()  # doctest: +SKIP
    """

    st.set_page_config(page_title="Factor Exposure Heatmap", page_icon="🎨", layout="wide")
    st.title("Factor Exposure Heatmap")

    if not FEATURE_FACTOR_HEATMAP:
        st.info("Feature not available.")
        return

    user = get_current_user()

    if not has_permission(user, Permission.VIEW_FACTOR_ANALYTICS):
        st.error("Permission denied: VIEW_FACTOR_ANALYTICS required.")
        st.stop()

    if not has_permission(user, Permission.VIEW_ALL_POSITIONS):
        st.error("Permission denied: VIEW_ALL_POSITIONS required for global positions.")
        st.stop()

    if get_db_pool() is None:
        st.error("Database connection unavailable. Set DATABASE_URL.")
        return

    st.warning("Positions are global (no strategy_id). Exposure views require VIEW_ALL_POSITIONS.")

    service = _init_service(user)

    with st.sidebar:
        st.header("Configuration")

        today = date.today()
        col1, col2 = st.columns(2)
        with col1:
            start_date = st.date_input("Start", today - timedelta(days=90))
        with col2:
            end_date = st.date_input("End", today)

        factor_defs = service.get_factor_definitions()
        factor_names = [factor.name for factor in factor_defs]
        selected_factors = st.multiselect(
            "Factors",
            factor_names,
            default=factor_names[:5],
        )

    if start_date > end_date:
        st.error("Start date must be on or before end date.")
        return

    if not selected_factors:
        st.info("Select at least one factor to compute exposures.")
        return

    with st.spinner("Computing exposures..."):
        exposure_data = service.get_portfolio_exposures(
            portfolio_id="global",
            start_date=start_date,
            end_date=end_date,
            factors=selected_factors,
        )

    render_heatmap(exposure_data.exposures)
    render_exposure_timeseries(exposure_data.exposures)

    st.divider()
    st.subheader("Stock-Level Drill-Down")

    col1, col2 = st.columns(2)
    with col1:
        drill_factor = st.selectbox("Factor", selected_factors)
    with col2:
        drill_date = st.date_input("As of Date", end_date)

    if st.button("Show Stock Exposures"):
        stock_exposures = service.get_stock_exposures(
            portfolio_id="global",
            factor=drill_factor,
            as_of_date=drill_date,
        )
        render_stock_exposure_table(stock_exposures)


if __name__ == "__main__":
    main()
