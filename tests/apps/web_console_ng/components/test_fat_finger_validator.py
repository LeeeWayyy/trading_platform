from __future__ import annotations

from decimal import Decimal

from apps.web_console_ng.components.fat_finger_validator import (
    FatFingerThresholds,
    FatFingerValidator,
    parse_thresholds,
)


def test_fat_finger_qty_validation() -> None:
    validator = FatFingerValidator(FatFingerThresholds(max_qty=5))
    result = validator.validate(symbol="AAPL", qty=10, price=Decimal("1"), adv=1000)
    assert result.blocked is True
    assert any(w.type == "qty" for w in result.warnings)


def test_fat_finger_notional_validation() -> None:
    validator = FatFingerValidator(FatFingerThresholds(max_notional=Decimal("100")))
    result = validator.validate(symbol="AAPL", qty=10, price=Decimal("20"), adv=1000)
    assert result.blocked is True
    assert any(w.type == "notional" for w in result.warnings)


def test_fat_finger_adv_validation() -> None:
    validator = FatFingerValidator(FatFingerThresholds(max_adv_pct=Decimal("0.10")))
    result = validator.validate(symbol="AAPL", qty=10, price=Decimal("1"), adv=50)
    assert result.blocked is True
    assert any(w.type == "adv_pct" for w in result.warnings)


def test_fat_finger_remaining_capacity() -> None:
    thresholds = FatFingerThresholds(
        max_qty=10,
        max_notional=Decimal("1000"),
        max_adv_pct=Decimal("0.20"),
    )
    validator = FatFingerValidator(thresholds)
    result = validator.validate(symbol="AAPL", qty=5, price=Decimal("50"), adv=1000)
    assert result.blocked is False
    assert result.remaining_qty == 5
    assert result.remaining_notional == Decimal("750")
    assert result.remaining_adv_shares == 195


def test_parse_thresholds_payload() -> None:
    payload = {
        "default_thresholds": {
            "max_notional": "100000",
            "max_qty": 10000,
            "max_adv_pct": "0.05",
        },
        "symbol_overrides": {"AAPL": {"max_qty": 5000}},
    }
    defaults, overrides = parse_thresholds(payload)
    assert defaults.max_qty == 10000
    assert defaults.max_notional == Decimal("100000")
    assert overrides["AAPL"].max_qty == 5000
