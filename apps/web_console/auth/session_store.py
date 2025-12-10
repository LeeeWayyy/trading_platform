"""Redis session store for OAuth2 tokens (Component 1 design specification).

Component 1 of P2T3 Phase 3 (OAuth2/OIDC Authentication).

This module provides a Redis-backed session store with AES-256-GCM encryption
for OAuth2 tokens. Tokens are NEVER stored in Streamlit session_state to prevent
XSS attacks and process memory exposure.

Security Design:
- Redis DB 1 (dedicated, isolated from features/metrics)
- AES-256-GCM encryption with 32-byte key from AWS Secrets Manager
- Dual-key rotation support for zero-downtime key updates
- Session binding via IP + User-Agent hash
- 4-hour absolute timeout (Redis TTL)
- 15-minute idle timeout (application-enforced)

References:
- docs/TASKS/P2T3-Phase3_Component1_Plan.md (Task 5)
- docs/ARCHITECTURE/redis-session-schema.md
- docs/TASKS/P2T3_Phase3_FINAL_PLAN.md (lines 29-59)
"""

import base64
import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any

import redis.asyncio
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class SessionData(BaseModel):
    """OAuth2 session data stored in Redis.

    Attributes:
        access_token: OAuth2 access token (1-hour expiration)
        refresh_token: OAuth2 refresh token (4-hour max lifetime)
        id_token: OIDC ID token with user identity claims
        user_id: Auth0 user ID (e.g., "auth0|12345")
        email: User email address
        created_at: Session creation timestamp (UTC)
        last_activity: Last request timestamp for idle timeout (UTC)
        ip_address: Client IP address for session binding
        user_agent: Client User-Agent for session binding
        access_token_expires_at: Access token expiry timestamp for auto-refresh (Component 3)
        role: RBAC role (viewer/operator/admin)
        strategies: List of authorized strategy IDs
        session_version: Session invalidation counter (increments on role change)
        step_up_claims: Claims from recent MFA step-up ID token
        step_up_requested_at: When a step-up was initiated (for timeout)
        pending_action: Post-step-up redirect hint
    """

    access_token: str
    refresh_token: str
    id_token: str
    user_id: str  # Auth0 user ID (e.g., "auth0|12345")
    email: str
    created_at: datetime
    last_activity: datetime
    ip_address: str
    user_agent: str
    # NEW: Component 3 - Track token expiry for auto-refresh
    # Optional for backward compatibility with existing sessions (defaults to created_at + 1h)
    access_token_expires_at: datetime | None = None
    # RBAC fields
    role: str = "viewer"
    strategies: list[str] = Field(default_factory=list)
    session_version: int = 1
    # Step-up / MFA fields
    step_up_claims: dict[str, Any] | None = None
    step_up_requested_at: datetime | None = None
    pending_action: str | None = None


class RedisSessionStore:
    """Redis-backed session store with AES-256-GCM encryption.

    Design:
    - Redis DB 1 (dedicated, isolated from features/metrics)
    - Key format: session:{session_id}
    - Value: AES-256-GCM encrypted JSON blob of SessionData
    - TTL: 4 hours (absolute timeout, enforced by Redis)
    - Session binding: Hash(session_id + IP + user_agent) prevents hijacking

    Security:
    - Encryption key: 32-byte key from AWS Secrets Manager
    - Dual-key rotation: Primary + secondary key support for zero-downtime rotation
    - Never store tokens in Streamlit session_state (CI validates)

    Example:
        >>> store = RedisSessionStore(redis_client, encryption_key)
        >>> session_data = SessionData(
        ...     access_token="eyJhbGci...",
        ...     refresh_token="v1.MR...",
        ...     id_token="eyJhbGci...",
        ...     user_id="auth0|12345",
        ...     email="trader@example.com",
        ...     created_at=datetime.now(timezone.utc),
        ...     last_activity=datetime.now(timezone.utc),
        ...     ip_address="192.168.1.100",
        ...     user_agent="Mozilla/5.0..."
        ... )
        >>> await store.create_session("abc123", session_data)
        >>> session = await store.get_session("abc123")
    """

    def __init__(
        self,
        redis_client: redis.asyncio.Redis,
        encryption_key: bytes,
        secondary_key: bytes | None = None,
        absolute_timeout_hours: int = 4,
        idle_timeout_minutes: int = 15,
    ):
        """Initialize Redis session store.

        Args:
            redis_client: Redis async client instance configured for DB 1 (sessions).
                         MUST be instantiated with db=1 parameter to ensure isolation.
            encryption_key: 32-byte AES-256 key (primary)
            secondary_key: Optional 32-byte key for rotation fallback
            absolute_timeout_hours: Maximum session lifetime (default 4)
            idle_timeout_minutes: Inactivity timeout (default 15)

        Raises:
            ValueError: If encryption_key is not 32 bytes

        Example:
            >>> redis_client = redis.asyncio.Redis(host="localhost", port=6379, db=1)
            >>> store = RedisSessionStore(redis_client, encryption_key=key)

        Important:
            The caller MUST provide a Redis client configured for DB 1 to ensure
            session isolation from feature cache (DB 0) and metrics (DB 2).
        """
        if len(encryption_key) != 32:
            raise ValueError("Encryption key must be exactly 32 bytes for AES-256")

        if secondary_key and len(secondary_key) != 32:
            raise ValueError("Secondary key must be exactly 32 bytes for AES-256")

        self.redis = redis_client
        self.cipher_primary = AESGCM(encryption_key)
        self.cipher_secondary = AESGCM(secondary_key) if secondary_key else None
        self.absolute_timeout = timedelta(hours=absolute_timeout_hours)
        self.idle_timeout = timedelta(minutes=idle_timeout_minutes)

        logger.info(
            "Redis session store initialized",
            extra={"absolute_timeout_hours": absolute_timeout_hours},
        )

    def _encrypt(self, data: str) -> str:
        """Encrypt data with AES-256-GCM using primary key.

        Args:
            data: Plaintext string to encrypt

        Returns:
            Base64-encoded encrypted blob (nonce + ciphertext + tag)
        """
        nonce = os.urandom(12)  # 96-bit nonce for GCM
        ciphertext = self.cipher_primary.encrypt(nonce, data.encode(), None)

        # Format: base64(nonce + ciphertext)
        # GCM automatically appends 128-bit authentication tag to ciphertext
        return base64.b64encode(nonce + ciphertext).decode()

    def _decrypt(self, encrypted: str) -> str:
        """Decrypt data with dual-key fallback.

        Args:
            encrypted: Base64-encoded encrypted blob

        Returns:
            Decrypted plaintext string

        Raises:
            cryptography.exceptions.InvalidTag: If decryption fails with both keys
        """
        blob = base64.b64decode(encrypted)
        nonce = blob[:12]
        ciphertext = blob[12:]  # Includes 128-bit auth tag at end

        # Try primary key first
        try:
            return self.cipher_primary.decrypt(nonce, ciphertext, None).decode()
        except Exception as e:
            # Fallback to secondary key during rotation
            if self.cipher_secondary:
                logger.warning(
                    "Primary key decryption failed, trying secondary key",
                    extra={"error": str(e)},
                )
                return self.cipher_secondary.decrypt(nonce, ciphertext, None).decode()
            raise

    async def create_session(self, session_id: str, session_data: SessionData) -> None:
        """Create encrypted session in Redis.

        Args:
            session_id: Unique session identifier (HttpOnly cookie value)
            session_data: OAuth2 tokens and user metadata
        """
        # Serialize to JSON
        json_data = session_data.model_dump_json()

        # Encrypt
        encrypted = self._encrypt(json_data)

        # Store in Redis with TTL
        key = f"session:{session_id}"
        ttl_seconds = int(self.absolute_timeout.total_seconds())

        await self.redis.setex(key, ttl_seconds, encrypted)

        logger.info(
            "Session created",
            extra={
                "session_id": session_id[:8] + "...",  # Redact full ID
                "user_id": session_data.user_id,
                "ttl_seconds": ttl_seconds,
            },
        )

    async def get_session(
        self,
        session_id: str,
        current_ip: str | None = None,
        current_user_agent: str | None = None,
        update_activity: bool = True,
    ) -> SessionData | None:
        """Retrieve and decrypt session from Redis with binding validation.

        Args:
            session_id: Session identifier
            current_ip: Current request IP address (for session binding validation)
            current_user_agent: Current request User-Agent (for session binding validation)
            update_activity: If True, update last_activity timestamp

        Returns:
            SessionData if valid, None if expired, not found, or binding mismatch

        Security:
            If current_ip or current_user_agent are provided, they MUST match the
            stored values to prevent session hijacking via stolen cookies.
        """
        key = f"session:{session_id}"
        encrypted = await self.redis.get(key)

        if not encrypted:
            logger.debug(
                "Session not found or expired",
                extra={"session_id": session_id[:8] + "..."},
            )
            return None

        # Decrypt
        try:
            json_data = self._decrypt(encrypted.decode())
            session_data = SessionData.model_validate_json(json_data)
        except Exception as e:
            logger.error(
                "Session decryption failed",
                extra={
                    "session_id": session_id[:8] + "...",
                    "error": str(e),
                },
            )
            return None

        # Session binding validation (prevents session hijacking)
        if current_ip and session_data.ip_address != current_ip:
            logger.warning(
                "Session IP mismatch - possible hijack attempt",
                extra={
                    "session_id": session_id[:8] + "...",
                    "stored_ip": session_data.ip_address,
                    "current_ip": current_ip,
                },
            )
            await self.delete_session(session_id)
            return None

        if current_user_agent and session_data.user_agent != current_user_agent:
            logger.warning(
                "Session User-Agent mismatch - possible hijack attempt",
                extra={
                    "session_id": session_id[:8] + "...",
                    "stored_ua": session_data.user_agent[:50] + "...",
                    "current_ua": current_user_agent[:50] + "...",
                },
            )
            await self.delete_session(session_id)
            return None

        # Check idle timeout
        now = datetime.now(UTC)
        if now - session_data.last_activity > self.idle_timeout:
            logger.info(
                "Session idle timeout",
                extra={
                    "session_id": session_id[:8] + "...",
                    "idle_minutes": (now - session_data.last_activity).total_seconds() / 60,
                },
            )
            await self.delete_session(session_id)
            return None

        # Check absolute timeout (CRITICAL: prevent TTL reset vulnerability)
        if now - session_data.created_at > self.absolute_timeout:
            logger.info(
                "Session absolute timeout",
                extra={
                    "session_id": session_id[:8] + "...",
                    "age_hours": (now - session_data.created_at).total_seconds() / 3600,
                },
            )
            await self.delete_session(session_id)
            return None

        # Update activity timestamp if requested
        # CRITICAL FIX: Only update last_activity via EXPIRE, not full re-write
        # This preserves the absolute timeout (created_at remains unchanged)
        if update_activity:
            session_data.last_activity = now

            # Calculate remaining TTL based on absolute timeout
            remaining_absolute = self.absolute_timeout - (now - session_data.created_at)
            remaining_seconds = max(1, int(remaining_absolute.total_seconds()))  # Minimum 1 second

            # Re-encrypt and store with REMAINING TTL (not full 4 hours!)
            json_data = session_data.model_dump_json()
            encrypted_updated = self._encrypt(json_data)
            await self.redis.setex(key, remaining_seconds, encrypted_updated)

        return session_data

    async def _persist_session(self, session_id: str, session_data: SessionData) -> None:
        """Encrypt and persist session without extending absolute lifetime."""

        key = f"session:{session_id}"
        now = datetime.now(UTC)
        remaining_absolute = self.absolute_timeout - (now - session_data.created_at)
        remaining_seconds = max(1, int(remaining_absolute.total_seconds()))
        encrypted = self._encrypt(session_data.model_dump_json())
        await self.redis.setex(key, remaining_seconds, encrypted)

    async def update_session_fields(self, session_id: str, **updates: object) -> bool:
        """Generic in-place session update (fails closed on missing session)."""

        session = await self.get_session(session_id, update_activity=False)
        if not session:
            return False

        for key, value in updates.items():
            if hasattr(session, key):
                setattr(session, key, value)

        await self._persist_session(session_id, session)
        return True

    async def set_step_up_request(
        self, session_id: str, pending_action: str | None, requested_at: datetime | None
    ) -> bool:
        return await self.update_session_fields(
            session_id,
            pending_action=pending_action,
            step_up_requested_at=requested_at,
            step_up_claims=None,
        )

    async def update_step_up_claims(
        self, session_id: str, step_up_claims: dict[str, Any] | None
    ) -> bool:
        return await self.update_session_fields(
            session_id,
            step_up_claims=step_up_claims,
        )

    async def clear_step_up_state(self, session_id: str) -> bool:
        return await self.update_session_fields(
            session_id,
            step_up_claims=None,
            step_up_requested_at=None,
            pending_action=None,
        )

    async def clear_step_up_request_timestamp(self, session_id: str) -> bool:
        return await self.update_session_fields(
            session_id,
            step_up_requested_at=None,
        )

    async def delete_session(self, session_id: str) -> None:
        """Delete session from Redis.

        Args:
            session_id: Session identifier to delete
        """
        key = f"session:{session_id}"
        deleted = await self.redis.delete(key)

        logger.info(
            "Session deleted",
            extra={
                "session_id": session_id[:8] + "...",
                "existed": deleted > 0,
            },
        )

    async def cleanup_all_sessions(self, prefix: str = "session:") -> int:
        """Delete all OAuth2 sessions (for IdP fallback).

        SAFE IMPLEMENTATION: Uses SCAN + DEL (not FLUSHDB).

        Args:
            prefix: Session key prefix (default: "session:")

        Returns:
            Number of sessions deleted
        """
        cursor = 0
        deleted = 0

        logger.warning(
            "Starting bulk session cleanup",
            extra={"prefix": prefix},
        )

        while True:
            cursor, keys = await self.redis.scan(cursor, match=f"{prefix}*", count=1000)

            if keys:
                deleted_batch = await self.redis.delete(*keys)
                deleted += deleted_batch
                logger.info(
                    "Session cleanup batch",
                    extra={"batch_size": len(keys), "deleted": deleted_batch},
                )

            if cursor == 0:
                break

        logger.warning("Bulk session cleanup complete", extra={"total_deleted": deleted})

        return deleted
