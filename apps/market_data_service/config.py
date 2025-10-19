"""
Market Data Service Configuration

Settings loaded from environment variables.
"""

import os
from typing import Optional

from pydantic_settings import BaseSettings


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

    # Redis Configuration
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: Optional[str] = None

    # Market Data Configuration
    price_cache_ttl: int = 300  # 5 minutes

    # WebSocket Configuration
    max_reconnect_attempts: int = 10
    reconnect_base_delay: int = 5  # Base delay in seconds

    # Auto-Subscription Configuration
    execution_gateway_url: str = "http://localhost:8002"
    subscription_sync_interval: int = 300  # 5 minutes

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False


# Global settings instance
settings = Settings()
