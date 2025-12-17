"""
Configuration for Signal Service.

This module defines all configurable settings for the signal service using
Pydantic Settings for type-safe configuration management. Settings can be
overridden via environment variables or .env file.

Example:
    >>> from apps.signal_service.config import settings
    >>> print(settings.port)
    8001
    >>> print(settings.tradable_symbols)
    ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'TSLA']

See Also:
    - /docs/ADRs/0004-signal-service-architecture.md for architectural decisions
    - /docs/IMPLEMENTATION_GUIDES/t3-signal-service.md for deployment guide
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Signal service configuration settings.

    All settings can be overridden via environment variables with uppercase names.
    For example, DATABASE_URL environment variable overrides database_url setting.

    Attributes:
        host: Service bind address (default: 0.0.0.0 for all interfaces)
        port: Service port (default: 8001)
        debug: Enable debug mode with auto-reload (default: False)
        database_url: Postgres connection string
        data_dir: Path to T1 adjusted data directory
        default_strategy: Default strategy name for model loading
        tradable_symbols: List of symbols to generate signals for
        top_n: Number of long positions in portfolio
        bottom_n: Number of short positions in portfolio
        model_reload_interval_seconds: How often to check for model updates
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR)

    Example:
        # Via environment variables
        export DATABASE_URL="postgresql://user:pass@localhost/db"
        export TOP_N=5
        export BOTTOM_N=5

        # Via .env file
        # DATABASE_URL=postgresql://user:pass@localhost/db
        # TOP_N=5

        # In code
        from apps.signal_service.config import settings
        print(settings.top_n)  # 5
    """

    # ========================================================================
    # Service Configuration
    # ========================================================================

    host: str = "0.0.0.0"
    """Service bind address. 0.0.0.0 = all interfaces, 127.0.0.1 = localhost only."""

    port: int = 8001
    """Service port. Must not conflict with other services (execution gateway = 8002, etc.)."""

    debug: bool = False
    """Enable debug mode with auto-reload. Never use in production."""

    testing: bool = False
    """
    Enable testing mode for CI/E2E tests.

    When enabled:
        - Service starts without requiring an active model in database
        - Model loading failures are logged as warnings instead of crashing
        - Allows health checks and endpoint testing without model data

    When disabled:
        - Service requires active model to start (production behavior)
        - Model loading failures cause startup failure

    Example:
        export TESTING=true   # CI/E2E environments
        export TESTING=false  # Production (default)

    Notes:
        - Only use in CI/E2E test environments
        - Signal generation endpoints return 500 when no model loaded
        - Health check passes even without model
    """

    # ========================================================================
    # Database Configuration
    # ========================================================================

    database_url: str = "postgresql://trader:trader@localhost:5433/trader"
    """
    Postgres connection string.

    Format: postgresql://[user[:password]@][host][:port][/dbname]

    Example:
        postgresql://trader:trader@localhost:5433/trader

    Notes:
        - Must have model_registry table (see migrations/001_create_model_registry.sql)
        - Service won't start if database is unreachable
    """

    # ========================================================================
    # Data Configuration
    # ========================================================================

    data_dir: Path = Path("data/adjusted")
    """
    Path to T1 adjusted data directory.

    Directory structure:
        data/adjusted/
        ├── 2024-01-01/
        │   ├── AAPL.parquet
        │   ├── MSFT.parquet
        │   └── ...
        └── 2024-01-02/
            └── ...

    Notes:
        - Must contain T1 adjusted Parquet files (backward-adjusted for splits/dividends)
        - Used by T1DataProvider to fetch market data
        - Relative paths resolved from project root
    """

    # ========================================================================
    # Strategy Configuration
    # ========================================================================

    default_strategy: str = "alpha_baseline"
    """
    Default strategy name for model loading.

    This determines which strategy's active model to load on startup.
    Must match strategy_name in model_registry table.

    Example:
        - "alpha_baseline" (initial baseline strategy from T2)
        - "alpha_v2" (future improved strategy)
    """

    tradable_symbols: list[str] = ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA"]
    """
    List of tradable symbols for signal generation.

    These are the symbols the strategy can generate signals for. Should match
    the universe used during model training.

    Notes:
        - Only liquid, well-covered stocks recommended
        - Can be overridden via API query parameter
        - Expanding this list requires retraining model on larger universe
    """

    # ========================================================================
    # Portfolio Configuration
    # ========================================================================

    top_n: int = 3
    """
    Number of long positions (stocks with highest predicted returns).

    The portfolio strategy allocates equal weight to top N stocks by predicted return.
    Example: top_n=3 means 33.3% each for top 3 stocks.

    Notes:
        - Must be > 0
        - Total positions = top_n + bottom_n
        - Larger N = more diversification but diluted conviction
    """

    bottom_n: int = 3
    """
    Number of short positions (stocks with lowest predicted returns).

    The portfolio strategy shorts equal weight to bottom N stocks by predicted return.
    Example: bottom_n=3 means -33.3% each for bottom 3 stocks.

    Notes:
        - Must be > 0
        - Shorting requires margin account
        - bottom_n=0 for long-only strategy (not recommended, reduces alpha)
    """

    # ========================================================================
    # Model Reload Configuration
    # ========================================================================

    model_reload_interval_seconds: int = 300
    """
    Interval for checking model registry for updates (in seconds).

    Background task polls database every N seconds to check if active model
    changed. If changed, reloads model without service restart (hot reload).

    Notes:
        - Default 300s = 5 minutes
        - Lower values = faster model updates but more database load
        - Can trigger manual reload via POST /model/reload (bypasses interval)
        - Zero means no background polling (manual reload only)
    """

    # ========================================================================
    # Logging Configuration
    # ========================================================================

    log_level: str = "INFO"
    """
    Logging level.

    Levels (from most to least verbose):
        - DEBUG: Detailed diagnostic information
        - INFO: General informational messages
        - WARNING: Warning messages (potential issues)
        - ERROR: Error messages (failures)

    Example:
        export LOG_LEVEL=DEBUG  # For development
        export LOG_LEVEL=INFO   # For production
    """

    # ========================================================================
    # Redis Configuration (T1.2 - Feature Store & Event Bus)
    # ========================================================================

    redis_enabled: bool = False
    """
    Enable Redis feature caching and event publishing.

    When enabled:
        - Features are cached in Redis for faster retrieval (10x speedup on cache hits)
        - Events are published to Redis channels for event-driven workflows

    When disabled:
        - Service operates normally without caching (graceful degradation)
        - Feature generation takes full time (~50ms per symbol)

    Example:
        export REDIS_ENABLED=true  # Enable Redis features
        export REDIS_ENABLED=false  # Disable Redis (default)

    Notes:
        - Requires Redis server running (redis-server)
        - Service starts successfully even if Redis is unreachable
        - See docs/IMPLEMENTATION_GUIDES/t1.2-redis-integration.md
    """

    redis_host: str = "localhost"
    """
    Redis server hostname.

    Example:
        export REDIS_HOST=localhost  # Local Redis
        export REDIS_HOST=redis.example.com  # Remote Redis

    Notes:
        - Only used if redis_enabled=true
        - Defaults to localhost for development
    """

    redis_port: int = 6379
    """
    Redis server port.

    Example:
        export REDIS_PORT=6379  # Default Redis port

    Notes:
        - Only used if redis_enabled=true
        - Standard Redis port is 6379
    """

    redis_db: int = 0
    """
    Redis database number (0-15).

    Example:
        export REDIS_DB=0  # Production
        export REDIS_DB=1  # Testing (isolate test data)

    Notes:
        - Only used if redis_enabled=true
        - Redis supports 16 databases (0-15)
        - Use different DB for testing to avoid conflicts
    """

    redis_ttl: int = 3600
    """
    Feature cache TTL (time-to-live) in seconds.

    Cached features expire after this duration. Default: 3600s (1 hour).

    Example:
        export REDIS_TTL=3600  # 1 hour (default)
        export REDIS_TTL=7200  # 2 hours
        export REDIS_TTL=1800  # 30 minutes

    Notes:
        - Only used if redis_enabled=true
        - Features are deterministic, but TTL handles data corrections
        - Lower TTL = more cache misses but fresher data
        - Higher TTL = more cache hits but risk of stale data
        - Recommended: 1-2 hours for production
    """

    # ========================================================================
    # Feature Hydration Configuration (T2)
    # ========================================================================

    feature_hydration_enabled: bool = True
    """
    Enable startup hydration of the feature cache.

    When enabled:
        - Service warms the feature cache in the background at startup
        - Health reports "degraded" until hydration completes

    When disabled:
        - No startup hydration runs
        - Service is immediately "healthy" (no readiness gate for hydration)

    Notes:
        - Requires Redis feature cache to be enabled
        - Safe to disable in development or when cache is not needed
    """

    feature_hydration_timeout_seconds: int = 300
    """
    Maximum time to allow for startup feature hydration (seconds).

    If hydration exceeds this timeout, the background task logs a warning
    and exits. The service remains running and no crash occurs.

    Notes:
        - Applies only when feature_hydration_enabled is True
        - Does not block service startup
    """

    # ========================================================================
    # Pydantic Settings Configuration
    # ========================================================================

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,  # DATABASE_URL and database_url both work
        extra="ignore",  # Ignore extra environment variables
    )


# ============================================================================
# Global Settings Instance
# ============================================================================

settings = Settings()
"""
Global settings instance.

Import this singleton instance throughout the application:

Example:
    >>> from apps.signal_service.config import settings
    >>> print(settings.port)
    8001
    >>> print(settings.database_url)
    postgresql://postgres:postgres@localhost:5432/trading_platform

Notes:
    - Settings loaded once on import
    - Changes to environment variables require restart
    - Override via pytest fixtures for testing
"""
