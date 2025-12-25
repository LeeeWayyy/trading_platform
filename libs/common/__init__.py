"""Common utilities and exceptions."""

from libs.common.exceptions import (
    DataQualityError,
    OutlierError,
    StalenessError,
    TradingPlatformError,
)
from libs.common.file_utils import hash_file_sha256
from libs.common.rate_limit_dependency import (
    RateLimitConfig,
    rate_limit,
    rate_limit_api_checks_total,
    rate_limit_bypass_total,
    rate_limit_redis_timeout_total,
)
from libs.common.schemas import TimestampSerializerMixin
from libs.common.secrets import (
    CRITICAL_SECRETS,
    close_secret_manager,
    get_optional_secret,
    get_optional_secret_or_none,
    get_path_secret,
    get_required_secret,
    get_secret_manager,
    get_secret_uncached,
    invalidate_secret,
    refresh_secrets,
    validate_required_secrets,
)

__all__ = [
    "TradingPlatformError",
    "DataQualityError",
    "StalenessError",
    "OutlierError",
    "TimestampSerializerMixin",
    "hash_file_sha256",
    "RateLimitConfig",
    "rate_limit",
    "rate_limit_api_checks_total",
    "rate_limit_bypass_total",
    "rate_limit_redis_timeout_total",
    # Secrets management
    "CRITICAL_SECRETS",
    "close_secret_manager",
    "get_optional_secret",
    "get_optional_secret_or_none",
    "get_path_secret",
    "get_required_secret",
    "get_secret_manager",
    "get_secret_uncached",
    "invalidate_secret",
    "refresh_secrets",
    "validate_required_secrets",
]
