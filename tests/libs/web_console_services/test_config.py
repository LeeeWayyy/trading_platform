"""Tests for web console configuration."""

from __future__ import annotations

import importlib
import sys

import pytest

_ENV_KEYS = [
    "WEB_CONSOLE_DEV_STRATEGIES",
    "STRATEGY_ID",
    "FEATURE_RISK_DASHBOARD",
    "FEATURE_STRATEGY_COMPARISON",
    "TRUSTED_PROXY_IPS",
    "ENVIRONMENT",
    "RISK_BUDGET_VAR_LIMIT",
    "RISK_BUDGET_WARNING_THRESHOLD",
]


def _reload_config(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> object:
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    module_name = "libs.web_console_services.config"
    if module_name not in sys.modules:
        return importlib.import_module(module_name)
    return importlib.reload(sys.modules[module_name])


def test_safe_float_returns_default_on_invalid(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("TEST_FLOAT", "not-a-number")
    config = _reload_config(monkeypatch, {"ENVIRONMENT": "dev"})
    with caplog.at_level("WARNING"):
        value = config._safe_float("TEST_FLOAT", 1.25)
    assert value == 1.25
    assert any("Invalid float value for TEST_FLOAT" in record.message for record in caplog.records)


def test_safe_float_returns_default_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TEST_FLOAT", raising=False)
    config = _reload_config(monkeypatch, {"ENVIRONMENT": "dev"})
    assert config._safe_float("TEST_FLOAT", 2.5) == 2.5


def test_dev_strategies_falls_back_to_strategy_id(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _reload_config(
        monkeypatch,
        {
            "ENVIRONMENT": "dev",
            "WEB_CONSOLE_DEV_STRATEGIES": "",
            "STRATEGY_ID": "alpha_baseline",
        },
    )

    assert cfg.DEV_STRATEGIES == ["alpha_baseline"]


def test_dev_strategies_uses_explicit_list(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _reload_config(
        monkeypatch,
        {
            "ENVIRONMENT": "dev",
            "WEB_CONSOLE_DEV_STRATEGIES": " alpha , , beta ",
            "STRATEGY_ID": "alpha_baseline",
        },
    )

    assert cfg.DEV_STRATEGIES == ["alpha", "beta"]


def test_feature_flags_and_proxy_ips(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _reload_config(
        monkeypatch,
        {
            "ENVIRONMENT": "prod",
            "FEATURE_RISK_DASHBOARD": "Yes",
            "FEATURE_STRATEGY_COMPARISON": "1",
            "TRUSTED_PROXY_IPS": "10.0.0.1, 10.0.0.2",
            "RISK_BUDGET_VAR_LIMIT": "0.10",
            "RISK_BUDGET_WARNING_THRESHOLD": "0.9",
        },
    )

    assert cfg.FEATURE_RISK_DASHBOARD is True
    assert cfg.FEATURE_STRATEGY_COMPARISON is True
    assert cfg.TRUSTED_PROXY_IPS == ["10.0.0.1", "10.0.0.2"]
    assert cfg.RISK_BUDGET_VAR_LIMIT == pytest.approx(0.10)
    assert cfg.RISK_BUDGET_WARNING_THRESHOLD == pytest.approx(0.9)
