"""Pydantic DTO definitions for strategy exposure dashboard (P6T15/T15.3).

Notional values use ``float`` (not ``Decimal``) because these DTOs feed
directly into Plotly charts and AG Grid JavaScript, which require JSON-
serialisable numeric types.  Intermediate calculations use ``Decimal``
for precision; final values are rounded to 2 decimal places.
"""

from __future__ import annotations

from pydantic import BaseModel


class StrategyExposureDTO(BaseModel):
    """Per-strategy exposure breakdown."""

    strategy: str
    long_notional: float
    short_notional: float
    gross_notional: float
    net_notional: float
    net_pct: float
    position_count: int
    missing_price_count: int = 0
    fallback_price_count: int = 0


class TotalExposureDTO(BaseModel):
    """Aggregate exposure across all strategies."""

    long_total: float
    short_total: float
    gross_total: float
    net_total: float
    net_pct: float
    strategy_count: int
    bias_warning: str | None = None
    bias_severity: str | None = None
    is_placeholder: bool = False
    is_partial: bool = False
    data_quality_warning: str | None = None
    missing_price_count: int = 0
    fallback_price_count: int = 0
    unmapped_position_count: int = 0


__all__ = [
    "StrategyExposureDTO",
    "TotalExposureDTO",
]
