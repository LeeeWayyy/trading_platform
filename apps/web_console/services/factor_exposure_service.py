"""Service layer for factor exposure calculations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

import polars as pl

from apps.web_console.auth.permissions import Permission, has_permission
from apps.web_console.utils.async_helpers import run_async

if TYPE_CHECKING:
    from apps.web_console.utils.db_pool import AsyncConnectionAdapter
    from libs.factors.factor_builder import FactorBuilder


@dataclass(frozen=True)
class FactorDefinition:
    """Factor definition metadata for display purposes.

    Attributes:
        name: Canonical factor name (e.g., "momentum_12_1").
        category: Factor bucket such as value, momentum, quality, size, low_vol.
        description: Human-readable description used in the UI.
    """

    name: str
    category: str
    description: str


@dataclass(frozen=True)
class ExposureData:
    """Container for exposure data returned to Streamlit components.

    Attributes:
        exposures: Polars DataFrame with columns [date, factor, exposure].
        factors: Ordered list of factor names requested.
        date_range: (start_date, end_date) tuple for the exposure window.
    """

    exposures: pl.DataFrame
    factors: list[str]
    date_range: tuple[date, date]


class FactorExposureService:
    """Compute portfolio factor exposures for the web console.

    This service pulls current positions (global table), maps symbols to PERMNOs,
    and computes factor exposures via FactorBuilder. The results are aggregated
    into portfolio-level exposures using weight-averaged z-scores.
    """

    def __init__(
        self,
        factor_builder: FactorBuilder,
        db_adapter: AsyncConnectionAdapter | None,
        redis_client: Any,
        user: dict[str, Any],
    ) -> None:
        """Initialize the service with dependencies and user context.

        Args:
            factor_builder: FactorBuilder instance for computing exposures.
            db_adapter: AsyncConnectionAdapter for database access (may be None).
            redis_client: Redis adapter (unused in MVP but kept for parity).
            user: Authenticated user dict from get_current_user().

        Returns:
            None.

        Example:
            >>> service = FactorExposureService(builder, db_adapter, None, {"role": "admin"})
            >>> defs = service.get_factor_definitions()
            >>> defs[0].name
            'momentum_12_1'
        """

        self._builder = factor_builder
        self._db = db_adapter
        self._redis = redis_client
        self._user = user

    def get_factor_definitions(self) -> list[FactorDefinition]:
        """Return canonical factor definitions for UI selection.

        Uses libs.factors.factor_definitions.CANONICAL_FACTORS and instantiates
        each factor class to access category and description attributes.

        Returns:
            List of FactorDefinition entries in canonical order.

        Example:
            >>> service = FactorExposureService(builder, db_adapter, None, {"role": "admin"})
            >>> [f.name for f in service.get_factor_definitions()][:2]
            ['momentum_12_1', 'book_to_market']
        """

        from libs.factors import CANONICAL_FACTORS

        return [
            FactorDefinition(
                name=name,
                category=factor_cls().category,
                description=factor_cls().description,
            )
            for name, factor_cls in CANONICAL_FACTORS.items()
        ]

    def get_portfolio_exposures(
        self,
        portfolio_id: str,
        start_date: date,
        end_date: date,
        factors: list[str] | None = None,
    ) -> ExposureData:
        """Compute portfolio factor exposures over a date range.

        The portfolio exposures are computed by taking the weight-averaged
        z-scores of each factor across the current holdings. We use absolute
        market value for total portfolio weight while preserving signed weights
        for long/short exposure direction.

        Args:
            portfolio_id: Portfolio or strategy identifier for display (unused in MVP).
            start_date: First date (inclusive) for exposure computation.
            end_date: Last date (inclusive) for exposure computation.
            factors: Optional subset of factor names to compute. None means all.

        Returns:
            ExposureData containing a DataFrame with [date, factor, exposure].

        Raises:
            ValueError: If start_date is after end_date.

        Example:
            >>> from datetime import date
            >>> data = service.get_portfolio_exposures(\"alpha\", date(2024, 1, 1), date(2024, 1, 1))
            >>> data.exposures.columns
            ['date', 'factor', 'exposure']
        """

        if start_date > end_date:
            raise ValueError("start_date must be <= end_date")

        if factors is None:
            factors = [f.name for f in self.get_factor_definitions()]

        results: list[dict[str, Any]] = []
        current_date = start_date

        while current_date <= end_date:
            holdings = self._get_portfolio_holdings(portfolio_id, current_date)

            if holdings is not None and not holdings.is_empty():
                permnos = holdings.select("permno").to_series().to_list()
                factor_result = self._builder.compute_all_factors(
                    as_of_date=current_date,
                    universe=permnos,
                )

                exposures = factor_result.exposures

                for factor in factors:
                    factor_exposures = exposures.filter(pl.col("factor_name") == factor)
                    merged = factor_exposures.join(
                        holdings.select(["permno", "weight"]),
                        on="permno",
                        how="inner",
                    )

                    if merged.is_empty():
                        continue

                    # Portfolio exposure is weight-averaged z-score across holdings.
                    portfolio_exposure = (
                        merged.select((pl.col("weight") * pl.col("zscore")).sum()).item()
                    )
                    results.append(
                        {
                            "date": current_date,
                            "factor": factor,
                            "exposure": float(portfolio_exposure),
                        }
                    )

            current_date = current_date + timedelta(days=1)

        exposures_df = (
            pl.DataFrame(results)
            if results
            else pl.DataFrame(
                schema={"date": pl.Date, "factor": pl.Utf8, "exposure": pl.Float64}
            )
        )

        return ExposureData(
            exposures=exposures_df,
            factors=factors,
            date_range=(start_date, end_date),
        )

    def get_benchmark_exposures(
        self,
        benchmark: str,
        start_date: date,
        end_date: date,
        factors: list[str] | None = None,
    ) -> ExposureData | None:
        """Return benchmark exposures (MVP placeholder).

        Benchmark holdings data is not currently available in the platform,
        so this method returns None and logs the requested benchmark.

        Args:
            benchmark: Benchmark ticker symbol (e.g., "SPY").
            start_date: Start date for exposure window.
            end_date: End date for exposure window.
            factors: Optional factor list (unused for MVP).

        Returns:
            None for MVP; placeholder for future benchmark support.

        Example:
            >>> from datetime import date
            >>> service.get_benchmark_exposures(\"SPY\", date(2024, 1, 1), date(2024, 1, 5)) is None
            True
        """

        import logging

        logging.getLogger(__name__).info(
            "benchmark_exposure_unavailable",
            extra={"benchmark": benchmark, "start": start_date, "end": end_date},
        )
        return None

    def get_stock_exposures(
        self,
        portfolio_id: str,
        factor: str,
        as_of_date: date,
    ) -> pl.DataFrame:
        """Compute stock-level factor exposures for drill-down.

        Args:
            portfolio_id: Portfolio or strategy identifier (unused in MVP).
            factor: Factor name to display.
            as_of_date: Date for the exposure snapshot.

        Returns:
            DataFrame with columns [symbol, weight, exposure, contribution].

        Example:
            >>> from datetime import date
            >>> df = service.get_stock_exposures(\"alpha\", \"momentum_12_1\", date(2024, 1, 1))
            >>> df.columns
            ['symbol', 'weight', 'exposure', 'contribution']
        """

        holdings = self._get_portfolio_holdings(portfolio_id, as_of_date)

        if holdings is None or holdings.is_empty():
            return pl.DataFrame(
                schema={
                    "symbol": pl.Utf8,
                    "weight": pl.Float64,
                    "exposure": pl.Float64,
                    "contribution": pl.Float64,
                }
            )

        permnos = holdings.select("permno").to_series().to_list()
        factor_result = self._builder.compute_all_factors(
            as_of_date=as_of_date,
            universe=permnos,
        )

        exposures = factor_result.exposures.filter(pl.col("factor_name") == factor)
        if exposures.is_empty():
            return pl.DataFrame(
                schema={
                    "symbol": pl.Utf8,
                    "weight": pl.Float64,
                    "exposure": pl.Float64,
                    "contribution": pl.Float64,
                }
            )
        stock_exposures = exposures.join(
            holdings.select(["permno", "symbol", "weight"]),
            on="permno",
            how="inner",
        ).select(
            [
                "symbol",
                "weight",
                pl.col("zscore").alias("exposure"),
            ]
        )

        return (
            stock_exposures.with_columns(
                (pl.col("weight") * pl.col("exposure")).alias("contribution")
            )
            .sort("contribution", descending=True)
        )

    def _get_portfolio_holdings(
        self,
        portfolio_id: str,
        as_of_date: date,
    ) -> pl.DataFrame | None:
        """Return current holdings with PERMNO mapping and portfolio weights.

        Positions are stored in a global table without strategy_id. Access is
        restricted by VIEW_ALL_POSITIONS permission to prevent data leakage.
        We use absolute market value for total portfolio scaling while keeping
        signed weights for long/short exposure direction.

        Args:
            portfolio_id: Portfolio identifier (unused in MVP).
            as_of_date: Date used for CRSP PERMNO mapping.

        Returns:
            DataFrame with columns [permno, symbol, weight], or None if no data.

        Example:
            >>> from datetime import date
            >>> holdings = service._get_portfolio_holdings(\"alpha\", date(2024, 1, 1))
            >>> holdings is None or set(holdings.columns) == {\"permno\", \"symbol\", \"weight\"}
            True
        """

        from pathlib import Path

        from libs.data_providers.crsp_local_provider import CRSPLocalProvider
        from libs.data_quality.exceptions import DataNotFoundError
        from libs.data_quality.manifest import ManifestManager

        async def _fetch() -> pl.DataFrame | None:
            import logging

            if not has_permission(self._user, Permission.VIEW_ALL_POSITIONS):
                logging.getLogger(__name__).warning(
                    "positions_access_denied",
                    extra={
                        "user_id": self._user.get("user_id"),
                        "permission": Permission.VIEW_ALL_POSITIONS.value,
                    },
                )
                return None

            if self._db is None:
                logging.getLogger(__name__).warning("positions_db_unavailable")
                return None

            async with self._db.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        SELECT symbol, qty, avg_entry_price, current_price
                        FROM positions
                        WHERE qty != %s
                        """,
                        (0,),
                    )
                    rows: list[dict[str, Any]] = await cur.fetchall()

            if not rows:
                return None

            data: list[dict[str, Any]] = []
            total_abs_value = 0.0

            for row in rows:
                qty = row["qty"] or 0
                if qty == 0:
                    continue
                current_price = row["current_price"]
                if current_price is None:
                    logging.getLogger(__name__).warning(
                        "position_missing_price",
                        extra={"symbol": row["symbol"]},
                    )
                    continue

                market_value = float(qty) * float(current_price)
                # Use absolute market value for scaling, but preserve sign in weights.
                abs_value = abs(market_value)
                total_abs_value += abs_value
                data.append(
                    {
                        "symbol": row["symbol"],
                        "market_value": market_value,
                        "abs_value": abs_value,
                    }
                )

            if total_abs_value == 0:
                return None

            storage_path = Path("data/wrds/crsp/daily")
            manifest_manager = ManifestManager(Path("data/manifests"))
            crsp = CRSPLocalProvider(storage_path, manifest_manager)

            result: list[dict[str, Any]] = []
            for row_data in data:
                try:
                    permno = crsp.ticker_to_permno(row_data["symbol"], as_of_date)
                    result.append(
                        {
                            "permno": permno,
                            "symbol": row_data["symbol"],
                            "weight": row_data["market_value"] / total_abs_value,
                        }
                    )
                except DataNotFoundError:
                    logging.getLogger(__name__).warning(
                        "permno_mapping_missing",
                        extra={"symbol": row_data["symbol"], "date": as_of_date},
                    )

            if not result:
                return None

            return pl.DataFrame(result)

        return run_async(_fetch())


__all__ = [
    "FactorExposureService",
    "FactorDefinition",
    "ExposureData",
]
