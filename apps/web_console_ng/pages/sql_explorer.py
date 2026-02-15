"""SQL Explorer page for NiceGUI web console (P6T14/T14.1)."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import polars as pl
from nicegui import ui

from apps.web_console_ng.auth.middleware import get_current_user, requires_auth
from apps.web_console_ng.core.redis_ha import get_redis_store
from apps.web_console_ng.ui.layout import main_layout
from apps.web_console_ng.ui.trading_layout import apply_compact_grid_options
from libs.platform.web_console_auth.permissions import Permission, has_permission
from libs.platform.web_console_auth.rate_limiter import RateLimiter
from libs.web_console_services.sql_explorer_service import (
    _DATA_ROOT_AVAILABLE,
    _MAX_CELLS,
    _MAX_ROWS_LIMIT,
    SqlExplorerService,
    can_query_dataset,
)

logger = logging.getLogger(__name__)

_PATH_CACHE_TTL = 120
_path_cache: tuple[dict[str, set[str]], list[str]] | None = None
_path_cache_ts: float = 0.0
_path_cache_lock = asyncio.Lock()


async def _get_validated_paths() -> tuple[dict[str, set[str]], list[str]]:
    from libs.web_console_services.sql_explorer_service import _validate_table_paths

    global _path_cache, _path_cache_ts
    now = time.monotonic()
    if _path_cache is not None and (now - _path_cache_ts) < _PATH_CACHE_TTL:
        return _path_cache

    async with _path_cache_lock:
        now = time.monotonic()
        if _path_cache is not None and (now - _path_cache_ts) < _PATH_CACHE_TTL:
            return _path_cache
        result = await asyncio.to_thread(_validate_table_paths)
        _path_cache = result
        _path_cache_ts = now
        return result


@ui.page("/data/sql-explorer")
@requires_auth
@main_layout
async def sql_explorer_page() -> None:
    """Render SQL Explorer with safe server-side execution controls."""

    user = get_current_user()

    if not has_permission(user, Permission.QUERY_DATA):
        ui.notify("Permission denied: QUERY_DATA required", type="negative")
        with ui.card().classes("w-full p-6"):
            ui.label("Permission denied: QUERY_DATA required.").classes("text-red-500 text-center")
        return

    ui.label("SQL Explorer").classes("text-2xl font-bold mb-2")

    if not _DATA_ROOT_AVAILABLE:
        ui.notify(
            "SQL Explorer unavailable: data directory not found. Set PROJECT_ROOT env var.",
            type="warning",
        )
        with ui.card().classes("w-full p-4"):
            ui.label("SQL Explorer unavailable: missing data directory.").classes("text-amber-500")
        return

    rate_limiter: RateLimiter | None = None
    try:
        redis_client = await get_redis_store().get_master()
        rate_limiter = RateLimiter(redis_client=redis_client, fallback_mode="deny")
    except Exception:
        logger.exception("sql_explorer_rate_limiter_init_failed")

    try:
        service = SqlExplorerService(rate_limiter=rate_limiter)
    except (ValueError, RuntimeError):
        logger.exception("sql_explorer_service_init_failed")
        with ui.card().classes("w-full p-4"):
            ui.label("SQL Explorer unavailable: service initialization failed.").classes(
                "text-amber-500"
            )
        return

    available_tables_by_dataset, warnings = await _get_validated_paths()
    for warning in warnings:
        logger.warning("sql_explorer_path_warning", extra={"detail": warning})

    allowed_datasets = sorted(
        [
            dataset
            for dataset in available_tables_by_dataset
            if can_query_dataset(user, dataset)
        ]
    )

    if not allowed_datasets:
        with ui.card().classes("w-full p-4"):
            ui.label("No queryable datasets available for your account.").classes("text-gray-400")
        return

    history: list[dict[str, Any]] = []
    query_running = False
    last_result_dataset: str | None = None

    with ui.row().classes("w-full gap-3 items-end"):
        dataset_select = ui.select(
            label="Dataset",
            options=allowed_datasets,
            value=allowed_datasets[0],
        ).classes("w-56")
        timeout_select = ui.select(
            label="Timeout",
            options=["10", "30", "60", "120"],
            value="30",
        ).classes("w-36")
        max_rows_input = ui.number(
            label="Max Rows",
            value=10_000,
            min=1,
            max=_MAX_ROWS_LIMIT,
            step=100,
        ).classes("w-40")

    tables_label = ui.label("").classes("text-sm text-gray-400 mb-1")

    def _update_table_hint() -> None:
        dataset = str(dataset_select.value)
        tables = sorted(available_tables_by_dataset.get(dataset, set()))
        tables_label.text = f"Available tables: {', '.join(tables)}" if tables else "Available tables: -"

    _update_table_hint()
    dataset_select.on_value_change(lambda _: _update_table_hint())

    query_editor = ui.textarea(
        label="SQL Query",
        placeholder="SELECT * FROM crsp_daily WHERE symbol = 'AAPL' LIMIT 100",
    ).classes("w-full font-mono")

    status_label = ui.label("Run a query to see results").classes("text-sm text-gray-400 mt-1")

    grid_options = apply_compact_grid_options(
        {
            "columnDefs": [],
            "rowData": [],
            "rowSelection": "single",
            "animateRows": True,
        }
    )
    grid = ui.aggrid(grid_options).classes("w-full ag-theme-alpine-dark mt-3")

    with ui.right_drawer(value=False).classes("w-96 bg-surface-1 p-3") as history_drawer:
        ui.label("Query History").classes("text-lg font-bold mb-2")
        history_container = ui.column().classes("w-full gap-2")

        def _render_history() -> None:
            history_container.clear()
            with history_container:
                if not history:
                    ui.label("No query history yet").classes("text-gray-500 text-sm")
                    return
                for item in history:
                    with ui.card().classes("w-full p-2"):
                        ui.label(item["fingerprint"]).classes("text-xs font-mono")
                        ui.label(
                            f"{item['dataset']} | {item['status']} | {item['rows']} rows"
                        ).classes("text-xs text-gray-400")
                        def _replay_fingerprint(fp: str = item["fingerprint"]) -> None:
                            query_editor.value = fp
                            if "?" in fp:
                                ui.notify(
                                    "Query template loaded — replace ? placeholders with actual values",
                                    type="info",
                                )

                        ui.button("Use in editor", on_click=_replay_fingerprint).props(
                            "flat dense"
                        )

        def _add_history(fingerprint: str, dataset: str, status: str, rows: int) -> None:
            history.insert(
                0,
                {
                    "fingerprint": fingerprint,
                    "dataset": dataset,
                    "status": status,
                    "rows": rows,
                },
            )
            if len(history) > 20:
                del history[20:]
            _render_history()

        with ui.row().classes("gap-2 mt-2"):
            def _clear_history() -> None:
                history.clear()
                _render_history()

            ui.button("Clear History", on_click=_clear_history).props("flat")

        _render_history()

    async def run_query() -> None:
        nonlocal query_running, last_result_dataset
        if query_running:
            ui.notify("Query already running", type="info")
            return

        query = str(query_editor.value or "").strip()
        if not query:
            ui.notify("Enter a query", type="warning")
            return

        dataset = str(dataset_select.value)
        timeout = int(str(timeout_select.value))
        max_rows_raw = int(max_rows_input.value or 10_000)
        max_rows = min(max_rows_raw, _MAX_ROWS_LIMIT)

        query_running = True
        status_label.text = "Running..."

        try:
            result = await service.execute_query(
                user=user,
                dataset=dataset,
                query=query,
                timeout_seconds=timeout,
                max_rows=max_rows,
                available_tables=available_tables_by_dataset.get(dataset, set()),
            )

            cell_count = len(result.df) * len(result.df.columns)
            if cell_count > _MAX_CELLS:
                ui.notify(f"Result too large ({cell_count:,} cells). Add more filters.", type="warning")
                status_label.text = f"Result too large ({cell_count:,} cells)"
                _add_history(result.fingerprint, dataset, "too_large", 0)
                return

            _LARGE_RESULT_THRESHOLD = 200_000
            if cell_count > _LARGE_RESULT_THRESHOLD:
                ui.notify(
                    f"Large result ({cell_count:,} cells) — browser may be slow. "
                    "Consider adding filters.",
                    type="info",
                )

            grid.options["columnDefs"] = [
                {
                    "field": column,
                    "headerName": column,
                    "sortable": True,
                    "filter": True,
                }
                for column in result.df.columns
            ]
            grid.options["rowData"] = result.df.to_dicts()
            grid.update()

            last_result_dataset = dataset
            status_label.text = f"{len(result.df)} rows in {result.execution_ms}ms"
            _add_history(result.fingerprint, dataset, "success", len(result.df))

        except PermissionError as exc:
            ui.notify(str(exc), type="negative")
            status_label.text = "Authorization denied"
            _add_history("<denied>", dataset, "authorization_denied", 0)
        except ValueError as exc:
            ui.notify(f"Validation failed: {exc}", type="negative")
            status_label.text = "Validation failed"
            _add_history("<validation_error>", dataset, "validation_error", 0)
        except TimeoutError:
            ui.notify(f"Query timed out after {timeout}s", type="negative")
            status_label.text = f"Timeout after {timeout}s"
            _add_history("<timed out>", dataset, "timeout", 0)
        except RuntimeError as exc:
            logger.warning("sql_query_runtime_error", extra={"dataset": dataset, "error": str(exc)})
            ui.notify("Query could not be executed. Please try again later.", type="warning")
            status_label.text = "Service unavailable"
            _add_history("<runtime_error>", dataset, "runtime_error", 0)
        except Exception:
            logger.exception("sql_query_error", extra={"dataset": dataset})
            ui.notify("Query execution failed", type="negative")
            status_label.text = "Error"
            _add_history("<error>", dataset, "error", 0)
        finally:
            query_running = False

    async def export_csv() -> None:
        export_dataset = last_result_dataset or str(dataset_select.value)
        row_data = grid.options.get("rowData", [])
        if not row_data:
            ui.notify("No data to export", type="info")
            return

        df = pl.DataFrame(row_data)
        try:
            csv_bytes = await service.export_csv(user=user, dataset=export_dataset, df=df)
        except PermissionError as exc:
            ui.notify(str(exc), type="negative")
            return
        except RuntimeError as exc:
            logger.warning("sql_export_runtime_error", extra={"error": str(exc)})
            ui.notify("Export failed. Please try again later.", type="warning")
            return

        ui.download(csv_bytes, filename=f"query_results_{time.time_ns()}.csv")

    with ui.row().classes("gap-2 mt-3"):
        ui.button("Run", on_click=run_query, color="primary")
        if has_permission(user, Permission.EXPORT_DATA):
            ui.button("Export CSV", on_click=export_csv)
        ui.button("History", on_click=history_drawer.toggle).props("flat")


__all__ = ["sql_explorer_page"]
