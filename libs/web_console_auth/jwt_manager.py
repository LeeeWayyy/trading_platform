"""JWT token generation and validation."""

import hashlib
import logging
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from redis import Redis

from libs.web_console_auth.config import AuthConfig
from libs.web_console_auth.exceptions import (
    ImmatureSignatureError,
    InvalidAudienceError,
    InvalidIssuerError,
    InvalidSignatureError,
    InvalidTokenError,
    MissingJtiError,
    TokenExpiredError,
    TokenRevokedError,
)

logger = logging.getLogger(__name__)


class JWTManager:
    """Manages JWT token generation, validation, and revocation.

    Implements RS256 (RSA-SHA256) token signing using keys from Component 1.
    Supports token revocation via Redis blacklist with TTL-based expiry.

    Security Features:
    - NEVER logs full tokens (logs jti only - Codex Recommendation #1)
    - Blacklist TTL = token_exp - current_time (Codex Recommendation #5)
    - Clock skew tolerance (configurable, default 30s)
    """

    def __init__(self, config: AuthConfig, redis_client: Redis) -> None:
        """Initialize JWT manager with RSA keys and Redis client.

        Args:
            config: Authentication configuration
            redis_client: Redis client for token blacklist

        Raises:
            FileNotFoundError: If JWT key files don't exist
            ValueError: If keys cannot be loaded
        """
        self.config = config
        self.redis = redis_client

        # Load RSA keys from Component 1 certificate infrastructure
        self.private_key = self._load_private_key(config.jwt_private_key_path)
        self.public_key = self._load_public_key(config.jwt_public_key_path)

        logger.info(
            "JWTManager initialized",
            extra={
                "algorithm": config.jwt_algorithm,
                "access_ttl": config.access_token_ttl,
                "refresh_ttl": config.refresh_token_ttl,
            },
        )

    def _load_private_key(self, path: Path) -> rsa.RSAPrivateKey:
        """Load RSA private key from PEM file."""
        if not path.exists():
            raise FileNotFoundError(f"JWT private key not found: {path}")

        with path.open("rb") as f:
            private_key = serialization.load_pem_private_key(f.read(), password=None)

        if not isinstance(private_key, rsa.RSAPrivateKey):
            raise ValueError(f"Expected RSA private key, got {type(private_key)}")

        return private_key

    def _load_public_key(self, path: Path) -> rsa.RSAPublicKey:
        """Load RSA public key from PEM file."""
        if not path.exists():
            raise FileNotFoundError(f"JWT public key not found: {path}")

        with path.open("rb") as f:
            public_key = serialization.load_pem_public_key(f.read())

        if not isinstance(public_key, rsa.RSAPublicKey):
            raise ValueError(f"Expected RSA public key, got {type(public_key)}")

        return public_key

    def generate_access_token(
        self, user_id: str, session_id: str, client_ip: str, user_agent: str
    ) -> str:
        """Generate signed access token with session binding.

        Args:
            user_id: User identifier (subject)
            session_id: Session identifier for binding
            client_ip: Client IP address for binding
            user_agent: Client User-Agent for fingerprinting

        Returns:
            Signed JWT access token (RS256)

        Note:
            Token includes IP and user_agent_hash for session binding.
            User agent is hashed (SHA256) for privacy and size reduction.
        """
        now = datetime.now(UTC)
        jti = str(uuid.uuid4())
        user_agent_hash = hashlib.sha256(user_agent.encode()).hexdigest()

        payload = {
            "sub": user_id,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(seconds=self.config.access_token_ttl)).timestamp()),
            "jti": jti,
            "iss": self.config.jwt_issuer,  # Issuer claim (prevents token confusion)
            "aud": self.config.jwt_audience,  # Audience claim (prevents cross-service replay)
            "type": "access",
            "session_id": session_id,
            "ip": client_ip,
            "user_agent_hash": user_agent_hash,
        }

        token = jwt.encode(payload, self.private_key, algorithm=self.config.jwt_algorithm)

        # Structured logging (Gemini Finding #2 + Codex Recommendation #1)
        logger.info(
            "access_token_generated",
            extra={
                "user_id": user_id,
                "session_id": session_id,
                "jti": jti,  # Log token ID only, NEVER full token
                "ip": client_ip,
                "user_agent_hash": user_agent_hash,
                "exp": payload["exp"],
            },
        )

        return token

    def generate_refresh_token(self, user_id: str, session_id: str, access_jti: str) -> str:
        """Generate signed refresh token linked to access token.

        Args:
            user_id: User identifier (subject)
            session_id: Session identifier for binding
            access_jti: JTI of the access token this refresh token is linked to

        Returns:
            Signed JWT refresh token (RS256)

        Note:
            Refresh token has longer TTL and is used to obtain new access tokens.
            Links to access token via access_jti for audit trail.
        """
        now = datetime.now(UTC)
        jti = str(uuid.uuid4())

        payload = {
            "sub": user_id,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(seconds=self.config.refresh_token_ttl)).timestamp()),
            "jti": jti,
            "iss": self.config.jwt_issuer,  # Issuer claim (prevents token confusion)
            "aud": self.config.jwt_audience,  # Audience claim (prevents cross-service replay)
            "type": "refresh",
            "session_id": session_id,
            "access_jti": access_jti,
        }

        token = jwt.encode(payload, self.private_key, algorithm=self.config.jwt_algorithm)

        logger.info(
            "refresh_token_generated",
            extra={
                "user_id": user_id,
                "session_id": session_id,
                "jti": jti,
                "access_jti": access_jti,
                "exp": payload["exp"],
            },
        )

        return token

    def generate_service_token(
        self, user_id: str, session_id: str, client_ip: str, user_agent: str
    ) -> str:
        """Generate service-to-service token for internal API calls.

        Unlike access tokens (for end-user sessions), service tokens are used
        for trusted service-to-service communication (Web Console → Execution Gateway).

        Args:
            user_id: User identifier (becomes sub claim, must match X-User-ID header)
            session_id: Session identifier for binding
            client_ip: Client IP address for binding
            user_agent: Client User-Agent for fingerprinting

        Returns:
            Signed JWT service token (RS256)

        Note:
            Token has type="service" which is REQUIRED by GatewayAuthenticator.
            GatewayAuthenticator fetches role/strategies from database, not JWT claims.
            One-time-use JTI enforcement prevents replay attacks.
        """
        now = datetime.now(UTC)
        jti = str(uuid.uuid4())
        user_agent_hash = hashlib.sha256(user_agent.encode()).hexdigest()

        payload = {
            "sub": user_id,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(seconds=self.config.access_token_ttl)).timestamp()),
            "jti": jti,
            "iss": self.config.jwt_issuer,
            "aud": self.config.jwt_audience,
            "type": "service",  # CRITICAL: Must be "service" for GatewayAuthenticator
            "session_id": session_id,
            "ip": client_ip,
            "user_agent_hash": user_agent_hash,
        }

        token = jwt.encode(payload, self.private_key, algorithm=self.config.jwt_algorithm)

        logger.info(
            "service_token_generated",
            extra={
                "user_id": user_id,
                "session_id": session_id,
                "jti": jti,  # Log token ID only, NEVER full token
                "ip": client_ip,
                "exp": payload["exp"],
            },
        )

        return token

    def validate_token(self, token: str, expected_type: str) -> dict[str, Any]:
        """Validate token signature and claims.

        Args:
            token: JWT token to validate
            expected_type: Expected token type ("access", "refresh", or "service")

        Returns:
            Decoded token payload with verified claims

        Raises:
            InvalidTokenError: Token is malformed, has invalid signature, or wrong type
            TokenExpiredError: Token has expired (beyond clock skew tolerance)
            TokenRevokedError: Token has been revoked (blacklisted)

        Note:
            Checks revocation blacklist on every validation.
            Allows clock skew tolerance (default 30s) per config.
        """
        try:
            # Decode and verify signature + iss/aud claims
            payload = jwt.decode(
                token,
                self.public_key,
                algorithms=[self.config.jwt_algorithm],
                issuer=self.config.jwt_issuer,  # Verify issuer claim
                audience=self.config.jwt_audience,  # Verify audience claim
                options={
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_iat": True,
                    "verify_nbf": True,
                },
                leeway=self.config.clock_skew_seconds,
            )
        except jwt.ExpiredSignatureError as e:
            logger.warning(
                "token_expired",
                extra={
                    "error": str(e),
                    "expected_type": expected_type,
                },
            )
            raise TokenExpiredError("Token has expired") from e
        except jwt.ImmatureSignatureError as e:
            logger.warning(
                "token_not_yet_valid",
                extra={
                    "error": str(e),
                    "expected_type": expected_type,
                },
            )
            raise ImmatureSignatureError("Token not yet valid") from e
        except jwt.InvalidIssuerError as e:
            logger.warning(
                "token_invalid_issuer",
                extra={
                    "error": str(e),
                    "expected_type": expected_type,
                },
            )
            raise InvalidIssuerError("Token issuer not trusted") from e
        except jwt.InvalidAudienceError as e:
            logger.warning(
                "token_invalid_audience",
                extra={
                    "error": str(e),
                    "expected_type": expected_type,
                },
            )
            raise InvalidAudienceError("Token not intended for this service") from e
        except jwt.InvalidSignatureError as e:
            logger.warning(
                "token_invalid_signature",
                extra={
                    "error": str(e),
                    "expected_type": expected_type,
                },
            )
            raise InvalidSignatureError("Token signature verification failed") from e
        except jwt.InvalidTokenError as e:
            logger.warning(
                "token_invalid",
                extra={
                    "error": str(e),
                    "expected_type": expected_type,
                },
            )
            raise InvalidTokenError(f"Invalid token: {e}") from e

        # Verify token type
        if payload.get("type") != expected_type:
            logger.warning(
                "token_wrong_type",
                extra={
                    "expected": expected_type,
                    "actual": payload.get("type"),
                    "jti": payload.get("jti"),
                },
            )
            raise InvalidTokenError(f"Expected {expected_type} token, got {payload.get('type')}")

        # Check revocation blacklist
        jti = payload.get("jti")
        if not jti:
            raise MissingJtiError("Token missing jti claim")

        if self.is_token_revoked(jti):
            logger.warning(
                "token_revoked",
                extra={
                    "jti": jti,
                    "user_id": payload.get("sub"),
                    "session_id": payload.get("session_id"),
                },
            )
            raise TokenRevokedError(f"Token has been revoked: {jti}")

        logger.debug(
            "token_validated",
            extra={
                "jti": jti,
                "type": expected_type,
                "user_id": payload.get("sub"),
                "session_id": payload.get("session_id"),
            },
        )

        return payload  # type: ignore[no-any-return]

    def decode_token(self, token: str) -> dict[str, Any]:
        """Decode token WITHOUT validation (for debugging/inspection).

        Args:
            token: JWT token to decode

        Returns:
            Decoded token payload (unverified)

        Warning:
            This does NOT validate signature or expiration.
            Use validate_token() for security-critical operations.
        """
        return jwt.decode(token, options={"verify_signature": False})  # type: ignore[no-any-return]

    def revoke_token(self, jti: str, exp: int) -> None:
        """Revoke token by adding to Redis blacklist.

        Args:
            jti: Token ID to revoke
            exp: Token expiration timestamp (Unix time)

        Note:
            Blacklist TTL = exp - current_time (Codex Recommendation #5)
            This prevents dangling entries in Redis after token expires.
            If token is already expired, TTL defaults to 1 second (cleanup).
        """
        now = int(time.time())
        ttl = max(exp - now, 1)  # At least 1 second to ensure Redis accepts it

        key = f"{self.config.redis_blacklist_prefix}{jti}"
        self.redis.setex(key, ttl, "revoked")

        logger.info(
            "token_revoked",
            extra={
                "jti": jti,
                "ttl": ttl,
                "exp": exp,
            },
        )

    def is_token_revoked(self, jti: str) -> bool:
        """Check if token is revoked (blacklisted).

        Args:
            jti: Token ID to check

        Returns:
            True if token is revoked, False otherwise
        """
        key = f"{self.config.redis_blacklist_prefix}{jti}"
        exists_result: int = self.redis.exists(key)  # type: ignore[assignment]
        return exists_result > 0
