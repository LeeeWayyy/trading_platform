"""
Tests for market data service configuration.

Covers:
- Default values
- Environment variable overrides
- Required field validation
- Type coercion
- Redis configuration
- WebSocket configuration
- Auto-subscription configuration
"""

import importlib
import sys

import pytest
from pydantic import ValidationError


def _import_config_module(monkeypatch, **env):
    """Helper to import config module with environment variables set."""
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    module_name = "apps.market_data_service.config"
    if module_name in sys.modules:
        del sys.modules[module_name]
    return importlib.import_module(module_name)


class TestSettingsDefaults:
    """Tests for default configuration values."""

    def test_settings_defaults_with_explicit_required_fields(self, monkeypatch):
        """Test all default values when only required fields provided."""
        config = _import_config_module(
            monkeypatch,
            ALPACA_API_KEY="env-key",
            ALPACA_SECRET_KEY="env-secret",
        )

        settings = config.Settings(alpaca_api_key="key", alpaca_secret_key="secret")

        # Service defaults
        assert settings.service_name == "market-data-service"
        assert settings.port == 8004
        assert settings.log_level == "INFO"

        # Alpaca defaults
        assert settings.alpaca_base_url == "https://paper-api.alpaca.markets"

        # Redis defaults
        assert settings.redis_host == "localhost"
        assert settings.redis_port == 6379
        assert settings.redis_db == 0
        assert settings.redis_password is None

        # Market data defaults
        assert settings.price_cache_ttl == 300

        # WebSocket defaults
        assert settings.max_reconnect_attempts == 10
        assert settings.reconnect_base_delay == 5

        # Auto-subscription defaults
        assert settings.execution_gateway_url == "http://localhost:8002"
        assert settings.subscription_sync_interval == 300

    def test_service_name_default(self, monkeypatch):
        """Test service_name default value."""
        config = _import_config_module(
            monkeypatch,
            ALPACA_API_KEY="key",
            ALPACA_SECRET_KEY="secret",
        )

        assert config.settings.service_name == "market-data-service"

    def test_port_default(self, monkeypatch):
        """Test port default value."""
        config = _import_config_module(
            monkeypatch,
            ALPACA_API_KEY="key",
            ALPACA_SECRET_KEY="secret",
        )

        assert config.settings.port == 8004

    def test_log_level_default(self, monkeypatch):
        """Test log_level default value."""
        config = _import_config_module(
            monkeypatch,
            ALPACA_API_KEY="key",
            ALPACA_SECRET_KEY="secret",
        )

        assert config.settings.log_level == "INFO"


class TestRequiredFields:
    """Tests for required field validation."""

    def test_missing_alpaca_api_key(self, monkeypatch):
        """Test that missing alpaca_api_key raises ValidationError."""
        # Clear all Alpaca env vars and set only secret
        monkeypatch.delenv("ALPACA_API_KEY", raising=False)
        monkeypatch.setenv("ALPACA_SECRET_KEY", "secret")

        from apps.market_data_service.config import Settings

        with pytest.raises(ValidationError) as exc_info:
            Settings()

        errors = exc_info.value.errors()
        assert any(e["loc"] == ("alpaca_api_key",) for e in errors)

    def test_missing_alpaca_secret_key(self, monkeypatch):
        """Test that missing alpaca_secret_key raises ValidationError."""
        # Clear all Alpaca env vars and set only key
        monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
        monkeypatch.setenv("ALPACA_API_KEY", "key")

        from apps.market_data_service.config import Settings

        with pytest.raises(ValidationError) as exc_info:
            Settings()

        errors = exc_info.value.errors()
        assert any(e["loc"] == ("alpaca_secret_key",) for e in errors)

    def test_required_fields_from_env(self, monkeypatch):
        """Test that required fields can be loaded from environment."""
        config = _import_config_module(
            monkeypatch,
            ALPACA_API_KEY="env-key",
            ALPACA_SECRET_KEY="env-secret",
        )

        assert config.settings.alpaca_api_key == "env-key"
        assert config.settings.alpaca_secret_key == "env-secret"


class TestEnvironmentOverrides:
    """Tests for environment variable overrides."""

    def test_settings_env_overrides_case_insensitive(self, monkeypatch):
        """Test that environment variables override defaults (case-insensitive)."""
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

    def test_port_env_override(self, monkeypatch):
        """Test port override from environment."""
        config = _import_config_module(
            monkeypatch,
            ALPACA_API_KEY="key",
            ALPACA_SECRET_KEY="secret",
            PORT="9999",
        )

        assert config.settings.port == 9999

    def test_alpaca_base_url_override(self, monkeypatch):
        """Test Alpaca base URL override for live trading."""
        config = _import_config_module(
            monkeypatch,
            ALPACA_API_KEY="key",
            ALPACA_SECRET_KEY="secret",
            ALPACA_BASE_URL="https://api.alpaca.markets",
        )

        assert config.settings.alpaca_base_url == "https://api.alpaca.markets"

    def test_redis_config_overrides(self, monkeypatch):
        """Test Redis configuration overrides."""
        config = _import_config_module(
            monkeypatch,
            ALPACA_API_KEY="key",
            ALPACA_SECRET_KEY="secret",
            REDIS_HOST="redis.example.com",
            REDIS_PORT="6380",
            REDIS_DB="1",
            REDIS_PASSWORD="redis-pass",
        )

        assert config.settings.redis_host == "redis.example.com"
        assert config.settings.redis_port == 6380
        assert config.settings.redis_db == 1
        assert config.settings.redis_password == "redis-pass"

    def test_websocket_config_overrides(self, monkeypatch):
        """Test WebSocket configuration overrides."""
        config = _import_config_module(
            monkeypatch,
            ALPACA_API_KEY="key",
            ALPACA_SECRET_KEY="secret",
            MAX_RECONNECT_ATTEMPTS="20",
            RECONNECT_BASE_DELAY="10",
        )

        assert config.settings.max_reconnect_attempts == 20
        assert config.settings.reconnect_base_delay == 10

    def test_subscription_config_overrides(self, monkeypatch):
        """Test auto-subscription configuration overrides."""
        config = _import_config_module(
            monkeypatch,
            ALPACA_API_KEY="key",
            ALPACA_SECRET_KEY="secret",
            EXECUTION_GATEWAY_URL="http://gateway:8000",
            SUBSCRIPTION_SYNC_INTERVAL="600",
        )

        assert config.settings.execution_gateway_url == "http://gateway:8000"
        assert config.settings.subscription_sync_interval == 600


class TestTypeCoercion:
    """Tests for type coercion from environment variables."""

    def test_int_type_coercion(self, monkeypatch):
        """Test integer fields coerce string env vars."""
        config = _import_config_module(
            monkeypatch,
            ALPACA_API_KEY="key",
            ALPACA_SECRET_KEY="secret",
            PORT="8005",
            REDIS_PORT="6380",
            REDIS_DB="2",
            PRICE_CACHE_TTL="600",
            MAX_RECONNECT_ATTEMPTS="15",
            RECONNECT_BASE_DELAY="8",
            SUBSCRIPTION_SYNC_INTERVAL="450",
        )

        assert config.settings.port == 8005
        assert config.settings.redis_port == 6380
        assert config.settings.redis_db == 2
        assert config.settings.price_cache_ttl == 600
        assert config.settings.max_reconnect_attempts == 15
        assert config.settings.reconnect_base_delay == 8
        assert config.settings.subscription_sync_interval == 450

    def test_optional_string_type(self, monkeypatch):
        """Test optional string field accepts None."""
        config = _import_config_module(
            monkeypatch,
            ALPACA_API_KEY="key",
            ALPACA_SECRET_KEY="secret",
        )

        assert config.settings.redis_password is None

    def test_optional_string_with_value(self, monkeypatch):
        """Test optional string field accepts value."""
        config = _import_config_module(
            monkeypatch,
            ALPACA_API_KEY="key",
            ALPACA_SECRET_KEY="secret",
            REDIS_PASSWORD="pass123",
        )

        assert config.settings.redis_password == "pass123"


class TestSettingsConfigDict:
    """Tests for SettingsConfigDict behavior."""

    def test_settings_ignores_extra_fields(self, monkeypatch):
        """Test that extra fields are ignored per extra='ignore'."""
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

    def test_extra_env_vars_ignored(self, monkeypatch):
        """Test that extra environment variables are ignored."""
        config = _import_config_module(
            monkeypatch,
            ALPACA_API_KEY="key",
            ALPACA_SECRET_KEY="secret",
            SOME_RANDOM_VAR="ignored",
            ANOTHER_VAR="also-ignored",
        )

        assert not hasattr(config.settings, "some_random_var")
        assert not hasattr(config.settings, "another_var")

    def test_case_insensitive_env_vars(self, monkeypatch):
        """Test case-insensitive environment variable matching."""
        config = _import_config_module(
            monkeypatch,
            alpaca_api_key="lower-key",  # lowercase
            ALPACA_SECRET_KEY="upper-secret",  # uppercase
            Log_Level="MixedCase",  # mixed case
        )

        assert config.settings.alpaca_api_key == "lower-key"
        assert config.settings.alpaca_secret_key == "upper-secret"
        assert config.settings.log_level == "MixedCase"


class TestGlobalSettingsInstance:
    """Tests for global settings instance."""

    def test_global_settings_instance_exists(self, monkeypatch):
        """Test that global settings instance is created."""
        config = _import_config_module(
            monkeypatch,
            ALPACA_API_KEY="key",
            ALPACA_SECRET_KEY="secret",
        )

        assert hasattr(config, "settings")
        assert config.settings.service_name == "market-data-service"

    def test_global_settings_uses_env_vars(self, monkeypatch):
        """Test that global settings loads from environment."""
        config = _import_config_module(
            monkeypatch,
            ALPACA_API_KEY="global-key",
            ALPACA_SECRET_KEY="global-secret",
            PORT="8888",
        )

        assert config.settings.alpaca_api_key == "global-key"
        assert config.settings.alpaca_secret_key == "global-secret"
        assert config.settings.port == 8888


class TestRedisConfiguration:
    """Tests specific to Redis configuration."""

    def test_redis_no_password_default(self, monkeypatch):
        """Test Redis password defaults to None."""
        config = _import_config_module(
            monkeypatch,
            ALPACA_API_KEY="key",
            ALPACA_SECRET_KEY="secret",
        )

        assert config.settings.redis_password is None

    def test_redis_with_password(self, monkeypatch):
        """Test Redis with password set."""
        config = _import_config_module(
            monkeypatch,
            ALPACA_API_KEY="key",
            ALPACA_SECRET_KEY="secret",
            REDIS_PASSWORD="secure-pass",
        )

        assert config.settings.redis_password == "secure-pass"

    def test_redis_default_db(self, monkeypatch):
        """Test Redis database defaults to 0."""
        config = _import_config_module(
            monkeypatch,
            ALPACA_API_KEY="key",
            ALPACA_SECRET_KEY="secret",
        )

        assert config.settings.redis_db == 0

    def test_redis_custom_db(self, monkeypatch):
        """Test Redis custom database selection."""
        config = _import_config_module(
            monkeypatch,
            ALPACA_API_KEY="key",
            ALPACA_SECRET_KEY="secret",
            REDIS_DB="5",
        )

        assert config.settings.redis_db == 5


class TestMarketDataConfiguration:
    """Tests specific to market data configuration."""

    def test_price_cache_ttl_default(self, monkeypatch):
        """Test price cache TTL defaults to 300 seconds."""
        config = _import_config_module(
            monkeypatch,
            ALPACA_API_KEY="key",
            ALPACA_SECRET_KEY="secret",
        )

        assert config.settings.price_cache_ttl == 300

    def test_price_cache_ttl_override(self, monkeypatch):
        """Test price cache TTL can be overridden."""
        config = _import_config_module(
            monkeypatch,
            ALPACA_API_KEY="key",
            ALPACA_SECRET_KEY="secret",
            PRICE_CACHE_TTL="60",
        )

        assert config.settings.price_cache_ttl == 60


class TestWebSocketConfiguration:
    """Tests specific to WebSocket configuration."""

    def test_reconnect_attempts_default(self, monkeypatch):
        """Test max reconnect attempts defaults to 10."""
        config = _import_config_module(
            monkeypatch,
            ALPACA_API_KEY="key",
            ALPACA_SECRET_KEY="secret",
        )

        assert config.settings.max_reconnect_attempts == 10

    def test_reconnect_delay_default(self, monkeypatch):
        """Test reconnect base delay defaults to 5 seconds."""
        config = _import_config_module(
            monkeypatch,
            ALPACA_API_KEY="key",
            ALPACA_SECRET_KEY="secret",
        )

        assert config.settings.reconnect_base_delay == 5

    def test_reconnect_config_overrides(self, monkeypatch):
        """Test WebSocket reconnect configuration overrides."""
        config = _import_config_module(
            monkeypatch,
            ALPACA_API_KEY="key",
            ALPACA_SECRET_KEY="secret",
            MAX_RECONNECT_ATTEMPTS="25",
            RECONNECT_BASE_DELAY="3",
        )

        assert config.settings.max_reconnect_attempts == 25
        assert config.settings.reconnect_base_delay == 3


class TestAutoSubscriptionConfiguration:
    """Tests specific to auto-subscription configuration."""

    def test_execution_gateway_url_default(self, monkeypatch):
        """Test execution gateway URL defaults to localhost."""
        config = _import_config_module(
            monkeypatch,
            ALPACA_API_KEY="key",
            ALPACA_SECRET_KEY="secret",
        )

        assert config.settings.execution_gateway_url == "http://localhost:8002"

    def test_subscription_sync_interval_default(self, monkeypatch):
        """Test subscription sync interval defaults to 300 seconds."""
        config = _import_config_module(
            monkeypatch,
            ALPACA_API_KEY="key",
            ALPACA_SECRET_KEY="secret",
        )

        assert config.settings.subscription_sync_interval == 300

    def test_subscription_config_overrides(self, monkeypatch):
        """Test auto-subscription configuration overrides."""
        config = _import_config_module(
            monkeypatch,
            ALPACA_API_KEY="key",
            ALPACA_SECRET_KEY="secret",
            EXECUTION_GATEWAY_URL="http://prod-gateway:8002",
            SUBSCRIPTION_SYNC_INTERVAL="180",
        )

        assert config.settings.execution_gateway_url == "http://prod-gateway:8002"
        assert config.settings.subscription_sync_interval == 180
