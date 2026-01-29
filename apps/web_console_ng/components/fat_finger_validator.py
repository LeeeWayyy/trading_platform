"""Fat-finger pre-validation utilities for the web console."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal


@dataclass(frozen=True)
class FatFingerThresholds:
    """Fat-finger thresholds for validation (frontend mirror)."""

    max_notional: Decimal | None = None
    max_qty: int | None = None
    max_adv_pct: Decimal | None = None


@dataclass(frozen=True)
class FatFingerWarning:
    """Single warning or error emitted during validation."""

    type: Literal["qty", "notional", "adv_pct", "data_unavailable"]
    message: str
    severity: Literal["error", "warning"]


@dataclass(frozen=True)
class FatFingerValidationResult:
    """Validation result with remaining capacity metadata."""

    warnings: list[FatFingerWarning]
    blocked: bool
    notional: Decimal | None
    adv_pct: Decimal | None
    remaining_qty: int | None
    remaining_notional: Decimal | None
    remaining_adv_shares: int | None


class FatFingerValidator:
    """Validates orders against fat-finger thresholds."""

    def __init__(
        self,
        default_thresholds: FatFingerThresholds,
        symbol_overrides: dict[str, FatFingerThresholds] | None = None,
    ) -> None:
        self._default_thresholds = default_thresholds
        self._symbol_overrides = {
            symbol.upper(): thresholds for symbol, thresholds in (symbol_overrides or {}).items()
        }

    def get_effective_thresholds(self, symbol: str) -> FatFingerThresholds:
        """Merge defaults with per-symbol overrides (override wins when set)."""

        override = self._symbol_overrides.get(symbol.upper())
        if override is None:
            return self._default_thresholds
        return FatFingerThresholds(
            max_notional=(
                override.max_notional
                if override.max_notional is not None
                else self._default_thresholds.max_notional
            ),
            max_qty=(
                override.max_qty
                if override.max_qty is not None
                else self._default_thresholds.max_qty
            ),
            max_adv_pct=(
                override.max_adv_pct
                if override.max_adv_pct is not None
                else self._default_thresholds.max_adv_pct
            ),
        )

    def validate(
        self,
        *,
        symbol: str,
        qty: int,
        price: Decimal | None,
        adv: int | None,
        thresholds: FatFingerThresholds | None = None,
    ) -> FatFingerValidationResult:
        """Validate against thresholds and return warnings + capacity."""

        effective = thresholds or self.get_effective_thresholds(symbol)
        warnings: list[FatFingerWarning] = []
        blocked = False

        notional: Decimal | None = None
        remaining_notional: Decimal | None = None
        if effective.max_notional is not None:
            if price is not None:
                notional = price * Decimal(qty)
                remaining_notional = effective.max_notional - notional
                if notional > effective.max_notional:
                    blocked = True
                    warnings.append(
                        FatFingerWarning(
                            type="notional",
                            message=(
                                "Order value "
                                f"${_format_money(notional)} exceeds limit of "
                                f"${_format_money(effective.max_notional)}"
                            ),
                            severity="error",
                        )
                    )

        remaining_qty: int | None = None
        if effective.max_qty is not None:
            remaining_qty = effective.max_qty - qty
            if qty > effective.max_qty:
                blocked = True
                warnings.append(
                    FatFingerWarning(
                        type="qty",
                        message=(
                            "Order quantity "
                            f"{qty:,} exceeds limit of {effective.max_qty:,} shares"
                        ),
                        severity="error",
                    )
                )

        adv_pct: Decimal | None = None
        remaining_adv_shares: int | None = None
        if effective.max_adv_pct is not None:
            if adv is not None and adv > 0:
                adv_pct = Decimal(qty) / Decimal(adv)
                max_adv_shares = int((effective.max_adv_pct * Decimal(adv)).to_integral_value())
                remaining_adv_shares = max_adv_shares - qty
                if adv_pct > effective.max_adv_pct:
                    blocked = True
                    warnings.append(
                        FatFingerWarning(
                            type="adv_pct",
                            message=(
                                "Order represents "
                                f"{_format_pct(adv_pct)}% of daily volume "
                                f"(max {_format_pct(effective.max_adv_pct)}%)"
                            ),
                            severity="error",
                        )
                    )

        return FatFingerValidationResult(
            warnings=warnings,
            blocked=blocked,
            notional=notional,
            adv_pct=adv_pct,
            remaining_qty=remaining_qty,
            remaining_notional=remaining_notional,
            remaining_adv_shares=remaining_adv_shares,
        )


def parse_thresholds(payload: dict[str, object]) -> tuple[FatFingerThresholds, dict[str, FatFingerThresholds]]:
    """Parse thresholds API payload into frontend models."""

    default_payload = payload.get("default_thresholds")
    overrides_payload = payload.get("symbol_overrides")

    default_thresholds = _parse_thresholds_dict(default_payload)
    overrides: dict[str, FatFingerThresholds] = {}
    if isinstance(overrides_payload, dict):
        for symbol, value in overrides_payload.items():
            if isinstance(symbol, str) and isinstance(value, dict):
                overrides[symbol.upper()] = _parse_thresholds_dict(value)

    return default_thresholds, overrides


def _parse_thresholds_dict(data: object) -> FatFingerThresholds:
    if not isinstance(data, dict):
        return FatFingerThresholds()
    max_notional = _to_decimal(data.get("max_notional"))
    max_qty = _to_int(data.get("max_qty"))
    max_adv_pct = _to_decimal(data.get("max_adv_pct"))
    return FatFingerThresholds(
        max_notional=max_notional,
        max_qty=max_qty,
        max_adv_pct=max_adv_pct,
    )


def _to_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _to_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _format_money(value: Decimal) -> str:
    return f"{value:,.2f}"


def _format_pct(value: Decimal) -> str:
    return f"{(value * Decimal(100)):.1f}"


__all__ = [
    "FatFingerThresholds",
    "FatFingerValidationResult",
    "FatFingerValidator",
    "FatFingerWarning",
    "parse_thresholds",
]
