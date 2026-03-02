"""Universe management DTOs (P6T15/T15.1).

Pydantic schemas for universe metadata, constituents, filters,
and custom universe definitions.

Unit Convention:
    - ``market_cap``: Raw CRSP value in **$thousands** (``abs(prc) * shrout``).
    - ``adv_20d``: **$ notional** (``mean(abs(prc) * vol)``, 20-day).
    - Display conversion to human-readable ($B, $M) is component-layer only.
    - Filter thresholds use the same raw units as DTO fields.
"""

from __future__ import annotations

import re
from datetime import date

from pydantic import BaseModel, Field, field_validator

# Re-export shared domain models from neutral data-layer location
from libs.data.schemas.universe import (
    UniverseFilterDTO,
    UniverseMetadata,
    UniverseTypeStr,
)

ALLOWED_FILTER_FIELDS = ("market_cap", "adv_20d")
ALLOWED_FILTER_OPERATORS = ("gt", "lt", "gte", "lte")

# Ticker format: 1-10 chars of A-Z, 0-9, dot, hyphen (e.g. AAPL, BRK.B, BF-B)
_TICKER_RE = re.compile(r"^[A-Z0-9.\-]{1,10}$")


class CustomUniverseDefinitionDTO(BaseModel):
    """Definition for creating a custom universe."""

    name: str = Field(min_length=1, max_length=128)
    base_universe_id: str | None = None  # None for manual list
    filters: list[UniverseFilterDTO] = Field(default_factory=list, max_length=20)
    exclude_symbols: list[str] = Field(default_factory=list, max_length=500)
    manual_symbols: list[str] | None = Field(default=None, max_length=5000)

    @field_validator("exclude_symbols", mode="before")
    @classmethod
    def _validate_exclude_tickers(cls, v: list[str]) -> list[str]:
        if not isinstance(v, list):
            raise ValueError(f"exclude_symbols must be a list, got {type(v).__name__}")
        seen: set[str] = set()
        cleaned: list[str] = []
        for ticker in v:
            if not isinstance(ticker, str):
                raise ValueError(f"Each ticker must be a string, got {type(ticker).__name__}")
            upper = ticker.strip().upper()
            if not upper:
                continue
            if not _TICKER_RE.match(upper):
                raise ValueError(f"Invalid ticker format: '{ticker}'")
            if upper not in seen:
                seen.add(upper)
                cleaned.append(upper)
        return cleaned

    @field_validator("manual_symbols", mode="before")
    @classmethod
    def _validate_manual_tickers(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        if not isinstance(v, list):
            raise ValueError(f"manual_symbols must be a list, got {type(v).__name__}")
        seen: set[str] = set()
        cleaned: list[str] = []
        for ticker in v:
            if not isinstance(ticker, str):
                raise ValueError(f"Each ticker must be a string, got {type(ticker).__name__}")
            upper = ticker.strip().upper()
            if not upper:
                continue
            if not _TICKER_RE.match(upper):
                raise ValueError(f"Invalid ticker format: '{ticker}'")
            if upper not in seen:
                seen.add(upper)
                cleaned.append(upper)
        return cleaned


class UniverseConstituentDTO(BaseModel):
    """Individual security in a universe."""

    permno: int
    ticker: str | None = None
    market_cap: float | None = None  # $thousands (CRSP units)
    adv_20d: float | None = None  # $ notional


class UniverseListItemDTO(BaseModel):
    """Summary item for universe listing."""

    id: str
    name: str
    universe_type: UniverseTypeStr
    symbol_count: int | None = None  # None when CRSP unavailable
    count_is_approximate: bool = False  # True for manual lists (pre-CRSP resolution)
    last_updated: str | None = None
    base: str | None = None  # Base universe for custom, "CRSP" for built-in


class UniverseDetailDTO(BaseModel):
    """Detailed view of a universe including constituents."""

    id: str
    name: str
    universe_type: UniverseTypeStr
    constituents: list[UniverseConstituentDTO] = Field(default_factory=list)
    symbol_count: int = 0
    filters_applied: list[UniverseFilterDTO] = Field(default_factory=list)
    unresolved_tickers: list[str] = Field(default_factory=list)
    as_of_date: date | None = None
    base_universe_id: str | None = None
    exclude_symbols: list[str] = Field(default_factory=list)
    crsp_unavailable: bool = False
    error_message: str | None = None


class UniverseAnalyticsDTO(BaseModel):
    """Analytics summary for a universe (P6T15/T15.2).

    Unit conventions:
        - ``avg_market_cap`` / ``total_market_cap``: $thousands (CRSP).
        - ``median_adv``: $ notional.
        - ``market_cap_distribution``: pre-filtered positive values only (no zero/null).
        - ``adv_distribution``: pre-filtered positive values only (no zero/null).
        - ``sector_distribution``: GICS sector weights summing to ~1.0 (mock v1).
        - ``factor_exposure``: factor loadings (mock v1).
    """

    universe_id: str
    symbol_count: int
    avg_market_cap: float
    median_adv: float
    total_market_cap: float
    market_cap_distribution: list[float] = Field(default_factory=list)
    adv_distribution: list[float] = Field(default_factory=list)
    sector_distribution: dict[str, float] = Field(default_factory=dict)
    factor_exposure: dict[str, float] = Field(default_factory=dict)
    is_sector_mock: bool = True
    is_factor_mock: bool = True
    crsp_unavailable: bool = False
    error_message: str | None = None


class UniverseComparisonDTO(BaseModel):
    """Side-by-side comparison of two universes (P6T15/T15.2).

    ``overlap_pct`` is the percentage of the *smaller* universe
    that overlaps with the larger.
    """

    universe_a_stats: UniverseAnalyticsDTO
    universe_b_stats: UniverseAnalyticsDTO
    overlap_count: int
    overlap_pct: float
    error_message: str | None = None


__all__ = [
    "CustomUniverseDefinitionDTO",
    "UniverseAnalyticsDTO",
    "UniverseComparisonDTO",
    "UniverseConstituentDTO",
    "UniverseDetailDTO",
    "UniverseFilterDTO",
    "UniverseListItemDTO",
    "UniverseMetadata",
    "UniverseTypeStr",
]
