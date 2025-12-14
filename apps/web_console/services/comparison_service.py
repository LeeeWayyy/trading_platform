"""Strategy comparison service (T6.4b).

This service fetches P&L data for a set of strategies and produces:
- Side-by-side performance metrics
- Equity curve data
- Correlation matrix
- Combined portfolio simulation utilities
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any, cast

import pandas as pd

from apps.web_console.data.strategy_scoped_queries import StrategyScopedDataAccess


class ComparisonService:
    """Business logic for the strategy comparison tool."""

    def __init__(self, scoped_access: Any):
        self._scoped_access = scoped_access

    async def get_comparison_data(
        self,
        strategy_ids: list[str],
        date_from: date,
        date_to: date,
    ) -> dict[str, Any]:
        """Fetch P&L data and compute comparison artifacts."""
        if not strategy_ids:
            return self._empty_payload(strategy_ids)
        if date_from > date_to:
            raise ValueError("date_from cannot be after date_to")

        # Guard against zero authorized strategies (would cause divide by zero later)
        authorized_count = len(self._scoped_access.authorized_strategies)
        if authorized_count == 0:
            raise PermissionError("No authorized strategies available")

        # Calculate sufficient limit to avoid data truncation:
        # get_pnl_summary queries ALL authorized strategies, so we must size the limit
        # based on authorized count (not selected count) to ensure full date coverage.
        # After fetch, _to_pnl_frame filters to only selected strategies.
        num_days = (date_to - date_from).days + 1
        required_rows = num_days * authorized_count
        max_limit = StrategyScopedDataAccess.MAX_LIMIT
        truncation_warning = None
        if required_rows > max_limit:
            truncation_warning = (
                f"Date range requires ~{required_rows} rows but limit is {max_limit}. "
                f"Results may be incomplete for ranges exceeding "
                f"~{max_limit // authorized_count} days with {authorized_count} authorized strategies."
            )
        pnl_rows = await self._scoped_access.get_pnl_summary(
            date_from, date_to, limit=min(required_rows, max_limit)
        )
        pnl_frame = self._to_pnl_frame(pnl_rows, strategy_ids)

        if pnl_frame.empty:
            payload = self._empty_payload(strategy_ids)
            payload["truncation_warning"] = truncation_warning
            return payload

        equity_curves = self._build_equity_curves(pnl_frame)
        metrics = self._compute_metrics(pnl_frame)
        corr_matrix = self.compute_correlation_matrix(pnl_frame)
        default_weights = {sid: round(1 / len(strategy_ids), 4) for sid in strategy_ids}
        combined = self.compute_combined_portfolio(default_weights, pnl_frame)

        return {
            "pnl_frame": pnl_frame,
            "equity_curves": equity_curves,
            "metrics": metrics,
            "correlation_matrix": corr_matrix,
            "combined_portfolio": combined,
            "default_weights": default_weights,
            "truncation_warning": truncation_warning,
        }

    @staticmethod
    def _to_pnl_frame(pnl_rows: list[dict[str, Any]], strategy_ids: list[str]) -> pd.DataFrame:
        """Convert raw P&L rows to a pivoted DataFrame indexed by trade_date."""
        if not pnl_rows:
            return pd.DataFrame()

        df = pd.DataFrame(pnl_rows)
        if df.empty:
            return df

        if "trade_date" not in df.columns or "strategy_id" not in df.columns:
            return pd.DataFrame()

        df = df[df["strategy_id"].isin(strategy_ids)]
        if df.empty:
            return pd.DataFrame()

        df["trade_date"] = pd.to_datetime(df["trade_date"])
        # Note: Using explicit if/else instead of df.get("daily_pnl", 0).fillna(0)
        # because df.get() returns a scalar when column is missing, which fails on .fillna()
        if "daily_pnl" in df.columns:
            df["daily_pnl"] = df["daily_pnl"].fillna(0).astype(float)
        else:
            df["daily_pnl"] = 0.0
        df = df.sort_values("trade_date")

        pivot = (
            df.pivot_table(
                index="trade_date",
                columns="strategy_id",
                values="daily_pnl",
                aggfunc="sum",
            )
            .fillna(0.0)
            .sort_index()
        )
        return pivot

    @staticmethod
    def _build_equity_curves(pnl_frame: pd.DataFrame) -> list[dict[str, Any]]:
        """Build cumulative equity curves from daily P&L."""
        curves: list[dict[str, Any]] = []
        if pnl_frame.empty:
            return curves

        equity = pnl_frame.cumsum()
        for strategy_id in equity.columns:
            series = equity[strategy_id]
            curves.append(
                {
                    "strategy_id": strategy_id,
                    "equity": [
                        {"date": cast(pd.Timestamp, idx).date(), "equity": float(val)}
                        for idx, val in series.items()
                    ],
                }
            )
        return curves

    @staticmethod
    def _max_drawdown(series: pd.Series) -> float:
        if series.empty:
            return 0.0
        # Prepend zero baseline to correctly capture drawdowns from negative starts
        # e.g., [-100, 50] should report -100 drawdown, not 0
        equity = pd.concat([pd.Series([0.0]), series]).reset_index(drop=True)
        running_max = equity.cummax()
        drawdown = equity - running_max
        return float(drawdown.min() or 0.0)

    def _compute_metrics(self, pnl_frame: pd.DataFrame) -> dict[str, dict[str, float]]:
        """Compute simple performance metrics per strategy.

        Note: Sharpe ratio and volatility are computed from raw P&L dollars,
        not percentage returns. This is intentional as portfolio capital data
        is not available in this context. The metrics remain valid for relative
        comparison between strategies but are not directly comparable to
        industry-standard return-based calculations.
        """
        metrics: dict[str, dict[str, float]] = {}
        if pnl_frame.empty:
            return metrics

        equity = pnl_frame.cumsum()
        annualizer = math.sqrt(252)

        for strategy_id in pnl_frame.columns:
            daily = pnl_frame[strategy_id]
            total_return = float(daily.sum())
            # Guard against NaN from ddof=1 with single data point
            vol_raw = daily.std(ddof=1)
            volatility = 0.0 if (len(daily) < 2 or pd.isna(vol_raw)) else float(vol_raw)
            sharpe = float((daily.mean() / volatility) * annualizer) if volatility else 0.0
            max_dd = self._max_drawdown(equity[strategy_id])
            metrics[strategy_id] = {
                "total_return": total_return,
                "volatility": volatility,
                "sharpe": sharpe,
                "max_drawdown": max_dd,
            }
        return metrics

    @staticmethod
    def compute_correlation_matrix(pnl_data: pd.DataFrame | list[dict[str, Any]]) -> pd.DataFrame:
        """Compute correlation matrix from daily P&L."""
        if isinstance(pnl_data, list):
            if not pnl_data:
                return pd.DataFrame()
            df = pd.DataFrame(pnl_data)
            if {"trade_date", "strategy_id", "daily_pnl"} - set(df.columns):
                return pd.DataFrame()
            df["trade_date"] = pd.to_datetime(df["trade_date"])
            df["daily_pnl"] = df["daily_pnl"].astype(float)
            pnl_frame = (
                df.pivot_table(
                    index="trade_date", columns="strategy_id", values="daily_pnl", aggfunc="sum"
                )
                .fillna(0.0)
                .sort_index()
            )
        else:
            pnl_frame = pnl_data

        if pnl_frame.empty or len(pnl_frame.columns) < 2:
            return pd.DataFrame()

        return pnl_frame.corr()

    def compute_combined_portfolio(
        self, weights: dict[str, float], pnl_data: pd.DataFrame | list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Simulate a combined portfolio given weights and daily P&L."""
        valid, msg = self.validate_weights(weights)
        if not valid:
            raise ValueError(msg)

        if isinstance(pnl_data, pd.DataFrame):
            pnl_frame = pnl_data
        else:
            pnl_frame = self._to_pnl_frame(pnl_data, list(weights.keys()))

        if pnl_frame.empty:
            return {
                "weights": weights,
                "equity_curve": [],
                "total_return": 0.0,
                "max_drawdown": 0.0,
            }

        # Reindex to include all weight keys, filling missing strategies with 0
        # This prevents KeyError when a strategy has no P&L data for the date range
        aligned = pnl_frame.reindex(columns=list(weights.keys()), fill_value=0.0)
        weight_vector = pd.Series(weights)
        combined_daily = aligned.mul(weight_vector, axis=1).sum(axis=1)
        equity = combined_daily.cumsum()

        # Guard against NaN from ddof=1 with single data point (consistent with _compute_metrics)
        vol_raw = combined_daily.std(ddof=1)
        volatility = 0.0 if len(combined_daily) < 2 or pd.isna(vol_raw) else float(vol_raw)

        return {
            "weights": weights,
            "equity_curve": [
                {"date": cast(pd.Timestamp, idx).date(), "equity": float(val)}
                for idx, val in equity.items()
            ],
            "total_return": float(combined_daily.sum()),
            "volatility": volatility,
            "max_drawdown": self._max_drawdown(equity),
        }

    @staticmethod
    def validate_weights(weights: dict[str, float]) -> tuple[bool, str]:
        """Validate weight bounds and sum."""
        if not weights:
            return False, "At least one weight is required"
        for strategy_id, weight in weights.items():
            if weight < 0.0 or weight > 1.0:
                return False, f"Weight for {strategy_id} must be between 0 and 1"
        total = sum(weights.values())
        if abs(total - 1.0) > 0.001:
            return False, f"Weights must sum to 1.0 (currently {total:.3f})"
        return True, ""

    @staticmethod
    def _empty_payload(strategy_ids: list[str]) -> dict[str, Any]:
        default_weights = (
            {sid: round(1 / len(strategy_ids), 4) for sid in strategy_ids} if strategy_ids else {}
        )
        return {
            "pnl_frame": pd.DataFrame(),
            "equity_curves": [],
            "metrics": {},
            "correlation_matrix": pd.DataFrame(),
            "combined_portfolio": {
                "weights": default_weights,
                "equity_curve": [],
                "total_return": 0.0,
                "max_drawdown": 0.0,
            },
            "default_weights": default_weights,
            "truncation_warning": None,
        }


__all__ = ["ComparisonService"]
