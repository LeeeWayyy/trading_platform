"""
Schemas for Market Data Service endpoints.

Defines request/response models used by HTTP API routes.
"""

from datetime import date, datetime

from pydantic import BaseModel, Field


class ADVResponse(BaseModel):
    """Average Daily Volume response payload."""

    symbol: str = Field(..., description="Stock symbol")
    adv: int = Field(..., description="20-day average daily volume in shares", ge=0)
    data_date: date = Field(..., description="Date of the ADV calculation")
    source: str = Field(..., description="Data provider (e.g., 'alpaca')")
    cached: bool = Field(..., description="Whether response came from cache")
    cached_at: datetime | None = Field(default=None, description="Timestamp when cached (UTC)")
    stale: bool = Field(..., description="True if data_date is >5 trading days old")
