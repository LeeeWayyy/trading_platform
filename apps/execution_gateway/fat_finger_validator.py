"""
Fat-finger order validation utilities.

This module provides threshold-based validation to catch suspiciously large
orders (e.g., typo quantities). It is intentionally side-effect free so it can
be reused in API, tests, and other services.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from apps.execution_gateway.schemas import FatFingerThresholds

ThresholdType = Literal["notional", "qty", "adv_pct", "data_unavailable"]


@dataclass(frozen=True)
class FatFingerBreach:
    """Represents a single threshold breach."""

    threshold_type: ThresholdType
    limit: Decimal | int | None
    actual: Decimal | int | None
    metadata: dict[str, Any]


@dataclass(frozen=True)
class FatFingerResult:
    """Validation result for fat-finger checks."""

    breached: bool
    breaches: tuple[FatFingerBreach, ...]
    thresholds: FatFingerThresholds
    notional: Decimal | None
    adv: int | None
    adv_pct: Decimal | None
    price: Decimal | None

    def to_response(self) -> dict[str, Any]:
        """Return JSON-serializable payload for API responses."""

        return {
            "breached": self.breached,
            "thresholds": {
                "max_notional": _decimal_to_str(self.thresholds.max_notional),
                "max_qty": self.thresholds.max_qty,
                "max_adv_pct": _decimal_to_str(self.thresholds.max_adv_pct),
            },
            "notional": _decimal_to_str(self.notional),
            "adv": self.adv,
            "adv_pct": _decimal_to_str(self.adv_pct),
            "price": _decimal_to_str(self.price),
            "breaches": [
                {
                    "threshold_type": breach.threshold_type,
                    "limit": _decimal_to_str(breach.limit),
                    "actual": _decimal_to_str(breach.actual),
                    "metadata": breach.metadata,
                }
                for breach in self.breaches
            ],
        }

    def log_fields(self) -> dict[str, Any]:
        """Return structured log fields for warning logs."""

        return {
            "breached": self.breached,
            "thresholds": {
                "max_notional": _decimal_to_str(self.thresholds.max_notional),
                "max_qty": self.thresholds.max_qty,
                "max_adv_pct": _decimal_to_str(self.thresholds.max_adv_pct),
            },
            "notional": _decimal_to_str(self.notional),
            "adv": self.adv,
            "adv_pct": _decimal_to_str(self.adv_pct),
            "price": _decimal_to_str(self.price),
            "breach_types": [breach.threshold_type for breach in self.breaches],
        }


class FatFingerValidator:
    """Validates orders against fat-finger thresholds."""

    def __init__(
        self,
        default_thresholds: FatFingerThresholds,
        symbol_overrides: dict[str, FatFingerThresholds] | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._default_thresholds = default_thresholds.model_copy(deep=True)
        self._symbol_overrides: dict[str, FatFingerThresholds] = {}

        if symbol_overrides:
            for symbol, thresholds in symbol_overrides.items():
                self._symbol_overrides[symbol.upper()] = thresholds.model_copy(deep=True)

    def get_default_thresholds(self) -> FatFingerThresholds:
        """Return a copy of the default thresholds."""
        with self._lock:
            return self._default_thresholds.model_copy(deep=True)

    def get_symbol_overrides(self) -> dict[str, FatFingerThresholds]:
        """Return copies of all per-symbol overrides."""
        with self._lock:
            return {
                symbol: value.model_copy(deep=True)
                for symbol, value in self._symbol_overrides.items()
            }

    def update_defaults(self, new_defaults: FatFingerThresholds) -> None:
        """Replace default thresholds."""
        with self._lock:
            self._default_thresholds = new_defaults.model_copy(deep=True)

    def update_symbol_overrides(self, overrides: dict[str, FatFingerThresholds | None]) -> None:
        """Patch per-symbol overrides.

        A None value removes the override for the symbol.
        """
        with self._lock:
            for symbol, thresholds in overrides.items():
                normalized = symbol.upper()
                if thresholds is None:
                    self._symbol_overrides.pop(normalized, None)
                    continue
                existing = self._symbol_overrides.get(normalized, FatFingerThresholds())
                patched = _patch_thresholds(existing, thresholds)
                self._symbol_overrides[normalized] = patched

    def get_effective_thresholds(self, symbol: str) -> FatFingerThresholds:
        """Return merged thresholds for the symbol (overrides applied)."""
        normalized = symbol.upper()
        with self._lock:
            override = self._symbol_overrides.get(normalized)
            defaults = self._default_thresholds

        if override is None:
            return defaults.model_copy(deep=True)

        return _merge_thresholds(defaults, override)

    def validate(
        self,
        *,
        symbol: str,
        qty: int,
        price: Decimal | None,
        adv: int | None,
        thresholds: FatFingerThresholds | None = None,
    ) -> FatFingerResult:
        """Validate an order against thresholds.

        Args:
            symbol: Stock symbol
            qty: Order quantity
            price: Price used for notional checks (limit, stop, or market price)
            adv: 20-day ADV for ADV% checks
            thresholds: Optional pre-resolved thresholds (for efficiency)
        """

        effective = thresholds or self.get_effective_thresholds(symbol)
        breaches: list[FatFingerBreach] = []
        missing_fields: list[str] = []

        notional: Decimal | None = None
        if effective.max_notional is not None:
            if price is None:
                missing_fields.append("price")
            else:
                notional = price * Decimal(qty)
                if notional > effective.max_notional:
                    breaches.append(
                        FatFingerBreach(
                            threshold_type="notional",
                            limit=effective.max_notional,
                            actual=notional,
                            metadata={
                                "price": _decimal_to_str(price),
                                "qty": qty,
                            },
                        )
                    )

        if effective.max_qty is not None and qty > effective.max_qty:
            breaches.append(
                FatFingerBreach(
                    threshold_type="qty",
                    limit=effective.max_qty,
                    actual=qty,
                    metadata={},
                )
            )

        adv_pct: Decimal | None = None
        if effective.max_adv_pct is not None:
            if adv is None or adv <= 0:
                missing_fields.append("adv")
            else:
                adv_pct = Decimal(qty) / Decimal(adv)
                if adv_pct > effective.max_adv_pct:
                    breaches.append(
                        FatFingerBreach(
                            threshold_type="adv_pct",
                            limit=effective.max_adv_pct,
                            actual=adv_pct,
                            metadata={
                                "adv": adv,
                                "qty": qty,
                            },
                        )
                    )

        if missing_fields:
            breaches.append(
                FatFingerBreach(
                    threshold_type="data_unavailable",
                    limit=None,
                    actual=None,
                    metadata={"missing": missing_fields},
                )
            )

        return FatFingerResult(
            breached=bool(breaches),
            breaches=tuple(breaches),
            thresholds=effective,
            notional=notional,
            adv=adv,
            adv_pct=adv_pct,
            price=price,
        )


def _merge_thresholds(
    defaults: FatFingerThresholds, override: FatFingerThresholds
) -> FatFingerThresholds:
    """Merge defaults with overrides (override wins when set)."""

    return FatFingerThresholds(
        max_notional=(
            override.max_notional if override.max_notional is not None else defaults.max_notional
        ),
        max_qty=override.max_qty if override.max_qty is not None else defaults.max_qty,
        max_adv_pct=(
            override.max_adv_pct if override.max_adv_pct is not None else defaults.max_adv_pct
        ),
    )


def _patch_thresholds(base: FatFingerThresholds, patch: FatFingerThresholds) -> FatFingerThresholds:
    """Patch base thresholds with non-None values from patch."""

    return FatFingerThresholds(
        max_notional=patch.max_notional if patch.max_notional is not None else base.max_notional,
        max_qty=patch.max_qty if patch.max_qty is not None else base.max_qty,
        max_adv_pct=patch.max_adv_pct if patch.max_adv_pct is not None else base.max_adv_pct,
    )


def _decimal_to_str(value: Decimal | int | None) -> str | int | None:
    """Convert Decimal to string for JSON-friendly payloads."""

    if value is None:
        return None
    if isinstance(value, Decimal):
        return str(value)
    return value


def iter_breach_types(breaches: Iterable[FatFingerBreach]) -> Iterable[ThresholdType]:
    """Yield threshold types from breaches (for metrics)."""

    for breach in breaches:
        yield breach.threshold_type


__all__ = [
    "FatFingerBreach",
    "FatFingerResult",
    "FatFingerThresholds",
    "FatFingerValidator",
    "ThresholdType",
    "iter_breach_types",
]
