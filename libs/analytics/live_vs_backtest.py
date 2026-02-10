"""Live vs Backtest overlay analyzer (P6T12.3).

Pure data-in/data-out analyzer that compares live trading performance
against backtest expectations.  No I/O - the caller is responsible
for fetching and normalising inputs.

**Inputs:** Both ``live_returns`` and ``backtest_returns`` must have
``{date, return}`` schema with ``date`` as ``pl.Date`` and sorted
ascending.

**Known Limitations (displayed near overlay chart):**
- Live series from ``pnl_daily`` includes realized + unrealized P&L
- Backtest assumes instant fills at close; live has actual fill prices
- Market impact estimated in backtest, real in live
- Corporate action reconciliation may have delays
- Trade timing: backtest is T, live is T+settlement
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from enum import Enum

import polars as pl
from pydantic import BaseModel

from libs.analytics.metrics import compute_tracking_error


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
class OverlayConfig(BaseModel):
    """Configuration for live vs backtest overlay analysis."""

    tracking_error_threshold: float = 0.05  # 5% annualized
    divergence_threshold: float = 0.10  # 10pp cumulative
    consecutive_days_for_yellow: int = 5
    rolling_window_days: int = 20


# ---------------------------------------------------------------------------
# Alert levels
# ---------------------------------------------------------------------------
class AlertLevel(Enum):
    """Severity levels for live vs backtest divergence."""

    NONE = "none"
    YELLOW = "yellow"
    RED = "red"


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------
@dataclass
class OverlayResult:
    """Output of ``LiveVsBacktestAnalyzer.analyze()``."""

    live_cumulative: pl.DataFrame  # {date, cumulative_return}
    backtest_cumulative: pl.DataFrame  # {date, cumulative_return}
    tracking_error_annualized: float | None  # None if < 2 aligned dates
    cumulative_divergence: float | None  # None if < 2 aligned dates
    divergence_start_date: date | None
    alert_level: AlertLevel
    alert_message: str


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------
class LiveVsBacktestAnalyzer:
    """Compare live performance against backtest expectations.

    Pure data-in/data-out.  No I/O - the caller fetches data.
    """

    def __init__(self, config: OverlayConfig | None = None) -> None:
        self.config = config or OverlayConfig()

    def analyze(
        self,
        live_returns: pl.DataFrame,
        backtest_returns: pl.DataFrame,
        overlay_start: date | None = None,
        overlay_end: date | None = None,
    ) -> OverlayResult:
        """Run the overlay analysis.

        Args:
            live_returns: ``{date, return}`` from live trading (sorted asc).
            backtest_returns: ``{date, return}`` from backtest (sorted asc).
            overlay_start: Start of overlay window (defaults to backtest start).
            overlay_end: End of overlay window (defaults to backtest end).

        Returns:
            ``OverlayResult`` with cumulative curves, metrics, and alerts.
        """
        # Determine overlay window
        bt_dates = backtest_returns["date"].to_list()
        if not bt_dates:
            return self._insufficient_result()

        start = overlay_start or bt_dates[0]
        end = overlay_end or bt_dates[-1]

        # Restrict backtest to overlay window
        bt_window = backtest_returns.filter(
            (pl.col("date") >= start) & (pl.col("date") <= end)
        ).sort("date")

        if len(bt_window) == 0:
            return self._insufficient_result(
                message="Selected window has no overlapping dates with backtest"
            )

        # Left-join live returns onto backtest dates, zero-fill missing
        aligned = bt_window.select("date", pl.col("return").alias("bt_return")).join(
            live_returns.select("date", pl.col("return").alias("live_return")),
            on="date",
            how="left",
        ).with_columns(
            pl.col("live_return").fill_null(0.0),  # no trades = flat
        ).sort("date")

        n_aligned = len(aligned)
        if n_aligned < 2:
            return self._insufficient_result()

        # Drop NaN/null in either return series to prevent NaN propagation
        # into cumulative curves and alert computations
        aligned = aligned.filter(
            pl.col("bt_return").is_not_null()
            & pl.col("bt_return").is_not_nan()
            & pl.col("live_return").is_not_nan()
        )
        if len(aligned) < 2:
            return self._insufficient_result()

        # Compute cumulative returns: cumprod(1 + r) - 1
        bt_cum = (aligned["bt_return"] + 1.0).cum_prod() - 1.0
        live_cum = (aligned["live_return"] + 1.0).cum_prod() - 1.0
        dates = aligned["date"].to_list()

        live_cumulative = pl.DataFrame({
            "date": dates,
            "cumulative_return": live_cum.to_list(),
        })
        backtest_cumulative = pl.DataFrame({
            "date": dates,
            "cumulative_return": bt_cum.to_list(),
        })

        # Tracking error (pre-aligned, using left-join with zero-fill)
        te_df_live = aligned.select("date", pl.col("live_return").alias("return"))
        te_df_bt = aligned.select("date", pl.col("bt_return").alias("return"))
        te = compute_tracking_error(te_df_live, te_df_bt, pre_aligned=True)

        # Cumulative divergence: abs(cum_live[-1] - cum_bt[-1])
        cum_div: float | None = None
        if len(live_cum) > 0 and len(bt_cum) > 0:
            cum_div = abs(live_cum[-1] - bt_cum[-1])

        # Divergence start date
        div_start = self._find_divergence_start(
            live_cum.to_list(), bt_cum.to_list(), dates
        )

        # Alert logic
        alert_level, alert_message = self._compute_alert(
            aligned, te, cum_div, len(aligned)
        )

        return OverlayResult(
            live_cumulative=live_cumulative,
            backtest_cumulative=backtest_cumulative,
            tracking_error_annualized=te,
            cumulative_divergence=cum_div,
            divergence_start_date=div_start,
            alert_level=alert_level,
            alert_message=alert_message,
        )

    def _find_divergence_start(
        self,
        live_cum: list[float],
        bt_cum: list[float],
        dates: list[date],
    ) -> date | None:
        """Find the first date where rolling max divergence breaches threshold."""
        if len(live_cum) < 2:
            return None

        window = self.config.rolling_window_days
        divergences = [abs(lc - bc) for lc, bc in zip(live_cum, bt_cum, strict=True)]

        for i in range(window - 1, len(divergences)):
            window_max = max(divergences[i - window + 1: i + 1])
            if window_max > self.config.divergence_threshold:
                return dates[i]

        return None

    def _compute_alert(
        self,
        aligned: pl.DataFrame,
        te: float | None,
        cum_div: float | None,
        n_dates: int,
    ) -> tuple[AlertLevel, str]:
        """Determine alert level (RED > YELLOW > NONE)."""
        cfg = self.config

        # RED: cumulative divergence breach (can trigger with >= 2 dates)
        if cum_div is not None and cum_div > cfg.divergence_threshold:
            return (
                AlertLevel.RED,
                f"Cumulative divergence {cum_div:.1%} exceeds "
                f"threshold {cfg.divergence_threshold:.1%}",
            )

        # YELLOW: rolling TE breach for consecutive days
        if n_dates >= cfg.rolling_window_days:
            diffs = (
                aligned["live_return"] - aligned["bt_return"]
            ).to_list()

            # Compute rolling TE series
            consecutive = 0
            for i in range(cfg.rolling_window_days - 1, len(diffs)):
                window_data = diffs[i - cfg.rolling_window_days + 1: i + 1]
                n = len(window_data)
                if n < 2:
                    continue
                mean_d = sum(window_data) / n
                var_d = sum((d - mean_d) ** 2 for d in window_data) / (n - 1)
                rolling_te = math.sqrt(var_d) * math.sqrt(252)

                if math.isnan(rolling_te):
                    continue  # NaN skips without resetting streak

                if rolling_te > cfg.tracking_error_threshold:
                    consecutive += 1
                    if consecutive >= cfg.consecutive_days_for_yellow:
                        return (
                            AlertLevel.YELLOW,
                            f"Rolling TE > {cfg.tracking_error_threshold:.1%} "
                            f"for {consecutive} consecutive days",
                        )
                else:
                    consecutive = 0
        elif n_dates >= 2:
            # Enough for RED check (already done above) but not YELLOW
            pass
        else:
            return (AlertLevel.NONE, "Insufficient data for alert computation")

        return (AlertLevel.NONE, "No divergence detected")

    def _insufficient_result(self, message: str = "Insufficient data") -> OverlayResult:
        empty = pl.DataFrame(
            {"date": [], "cumulative_return": []},
            schema={"date": pl.Date, "cumulative_return": pl.Float64},
        )
        return OverlayResult(
            live_cumulative=empty,
            backtest_cumulative=empty,
            tracking_error_annualized=None,
            cumulative_divergence=None,
            divergence_start_date=None,
            alert_level=AlertLevel.NONE,
            alert_message=message,
        )


__all__ = [
    "AlertLevel",
    "LiveVsBacktestAnalyzer",
    "OverlayConfig",
    "OverlayResult",
]
