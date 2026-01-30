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
import math
import os
import re
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import plotly.graph_objects as go
import polars as pl
from nicegui import run, ui

from apps.web_console_ng import config
from apps.web_console_ng.auth.middleware import get_current_user, requires_auth
from apps.web_console_ng.core.client_lifecycle import ClientLifecycleManager
from apps.web_console_ng.core.dependencies import get_sync_db_pool, get_sync_redis_client
from apps.web_console_ng.ui.helpers import safe_classes
from apps.web_console_ng.ui.layout import main_layout
from libs.platform.web_console_auth.permissions import Permission, has_permission

if TYPE_CHECKING:
    from psycopg_pool import ConnectionPool
    from redis import Redis

    from libs.trading.backtest.job_queue import BacktestJobQueue

logger = logging.getLogger(__name__)

# Constants
BACKTEST_JOB_QUERY_LIMIT = 50
MAX_COMPARISON_SELECTIONS = 5
DEFAULT_END_DATE_OFFSET_DAYS = 1
DEFAULT_BACKTEST_PERIOD_DAYS = 730  # ~2 years
MIN_BACKTEST_PERIOD_DAYS = 30

# Polling intervals (progressive backoff) in seconds
POLL_INTERVALS = {
    30: 2.0,  # < 30s: 2s
    60: 5.0,  # < 60s: 5s
    300: 10.0,  # < 5min: 10s
    None: 30.0,  # > 5min: 30s
}

# Valid job statuses
VALID_STATUSES = {"pending", "running", "completed", "failed", "cancelled"}

# Symbol validation pattern: must start with letter, then alphanumeric/dots/hyphens (e.g., BRK.A, KHC)
# Prevents injection via malicious symbol names and enforces exchange naming conventions
SYMBOL_PATTERN = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")

# Cached Redis client for RQ (decode_responses=False for binary payloads)
_rq_redis_client: Redis | None = None


def _get_rq_redis_client() -> Redis:
    """Get a cached Redis client instance suitable for RQ.

    RQ expects binary Redis responses; decode_responses=True can raise
    UnicodeDecodeError when RQ stores non-UTF8 payloads.
    """
    global _rq_redis_client
    if _rq_redis_client is None:
        from redis import Redis as _Redis

        _rq_redis_client = _Redis.from_url(config.REDIS_URL, decode_responses=False)
    return _rq_redis_client


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
    from libs.trading.backtest.job_queue import BacktestJobQueue as _BacktestJobQueue

    redis_client = _get_rq_redis_client()
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

    sql = """
        SELECT job_id, alpha_name, start_date, end_date, status, created_at,
               error_message, mean_ic, icir, hit_rate, coverage, average_turnover,
               result_path,
               COALESCE(config_json->>'provider', 'crsp') AS provider
        FROM backtest_jobs
        WHERE created_by = %s AND status = ANY(%s)
        ORDER BY created_at DESC
        LIMIT %s
    """
    with db_pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, (created_by, status, BACKTEST_JOB_QUERY_LIMIT))
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
            except (json.JSONDecodeError, TypeError) as e:
                logger.debug(
                    "Failed to parse progress JSON, using default",
                    extra={"error": str(e)},
                )
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
                "result_path": job.get("result_path"),
                "provider": job.get("provider") or "crsp",
            }
        )
    return result


def _get_available_alphas() -> list[str]:
    """Get list of registered alpha names from alpha library."""
    from libs.trading.alpha.alpha_library import CANONICAL_ALPHAS

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
    Uses SELECT FOR UPDATE to prevent TOCTOU race conditions when followed
    by sensitive operations in the same transaction.

    Returns True if job exists and belongs to user, False otherwise.
    """
    from psycopg.rows import dict_row

    with db_pool.connection() as conn:
        cur = conn.cursor(row_factory=dict_row)
        # Use FOR UPDATE to lock the row and prevent TOCTOU race conditions
        # This ensures the ownership check and subsequent action are atomic
        cur.execute(
            "SELECT created_by FROM backtest_jobs WHERE job_id = %s FOR UPDATE",
            (job_id,),
        )
        row = cur.fetchone()
        # Note: Connection is released after context exits, releasing the lock
        # Callers needing atomicity should pass the connection and hold the lock

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
    from libs.trading.backtest.job_queue import (
        BacktestJobConfig,
        DataProvider,
        JobPriority,
        WeightMethod,
    )

    with ui.card().classes("w-full p-4"):
        ui.label("Configure Backtest").classes("text-xl font-bold mb-4")

        with ui.row().classes("w-full gap-4"):
            # Left column
            with ui.column().classes("flex-1"):
                data_root = Path(os.getenv("DATA_ROOT", "data")).resolve()
                crsp_manifest_path = data_root / "manifests" / "crsp.json"
                crsp_available = crsp_manifest_path.exists()

                data_provider_select = ui.select(
                    label="Data Source",
                    options=["CRSP (production)", "Yahoo Finance (dev only)"],
                    value="CRSP (production)",
                ).classes("w-full")
                provider_help = ui.label(
                    "CRSP is required for point-in-time backtests and universe filtering. "
                    "Yahoo Finance is for development only and does not support PIT checks."
                ).classes("text-xs text-gray-500")
                status_label = ui.label("").classes("text-xs")

                def _update_provider_status(value: str) -> None:
                    if value.startswith("CRSP"):
                        status_text = (
                            "CRSP manifest found"
                            if crsp_available
                            else "CRSP manifest missing (data/manifests/crsp.json)"
                        )
                        status_label.set_text(status_text)
                        safe_classes(
                            status_label,
                            replace="text-xs "
                            + ("text-green-600" if crsp_available else "text-red-600"),
                        )
                        provider_help.set_text(
                            "CRSP is required for point-in-time backtests and universe filtering. "
                            "Yahoo Finance is for development only and does not support PIT checks."
                        )
                    else:
                        status_label.set_text("Yahoo Finance uses cached data in data/yfinance")
                        safe_classes(status_label, replace="text-xs text-blue-600")
                        provider_help.set_text(
                            "Yahoo Finance is for development only and does not support PIT checks. "
                            "Use it to validate workflows before subscribing to CRSP."
                        )

                def _provider_event_value(event: Any) -> str:
                    if hasattr(event, "value"):
                        return str(event.value)
                    args = getattr(event, "args", None)
                    if isinstance(args, dict) and "value" in args:
                        return str(args["value"])
                    return str(data_provider_select.value)

                data_provider_select.on(
                    "update:model-value",
                    lambda e: _update_provider_status(_provider_event_value(e)),
                )
                _update_provider_status(data_provider_select.value)

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

                universe_input = ui.input(
                    label="Yahoo Universe (comma-separated tickers)",
                    placeholder="AAPL, MSFT, NVDA",
                ).classes("w-full")
                ui.label(
                    "Only used for Yahoo Finance. Leave blank to use a small default universe."
                ).classes("text-xs text-gray-500 -mt-2")

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

        # Cost Model Configuration (T9.2)
        with ui.expansion("Cost Model Settings", icon="attach_money").classes("w-full"):
            cost_enabled = ui.switch("Enable Cost Model", value=False)

            with ui.column().classes("w-full gap-2").bind_visibility_from(cost_enabled, "value"):
                portfolio_value_input = ui.number(
                    "Portfolio Value (USD)",
                    value=1_000_000,
                    min=10_000,
                    max=1_000_000_000,
                    step=100_000,
                ).props("prefix=$").classes("w-full")
                ui.label(
                    "Constant notional AUM for cost calculations"
                ).classes("text-xs text-gray-500 -mt-2")

                bps_per_trade_input = ui.number(
                    "Commission + Spread (bps)",
                    value=5.0,
                    min=0,
                    max=50,
                    step=0.5,
                ).classes("w-full")
                ui.label(
                    "Fixed cost per trade (commission + half-spread)"
                ).classes("text-xs text-gray-500 -mt-2")

                impact_coefficient_input = ui.number(
                    "Impact Coefficient (eta)",
                    value=0.1,
                    min=0.01,
                    max=1.0,
                    step=0.01,
                ).classes("w-full")
                ui.label(
                    "Almgren-Chriss market impact parameter"
                ).classes("text-xs text-gray-500 -mt-2")

                participation_limit_input = ui.number(
                    "ADV Participation Limit (%)",
                    value=5.0,
                    min=1,
                    max=20,
                    step=1,
                ).classes("w-full")
                ui.label(
                    "Max fraction of daily volume per trade (for capacity analysis)"
                ).classes("text-xs text-gray-500 -mt-2")

        def build_cost_config(provider: DataProvider) -> dict[str, Any] | None:
            """Build cost model config dict for extra_params.

            Args:
                provider: Selected data provider (determines adv_source)

            Returns:
                Cost model config dict or None if disabled
            """
            if not cost_enabled.value:
                return None
            # Set adv_source based on provider to accurately represent data provenance
            # CRSP provider uses PIT-compliant CRSP ADV data; Yahoo uses yahoo data
            adv_source = "crsp" if provider == DataProvider.CRSP else "yahoo"
            # Let the backend handle None values and type coercion.
            # The UI provides the participation limit in %, so we convert it to a fraction.
            part_limit_val = participation_limit_input.value
            part_limit_fraction = (part_limit_val / 100.0) if part_limit_val is not None else None

            return {
                "enabled": True,
                "bps_per_trade": bps_per_trade_input.value,
                "impact_coefficient": impact_coefficient_input.value,
                "participation_limit": part_limit_fraction,
                "adv_source": adv_source,
                "portfolio_value_usd": portfolio_value_input.value,
            }

        async def submit_job() -> None:
            ui.notify("Submitting backtest...", type="info")
            selected_provider = data_provider_select.value
            data_provider = (
                DataProvider.CRSP if selected_provider.startswith("CRSP") else DataProvider.YFINANCE
            )

            universe: list[str] | None = None
            if data_provider == DataProvider.YFINANCE:
                raw_universe_value = universe_input.value
                raw_universe = ""
                if raw_universe_value is not None:
                    raw_universe = str(raw_universe_value).strip()
                if raw_universe:
                    universe = [
                        symbol.strip().upper()
                        for symbol in raw_universe.split(",")
                        if symbol.strip()
                    ]
                    # SECURITY: Validate symbol format to prevent injection
                    invalid_symbols = [s for s in universe if not SYMBOL_PATTERN.match(s)]
                    if invalid_symbols:
                        # Sanitize for display/logging to prevent log injection
                        sanitized = [
                            s.replace("\n", "").replace("\r", "")[:10] for s in invalid_symbols[:5]
                        ]
                        ui.notify(
                            f"Invalid symbols: {', '.join(sanitized)}. "
                            "Symbols must start with a letter, be 1-10 characters (alphanumeric/dots/hyphens).",
                            type="negative",
                        )
                        return

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
                ui.notify(
                    f"Backtest period must be at least {MIN_BACKTEST_PERIOD_DAYS} days",
                    type="negative",
                )
                return

            # Validate reasonable date bounds
            today = date.today()
            if end_dt > today + timedelta(days=1):  # Allow tomorrow for timezone tolerance
                ui.notify("End date cannot be in the future", type="negative")
                return
            if start_dt.year < 1990 or end_dt.year > 2100:
                ui.notify("Dates must be between 1990 and 2100", type="negative")
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
                provider=data_provider,
            )
            if universe:
                job_config.extra_params["universe"] = universe

            # Add cost model configuration if enabled (T9.2)
            cost_config = build_cost_config(data_provider)
            if cost_config is not None:
                if data_provider == DataProvider.YFINANCE:
                    # Warn user that cost model will be skipped for Yahoo
                    ui.notify(
                        "Cost model enabled but Yahoo Finance lacks PIT ADV data. "
                        "Cost calculations will be skipped. Use CRSP for cost analysis.",
                        type="warning",
                    )
                job_config.extra_params["cost_model"] = cost_config

            try:
                user_id = _get_user_id(user)
            except ValueError as e:
                logger.error("backtest_submit_user_missing", extra={"error": str(e)}, exc_info=True)
                ui.notify("Failed to submit backtest: user identity missing", type="negative")
                return

            # Submit job (sync operation)
            try:

                def submit_sync() -> Any:
                    queue = _get_job_queue()
                    logger.info(
                        "backtest_submit_request",
                        extra={
                            "user_id": user_id,
                            "alpha_name": job_config.alpha_name,
                            "provider": job_config.provider.value,
                            "start_date": str(job_config.start_date),
                            "end_date": str(job_config.end_date),
                        },
                    )
                    return queue.enqueue(job_config, priority=priority, created_by=user_id)

                job = await run.io_bound(submit_sync)
                ui.notify(
                    f"Backtest queued! Job ID: {job.id} (data source: {data_provider.value})",
                    type="positive",
                )
            except (ConnectionError, OSError) as e:
                logger.error(
                    "backtest_submit_db_connection_failed",
                    extra={
                        "user_id": user_id,
                        "alpha_name": job_config.alpha_name,
                        "error": str(e),
                    },
                    exc_info=True,
                )
                ui.notify("Failed to submit backtest: Database connection error", type="negative")
            except (ValueError, TypeError) as e:
                logger.error(
                    "backtest_submit_data_error",
                    extra={
                        "user_id": user_id,
                        "alpha_name": job_config.alpha_name,
                        "error": str(e),
                    },
                    exc_info=True,
                )
                ui.notify("Failed to submit backtest: Invalid configuration", type="negative")

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
                        icon_class = (
                            "text-blue-600" if job["status"] == "running" else "text-gray-500"
                        )
                        with ui.row().classes("items-center gap-2"):
                            ui.icon(status_icon).classes(f"text-xl {icon_class}")
                            ui.label(job["alpha_name"]).classes("font-semibold")
                            ui.label(job.get("provider", "crsp").upper()).classes(
                                "text-xs text-gray-500"
                            )
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
                        except (ConnectionError, OSError) as e:
                            logger.error(
                                "job_cancel_db_connection_failed",
                                extra={"job_id": job_id, "error": str(e)},
                                exc_info=True,
                            )
                            ui.notify(
                                "Failed to cancel job: Database connection error", type="negative"
                            )
                        except (ValueError, TypeError) as e:
                            logger.error(
                                "job_cancel_data_error",
                                extra={"job_id": job_id, "error": str(e)},
                                exc_info=True,
                            )
                            ui.notify("Failed to cancel job: Invalid operation", type="negative")

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
    from libs.trading.backtest.models import ResultPathMissing
    from libs.trading.backtest.result_storage import BacktestResultStorage

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
                j["job_id"]: (
                    f"{j['alpha_name']} ({j['start_date']} - {j['end_date']}) "
                    f"[{j.get('provider', 'crsp').upper()}]"
                )
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
                    ui.notify(
                        f"Maximum {MAX_COMPARISON_SELECTIONS} selections allowed", type="warning"
                    )
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
                    except (ConnectionError, OSError) as e:
                        logger.error(
                            "result_load_db_connection_failed",
                            extra={"job_id": job_id, "error": str(e)},
                            exc_info=True,
                        )
                        ui.notify(
                            "Failed to load result: Database connection error", type="negative"
                        )
                    except (ValueError, KeyError, TypeError) as e:
                        logger.error(
                            "result_load_data_error",
                            extra={"job_id": job_id, "error": str(e)},
                            exc_info=True,
                        )
                        ui.notify("Failed to load result: Data processing error", type="negative")

                if len(results) >= 2:
                    _render_comparison_table(results)

            ui.button("Compare Selected", on_click=show_comparison, color="primary").classes("mt-2")

        else:
            # Single result view
            for job in jobs_data:
                status_icon = (
                    "check_circle"
                    if job["status"] == "completed"
                    else "cancel" if job["status"] == "failed" else "warning"
                )
                status_color = (
                    "text-green-600"
                    if job["status"] == "completed"
                    else "text-red-600" if job["status"] == "failed" else "text-yellow-600"
                )

                with ui.expansion(
                    f"{job['alpha_name']} ({job['start_date']} - {job['end_date']})"
                ).classes("w-full"):
                    with ui.row().classes("items-center gap-2 mb-2"):
                        ui.icon(status_icon).classes(f"text-xl {status_color}")
                        ui.label(job["status"].upper()).classes(f"font-medium {status_color}")
                        ui.label(job.get("provider", "crsp").upper()).classes(
                            "text-xs text-gray-500"
                        )

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
                            except ResultPathMissing:
                                ui.notify(
                                    "Result artifacts missing for this job. Rerun the backtest.",
                                    type="warning",
                                )
                            except (ConnectionError, OSError) as e:
                                logger.error(
                                    "result_load_db_connection_failed",
                                    extra={"job_id": job_id, "error": str(e)},
                                    exc_info=True,
                                )
                                ui.notify(
                                    "Failed to load result: Database connection error",
                                    type="negative",
                                )
                            except (ValueError, KeyError, TypeError) as e:
                                logger.error(
                                    "result_load_data_error",
                                    extra={"job_id": job_id, "error": str(e)},
                                    exc_info=True,
                                )
                                ui.notify(
                                    "Failed to load result: Data processing error", type="negative"
                                )

                        if job.get("result_path"):
                            ui.button("Load Details", on_click=show_result).props("flat")
                        else:
                            ui.label("Result artifacts missing. Rerun to view details.").classes(
                                "text-xs text-yellow-600"
                            )

                        # Show summary metrics inline
                        with ui.row().classes("gap-4 mt-2"):
                            if job.get("mean_ic") is not None:
                                ui.label(f"Mean IC: {job['mean_ic']:.4f}").classes("text-sm")
                            if job.get("icir") is not None:
                                ui.label(f"ICIR: {job['icir']:.2f}").classes("text-sm")
                            if job.get("hit_rate") is not None:
                                ui.label(f"Hit Rate: {job['hit_rate'] * 100:.1f}%").classes(
                                    "text-sm"
                                )

    comparison_checkbox.on_value_change(results_display.refresh)
    results_display()


def _fmt_float(value: float | None, fmt: str) -> str:
    """Format a float value with N/A fallback for None/NaN/Inf."""
    if value is None:
        return "N/A"
    try:
        val = float(value)
    except (TypeError, ValueError):
        return "N/A"
    if not math.isfinite(val):
        return "N/A"
    return fmt.format(val)


def _fmt_pct(value: float | None, fmt: str) -> str:
    """Format a decimal as percentage with N/A fallback for None/NaN/Inf."""
    if value is None:
        return "N/A"
    try:
        val = float(value)
    except (TypeError, ValueError):
        return "N/A"
    if not math.isfinite(val):
        return "N/A"
    return fmt.format(val * 100)


def _render_yahoo_backtest_details(result: Any, user: dict[str, Any]) -> None:
    """Render Yahoo Finance-specific backtest details: universe, signals, charts, trades.

    This helper renders:
    - Universe section with symbol list
    - Signal Details with symbol selector and CSV export
    - Price + Signal Triggers chart
    - Trade P&L section with computed trades table

    Args:
        result: Backtest result object with daily_prices, daily_signals, etc.
        user: Current user dict for permission checks.
    """
    symbols: list[str] = []
    prices = getattr(result, "daily_prices", None)
    signals = getattr(result, "daily_signals", None)
    returns = getattr(result, "daily_returns", None)
    weights = getattr(result, "daily_weights", None)

    if prices is not None and hasattr(prices, "is_empty") and not prices.is_empty():
        if "symbol" in prices.columns:
            symbols = sorted(prices["symbol"].unique().to_list())
    elif signals is not None and hasattr(signals, "is_empty") and not signals.is_empty():
        if "symbol" in signals.columns:
            symbols = sorted(signals["symbol"].unique().to_list())

    if not symbols:
        return

    ui.separator().classes("my-4")
    ui.label("Universe").classes("text-lg font-bold mb-2")
    ui.label(f"{len(symbols)} symbols").classes("text-sm text-gray-500 mb-2")
    ui.label(", ".join(symbols)).classes("text-sm text-gray-700")

    ui.separator().classes("my-4")
    ui.label("Signal Details").classes("text-lg font-bold mb-2")

    state = {"symbol": symbols[0] if symbols else None}

    def _build_events(symbol: str | None) -> list[dict[str, Any]]:
        if signals is None or returns is None:
            return []
        if getattr(signals, "is_empty", lambda: True)():
            return []
        base = signals
        if "symbol" not in base.columns and prices is not None and "symbol" in prices.columns:
            mapping = prices.select(["permno", "symbol"]).unique()
            base = base.join(mapping, on="permno", how="left")

        events = base
        if returns is not None and hasattr(returns, "is_empty") and not returns.is_empty():
            events = events.join(
                returns.select(["permno", "date", "return"]),
                on=["permno", "date"],
                how="left",
            )
        if weights is not None and hasattr(weights, "is_empty") and not weights.is_empty():
            events = events.join(
                weights.select(["permno", "date", "weight"]),
                on=["permno", "date"],
                how="left",
            )
        if prices is not None and hasattr(prices, "is_empty") and not prices.is_empty():
            events = events.join(
                prices.select(["permno", "date", "price"]),
                on=["permno", "date"],
                how="left",
            )

        events = events.with_columns(
            [
                pl.when(pl.col("signal") > 0)
                .then(pl.lit("BUY"))
                .when(pl.col("signal") < 0)
                .then(pl.lit("SELL"))
                .otherwise(pl.lit("FLAT"))
                .alias("side"),
                pl.col("return").fill_null(0).alias("forward_return"),
                (pl.col("weight").fill_null(0) * pl.col("return").fill_null(0)).alias(
                    "weighted_pnl"
                ),
            ]
        ).filter(pl.col("side") != "FLAT")

        if symbol and "symbol" in events.columns:
            events = events.filter(pl.col("symbol") == symbol)

        events = events.sort("date", descending=True).head(200)
        rows: list[dict[str, Any]] = events.select(
            [
                pl.col("date").cast(pl.Date),
                pl.col("symbol"),
                pl.col("side"),
                pl.col("signal"),
                pl.col("weight"),
                pl.col("price"),
                pl.col("forward_return"),
                pl.col("weighted_pnl"),
            ]
        ).to_dicts()
        return rows

    def _update_symbol(value: str) -> None:
        state["symbol"] = value
        _render_price_chart.refresh()
        _render_trade_pnl.refresh()

    def _download_signal_csv() -> None:
        # SECURITY: Verify EXPORT_DATA permission before allowing download
        if not has_permission(user, Permission.EXPORT_DATA):
            ui.notify("Export requires EXPORT_DATA permission", type="negative")
            return
        rows = _build_events(state["symbol"])
        if not rows:
            ui.notify("No signal events to export for this symbol.", type="warning")
            return
        import csv
        import io

        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
        ui.download(
            buf.getvalue().encode(),
            filename=f"signals_{result.backtest_id}_{state['symbol']}.csv",
        )

    with ui.row().classes("items-center gap-3 mb-2"):
        ui.select(
            label="Symbol",
            options=symbols,
            value=symbols[0],
            on_change=lambda e: _update_symbol(str(e.value)),
        ).classes("w-60")
        # Only show download button if user has EXPORT_DATA permission
        if has_permission(user, Permission.EXPORT_DATA):
            ui.button("Download Signals CSV", on_click=_download_signal_csv).props("flat")

    ui.separator().classes("my-4")
    ui.label("Price + Signal Triggers").classes("text-lg font-bold mb-2")

    @ui.refreshable
    def _render_price_chart() -> None:
        symbol = state["symbol"]
        if prices is None or prices.is_empty() or symbol is None:
            ui.label("No price data available for chart.").classes("text-gray-500")
            return

        price_series = prices.filter(pl.col("symbol") == symbol).sort("date")
        if price_series.is_empty():
            ui.label("No price data available for chart.").classes("text-gray-500")
            return

        sigs = signals
        if sigs is not None and not sigs.is_empty():
            if "symbol" not in sigs.columns and prices is not None and "symbol" in prices.columns:
                mapping = prices.select(["permno", "symbol"]).unique()
                sigs = sigs.join(mapping, on="permno", how="left")
            if "symbol" in sigs.columns:
                sigs = sigs.filter(pl.col("symbol") == symbol)
            else:
                sigs = None

        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=price_series["date"].to_list(),
                y=price_series["price"].to_list(),
                mode="lines",
                name="Price",
            )
        )

        if sigs is not None and not sigs.is_empty():
            sigs = sigs.select(["date", "signal"]).sort("date")
            sig_prices = price_series.join(sigs, on="date", how="inner")
            buy = sig_prices.filter(pl.col("signal") > 0)
            sell = sig_prices.filter(pl.col("signal") < 0)
            if not buy.is_empty():
                fig.add_trace(
                    go.Scatter(
                        x=buy["date"].to_list(),
                        y=buy["price"].to_list(),
                        mode="markers",
                        name="BUY",
                        marker={"color": "green", "size": 7, "symbol": "triangle-up"},
                    )
                )
            if not sell.is_empty():
                fig.add_trace(
                    go.Scatter(
                        x=sell["date"].to_list(),
                        y=sell["price"].to_list(),
                        mode="markers",
                        name="SELL",
                        marker={"color": "red", "size": 7, "symbol": "triangle-down"},
                    )
                )

        fig.update_layout(
            margin={"l": 20, "r": 20, "t": 20, "b": 20},
            height=360,
            legend={"orientation": "h"},
        )
        ui.plotly(fig).classes("w-full")

    _render_price_chart()

    ui.separator().classes("my-4")
    ui.label("Trade P&L (per symbol)").classes("text-lg font-bold mb-2")

    def _compute_trades(symbol: str | None) -> list[dict[str, Any]]:
        if symbol is None or prices is None or signals is None:
            return []
        if prices.is_empty() or signals.is_empty():
            return []

        # Ensure signals have symbol
        sigs = signals
        if "symbol" not in sigs.columns:
            mapping = prices.select(["permno", "symbol"]).unique()
            sigs = sigs.join(mapping, on="permno", how="left")
        if "symbol" not in sigs.columns:
            return []

        sigs = sigs.filter(pl.col("symbol") == symbol).select(["date", "signal"]).sort("date")
        if sigs.is_empty():
            return []

        price_series = (
            prices.filter(pl.col("symbol") == symbol).select(["date", "price"]).sort("date")
        )
        price_map = {row["date"]: row["price"] for row in price_series.to_dicts()}
        last_price_row = price_series.tail(1).to_dicts()
        last_price_date = last_price_row[0]["date"] if last_price_row else None
        last_price = last_price_row[0]["price"] if last_price_row else None

        trades: list[dict[str, Any]] = []
        position = 0  # -1, 0, 1
        entry_date = None
        entry_price = None

        for row in sigs.to_dicts():
            date_val = row["date"]
            sig_val = row["signal"]
            side = 1 if sig_val > 0 else -1 if sig_val < 0 else 0
            if side == position:
                continue
            # Close existing position
            if position != 0 and entry_date is not None and entry_price is not None:
                exit_price = price_map.get(date_val)
                if exit_price is not None:
                    pnl = (
                        (exit_price - entry_price) / entry_price
                        if position > 0
                        else (entry_price - exit_price) / entry_price
                    )
                    trades.append(
                        {
                            "entry_date": entry_date,
                            "exit_date": date_val,
                            "side": "BUY" if position > 0 else "SELL",
                            "entry_price": entry_price,
                            "exit_price": exit_price,
                            "pnl_pct": pnl,
                        }
                    )
            # Open new position if non-zero
            if side != 0:
                entry_date = date_val
                entry_price = price_map.get(date_val)
            else:
                entry_date = None
                entry_price = None
            position = side

        # Mark-to-market open position at end of series
        if position != 0 and entry_date is not None and entry_price is not None:
            if last_price_date is not None and last_price is not None:
                pnl = (
                    (last_price - entry_price) / entry_price
                    if position > 0
                    else (entry_price - last_price) / entry_price
                )
                trades.append(
                    {
                        "entry_date": entry_date,
                        "exit_date": last_price_date,
                        "side": "BUY" if position > 0 else "SELL",
                        "entry_price": entry_price,
                        "exit_price": last_price,
                        "pnl_pct": pnl,
                    }
                )

        return trades

    @ui.refreshable
    def _render_trade_pnl() -> None:
        symbol = state["symbol"]
        trades = _compute_trades(symbol)
        if not trades:
            ui.label("No trade events computed for this symbol.").classes("text-gray-500")
            return
        total_pnl = sum(t.get("pnl_pct", 0) for t in trades)
        wins = sum(1 for t in trades if t.get("pnl_pct", 0) > 0)
        win_rate = (wins / len(trades)) if trades else 0.0

        with ui.row().classes("gap-4 mb-2"):
            with ui.card().classes("p-3 text-center").props("flat bordered"):
                ui.label("Trades").classes("text-xs text-gray-500")
                ui.label(f"{len(trades)}").classes("text-lg font-bold")
            win_rate_text = _fmt_pct(win_rate, "{:.1f}%")
            total_pnl_text = _fmt_pct(total_pnl, "{:.2f}%")
            with ui.card().classes("p-3 text-center").props("flat bordered"):
                ui.label("Win Rate").classes("text-xs text-gray-500")
                ui.label(win_rate_text).classes("text-lg font-bold")
            with ui.card().classes("p-3 text-center").props("flat bordered"):
                ui.label("Total P&L").classes("text-xs text-gray-500")
                ui.label(total_pnl_text).classes("text-lg font-bold")

        # Show last 50 trades
        rows = trades[-50:]
        columns = [
            {"name": "entry_date", "label": "Entry", "field": "entry_date"},
            {"name": "exit_date", "label": "Exit", "field": "exit_date"},
            {"name": "side", "label": "Side", "field": "side"},
            {"name": "entry_price", "label": "Entry Px", "field": "entry_price"},
            {"name": "exit_price", "label": "Exit Px", "field": "exit_price"},
            {"name": "pnl_pct", "label": "P&L (%)", "field": "pnl_pct"},
        ]
        ui.table(columns=columns, rows=rows, row_key="entry_date").classes("w-full")

    def _download_trades_csv() -> None:
        # SECURITY: Verify EXPORT_DATA permission before allowing download
        if not has_permission(user, Permission.EXPORT_DATA):
            ui.notify("Export requires EXPORT_DATA permission", type="negative")
            return
        trades = _compute_trades(state["symbol"])
        if not trades:
            ui.notify("No trades to export for this symbol.", type="warning")
            return
        import csv
        import io

        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=list(trades[0].keys()))
        writer.writeheader()
        writer.writerows(trades)
        ui.download(
            buf.getvalue().encode(),
            filename=f"trades_{result.backtest_id}_{state['symbol']}.csv",
        )

    # Only show download button if user has EXPORT_DATA permission
    if has_permission(user, Permission.EXPORT_DATA):
        ui.button("Download Trades CSV", on_click=_download_trades_csv).props("flat")
    _render_trade_pnl()


def _render_backtest_result(result: Any, user: dict[str, Any]) -> None:
    """Render complete backtest result with metrics and charts."""

    # Header
    ui.label(f"Backtest: {result.alpha_name}").classes("text-xl font-bold")
    ui.label(
        f"Period: {result.start_date} to {result.end_date} | "
        f"Days: {result.n_days} | "
        f"Avg Symbols: {result.n_symbols_avg:.0f}"
    ).classes("text-sm text-gray-500 mb-4")
    provider = None
    if isinstance(getattr(result, "dataset_version_ids", None), dict):
        provider = result.dataset_version_ids.get("provider")
    if provider:
        ui.label(f"Data Source: {provider.upper()}").classes("text-sm text-gray-500 mb-4")

    # Metrics summary
    ic_note = None
    if hasattr(result, "daily_ic") and result.daily_ic is not None:
        try:
            ic_df = result.daily_ic
            if hasattr(ic_df, "is_empty") and not ic_df.is_empty():
                ic_series = ic_df["rank_ic"] if "rank_ic" in ic_df.columns else ic_df["ic"]
                ic_valid = ic_series.drop_nulls()
                ic_valid = ic_valid.filter(ic_valid.is_finite())
                if ic_valid.len() == 0:
                    ic_note = "IC/ICIR unavailable (no valid cross-sectional IC values)."
        except Exception:
            ic_note = "IC/ICIR unavailable (unable to compute from daily IC series)."

    with ui.row().classes("w-full gap-4 mb-4"):
        with ui.card().classes("flex-1 p-3 text-center"):
            ui.label("Mean IC").classes("text-sm text-gray-500")
            ui.label(_fmt_float(result.mean_ic, "{:.4f}")).classes("text-lg font-bold")
        with ui.card().classes("flex-1 p-3 text-center"):
            ui.label("ICIR").classes("text-sm text-gray-500")
            ui.label(_fmt_float(result.icir, "{:.2f}")).classes("text-lg font-bold")
        with ui.card().classes("flex-1 p-3 text-center"):
            ui.label("Hit Rate").classes("text-sm text-gray-500")
            ui.label(_fmt_pct(result.hit_rate, "{:.1f}%")).classes("text-lg font-bold")
        with ui.card().classes("flex-1 p-3 text-center"):
            ui.label("Coverage").classes("text-sm text-gray-500")
            ui.label(_fmt_pct(result.coverage, "{:.1f}%")).classes("text-lg font-bold")
        with ui.card().classes("flex-1 p-3 text-center"):
            turnover = result.turnover_result.average_turnover if result.turnover_result else None
            ui.label("Avg Turnover").classes("text-sm text-gray-500")
            ui.label(_fmt_pct(turnover, "{:.2f}%")).classes("text-lg font-bold")

    if ic_note:
        ui.label(ic_note).classes("text-xs text-gray-500 mb-4")

    # Cost Analysis Summary (T9.2) - displayed when cost model data is available
    # Check for cost data in result attributes (added by P6T9)
    cost_summary = getattr(result, "cost_summary", None)
    cost_config = getattr(result, "cost_config", None)
    capacity_analysis = getattr(result, "capacity_analysis", None)

    if cost_config and cost_summary:
        ui.separator().classes("my-4")
        ui.label("Cost Analysis").classes("text-lg font-bold mb-2")

        with ui.row().classes("w-full gap-4 mb-4"):
            with ui.card().classes("flex-1 p-3 text-center"):
                ui.label("Gross Return").classes("text-sm text-gray-500")
                gross_ret = cost_summary.get("total_gross_return")
                ui.label(_fmt_pct(gross_ret, "{:.2f}%")).classes("text-lg font-bold")
            with ui.card().classes("flex-1 p-3 text-center"):
                ui.label("Net Return").classes("text-sm text-gray-500")
                net_ret = cost_summary.get("total_net_return")
                ui.label(_fmt_pct(net_ret, "{:.2f}%")).classes("text-lg font-bold")
            with ui.card().classes("flex-1 p-3 text-center"):
                ui.label("Total Cost").classes("text-sm text-gray-500")
                total_cost = cost_summary.get("total_cost_usd", 0)
                ui.label(f"${total_cost:,.0f}").classes("text-lg font-bold")
            with ui.card().classes("flex-1 p-3 text-center"):
                ui.label("Avg Cost").classes("text-sm text-gray-500")
                avg_cost_bps = cost_summary.get("avg_trade_cost_bps", 0)
                ui.label(f"{avg_cost_bps:.1f} bps").classes("text-lg font-bold")

        # Cost breakdown details
        with ui.expansion("Cost Breakdown", icon="expand_more").classes("w-full mb-4"):
            with ui.row().classes("gap-4"):
                comm_cost = cost_summary.get("commission_spread_cost_usd", 0)
                impact_cost = cost_summary.get("market_impact_cost_usd", 0)
                ui.label(f"Commission + Spread: ${comm_cost:,.0f}").classes("text-sm")
                ui.label(f"Market Impact: ${impact_cost:,.0f}").classes("text-sm")
                num_trades = cost_summary.get("num_trades", 0)
                ui.label(f"Number of Trades: {num_trades}").classes("text-sm")

            # Net risk metrics
            net_sharpe = cost_summary.get("net_sharpe")
            net_max_dd = cost_summary.get("net_max_drawdown")
            if net_sharpe is not None or net_max_dd is not None:
                with ui.row().classes("gap-4 mt-2"):
                    if net_sharpe is not None:
                        ui.label(f"Net Sharpe: {net_sharpe:.2f}").classes("text-sm")
                    if net_max_dd is not None:
                        ui.label(f"Net Max Drawdown: {net_max_dd:.1%}").classes("text-sm")

            # Data quality warnings (P6T9 - T9.2)
            adv_fallbacks = cost_summary.get("adv_fallback_count", 0)
            vol_fallbacks = cost_summary.get("volatility_fallback_count", 0)
            violations = cost_summary.get("participation_violations", 0)

            if adv_fallbacks > 0 or vol_fallbacks > 0 or violations > 0:
                ui.separator().classes("my-2")
                ui.label("Data Quality Warnings").classes("text-sm font-bold text-amber-600")
                with ui.column().classes("gap-1 mt-1"):
                    if adv_fallbacks > 0:
                        ui.label(
                            f"⚠️ {adv_fallbacks} trades used ADV fallback (missing volume data)"
                        ).classes("text-xs text-amber-600")
                    if vol_fallbacks > 0:
                        ui.label(
                            f"⚠️ {vol_fallbacks} trades used volatility fallback (missing return data)"
                        ).classes("text-xs text-amber-600")
                    if violations > 0:
                        ui.label(
                            f"⚠️ {violations} trades exceeded ADV participation limit"
                        ).classes("text-xs text-amber-600")

        # Capacity Analysis (T9.3 display)
        if capacity_analysis:
            with ui.expansion("Capacity Analysis", icon="analytics").classes("w-full mb-4"):
                implied_cap = capacity_analysis.get("implied_max_capacity")
                limiting = capacity_analysis.get("limiting_factor")

                if implied_cap is not None:
                    cap_formatted = f"${implied_cap:,.0f}" if implied_cap < 1e12 else "Unlimited"
                    ui.label(f"Implied Capacity: {cap_formatted}").classes("text-lg font-bold")
                    if limiting:
                        ui.label(f"Binding Constraint: {limiting}").classes("text-sm text-gray-500")

                with ui.row().classes("gap-4 mt-2"):
                    turnover = capacity_analysis.get("avg_daily_turnover")
                    if turnover is not None:
                        ui.label(f"Avg Daily Turnover: {turnover:.1%}").classes("text-sm")
                    holding = capacity_analysis.get("avg_holding_period_days")
                    if holding is not None:
                        ui.label(f"Avg Holding Period: {holding:.1f} days").classes("text-sm")

                # Constraint details
                impact_5 = capacity_analysis.get("impact_aum_5bps")
                participation = capacity_analysis.get("participation_aum")
                breakeven = capacity_analysis.get("breakeven_aum")

                with ui.column().classes("mt-2 text-sm"):
                    if impact_5 is not None:
                        ui.label(f"At 5 bps impact: ${impact_5:,.0f}")
                    if participation is not None:
                        ui.label(f"At participation limit: ${participation:,.0f}")
                    if breakeven is not None:
                        ui.label(f"Breakeven AUM: ${breakeven:,.0f}")

    elif cost_config:
        # Cost model enabled but no summary (job incomplete or legacy)
        ui.separator().classes("my-4")
        with ui.card().classes("w-full p-4"):
            ui.label("Cost Analysis").classes("text-lg font-bold")
            ui.label("Cost data unavailable for this backtest.").classes("text-sm text-amber-500")

    # Render Yahoo Finance-specific details (universe, signals, charts, trades)
    _render_yahoo_backtest_details(result, user)

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

            # Add cost data to metrics if available (T9.4)
            cost_summary = getattr(result, "cost_summary", None)
            cost_config = getattr(result, "cost_config", None)
            capacity_analysis = getattr(result, "capacity_analysis", None)

            if cost_summary:
                metrics_dict["cost_summary"] = cost_summary
            if cost_config:
                metrics_dict["cost_config"] = cost_config
            if capacity_analysis:
                metrics_dict["capacity_analysis"] = capacity_analysis

            metrics_json = json.dumps(metrics_dict, indent=2, default=str)

            def download_metrics() -> None:
                ui.download(
                    metrics_json.encode(),
                    filename=f"metrics_{result.backtest_id}.json",
                )

            # Daily returns CSV export (T9.4)
            def download_returns_csv() -> None:
                """Export daily portfolio returns as CSV."""
                if not hasattr(result, "daily_portfolio_returns") or result.daily_portfolio_returns is None:
                    ui.notify("No daily returns data available", type="warning")
                    return

                df = result.daily_portfolio_returns
                # Add cost columns if available
                if cost_summary:
                    # For now, export basic returns; cost breakdown requires full integration
                    pass

                csv_content = df.write_csv()
                ui.download(
                    csv_content.encode() if isinstance(csv_content, str) else csv_content,
                    filename=f"returns_{result.backtest_id}.csv",
                )

            # Full summary JSON export (T9.4)
            def download_full_summary() -> None:
                """Export complete backtest summary including cost analysis."""
                summary_dict = {
                    "job_id": result.backtest_id,
                    "backtest_period": {
                        "start": str(result.start_date),
                        "end": str(result.end_date),
                    },
                    "alpha_name": result.alpha_name,
                    "weight_method": result.weight_method,
                    "results": {
                        "mean_ic": result.mean_ic,
                        "icir": result.icir,
                        "hit_rate": result.hit_rate,
                        "coverage": result.coverage,
                        "n_days": result.n_days,
                        "n_symbols_avg": result.n_symbols_avg,
                    },
                    "dataset_version_ids": result.dataset_version_ids,
                    "snapshot_id": result.snapshot_id,
                }

                if result.turnover_result:
                    summary_dict["results"]["average_turnover"] = result.turnover_result.average_turnover

                # Add cost analysis data if available (T9.4)
                if cost_config:
                    summary_dict["cost_model_config"] = cost_config
                    summary_dict["portfolio_value_usd"] = cost_config.get("portfolio_value_usd", 1_000_000)

                if cost_summary:
                    summary_dict["results"]["gross_total_return"] = cost_summary.get("total_gross_return")
                    summary_dict["results"]["net_total_return"] = cost_summary.get("total_net_return")
                    summary_dict["results"]["total_cost_usd"] = cost_summary.get("total_cost_usd")
                    summary_dict["results"]["net_sharpe"] = cost_summary.get("net_sharpe")
                    summary_dict["results"]["net_max_drawdown"] = cost_summary.get("net_max_drawdown")

                if capacity_analysis:
                    summary_dict["capacity_analysis"] = {
                        "implied_capacity": capacity_analysis.get("implied_max_capacity"),
                        "binding_constraint": capacity_analysis.get("limiting_factor"),
                        "avg_daily_turnover": capacity_analysis.get("avg_daily_turnover"),
                        "avg_holding_period_days": capacity_analysis.get("avg_holding_period_days"),
                    }

                summary_json = json.dumps(summary_dict, indent=2, default=str)
                ui.download(
                    summary_json.encode(),
                    filename=f"summary_{result.backtest_id}.json",
                )

            # Net returns Parquet export (T9.4)
            def download_net_returns_parquet() -> None:
                """Export net portfolio returns as Parquet file."""
                if not hasattr(result, "net_portfolio_returns") or result.net_portfolio_returns is None:
                    ui.notify("No net returns data available (cost model not applied)", type="warning")
                    return

                import io
                buffer = io.BytesIO()
                result.net_portfolio_returns.write_parquet(buffer)
                buffer.seek(0)
                ui.download(
                    buffer.getvalue(),
                    filename=f"net_returns_{result.backtest_id}.parquet",
                )

            with ui.row().classes("gap-2"):
                ui.button("Download Metrics JSON", on_click=download_metrics)
                ui.button("Download Returns CSV", on_click=download_returns_csv)
                ui.button("Download Full Summary", on_click=download_full_summary)
                # Only show Parquet button if net returns available
                if hasattr(result, "net_portfolio_returns") and result.net_portfolio_returns is not None:
                    ui.button("Download Net Returns Parquet", on_click=download_net_returns_parquet)
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
        rows.append(
            {
                "alpha": result.alpha_name,
                "period": f"{result.start_date} - {result.end_date}",
                "mean_ic": f"{result.mean_ic:.4f}" if result.mean_ic is not None else "N/A",
                "icir": f"{result.icir:.2f}" if result.icir is not None else "N/A",
                "hit_rate": (
                    f"{result.hit_rate * 100:.1f}%" if result.hit_rate is not None else "N/A"
                ),
                "coverage": (
                    f"{result.coverage * 100:.1f}%" if result.coverage is not None else "N/A"
                ),
                "turnover": f"{turnover:.2%}" if turnover is not None else "N/A",
                "days": result.n_days,
            }
        )

    ui.table(columns=columns, rows=rows).classes("w-full")


__all__ = ["backtest_page"]
