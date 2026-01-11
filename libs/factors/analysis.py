"""Factor analysis and portfolio exposure aggregation."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

import polars as pl

if TYPE_CHECKING:
    from libs.factors.factor_builder import FactorBuilder

logger = logging.getLogger(__name__)


@dataclass
class PortfolioExposureResult:
    """Portfolio-level factor exposures."""

    date: date
    exposures: pl.DataFrame  # [factor, exposure]
    stock_exposures: pl.DataFrame  # [permno, factor, exposure, weight, contribution]
    coverage: pl.DataFrame  # [factor, coverage_pct]


class FactorAnalysisService:
    """Service for advanced factor analysis and portfolio aggregation."""

    def __init__(self, builder: FactorBuilder):
        self.builder = builder

    def compute_portfolio_exposure(
        self,
        portfolio_weights: pl.DataFrame,
        factor_names: list[str],
        as_of_date: date,
    ) -> PortfolioExposureResult:
        """Compute portfolio exposures for multiple factors.

        Args:
            portfolio_weights: DataFrame with permno (int) and weight (float) columns.
            factor_names: List of factors to compute.
            as_of_date: Date for computation.

        Returns:
            PortfolioExposureResult with aggregated and stock-level exposures.
        """
        if portfolio_weights.is_empty():
            return self._empty_result(as_of_date)

        universe = portfolio_weights["permno"].unique().to_list()

        stock_exposures_list = []

        for factor_name in factor_names:
            try:
                result = self.builder.compute_factor(factor_name, as_of_date, universe=universe)

                # Use z-score as the standard exposure metric
                if "zscore" in result.exposures.columns:
                    exp_df = result.exposures.select(
                        pl.col("permno"),
                        pl.lit(factor_name).alias("factor"),
                        pl.col("zscore").alias("exposure"),
                    )
                    stock_exposures_list.append(exp_df)
                else:
                    logger.warning(
                        "Factor result missing zscore column, skipping factor",
                        extra={"factor": factor_name, "as_of_date": str(as_of_date)},
                    )
            except (KeyError, ValueError) as e:
                logger.error(
                    "Factor computation failed - data error",
                    extra={
                        "factor": factor_name,
                        "as_of_date": str(as_of_date),
                        "error": str(e),
                    },
                    exc_info=True,
                )
            except (pl.exceptions.ComputeError, pl.exceptions.ColumnNotFoundError) as e:
                logger.error(
                    "Factor computation failed - Polars computation error",
                    extra={
                        "factor": factor_name,
                        "as_of_date": str(as_of_date),
                        "error": str(e),
                    },
                    exc_info=True,
                )

        if not stock_exposures_list:
            return self._empty_result(as_of_date)

        all_stock_exposures = pl.concat(stock_exposures_list)

        # Join with weights to compute contribution
        # Left join on portfolio weights ensures we track missing coverage
        joined = all_stock_exposures.join(portfolio_weights, on="permno", how="inner")

        joined = joined.with_columns((pl.col("exposure") * pl.col("weight")).alias("contribution"))

        # Aggregated exposure per factor (sum of contributions)
        agg_exposures = joined.group_by("factor").agg(
            pl.col("contribution").sum().alias("exposure")
        )

        # Compute coverage per factor (sum of weights of stocks with valid exposure)
        coverage = joined.group_by("factor").agg(pl.col("weight").sum().alias("coverage_pct"))

        return PortfolioExposureResult(
            date=as_of_date, exposures=agg_exposures, stock_exposures=joined, coverage=coverage
        )

    def _empty_result(self, as_of_date: date) -> PortfolioExposureResult:
        return PortfolioExposureResult(
            date=as_of_date,
            exposures=pl.DataFrame(schema={"factor": pl.Utf8, "exposure": pl.Float64}),
            stock_exposures=pl.DataFrame(
                schema={
                    "permno": pl.Int64,
                    "factor": pl.Utf8,
                    "exposure": pl.Float64,
                    "weight": pl.Float64,
                    "contribution": pl.Float64,
                }
            ),
            coverage=pl.DataFrame(schema={"factor": pl.Utf8, "coverage_pct": pl.Float64}),
        )
