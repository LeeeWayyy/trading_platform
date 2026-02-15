"""Feature Store Browser page for NiceGUI web console (P6T14/T14.3)."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pandas as pd
import plotly.graph_objects as go
from nicegui import app, ui

from apps.web_console_ng.auth.middleware import get_current_user, requires_auth
from apps.web_console_ng.core.client_lifecycle import ClientLifecycleManager
from apps.web_console_ng.ui.layout import main_layout
from apps.web_console_ng.ui.trading_layout import apply_compact_grid_options
from apps.web_console_ng.utils.session import get_or_create_client_id
from libs.data.feature_metadata import (
    FeatureMetadata,
    compute_feature_statistics,
    get_feature_catalog,
    get_sample_values,
)
from libs.platform.web_console_auth.permissions import Permission, has_permission
from strategies.alpha_baseline.features import get_alpha158_features

logger = logging.getLogger(__name__)

_MAX_CACHE_DAYS = 30
_MAX_CACHE_SYMBOLS = 5
_MAX_LOOKBACK_DAYS = 60
_CACHE_TTL_SECONDS = 1800
_MAX_CACHE_BYTES = 50 * 1024 * 1024
_DEFAULT_SYMBOLS = ["AAPL", "MSFT", "GOOGL", "AMZN", "META"]
_CLEANUP_OWNER_KEY = "feature_browser_cache"
_CACHE_KEY = "feature_cache"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _cache_fresh(cached_at: float) -> bool:
    return (time.time() - cached_at) <= _CACHE_TTL_SECONDS


def _slice_display_window(features_df: pd.DataFrame) -> pd.DataFrame:
    if features_df.empty or not isinstance(features_df.index, pd.MultiIndex):
        return features_df
    index_dates = pd.to_datetime(features_df.index.get_level_values(0), errors="coerce")
    if len(index_dates) == 0:
        return features_df
    max_date = index_dates.max()
    if pd.isna(max_date):
        return features_df
    cutoff = pd.Timestamp(max_date) - pd.Timedelta(days=_MAX_CACHE_DAYS)
    mask = index_dates >= cutoff
    return features_df.loc[mask]


def _catalog_rows(catalog: list[FeatureMetadata]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for feature in catalog:
        rows.append(
            {
                "name": feature.name,
                "category": feature.category,
                "description": feature.description,
                "lookback_window": (
                    "Point-in-time"
                    if feature.lookback_window is None
                    else f"{feature.lookback_window}d"
                ),
            }
        )
    return rows


def _null_pct_color(null_pct: float) -> str:
    if null_pct < 5.0:
        return "text-green-600"
    if null_pct <= 20.0:
        return "text-amber-600"
    return "text-red-600"


@ui.page("/data/features")
@requires_auth
@main_layout
async def feature_browser_page() -> None:
    """Render Alpha158 feature catalog and on-demand detail/statistics."""
    user = get_current_user()

    if not has_permission(user, Permission.VIEW_FEATURES):
        ui.notify("Permission denied: VIEW_FEATURES required", type="negative")
        with ui.card().classes("w-full p-6"):
            ui.label("Permission denied: VIEW_FEATURES required.").classes(
                "text-red-500 text-center"
            )
        return

    ui.label("Feature Store Browser").classes("text-2xl font-bold mb-2")

    catalog = get_feature_catalog()
    category_options = ["all", *sorted({f.category for f in catalog})]
    metadata_by_name = {item.name: item for item in catalog}

    detail_panel = ui.column().classes("w-full")
    samples_panel = ui.column().classes("w-full")
    stats_panel = ui.column().classes("w-full")
    chart_panel = ui.column().classes("w-full")

    selected_feature: dict[str, str | None] = {"name": None}
    loading_state = {"feature_data_loading": False, "detail_loading": False}

    with ui.row().classes("w-full gap-3 mb-3 items-end"):
        category_select = ui.select(
            options=category_options,
            value="all",
            label="Category",
        ).classes("w-56")
        search_input = ui.input(label="Search feature", placeholder="KBAR, ROC, ...").classes(
            "w-72"
        )

    grid_options = apply_compact_grid_options(
        {
            "columnDefs": [
                {"field": "name", "headerName": "Name", "sortable": True, "minWidth": 140},
                {
                    "field": "category",
                    "headerName": "Category",
                    "sortable": True,
                    "filter": True,
                    "minWidth": 120,
                },
                {
                    "field": "description",
                    "headerName": "Description",
                    "sortable": True,
                    "flex": 1,
                    "minWidth": 280,
                },
                {
                    "field": "lookback_window",
                    "headerName": "Lookback Window",
                    "sortable": True,
                    "minWidth": 150,
                },
            ],
            "rowData": _catalog_rows(catalog),
            "rowSelection": "single",
            "animateRows": True,
            "domLayout": "normal",
        }
    )
    grid = ui.aggrid(grid_options).classes("w-full ag-theme-alpine-dark mb-3")

    async def _load_feature_dataframe(force_reload: bool = False) -> pd.DataFrame | None:
        if not force_reload:
            cached = app.storage.client.get(_CACHE_KEY)
            if isinstance(cached, dict):
                cached_at = cached.get("cached_at")
                data = cached.get("data")
                if isinstance(cached_at, int | float) and isinstance(data, pd.DataFrame):
                    if _cache_fresh(float(cached_at)):
                        return data
                    app.storage.client.pop(_CACHE_KEY, None)

        if loading_state["feature_data_loading"]:
            return None

        loading_state["feature_data_loading"] = True
        try:
            end_dt = date.today()
            start_dt = end_dt - timedelta(days=_MAX_CACHE_DAYS + _MAX_LOOKBACK_DAYS)
            symbols = _DEFAULT_SYMBOLS[:_MAX_CACHE_SYMBOLS]
            features_df = await asyncio.to_thread(
                get_alpha158_features,
                symbols=symbols,
                start_date=start_dt.isoformat(),
                end_date=end_dt.isoformat(),
            )
            if features_df is None or features_df.empty:
                return None

            features_df = _slice_display_window(features_df)
            cache_size_bytes = int(features_df.memory_usage(deep=True).sum())
            logger.info(
                "feature_cache_loaded",
                extra={
                    "cache_size_bytes": cache_size_bytes,
                    "symbols_count": len(symbols),
                    "rows": len(features_df),
                },
            )

            if cache_size_bytes <= _MAX_CACHE_BYTES:
                app.storage.client[_CACHE_KEY] = {
                    "data": features_df,
                    "cached_at": time.time(),
                }
            else:
                logger.warning(
                    "feature_cache_skipped_size_limit",
                    extra={
                        "cache_size_bytes": cache_size_bytes,
                        "max_cache_bytes": _MAX_CACHE_BYTES,
                    },
                )

            return features_df
        except FileNotFoundError:
            ui.notify("Feature data not available — run ETL pipeline first", type="warning")
            return None
        except Exception:
            logger.exception("feature_data_load_failed")
            ui.notify("Feature data unavailable", type="warning")
            return None
        finally:
            loading_state["feature_data_loading"] = False

    def _render_metadata_panel(metadata: FeatureMetadata) -> None:
        detail_panel.clear()
        with detail_panel:
            with ui.card().classes("w-full p-4"):
                ui.label(f"Feature: {metadata.name}").classes("text-lg font-bold")
                ui.label(f"Category: {metadata.category}").classes("text-sm text-gray-400")
                ui.label(metadata.description).classes("text-sm text-gray-300")
                ui.label(f"Formula: {metadata.formula}").classes("text-sm font-mono text-gray-200")
                lookback_text = (
                    "Point-in-time"
                    if metadata.lookback_window is None
                    else f"{metadata.lookback_window} days"
                )
                ui.label(f"Lookback: {lookback_text}").classes("text-sm text-gray-300")
                with ui.row().classes("gap-2 mt-2"):
                    for col in metadata.input_columns:
                        ui.label(col).classes(
                            "px-2 py-1 rounded bg-slate-700 text-xs text-slate-100"
                        )
                ui.separator().classes("my-2")
                ui.label(
                    "Input Columns: "
                    f"{metadata.input_columns}\n"
                    "  -> Qlib Alpha158 Handler (strategies/alpha_baseline/features.py)\n"
                    f"    -> Feature: {metadata.name}\n"
                    f"      Formula: {metadata.formula}"
                ).classes("text-xs font-mono text-gray-300 whitespace-pre-line")

    def _render_samples_table(samples: list[dict[str, Any]]) -> None:
        samples_panel.clear()
        with samples_panel:
            with ui.card().classes("w-full p-4"):
                ui.label("Sample Values (Most Recent Date)").classes("font-bold mb-2")
                if not samples:
                    ui.label("No sample values available").classes("text-gray-500")
                    return
                columns = [
                    {"name": "date", "label": "Date", "field": "date"},
                    {"name": "symbol", "label": "Symbol", "field": "symbol"},
                    {"name": "value", "label": "Value", "field": "value"},
                ]
                ui.table(columns=columns, rows=samples).classes("w-full")

    def _render_statistics_panel(stat: Any) -> None:
        stats_panel.clear()
        with stats_panel:
            with ui.card().classes("w-full p-4"):
                ui.label("Feature Statistics").classes("font-bold mb-2")
                with ui.row().classes("w-full gap-4 flex-wrap"):
                    ui.label(f"Count: {stat.count}").classes("text-sm")
                    ui.label(f"Mean: {stat.mean if stat.mean is not None else '-'}").classes("text-sm")
                    ui.label(f"Std: {stat.std if stat.std is not None else '-'}").classes("text-sm")
                    ui.label(f"Min: {stat.min_val if stat.min_val is not None else '-'}").classes(
                        "text-sm"
                    )
                    ui.label(f"Q25: {stat.q25 if stat.q25 is not None else '-'}").classes("text-sm")
                    ui.label(f"Median: {stat.median if stat.median is not None else '-'}").classes(
                        "text-sm"
                    )
                    ui.label(f"Q75: {stat.q75 if stat.q75 is not None else '-'}").classes("text-sm")
                    ui.label(f"Max: {stat.max_val if stat.max_val is not None else '-'}").classes(
                        "text-sm"
                    )
                    ui.label(f"Null%: {stat.null_pct:.2f}%").classes(
                        f"text-sm font-bold {_null_pct_color(stat.null_pct)}"
                    )

    def _render_chart(samples: list[dict[str, Any]], feature_name: str) -> None:
        chart_panel.clear()
        with chart_panel:
            if not samples:
                return
            values = [row["value"] for row in samples if row.get("value") is not None]
            symbols = [str(row["symbol"]) for row in samples if row.get("value") is not None]
            if not values:
                return
            fig = go.Figure(
                data=[go.Bar(x=symbols, y=values, marker_color="#60a5fa")]
            )
            fig.update_layout(
                title=f"Sample Distribution: {feature_name}",
                xaxis_title="Symbol",
                yaxis_title="Value",
                height=280,
                margin={"l": 10, "r": 10, "t": 40, "b": 20},
            )
            ui.plotly(fig).classes("w-full")

    async def _load_detail(feature_name: str) -> None:
        if loading_state["detail_loading"]:
            return

        metadata = metadata_by_name.get(feature_name)
        if metadata is None:
            return

        loading_state["detail_loading"] = True
        selected_feature["name"] = feature_name
        try:
            _render_metadata_panel(metadata)
            samples_panel.clear()
            stats_panel.clear()
            chart_panel.clear()
            with samples_panel:
                ui.label("Loading sample values...").classes("text-gray-400")

            feature_df = await _load_feature_dataframe()
            if feature_df is None or feature_df.empty:
                samples_panel.clear()
                stats_panel.clear()
                chart_panel.clear()
                with samples_panel:
                    ui.label(
                        "No feature data available. Run the ETL pipeline to generate features."
                    ).classes("text-gray-500")
                return

            stats_list = await asyncio.to_thread(
                compute_feature_statistics,
                feature_df,
                [feature_name],
            )
            samples = await asyncio.to_thread(get_sample_values, feature_df, feature_name, 10)

            _render_samples_table(samples)
            if stats_list:
                _render_statistics_panel(stats_list[0])
            _render_chart(samples, feature_name)
        except Exception:
            logger.exception("feature_statistics_failed", extra={"feature": feature_name})
            ui.notify("Feature statistics unavailable", type="warning")
        finally:
            loading_state["detail_loading"] = False

    def _apply_filters() -> None:
        selected_category = str(category_select.value or "all")
        query = str(search_input.value or "").strip().lower()
        rows = _catalog_rows(catalog)
        filtered = [
            row
            for row in rows
            if (selected_category == "all" or row["category"] == selected_category)
            and (not query or query in str(row["name"]).lower())
        ]
        grid.options["rowData"] = filtered
        grid.update()

    async def _open_selected() -> None:
        selected_rows = await grid.get_selected_rows()
        if not selected_rows:
            ui.notify("Select a feature first", type="info")
            return
        feature_name = str(selected_rows[0].get("name", "")).strip()
        if not feature_name:
            return
        await _load_detail(feature_name)

    async def _row_clicked(event: Any) -> None:
        payload = getattr(event, "args", None)
        if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
            feature_name = str(payload["data"].get("name", "")).strip()
            if feature_name:
                await _load_detail(feature_name)
                return
        await _open_selected()

    category_select.on_value_change(lambda _e: _apply_filters())
    search_input.on_value_change(lambda _e: _apply_filters())
    grid.on("rowClicked", _row_clicked)

    with ui.row().classes("w-full justify-end mb-3"):
        ui.button("Open Selected Feature", on_click=_open_selected)

    with ui.card().classes("w-full p-4"):
        ui.label("Select a feature row to view metadata, lineage, samples, and statistics.").classes(
            "text-gray-400"
        )

    await _load_detail(catalog[0].name)

    lifecycle = ClientLifecycleManager.get()
    client_id = get_or_create_client_id()
    if client_id:
        await lifecycle.register_client(client_id)
        await lifecycle.register_cleanup_callback(
            client_id,
            lambda: app.storage.client.pop(_CACHE_KEY, None),
            owner_key=_CLEANUP_OWNER_KEY,
        )


__all__ = ["feature_browser_page"]
