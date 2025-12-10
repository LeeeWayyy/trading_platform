"""OAuth2 Authorization Code Flow with PKCE implementation.

This module implements the complete OAuth2 authorization flow:
1. /login: Generate PKCE challenge, redirect to Auth0 authorization endpoint
2. /callback: Exchange authorization code for tokens, validate ID token, create session
3. /refresh: Refresh access token using refresh token (with absolute timeout enforcement)
4. /logout: Revoke tokens, delete session, redirect to Auth0 logout

References:
- OAuth2: RFC 6749
- PKCE: RFC 7636
- OIDC: https://openid.net/specs/openid-connect-core-1_0.html
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
from pydantic import BaseModel

from apps.web_console.auth.jwks_validator import JWKSValidator
from apps.web_console.auth.oauth2_state import OAuth2State, OAuth2StateStore
from apps.web_console.auth.pkce import (
    generate_nonce,
    generate_pkce_challenge,
    generate_session_id,
    generate_state,
)
from apps.web_console.auth.session_invalidation import validate_session_version
from apps.web_console.auth.session_store import RedisSessionStore, SessionData

logger = logging.getLogger(__name__)


class OAuth2Config(BaseModel):
    """OAuth2 configuration from environment variables."""

    auth0_domain: str
    client_id: str
    client_secret: str
    audience: str
    redirect_uri: str
    logout_redirect_uri: str


class OAuth2FlowHandler:
    """Handles OAuth2 authorization code flow with PKCE."""

    def __init__(
        self,
        config: OAuth2Config,
        session_store: RedisSessionStore,
        state_store: OAuth2StateStore,
        jwks_validator: JWKSValidator,
        db_pool: Any | None = None,
    ):
        """Initialize OAuth2 flow handler.

        Args:
            config: OAuth2 configuration
            session_store: Redis session store for token storage
            state_store: Redis state store for PKCE/state/nonce
            jwks_validator: JWKS validator for ID token verification
        """
        self.config = config
        self.session_store = session_store
        self.state_store = state_store
        self.jwks_validator = jwks_validator
        self.db_pool = db_pool

        # Auth0 endpoints
        self.authorization_endpoint = f"https://{config.auth0_domain}/authorize"
        self.token_endpoint = f"https://{config.auth0_domain}/oauth/token"
        self.logout_endpoint = f"https://{config.auth0_domain}/v2/logout"

    async def initiate_login(self) -> tuple[str, OAuth2State]:
        """Initiate OAuth2 login flow.

        Returns:
            Tuple of (authorization_url, oauth_state)
        """
        # Generate PKCE challenge
        pkce = generate_pkce_challenge()
        state = generate_state()
        nonce = generate_nonce()

        # Store state in Redis with 10-minute TTL
        oauth_state = OAuth2State(
            state=state,
            code_verifier=pkce.code_verifier,
            nonce=nonce,
            code_challenge=pkce.code_challenge,
            redirect_uri=self.config.redirect_uri,
            created_at=datetime.now(UTC),
        )
        await self.state_store.store_state(oauth_state)

        # Build authorization URL with proper URL encoding
        params = {
            "response_type": "code",
            "client_id": self.config.client_id,
            "redirect_uri": self.config.redirect_uri,
            "scope": "openid profile email offline_access",  # offline_access for refresh token
            "audience": self.config.audience,
            "state": state,
            "nonce": nonce,
            "code_challenge": pkce.code_challenge,
            "code_challenge_method": "S256",
        }

        query = urlencode(params)
        authorization_url = f"{self.authorization_endpoint}?{query}"

        return authorization_url, oauth_state

    async def handle_callback(
        self,
        code: str,
        state: str,
        ip_address: str,
        user_agent: str,
        db_pool: Any | None = None,
        audit_logger: Any | None = None,
    ) -> tuple[str, SessionData]:
        """Handle OAuth2 callback from Auth0.

        Args:
            code: Authorization code from callback
            state: State parameter from callback
            ip_address: Client IP address
            user_agent: Client User-Agent

        Returns:
            Tuple of (session_id, session_data)

        Raises:
            ValueError: If state validation fails or token exchange fails
        """
        db_pool = db_pool or self.db_pool

        # CRITICAL: Retrieve and DELETE state (single-use enforcement)
        oauth_state = await self.state_store.get_and_delete_state(state)
        if not oauth_state:
            raise ValueError("Invalid or expired state parameter")

        # Exchange authorization code for tokens and validate ID token
        try:
            tokens = await self._exchange_code_for_tokens(
                authorization_code=code,
                code_verifier=oauth_state.code_verifier,
            )

            # Validate ID token
            id_token_claims = await self.jwks_validator.validate_id_token(
                id_token=tokens["id_token"],
                expected_nonce=oauth_state.nonce,
                expected_audience=self.config.client_id,
                expected_issuer=f"https://{self.config.auth0_domain}/",
            )
        except httpx.HTTPStatusError as e:
            # Auth0 returned 4xx/5xx error during token exchange
            logger.error(f"Token exchange failed: HTTP {e.response.status_code}")
            raise ValueError(f"Token exchange failed: {e.response.status_code}") from e
        except httpx.RequestError as e:
            # Network error during token exchange
            logger.error(f"Token exchange network error: {e}")
            raise ValueError(f"Token exchange network error: {str(e)}") from e
        except Exception as e:
            # JWT validation error or other unexpected errors
            logger.error(f"Callback validation failed: {e}")
            raise ValueError(f"Authentication validation failed: {str(e)}") from e

        # Validate token response contains required fields
        if not all(key in tokens for key in ["access_token", "refresh_token", "id_token"]):
            missing_keys = [
                k for k in ["access_token", "refresh_token", "id_token"] if k not in tokens
            ]
            logger.error(f"Token exchange response missing required fields: {missing_keys}")
            raise ValueError(f"Token exchange response incomplete: missing {missing_keys}")

        # Fetch RBAC provisioning data if available
        user_id = id_token_claims["sub"]
        role_data = None
        strategies: list[str] = []
        if db_pool is not None:
            role_data = await _fetch_user_role_data(user_id, db_pool)
            if role_data is None:
                if audit_logger:
                    await audit_logger.log_auth_event(
                        user_id=user_id,
                        action="login",
                        outcome="denied",
                        details={"reason": "user_not_provisioned"},
                    )
                raise ValueError("User not provisioned. Contact administrator.")
            strategies = await _fetch_user_strategies(user_id, db_pool)

        # Create session
        session_id = generate_session_id()
        now = datetime.now(UTC)

        # Calculate access token expiry (Component 3: Auto-refresh)
        # Auth0 default: 1 hour (3600s), but use expires_in from response if available
        expires_in_seconds = tokens.get("expires_in", 3600)
        access_token_expires_at = now + timedelta(seconds=expires_in_seconds)

        session_data = SessionData(
            access_token=tokens["access_token"],
            refresh_token=tokens["refresh_token"],
            id_token=tokens["id_token"],
            user_id=user_id,
            email=id_token_claims.get("email", "unknown@example.com"),
            created_at=now,
            last_activity=now,
            ip_address=ip_address,
            user_agent=user_agent,
            access_token_expires_at=access_token_expires_at,  # Component 3
            role=role_data["role"] if role_data else "viewer",
            session_version=int(role_data["session_version"]) if role_data else 1,
            strategies=strategies,
        )

        await self.session_store.create_session(session_id, session_data)

        return session_id, session_data


    async def _exchange_code_for_tokens(
        self,
        authorization_code: str,
        code_verifier: str,
    ) -> dict[str, Any]:
        """Exchange authorization code for tokens (internal method).

        Args:
            authorization_code: Authorization code from callback
            code_verifier: PKCE code verifier

        Returns:
            Token response from Auth0 (access_token, id_token, refresh_token)

        Raises:
            httpx.HTTPStatusError: If token exchange fails
        """
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                self.token_endpoint,
                data={
                    "grant_type": "authorization_code",
                    "client_id": self.config.client_id,
                    "client_secret": self.config.client_secret,
                    "code": authorization_code,
                    "code_verifier": code_verifier,
                    "redirect_uri": self.config.redirect_uri,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            response.raise_for_status()
            return response.json()  # type: ignore[no-any-return]

    async def refresh_tokens(
        self,
        session_id: str,
        ip_address: str | None = None,
        user_agent: str | None = None,
        db_pool: Any | None = None,
    ) -> SessionData:
        """Refresh access token using refresh token.

        CRITICAL: Enforces absolute 4-hour timeout.
        Optionally enforces session binding (pass None to skip).
        Rotates refresh token on every refresh per OAuth2 best practices.

        FIX (Codex High): Made binding optional since Streamlit background
        refreshes originate from the server, not the user's browser.
        The HttpOnly cookie itself proves authentication.

        Args:
            session_id: Session ID from cookie
            ip_address: Client IP address (for binding validation), or None to skip
            user_agent: Client User-Agent (for binding validation), or None to skip

        Returns:
            Updated session data with new tokens

        Raises:
            ValueError: If session not found, binding fails, or absolute timeout exceeded
        """
        db_pool = db_pool or self.db_pool

        # Retrieve session with optional binding validation
        session_data = await self.session_store.get_session(
            session_id,
            current_ip=ip_address,
            current_user_agent=user_agent,
            update_activity=False,  # Don't update yet
        )

        if not session_data:
            raise ValueError("Session not found or invalid")

        if db_pool is not None and hasattr(session_data, "session_version"):
            is_current = await validate_session_version(
                session_data.user_id,
                session_data.session_version,
                db_pool,
            )
            if not is_current:
                logger.warning(
                    "session_version_mismatch_on_refresh",
                    extra={
                        "user_id": session_data.user_id,
                        "session_version": session_data.session_version,
                    },
                )
                await self.session_store.delete_session(session_id)
                raise ValueError("Session invalidated. Please sign in again.")

        # Refresh token exchange
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    self.token_endpoint,
                    data={
                        "grant_type": "refresh_token",
                        "client_id": self.config.client_id,
                        "client_secret": self.config.client_secret,
                        "refresh_token": session_data.refresh_token,
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                response.raise_for_status()
                tokens = response.json()
        except httpx.HTTPStatusError as e:
            # Auth0 returned 4xx/5xx error (e.g., invalid/expired refresh token)
            logger.error(f"Token refresh failed: HTTP {e.response.status_code} - {e.response.text}")
            raise ValueError(f"Token refresh failed: {e.response.status_code}") from e
        except httpx.RequestError as e:
            # Network error (connection timeout, DNS failure, etc.)
            logger.error(f"Token refresh network error: {e}")
            raise ValueError(f"Token refresh network error: {str(e)}") from e

        # Validate refresh response contains required access_token
        if "access_token" not in tokens:
            logger.error("Refresh token response missing access_token")
            raise ValueError("Refresh token response incomplete: missing access_token")

        # Update session with new tokens
        session_data.access_token = tokens["access_token"]
        session_data.refresh_token = tokens.get(
            "refresh_token", session_data.refresh_token
        )  # May rotate

        # Component 3: Update access token expiry for auto-refresh
        now = datetime.now(UTC)
        expires_in_seconds = tokens.get("expires_in", 3600)
        session_data.access_token_expires_at = now + timedelta(seconds=expires_in_seconds)

        # Validate new ID token if present (security: prevent identity swap)
        if "id_token" in tokens:
            try:
                new_id_token_claims = await self.jwks_validator.validate_id_token(
                    id_token=tokens["id_token"],
                    expected_nonce=None,  # Nonce only used for initial login, not refresh
                    expected_audience=self.config.client_id,
                    expected_issuer=f"https://{self.config.auth0_domain}/",
                )

                # CRITICAL: Verify subject (user_id) matches existing session
                # Prevents identity swap attack via compromised IdP response
                if new_id_token_claims["sub"] != session_data.user_id:
                    logger.error(
                        f"ID token subject mismatch on refresh: "
                        f"expected={session_data.user_id}, got={new_id_token_claims['sub']}"
                    )
                    # Delete compromised session
                    await self.session_store.delete_session(session_id)
                    raise ValueError("ID token subject mismatch - session terminated")

                # Subject matches - safe to update
                session_data.id_token = tokens["id_token"]
            except Exception as e:
                logger.error(f"ID token validation failed on refresh: {e}")
                # Delete session on validation failure
                await self.session_store.delete_session(session_id)
                raise ValueError(f"ID token validation failed: {e}") from e

        session_data.last_activity = datetime.now(UTC)

        # CRITICAL: Calculate remaining TTL (preserve absolute timeout)
        now = datetime.now(UTC)
        remaining_absolute = self.session_store.absolute_timeout - (now - session_data.created_at)
        remaining_seconds = max(1, int(remaining_absolute.total_seconds()))

        # Re-encrypt and store with REMAINING TTL
        key = f"session:{session_id}"
        json_data = session_data.model_dump_json()
        encrypted_updated = self.session_store._encrypt(json_data)
        await self.session_store.redis.setex(key, remaining_seconds, encrypted_updated)

        logger.info(
            "Tokens refreshed",
            extra={
                "session_id": session_id[:8] + "...",
                "user_id": session_data.user_id,
                "remaining_ttl": remaining_seconds,
            },
        )

        return session_data

    async def handle_logout(
        self,
        session_id: str,
        current_ip: str,
        current_user_agent: str,
    ) -> str:
        """Handle OAuth2 logout with binding validation and token revocation.

        FIX (Codex Medium #5): Validates session binding before revoking tokens
        to prevent attacker with stolen cookie from revoking real user's refresh token.

        Args:
            session_id: Session ID to delete
            current_ip: Client IP address for binding validation
            current_user_agent: Client User-Agent for binding validation

        Returns:
            Auth0 logout URL to redirect to
        """
        # Retrieve session WITH binding validation
        session_data = await self.session_store.get_session(
            session_id,
            current_ip=current_ip,
            current_user_agent=current_user_agent,
            update_activity=False,
        )

        # Revoke refresh token at Auth0 ONLY if binding is valid
        if session_data and session_data.refresh_token:
            try:
                await self._revoke_refresh_token(session_data.refresh_token)
                logger.info(
                    "Refresh token revoked at Auth0",
                    extra={"user_id": session_data.user_id}
                )
            except Exception as e:
                # Non-critical: Session will still be deleted locally
                logger.error(f"Refresh token revocation failed (non-critical): {e}")
        elif not session_data:
            # Binding failed - delete session locally but don't revoke at Auth0
            logger.warning(
                "Logout binding validation failed - deleting session locally only",
                extra={
                    "session_id": session_id[:8] + "...",
                    "current_ip": current_ip,
                }
            )

        # Delete session from Redis
        await self.session_store.delete_session(session_id)

        # Build Auth0 logout URL
        params = {
            "client_id": self.config.client_id,
            "returnTo": self.config.logout_redirect_uri,
        }
        query = urlencode(params)
        logout_url = f"{self.logout_endpoint}?{query}"

        return logout_url

    async def _revoke_refresh_token(self, refresh_token: str) -> None:
        """Revoke refresh token at Auth0 (internal method).

        See: https://auth0.com/docs/api/authentication#revoke-refresh-token

        Args:
            refresh_token: Refresh token to revoke

        Raises:
            httpx.HTTPStatusError: If revocation fails
        """
        revocation_endpoint = f"https://{self.config.auth0_domain}/oauth/revoke"

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                revocation_endpoint,
                data={
                    "client_id": self.config.client_id,
                    "client_secret": self.config.client_secret,
                    "token": refresh_token,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            response.raise_for_status()


@asynccontextmanager
async def _acquire(db_pool: Any) -> AsyncIterator[Any]:
    if hasattr(db_pool, "acquire"):
        async with db_pool.acquire() as conn:
            yield conn
    elif hasattr(db_pool, "connection"):
        async with db_pool.connection() as conn:
            yield conn
    else:
        raise RuntimeError("Unsupported db_pool interface")


async def _fetch_user_role_data(user_id: str, db_pool: Any) -> dict[str, Any] | None:
    async with _acquire(db_pool) as conn:
        row = await conn.fetchrow(
            "SELECT role, session_version FROM user_roles WHERE user_id = $1",
            user_id,
        )
    if not row:
        return None
    return {"role": row["role"], "session_version": row["session_version"]}


async def _fetch_user_strategies(user_id: str, db_pool: Any) -> list[str]:
    async with _acquire(db_pool) as conn:
        rows = await conn.fetch(
            "SELECT strategy_id FROM user_strategy_access WHERE user_id = $1",
            user_id,
        )
    return [row["strategy_id"] for row in rows]
