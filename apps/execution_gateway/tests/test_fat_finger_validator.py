"""Unit tests for FatFingerValidator."""

from decimal import Decimal

from apps.execution_gateway.fat_finger_validator import FatFingerValidator
from apps.execution_gateway.schemas import FatFingerThresholds


def _defaults() -> FatFingerThresholds:
    return FatFingerThresholds(
        max_notional=Decimal("100000"),
        max_qty=10_000,
        max_adv_pct=Decimal("0.05"),
    )


def test_qty_breach() -> None:
    validator = FatFingerValidator(default_thresholds=_defaults())
    result = validator.validate(
        symbol="AAPL",
        qty=10_001,
        price=Decimal("100"),
        adv=1_000_000,
    )
    assert result.breached is True
    assert any(b.threshold_type == "qty" for b in result.breaches)


def test_notional_breach() -> None:
    validator = FatFingerValidator(default_thresholds=_defaults())
    result = validator.validate(
        symbol="AAPL",
        qty=2_000,
        price=Decimal("100"),
        adv=1_000_000,
    )
    assert result.breached is True
    assert any(b.threshold_type == "notional" for b in result.breaches)


def test_adv_pct_breach() -> None:
    validator = FatFingerValidator(default_thresholds=_defaults())
    result = validator.validate(
        symbol="AAPL",
        qty=6_000,
        price=Decimal("50"),
        adv=100_000,
    )
    assert result.breached is True
    assert any(b.threshold_type == "adv_pct" for b in result.breaches)


def test_symbol_override_applies() -> None:
    validator = FatFingerValidator(
        default_thresholds=_defaults(),
        symbol_overrides={"AAPL": FatFingerThresholds(max_qty=500)},
    )
    result = validator.validate(
        symbol="AAPL",
        qty=600,
        price=Decimal("10"),
        adv=100_000,
    )
    assert result.breached is True
    assert any(b.threshold_type == "qty" for b in result.breaches)

    other = validator.validate(
        symbol="MSFT",
        qty=600,
        price=Decimal("10"),
        adv=100_000,
    )
    assert other.breached is False


def test_missing_price_reports_data_unavailable() -> None:
    validator = FatFingerValidator(default_thresholds=_defaults())
    result = validator.validate(
        symbol="AAPL",
        qty=500,
        price=None,
        adv=1_000_000,
    )
    assert result.breached is True
    assert any(b.threshold_type == "data_unavailable" for b in result.breaches)
    assert all(b.threshold_type != "notional" for b in result.breaches)


def test_missing_adv_reports_data_unavailable() -> None:
    validator = FatFingerValidator(default_thresholds=_defaults())
    result = validator.validate(
        symbol="AAPL",
        qty=6_000,
        price=Decimal("10"),
        adv=None,
    )
    assert result.breached is True
    assert any(b.threshold_type == "data_unavailable" for b in result.breaches)
    assert all(b.threshold_type != "adv_pct" for b in result.breaches)


def test_override_patch_merges_values() -> None:
    validator = FatFingerValidator(
        default_thresholds=_defaults(),
        symbol_overrides={"AAPL": FatFingerThresholds(max_qty=1000)},
    )

    validator.update_symbol_overrides(
        {"AAPL": FatFingerThresholds(max_notional=Decimal("200000"))}
    )
    effective = validator.get_effective_thresholds("AAPL")
    assert effective.max_qty == 1000
    assert effective.max_notional == Decimal("200000")
