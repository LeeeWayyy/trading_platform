"""Tests for apps/signal_service/config.py Settings."""

from pathlib import Path

import pytest

from apps.signal_service.config import Settings

_ENV_KEYS = [
    "HOST",
    "PORT",
    "DEBUG",
    "TESTING",
    "DATABASE_URL",
    "DATA_DIR",
    "DEFAULT_STRATEGY",
    "TRADABLE_SYMBOLS",
    "TOP_N",
    "BOTTOM_N",
    "MODEL_RELOAD_INTERVAL_SECONDS",
    "LOG_LEVEL",
    "REDIS_ENABLED",
    "REDIS_HOST",
    "REDIS_PORT",
    "REDIS_DB",
    "REDIS_TTL",
    "REDIS_FALLBACK_BUFFER_MAX_SIZE",
    "REDIS_FALLBACK_BUFFER_PATH",
    "REDIS_FALLBACK_REPLAY_INTERVAL_SECONDS",
    "FEATURE_HYDRATION_ENABLED",
    "FEATURE_HYDRATION_TIMEOUT_SECONDS",
    "SHADOW_VALIDATION_ENABLED",
    "SHADOW_SAMPLE_COUNT",
    "SKIP_SHADOW_VALIDATION",
]


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
        monkeypatch.delenv(key.lower(), raising=False)


def test_settings_defaults_ignore_env_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify Settings defaults without env or .env overrides."""
    _clear_env(monkeypatch)

    settings = Settings(_env_file=None)

    assert settings.host == "0.0.0.0"
    assert settings.port == 8001
    assert settings.debug is False
    assert settings.testing is False
    assert settings.database_url.startswith("postgresql://")
    assert settings.data_dir == Path("data/adjusted")
    assert settings.default_strategy == "alpha_baseline"
    assert settings.tradable_symbols == ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA"]
    assert settings.top_n == 3
    assert settings.bottom_n == 3
    assert settings.redis_enabled is False
    assert settings.redis_fallback_buffer_path is None


def test_settings_env_overrides_case_insensitive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Verify env overrides are parsed and case-insensitive."""
    _clear_env(monkeypatch)

    data_dir = tmp_path / "adjusted"
    monkeypatch.setenv("port", "9005")
    monkeypatch.setenv("DEBUG", "true")
    monkeypatch.setenv("REDIS_ENABLED", "true")
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("database_url", "postgresql://user:pass@db:5432/trader_test")
    monkeypatch.setenv("REDIS_TTL", "7200")
    monkeypatch.setenv("TOP_N", "4")
    monkeypatch.setenv("BOTTOM_N", "2")

    settings = Settings(_env_file=None)

    assert settings.port == 9005
    assert settings.debug is True
    assert settings.redis_enabled is True
    assert settings.data_dir == data_dir
    assert settings.database_url == "postgresql://user:pass@db:5432/trader_test"
    assert settings.redis_ttl == 7200
    assert settings.top_n == 4
    assert settings.bottom_n == 2
