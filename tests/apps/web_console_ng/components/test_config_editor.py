"""Tests for config_editor component (P6T12.1).

Tests JSON round-trip, validation, provider mapping, and unknown key detection.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from apps.web_console_ng.components.config_editor import (
    PROVIDER_DISPLAY,
    PROVIDER_DISPLAY_INVERSE,
    ValidationResult,
    detect_unknown_keys,
    form_state_to_json,
    json_to_form_state,
    validate_backtest_params,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def valid_config() -> dict[str, Any]:
    """Minimal valid config dict matching BacktestJobConfig.to_dict() format."""
    return {
        "alpha_name": "momentum_1m",
        "start_date": "2024-01-01",
        "end_date": "2025-12-31",
        "weight_method": "zscore",
        "provider": "crsp",
    }


@pytest.fixture()
def config_with_extras(valid_config: dict[str, Any]) -> dict[str, Any]:
    """Config with extra_params (universe + cost_model)."""
    valid_config["extra_params"] = {
        "universe": ["AAPL", "MSFT", "NVDA"],
        "cost_model": {
            "enabled": True,
            "bps_per_trade": 5.0,
            "impact_coefficient": 0.1,
            "participation_limit": 0.05,
            "adv_source": "crsp",
            "portfolio_value_usd": 1_000_000,
        },
    }
    return valid_config


# ===================================================================
# ValidationResult
# ===================================================================
class TestValidationResult:
    def test_no_errors_is_valid(self) -> None:
        r = ValidationResult()
        assert r.is_valid is True

    def test_errors_is_invalid(self) -> None:
        r = ValidationResult(errors=["oops"])
        assert r.is_valid is False

    def test_warnings_still_valid(self) -> None:
        r = ValidationResult(warnings=["heads up"])
        assert r.is_valid is True


# ===================================================================
# validate_backtest_params
# ===================================================================
class TestValidateBacktestParams:
    def test_valid_config(self, valid_config: dict[str, Any]) -> None:
        result = validate_backtest_params(valid_config)
        assert result.is_valid
        assert not result.warnings

    def test_missing_alpha_name(self, valid_config: dict[str, Any]) -> None:
        del valid_config["alpha_name"]
        result = validate_backtest_params(valid_config)
        assert not result.is_valid
        assert any("alpha_name" in e for e in result.errors)

    def test_missing_start_date(self, valid_config: dict[str, Any]) -> None:
        del valid_config["start_date"]
        result = validate_backtest_params(valid_config)
        assert not result.is_valid
        assert any("start_date" in e for e in result.errors)

    def test_missing_end_date(self, valid_config: dict[str, Any]) -> None:
        del valid_config["end_date"]
        result = validate_backtest_params(valid_config)
        assert not result.is_valid
        assert any("end_date" in e for e in result.errors)

    def test_invalid_date_format(self, valid_config: dict[str, Any]) -> None:
        valid_config["start_date"] = "not-a-date"
        result = validate_backtest_params(valid_config)
        assert not result.is_valid
        assert any("date" in e.lower() for e in result.errors)

    def test_end_before_start(self, valid_config: dict[str, Any]) -> None:
        valid_config["start_date"] = "2025-06-01"
        valid_config["end_date"] = "2025-01-01"
        result = validate_backtest_params(valid_config)
        assert not result.is_valid
        assert any("after" in e.lower() for e in result.errors)

    def test_period_too_short(self, valid_config: dict[str, Any]) -> None:
        valid_config["start_date"] = "2024-01-01"
        valid_config["end_date"] = "2024-01-15"
        result = validate_backtest_params(valid_config)
        assert not result.is_valid
        assert any("30 days" in e for e in result.errors)

    def test_future_end_date(self, valid_config: dict[str, Any]) -> None:
        valid_config["end_date"] = "2099-01-01"
        result = validate_backtest_params(valid_config)
        assert not result.is_valid
        assert any("future" in e.lower() for e in result.errors)

    def test_year_before_1990(self, valid_config: dict[str, Any]) -> None:
        valid_config["start_date"] = "1985-01-01"
        valid_config["end_date"] = "1989-12-31"
        result = validate_backtest_params(valid_config)
        assert not result.is_valid
        assert any("1990" in e for e in result.errors)

    def test_unrecognised_provider(self, valid_config: dict[str, Any]) -> None:
        valid_config["provider"] = "bloomberg"
        result = validate_backtest_params(valid_config)
        assert not result.is_valid
        assert any("provider" in e.lower() for e in result.errors)

    def test_invalid_universe_symbol(self, valid_config: dict[str, Any]) -> None:
        valid_config["extra_params"] = {"universe": ["AAPL", "123BAD"]}
        result = validate_backtest_params(valid_config)
        assert not result.is_valid
        assert any("symbol" in e.lower() for e in result.errors)

    def test_valid_universe_symbols(self, valid_config: dict[str, Any]) -> None:
        valid_config["extra_params"] = {"universe": ["AAPL", "BRK.A", "KHC"]}
        result = validate_backtest_params(valid_config)
        assert result.is_valid

    def test_yahoo_cost_model_warning(self, valid_config: dict[str, Any]) -> None:
        valid_config["provider"] = "yfinance"
        valid_config["extra_params"] = {
            "cost_model": {"enabled": True, "bps_per_trade": 5.0},
        }
        result = validate_backtest_params(valid_config)
        assert result.is_valid  # warning, not error
        assert len(result.warnings) == 1
        assert "Yahoo" in result.warnings[0]

    def test_provider_defaults_to_crsp(self) -> None:
        config = {
            "alpha_name": "test",
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
        }
        result = validate_backtest_params(config)
        assert result.is_valid


# ===================================================================
# Provider mapping
# ===================================================================
class TestProviderMapping:
    def test_display_keys(self) -> None:
        assert "crsp" in PROVIDER_DISPLAY
        assert "yfinance" in PROVIDER_DISPLAY

    def test_inverse_roundtrip(self) -> None:
        for key, display in PROVIDER_DISPLAY.items():
            assert PROVIDER_DISPLAY_INVERSE[display] == key


# ===================================================================
# form_state_to_json / json_to_form_state round-trip
# ===================================================================
class TestFormJsonRoundTrip:
    def test_basic_roundtrip(self) -> None:
        json_str = form_state_to_json(
            alpha_name="momentum_1m",
            start_date="2024-01-01",
            end_date="2025-12-31",
            weight_method="zscore",
            provider_display_label="CRSP (production)",
            universe_csv=None,
            cost_config=None,
        )
        data = json.loads(json_str)
        assert data["alpha_name"] == "momentum_1m"
        assert data["provider"] == "crsp"
        assert "extra_params" not in data  # no extras → omitted

    def test_roundtrip_with_universe(self) -> None:
        json_str = form_state_to_json(
            alpha_name="alpha1",
            start_date="2024-01-01",
            end_date="2024-12-31",
            weight_method="rank",
            provider_display_label="Yahoo Finance (dev only)",
            universe_csv="AAPL, MSFT, NVDA",
            cost_config=None,
        )
        data = json.loads(json_str)
        assert data["provider"] == "yfinance"
        assert data["extra_params"]["universe"] == ["AAPL", "MSFT", "NVDA"]

    def test_roundtrip_with_cost_model(self) -> None:
        cost = {"enabled": True, "bps_per_trade": 5.0}
        json_str = form_state_to_json(
            alpha_name="alpha1",
            start_date="2024-01-01",
            end_date="2024-12-31",
            weight_method="zscore",
            provider_display_label="CRSP (production)",
            universe_csv=None,
            cost_config=cost,
        )
        data = json.loads(json_str)
        assert data["extra_params"]["cost_model"]["bps_per_trade"] == 5.0

    def test_hidden_extra_params_preserved(self) -> None:
        json_str = form_state_to_json(
            alpha_name="alpha1",
            start_date="2024-01-01",
            end_date="2024-12-31",
            weight_method="zscore",
            provider_display_label="CRSP (production)",
            universe_csv=None,
            cost_config=None,
            extra_params_hidden={"custom_key": 42},
        )
        data = json.loads(json_str)
        assert data["extra_params"]["custom_key"] == 42

    def test_json_to_form_basic(self) -> None:
        raw = json.dumps({
            "alpha_name": "momentum_1m",
            "start_date": "2024-01-01",
            "end_date": "2025-12-31",
            "weight_method": "quantile",
            "provider": "yfinance",
        })
        fs = json_to_form_state(raw)
        assert fs.alpha_name == "momentum_1m"
        assert fs.weight_method == "quantile"
        assert fs.provider_display_label == "Yahoo Finance (dev only)"
        assert fs.universe_csv == ""
        assert fs.cost_config is None

    def test_json_to_form_with_extras(self) -> None:
        raw = json.dumps({
            "alpha_name": "alpha1",
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
            "provider": "crsp",
            "extra_params": {
                "universe": ["AAPL", "MSFT"],
                "cost_model": {"enabled": True},
                "custom_key": "secret",
            },
        })
        fs = json_to_form_state(raw)
        assert fs.universe_csv == "AAPL, MSFT"
        assert fs.cost_config == {"enabled": True}
        assert fs.extra_params_hidden == {"custom_key": "secret"}

    def test_json_to_form_invalid_json(self) -> None:
        with pytest.raises(ValueError, match="Invalid JSON"):
            json_to_form_state("{bad json")

    def test_json_to_form_not_object(self) -> None:
        with pytest.raises(ValueError, match="object"):
            json_to_form_state("[1,2,3]")

    def test_json_to_form_bad_provider(self) -> None:
        with pytest.raises(ValueError, match="provider"):
            json_to_form_state(json.dumps({"provider": "bloomberg"}))

    def test_full_roundtrip(self) -> None:
        """form → JSON → form state → JSON produces consistent output."""
        original_json = form_state_to_json(
            alpha_name="momentum_1m",
            start_date="2024-01-01",
            end_date="2025-12-31",
            weight_method="zscore",
            provider_display_label="CRSP (production)",
            universe_csv="AAPL, MSFT",
            cost_config={"enabled": True, "bps_per_trade": 5.0},
        )
        fs = json_to_form_state(original_json)
        reconstructed = form_state_to_json(
            alpha_name=fs.alpha_name,
            start_date=fs.start_date,
            end_date=fs.end_date,
            weight_method=fs.weight_method,
            provider_display_label=fs.provider_display_label,
            universe_csv=fs.universe_csv,
            cost_config=fs.cost_config,
            extra_params_hidden=fs.extra_params_hidden or None,
        )
        assert json.loads(original_json) == json.loads(reconstructed)


# ===================================================================
# detect_unknown_keys
# ===================================================================
class TestDetectUnknownKeys:
    def test_no_unknown(self, valid_config: dict[str, Any]) -> None:
        assert detect_unknown_keys(valid_config) == []

    def test_unknown_keys_detected(self, valid_config: dict[str, Any]) -> None:
        valid_config["foo"] = "bar"
        valid_config["baz"] = 42
        unknown = detect_unknown_keys(valid_config)
        assert unknown == ["baz", "foo"]  # sorted

    def test_extra_params_is_known(self, config_with_extras: dict[str, Any]) -> None:
        assert detect_unknown_keys(config_with_extras) == []


# ===================================================================
# Integration: validate + from_dict
# ===================================================================
class TestValidationWithFromDict:
    """Verify that configs passing validate_backtest_params also pass from_dict."""

    def test_valid_config_passes_from_dict(self, valid_config: dict[str, Any]) -> None:
        from libs.trading.backtest.job_queue import BacktestJobConfig

        result = validate_backtest_params(valid_config)
        assert result.is_valid

        config = BacktestJobConfig.from_dict(valid_config)
        assert config.alpha_name == "momentum_1m"
        assert config.provider.value == "crsp"

    def test_config_with_extras_passes_from_dict(
        self, config_with_extras: dict[str, Any]
    ) -> None:
        from libs.trading.backtest.job_queue import BacktestJobConfig

        result = validate_backtest_params(config_with_extras)
        assert result.is_valid

        config = BacktestJobConfig.from_dict(config_with_extras)
        assert config.extra_params["universe"] == ["AAPL", "MSFT", "NVDA"]
        assert config.extra_params["cost_model"]["bps_per_trade"] == 5.0

    def test_roundtrip_to_dict_from_dict(self, valid_config: dict[str, Any]) -> None:
        from libs.trading.backtest.job_queue import BacktestJobConfig

        config1 = BacktestJobConfig.from_dict(valid_config)
        serialised = config1.to_dict()
        config2 = BacktestJobConfig.from_dict(serialised)

        assert config1.alpha_name == config2.alpha_name
        assert config1.start_date == config2.start_date
        assert config1.end_date == config2.end_date
        assert config1.weight_method == config2.weight_method
        assert config1.provider == config2.provider
