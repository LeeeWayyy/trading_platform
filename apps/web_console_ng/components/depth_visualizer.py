"""DOM depth visualization helpers."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DepthLevel:
    """Normalized depth level for rendering."""

    price: float
    size: float
    size_ratio: float
    is_large: bool


@dataclass(frozen=True)
class DepthSnapshot:
    """Normalized order book snapshot for DOM ladder rendering."""

    symbol: str
    timestamp: str | None
    bids: list[DepthLevel]
    asks: list[DepthLevel]
    mid_price: float | None
    avg_bid_size: float
    avg_ask_size: float


class DOMDataProcessor:
    """Process raw orderbook updates into render-friendly snapshots.

    Tracks rolling average sizes to flag large orders.
    """

    def __init__(
        self,
        *,
        levels: int = 10,
        history_size: int = 200,
        large_multiplier: float = 2.0,
    ) -> None:
        self._levels = max(1, levels)
        self._large_multiplier = max(1.0, large_multiplier)
        self._bid_sizes: deque[float] = deque(maxlen=history_size)
        self._ask_sizes: deque[float] = deque(maxlen=history_size)

    def process(self, data: dict[str, Any]) -> DepthSnapshot | None:
        symbol = str(data.get("S") or data.get("symbol") or "").strip().upper()
        if not symbol:
            return None

        bids_raw = data.get("b") or data.get("bids") or []
        asks_raw = data.get("a") or data.get("asks") or []
        if not isinstance(bids_raw, list) or not isinstance(asks_raw, list):
            return None

        bids = self._normalize_levels(bids_raw, descending=True)
        asks = self._normalize_levels(asks_raw, descending=False)

        if not bids and not asks:
            return DepthSnapshot(
                symbol=symbol,
                timestamp=self._normalize_timestamp(data.get("t")),
                bids=[],
                asks=[],
                mid_price=None,
                avg_bid_size=self._average(self._bid_sizes),
                avg_ask_size=self._average(self._ask_sizes),
            )

        avg_bid = self._average(self._bid_sizes)
        avg_ask = self._average(self._ask_sizes)

        max_size = max([level["size"] for level in bids + asks] or [1.0])

        bid_levels = [
            DepthLevel(
                price=level["price"],
                size=level["size"],
                size_ratio=level["size"] / max_size if max_size else 0.0,
                is_large=self._is_large(level["size"], avg_bid),
            )
            for level in bids
        ]
        ask_levels = [
            DepthLevel(
                price=level["price"],
                size=level["size"],
                size_ratio=level["size"] / max_size if max_size else 0.0,
                is_large=self._is_large(level["size"], avg_ask),
            )
            for level in asks
        ]

        # Update rolling size history after evaluating large orders
        for level in bids:
            self._bid_sizes.append(level["size"])
        for level in asks:
            self._ask_sizes.append(level["size"])

        mid_price = None
        if bid_levels and ask_levels:
            mid_price = (bid_levels[0].price + ask_levels[0].price) / 2

        return DepthSnapshot(
            symbol=symbol,
            timestamp=self._normalize_timestamp(data.get("t")),
            bids=bid_levels,
            asks=ask_levels,
            mid_price=mid_price,
            avg_bid_size=avg_bid,
            avg_ask_size=avg_ask,
        )

    def _normalize_levels(self, raw: list[Any], *, descending: bool) -> list[dict[str, float]]:
        levels: list[dict[str, float]] = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            price_raw = entry.get("p")
            size_raw = entry.get("s")
            if price_raw is None or size_raw is None:
                continue
            try:
                price = float(price_raw)
                size = float(size_raw)
            except (TypeError, ValueError):
                continue
            if price <= 0 or size <= 0:
                continue
            levels.append({"price": price, "size": size})

        levels.sort(key=lambda x: x["price"], reverse=descending)
        return levels[: self._levels]

    @staticmethod
    def _average(values: deque[float]) -> float:
        if not values:
            return 0.0
        return sum(values) / len(values)

    def _is_large(self, size: float, avg_size: float) -> bool:
        if avg_size <= 0:
            return False
        return size >= (self._large_multiplier * avg_size)

    @staticmethod
    def _normalize_timestamp(value: Any) -> str | None:
        if value is None:
            return None
        return str(value)


class DepthVisualizer:
    """Build render payloads for DOM ladder."""

    def __init__(
        self,
        *,
        levels: int = 10,
        history_size: int = 200,
        large_multiplier: float = 2.0,
    ) -> None:
        self._processor = DOMDataProcessor(
            levels=levels,
            history_size=history_size,
            large_multiplier=large_multiplier,
        )

    def build_payload(self, data: dict[str, Any]) -> dict[str, Any] | None:
        snapshot = self._processor.process(data)
        if snapshot is None:
            return None

        return {
            "symbol": snapshot.symbol,
            "timestamp": snapshot.timestamp,
            "mid": snapshot.mid_price,
            "avg_bid_size": snapshot.avg_bid_size,
            "avg_ask_size": snapshot.avg_ask_size,
            "bids": [
                {
                    "price": level.price,
                    "size": level.size,
                    "ratio": level.size_ratio,
                    "is_large": level.is_large,
                }
                for level in snapshot.bids
            ],
            "asks": [
                {
                    "price": level.price,
                    "size": level.size,
                    "ratio": level.size_ratio,
                    "is_large": level.is_large,
                }
                for level in snapshot.asks
            ],
        }
