"""JWKS (JSON Web Key Set) validator shared across services."""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt
from jwt.algorithms import ECAlgorithm, RSAAlgorithm

logger = logging.getLogger(__name__)


class JWKSValidator:
    """Validates JWT ID tokens using JWKS."""

    def __init__(
        self,
        auth0_domain: str,
        cache_ttl_hours: int = 12,
        allowed_algorithms: list[str] | None = None,
    ):
        self.auth0_domain = auth0_domain
        self.jwks_url = f"https://{auth0_domain}/.well-known/jwks.json"
        self.cache_ttl = timedelta(hours=cache_ttl_hours)
        self.allowed_algorithms = allowed_algorithms or ["RS256", "ES256"]

        self._jwks_cache: dict[str, Any] | None = None
        self._cache_expires_at: datetime | None = None

    async def _fetch_jwks(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(self.jwks_url)
            response.raise_for_status()
            return response.json()  # type: ignore[no-any-return]

    async def get_jwks(self, force_refresh: bool = False) -> dict[str, Any]:
        now = datetime.now(UTC)

        if not force_refresh and self._jwks_cache and self._cache_expires_at:
            if now < self._cache_expires_at:
                return self._jwks_cache

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
        if alg == "RS256":
            return RSAAlgorithm.from_jwk(jwk)
        if alg == "ES256":
            return ECAlgorithm.from_jwk(jwk)
        raise ValueError(f"Unsupported algorithm: {alg}")

    async def validate_id_token(
        self,
        id_token: str,
        expected_nonce: str | None,
        expected_audience: str,
        expected_issuer: str,
    ) -> dict[str, Any]:
        jwks = await self.get_jwks()

        unverified_header = jwt.get_unverified_header(id_token)
        kid = unverified_header.get("kid")
        alg = unverified_header.get("alg")

        if alg not in self.allowed_algorithms:
            raise jwt.InvalidAlgorithmError(
                f"Algorithm {alg} not allowed. Only {self.allowed_algorithms} permitted."
            )

        signing_key = None
        jwk_match = None
        for key in jwks.get("keys", []):
            if key.get("kid") == kid:
                jwk_match = key
                break

        if jwk_match:
            signing_key = self._load_signing_key(jwk_match, alg)

        if not signing_key:
            logger.warning(
                "Signing key not found in JWKS cache, refreshing...",
                extra={"kid": kid, "alg": alg},
            )
            jwks = await self.get_jwks(force_refresh=True)
            for key in jwks.get("keys", []):
                if key.get("kid") == kid:
                    signing_key = self._load_signing_key(key, alg)
                    break

        if not signing_key:
            raise jwt.InvalidKeyError(f"Signing key with kid={kid} not found in JWKS")

        try:
            claims = jwt.decode(
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

        return claims  # type: ignore[no-any-return]


__all__ = ["JWKSValidator"]
