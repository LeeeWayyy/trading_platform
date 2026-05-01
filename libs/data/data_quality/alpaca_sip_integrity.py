"""Alpaca SIP deterministic re-pull integrity checks.

This module verifies that a bounded historical request can be pulled twice with
the same canonical row hashes. It is intentionally a Phase 0 spike tool: it
does not write training data and does not replace manifest-backed bulk sync.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol, cast

from libs.data.data_quality.alpaca_feed_delta import (
    NormalizedFeedBar,
    normalize_feed_bars_response,
    normalize_symbols,
)

try:
    from alpaca.common.enums import Sort
    from alpaca.data.enums import Adjustment, DataFeed
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    ALPACA_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency guard
    ALPACA_AVAILABLE = False
    Sort = None  # type: ignore[assignment,misc]
    Adjustment = None  # type: ignore[assignment,misc]
    DataFeed = None  # type: ignore[assignment,misc]
    StockHistoricalDataClient = None  # type: ignore[assignment,misc]
    StockBarsRequest = None  # type: ignore[assignment,misc]
    TimeFrame = None  # type: ignore[assignment,misc]
    TimeFrameUnit = None  # type: ignore[assignment,misc]


IntegrityStatus = Literal["passed", "warning", "failed"]


class AlpacaBarsClient(Protocol):
    """Minimal Alpaca historical bars client interface."""

    def get_stock_bars(self, request_params: Any) -> Any:
        """Return historical stock bars for a request."""


@dataclass(frozen=True)
class SIPIntegrityMismatch:
    """One sampled mismatch between two same-window SIP pulls."""

    key: str
    first_hash: str | None
    second_hash: str | None
    reason: str

    def to_dict(self) -> dict[str, str | None]:
        """Serialize mismatch to stable JSON-compatible values."""
        return {
            "key": self.key,
            "first_hash": self.first_hash,
            "second_hash": self.second_hash,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class SIPIntegrityReport:
    """Deterministic SIP re-pull integrity report."""

    symbols: tuple[str, ...]
    start: datetime.datetime
    end: datetime.datetime
    timeframe: str
    adjustment_mode: str
    feed: str
    first_row_count: int
    second_row_count: int
    matched_row_count: int
    first_aggregate_hash: str
    second_aggregate_hash: str
    mismatch_count: int
    duplicate_count: int
    status: IntegrityStatus
    mismatches: tuple[SIPIntegrityMismatch, ...]

    @property
    def content_hash(self) -> str:
        """Return a deterministic SHA-256 hash of the report payload."""
        payload = self.to_dict(include_hash=False)
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    def to_dict(self, *, include_hash: bool = True) -> dict[str, object]:
        """Serialize report to stable JSON-compatible values."""
        payload: dict[str, object] = {
            "report_type": "alpaca_sip_integrity",
            "symbols": list(self.symbols),
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "timeframe": self.timeframe,
            "adjustment_mode": self.adjustment_mode,
            "feed": self.feed,
            "first_row_count": self.first_row_count,
            "second_row_count": self.second_row_count,
            "matched_row_count": self.matched_row_count,
            "first_aggregate_hash": self.first_aggregate_hash,
            "second_aggregate_hash": self.second_aggregate_hash,
            "mismatch_count": self.mismatch_count,
            "duplicate_count": self.duplicate_count,
            "status": self.status,
            "mismatches": [mismatch.to_dict() for mismatch in self.mismatches],
        }
        if include_hash:
            payload["content_hash"] = self.content_hash
        return payload


class AlpacaSIPIntegrityChecker:
    """Run deterministic same-window re-pull checks against Alpaca SIP."""

    def __init__(self, client: AlpacaBarsClient) -> None:
        self.client = client

    @classmethod
    def from_env(cls) -> AlpacaSIPIntegrityChecker:
        """Build a checker from standard Alpaca market-data credentials."""
        if not ALPACA_AVAILABLE:
            raise ImportError("alpaca-py package is required for Alpaca SIP integrity checks")

        api_key = os.getenv("ALPACA_API_KEY_ID") or os.getenv("ALPACA_API_KEY")
        secret_key = os.getenv("ALPACA_API_SECRET_KEY") or os.getenv("ALPACA_SECRET_KEY")
        if not api_key or not secret_key:
            raise ValueError(
                "Alpaca credentials required: set ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY"
            )
        client = cast(Any, StockHistoricalDataClient)(api_key=api_key, secret_key=secret_key)
        return cls(client=client)

    def run(
        self,
        *,
        symbols: Sequence[str],
        start: datetime.datetime,
        end: datetime.datetime,
        timeframe: str = "1Day",
        adjustment_mode: str = "all",
        feed: str = "sip",
        max_mismatch_samples: int = 100,
    ) -> SIPIntegrityReport:
        """Pull the same window twice and compare canonical row hashes."""
        normalized_symbols = normalize_symbols(symbols)
        first_response = self._fetch_bars(
            symbols=normalized_symbols,
            start=start,
            end=end,
            timeframe=timeframe,
            adjustment_mode=adjustment_mode,
            feed=feed,
        )
        second_response = self._fetch_bars(
            symbols=normalized_symbols,
            start=start,
            end=end,
            timeframe=timeframe,
            adjustment_mode=adjustment_mode,
            feed=feed,
        )
        return compare_sip_integrity_responses(
            first_response=first_response,
            second_response=second_response,
            symbols=normalized_symbols,
            start=start,
            end=end,
            timeframe=timeframe,
            adjustment_mode=adjustment_mode,
            feed=feed,
            max_mismatch_samples=max_mismatch_samples,
        )

    def _fetch_bars(
        self,
        *,
        symbols: Sequence[str],
        start: datetime.datetime,
        end: datetime.datetime,
        timeframe: str,
        adjustment_mode: str,
        feed: str,
    ) -> Any:
        if not ALPACA_AVAILABLE:
            raise ImportError("alpaca-py package is required for Alpaca SIP integrity checks")

        request = cast(Any, StockBarsRequest)(
            symbol_or_symbols=list(symbols),
            timeframe=_resolve_timeframe(timeframe),
            start=_ensure_utc(start),
            end=_ensure_utc(end),
            sort=cast(Any, Sort).ASC,
            adjustment=cast(Any, Adjustment)(adjustment_mode.lower().strip()),
            feed=cast(Any, DataFeed)(feed.lower().strip()),
        )
        return self.client.get_stock_bars(request)


def compare_sip_integrity_responses(
    *,
    first_response: Any,
    second_response: Any,
    symbols: Sequence[str],
    start: datetime.datetime,
    end: datetime.datetime,
    timeframe: str,
    adjustment_mode: str,
    feed: str = "sip",
    max_mismatch_samples: int = 100,
) -> SIPIntegrityReport:
    """Compare two Alpaca SIP response payloads for deterministic content."""
    first_bars = normalize_feed_bars_response(first_response)
    second_bars = normalize_feed_bars_response(second_response)
    first_hashes, first_duplicates = _row_hashes(
        first_bars,
        timeframe=timeframe,
        adjustment_mode=adjustment_mode,
        feed=feed,
    )
    second_hashes, second_duplicates = _row_hashes(
        second_bars,
        timeframe=timeframe,
        adjustment_mode=adjustment_mode,
        feed=feed,
    )
    first_aggregate = _aggregate_hash(first_hashes)
    second_aggregate = _aggregate_hash(second_hashes)

    mismatches = _find_mismatches(
        first_hashes,
        second_hashes,
        max_mismatch_samples=max_mismatch_samples,
    )
    duplicate_count = first_duplicates + second_duplicates
    mismatch_count = _mismatch_count(first_hashes, second_hashes)
    matched_row_count = len(set(first_hashes).intersection(second_hashes))
    if not first_hashes or not second_hashes or mismatch_count:
        status: IntegrityStatus = "failed"
    elif duplicate_count:
        status = "warning"
    else:
        status = "passed"

    return SIPIntegrityReport(
        symbols=tuple(sorted(normalize_symbols(symbols))),
        start=_ensure_utc(start),
        end=_ensure_utc(end),
        timeframe=timeframe,
        adjustment_mode=adjustment_mode.lower().strip(),
        feed=feed.lower().strip(),
        first_row_count=len(first_bars),
        second_row_count=len(second_bars),
        matched_row_count=matched_row_count,
        first_aggregate_hash=first_aggregate,
        second_aggregate_hash=second_aggregate,
        mismatch_count=mismatch_count,
        duplicate_count=duplicate_count,
        status=status,
        mismatches=tuple(mismatches),
    )


def _row_hashes(
    bars: Sequence[NormalizedFeedBar],
    *,
    timeframe: str,
    adjustment_mode: str,
    feed: str,
) -> tuple[dict[str, str], int]:
    hashes: dict[str, str] = {}
    duplicate_count = 0
    for bar in sorted(bars, key=lambda item: item.key):
        key = _bar_key(bar, timeframe=timeframe, adjustment_mode=adjustment_mode)
        if key in hashes:
            duplicate_count += 1
        payload = {
            "symbol": bar.symbol,
            "timestamp": bar.timestamp.isoformat(),
            "timeframe": timeframe,
            "adjustment_mode": adjustment_mode.lower().strip(),
            "feed": feed.lower().strip(),
            "open": round(bar.open, 10),
            "high": round(bar.high, 10),
            "low": round(bar.low, 10),
            "close": round(bar.close, 10),
            "volume": round(bar.volume, 10),
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        hashes[key] = hashlib.sha256(encoded).hexdigest()
    return hashes, duplicate_count


def _bar_key(
    bar: NormalizedFeedBar,
    *,
    timeframe: str,
    adjustment_mode: str,
) -> str:
    return "|".join(
        [
            bar.symbol,
            bar.timestamp.isoformat(),
            timeframe,
            adjustment_mode.lower().strip(),
        ]
    )


def _aggregate_hash(row_hashes: dict[str, str]) -> str:
    encoded = json.dumps(row_hashes, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _mismatch_count(first_hashes: dict[str, str], second_hashes: dict[str, str]) -> int:
    keys = set(first_hashes).union(second_hashes)
    return sum(1 for key in keys if first_hashes.get(key) != second_hashes.get(key))


def _find_mismatches(
    first_hashes: dict[str, str],
    second_hashes: dict[str, str],
    *,
    max_mismatch_samples: int,
) -> list[SIPIntegrityMismatch]:
    mismatches: list[SIPIntegrityMismatch] = []
    for key in sorted(set(first_hashes).union(second_hashes)):
        first = first_hashes.get(key)
        second = second_hashes.get(key)
        if first == second:
            continue
        if first is None:
            reason = "missing_first_pull"
        elif second is None:
            reason = "missing_second_pull"
        else:
            reason = "hash_changed"
        if len(mismatches) < max_mismatch_samples:
            mismatches.append(
                SIPIntegrityMismatch(
                    key=key,
                    first_hash=first,
                    second_hash=second,
                    reason=reason,
                )
            )
    return mismatches


def _ensure_utc(value: datetime.datetime) -> datetime.datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=datetime.UTC)
    return value.astimezone(datetime.UTC)


def _resolve_timeframe(timeframe: str) -> Any:
    if not ALPACA_AVAILABLE:
        raise ImportError("alpaca-py package is required for Alpaca SIP integrity checks")
    normalized = timeframe.strip().lower()
    mapping: dict[str, Any] = {
        "1min": cast(Any, TimeFrame)(1, cast(Any, TimeFrameUnit).Minute),
        "5min": cast(Any, TimeFrame)(5, cast(Any, TimeFrameUnit).Minute),
        "15min": cast(Any, TimeFrame)(15, cast(Any, TimeFrameUnit).Minute),
        "1hour": cast(Any, TimeFrame)(1, cast(Any, TimeFrameUnit).Hour),
        "1day": cast(Any, TimeFrame).Day,
    }
    resolved = mapping.get(normalized)
    if resolved is None:
        raise ValueError(f"Unsupported timeframe: {timeframe}")
    return resolved
