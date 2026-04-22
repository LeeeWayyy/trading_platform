"""
Market Data Service Configuration

Settings loaded from environment variables.
"""

import os
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Market Data Service settings."""

    # Service Configuration
    service_name: str = "market-data-service"
    port: int = 8004
    log_level: str = "INFO"

    # Alpaca API Configuration
    alpaca_api_key: str
    alpaca_secret_key: str
    alpaca_base_url: str = "https://paper-api.alpaca.markets"  # Paper trading default
    alpaca_data_feed: Literal["iex", "sip", "otc", "boats"] = "iex"
    internal_token_secret: str = ""

    @field_validator("alpaca_data_feed", mode="before")
    @classmethod
    def normalize_alpaca_data_feed(cls, value: object) -> str:
        """Normalize ALPACA_DATA_FEED and enforce supported options.

        Supported values:
        - iex: free feed
        - sip: consolidated tape (entitlement required)
        - otc: OTC symbols feed
        - boats: Blue Ocean ATS feed
        """
        if not isinstance(value, str):
            # Handles None and other non-string types.
            value = str(value or "")
        normalized = value.strip().lower()
        # Default to "iex" when environment value is empty.
        return normalized or "iex"

    def current_internal_token_secret(self) -> str:
        """Return shared internal token secret with live env override support."""
        return os.getenv("INTERNAL_TOKEN_SECRET", "").strip()

    @staticmethod
    def service_internal_token_secret(service_key: str) -> str:
        """Return service-scoped internal token secret from environment."""
        env_name = f"INTERNAL_TOKEN_SECRET_{service_key}"
        return os.getenv(env_name, "").strip()

    # Redis Configuration
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: str | None = None

    # Market Data Configuration
    price_cache_ttl: int = 300  # 5 minutes

    # WebSocket Configuration
    max_reconnect_attempts: int = 10
    reconnect_base_delay: int = 5  # Base delay in seconds

    # Auto-Subscription Configuration
    execution_gateway_url: str = "http://localhost:8002"
    subscription_sync_interval: int = 300  # 5 minutes

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # Ignore extra environment variables
    )


# Global settings instance
# Note: Pydantic BaseSettings loads required fields from environment variables
settings = Settings()  # type: ignore[call-arg]
