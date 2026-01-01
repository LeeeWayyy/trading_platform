from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import time
from collections.abc import Callable
from typing import Any, cast
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx
import redis.asyncio as redis
from jose import JWTError, jwt

from apps.web_console_ng import config
from apps.web_console_ng.auth.auth_result import AuthResult
from apps.web_console_ng.auth.providers.base import AuthProvider
from apps.web_console_ng.auth.session_store import get_session_store

logger = logging.getLogger(__name__)


class OAuth2AuthHandler(AuthProvider):
    """OAuth2/OIDC authentication handler with PKCE."""

    OAUTH2_STATE_TTL = 600  # 10 minutes
    OAUTH2_STATE_PREFIX = "oauth2_flow:"
    JWKS_CACHE_TTL = 300  # 5 minutes
    ALLOWED_ALGORITHMS = ["RS256"]

    def __init__(self) -> None:
        # Configuration keys would typically be in config.py
        # For now we assume they exist or are passed/defaulted
        self.redis = _redis_from_url(config.REDIS_URL, decode_responses=False)
        self.client_id = getattr(config, "OAUTH2_CLIENT_ID", "mock_client_id")
        self.client_secret = getattr(config, "OAUTH2_CLIENT_SECRET", "mock_secret")
        self.authorize_url = getattr(
            config, "OAUTH2_AUTHORIZE_URL", "https://mock.auth0.com/authorize"
        )
        self.token_url = getattr(config, "OAUTH2_TOKEN_URL", "https://mock.auth0.com/oauth/token")
        self.userinfo_url = getattr(
            config, "OAUTH2_USERINFO_URL", "https://mock.auth0.com/userinfo"
        )
        self.callback_url = getattr(
            config, "OAUTH2_CALLBACK_URL", "http://localhost:8080/auth/callback"
        )
        self.issuer = getattr(config, "OAUTH2_ISSUER", "https://mock.auth0.com/")
        self._jwks_cache: dict[str, Any] | None = None
        self._jwks_cache_expires_at = 0.0

    async def authenticate(self, **kwargs: Any) -> AuthResult:
        """Authenticate via OAuth2 (Callback handling).

        This method corresponds to the exchange of code for tokens.
        """
        code = kwargs.get("code")
        state = kwargs.get("state")

        if not code or not state:
            return AuthResult(success=False, error_message="Missing code or state")

        return await self.handle_callback(code, state, **kwargs)

    async def get_authorization_url(self) -> str:
        """Generate authorization URL with PKCE and nonce."""
        # Generate PKCE challenge
        code_verifier = secrets.token_urlsafe(64)
        code_challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest())
            .decode()
            .rstrip("=")
        )

        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)

        # Store flow data in Redis
        flow_data = {
            "code_verifier": code_verifier,
            "nonce": nonce,
            "created_at": time.time(),
            "redirect_uri": self.callback_url,
        }
        await self.redis.setex(
            f"{self.OAUTH2_STATE_PREFIX}{state}",
            self.OAUTH2_STATE_TTL,
            json.dumps(flow_data),
        )

        params = {
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": self.callback_url,
            "scope": "openid profile email",
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }

        return f"{self.authorize_url}?{urlencode(params)}"

    async def handle_callback(self, code: str, state: str, **kwargs: Any) -> AuthResult:
        """Handle OAuth2 callback and exchange code for tokens."""
        # Validate state
        flow_key = f"{self.OAUTH2_STATE_PREFIX}{state}"
        flow_data_raw = await self.redis.get(flow_key)

        if not flow_data_raw:
            return AuthResult(success=False, error_message="Invalid or expired state")

        # Delete immediately to prevent replay
        await self.redis.delete(flow_key)

        flow_data = json.loads(flow_data_raw)
        code_verifier = flow_data["code_verifier"]
        expected_nonce = flow_data["nonce"]
        expected_redirect_uri = flow_data.get("redirect_uri", self.callback_url)
        provided_redirect_uri = kwargs.get("redirect_uri")

        if provided_redirect_uri:
            normalized_expected = self._normalize_redirect_uri(expected_redirect_uri)
            normalized_callback = self._normalize_redirect_uri(self.callback_url)
            normalized_provided = self._normalize_redirect_uri(provided_redirect_uri)

            if (
                normalized_provided != normalized_expected
                or normalized_provided != normalized_callback
            ):
                return AuthResult(success=False, error_message="Invalid redirect URI")

        # Exchange code for tokens
        async with httpx.AsyncClient() as client:
            try:
                token_response = await client.post(
                    self.token_url,
                    data={
                        "grant_type": "authorization_code",
                        "code": code,
                        "redirect_uri": self.callback_url,
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                        "code_verifier": code_verifier,
                    },
                )

                if token_response.status_code != 200:
                    logger.error(f"Token exchange failed: {token_response.text}")
                    return AuthResult(success=False, error_message="Token exchange failed")

                tokens = token_response.json()
                access_token = tokens["access_token"]
                id_token = tokens.get("id_token")
                is_valid, error_message = await self._validate_id_token(
                    id_token,
                    expected_nonce,
                )
                if not is_valid:
                    return AuthResult(
                        success=False,
                        error_message=error_message or "Invalid id_token",
                    )

                # Fetch user info
                userinfo_response = await client.get(
                    self.userinfo_url,
                    headers={"Authorization": f"Bearer {access_token}"},
                )

                if userinfo_response.status_code != 200:
                    return AuthResult(success=False, error_message="Failed to fetch user info")

                userinfo = userinfo_response.json()

            except httpx.RequestError as e:
                logger.error(f"OAuth2 request error: {e}")
                return AuthResult(success=False, error_message="OAuth2 provider unreachable")

        # Map user info - store id_token for RP-initiated logout
        user_data = {
            "user_id": userinfo.get("sub"),
            "username": userinfo.get("email", userinfo.get("name")),
            "email": userinfo.get("email"),
            "role": self._map_role(userinfo),
            "auth_method": "oauth2",
            "id_token": tokens.get("id_token"),  # For RP-initiated logout
        }

        session_store = get_session_store()
        cookie_value, csrf_token = await session_store.create_session(
            user_data=user_data,
            device_info={"user_agent": kwargs.get("user_agent", "")},
            client_ip=kwargs.get("client_ip", "127.0.0.1"),
        )

        return AuthResult(
            success=True,
            cookie_value=cookie_value,
            csrf_token=csrf_token,
            user_data=user_data,
        )

    async def get_logout_url(self, id_token: str | None = None) -> str | None:
        """Get RP-initiated logout URL for OAuth2 providers.

        Args:
            id_token: The id_token from the session (for id_token_hint).

        Returns:
            Logout URL if configured, None otherwise.
        """
        logout_url = getattr(config, "OAUTH2_LOGOUT_URL", None)
        if not logout_url:
            return None

        post_logout_redirect = getattr(
            config, "OAUTH2_POST_LOGOUT_REDIRECT_URL", "http://localhost:8080/login"
        )

        params = {"post_logout_redirect_uri": post_logout_redirect}
        if id_token:
            params["id_token_hint"] = id_token

        return f"{logout_url}?{urlencode(params)}"

    async def _get_jwks(self) -> dict[str, Any]:
        now = time.time()
        if self._jwks_cache and now < self._jwks_cache_expires_at:
            return self._jwks_cache

        jwks_url = f"{self.issuer.rstrip('/')}/.well-known/jwks.json"
        async with httpx.AsyncClient() as client:
            response = await client.get(jwks_url)
            if response.status_code != 200:
                logger.error("JWKS fetch failed: %s", response.text)
                raise ValueError("Failed to fetch JWKS")
            jwks = response.json()

        if not isinstance(jwks, dict) or "keys" not in jwks:
            raise ValueError("Invalid JWKS payload")

        self._jwks_cache = jwks
        self._jwks_cache_expires_at = now + self.JWKS_CACHE_TTL
        return jwks

    async def _validate_id_token(
        self,
        id_token: str | None,
        expected_nonce: str,
    ) -> tuple[bool, str | None]:
        if not id_token:
            return False, "Missing id_token"

        try:
            jwks = await self._get_jwks()
            header = jwt.get_unverified_header(id_token)
            kid = header.get("kid")
            if not kid:
                return False, "id_token missing kid"
            key = next(
                (candidate for candidate in jwks.get("keys", []) if candidate.get("kid") == kid),
                None,
            )
            if not key:
                return False, "id_token key not found"
            alg = header.get("alg")
            if alg not in self.ALLOWED_ALGORITHMS:
                return False, "Unsupported algorithm"
            claims = jwt.decode(
                id_token,
                key,
                algorithms=self.ALLOWED_ALGORITHMS,
                audience=self.client_id,
                issuer=self.issuer,
            )
        except JWTError as exc:
            logger.warning("Invalid id_token: %s", exc)
            return False, "Invalid id_token"
        except Exception as exc:
            logger.error("id_token validation error: %s", exc)
            return False, "Invalid id_token"

        exp_value = claims.get("exp")
        try:
            exp = float(exp_value)
        except (TypeError, ValueError):
            return False, "Invalid id_token expiry"

        if exp <= time.time():
            return False, "Expired id_token"

        nonce = claims.get("nonce")
        if not nonce or nonce != expected_nonce:
            return False, "Invalid nonce"

        return True, None

    def _map_role(self, userinfo: dict[str, Any]) -> str:
        roles = userinfo.get("roles", [])
        if "admin" in roles:
            return "admin"
        if "trader" in roles:
            return "trader"
        return "viewer"

    @staticmethod
    def _normalize_redirect_uri(value: str) -> str:
        """Normalize redirect URIs for comparison (strip query/fragment, normalize path)."""
        parts = urlsplit(value)
        path = parts.path.rstrip("/") or "/"
        return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def _redis_from_url(url: str, *, decode_responses: bool) -> redis.Redis:
    from_url = cast(Callable[..., redis.Redis], redis.Redis.from_url)
    return from_url(url, decode_responses=decode_responses)
