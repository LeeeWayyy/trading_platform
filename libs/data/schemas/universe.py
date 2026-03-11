"""Universe domain models shared across data and service layers (P6T15).

These are neutral domain models that live in the data layer to avoid
circular imports between ``libs/data`` and ``libs/web_console_services``.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

UniverseTypeStr = Literal["built_in", "custom", "unknown"]


class UniverseFilterDTO(BaseModel):
    """Single filter criterion for universe filtering."""

    field: Literal["market_cap", "adv_20d"]
    operator: Literal["gt", "lt", "gte", "lte"]
    value: float

    @field_validator("value")
    @classmethod
    def _value_must_be_finite(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("Filter value must be a finite number")
        return v


class UniverseMetadata(BaseModel):
    """Internal metadata for a universe (built-in or custom)."""

    id: str
    name: str
    universe_type: UniverseTypeStr
    base_universe_id: str | None = None
    filters: list[UniverseFilterDTO] = Field(default_factory=list)
    exclude_symbols: list[str] = Field(default_factory=list)
    manual_symbols: list[str] | None = None
    created_by: str | None = None
    created_at: datetime | None = None


__all__ = [
    "UniverseFilterDTO",
    "UniverseMetadata",
    "UniverseTypeStr",
]
