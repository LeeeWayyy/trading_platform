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

    validator.update_symbol_overrides({"AAPL": FatFingerThresholds(max_notional=Decimal("200000"))})
    effective = validator.get_effective_thresholds("AAPL")
    assert effective.max_qty == 1000
    assert effective.max_notional == Decimal("200000")


def test_result_to_response_serializes_correctly() -> None:
    """FatFingerResult.to_response() creates JSON-serializable dict."""
    validator = FatFingerValidator(default_thresholds=_defaults())
    result = validator.validate(
        symbol="AAPL",
        qty=10_001,
        price=Decimal("100"),
        adv=1_000_000,
    )
    response = result.to_response()

    assert response["breached"] is True
    assert "thresholds" in response
    assert "breaches" in response
    assert len(response["breaches"]) > 0
    # Decimal should be converted to string
    assert isinstance(response["notional"], str)


def test_result_log_fields_includes_breach_types() -> None:
    """FatFingerResult.log_fields() provides structured log data."""
    validator = FatFingerValidator(default_thresholds=_defaults())
    result = validator.validate(
        symbol="AAPL",
        qty=10_001,
        price=Decimal("100"),
        adv=1_000_000,
    )
    log_fields = result.log_fields()

    assert log_fields["breached"] is True
    assert "breach_types" in log_fields
    assert "qty" in log_fields["breach_types"]


def test_update_symbol_overrides_removes_with_none() -> None:
    """update_symbol_overrides with None removes override."""
    validator = FatFingerValidator(
        default_thresholds=_defaults(),
        symbol_overrides={"AAPL": FatFingerThresholds(max_qty=500)},
    )

    # Remove override
    validator.update_symbol_overrides({"AAPL": None})
    effective = validator.get_effective_thresholds("AAPL")

    # Should fallback to defaults
    assert effective.max_qty == 10_000  # Default value


def test_zero_adv_reports_data_unavailable() -> None:
    """ADV <= 0 is treated as missing data."""
    validator = FatFingerValidator(default_thresholds=_defaults())
    result = validator.validate(
        symbol="AAPL",
        qty=6_000,
        price=Decimal("10"),
        adv=0,  # Zero ADV
    )
    assert result.breached is True
    assert any(b.threshold_type == "data_unavailable" for b in result.breaches)
    assert all(b.threshold_type != "adv_pct" for b in result.breaches)


def test_iter_breach_types() -> None:
    """iter_breach_types utility extracts threshold types."""
    from apps.execution_gateway.fat_finger_validator import FatFingerBreach, iter_breach_types

    breaches = [
        FatFingerBreach(threshold_type="qty", limit=1000, actual=2000, metadata={}),
        FatFingerBreach(
            threshold_type="notional",
            limit=Decimal("100000"),
            actual=Decimal("200000"),
            metadata={},
        ),
    ]

    types = list(iter_breach_types(breaches))
    assert types == ["qty", "notional"]
