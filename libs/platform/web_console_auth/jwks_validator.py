"""JWKS (JSON Web Key Set) validator for ID token signature verification.

This module fetches and caches Auth0's public keys (JWKS) and validates
JWT signatures using the PyJWT library.

Security:
- JWKS caching: 12-hour TTL with kid rollover support
- Algorithm pinning: RS256 or ES256 only (no HS256)
- Algorithm-specific key loading: RSA vs EC
- Claim validation: iss, aud, exp, sub, nonce
- Signature verification: RSA or ECDSA public key from JWKS

References:
- JWKS: RFC 7517
- JWT: RFC 7519
- OIDC ID Token: https://openid.net/specs/openid-connect-core-1_0.html#IDToken
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt
from jwt.algorithms import ECAlgorithm, RSAAlgorithm

logger = logging.getLogger(__name__)


class JWKSValidator:
    """Validates JWT ID tokens using Auth0 JWKS."""

    def __init__(
        self,
        auth0_domain: str,
        cache_ttl_hours: int = 12,
        allowed_algorithms: list[str] | None = None,
    ):
        """Initialize JWKS validator.

        Args:
            auth0_domain: Auth0 domain (e.g., "trading-platform.us.auth0.com")
            cache_ttl_hours: JWKS cache TTL in hours (default: 12)
            allowed_algorithms: Allowed signing algorithms (default: RS256, ES256)
        """
        self.auth0_domain = auth0_domain
        self.jwks_url = f"https://{auth0_domain}/.well-known/jwks.json"
        self.cache_ttl = timedelta(hours=cache_ttl_hours)
        self.allowed_algorithms = allowed_algorithms or ["RS256", "ES256"]

        # JWKS cache
        self._jwks_cache: dict[str, Any] | None = None
        self._cache_expires_at: datetime | None = None

    async def _fetch_jwks(self) -> dict[str, Any]:
        """Fetch JWKS from Auth0 .well-known endpoint.

        Returns:
            JWKS dictionary with public keys

        Raises:
            httpx.HTTPStatusError: If JWKS fetch fails
        """
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(self.jwks_url)
            response.raise_for_status()
            return response.json()  # type: ignore[no-any-return]

    async def get_jwks(self, force_refresh: bool = False) -> dict[str, Any]:
        """Get JWKS with 12-hour caching.

        Args:
            force_refresh: Force cache refresh (for key rotation)

        Returns:
            JWKS dictionary
        """
        now = datetime.now(UTC)

        # Check cache validity
        if not force_refresh and self._jwks_cache and self._cache_expires_at:
            if now < self._cache_expires_at:
                return self._jwks_cache

        # Fetch fresh JWKS
        self._jwks_cache = await self._fetch_jwks()
        self._cache_expires_at = now + self.cache_ttl

        logger.info(
            "JWKS fetched and cached",
            extra={
                "auth0_domain": self.auth0_domain,
                "cache_ttl_hours": self.cache_ttl.total_seconds() / 3600,
                "expires_at": self._cache_expires_at.isoformat(),
            },
        )

        return self._jwks_cache

    def _load_signing_key(self, jwk: dict[str, Any], alg: str) -> Any:
        """Load signing key from JWK based on algorithm.

        Supports both RS256 (RSA) and ES256 (ECDSA) algorithms.

        Args:
            jwk: JSON Web Key dictionary
            alg: Algorithm from JWT header (RS256 or ES256)

        Returns:
            Signing key object (RSA or EC)

        Raises:
            ValueError: If algorithm not supported
        """
        if alg == "RS256":
            return RSAAlgorithm.from_jwk(jwk)
        elif alg == "ES256":
            return ECAlgorithm.from_jwk(jwk)
        else:
            raise ValueError(f"Unsupported algorithm: {alg}")

    async def validate_id_token(
        self,
        id_token: str,
        expected_nonce: str | None,
        expected_audience: str,
        expected_issuer: str,
    ) -> dict[str, Any]:
        """Validate ID token signature and claims.

        Args:
            id_token: JWT ID token from Auth0
            expected_nonce: Nonce from original authorization request (None for refresh flows)
            expected_audience: Expected audience (client_id)
            expected_issuer: Expected issuer (https://{auth0_domain}/)

        Returns:
            Validated ID token claims

        Raises:
            jwt.InvalidTokenError: If validation fails (signature, claims, expiry)
        """
        # Get signing key from JWKS
        jwks = await self.get_jwks()

        # Decode header to get kid (key ID) and alg
        unverified_header = jwt.get_unverified_header(id_token)
        kid = unverified_header.get("kid")
        alg = unverified_header.get("alg")

        # Algorithm pinning: ONLY RS256 or ES256
        if alg not in self.allowed_algorithms:
            raise jwt.InvalidAlgorithmError(
                f"Algorithm {alg} not allowed. Only {self.allowed_algorithms} permitted."
            )

        # Find matching key in JWKS
        signing_key = None
        jwk_match = None
        for key in jwks.get("keys", []):
            if key.get("kid") == kid:
                jwk_match = key
                break

        if jwk_match:
            # Algorithm-specific key loading (RS256 vs ES256)
            try:
                signing_key = self._load_signing_key(jwk_match, alg)
            except (ValueError, TypeError) as e:
                # Key loading errors
                # ValueError: Unsupported algorithm or invalid JWK format
                # TypeError: Type mismatch in key loading
                logger.error(
                    "failed_to_load_signing_key",
                    extra={
                        "kid": kid,
                        "alg": alg,
                        "error": str(e),
                        "error_type": type(e).__name__,
                    },
                )
                raise

        if not signing_key:
            # Key rotation: Refresh JWKS cache and retry once
            logger.warning(
                "Signing key not found in JWKS cache, refreshing...",
                extra={"kid": kid, "alg": alg},
            )
            jwks = await self.get_jwks(force_refresh=True)
            for key in jwks.get("keys", []):
                if key.get("kid") == kid:
                    jwk_match = key
                    signing_key = self._load_signing_key(key, alg)
                    break

        if not signing_key:
            raise jwt.InvalidKeyError(f"Signing key with kid={kid} not found in JWKS")

        # Decode and validate JWT
        try:
            claims: dict[str, Any] = jwt.decode(
                id_token,
                key=signing_key,
                algorithms=self.allowed_algorithms,
                audience=expected_audience,
                issuer=expected_issuer,
                options={
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_aud": True,
                    "verify_iss": True,
                },
            )
        except jwt.ExpiredSignatureError:
            logger.error("ID token expired")
            raise
        except jwt.InvalidAudienceError:
            logger.error("ID token audience mismatch")
            raise
        except jwt.InvalidIssuerError:
            logger.error("ID token issuer mismatch")
            raise

        # Validate nonce (replay protection) - only when expected_nonce is provided
        # During token refresh, expected_nonce=None since nonce is only for initial authorization
        if expected_nonce is not None:
            if claims.get("nonce") != expected_nonce:
                raise jwt.InvalidTokenError(
                    f"Nonce mismatch: expected {expected_nonce}, got {claims.get('nonce')}"
                )

        logger.info(
            "ID token validated successfully",
            extra={
                "user_id": claims.get("sub"),
                "email": claims.get("email"),
                "alg": alg,
            },
        )

        return claims
