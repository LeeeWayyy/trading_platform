"""Session management with rate limiting and security features."""

import hashlib
import logging
import time
import uuid
from typing import Any, cast

from redis import Redis

from libs.web_console_auth.config import AuthConfig
from libs.web_console_auth.exceptions import (
    InvalidTokenError,
    RateLimitExceededError,
)
from libs.web_console_auth.jwt_manager import JWTManager

logger = logging.getLogger(__name__)


class SessionManager:
    """Manages user sessions with security features.

    Features:
    - Session creation with access + refresh token pairs
    - Session binding (IP + User Agent fingerprinting)
    - Session limits (max 3 concurrent per user)
    - Token refresh with rotation
    - Rate limiting (5 attempts/15min per IP)
    - Secure cookie parameter helpers

    Security Notes:
    - All authentication events logged with structured fields (Gemini Finding #2)
    - Session binding prevents token theft/MITM (configurable strict mode)
    - Rate limiting prevents brute force attacks (Gemini Finding #1)
    """

    def __init__(
        self, redis_client: Redis, jwt_manager: JWTManager, auth_config: AuthConfig
    ) -> None:
        """Initialize session manager.

        Args:
            redis_client: Redis client for session storage
            jwt_manager: JWT manager for token operations
            auth_config: Authentication configuration
        """
        self.redis = redis_client
        self.jwt = jwt_manager
        self.config = auth_config

        logger.info(
            "SessionManager initialized",
            extra={
                "max_sessions": auth_config.max_sessions_per_user,
                "rate_limit_enabled": auth_config.rate_limit_enabled,
                "binding_strict": auth_config.session_binding_strict,
            },
        )

    def create_session(self, user_id: str, client_ip: str, user_agent: str) -> tuple[str, str]:
        """Create new session with access + refresh token pair.

        Args:
            user_id: User identifier
            client_ip: Client IP address
            user_agent: Client User-Agent string

        Returns:
            Tuple of (access_token, refresh_token)

        Raises:
            RateLimitExceededError: If rate limit exceeded for this IP

        Note:
            Enforces session limits (max 3 per user) by automatically evicting
            oldest session when limit exceeded. Eviction is logged for audit.
            All events logged with structured fields for audit trail.
        """
        # Check rate limit
        if self.config.rate_limit_enabled:
            if not self.check_rate_limit(client_ip, "create_session"):
                logger.warning(
                    "rate_limit_exceeded",
                    extra={
                        "ip": client_ip,
                        "action": "create_session",
                        "user_id": user_id,
                    },
                )
                raise RateLimitExceededError(f"Rate limit exceeded for IP: {client_ip}")

        # Generate session ID with user_id prefix for recovery during cleanup
        # Format: {user_id}_{uuid} enables terminate_session to extract user_id
        # when session metadata has expired via TTL
        session_id = f"{user_id}_{uuid.uuid4()}"

        # Generate token pair
        access_token = self.jwt.generate_access_token(user_id, session_id, client_ip, user_agent)
        access_payload = self.jwt.decode_token(access_token)
        access_jti = access_payload["jti"]

        refresh_token = self.jwt.generate_refresh_token(user_id, session_id, access_jti)
        refresh_payload = self.jwt.decode_token(refresh_token)

        # Store session metadata in Redis
        user_agent_hash = hashlib.sha256(user_agent.encode()).hexdigest()
        session_key = f"{self.config.redis_session_prefix}{session_id}"
        session_data = {
            "user_id": user_id,
            "session_id": session_id,
            "ip": client_ip,
            "user_agent_hash": user_agent_hash,
            "access_jti": access_jti,
            "refresh_jti": refresh_payload["jti"],
            "access_exp": str(access_payload["exp"]),    # Store for precise revocation TTL
            "refresh_exp": str(refresh_payload["exp"]),  # Store for precise revocation TTL
            "created_at": int(time.time()),
        }

        # Convert dict to Redis hash
        self.redis.hset(session_key, mapping=session_data)
        self.redis.expire(session_key, self.config.refresh_token_ttl)

        # Track session in user's session index
        self._add_session_to_index(user_id, session_id)

        # Enforce session limit (evict oldest if needed)
        self._enforce_session_limit(user_id)

        logger.info(
            "session_created",
            extra={
                "user_id": user_id,
                "session_id": session_id,
                "ip": client_ip,
                "user_agent_hash": user_agent_hash,
                "access_jti": access_jti,
                "refresh_jti": refresh_payload["jti"],
            },
        )

        return access_token, refresh_token

    def refresh_session(
        self, refresh_token: str, client_ip: str, user_agent: str
    ) -> tuple[str, str]:
        """Refresh session by issuing new access + refresh token pair.

        Args:
            refresh_token: Valid refresh token
            client_ip: Client IP address
            user_agent: Client User-Agent string

        Returns:
            Tuple of (new_access_token, new_refresh_token)

        Raises:
            InvalidTokenError: Refresh token invalid or expired
            InvalidTokenError: Session binding mismatch (IP/UA changed)
            RateLimitExceededError: If rate limit exceeded

        Note:
            Validates session binding if strict mode enabled.
            Rotates both access AND refresh tokens to prevent refresh token replay.
            Old access and refresh tokens are revoked (blacklisted).
        """
        # Check rate limit
        if self.config.rate_limit_enabled:
            if not self.check_rate_limit(client_ip, "refresh_session"):
                logger.warning(
                    "rate_limit_exceeded",
                    extra={
                        "ip": client_ip,
                        "action": "refresh_session",
                    },
                )
                raise RateLimitExceededError(f"Rate limit exceeded for IP: {client_ip}")

        # Validate refresh token
        refresh_payload = self.jwt.validate_token(refresh_token, "refresh")
        user_id = refresh_payload["sub"]
        session_id = refresh_payload["session_id"]
        presented_refresh_jti = refresh_payload["jti"]

        # Verify session binding
        self._verify_session_binding(session_id, client_ip, user_agent)

        # Generate new tokens FIRST (before atomic swap)
        session_key = f"{self.config.redis_session_prefix}{session_id}"

        # Generate new access token
        new_access_token = self.jwt.generate_access_token(
            user_id, session_id, client_ip, user_agent
        )
        new_access_payload = self.jwt.decode_token(new_access_token)
        new_access_jti = new_access_payload["jti"]

        # Generate new refresh token (rotation)
        new_refresh_token = self.jwt.generate_refresh_token(user_id, session_id, new_access_jti)
        new_refresh_payload = self.jwt.decode_token(new_refresh_token)
        new_refresh_jti = new_refresh_payload["jti"]

        # Atomic compare-and-swap using Lua script (prevents concurrent refresh race)
        # Try atomic Lua-based CAS (production), fallback to non-atomic for FakeRedis (tests)
        try:
            old_access_jti, old_access_exp = self._atomic_refresh_cas(
                session_key,
                presented_refresh_jti,
                new_access_jti,
                new_refresh_jti,
                new_access_payload["exp"],
                new_refresh_payload["exp"],
            )
        except Exception as e:
            # Check if this is a FakeRedis "eval not supported" error
            if "unknown command" in str(e) and "eval" in str(e):
                logger.warning(
                    "lua_eval_not_supported_using_fallback",
                    extra={
                        "session_id": session_id,
                        "reason": "FakeRedis or Redis version lacks Lua support",
                    },
                )
                old_access_jti, old_access_exp = self._fallback_refresh_cas(
                    session_key,
                    presented_refresh_jti,
                    new_access_jti,
                    new_refresh_jti,
                    new_access_payload["exp"],
                    new_refresh_payload["exp"],
                )
            else:
                raise

        # Revoke old tokens with precise expirations (after successful atomic swap)
        if old_access_jti:
            # Use actual old access exp if available, fallback to refresh exp
            access_exp_for_revocation = old_access_exp if old_access_exp > 0 else refresh_payload["exp"]
            self.jwt.revoke_token(old_access_jti, access_exp_for_revocation)
        self.jwt.revoke_token(presented_refresh_jti, refresh_payload["exp"])

        logger.info(
            "session_refreshed",
            extra={
                "user_id": user_id,
                "session_id": session_id,
                "new_access_jti": new_access_jti,
                "new_refresh_jti": new_refresh_jti,
                "old_refresh_jti_revoked": presented_refresh_jti,
            },
        )

        return new_access_token, new_refresh_token

    def validate_session(
        self, access_token: str, client_ip: str, user_agent: str
    ) -> dict[str, Any]:
        """Validate session and return user info.

        Args:
            access_token: Access token to validate
            client_ip: Client IP address
            user_agent: Client User-Agent string

        Returns:
            Validated token payload with user info

        Raises:
            InvalidTokenError: Token invalid, expired, or revoked
            InvalidTokenError: Session binding mismatch (IP/UA changed)

        Note:
            Checks token signature, expiration, revocation, and session binding.
        """
        # Validate access token
        payload = self.jwt.validate_token(access_token, "access")
        session_id = payload["session_id"]

        # Verify session binding
        self._verify_session_binding(session_id, client_ip, user_agent)

        logger.debug(
            "session_validated",
            extra={
                "user_id": payload["sub"],
                "session_id": session_id,
                "jti": payload["jti"],
            },
        )

        return payload

    def terminate_session(self, session_id: str) -> None:
        """Terminate session and revoke all associated tokens.

        Args:
            session_id: Session ID to terminate

        Note:
            Revokes both access and refresh tokens for this session.
            Removes session metadata from Redis.
            If session metadata is missing (expired via TTL), still removes
            from index to prevent stale entries blocking session limit enforcement.
        """
        # Get session metadata to extract token JTIs
        session_key = f"{self.config.redis_session_prefix}{session_id}"
        session_data = self.redis.hgetall(session_key)

        if not session_data:
            # Session metadata already expired (natural TTL), but we must still
            # remove from user index to prevent stale entries. Otherwise, session
            # limit enforcement will repeatedly hit missing entries and fail to evict.
            # Extract user_id from session_id format: {user_id}_{uuid}
            # Use rsplit to handle user_ids containing underscores (e.g., "alice_smith")
            try:
                user_id = session_id.rsplit("_", 1)[0] if "_" in session_id else None
                if user_id:
                    self._remove_session_from_index(user_id, session_id)
                    logger.info(
                        "session_index_cleaned",
                        extra={"user_id": user_id, "session_id": session_id},
                    )
            except Exception as e:
                logger.error(
                    "failed_to_clean_expired_session_index",
                    extra={"session_id": session_id, "error": str(e)},
                )
            logger.warning("session_not_found", extra={"session_id": session_id})
            return

        # Decode session data (Redis returns bytes)
        session_dict = cast(dict[bytes, bytes], session_data)
        user_id = session_dict.get(b"user_id", b"").decode()
        access_jti = session_dict.get(b"access_jti", b"").decode()
        refresh_jti = session_dict.get(b"refresh_jti", b"").decode()

        # Get actual token expirations for precise revocation TTL
        # Fallback to max TTL if exp not stored (older sessions)
        now = int(time.time())
        access_exp_str = session_dict.get(b"access_exp", b"").decode()
        refresh_exp_str = session_dict.get(b"refresh_exp", b"").decode()

        access_exp = int(access_exp_str) if access_exp_str else (now + self.config.access_token_ttl)
        refresh_exp = int(refresh_exp_str) if refresh_exp_str else (now + self.config.refresh_token_ttl)

        if access_jti:
            self.jwt.revoke_token(access_jti, access_exp)
        if refresh_jti:
            self.jwt.revoke_token(refresh_jti, refresh_exp)

        # Remove session from user index
        if user_id:
            self._remove_session_from_index(user_id, session_id)

        # Delete session metadata
        self.redis.delete(session_key)

        logger.info(
            "session_terminated",
            extra={
                "user_id": user_id,
                "session_id": session_id,
                "access_jti": access_jti,
                "refresh_jti": refresh_jti,
            },
        )

    def cleanup_expired_sessions(self) -> int:
        """Clean up expired sessions from Redis.

        Returns:
            Number of sessions cleaned up

        Note:
            Sessions are automatically expired by Redis TTL.
            This method cleans up orphaned entries in user session indexes.
        """
        # This is a placeholder - in practice, Redis TTL handles cleanup
        # We could scan user session indexes and remove expired entries
        # For now, return 0 as TTL-based cleanup is automatic
        logger.debug("cleanup_expired_sessions called (TTL-based cleanup automatic)")
        return 0

    def check_rate_limit(self, client_ip: str, action: str = "auth") -> bool:
        """Check if client is within rate limit.

        Args:
            client_ip: Client IP address
            action: Action being rate limited (for separate limits per action)

        Returns:
            True if within rate limit, False if exceeded

        Note:
            Uses Redis sliding window rate limiting.
            Default: 5 attempts per 15 minutes per IP.
        """
        if not self.config.rate_limit_enabled:
            return True

        key = f"{self.config.redis_rate_limit_prefix}{action}:{client_ip}"
        now = time.time()  # Use float for more precision
        window_start = now - self.config.rate_limit_window

        # Remove old entries outside window
        self.redis.zremrangebyscore(key, 0, window_start)

        # Count attempts in current window
        count = self.redis.zcard(key)

        count = cast(int, self.redis.zcard(key))
        if count >= self.config.rate_limit_max_attempts:
            # Do NOT add the attempt - rate limit exceeded
            return False

        # Add current attempt (within limit)
        # Use unique member (timestamp with microseconds) and timestamp as score
        self.redis.zadd(key, {str(now): now})

        # Set expiry on the key (cleanup old rate limit data)
        self.redis.expire(key, int(self.config.rate_limit_window))

        return True

    def get_session_cookie_params(self) -> dict[str, Any]:
        """Get session cookie security parameters.

        Returns:
            Dictionary with cookie parameters (Secure, HttpOnly, SameSite, etc.)

        Note:
            Codex Recommendation #3: Centralized cookie security settings.
            max_age defaults to refresh_token_ttl if not configured.
        """
        return {
            "secure": self.config.cookie_secure,
            "httponly": self.config.cookie_httponly,
            "samesite": self.config.cookie_samesite,
            "domain": self.config.cookie_domain,
            "path": self.config.cookie_path,
            "max_age": self.config.cookie_max_age or self.config.refresh_token_ttl,
        }

    def _verify_session_binding(self, session_id: str, client_ip: str, user_agent: str) -> None:
        """Verify session binding (IP + User Agent).

        Args:
            session_id: Session ID to verify
            client_ip: Client IP address
            user_agent: Client User-Agent string

        Raises:
            InvalidTokenError: Session binding mismatch (if strict mode enabled)

        Note:
            If strict binding disabled, logs warning but doesn't raise error.
        """
        session_key = f"{self.config.redis_session_prefix}{session_id}"
        session_data = self.redis.hgetall(session_key)

        if not session_data:
            raise InvalidTokenError(f"Session not found: {session_id}")
        session_dict2 = cast(dict[bytes, bytes], session_data)
        stored_user_agent_hash = session_dict2.get(b"user_agent_hash", b"").decode()
        stored_ip = session_dict2.get(b"ip", b"").decode()
        current_ua_hash = hashlib.sha256(user_agent.encode()).hexdigest()

        if self.config.session_binding_strict:
            if stored_ip != client_ip:
                logger.warning(
                    "session_binding_mismatch_ip",
                    extra={
                        "session_id": session_id,
                        "expected_ip": stored_ip,
                        "actual_ip": client_ip,
                    },
                )
                raise InvalidTokenError("Session IP mismatch")

            if stored_user_agent_hash != current_ua_hash:
                logger.warning(
                    "session_binding_mismatch_ua",
                    extra={
                        "session_id": session_id,
                        "expected_ua_hash": stored_user_agent_hash,
                        "actual_ua_hash": current_ua_hash,
                    },
                )
                raise InvalidTokenError("Session User-Agent mismatch")
        else:
            # Log warnings but don't raise errors
            if stored_ip != client_ip or stored_user_agent_hash != current_ua_hash:
                logger.warning(
                    "session_binding_mismatch_relaxed",
                    extra={
                        "session_id": session_id,
                        "ip_match": stored_ip == client_ip,
                        "ua_match": stored_user_agent_hash == current_ua_hash,
                    },
                )

    def _add_session_to_index(self, user_id: str, session_id: str) -> None:
        """Add session to user's session index (sorted set by creation time)."""
        index_key = f"{self.config.redis_session_index_prefix}{user_id}"
        now = time.time()
        self.redis.zadd(index_key, {session_id: now})

    def _remove_session_from_index(self, user_id: str, session_id: str) -> None:
        """Remove session from user's session index."""
        index_key = f"{self.config.redis_session_index_prefix}{user_id}"
        self.redis.zrem(index_key, session_id)

    def _enforce_session_limit(self, user_id: str) -> None:
        """Enforce maximum concurrent session limit per user.

        Evicts oldest sessions if limit exceeded.

        Raises:
            Note: Does NOT raise SessionLimitExceededError - automatically evicts oldest
        """
        index_key = f"{self.config.redis_session_index_prefix}{user_id}"
        session_count = cast(int, self.redis.zcard(index_key))
        if session_count > self.config.max_sessions_per_user:
            excess_count = session_count - self.config.max_sessions_per_user
            oldest_sessions = cast(list[bytes], self.redis.zrange(index_key, 0, excess_count - 1))
            for session_id_bytes in oldest_sessions:
                session_id = (
                    session_id_bytes.decode()
                    if isinstance(session_id_bytes, bytes)
                    else session_id_bytes
                )
                logger.info(
                    "session_evicted_limit",
                    extra={
                        "user_id": user_id,
                        "session_id": session_id,
                        "reason": "max_sessions_exceeded",
                    },
                )
                self.terminate_session(session_id)

    def _atomic_refresh_cas(
        self,
        session_key: str,
        presented_refresh_jti: str,
        new_access_jti: str,
        new_refresh_jti: str,
        new_access_exp: int,
        new_refresh_exp: int,
    ) -> tuple[str, int]:
        """Atomic compare-and-swap for refresh token using Lua script.

        Args:
            session_key: Redis key for session
            presented_refresh_jti: JTI from the refresh token being used
            new_access_jti: JTI for the new access token
            new_refresh_jti: JTI for the new refresh token
            new_access_exp: Expiration timestamp for new access token
            new_refresh_exp: Expiration timestamp for new refresh token

        Returns:
            Tuple of (old_access_jti, old_access_exp) for precise revocation TTL

        Raises:
            InvalidTokenError: If session not found or JTI mismatch

        Note:
            Requires Redis with Lua support. Will raise exception if eval not supported.
        """
        # Lua script for atomic compare-and-swap of refresh JTI
        lua_cas_script = """
        local session_key = KEYS[1]
        local presented_refresh_jti = ARGV[1]
        local new_access_jti = ARGV[2]
        local new_refresh_jti = ARGV[3]
        local refresh_ttl = tonumber(ARGV[4])
        local new_access_exp = ARGV[5]
        local new_refresh_exp = ARGV[6]

        -- Get current session data
        local session_data = redis.call('HGETALL', session_key)
        if #session_data == 0 then
            return 'ERROR:SESSION_NOT_FOUND'
        end

        -- Convert array to hash
        local session = {}
        for i = 1, #session_data, 2 do
            session[session_data[i]] = session_data[i + 1]
        end

        -- Verify refresh JTI matches (atomic check)
        if session['refresh_jti'] ~= presented_refresh_jti then
            return 'ERROR:JTI_MISMATCH'
        end

        -- Save old access JTI and exp for precise revocation TTL
        local old_access_jti = session['access_jti'] or ''
        local old_access_exp = session['access_exp'] or ''

        -- Atomic swap: update JTIs, exp timestamps, and extend TTL
        redis.call('HSET', session_key, 'access_jti', new_access_jti)
        redis.call('HSET', session_key, 'refresh_jti', new_refresh_jti)
        redis.call('HSET', session_key, 'access_exp', new_access_exp)
        redis.call('HSET', session_key, 'refresh_exp', new_refresh_exp)
        redis.call('EXPIRE', session_key, refresh_ttl)

        -- Return old access JTI and exp (pipe-delimited for parsing)
        return old_access_jti .. '|' .. old_access_exp
        """

        # Execute atomic CAS with swap
        try:
            result = self.redis.eval(
                lua_cas_script,
                1,
                session_key,
                presented_refresh_jti,
                new_access_jti,
                new_refresh_jti,
                str(self.config.refresh_token_ttl),
                str(new_access_exp),
                str(new_refresh_exp),
            )
        except Exception as e:
            # Catch redis.exceptions.ResponseError if Lua script errors
            raise InvalidTokenError(f"CAS operation failed: {e}") from e

        # Handle sentinel error strings
        if isinstance(result, bytes):
            result = result.decode()

        if isinstance(result, str) and result.startswith("ERROR:"):
            error_type = result.replace("ERROR:", "")
            session_id = session_key.replace(self.config.redis_session_prefix, "")
            if error_type == "SESSION_NOT_FOUND":
                raise InvalidTokenError(f"Session not found: {session_id}")
            elif error_type == "JTI_MISMATCH":
                logger.warning(
                    "refresh_token_jti_mismatch",
                    extra={
                        "session_id": session_id,
                        "presented_jti": presented_refresh_jti,
                        "reason": "concurrent_refresh_or_replay_attack",
                    },
                )
                raise InvalidTokenError("Refresh token already used or revoked")

        # CAS succeeded - parse old_access_jti and old_access_exp
        # Format: "jti|exp" (pipe-delimited from Lua script)
        result_str = cast(str, result)
        if "|" in result_str:
            old_jti, old_exp_str = result_str.split("|", 1)
            old_exp = int(old_exp_str) if old_exp_str else 0
        else:
            # Backwards compatibility: old sessions may not have stored exp
            old_jti = result_str
            old_exp = 0

        return old_jti, old_exp

    def _fallback_refresh_cas(
        self,
        session_key: str,
        presented_refresh_jti: str,
        new_access_jti: str,
        new_refresh_jti: str,
        new_access_exp: int,
        new_refresh_exp: int,
    ) -> tuple[str, int]:
        """Non-atomic fallback CAS for testing with FakeRedis.

        Args:
            session_key: Redis key for session
            presented_refresh_jti: JTI from the refresh token being used
            new_access_jti: JTI for the new access token
            new_refresh_jti: JTI for the new refresh token
            new_access_exp: Expiration timestamp for new access token
            new_refresh_exp: Expiration timestamp for new refresh token

        Returns:
            Tuple of (old_access_jti, old_access_exp) for precise revocation TTL

        Raises:
            InvalidTokenError: If session not found or JTI mismatch

        Warning:
            NOT ATOMIC - only for testing! Production must use Lua-based CAS.
        """
        session_data = self.redis.hgetall(session_key)

        if not session_data:
            session_id = session_key.replace(self.config.redis_session_prefix, "")
            raise InvalidTokenError(f"Session not found: {session_id}")

        # Handle both bytes and strings (FakeRedis may return either)
        # Also handle both string and bytes keys (decode_responses affects which works)
        def safe_decode(value: bytes | str | None) -> str:
            if isinstance(value, bytes):
                return value.decode()
            return str(value) if value else ""

        # Try both string and bytes keys (decode_responses affects which works)
        # Redis was initialized with decode_responses=False, so keys are bytes
        stored_refresh_jti = safe_decode(session_data.get(b"refresh_jti", b""))
        old_access_jti = safe_decode(session_data.get(b"access_jti", b""))
        old_access_exp_str = safe_decode(session_data.get(b"access_exp", b""))
        old_access_exp = int(old_access_exp_str) if old_access_exp_str else 0

        # Non-atomic JTI check (race condition possible in production!)
        if presented_refresh_jti != stored_refresh_jti:
            session_id = session_key.replace(self.config.redis_session_prefix, "")
            logger.warning(
                "refresh_token_jti_mismatch",
                extra={
                    "session_id": session_id,
                    "presented_jti": presented_refresh_jti,
                    "stored_jti": stored_refresh_jti,
                    "reason": "concurrent_refresh_or_replay_attack",
                },
            )
            raise InvalidTokenError("Refresh token already used or revoked")

        # Non-atomic swap: update JTIs, exp timestamps, and extend TTL
        self.redis.hset(session_key, "access_jti", new_access_jti)
        self.redis.hset(session_key, "refresh_jti", new_refresh_jti)
        self.redis.hset(session_key, "access_exp", str(new_access_exp))
        self.redis.hset(session_key, "refresh_exp", str(new_refresh_exp))
        self.redis.expire(session_key, self.config.refresh_token_ttl)

        return old_access_jti, old_access_exp
