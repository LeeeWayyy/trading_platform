"""Configuration module for Execution Gateway.

This module centralizes all environment variable parsing and configuration logic,
replacing scattered config parsing in main.py with a single source of truth.

Design Rationale:
    - Single source of truth for configuration
    - Type-safe configuration via dataclass
    - Explicit default values with validation
    - Centralized env parsing logic
    - Easier testing (inject test config)

Usage:
    from apps.execution_gateway.config import get_config

    config = get_config()
    if config.dry_run:
        logger.info("Running in DRY_RUN mode")

See REFACTOR_EXECUTION_GATEWAY_TASK.md Phase 0 for design decisions.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

logger = logging.getLogger(__name__)


# ============================================================================
# Helper Functions
# ============================================================================


def _get_float_env(name: str, default: float) -> float:
    """Parse float from environment variable with fallback to default.

    Args:
        name: Environment variable name
        default: Default value if env var is missing or invalid

    Returns:
        Parsed float value or default
    """
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("Invalid float for %s=%s; using default=%s", name, raw, default)
        return default


def _get_decimal_env(name: str, default: Decimal) -> Decimal:
    """Parse Decimal from environment variable with fallback to default.

    Args:
        name: Environment variable name
        default: Default value if env var is missing or invalid

    Returns:
        Parsed Decimal value or default
    """
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return Decimal(raw)
    except (ValueError, InvalidOperation):
        logger.warning("Invalid decimal for %s=%s; using default=%s", name, raw, default)
        return default


def _get_int_env(name: str, default: int) -> int:
    """Parse int from environment variable with fallback to default.

    Args:
        name: Environment variable name
        default: Default value if env var is missing or invalid

    Returns:
        Parsed int value or default
    """
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("Invalid int for %s=%s; using default=%s", name, raw, default)
        return default


def _get_bool_env_strict(name: str, default: bool) -> bool:
    """Parse boolean from environment variable (strict: only "true").

    This function uses strict parsing (only "true" is True) to match
    the original behavior for most boolean flags.

    Args:
        name: Environment variable name
        default: Default value if env var is missing

    Returns:
        Parsed boolean value or default

    Note:
        Only accepts "true" (case-insensitive) as True
        All other values are False
    """
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.lower() == "true"


def _get_bool_env_permissive(name: str, default: bool) -> bool:
    """Parse boolean from environment variable (permissive: true/yes/on/1).

    This function uses permissive parsing to match the original behavior
    for flags like LIQUIDITY_CHECK_ENABLED and FEATURE_PERFORMANCE_DASHBOARD.

    Args:
        name: Environment variable name
        default: Default value if env var is missing

    Returns:
        Parsed boolean value or default

    Note:
        Accepts: "true", "yes", "on", "1" (case-insensitive) as True
        All other values are False
    """
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.lower() in ("true", "yes", "on", "1")


# ============================================================================
# Configuration Dataclass
# ============================================================================


@dataclass
class ExecutionGatewayConfig:
    """Configuration for Execution Gateway.

    This dataclass holds all configuration values parsed from environment
    variables, providing type safety and explicit defaults.

    Attributes:
        # Core Settings
        log_level: Logging level (default: INFO)
        environment: Environment name (dev, staging, prod)
        strategy_id: Strategy identifier (e.g., "alpha_baseline")
        dry_run: Enable dry-run mode (no broker submission)

        # Alpaca Configuration
        alpaca_base_url: Alpaca API URL
        alpaca_paper: Use paper trading API
        alpaca_data_feed: Alpaca data feed (optional)

        # Circuit Breaker & Safety
        circuit_breaker_enabled: Enable circuit breaker
        liquidity_check_enabled: Enable liquidity checks
        max_slice_pct_of_adv: Max slice size as % of ADV

        # Fat-Finger Thresholds
        fat_finger_max_notional: Max notional value per order
        fat_finger_max_qty: Max quantity per order
        fat_finger_max_adv_pct: Max order size as % of ADV
        fat_finger_max_price_age_seconds: Max age of price data for validation

        # Redis Configuration
        redis_host: Redis hostname
        redis_port: Redis port
        redis_db: Redis database number

        # Performance & Caching
        performance_cache_ttl: Cache TTL for performance data (seconds)
        max_performance_days: Max days of performance history
        feature_performance_dashboard: Enable performance dashboard

        # Strategy Management
        reduce_only_lock_timeout_seconds: Timeout for reduce-only lock
        reduce_only_lock_blocking_seconds: Blocking time for reduce-only lock
        strategy_activity_threshold_seconds: Threshold for inactive strategies

        # Reconciliation
        fills_backfill_limit: Max fills backfill requests in window
        fills_backfill_window_seconds: Rate limit window for fills backfill (seconds)

        # TWAP Legacy
        legacy_twap_interval_seconds: Legacy TWAP slice interval (backward compat)
    """

    # Core Settings
    log_level: str
    environment: str
    strategy_id: str
    dry_run: bool

    # Alpaca Configuration
    alpaca_base_url: str
    alpaca_paper: bool
    alpaca_data_feed: str | None

    # Circuit Breaker & Safety
    circuit_breaker_enabled: bool
    liquidity_check_enabled: bool
    max_slice_pct_of_adv: float

    # Fat-Finger Thresholds
    fat_finger_max_notional: Decimal | None
    fat_finger_max_qty: int | None
    fat_finger_max_adv_pct: Decimal | None
    fat_finger_max_price_age_seconds: int

    # Redis Configuration
    redis_host: str
    redis_port: int
    redis_db: int

    # Performance & Caching
    performance_cache_ttl: int
    max_performance_days: int
    feature_performance_dashboard: bool

    # Strategy Management
    reduce_only_lock_timeout_seconds: int
    reduce_only_lock_blocking_seconds: int
    strategy_activity_threshold_seconds: int

    # Reconciliation
    fills_backfill_limit: int
    fills_backfill_window_seconds: int

    # TWAP Legacy
    legacy_twap_interval_seconds: int


# ============================================================================
# Configuration Defaults
# ============================================================================

# Fat-finger defaults
FAT_FINGER_MAX_NOTIONAL_DEFAULT = Decimal("100000")
FAT_FINGER_MAX_QTY_DEFAULT = 10_000
FAT_FINGER_MAX_ADV_PCT_DEFAULT = Decimal("0.05")
FAT_FINGER_MAX_PRICE_AGE_SECONDS_DEFAULT = 30

# Legacy TWAP slicer interval (seconds). Legacy plans scheduled slices once per minute
# and did not persist the interval, so backward-compatibility fallbacks must only apply
# when callers request the same default pacing.
LEGACY_TWAP_INTERVAL_SECONDS = 60


# ============================================================================
# Configuration Factory
# ============================================================================


def get_config() -> ExecutionGatewayConfig:
    """Load and validate configuration from environment variables.

    This function parses all environment variables, validates values,
    and returns a type-safe configuration object.

    Returns:
        ExecutionGatewayConfig: Validated configuration

    Raises:
        None: Invalid values fall back to defaults with warnings

    Note:
        This function is idempotent and can be called multiple times.
        However, for performance, consider caching the result if called
        frequently in hot paths.
    """
    # Parse fat-finger thresholds with validation
    fat_finger_max_notional_init = _get_decimal_env(
        "FAT_FINGER_MAX_NOTIONAL", FAT_FINGER_MAX_NOTIONAL_DEFAULT
    )
    fat_finger_max_notional: Decimal | None = fat_finger_max_notional_init

    fat_finger_max_qty: int | None = _get_int_env(
        "FAT_FINGER_MAX_QTY", FAT_FINGER_MAX_QTY_DEFAULT
    )

    fat_finger_max_adv_pct_init = _get_decimal_env(
        "FAT_FINGER_MAX_ADV_PCT", FAT_FINGER_MAX_ADV_PCT_DEFAULT
    )
    fat_finger_max_adv_pct: Decimal | None = fat_finger_max_adv_pct_init

    fat_finger_max_price_age_seconds = _get_int_env(
        "FAT_FINGER_MAX_PRICE_AGE_SECONDS", FAT_FINGER_MAX_PRICE_AGE_SECONDS_DEFAULT
    )

    # Validate and apply thresholds
    if fat_finger_max_price_age_seconds <= 0:
        logger.warning(
            "FAT_FINGER_MAX_PRICE_AGE_SECONDS must be > 0; using default=%s",
            FAT_FINGER_MAX_PRICE_AGE_SECONDS_DEFAULT,
        )
        fat_finger_max_price_age_seconds = FAT_FINGER_MAX_PRICE_AGE_SECONDS_DEFAULT

    if fat_finger_max_notional_init <= 0:
        logger.warning(
            "FAT_FINGER_MAX_NOTIONAL must be > 0; disabling notional threshold",
            extra={"fat_finger_max_notional": str(fat_finger_max_notional)},
        )
        fat_finger_max_notional = None

    if fat_finger_max_qty is not None and fat_finger_max_qty <= 0:
        logger.warning(
            "FAT_FINGER_MAX_QTY must be > 0; disabling qty threshold",
            extra={"fat_finger_max_qty": fat_finger_max_qty},
        )
        fat_finger_max_qty = None

    if fat_finger_max_adv_pct_init <= 0 or fat_finger_max_adv_pct_init > 1:
        logger.warning(
            "FAT_FINGER_MAX_ADV_PCT must be within (0, 1]; disabling ADV threshold",
            extra={"fat_finger_max_adv_pct": str(fat_finger_max_adv_pct)},
        )
        fat_finger_max_adv_pct = None

    # Parse max slice % of ADV with validation
    max_slice_pct_of_adv = _get_float_env("MAX_SLICE_PCT_OF_ADV", 0.01)
    if max_slice_pct_of_adv <= 0:
        logger.warning(
            "MAX_SLICE_PCT_OF_ADV must be > 0; falling back to default=0.01",
            extra={"max_slice_pct_of_adv": max_slice_pct_of_adv},
        )
        max_slice_pct_of_adv = 0.01

    # Parse Alpaca data feed (strip whitespace, convert empty to None)
    alpaca_data_feed = os.getenv("ALPACA_DATA_FEED", "").strip() or None

    return ExecutionGatewayConfig(
        # Core Settings
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        environment=os.getenv("ENVIRONMENT", "dev"),
        strategy_id=os.getenv("STRATEGY_ID", "alpha_baseline"),
        dry_run=_get_bool_env_strict("DRY_RUN", True),
        # Alpaca Configuration
        alpaca_base_url=os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets"),
        alpaca_paper=_get_bool_env_strict("ALPACA_PAPER", True),
        alpaca_data_feed=alpaca_data_feed,
        # Circuit Breaker & Safety
        circuit_breaker_enabled=_get_bool_env_strict("CIRCUIT_BREAKER_ENABLED", True),
        liquidity_check_enabled=_get_bool_env_permissive("LIQUIDITY_CHECK_ENABLED", True),
        max_slice_pct_of_adv=max_slice_pct_of_adv,
        # Fat-Finger Thresholds
        fat_finger_max_notional=fat_finger_max_notional,
        fat_finger_max_qty=fat_finger_max_qty,
        fat_finger_max_adv_pct=fat_finger_max_adv_pct,
        fat_finger_max_price_age_seconds=fat_finger_max_price_age_seconds,
        # Redis Configuration
        redis_host=os.getenv("REDIS_HOST", "localhost"),
        redis_port=_get_int_env("REDIS_PORT", 6379),
        redis_db=_get_int_env("REDIS_DB", 0),
        # Performance & Caching
        performance_cache_ttl=_get_int_env("PERFORMANCE_CACHE_TTL", 300),
        max_performance_days=_get_int_env("MAX_PERFORMANCE_DAYS", 90),
        feature_performance_dashboard=_get_bool_env_permissive("FEATURE_PERFORMANCE_DASHBOARD", False),
        # Strategy Management
        reduce_only_lock_timeout_seconds=_get_int_env("REDUCE_ONLY_LOCK_TIMEOUT_SECONDS", 30),
        reduce_only_lock_blocking_seconds=_get_int_env("REDUCE_ONLY_LOCK_BLOCKING_SECONDS", 10),
        strategy_activity_threshold_seconds=_get_int_env("STRATEGY_ACTIVITY_THRESHOLD_SECONDS", 86400),
        # Reconciliation (rate limiter settings for fills backfill endpoint)
        fills_backfill_limit=_get_int_env("FILLS_BACKFILL_LIMIT", 2),
        fills_backfill_window_seconds=_get_int_env("FILLS_BACKFILL_WINDOW_SECONDS", 300),
        # TWAP Legacy
        legacy_twap_interval_seconds=LEGACY_TWAP_INTERVAL_SECONDS,
    )


# Singleton configuration instance (initialized on first import)
_config_instance: ExecutionGatewayConfig | None = None


def get_config_cached() -> ExecutionGatewayConfig:
    """Get cached configuration instance (singleton pattern).

    This function returns a cached configuration instance to avoid
    repeated environment variable parsing.

    Returns:
        ExecutionGatewayConfig: Cached configuration instance

    Note:
        The configuration is immutable after initialization. If you need
        to reload configuration (e.g., for testing), clear the cache first
        by setting _config_instance = None.
    """
    global _config_instance
    if _config_instance is None:
        _config_instance = get_config()
    return _config_instance
