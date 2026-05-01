"""Alpaca feed delta monitoring for IEX-vs-SIP historical bars.

The comparator is intentionally a monitor, not a parity assertion. IEX is a
venue-specific feed while SIP is consolidated tape data, so volume and
occasionally OHLC values can legitimately differ. This module records those
differences with fixed, versioned tolerances for Phase 0 SIP validation.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import math
import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol, cast

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


FeedDeltaStatus = Literal["passed", "warning", "failed"]
IssueSeverity = Literal["warning", "error"]

OHLC_FIELDS: tuple[str, ...] = ("open", "high", "low", "close")


class AlpacaHistoricalBarsClient(Protocol):
    """Minimal Alpaca client interface needed by the feed comparator."""

    def get_stock_bars(self, request_params: Any) -> Any:
        """Return bars for a stock-bars request."""


@dataclass(frozen=True)
class NormalizedFeedBar:
    """Canonical in-memory bar used for cross-feed comparison."""

    symbol: str
    timestamp: datetime.datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    @property
    def key(self) -> tuple[str, datetime.datetime]:
        """Return the matching key for IEX/SIP comparison."""
        return (self.symbol, self.timestamp)

    @property
    def date(self) -> datetime.date:
        """Return the UTC session date implied by the bar timestamp."""
        return self.timestamp.date()

    def value_for_field(self, field: str) -> float:
        """Return an OHLC value by name."""
        if field == "open":
            return self.open
        if field == "high":
            return self.high
        if field == "low":
            return self.low
        if field == "close":
            return self.close
        raise ValueError(f"Unsupported OHLC field: {field}")


@dataclass(frozen=True)
class LiquidityBucketTolerance:
    """Volume-ratio tolerance for one SIP liquidity bucket."""

    name: str
    min_daily_volume: float
    min_left_to_right_volume_ratio: float
    max_left_to_right_volume_ratio: float

    def to_dict(self) -> dict[str, str | float]:
        """Serialize tolerance to stable JSON-compatible values."""
        return {
            "name": self.name,
            "min_daily_volume": self.min_daily_volume,
            "min_left_to_right_volume_ratio": self.min_left_to_right_volume_ratio,
            "max_left_to_right_volume_ratio": self.max_left_to_right_volume_ratio,
        }


@dataclass(frozen=True)
class AlpacaFeedDeltaTolerances:
    """Pinned Phase 0 tolerance bands for Alpaca IEX-vs-SIP monitoring."""

    version: str = "alpaca-iex-sip-delta-v1"
    max_ohlc_relative_delta: float = 0.02
    min_ohlc_absolute_delta: float = 0.02
    max_issue_samples: int = 200
    buckets: tuple[LiquidityBucketTolerance, ...] = (
        LiquidityBucketTolerance(
            name="liquid",
            min_daily_volume=1_000_000.0,
            min_left_to_right_volume_ratio=0.001,
            max_left_to_right_volume_ratio=0.30,
        ),
        LiquidityBucketTolerance(
            name="mid",
            min_daily_volume=100_000.0,
            min_left_to_right_volume_ratio=0.0,
            max_left_to_right_volume_ratio=0.60,
        ),
        LiquidityBucketTolerance(
            name="thin",
            min_daily_volume=0.0,
            min_left_to_right_volume_ratio=0.0,
            max_left_to_right_volume_ratio=2.00,
        ),
    )

    def bucket_for_daily_volume(self, daily_volume: float) -> LiquidityBucketTolerance:
        """Resolve the configured liquidity bucket for an average daily volume."""
        for bucket in sorted(self.buckets, key=lambda item: item.min_daily_volume, reverse=True):
            if daily_volume >= bucket.min_daily_volume:
                return bucket
        return self.buckets[-1]

    def to_dict(self) -> dict[str, object]:
        """Serialize tolerances to stable JSON-compatible values."""
        return {
            "version": self.version,
            "max_ohlc_relative_delta": self.max_ohlc_relative_delta,
            "min_ohlc_absolute_delta": self.min_ohlc_absolute_delta,
            "max_issue_samples": self.max_issue_samples,
            "buckets": [bucket.to_dict() for bucket in self.buckets],
        }


@dataclass(frozen=True)
class FeedDeltaIssue:
    """One sampled issue from a feed delta report."""

    category: str
    severity: IssueSeverity
    symbol: str
    timestamp: str | None
    message: str
    details: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        """Serialize issue to stable JSON-compatible values."""
        return {
            "category": self.category,
            "severity": self.severity,
            "symbol": self.symbol,
            "timestamp": self.timestamp,
            "message": self.message,
            "details": _stable_object(self.details),
        }


@dataclass(frozen=True)
class FeedDeltaSymbolSummary:
    """Per-symbol summary included in a feed delta report."""

    symbol: str
    left_bar_count: int
    right_bar_count: int
    matched_bar_count: int
    missing_left_count: int
    missing_right_count: int
    timestamp_alignment_issue_count: int
    ohlc_sanity_issue_count: int
    price_delta_issue_count: int
    volume_ratio_issue_count: int
    avg_right_daily_volume: float
    liquidity_bucket: str

    def to_dict(self) -> dict[str, object]:
        """Serialize summary to stable JSON-compatible values."""
        return {
            "symbol": self.symbol,
            "left_bar_count": self.left_bar_count,
            "right_bar_count": self.right_bar_count,
            "matched_bar_count": self.matched_bar_count,
            "missing_left_count": self.missing_left_count,
            "missing_right_count": self.missing_right_count,
            "timestamp_alignment_issue_count": self.timestamp_alignment_issue_count,
            "ohlc_sanity_issue_count": self.ohlc_sanity_issue_count,
            "price_delta_issue_count": self.price_delta_issue_count,
            "volume_ratio_issue_count": self.volume_ratio_issue_count,
            "avg_right_daily_volume": round(self.avg_right_daily_volume, 6),
            "liquidity_bucket": self.liquidity_bucket,
        }


@dataclass(frozen=True)
class FeedDeltaReport:
    """Deterministic IEX-vs-SIP delta report."""

    symbols: tuple[str, ...]
    start: datetime.datetime
    end: datetime.datetime
    timeframe: str
    adjustment_mode: str
    left_feed: str
    right_feed: str
    tolerances: AlpacaFeedDeltaTolerances
    status: FeedDeltaStatus
    issue_counts: dict[str, int]
    summary: dict[str, object]
    symbol_summaries: tuple[FeedDeltaSymbolSummary, ...]
    issues: tuple[FeedDeltaIssue, ...]

    @property
    def content_hash(self) -> str:
        """Return a deterministic SHA-256 hash of the report payload."""
        payload = self.to_dict(include_hash=False)
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    def to_dict(self, *, include_hash: bool = True) -> dict[str, object]:
        """Serialize report to stable JSON-compatible values."""
        payload: dict[str, object] = {
            "report_type": "alpaca_feed_delta",
            "symbols": list(self.symbols),
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "timeframe": self.timeframe,
            "adjustment_mode": self.adjustment_mode,
            "left_feed": self.left_feed,
            "right_feed": self.right_feed,
            "tolerances": self.tolerances.to_dict(),
            "status": self.status,
            "issue_counts": dict(sorted(self.issue_counts.items())),
            "summary": _stable_object(self.summary),
            "symbol_summaries": [summary.to_dict() for summary in self.symbol_summaries],
            "issues": [issue.to_dict() for issue in self.issues],
        }
        if include_hash:
            payload["content_hash"] = self.content_hash
        return payload


@dataclass
class _MutableSymbolSummary:
    symbol: str
    left_bar_count: int = 0
    right_bar_count: int = 0
    matched_bar_count: int = 0
    missing_left_count: int = 0
    missing_right_count: int = 0
    timestamp_alignment_issue_count: int = 0
    ohlc_sanity_issue_count: int = 0
    price_delta_issue_count: int = 0
    volume_ratio_issue_count: int = 0
    avg_right_daily_volume: float = 0.0
    liquidity_bucket: str = "thin"

    def freeze(self) -> FeedDeltaSymbolSummary:
        """Convert mutable counters to the public immutable report shape."""
        return FeedDeltaSymbolSummary(
            symbol=self.symbol,
            left_bar_count=self.left_bar_count,
            right_bar_count=self.right_bar_count,
            matched_bar_count=self.matched_bar_count,
            missing_left_count=self.missing_left_count,
            missing_right_count=self.missing_right_count,
            timestamp_alignment_issue_count=self.timestamp_alignment_issue_count,
            ohlc_sanity_issue_count=self.ohlc_sanity_issue_count,
            price_delta_issue_count=self.price_delta_issue_count,
            volume_ratio_issue_count=self.volume_ratio_issue_count,
            avg_right_daily_volume=self.avg_right_daily_volume,
            liquidity_bucket=self.liquidity_bucket,
        )


class AlpacaFeedDeltaComparator:
    """Fetch two Alpaca feeds and produce a deterministic delta report."""

    def __init__(self, client: AlpacaHistoricalBarsClient) -> None:
        self.client = client

    @classmethod
    def from_env(cls) -> AlpacaFeedDeltaComparator:
        """Build a comparator from standard Alpaca market-data credentials."""
        if not ALPACA_AVAILABLE:
            raise ImportError("alpaca-py package is required for Alpaca feed delta checks")

        api_key = os.getenv("ALPACA_API_KEY_ID") or os.getenv("ALPACA_API_KEY")
        secret_key = os.getenv("ALPACA_API_SECRET_KEY") or os.getenv("ALPACA_SECRET_KEY")
        if not api_key or not secret_key:
            raise ValueError(
                "Alpaca credentials required: set ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY"
            )

        client = cast(Any, StockHistoricalDataClient)(api_key=api_key, secret_key=secret_key)
        return cls(client=client)

    def compare(
        self,
        *,
        symbols: Sequence[str],
        start: datetime.datetime,
        end: datetime.datetime,
        timeframe: str = "5Min",
        adjustment_mode: str = "all",
        left_feed: str = "iex",
        right_feed: str = "sip",
        tolerances: AlpacaFeedDeltaTolerances | None = None,
    ) -> FeedDeltaReport:
        """Fetch both feeds and compare them."""
        normalized_symbols = normalize_symbols(symbols)
        left_response = self._fetch_feed_bars(
            symbols=normalized_symbols,
            start=start,
            end=end,
            timeframe=timeframe,
            adjustment_mode=adjustment_mode,
            feed=left_feed,
        )
        right_response = self._fetch_feed_bars(
            symbols=normalized_symbols,
            start=start,
            end=end,
            timeframe=timeframe,
            adjustment_mode=adjustment_mode,
            feed=right_feed,
        )
        return compare_feed_bar_responses(
            left_response=left_response,
            right_response=right_response,
            symbols=normalized_symbols,
            start=start,
            end=end,
            timeframe=timeframe,
            adjustment_mode=adjustment_mode,
            left_feed=left_feed,
            right_feed=right_feed,
            tolerances=tolerances,
        )

    def _fetch_feed_bars(
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
            raise ImportError("alpaca-py package is required for Alpaca feed delta checks")

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


def compare_feed_bar_responses(
    *,
    left_response: Any,
    right_response: Any,
    symbols: Sequence[str],
    start: datetime.datetime,
    end: datetime.datetime,
    timeframe: str,
    adjustment_mode: str,
    left_feed: str = "iex",
    right_feed: str = "sip",
    tolerances: AlpacaFeedDeltaTolerances | None = None,
) -> FeedDeltaReport:
    """Normalize two Alpaca response payloads and compare their bars."""
    return compare_normalized_feed_bars(
        left_bars=normalize_feed_bars_response(left_response),
        right_bars=normalize_feed_bars_response(right_response),
        symbols=symbols,
        start=start,
        end=end,
        timeframe=timeframe,
        adjustment_mode=adjustment_mode,
        left_feed=left_feed,
        right_feed=right_feed,
        tolerances=tolerances,
    )


def compare_normalized_feed_bars(
    *,
    left_bars: Sequence[NormalizedFeedBar],
    right_bars: Sequence[NormalizedFeedBar],
    symbols: Sequence[str],
    start: datetime.datetime,
    end: datetime.datetime,
    timeframe: str,
    adjustment_mode: str,
    left_feed: str = "iex",
    right_feed: str = "sip",
    tolerances: AlpacaFeedDeltaTolerances | None = None,
) -> FeedDeltaReport:
    """Compare normalized bars from two feeds."""
    resolved_tolerances = tolerances or AlpacaFeedDeltaTolerances()
    normalized_symbols = normalize_symbols(symbols)
    left_index, left_duplicates = _index_bars(left_bars)
    right_index, right_duplicates = _index_bars(right_bars)

    issue_counts: dict[str, int] = {
        "coverage_gap": 0,
        "duplicate_bar": 0,
        "ohlc_sanity": 0,
        "price_delta": 0,
        "timestamp_alignment": 0,
        "volume_ratio": 0,
    }
    issues: list[FeedDeltaIssue] = []
    summaries = {
        symbol: _MutableSymbolSummary(symbol=symbol) for symbol in sorted(normalized_symbols)
    }

    def summary_for(symbol: str) -> _MutableSymbolSummary:
        normalized = symbol.upper().strip()
        if normalized not in summaries:
            summaries[normalized] = _MutableSymbolSummary(symbol=normalized)
        return summaries[normalized]

    def add_issue(
        *,
        category: str,
        severity: IssueSeverity,
        symbol: str,
        timestamp: datetime.datetime | None,
        message: str,
        details: Mapping[str, object],
    ) -> None:
        issue_counts[category] = issue_counts.get(category, 0) + 1
        if len(issues) < resolved_tolerances.max_issue_samples:
            issues.append(
                FeedDeltaIssue(
                    category=category,
                    severity=severity,
                    symbol=symbol,
                    timestamp=timestamp.isoformat() if timestamp is not None else None,
                    message=message,
                    details=dict(details),
                )
            )

    for bar in left_bars:
        summary_for(bar.symbol).left_bar_count += 1
    for bar in right_bars:
        summary_for(bar.symbol).right_bar_count += 1

    for duplicate in left_duplicates:
        add_issue(
            category="duplicate_bar",
            severity="warning",
            symbol=duplicate.symbol,
            timestamp=duplicate.timestamp,
            message=f"Duplicate {left_feed} bar; last response row was used",
            details={"feed": left_feed},
        )
    for duplicate in right_duplicates:
        add_issue(
            category="duplicate_bar",
            severity="warning",
            symbol=duplicate.symbol,
            timestamp=duplicate.timestamp,
            message=f"Duplicate {right_feed} bar; last response row was used",
            details={"feed": right_feed},
        )

    right_daily_volume = _average_daily_volume_by_symbol(right_index.values())
    for symbol, summary in summaries.items():
        daily_volume = right_daily_volume.get(symbol, 0.0)
        bucket = resolved_tolerances.bucket_for_daily_volume(daily_volume)
        summary.avg_right_daily_volume = daily_volume
        summary.liquidity_bucket = bucket.name

    for feed_name, bars in ((left_feed, left_index.values()), (right_feed, right_index.values())):
        for bar in sorted(bars, key=lambda item: item.key):
            problem = _ohlc_sanity_problem(bar)
            if problem is None:
                continue
            summary_for(bar.symbol).ohlc_sanity_issue_count += 1
            add_issue(
                category="ohlc_sanity",
                severity="error",
                symbol=bar.symbol,
                timestamp=bar.timestamp,
                message=f"{feed_name} bar failed OHLC sanity check: {problem}",
                details={
                    "feed": feed_name,
                    "open": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "volume": bar.volume,
                },
            )

    left_keys = set(left_index)
    right_keys = set(right_index)
    matched_keys = sorted(left_keys & right_keys)
    left_symbol_dates = {(symbol, timestamp.date()) for symbol, timestamp in left_keys}
    right_symbol_dates = {(symbol, timestamp.date()) for symbol, timestamp in right_keys}

    for key in sorted(right_keys - left_keys):
        symbol, timestamp = key
        summary = summary_for(symbol)
        if (symbol, timestamp.date()) in left_symbol_dates:
            summary.timestamp_alignment_issue_count += 1
            category = "timestamp_alignment"
            message = f"{left_feed} missing exact timestamp while same session exists"
        else:
            summary.missing_left_count += 1
            category = "coverage_gap"
            message = f"{left_feed} missing bar"
        add_issue(
            category=category,
            severity="warning",
            symbol=symbol,
            timestamp=timestamp,
            message=message,
            details={"missing_feed": left_feed, "present_feed": right_feed},
        )

    for key in sorted(left_keys - right_keys):
        symbol, timestamp = key
        summary = summary_for(symbol)
        if (symbol, timestamp.date()) in right_symbol_dates:
            summary.timestamp_alignment_issue_count += 1
            category = "timestamp_alignment"
            message = f"{right_feed} missing exact timestamp while same session exists"
        else:
            summary.missing_right_count += 1
            category = "coverage_gap"
            message = f"{right_feed} missing bar"
        add_issue(
            category=category,
            severity="warning",
            symbol=symbol,
            timestamp=timestamp,
            message=message,
            details={"missing_feed": right_feed, "present_feed": left_feed},
        )

    for key in matched_keys:
        left = left_index[key]
        right = right_index[key]
        summary = summary_for(left.symbol)
        summary.matched_bar_count += 1

        price_problem = _price_delta_problem(left, right, resolved_tolerances)
        if price_problem is not None:
            summary.price_delta_issue_count += 1
            add_issue(
                category="price_delta",
                severity="warning",
                symbol=left.symbol,
                timestamp=left.timestamp,
                message="OHLC delta exceeded pinned tolerance",
                details=price_problem,
            )

        volume_problem = _volume_ratio_problem(
            left=left,
            right=right,
            bucket=resolved_tolerances.bucket_for_daily_volume(
                right_daily_volume.get(left.symbol, 0.0)
            ),
            left_feed=left_feed,
            right_feed=right_feed,
        )
        if volume_problem is not None:
            summary.volume_ratio_issue_count += 1
            add_issue(
                category="volume_ratio",
                severity="warning",
                symbol=left.symbol,
                timestamp=left.timestamp,
                message="Volume ratio exceeded pinned liquidity-bucket tolerance",
                details=volume_problem,
            )

    issue_total = sum(issue_counts.values())
    left_count = len(left_index)
    right_count = len(right_index)
    matched_count = len(matched_keys)
    status: FeedDeltaStatus
    if left_count == 0 or right_count == 0 or matched_count == 0 or issue_counts["ohlc_sanity"]:
        status = "failed"
    elif issue_total:
        status = "warning"
    else:
        status = "passed"

    summary_payload: dict[str, object] = {
        "left_bar_count": left_count,
        "right_bar_count": right_count,
        "matched_bar_count": matched_count,
        "sampled_issue_count": len(issues),
        "total_issue_count": issue_total,
        "issue_sample_limit": resolved_tolerances.max_issue_samples,
    }

    return FeedDeltaReport(
        symbols=tuple(sorted(normalized_symbols)),
        start=_ensure_utc(start),
        end=_ensure_utc(end),
        timeframe=timeframe,
        adjustment_mode=adjustment_mode.lower().strip(),
        left_feed=left_feed.lower().strip(),
        right_feed=right_feed.lower().strip(),
        tolerances=resolved_tolerances,
        status=status,
        issue_counts=issue_counts,
        summary=summary_payload,
        symbol_summaries=tuple(summary.freeze() for summary in summaries.values()),
        issues=tuple(issues),
    )


def normalize_feed_bars_response(response: Any) -> list[NormalizedFeedBar]:
    """Normalize Alpaca SDK/test-double response shapes into bars."""
    data = getattr(response, "data", None)
    if data is None and isinstance(response, Mapping):
        data = response
    if not isinstance(data, Mapping):
        raise ValueError(f"Unexpected Alpaca bars response type: {type(response).__name__}")

    bars_out: list[NormalizedFeedBar] = []
    for fallback_symbol, bars in data.items():
        if bars is None:
            continue
        if not isinstance(bars, Iterable) or isinstance(bars, str | bytes):
            raise ValueError(f"Unexpected bars collection for symbol {fallback_symbol!r}")
        for bar in bars:
            bars_out.append(_normalize_bar(str(fallback_symbol), bar))
    return bars_out


def normalize_symbols(symbols: Sequence[str]) -> list[str]:
    """Normalize and de-duplicate symbols while preserving first-seen order."""
    normalized: list[str] = []
    seen: set[str] = set()
    for symbol in symbols:
        value = symbol.upper().strip()
        if not value or value in seen:
            continue
        normalized.append(value)
        seen.add(value)
    if not normalized:
        raise ValueError("symbols must contain at least one non-empty symbol")
    return normalized


def _normalize_bar(fallback_symbol: str, bar: Any) -> NormalizedFeedBar:
    symbol = _get_bar_field(bar, "symbol", "S") or fallback_symbol
    timestamp = _normalize_timestamp(_get_bar_field(bar, "timestamp", "t"))
    return NormalizedFeedBar(
        symbol=str(symbol).upper().strip(),
        timestamp=timestamp,
        open=_required_float(bar, "open", "o"),
        high=_required_float(bar, "high", "h"),
        low=_required_float(bar, "low", "l"),
        close=_required_float(bar, "close", "c"),
        volume=_required_float(bar, "volume", "v"),
    )


def _get_bar_field(bar: Any, primary_name: str, fallback_name: str) -> Any:
    if isinstance(bar, Mapping):
        value = bar.get(primary_name)
        return value if value is not None else bar.get(fallback_name)
    value = getattr(bar, primary_name, None)
    return value if value is not None else getattr(bar, fallback_name, None)


def _required_float(bar: Any, primary_name: str, fallback_name: str) -> float:
    value = _get_bar_field(bar, primary_name, fallback_name)
    if value is None:
        raise ValueError(f"Alpaca bar missing required field '{primary_name}'")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"Alpaca bar field '{primary_name}' must be finite")
    return numeric


def _normalize_timestamp(value: Any) -> datetime.datetime:
    if isinstance(value, datetime.datetime):
        return _ensure_utc(value)
    if isinstance(value, str):
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return _ensure_utc(parsed)
    raise ValueError(f"Unsupported Alpaca timestamp value: {value!r}")


def _ensure_utc(value: datetime.datetime) -> datetime.datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=datetime.UTC)
    return value.astimezone(datetime.UTC)


def _index_bars(
    bars: Sequence[NormalizedFeedBar],
) -> tuple[dict[tuple[str, datetime.datetime], NormalizedFeedBar], list[NormalizedFeedBar]]:
    index: dict[tuple[str, datetime.datetime], NormalizedFeedBar] = {}
    duplicates: list[NormalizedFeedBar] = []
    for bar in bars:
        if bar.key in index:
            duplicates.append(bar)
        index[bar.key] = bar
    return index, duplicates


def _average_daily_volume_by_symbol(
    bars: Iterable[NormalizedFeedBar],
) -> dict[str, float]:
    volumes_by_symbol_date: dict[tuple[str, datetime.date], float] = {}
    for bar in bars:
        key = (bar.symbol, bar.date)
        volumes_by_symbol_date[key] = volumes_by_symbol_date.get(key, 0.0) + bar.volume

    totals: dict[str, list[float]] = {}
    for (symbol, _date), volume in volumes_by_symbol_date.items():
        totals.setdefault(symbol, []).append(volume)

    return {
        symbol: sum(volumes) / len(volumes)
        for symbol, volumes in totals.items()
        if len(volumes) > 0
    }


def _ohlc_sanity_problem(bar: NormalizedFeedBar) -> str | None:
    prices = (bar.open, bar.high, bar.low, bar.close)
    if min(prices) <= 0:
        return "non-positive OHLC price"
    if bar.volume < 0:
        return "negative volume"
    if bar.high < bar.low:
        return "high below low"
    if bar.high < max(bar.open, bar.close):
        return "high below open/close"
    if bar.low > min(bar.open, bar.close):
        return "low above open/close"
    return None


def _price_delta_problem(
    left: NormalizedFeedBar,
    right: NormalizedFeedBar,
    tolerances: AlpacaFeedDeltaTolerances,
) -> dict[str, object] | None:
    worst_field: str | None = None
    worst_relative_delta = 0.0
    worst_absolute_delta = 0.0
    for field in OHLC_FIELDS:
        left_value = left.value_for_field(field)
        right_value = right.value_for_field(field)
        absolute_delta = abs(left_value - right_value)
        denominator = max(abs(right_value), 1e-12)
        relative_delta = absolute_delta / denominator
        if relative_delta > worst_relative_delta:
            worst_field = field
            worst_relative_delta = relative_delta
            worst_absolute_delta = absolute_delta

    if (
        worst_field is not None
        and worst_relative_delta > tolerances.max_ohlc_relative_delta
        and worst_absolute_delta > tolerances.min_ohlc_absolute_delta
    ):
        return {
            "field": worst_field,
            "relative_delta": round(worst_relative_delta, 8),
            "absolute_delta": round(worst_absolute_delta, 8),
            "max_ohlc_relative_delta": tolerances.max_ohlc_relative_delta,
            "min_ohlc_absolute_delta": tolerances.min_ohlc_absolute_delta,
        }
    return None


def _volume_ratio_problem(
    *,
    left: NormalizedFeedBar,
    right: NormalizedFeedBar,
    bucket: LiquidityBucketTolerance,
    left_feed: str,
    right_feed: str,
) -> dict[str, object] | None:
    if right.volume <= 0:
        if left.volume <= 0:
            return None
        return {
            "bucket": bucket.name,
            "left_feed": left_feed,
            "right_feed": right_feed,
            "left_volume": left.volume,
            "right_volume": right.volume,
            "left_to_right_volume_ratio": None,
            "min_left_to_right_volume_ratio": bucket.min_left_to_right_volume_ratio,
            "max_left_to_right_volume_ratio": bucket.max_left_to_right_volume_ratio,
        }

    ratio = left.volume / right.volume
    if (
        ratio < bucket.min_left_to_right_volume_ratio
        or ratio > bucket.max_left_to_right_volume_ratio
    ):
        return {
            "bucket": bucket.name,
            "left_feed": left_feed,
            "right_feed": right_feed,
            "left_volume": left.volume,
            "right_volume": right.volume,
            "left_to_right_volume_ratio": round(ratio, 8),
            "min_left_to_right_volume_ratio": bucket.min_left_to_right_volume_ratio,
            "max_left_to_right_volume_ratio": bucket.max_left_to_right_volume_ratio,
        }
    return None


def _resolve_timeframe(timeframe: str) -> Any:
    if not ALPACA_AVAILABLE:
        raise ImportError("alpaca-py package is required for Alpaca feed delta checks")
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


def _stable_object(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _stable_object(item) for key, item in sorted(value.items())}
    if isinstance(value, list | tuple):
        return [_stable_object(item) for item in value]
    if isinstance(value, datetime.datetime):
        return value.isoformat()
    if isinstance(value, datetime.date):
        return value.isoformat()
    return value
