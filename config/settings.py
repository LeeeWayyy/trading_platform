"""
Application settings loaded from environment variables.

Uses Pydantic Settings for type-safe configuration with validation.
All settings can be overridden via environment variables or .env file.
"""

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application configuration.

    All settings are loaded from environment variables or .env file.
    See .env.example for available options.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # Ignore extraneous env vars from broader platform configs
    )

    # Alpaca API Configuration
    alpaca_api_key_id: str = Field(
        default="",
        description="Alpaca API key ID",
    )
    alpaca_api_secret_key: str = Field(
        default="",
        description="Alpaca API secret key",
    )
    alpaca_base_url: str = Field(
        default="https://paper-api.alpaca.markets",
        description="Alpaca API base URL (paper or live)",
    )

    # Database Configuration
    database_url: str = Field(
        default="postgresql://trader:trader@localhost:5433/trader",
        description="PostgreSQL connection string (use postgresql:// for psycopg3)",
    )

    # Redis Configuration
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection string",
    )

    # Application Configuration
    strategy_id: str = Field(
        default="alpha_baseline",
        description="Active strategy identifier",
    )
    dry_run: bool = Field(
        default=True,
        description="If True, log orders but don't submit to broker",
    )

    # Data Pipeline Configuration
    data_freshness_minutes: int = Field(
        default=30,
        ge=1,
        le=1440,  # Max 24 hours
        description="Maximum age of data in minutes before considered stale",
    )
    outlier_threshold: float = Field(
        default=0.30,
        ge=0.01,
        le=1.0,
        description="Threshold for detecting price outliers (as fraction, e.g., 0.30 = 30%)",
    )

    # Logging Configuration
    log_level: str = Field(
        default="INFO",
        description="Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)",
    )

    # Internal Token Authentication
    # For service-to-service HMAC authentication (X-Internal-Token header)
    internal_token_secret: SecretStr = Field(
        default=SecretStr(""),
        description="HMAC secret for X-Internal-Token validation (empty disables validation)",
    )
    internal_token_required: bool = Field(
        default=True,
        description="Require X-Internal-Token validation for internal headers",
    )
    internal_token_timestamp_tolerance_seconds: int = Field(
        default=300,
        ge=0,
        le=3600,
        description="Max timestamp skew allowed for internal token validation (±seconds)",
    )

    # Data Providers Configuration (P6T10)
    crsp_data_dir: str = Field(
        default="data/crsp",
        description="Directory containing CRSP parquet files (daily_returns, index_constituents)",
    )
    ff_data_dir: str = Field(
        default="data/fama_french",
        description="Directory containing Fama-French factor parquet files",
    )


@lru_cache
def get_settings() -> Settings:
    """
    Get cached settings instance.

    Uses lru_cache to ensure settings are loaded only once.

    Returns:
        Settings instance with all configuration loaded.

    Example:
        >>> settings = get_settings()
        >>> print(settings.strategy_id)
        'alpha_baseline'
    """
    return Settings()
