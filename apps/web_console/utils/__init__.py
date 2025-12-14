"""Shared utilities for web console.

This module exports:
- Database/Redis connection utilities (db_pool.py)
- Network utilities (re-exported from libs/common/network_utils.py)
"""

# Database/Redis connection utilities
from apps.web_console.utils.db_pool import (  # noqa: F401
    AsyncConnectionAdapter,
    AsyncRedisAdapter,
    get_db_pool,
    get_redis_client,
)

# Re-export network utilities for backward compatibility
from libs.common.network_utils import (  # noqa: F401
    extract_client_ip_from_fastapi,
    extract_user_agent_from_fastapi,
    validate_trusted_proxy,
)
