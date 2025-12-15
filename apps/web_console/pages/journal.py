"""Trade Journal & Analysis page."""

from __future__ import annotations

import logging
import os
from datetime import date, timedelta
from typing import Any

import streamlit as st

from apps.web_console.auth.audit_log import AuditLogger
from apps.web_console.auth.permissions import Permission, get_authorized_strategies, has_permission
from apps.web_console.auth.session_manager import get_current_user, require_auth
from apps.web_console.components.trade_stats import render_trade_stats
from apps.web_console.components.trade_table import render_trade_table
from apps.web_console.data.strategy_scoped_queries import StrategyScopedDataAccess
from apps.web_console.utils.async_helpers import run_async
from apps.web_console.utils.db_pool import get_db_pool, get_redis_client

logger = logging.getLogger(__name__)

FEATURE_TRADE_JOURNAL = os.getenv("FEATURE_TRADE_JOURNAL", "false").lower() in {"1", "true", "yes", "on"}
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100
MAX_RANGE_DAYS = 365


@require_auth
def main() -> None:
    st.set_page_config(page_title="Trade Journal", page_icon="📔", layout="wide")
    st.title("Trade Journal")

    if not FEATURE_TRADE_JOURNAL:
        st.info("Feature not available.")
        logger.info("trade_journal_feature_disabled")
        return

    user = get_current_user()
    if not has_permission(user, Permission.VIEW_TRADES):
        st.error("Permission denied: VIEW_TRADES required.")
        logger.warning(
            "trade_journal_permission_denied",
            extra={"user_id": user.get("user_id"), "permission": "VIEW_TRADES"},
        )
        st.stop()

    authorized_strategies = get_authorized_strategies(user)
    if not authorized_strategies:
        st.warning("You don't have access to any strategies. Contact administrator.")
        st.stop()

    # Initialize pagination state before filters (needed for on_change callback)
    if "journal_page" not in st.session_state:
        st.session_state.journal_page = 0

    def _reset_pagination() -> None:
        """Reset pagination when filters change."""
        st.session_state.journal_page = 0

    start_date, end_date = _select_date_range(on_change=_reset_pagination)

    col1, col2 = st.columns(2)
    with col1:
        symbol_filter = st.text_input(
            "Filter by Symbol", "", on_change=_reset_pagination, key="journal_symbol"
        ).upper().strip() or None
    with col2:
        side_choice = st.selectbox(
            "Filter by Side", ["All", "buy", "sell"], on_change=_reset_pagination, key="journal_side"
        )
        side_filter: str | None = None if side_choice == "All" else side_choice

    page_size = st.selectbox(
        "Trades per page", [25, 50, 100], index=1, on_change=_reset_pagination, key="journal_page_size"
    )
    page_size = min(page_size, MAX_PAGE_SIZE)

    db_pool = get_db_pool()
    redis_client = get_redis_client()
    data_access = StrategyScopedDataAccess(db_pool, redis_client, user)

    with st.spinner("Loading statistics..."):
        stats = run_async(
            data_access.get_trade_stats(
                date_from=start_date,
                date_to=end_date + timedelta(days=1),
                symbol=symbol_filter,
                side=side_filter,
            )
        )
        render_trade_stats(stats)

    st.divider()

    offset = st.session_state.journal_page * page_size
    with st.spinner("Loading trades..."):
        trades = run_async(
            data_access.get_trades(
                limit=page_size,
                offset=offset,
                date_from=start_date,
                date_to=end_date + timedelta(days=1),
                symbol=symbol_filter,
                side=side_filter,
            )
        )

    render_trade_table(trades, page_size, st.session_state.journal_page)
    _render_pagination_controls(len(trades), page_size)

    st.divider()
    _render_export_section(data_access, user, start_date, end_date, symbol_filter, side_filter)


def _select_date_range(
    on_change: Any = None,
) -> tuple[date, date]:
    """Date range selector with UTC semantics.

    Args:
        on_change: Optional callback when date selection changes (resets pagination).
    """

    today = date.today()
    presets = {
        "7 Days": (today - timedelta(days=7), today),
        "30 Days": (today - timedelta(days=30), today),
        "90 Days": (today - timedelta(days=90), today),
        "YTD": (date(today.year, 1, 1), today),
        "Custom": None,
    }

    preset = st.selectbox(
        "Date Range", list(presets.keys()), index=1,
        on_change=on_change, key="journal_date_preset"
    )

    if preset != "Custom":
        result = presets[preset]
        assert result is not None  # Only "Custom" maps to None
        return result

    date_input = st.date_input(
        "Select Date Range",
        value=(today - timedelta(days=30), today),
        max_value=today,
        on_change=on_change,
        key="journal_date_custom",
    )

    if isinstance(date_input, tuple) and len(date_input) == 2:
        start_date, end_date = date_input
    else:
        start_date = end_date = date_input if isinstance(date_input, date) else today

    if (end_date - start_date).days > MAX_RANGE_DAYS:
        st.warning(f"Date range capped to {MAX_RANGE_DAYS} days.")
        start_date = end_date - timedelta(days=MAX_RANGE_DAYS)

    return start_date, end_date


def _render_pagination_controls(current_count: int, page_size: int) -> None:
    """Render pagination controls."""

    col1, col2, col3 = st.columns([1, 2, 1])

    with col1:
        if st.button("Previous", disabled=st.session_state.journal_page == 0):
            st.session_state.journal_page -= 1
            st.rerun()

    with col2:
        st.write(f"Page {st.session_state.journal_page + 1}")

    with col3:
        if st.button("Next", disabled=current_count < page_size):
            st.session_state.journal_page += 1
            st.rerun()


def _render_export_section(
    data_access: StrategyScopedDataAccess,
    user: dict[str, Any],
    start_date: date,
    end_date: date,
    symbol_filter: str | None,
    side_filter: str | None,
) -> None:
    """Render export section with permission and audit checks."""

    st.subheader("Export Trades")

    if not has_permission(user, Permission.EXPORT_DATA):
        st.info("Export permission required. Contact administrator.")
        return

    col1, col2 = st.columns(2)

    with col1:
        if st.button("Export to CSV"):
            _do_export(data_access, user, "csv", start_date, end_date, symbol_filter, side_filter)

    with col2:
        if st.button("Export to Excel"):
            _do_export(data_access, user, "xlsx", start_date, end_date, symbol_filter, side_filter)


def _do_export(
    data_access: StrategyScopedDataAccess,
    user: dict[str, Any],
    export_type: str,
    start_date: date,
    end_date: date,
    symbol_filter: str | None,
    side_filter: str | None,
) -> None:
    """Execute export with streaming and audit logging."""

    user_id = user.get("user_id", "unknown")
    authorized_strategies = get_authorized_strategies(user)

    # Build filters once so CSV/Excel exporters stay in sync and future filters
    # are added in a single place.
    filters: dict[str, Any] = {}
    if symbol_filter:
        filters["symbol"] = symbol_filter
    if side_filter:
        filters["side"] = side_filter

    try:
        with st.spinner(f"Exporting to {export_type.upper()}..."):
            if export_type == "csv":
                content, row_count = run_async(
                    _export_csv(data_access, start_date, end_date, filters)
                )
                mime_type = "text/csv"
                file_ext = "csv"
            else:
                content, row_count = run_async(
                    _export_excel(data_access, start_date, end_date, filters)
                )
                mime_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                file_ext = "xlsx"

            filename = f"trades_{start_date}_{end_date}.{file_ext}"

            db_pool = get_db_pool()
            audit_logger = AuditLogger(db_pool)
            run_async(
                audit_logger.log_export(
                    user_id=user_id,
                    export_type=export_type,
                    resource_type="trades",
                    row_count=row_count,
                    metadata={
                        "date_from": start_date,
                        "date_to": end_date,
                        "filters": filters,
                        "strategy_ids": authorized_strategies,
                    },
                )
            )

            logger.info(
                "trade_export_success",
                extra={
                    "user_id": user_id,
                    "export_type": export_type,
                    "row_count": row_count,
                    "date_range": f"{start_date}_{end_date}",
                    "filters": filters,
                    "strategy_ids": authorized_strategies,
                },
            )

            st.download_button(f"Download {export_type.upper()}", content, filename, mime_type)

    except Exception as exc:  # pragma: no cover - defensive
        logger.error(
            "trade_export_failed",
            extra={
                "user_id": user_id,
                "export_type": export_type,
                "error": str(exc),
                "date_range": f"{start_date}_{end_date}",
                "filters": filters,
                "strategy_ids": authorized_strategies,
            },
            exc_info=True,
        )
        st.error(f"Export failed: {exc}")


async def _export_csv(
    data_access: StrategyScopedDataAccess,
    start_date: date,
    end_date: date,
    filters: dict[str, Any],
) -> tuple[bytes, int]:
    """Export trades to CSV using streaming."""

    import csv
    import io

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Date", "Symbol", "Side", "Qty", "Price", "Realized P&L", "Strategy"])

    # Filters are pre-computed in _do_export to keep CSV/Excel parity and make
    # future filter additions a single-point change.
    row_count = 0

    async for trade in data_access.stream_trades_for_export(
        date_from=start_date,
        date_to=end_date + timedelta(days=1),
        **filters,
    ):
        writer.writerow(
            [
                trade.get("executed_at"),
                trade.get("symbol"),
                trade.get("side"),
                trade.get("qty"),
                trade.get("price"),
                trade.get("realized_pnl"),
                trade.get("strategy_id"),
            ]
        )
        row_count += 1

    return output.getvalue().encode("utf-8"), row_count


async def _export_excel(
    data_access: StrategyScopedDataAccess,
    start_date: date,
    end_date: date,
    filters: dict[str, Any],
) -> tuple[bytes, int]:
    """Export trades to Excel using streaming write mode."""

    import io

    from openpyxl import Workbook  # type: ignore[import-untyped]

    wb = Workbook(write_only=True)
    ws = wb.create_sheet("Trades")
    ws.append(["Date", "Symbol", "Side", "Qty", "Price", "Realized P&L", "Strategy"])

    # Filters are pre-computed in _do_export to keep CSV/Excel parity and make
    # future filter additions a single-point change.
    row_count = 0

    async for trade in data_access.stream_trades_for_export(
        date_from=start_date,
        date_to=end_date + timedelta(days=1),
        **filters,
    ):
        ws.append(
            [
                trade.get("executed_at"),
                trade.get("symbol"),
                trade.get("side"),
                trade.get("qty"),
                trade.get("price"),
                trade.get("realized_pnl"),
                trade.get("strategy_id"),
            ]
        )
        row_count += 1

    output = io.BytesIO()
    wb.save(output)
    return output.getvalue(), row_count


if __name__ == "__main__":  # pragma: no cover - manual run convenience
    main()
