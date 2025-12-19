"""Backtest configuration form component.

Provides a form for submitting new backtest jobs with:
- Alpha signal selection from CANONICAL_ALPHAS registry
- Date range picker with validation
- Weight method selection (zscore, quantile, rank)
- Priority selection (normal, high, low)
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta

import streamlit as st
from rq.job import Job

from libs.alpha.alpha_library import CANONICAL_ALPHAS
from libs.backtest.job_queue import BacktestJobConfig, JobPriority, WeightMethod

# Constants for default date calculations
DEFAULT_END_DATE_OFFSET_DAYS = 1  # Days before today for default end date
DEFAULT_BACKTEST_PERIOD_DAYS = 730  # ~2 years default backtest period
MIN_BACKTEST_PERIOD_DAYS = 30  # Minimum required backtest period


def get_available_alphas() -> list[str]:
    """Get list of registered alpha names from alpha library.

    Returns:
        List of canonical alpha names (momentum, reversal, value, quality, volatility)
    """
    return list(CANONICAL_ALPHAS.keys())


def render_backtest_form(
    on_submit: Callable[[BacktestJobConfig, JobPriority, str], Job] | None = None,
    get_current_username: Callable[[], str] | None = None,
) -> Job | None:
    """Render backtest configuration form.

    Args:
        on_submit: Callback function(config, priority, username) -> Job
                   If None, form submission is disabled
        get_current_username: Function to get current authenticated username
                              If None, uses "anonymous"

    Returns:
        Job if submitted successfully, None otherwise
    """
    with st.form("backtest_config", clear_on_submit=False):
        st.subheader("Configure Backtest")

        col1, col2 = st.columns(2)

        with col1:
            alpha_name = st.selectbox(
                "Alpha Signal",
                options=get_available_alphas(),
                help="Select the alpha signal to backtest. "
                "See docs/CONCEPTS/alpha-signals.md for details.",
            )

            # Default date range: last 2 years
            default_end = date.today() - timedelta(days=DEFAULT_END_DATE_OFFSET_DAYS)
            default_start = default_end - timedelta(days=DEFAULT_BACKTEST_PERIOD_DAYS)

            start_date = st.date_input(
                "Start Date",
                value=default_start,
                max_value=default_end,
                help="Backtest start date (inclusive)",
            )
            end_date = st.date_input(
                "End Date",
                value=default_end,
                min_value=start_date if start_date else default_start,
                help="Backtest end date (inclusive)",
            )

        with col2:
            weight_method_str = st.selectbox(
                "Weight Method",
                options=[wm.value for wm in WeightMethod],
                help="How to convert signals to portfolio weights:\n"
                "- zscore: Standardized z-scores\n"
                "- quantile: Quantile-based bucketing\n"
                "- rank: Rank-based normalization",
            )
            priority_str = st.selectbox(
                "Priority",
                options=["normal", "high", "low"],
                index=0,
                help="Job priority in the queue:\n"
                "- high: Processed first\n"
                "- normal: Standard priority\n"
                "- low: Processed when queue is empty",
            )

        # Form submission
        submitted = st.form_submit_button("Run Backtest", type="primary")

        if submitted:
            # Validate date range
            if end_date <= start_date:
                st.error("End date must be after start date")
                return None

            # Validate minimum date range
            date_diff = (end_date - start_date).days
            if date_diff < MIN_BACKTEST_PERIOD_DAYS:
                st.error(f"Backtest period must be at least {MIN_BACKTEST_PERIOD_DAYS} days")
                return None

            # Validate priority enum
            try:
                priority = JobPriority(priority_str)
            except ValueError:
                st.error(f"Invalid priority: {priority_str}")
                return None

            # Build config with validated weight method enum
            try:
                weight_method = WeightMethod(weight_method_str)
            except ValueError:
                st.error(f"Invalid weight method: {weight_method_str}")
                return None

            config = BacktestJobConfig(
                alpha_name=alpha_name,
                start_date=start_date,
                end_date=end_date,
                weight_method=weight_method,
            )

            # Get username
            username = "anonymous"
            if get_current_username is not None:
                username = get_current_username()

            # Submit job
            if on_submit is not None:
                try:
                    job = on_submit(config, priority, username)
                    st.success(f"Backtest queued! Job ID: {job.id}")
                    return job
                except Exception as e:
                    st.error(f"Failed to submit backtest: {e}")
                    return None
            else:
                st.warning("Job submission not configured")
                return None

    return None


__all__ = ["render_backtest_form", "get_available_alphas"]
