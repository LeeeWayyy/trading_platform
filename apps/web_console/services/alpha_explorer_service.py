"""Service layer for alpha signal exploration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

import polars as pl

from libs.models.registry import ModelRegistry
from libs.models.types import ModelMetadata, ModelStatus, ModelType

if TYPE_CHECKING:
    from libs.alpha.metrics import AlphaMetricsAdapter
    from libs.alpha.research_platform import BacktestResult


@dataclass
class SignalSummary:
    """Summary of alpha signal for list view.

    NOTE: ModelMetadata has no 'name' or 'status' fields.
    - Display name is derived from model_id or parameters['name']
    - Status filtering available via ModelRegistry.list_models(status=...) if needed
    """

    signal_id: str
    display_name: str
    version: str
    mean_ic: float | None
    icir: float | None
    created_at: date
    backtest_job_id: str | None


@dataclass
class SignalMetrics:
    """Detailed metrics for selected signal."""

    signal_id: str
    name: str
    version: str
    mean_ic: float
    icir: float
    hit_rate: float
    coverage: float
    average_turnover: float
    decay_half_life: float | None
    n_days: int
    start_date: date
    end_date: date


class AlphaExplorerService:
    """Service for browsing and analyzing alpha signals."""

    def __init__(
        self,
        registry: ModelRegistry,
        metrics_adapter: AlphaMetricsAdapter | None = None,
    ) -> None:
        self._registry = registry
        self._metrics = metrics_adapter

    def list_signals(
        self,
        status: ModelStatus | None = None,
        min_ic: float | None = None,
        max_ic: float | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> tuple[list[SignalSummary], int]:
        """List signals with filtering and pagination.

        Args:
            status: Filter by ModelStatus (staged, production, archived, failed)
            min_ic: Minimum IC threshold
            max_ic: Maximum IC threshold
            limit: Page size
            offset: Page offset

        Returns:
            Tuple of (signals, total_count)
        """
        models = self._registry.list_models(
            model_type=ModelType.alpha_weights,
            status=status,
        )

        if min_ic is not None or max_ic is not None:
            models = [m for m in models if self._in_ic_range(m, min_ic, max_ic)]

        total = len(models)
        page = models[offset : offset + limit]

        summaries = [self._to_summary(m) for m in page]
        return summaries, total

    def get_signal_metrics(self, signal_id: str) -> SignalMetrics:
        """Get detailed metrics for a signal.

        signal_id is the model_id from ModelMetadata (NOT model_type:version).
        Uses get_model_by_id to look up by model_id directly.
        """
        metadata = self._registry.get_model_by_id(signal_id)
        if not metadata:
            raise ValueError(f"Signal not found: {signal_id}")

        backtest_result = self._load_backtest_result(metadata)

        display_name = metadata.parameters.get("name", signal_id)

        return SignalMetrics(
            signal_id=signal_id,
            name=display_name,
            version=metadata.version,
            mean_ic=backtest_result.mean_ic if backtest_result else 0.0,
            icir=backtest_result.icir if backtest_result else 0.0,
            hit_rate=backtest_result.hit_rate if backtest_result else 0.0,
            coverage=backtest_result.coverage if backtest_result else 0.0,
            average_turnover=backtest_result.average_turnover if backtest_result else 0.0,
            decay_half_life=backtest_result.decay_half_life if backtest_result else None,
            n_days=backtest_result.n_days if backtest_result else 0,
            start_date=backtest_result.start_date if backtest_result else date.today(),
            end_date=backtest_result.end_date if backtest_result else date.today(),
        )

    def get_ic_timeseries(self, signal_id: str) -> pl.DataFrame:
        """Get daily IC time series for visualization.

        signal_id is the model_id - look up directly via get_model_by_id.

        Returns DataFrame with columns: [date, ic, rank_ic, rolling_ic_20d]
        """
        # Define consistent schema including rolling column
        ic_schema = {
            "date": pl.Date,
            "ic": pl.Float64,
            "rank_ic": pl.Float64,
            "rolling_ic_20d": pl.Float64,
        }

        metadata = self._registry.get_model_by_id(signal_id)
        if not metadata:
            return pl.DataFrame(schema=ic_schema)

        backtest_result = self._load_backtest_result(metadata)

        if backtest_result is None:
            return pl.DataFrame(schema=ic_schema)

        # Polars 1.x uses rolling_mean(window_size=N) for row-based rolling
        daily_ic: pl.DataFrame = backtest_result.daily_ic.with_columns(
            pl.col("rank_ic").rolling_mean(window_size=20).alias("rolling_ic_20d")
        )

        return daily_ic

    def get_decay_curve(self, signal_id: str) -> pl.DataFrame:
        """Get decay curve data for visualization.

        signal_id is the model_id - look up directly via get_model_by_id.

        Returns DataFrame with columns: [horizon, ic, rank_ic]
        """
        metadata = self._registry.get_model_by_id(signal_id)
        if not metadata:
            return pl.DataFrame(schema={"horizon": pl.Int64, "ic": pl.Float64, "rank_ic": pl.Float64})

        backtest_result = self._load_backtest_result(metadata)

        if backtest_result is None:
            return pl.DataFrame(schema={"horizon": pl.Int64, "ic": pl.Float64, "rank_ic": pl.Float64})

        return backtest_result.decay_curve

    def compute_correlation(self, signal_ids: list[str]) -> pl.DataFrame:
        """Compute correlation matrix for selected signals.

        signal_ids are model_ids - look up directly via get_model_by_id.

        Returns DataFrame with signal names as index/columns.
        """
        signals_data: dict[str, pl.DataFrame] = {}
        for sid in signal_ids:
            metadata = self._registry.get_model_by_id(sid)
            if not metadata:
                continue

            backtest_result = self._load_backtest_result(metadata)
            if backtest_result is None:
                continue

            daily_mean = (
                backtest_result.daily_signals.group_by("date")
                .agg(pl.col("signal").mean().alias("signal"))
                .sort("date")
            )
            display_name = metadata.parameters.get("name", sid)
            signals_data[display_name] = daily_mean

        # Return empty DataFrame with schema on insufficient data
        empty_corr = pl.DataFrame(schema={"signal": pl.Utf8})

        if len(signals_data) < 2:
            return empty_corr

        joined: pl.DataFrame | None = None
        for name, df in signals_data.items():
            renamed = df.rename({"signal": name})
            joined = renamed if joined is None else joined.join(renamed, on="date", how="inner")

        if joined is None or joined.width < 3:
            return empty_corr

        corr_pd = joined.drop("date").to_pandas().corr()
        corr_pd.insert(0, "signal", corr_pd.index)
        return pl.from_pandas(corr_pd, include_index=False)

    def _to_summary(self, metadata: ModelMetadata) -> SignalSummary:
        """Convert ModelMetadata to SignalSummary.

        NOTE: ModelMetadata has no 'name' or 'status' fields.
        - Display name derived from parameters['name'] or model_id
        - Status tracking is separate (MVP: assume all loaded = active)
        - backtest_job_id comes from parameters['backtest_job_id'] set at registration
        """
        display_name = metadata.parameters.get("name", metadata.model_id)

        backtest_job_id = metadata.parameters.get("backtest_job_id") if metadata.parameters else None

        return SignalSummary(
            signal_id=metadata.model_id,
            display_name=display_name,
            version=metadata.version,
            mean_ic=metadata.metrics.get("mean_ic") if metadata.metrics else None,
            icir=metadata.metrics.get("icir") if metadata.metrics else None,
            created_at=metadata.created_at.date(),
            backtest_job_id=backtest_job_id,
        )

    def _in_ic_range(
        self, metadata: ModelMetadata, min_ic: float | None, max_ic: float | None
    ) -> bool:
        """Check if model's IC is within range."""
        ic = metadata.metrics.get("mean_ic") if metadata.metrics else None
        if ic is None:
            return False
        if min_ic is not None and ic < min_ic:
            return False
        if max_ic is not None and ic > max_ic:
            return False
        return True

    def _load_backtest_result(self, metadata: ModelMetadata | None) -> BacktestResult | None:
        """Load backtest result from BacktestResultStorage.

        IMPORTANT: BacktestResultStorage is keyed by job_id (str), not run_id.
        The mapping approach:
        - ModelMetadata.parameters['backtest_job_id'] stores the job_id at registration time

        For MVP, we assume backtest_job_id is stored in ModelMetadata.parameters
        during the model registration process (after backtest completes).

        Example registration flow:
          1. Run backtest via backtest_jobs table -> job_id (str) created
          2. Register model -> registry.register(..., parameters={'backtest_job_id': job_id})
          3. Load here -> storage.get_result(job_id)

        Note: BacktestResultStorage.get_result() takes a str job_id, not UUID.

        CRITICAL: BacktestResultStorage uses SYNC database pool (not async).
        Must use get_sync_db_pool() from apps.web_console.utils.sync_db_pool,
        NOT the async pool from db_pool.py.
        """
        if metadata is None:
            return None

        job_id = metadata.parameters.get("backtest_job_id") if metadata.parameters else None
        if not job_id:
            return None

        from apps.web_console.utils.sync_db_pool import get_sync_db_pool
        from libs.backtest.result_storage import BacktestResultStorage

        try:
            pool = get_sync_db_pool()
            storage = BacktestResultStorage(pool)
            return storage.get_result(job_id)
        except Exception:
            import logging

            logging.getLogger(__name__).warning(
                "Failed to load backtest result for job_id=%s", job_id
            )
            return None


__all__ = ["AlphaExplorerService", "SignalSummary", "SignalMetrics"]
