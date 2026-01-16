"""Tests for Execution Gateway configuration parsing."""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.execution_gateway import config as config_module


def _reset_cached_config() -> None:
    config_module._config_instance = None  # type: ignore[attr-defined]


def test_get_float_env_invalid_logs_default(caplog, monkeypatch):
    monkeypatch.setenv("TEST_FLOAT", "not-a-float")
    with caplog.at_level("WARNING"):
        value = config_module._get_float_env("TEST_FLOAT", 1.25)
    assert value == 1.25
    assert "Invalid float" in caplog.text


def test_get_decimal_env_invalid_logs_default(caplog, monkeypatch):
    monkeypatch.setenv("TEST_DECIMAL", "bad")
    with caplog.at_level("WARNING"):
        value = config_module._get_decimal_env("TEST_DECIMAL", Decimal("2.5"))
    assert value == Decimal("2.5")
    assert "Invalid decimal" in caplog.text


def test_get_int_env_invalid_logs_default(caplog, monkeypatch):
    monkeypatch.setenv("TEST_INT", "bad")
    with caplog.at_level("WARNING"):
        value = config_module._get_int_env("TEST_INT", 7)
    assert value == 7
    assert "Invalid int" in caplog.text


def test_bool_env_strict_and_permissive(monkeypatch):
    monkeypatch.setenv("BOOL_STRICT", "true")
    assert config_module._get_bool_env_strict("BOOL_STRICT", False) is True

    monkeypatch.setenv("BOOL_STRICT", "yes")
    assert config_module._get_bool_env_strict("BOOL_STRICT", False) is False

    monkeypatch.setenv("BOOL_PERM", "yes")
    assert config_module._get_bool_env_permissive("BOOL_PERM", False) is True

    monkeypatch.setenv("BOOL_PERM", "off")
    assert config_module._get_bool_env_permissive("BOOL_PERM", True) is False


def test_get_config_validates_thresholds(monkeypatch, caplog):
    _reset_cached_config()
    monkeypatch.setenv("FAT_FINGER_MAX_NOTIONAL", "0")
    monkeypatch.setenv("FAT_FINGER_MAX_QTY", "-1")
    monkeypatch.setenv("FAT_FINGER_MAX_ADV_PCT", "2")
    monkeypatch.setenv("FAT_FINGER_MAX_PRICE_AGE_SECONDS", "0")
    monkeypatch.setenv("MAX_SLICE_PCT_OF_ADV", "0")
    monkeypatch.setenv("ALPACA_DATA_FEED", "   ")

    with caplog.at_level("WARNING"):
        cfg = config_module.get_config()

    assert cfg.fat_finger_max_notional is None
    assert cfg.fat_finger_max_qty is None
    assert cfg.fat_finger_max_adv_pct is None
    assert cfg.fat_finger_max_price_age_seconds == config_module.FAT_FINGER_MAX_PRICE_AGE_SECONDS_DEFAULT
    assert cfg.max_slice_pct_of_adv == 0.01
    assert cfg.alpaca_data_feed is None
    assert "FAT_FINGER_MAX_NOTIONAL must be > 0" in caplog.text
    assert "FAT_FINGER_MAX_QTY must be > 0" in caplog.text
    assert "FAT_FINGER_MAX_ADV_PCT must be within (0, 1]" in caplog.text
    assert "FAT_FINGER_MAX_PRICE_AGE_SECONDS must be > 0" in caplog.text
    assert "MAX_SLICE_PCT_OF_ADV must be > 0" in caplog.text


def test_get_config_cached_returns_singleton(monkeypatch):
    _reset_cached_config()
    monkeypatch.setenv("STRATEGY_ID", "alpha_baseline")
    cfg1 = config_module.get_config_cached()
    monkeypatch.setenv("STRATEGY_ID", "should_not_change")
    cfg2 = config_module.get_config_cached()

    assert cfg1 is cfg2
    assert cfg2.strategy_id == "alpha_baseline"

