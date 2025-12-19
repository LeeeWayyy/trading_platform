"""Backtest Manager page.

Provides a web interface for:
- Submitting new backtest jobs
- Monitoring running job status with progressive polling
- Viewing completed backtest results
- Comparing multiple backtests
- Exporting results (with RBAC permission check)

Dependencies:
- T5.1: BacktestJobQueue for job submission
- T5.2: BacktestResultStorage for result retrieval
- T6.1: OAuth2 auth (uses dev stub while pending)
"""

from __future__ import annotations

import json
from typing import Any

import streamlit as st
from psycopg.rows import dict_row
from rq.job import Job
from streamlit_autorefresh import st_autorefresh  # type: ignore[import-not-found]

from apps.web_console.auth.backtest_auth import backtest_requires_auth
from apps.web_console.auth.permissions import Permission, has_permission
from apps.web_console.auth.streamlit_helpers import get_user_info
from apps.web_console.components.backtest_form import render_backtest_form
from apps.web_console.components.backtest_results import (
    render_backtest_result,
    render_comparison_table,
)
from apps.web_console.utils.sync_db_pool import (
    get_job_queue,
    get_sync_db_pool,
    get_sync_redis_client,
)
from libs.backtest.job_queue import BacktestJobConfig, JobPriority
from libs.backtest.result_storage import BacktestResultStorage

# Valid job statuses in the database
VALID_STATUSES = {"pending", "running", "completed", "failed", "cancelled"}

# Query and UI limits
BACKTEST_JOB_QUERY_LIMIT = 50  # Max jobs to fetch per query
MAX_COMPARISON_SELECTIONS = 5  # Max backtests to compare at once


def _get_user_with_role() -> dict[str, Any]:
    """Get user info including role for RBAC checks.

    CRITICAL: get_user_info() only returns username/email/user_id/auth_method/session_id.
    It does NOT include role/strategies. We must read role from session_state directly.
    The dev stub and OAuth2 both set st.session_state["role"].
    """
    user_info = get_user_info()
    # Add role from session_state (set by both dev stub and OAuth2)
    user_info["role"] = st.session_state.get("role", "viewer")
    user_info["strategies"] = st.session_state.get("strategies", [])
    return user_info


def get_poll_interval_ms(elapsed_seconds: float) -> int:
    """Progressive polling: start fast, then back off for long/terminal jobs.

    Args:
        elapsed_seconds: Time since polling started

    Returns:
        Polling interval in milliseconds

    Intervals:
    - 0-30s: 2s (fast updates for quick jobs)
    - 30-60s: 5s
    - 60-300s: 10s
    - 300s+: 30s (slow updates for long-running jobs)
    """
    if elapsed_seconds < 30:
        return 2000
    if elapsed_seconds < 60:
        return 5000
    if elapsed_seconds < 300:
        return 10_000
    return 30_000


def get_user_jobs(created_by: str, status: list[str]) -> list[dict[str, Any]]:
    """Query jobs for a user with given statuses.

    Args:
        created_by: Username to filter by
        status: List of status strings to filter

    Returns:
        List of job dicts with progress info

    Uses sync query against Postgres backtest_jobs table.

    CRITICAL: Use DB status vocabulary (pending, running, completed, failed, cancelled),
    NOT RQ vocabulary (queued, started, finished).
    """
    invalid = set(status) - VALID_STATUSES
    if invalid:
        raise ValueError(f"Invalid statuses: {invalid}. Valid: {VALID_STATUSES}")

    pool = get_sync_db_pool()
    # Include error_message and summary metrics for terminal job display and comparison
    sql = f"""
        SELECT job_id, alpha_name, start_date, end_date, status, created_at,
               error_message, mean_ic, icir, hit_rate, coverage, average_turnover
        FROM backtest_jobs
        WHERE created_by = %s AND status = ANY(%s)
        ORDER BY created_at DESC
        LIMIT {BACKTEST_JOB_QUERY_LIMIT}
    """
    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, (created_by, status))
        jobs = cur.fetchall()

    # Return early if no jobs
    if not jobs:
        return []

    # Fetch progress from Redis for all jobs at once using MGET for efficiency
    redis = get_sync_redis_client()
    progress_keys = [f"backtest:progress:{job['job_id']}" for job in jobs]
    # Sync Redis.mget returns list[bytes | None] (mypy stubs incorrectly suggest Awaitable)
    progress_values_raw: list[bytes | None] = redis.mget(progress_keys)  # type: ignore[assignment]

    result = []
    for job, progress_raw in zip(jobs, progress_values_raw, strict=True):
        # Defensive parsing: handle malformed JSON from partial writes or manual debug
        progress = {"pct": 0}
        if progress_raw is not None:
            try:
                progress = json.loads(progress_raw)
            except (json.JSONDecodeError, TypeError):
                # Malformed data - use default progress
                pass
        result.append(
            {
                "job_id": job["job_id"],
                "alpha_name": job["alpha_name"],
                "start_date": str(job["start_date"]),
                "end_date": str(job["end_date"]),
                "progress_pct": progress.get("pct", 0),
                "status": job["status"],
                "created_at": job["created_at"].isoformat() if job.get("created_at") else None,
                # Terminal job fields for error display and comparison
                "error_message": job.get("error_message"),
                "mean_ic": job.get("mean_ic"),
                "icir": job.get("icir"),
                "hit_rate": job.get("hit_rate"),
                "coverage": job.get("coverage"),
                "average_turnover": job.get("average_turnover"),
            }
        )
    return result


def get_current_username() -> str:
    """Get username from session using standard auth pattern."""
    try:
        user_info = get_user_info()
        return user_info.get("username", "anonymous")
    except RuntimeError:
        st.warning("No authenticated user in session")
        return "anonymous"


def submit_backtest_job(
    config: BacktestJobConfig,
    priority: JobPriority,
    username: str,
) -> Job:
    """Submit a backtest job to the queue.

    Args:
        config: BacktestJobConfig with alpha, dates, weight_method
        priority: Job priority (high, normal, low)
        username: User submitting the job

    Returns:
        Job object from queue.enqueue()
    """
    with get_job_queue() as queue:
        job = queue.enqueue(config, priority=priority, created_by=username)
    return job


def render_running_jobs() -> None:
    """Render list of running/queued jobs with status."""
    created_by = get_current_username()

    # Progressive polling with st_autorefresh
    elapsed = st.session_state.get("backtest_poll_elapsed", 0.0)
    interval_ms = get_poll_interval_ms(elapsed)
    st_autorefresh(interval=interval_ms, key="backtest_poll")
    st.session_state["backtest_poll_elapsed"] = elapsed + interval_ms / 1000

    # Fetch jobs for current user only
    jobs = get_user_jobs(created_by=created_by, status=["pending", "running"])

    # Polling reset logic - reset elapsed when job set changes
    current_job_ids = {j["job_id"] for j in jobs}
    last_job_ids = st.session_state.get("backtest_last_job_ids", set())
    newest_created = max((j["created_at"] for j in jobs), default=None)
    last_newest = st.session_state.get("backtest_last_newest", None)

    if not jobs or current_job_ids != last_job_ids or newest_created != last_newest:
        st.session_state["backtest_poll_elapsed"] = 0.0

    st.session_state["backtest_last_job_ids"] = current_job_ids
    st.session_state["backtest_last_newest"] = newest_created

    if not jobs:
        st.info("No running or queued jobs")
        return

    for job in jobs:
        with st.container():
            col1, col2, col3 = st.columns([3, 1, 1])
            with col1:
                status_icon = "🔄" if job["status"] == "running" else "⏳"
                st.write(
                    f"{status_icon} **{job['alpha_name']}** "
                    f"({job['start_date']} to {job['end_date']})"
                )
            with col2:
                st.progress(job["progress_pct"] / 100)
                st.caption(f"{job['progress_pct']:.0f}%")
            with col3:
                if st.button("Cancel", key=f"cancel_{job['job_id']}"):
                    with get_job_queue() as queue:
                        queue.cancel_job(job["job_id"])
                    st.rerun()


def render_backtest_results() -> None:
    """Render completed backtest results with visualization."""
    created_by = get_current_username()
    user_info = _get_user_with_role()

    # Fetch completed, failed, cancelled jobs
    jobs = get_user_jobs(
        created_by=created_by,
        status=["completed", "failed", "cancelled"],
    )

    if not jobs:
        st.info("No completed backtests yet. Submit a new backtest to get started.")
        return

    # Comparison mode toggle
    comparison_mode = st.checkbox("Enable Comparison Mode")

    if comparison_mode:
        # Multi-select for comparison
        completed_jobs = [j for j in jobs if j["status"] == "completed"]
        if len(completed_jobs) < 2:
            st.warning("Need at least 2 completed backtests for comparison")
        else:
            options = {
                f"{j['alpha_name']} ({j['start_date']} - {j['end_date']})": j["job_id"]
                for j in completed_jobs
            }
            selected = st.multiselect(
                "Select backtests to compare",
                options=list(options.keys()),
                max_selections=MAX_COMPARISON_SELECTIONS,
            )
            if len(selected) >= 2:
                # Load full results for comparison
                pool = get_sync_db_pool()
                storage = BacktestResultStorage(pool)
                results = []
                for label in selected:
                    job_id = options[label]
                    try:
                        result = storage.get_result(job_id)
                        results.append(result)
                    except Exception as e:
                        st.error(f"Failed to load {label}: {e}")

                if results:
                    render_comparison_table(results)

    else:
        # Single result view
        for job in jobs:
            with st.expander(
                f"{'✅' if job['status'] == 'completed' else '❌' if job['status'] == 'failed' else '⚠️'} "
                f"{job['alpha_name']} ({job['start_date']} - {job['end_date']})",
                expanded=False,
            ):
                if job["status"] == "failed":
                    st.error(f"Failed: {job.get('error_message', 'Unknown error')}")
                elif job["status"] == "cancelled":
                    st.warning("Cancelled by user")
                else:
                    # Load full result for completed jobs
                    try:
                        pool = get_sync_db_pool()
                        storage = BacktestResultStorage(pool)
                        result = storage.get_result(job["job_id"])
                        render_backtest_result(result, user_info=user_info)
                    except Exception as e:
                        st.error(f"Failed to load result: {e}")


@backtest_requires_auth
def render_backtest_page() -> None:
    """Backtest configuration and results page."""
    # RBAC: Require VIEW_PNL permission to access backtest UI
    user_info = _get_user_with_role()
    if not has_permission(user_info, Permission.VIEW_PNL):
        st.error("Permission denied: VIEW_PNL required to access Backtest Manager.")
        st.stop()
        return

    st.header("Backtest Manager")

    tab1, tab2, tab3 = st.tabs(["New Backtest", "Running Jobs", "Results"])

    with tab1:
        render_backtest_form(
            on_submit=submit_backtest_job,
            get_current_username=get_current_username,
        )

    with tab2:
        render_running_jobs()

    with tab3:
        render_backtest_results()


# Page entry point
if __name__ == "__main__":
    render_backtest_page()
