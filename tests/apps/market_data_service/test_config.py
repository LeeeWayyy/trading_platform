import importlib
import sys


def _import_config_module(monkeypatch, **env):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    module_name = "apps.market_data_service.config"
    if module_name in sys.modules:
        del sys.modules[module_name]
    return importlib.import_module(module_name)


def test_settings_defaults_with_explicit_required_fields(monkeypatch):
    config = _import_config_module(
        monkeypatch,
        ALPACA_API_KEY="env-key",
        ALPACA_SECRET_KEY="env-secret",
    )

    settings = config.Settings(alpaca_api_key="key", alpaca_secret_key="secret")

    assert settings.service_name == "market-data-service"
    assert settings.port == 8004
    assert settings.log_level == "INFO"
    assert settings.alpaca_base_url == "https://paper-api.alpaca.markets"
    assert settings.redis_host == "localhost"
    assert settings.redis_port == 6379
    assert settings.redis_db == 0
    assert settings.redis_password is None
    assert settings.price_cache_ttl == 300
    assert settings.max_reconnect_attempts == 10
    assert settings.reconnect_base_delay == 5
    assert settings.execution_gateway_url == "http://localhost:8002"
    assert settings.subscription_sync_interval == 300


def test_settings_env_overrides_case_insensitive(monkeypatch):
    config = _import_config_module(
        monkeypatch,
        ALPACA_API_KEY="env-key",
        ALPACA_SECRET_KEY="env-secret",
        LOG_LEVEL="DEBUG",
        PRICE_CACHE_TTL="120",
    )

    assert config.settings.alpaca_api_key == "env-key"
    assert config.settings.alpaca_secret_key == "env-secret"
    assert config.settings.log_level == "DEBUG"
    assert config.settings.price_cache_ttl == 120


def test_settings_ignores_extra_fields(monkeypatch):
    config = _import_config_module(
        monkeypatch,
        ALPACA_API_KEY="env-key",
        ALPACA_SECRET_KEY="env-secret",
    )

    settings = config.Settings(
        alpaca_api_key="key",
        alpaca_secret_key="secret",
        extra_field="ignored",
    )

    assert not hasattr(settings, "extra_field")
