"""Alpha Signal Explorer page."""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import streamlit as st

from apps.web_console.auth import get_current_user
from apps.web_console.auth.permissions import Permission, has_permission
from apps.web_console.auth.streamlit_helpers import requires_auth
from apps.web_console.components.decay_curve import render_decay_curve
from apps.web_console.components.ic_chart import render_ic_chart
from apps.web_console.components.signal_correlation_matrix import render_correlation_matrix
from apps.web_console.services.alpha_explorer_service import AlphaExplorerService
from libs.models.registry import ModelRegistry
from libs.models.types import ModelStatus

FEATURE_ALPHA_EXPLORER = os.getenv("FEATURE_ALPHA_EXPLORER", "false").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100


def _init_service() -> AlphaExplorerService:
    from libs.alpha.metrics import AlphaMetricsAdapter

    registry_dir = Path(os.getenv("MODEL_REGISTRY_DIR", "data/models"))
    registry = ModelRegistry(registry_dir=registry_dir)
    metrics_adapter = AlphaMetricsAdapter()
    return AlphaExplorerService(registry, metrics_adapter)


@requires_auth
def main() -> None:
    st.set_page_config(page_title="Alpha Signal Explorer", page_icon="📈", layout="wide")
    st.title("Alpha Signal Explorer")

    if not FEATURE_ALPHA_EXPLORER:
        st.info("Feature not available.")
        return

    user = get_current_user()
    if not has_permission(user, Permission.VIEW_ALPHA_SIGNALS):
        st.error("Permission denied: VIEW_ALPHA_SIGNALS required.")
        st.stop()

    service = _init_service()

    with st.sidebar:
        st.header("Filters")

        status_options = ["All", "staged", "production", "archived", "failed"]
        status_selection = st.selectbox("Status", status_options, index=0)
        status_filter = ModelStatus(status_selection) if status_selection != "All" else None

        min_ic = st.number_input("Min IC", value=0.0, step=0.01)
        max_ic = st.number_input("Max IC", value=1.0, step=0.01)

        page_size = st.selectbox("Page Size", [25, 50, 100], index=0)
        page_size = min(int(page_size), MAX_PAGE_SIZE)
        page_index = st.number_input("Page", min_value=1, value=1, step=1)

    offset = (page_index - 1) * page_size

    signals, total = service.list_signals(
        status=status_filter,
        min_ic=min_ic if min_ic > 0 else None,
        max_ic=max_ic if max_ic < 1 else None,
        limit=page_size,
        offset=offset,
    )

    st.caption(f"Showing {len(signals)} of {total} signals")

    if not signals:
        st.info("No signals found matching filters.")
        return

    signal_names = [s.display_name for s in signals]
    selected_idx = st.selectbox(
        "Select Signal", range(len(signal_names)), format_func=lambda i: signal_names[i]
    )
    selected = signals[selected_idx]

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Signal Metrics")
        metrics = service.get_signal_metrics(selected.signal_id)

        mcol1, mcol2, mcol3 = st.columns(3)
        mcol1.metric("Mean IC", f"{metrics.mean_ic:.3f}")
        mcol2.metric("ICIR", f"{metrics.icir:.2f}")
        mcol3.metric("Hit Rate", f"{metrics.hit_rate:.1%}")

        mcol1.metric("Coverage", f"{metrics.coverage:.1%}")
        mcol2.metric("Turnover", f"{metrics.average_turnover:.1%}")
        if metrics.decay_half_life:
            mcol3.metric("Half-life", f"{metrics.decay_half_life:.1f}d")

    with col2:
        st.subheader("Quick Actions")
        if st.button("Launch Backtest", type="primary"):
            st.session_state["backtest_signal"] = selected.signal_id
            st.switch_page("pages/backtest.py")

        export_payload = {
            "signal_id": metrics.signal_id,
            "name": metrics.name,
            "version": metrics.version,
            "mean_ic": metrics.mean_ic,
            "icir": metrics.icir,
            "hit_rate": metrics.hit_rate,
            "coverage": metrics.coverage,
            "average_turnover": metrics.average_turnover,
            "decay_half_life": metrics.decay_half_life,
            "n_days": metrics.n_days,
            "start_date": metrics.start_date,
            "end_date": metrics.end_date,
        }
        export_df = pd.DataFrame([export_payload])
        st.download_button(
            "Export Metrics",
            data=export_df.to_csv(index=False),
            file_name=f"alpha_signal_{metrics.signal_id}_metrics.csv",
            mime="text/csv",
        )

    st.divider()

    tab1, tab2, tab3 = st.tabs(["IC Time Series", "Decay Curve", "Correlation"])

    with tab1:
        ic_data = service.get_ic_timeseries(selected.signal_id)
        render_ic_chart(ic_data)

    with tab2:
        decay_data = service.get_decay_curve(selected.signal_id)
        render_decay_curve(decay_data, metrics.decay_half_life)

    with tab3:
        st.subheader("Signal Correlation")
        multi_select = st.multiselect(
            "Select Signals", options=signal_names, default=[selected.display_name]
        )
        selected_ids = [
            s.signal_id for s in signals if s.display_name in set(multi_select)
        ]
        if len(selected_ids) < 2:
            st.info("Select at least two signals to view correlation matrix")
        else:
            corr = service.compute_correlation(selected_ids)
            render_correlation_matrix(corr)


if __name__ == "__main__":
    main()
