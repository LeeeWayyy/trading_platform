"""API key generation, hashing, validation, and revocation helpers."""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import re
import secrets
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel
from redis.exceptions import RedisError

from libs.web_console_auth.db import acquire_connection

logger = logging.getLogger(__name__)

# Constants
REVOKED_KEY_CACHE_TTL = 300  # 5 minutes
KEY_PREFIX_PATTERN = re.compile(r"^tp_live_[a-zA-Z0-9_-]{8}$")

_BASE64_KEY_LENGTH = 43
_DEBOUNCE_SECONDS = 60


class ApiKeyScopes(BaseModel):
    """Scopes that control API key permissions."""

    read_positions: bool = False
    read_orders: bool = False
    write_orders: bool = False
    read_strategies: bool = False


def _generate_base64_key() -> str:
    """Generate an unpadded base64url string from 32 random bytes (43 chars)."""
    random_bytes = secrets.token_bytes(32)
    encoded = base64.urlsafe_b64encode(random_bytes).decode("utf-8").rstrip("=")
    return encoded


def generate_api_key() -> tuple[str, str, str]:
    """Generate a new API key.

    Returns:
        full_key: 32 random bytes, base64url encoded (43 chars, no padding)
        prefix: tp_live_{first8chars of base64url}
        salt: 16 random bytes, hex encoded (32 chars)
    """
    # base64url only produces [A-Za-z0-9_-], which KEY_PREFIX_PATTERN accepts
    full_key = _generate_base64_key()
    prefix = f"tp_live_{full_key[:8]}"
    salt = secrets.token_bytes(16).hex()
    return full_key, prefix, salt


def hash_api_key(key: str, salt: str) -> str:
    """Return SHA-256 hash (hex digest) of the key with salt."""
    digest = hashlib.sha256(f"{salt}{key}".encode()).hexdigest()
    return digest


def validate_api_key(key: str, key_hash: str, key_salt: str) -> bool:
    """Verify key matches stored hash using timing-safe comparison."""
    expected = hash_api_key(key, key_salt)
    return hmac.compare_digest(expected, key_hash)


def parse_key_prefix(key: str) -> str | None:
    """Extract the key prefix from a full key or return None if invalid."""
    if not key:
        return None

    candidate_key = key.rstrip("=")
    if len(candidate_key) != _BASE64_KEY_LENGTH:
        return None

    prefix = f"tp_live_{candidate_key[:8]}"
    if KEY_PREFIX_PATTERN.match(prefix):
        return prefix
    return None


async def is_key_revoked(prefix: str, redis_client: Any, db_pool: Any) -> bool:
    """Check revocation status with cache-first lookup and DB fallback."""
    cache_key = f"api_key_revoked:{prefix}"

    if redis_client:
        try:
            if await redis_client.exists(cache_key):
                return True
        except RedisError as exc:  # pragma: no cover - defensive logging path
            logger.warning(
                "redis_revocation_check_failed",
                extra={"key_prefix": prefix, "error": str(exc)},
            )

    async with acquire_connection(db_pool) as conn:
        cursor = await conn.execute(
            "SELECT revoked_at FROM api_keys WHERE key_prefix = %s",
            (prefix,),
        )
        row = await cursor.fetchone()

    revoked_at = None
    if row:
        revoked_at = row["revoked_at"] if isinstance(row, dict) else row[0]

    if revoked_at:
        if redis_client:
            try:
                await redis_client.setex(cache_key, REVOKED_KEY_CACHE_TTL, "1")
            except RedisError as exc:  # pragma: no cover - defensive logging path
                logger.warning(
                    "redis_revocation_cache_set_failed",
                    extra={"key_prefix": prefix, "error": str(exc)},
                )
        return True

    return False


async def update_last_used(prefix: str, db_pool: Any, redis_client: Any) -> None:
    """Update last_used_at with 1-minute debounce (Redis backed when available).

    Uses atomic SET NX EX to avoid race conditions: if key doesn't exist, set it
    with expiry and proceed with DB update; if it exists, debounce (skip update).
    """
    debounce_key = f"api_key_last_used:{prefix}"
    current_minute = datetime.now(UTC).replace(second=0, microsecond=0)

    if redis_client:
        try:
            # Atomic: set key only if not exists, with expiry
            # Returns True if set, False/None if key already exists
            was_set = await redis_client.set(debounce_key, "1", nx=True, ex=_DEBOUNCE_SECONDS)
            if not was_set:
                # Key existed, debounce - skip DB update
                return
        except RedisError as exc:  # pragma: no cover - defensive logging path
            logger.warning(
                "redis_last_used_debounce_failed",
                extra={"key_prefix": prefix, "error": str(exc)},
            )

    async with acquire_connection(db_pool) as conn:
        await conn.execute(
            "UPDATE api_keys SET last_used_at = %s WHERE key_prefix = %s",
            (current_minute, prefix),
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
