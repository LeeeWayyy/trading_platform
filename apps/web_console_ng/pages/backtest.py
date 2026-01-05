"""Backtest Manager page for NiceGUI web console (P5T7).

Provides a web interface for:
- Submitting new backtest jobs
- Monitoring running job status with progressive polling
- Viewing completed backtest results
- Comparing multiple backtests
- Exporting results (with RBAC permission check)

PARITY: Mirrors apps/web_console/pages/backtest.py functionality
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

from nicegui import run, ui

from apps.web_console_ng import config
from apps.web_console_ng.auth.middleware import get_current_user, requires_auth
from apps.web_console_ng.core.client_lifecycle import ClientLifecycleManager
from apps.web_console_ng.core.dependencies import get_sync_db_pool, get_sync_redis_client
from apps.web_console_ng.ui.layout import main_layout
from libs.web_console_auth.permissions import Permission, has_permission

if TYPE_CHECKING:
    from psycopg_pool import ConnectionPool
    from redis import Redis

    from libs.backtest.job_queue import BacktestJobQueue

logger = logging.getLogger(__name__)

# Constants
BACKTEST_JOB_QUERY_LIMIT = 50
MAX_COMPARISON_SELECTIONS = 5
DEFAULT_END_DATE_OFFSET_DAYS = 1
DEFAULT_BACKTEST_PERIOD_DAYS = 730  # ~2 years
MIN_BACKTEST_PERIOD_DAYS = 30

# Polling intervals (progressive backoff) in seconds
POLL_INTERVALS = {
    30: 2.0,    # < 30s: 2s
    60: 5.0,    # < 60s: 5s
    300: 10.0,  # < 5min: 10s
    None: 30.0,  # > 5min: 30s
}

# Valid job statuses
VALID_STATUSES = {"pending", "running", "completed", "failed", "cancelled"}


def _get_user_id(user: dict[str, Any]) -> str:
    """Get user identifier with fail-closed behavior.

    Prefers user_id, falls back to username. Raises if neither present
    to prevent cross-user visibility with "anonymous" fallback.
    """
    user_id = user.get("user_id") or user.get("username")
    if not user_id:
        raise ValueError("User identification required - both user_id and username missing")
    return str(user_id)


def _get_job_queue() -> BacktestJobQueue:
    """Get BacktestJobQueue instance (sync context manager)."""
    from libs.backtest.job_queue import BacktestJobQueue as _BacktestJobQueue

    redis_client = get_sync_redis_client()
    db_pool = get_sync_db_pool()
    return _BacktestJobQueue(redis_client=redis_client, db_pool=db_pool)


def _get_user_jobs_sync(
    created_by: str,
    status: list[str],
    db_pool: ConnectionPool,
    redis_client: Redis,
) -> list[dict[str, Any]]:
    """Query jobs for a user with given statuses (sync).

    CRITICAL: Use DB status vocabulary (pending, running, completed, failed, cancelled).
    """
    from psycopg.rows import dict_row

    invalid = set(status) - VALID_STATUSES
    if invalid:
        raise ValueError(f"Invalid statuses: {invalid}. Valid: {VALID_STATUSES}")

    sql = f"""
        SELECT job_id, alpha_name, start_date, end_date, status, created_at,
               error_message, mean_ic, icir, hit_rate, coverage, average_turnover
        FROM backtest_jobs
        WHERE created_by = %s AND status = ANY(%s)
        ORDER BY created_at DESC
        LIMIT {BACKTEST_JOB_QUERY_LIMIT}
    """
    with db_pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, (created_by, status))
        jobs = cur.fetchall()

    if not jobs:
        return []

    # Fetch progress from Redis using MGET
    progress_keys = [f"backtest:progress:{job['job_id']}" for job in jobs]
    progress_values_raw: list[bytes | None] = redis_client.mget(progress_keys)  # type: ignore[assignment]

    result = []
    for job, progress_raw in zip(jobs, progress_values_raw, strict=True):
        progress = {"pct": 0}
        if progress_raw is not None:
            try:
                progress = json.loads(progress_raw)
            except (json.JSONDecodeError, TypeError):
                pass
        # Coerce and clamp progress_pct to handle string/None values from Redis
        raw_pct = progress.get("pct", 0)
        try:
            pct = float(raw_pct) if raw_pct is not None else 0.0
        except (ValueError, TypeError):
            pct = 0.0
        pct = max(0.0, min(pct, 100.0))  # Clamp to [0, 100]

        result.append(
            {
                "job_id": job["job_id"],
                "alpha_name": job["alpha_name"],
                "start_date": str(job["start_date"]),
                "end_date": str(job["end_date"]),
                "progress_pct": pct,
                "status": job["status"],
                "created_at": job["created_at"].isoformat() if job.get("created_at") else None,
                "error_message": job.get("error_message"),
                "mean_ic": job.get("mean_ic"),
                "icir": job.get("icir"),
                "hit_rate": job.get("hit_rate"),
                "coverage": job.get("coverage"),
                "average_turnover": job.get("average_turnover"),
            }
        )
    return result


def _get_available_alphas() -> list[str]:
    """Get list of registered alpha names from alpha library."""
    from libs.alpha.alpha_library import CANONICAL_ALPHAS

    return list(CANONICAL_ALPHAS.keys())


def _get_poll_interval(elapsed: float) -> float:
    """Get poll interval based on elapsed time (progressive backoff)."""
    for threshold, interval in sorted(POLL_INTERVALS.items(), key=lambda x: (x[0] or float("inf"))):
        if threshold is None or elapsed < threshold:
            return interval
    return 30.0


def _verify_job_ownership(job_id: str, user_id: str, db_pool: ConnectionPool) -> bool:
    """Verify that job_id belongs to user_id.

    SECURITY: Prevents cross-user job access via guessed/leaked job_ids.
    Returns True if job exists and belongs to user, False otherwise.
    """
    from psycopg.rows import dict_row

    with db_pool.connection() as conn:
        cur = conn.cursor(row_factory=dict_row)
        cur.execute(
            "SELECT created_by FROM backtest_jobs WHERE job_id = %s",
            (job_id,),
        )
        row = cur.fetchone()

    if not row:
        return False
    return row.get("created_by") == user_id


@ui.page("/backtest")
@requires_auth
@main_layout
async def backtest_page() -> None:
    """Backtest Manager page."""
    user = get_current_user()

    # Feature flag check
    if not config.FEATURE_BACKTEST_MANAGER:
        ui.label("Backtest Manager feature is disabled.").classes("text-lg")
        ui.label("Set FEATURE_BACKTEST_MANAGER=true to enable.").classes("text-gray-500")
        return

    # Permission check
    if not has_permission(user, Permission.VIEW_PNL):
        ui.label("Permission denied: VIEW_PNL required").classes("text-red-500 text-lg")
        return

    # Get sync infrastructure
    try:
        db_pool = get_sync_db_pool()
        redis_client = get_sync_redis_client()
    except RuntimeError as e:
        ui.label(f"Infrastructure unavailable: {e}").classes("text-red-500")
        return

    # Page title
    ui.label("Backtest Manager").classes("text-2xl font-bold mb-4")

    # Tabs
    with ui.tabs().classes("w-full") as tabs:
        tab_new = ui.tab("New Backtest")
        tab_running = ui.tab("Running Jobs")
        tab_results = ui.tab("Results")

    with ui.tab_panels(tabs, value=tab_new).classes("w-full"):
        # === NEW BACKTEST TAB ===
        with ui.tab_panel(tab_new):
            await _render_new_backtest_form(user)

        # === RUNNING JOBS TAB ===
        with ui.tab_panel(tab_running):
            await _render_running_jobs(user, db_pool, redis_client)

        # === RESULTS TAB ===
        with ui.tab_panel(tab_results):
            await _render_backtest_results(user, db_pool, redis_client)


async def _render_new_backtest_form(user: dict[str, Any]) -> None:
    """Render the new backtest submission form."""
    from libs.backtest.job_queue import BacktestJobConfig, JobPriority, WeightMethod

    with ui.card().classes("w-full p-4"):
        ui.label("Configure Backtest").classes("text-xl font-bold mb-4")

        with ui.row().classes("w-full gap-4"):
            # Left column
            with ui.column().classes("flex-1"):
                alpha_options = _get_available_alphas()
                if not alpha_options:
                    ui.label("No alpha signals registered in catalog").classes("text-yellow-600")
                    ui.label("Register alphas in libs/alpha/alpha_library.py").classes(
                        "text-sm text-gray-500"
                    )
                    return  # Cannot submit without alphas

                alpha_select = ui.select(
                    label="Alpha Signal",
                    options=alpha_options,
                    value=alpha_options[0],
                ).classes("w-full")

                # Default dates
                default_end = date.today() - timedelta(days=DEFAULT_END_DATE_OFFSET_DAYS)
                default_start = default_end - timedelta(days=DEFAULT_BACKTEST_PERIOD_DAYS)

                start_date_input = ui.date(value=default_start.isoformat()).classes("w-full")
                ui.label("Start Date").classes("text-sm text-gray-500 -mt-2")

                end_date_input = ui.date(value=default_end.isoformat()).classes("w-full")
                ui.label("End Date").classes("text-sm text-gray-500 -mt-2")

            # Right column
            with ui.column().classes("flex-1"):
                weight_options = [wm.value for wm in WeightMethod]
                weight_select = ui.select(
                    label="Weight Method",
                    options=weight_options,
                    value=weight_options[0],
                ).classes("w-full")
                ui.label(
                    "zscore: Standardized z-scores | quantile: Quantile bucketing | rank: Rank normalization"
                ).classes("text-xs text-gray-500")

                priority_options = ["normal", "high", "low"]
                priority_select = ui.select(
                    label="Priority",
                    options=priority_options,
                    value="normal",
                ).classes("w-full")

        async def submit_job() -> None:
            # Validate dates
            try:
                start_dt = date.fromisoformat(start_date_input.value)
                end_dt = date.fromisoformat(end_date_input.value)
            except (ValueError, TypeError):
                ui.notify("Invalid date format", type="negative")
                return

            if end_dt <= start_dt:
                ui.notify("End date must be after start date", type="negative")
                return

            if (end_dt - start_dt).days < MIN_BACKTEST_PERIOD_DAYS:
                ui.notify(f"Backtest period must be at least {MIN_BACKTEST_PERIOD_DAYS} days", type="negative")
                return

            # Build config
            try:
                priority = JobPriority(priority_select.value)
                weight_method = WeightMethod(weight_select.value)
            except ValueError as e:
                ui.notify(f"Invalid selection: {e}", type="negative")
                return

            job_config = BacktestJobConfig(
                alpha_name=alpha_select.value,
                start_date=start_dt,
                end_date=end_dt,
                weight_method=weight_method,
            )

            user_id = _get_user_id(user)

            # Submit job (sync operation)
            try:
                def submit_sync() -> Any:
                    queue = _get_job_queue()
                    return queue.enqueue(job_config, priority=priority, created_by=user_id)

                job = await run.io_bound(submit_sync)
                ui.notify(f"Backtest queued! Job ID: {job.id}", type="positive")
            except Exception as e:
                logger.exception("backtest_submit_failed", extra={"error": str(e)})
                ui.notify("Failed to submit backtest. Please try again.", type="negative")

        ui.button("Run Backtest", on_click=submit_job, color="primary").classes("mt-4")


async def _render_running_jobs(
    user: dict[str, Any],
    db_pool: ConnectionPool,
    redis_client: Redis,
) -> None:
    """Render list of running/queued jobs with progress."""
    jobs_data: list[dict[str, Any]] = []
    poll_elapsed = 0.0
    last_job_ids: set[str] = set()
    last_newest_created: str | None = None

    async def fetch_jobs() -> None:
        nonlocal jobs_data
        user_id = _get_user_id(user)
        jobs_data = await run.io_bound(
            lambda: _get_user_jobs_sync(
                created_by=user_id,
                status=["pending", "running"],
                db_pool=db_pool,
                redis_client=redis_client,
            )
        )

    await fetch_jobs()

    @ui.refreshable
    def jobs_list() -> None:
        if not jobs_data:
            ui.label("No running or queued jobs").classes("text-gray-500")
            return

        for job in jobs_data:
            with ui.card().classes("w-full p-4 mb-2"):
                with ui.row().classes("w-full items-center"):
                    # Job info
                    with ui.column().classes("flex-1"):
                        status_icon = "sync" if job["status"] == "running" else "hourglass_empty"
                        icon_class = "text-blue-600" if job["status"] == "running" else "text-gray-500"
                        with ui.row().classes("items-center gap-2"):
                            ui.icon(status_icon).classes(f"text-xl {icon_class}")
                            ui.label(job["alpha_name"]).classes("font-semibold")
                        ui.label(f"{job['start_date']} to {job['end_date']}").classes(
                            "text-xs text-gray-500"
                        )

                    # Progress
                    with ui.column().classes("w-32"):
                        ui.linear_progress(value=job["progress_pct"] / 100).classes("w-full")
                        ui.label(f"{job['progress_pct']:.0f}%").classes("text-xs text-center")

                    # Cancel button
                    async def cancel(job_id: str = job["job_id"]) -> None:
                        try:
                            # SECURITY: Verify job ownership before cancel
                            user_id = _get_user_id(user)
                            if not await run.io_bound(
                                lambda: _verify_job_ownership(job_id, user_id, db_pool)
                            ):
                                ui.notify("Job not found or access denied", type="negative")
                                return

                            def cancel_sync() -> None:
                                queue = _get_job_queue()
                                queue.cancel_job(job_id)

                            await run.io_bound(cancel_sync)
                            ui.notify("Job cancelled", type="positive")
                            await fetch_jobs()
                            jobs_list.refresh()
                        except Exception as e:
                            logger.exception("job_cancel_failed", extra={"job_id": job_id, "error": str(e)})
                            ui.notify("Failed to cancel job. Please try again.", type="negative")

                    ui.button("Cancel", on_click=cancel, color="red").props("flat")

    jobs_list()

    # Progressive polling with dynamic interval
    async def poll() -> None:
        nonlocal poll_elapsed, last_job_ids, last_newest_created
        await fetch_jobs()
        jobs_list.refresh()

        # Check if job set changed (reset polling on change)
        current_job_ids = {j["job_id"] for j in jobs_data}
        newest_created = max((j["created_at"] for j in jobs_data), default=None)

        if current_job_ids != last_job_ids or newest_created != last_newest_created:
            poll_elapsed = 0.0
        else:
            poll_elapsed += timer.interval

        last_job_ids = current_job_ids
        last_newest_created = newest_created

        # Update timer interval dynamically
        new_interval = _get_poll_interval(poll_elapsed)
        if timer.interval != new_interval:
            timer.interval = new_interval

    # Start with fast polling
    timer = ui.timer(_get_poll_interval(0), poll)

    # ⚠️ Timer lifecycle cleanup (see Note #29)
    client_id = ui.context.client.storage.get("client_id")
    if client_id:
        lifecycle_mgr = ClientLifecycleManager.get()
        await lifecycle_mgr.register_cleanup_callback(client_id, lambda: timer.cancel())


async def _render_backtest_results(
    user: dict[str, Any],
    db_pool: ConnectionPool,
    redis_client: Redis,
) -> None:
    """Render completed backtest results with visualization."""
    from libs.backtest.result_storage import BacktestResultStorage

    jobs_data: list[dict[str, Any]] = []
    comparison_mode = False
    selected_job_ids: list[str] = []

    async def fetch_completed_jobs() -> None:
        nonlocal jobs_data
        user_id = _get_user_id(user)
        jobs_data = await run.io_bound(
            lambda: _get_user_jobs_sync(
                created_by=user_id,
                status=["completed", "failed", "cancelled"],
                db_pool=db_pool,
                redis_client=redis_client,
            )
        )

    await fetch_completed_jobs()

    if not jobs_data:
        ui.label("No completed backtests yet. Submit a new backtest to get started.").classes(
            "text-gray-500"
        )
        return

    # Comparison mode toggle
    comparison_checkbox = ui.checkbox("Enable Comparison Mode")

    @ui.refreshable
    def results_display() -> None:
        nonlocal comparison_mode, selected_job_ids
        comparison_mode = comparison_checkbox.value

        if comparison_mode:
            # Multi-select for comparison
            completed_jobs = [j for j in jobs_data if j["status"] == "completed"]
            if len(completed_jobs) < 2:
                ui.label("Need at least 2 completed backtests for comparison").classes(
                    "text-yellow-600"
                )
                return

            options = {
                j["job_id"]: f"{j['alpha_name']} ({j['start_date']} - {j['end_date']})"
                for j in completed_jobs
            }

            select = ui.select(
                label="Select backtests to compare (max 5)",
                options=options,
                multiple=True,
                value=[],
            ).classes("w-full")

            async def show_comparison() -> None:
                if len(select.value) < 2:
                    ui.notify("Select at least 2 backtests to compare", type="warning")
                    return
                if len(select.value) > MAX_COMPARISON_SELECTIONS:
                    ui.notify(f"Maximum {MAX_COMPARISON_SELECTIONS} selections allowed", type="warning")
                    return

                # SECURITY: Verify ownership for all selected jobs
                user_id = _get_user_id(user)

                def check_ownership(jid: str) -> bool:
                    return _verify_job_ownership(jid, user_id, db_pool)

                for job_id in select.value:
                    if not await run.io_bound(check_ownership, job_id):
                        ui.notify("Job not found or access denied", type="negative")
                        return

                # Load full results for comparison
                storage = BacktestResultStorage(db_pool)
                results = []

                def load_result(jid: str) -> Any:
                    return storage.get_result(jid)

                for job_id in select.value:
                    try:
                        result = await run.io_bound(load_result, job_id)
                        results.append(result)
                    except Exception as e:
                        logger.exception("result_load_failed", extra={"job_id": job_id, "error": str(e)})
                        ui.notify("Failed to load result. Please try again.", type="negative")

                if len(results) >= 2:
                    _render_comparison_table(results)

            ui.button("Compare Selected", on_click=show_comparison, color="primary").classes("mt-2")

        else:
            # Single result view
            for job in jobs_data:
                status_icon = (
                    "check_circle" if job["status"] == "completed"
                    else "cancel" if job["status"] == "failed"
                    else "warning"
                )
                status_color = (
                    "text-green-600" if job["status"] == "completed"
                    else "text-red-600" if job["status"] == "failed"
                    else "text-yellow-600"
                )

                with ui.expansion(
                    f"{job['alpha_name']} ({job['start_date']} - {job['end_date']})"
                ).classes("w-full"):
                    with ui.row().classes("items-center gap-2 mb-2"):
                        ui.icon(status_icon).classes(f"text-xl {status_color}")
                        ui.label(job["status"].upper()).classes(f"font-medium {status_color}")

                    if job["status"] == "failed":
                        ui.label(f"Error: {job.get('error_message', 'Unknown error')}").classes(
                            "text-red-500"
                        )
                    elif job["status"] == "cancelled":
                        ui.label("Cancelled by user").classes("text-yellow-600")
                    else:
                        # Load and display full result
                        async def show_result(job_id: str = job["job_id"]) -> None:
                            try:
                                # SECURITY: Verify job ownership before loading result
                                user_id = _get_user_id(user)
                                if not await run.io_bound(
                                    lambda: _verify_job_ownership(job_id, user_id, db_pool)
                                ):
                                    ui.notify("Result not found or access denied", type="negative")
                                    return

                                storage = BacktestResultStorage(db_pool)
                                result = await run.io_bound(lambda: storage.get_result(job_id))
                                _render_backtest_result(result, user)
                            except Exception as e:
                                logger.exception("result_load_failed", extra={"job_id": job_id, "error": str(e)})
                                ui.notify("Failed to load result. Please try again.", type="negative")

                        ui.button("Load Details", on_click=show_result).props("flat")

                        # Show summary metrics inline
                        with ui.row().classes("gap-4 mt-2"):
                            if job.get("mean_ic") is not None:
                                ui.label(f"Mean IC: {job['mean_ic']:.4f}").classes("text-sm")
                            if job.get("icir") is not None:
                                ui.label(f"ICIR: {job['icir']:.2f}").classes("text-sm")
                            if job.get("hit_rate") is not None:
                                ui.label(f"Hit Rate: {job['hit_rate'] * 100:.1f}%").classes("text-sm")

    comparison_checkbox.on_value_change(results_display.refresh)
    results_display()


def _render_backtest_result(result: Any, user: dict[str, Any]) -> None:
    """Render complete backtest result with metrics and charts."""
    # Header
    ui.label(f"Backtest: {result.alpha_name}").classes("text-xl font-bold")
    ui.label(
        f"Period: {result.start_date} to {result.end_date} | "
        f"Days: {result.n_days} | "
        f"Avg Symbols: {result.n_symbols_avg:.0f}"
    ).classes("text-sm text-gray-500 mb-4")

    # Metrics summary
    with ui.row().classes("w-full gap-4 mb-4"):
        with ui.card().classes("flex-1 p-3 text-center"):
            ui.label("Mean IC").classes("text-sm text-gray-500")
            ui.label(f"{result.mean_ic:.4f}" if result.mean_ic is not None else "N/A").classes(
                "text-lg font-bold"
            )
        with ui.card().classes("flex-1 p-3 text-center"):
            ui.label("ICIR").classes("text-sm text-gray-500")
            ui.label(f"{result.icir:.2f}" if result.icir is not None else "N/A").classes(
                "text-lg font-bold"
            )
        with ui.card().classes("flex-1 p-3 text-center"):
            ui.label("Hit Rate").classes("text-sm text-gray-500")
            ui.label(
                f"{result.hit_rate * 100:.1f}%" if result.hit_rate is not None else "N/A"
            ).classes("text-lg font-bold")
        with ui.card().classes("flex-1 p-3 text-center"):
            ui.label("Coverage").classes("text-sm text-gray-500")
            ui.label(
                f"{result.coverage * 100:.1f}%" if result.coverage is not None else "N/A"
            ).classes("text-lg font-bold")
        with ui.card().classes("flex-1 p-3 text-center"):
            turnover = result.turnover_result.average_turnover if result.turnover_result else None
            ui.label("Avg Turnover").classes("text-sm text-gray-500")
            ui.label(f"{turnover:.2%}" if turnover is not None else "N/A").classes(
                "text-lg font-bold"
            )

    # Export buttons (if permitted)
    if has_permission(user, Permission.EXPORT_DATA):
        ui.separator().classes("my-4")
        ui.label("Export Data").classes("text-lg font-bold mb-2")

        with ui.row().classes("gap-2"):
            # Metrics JSON export
            metrics_dict = {
                "backtest_id": result.backtest_id,
                "alpha_name": result.alpha_name,
                "start_date": str(result.start_date),
                "end_date": str(result.end_date),
                "mean_ic": result.mean_ic,
                "icir": result.icir,
                "hit_rate": result.hit_rate,
                "coverage": result.coverage,
                "n_days": result.n_days,
                "n_symbols_avg": result.n_symbols_avg,
            }
            if result.turnover_result:
                metrics_dict["average_turnover"] = result.turnover_result.average_turnover

            metrics_json = json.dumps(metrics_dict, indent=2, default=str)

            def download_metrics() -> None:
                ui.download(
                    metrics_json.encode(),
                    filename=f"metrics_{result.backtest_id}.json",
                )

            ui.button("Download Metrics JSON", on_click=download_metrics)
    else:
        ui.label("Export requires EXPORT_DATA permission (Operator or Admin role)").classes(
            "text-sm text-gray-500 mt-4"
        )


def _render_comparison_table(results: list[Any]) -> None:
    """Render side-by-side comparison of multiple backtests."""
    if len(results) < 2:
        ui.label("Select at least 2 backtests to compare").classes("text-gray-500")
        return

    # Build comparison table
    columns: list[dict[str, Any]] = [
        {"name": "alpha", "label": "Alpha", "field": "alpha", "sortable": True},
        {"name": "period", "label": "Period", "field": "period"},
        {"name": "mean_ic", "label": "Mean IC", "field": "mean_ic", "sortable": True},
        {"name": "icir", "label": "ICIR", "field": "icir", "sortable": True},
        {"name": "hit_rate", "label": "Hit Rate", "field": "hit_rate", "sortable": True},
        {"name": "coverage", "label": "Coverage", "field": "coverage", "sortable": True},
        {"name": "turnover", "label": "Avg Turnover", "field": "turnover", "sortable": True},
        {"name": "days", "label": "Days", "field": "days", "sortable": True},
    ]

    rows = []
    for result in results:
        turnover = result.turnover_result.average_turnover if result.turnover_result else None
        rows.append({
            "alpha": result.alpha_name,
            "period": f"{result.start_date} - {result.end_date}",
            "mean_ic": f"{result.mean_ic:.4f}" if result.mean_ic is not None else "N/A",
            "icir": f"{result.icir:.2f}" if result.icir is not None else "N/A",
            "hit_rate": f"{result.hit_rate * 100:.1f}%" if result.hit_rate is not None else "N/A",
            "coverage": f"{result.coverage * 100:.1f}%" if result.coverage is not None else "N/A",
            "turnover": f"{turnover:.2%}" if turnover is not None else "N/A",
            "days": result.n_days,
        })

    ui.table(columns=columns, rows=rows).classes("w-full")


__all__ = ["backtest_page"]
