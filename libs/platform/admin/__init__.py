"""
Admin utilities package.

Exports API key helpers used by the admin dashboard and gateway auth.
"""

from .api_keys import (
    KEY_PREFIX_PATTERN,
    REVOKED_KEY_CACHE_TTL,
    ApiKeyScopes,
    generate_api_key,
    hash_api_key,
    is_key_revoked,
    parse_key_prefix,
    update_last_used,
    validate_api_key,
)

__all__ = [
    "ApiKeyScopes",
    "KEY_PREFIX_PATTERN",
    "REVOKED_KEY_CACHE_TTL",
    "generate_api_key",
    "hash_api_key",
    "is_key_revoked",
    "parse_key_prefix",
    "update_last_used",
    "validate_api_key",
]
