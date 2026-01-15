"""Tests for JWKS (JSON Web Key Set) validator.

Tests cover security-critical functionality:
- Algorithm pinning (RS256/ES256 only, HS256 rejected)
- JWKS caching with TTL
- Key rotation refresh (kid mismatch triggers refresh)
- Nonce enforcement
- Issuer/audience mismatch cases
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from libs.platform.web_console_auth.jwks_validator import JWKSValidator


def _generate_rsa_keypair() -> tuple[rsa.RSAPrivateKey, dict[str, Any]]:
    """Generate RSA key pair and JWK for testing."""
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    public_key = private_key.public_key()

    # Get public key numbers for JWK
    public_numbers = public_key.public_numbers()

    import base64

    def _int_to_base64url(n: int, length: int) -> str:
        return base64.urlsafe_b64encode(n.to_bytes(length, byteorder="big")).decode().rstrip("=")

    jwk = {
        "kty": "RSA",
        "kid": "test-key-1",
        "use": "sig",
        "alg": "RS256",
        "n": _int_to_base64url(public_numbers.n, 256),
        "e": _int_to_base64url(public_numbers.e, 3),
    }

    return private_key, jwk


def _generate_ec_keypair() -> tuple[ec.EllipticCurvePrivateKey, dict[str, Any]]:
    """Generate EC key pair and JWK for testing."""
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_key = private_key.public_key()

    # Get public key numbers for JWK
    public_numbers = public_key.public_numbers()

    import base64

    def _int_to_base64url(n: int, length: int) -> str:
        return base64.urlsafe_b64encode(n.to_bytes(length, byteorder="big")).decode().rstrip("=")

    jwk = {
        "kty": "EC",
        "kid": "test-ec-key-1",
        "use": "sig",
        "alg": "ES256",
        "crv": "P-256",
        "x": _int_to_base64url(public_numbers.x, 32),
        "y": _int_to_base64url(public_numbers.y, 32),
    }

    return private_key, jwk


def _create_test_token(
    private_key: Any,
    algorithm: str,
    kid: str,
    claims: dict[str, Any],
) -> str:
    """Create a signed test JWT token."""
    headers = {"kid": kid, "alg": algorithm}
    return jwt.encode(claims, private_key, algorithm=algorithm, headers=headers)


class TestJWKSValidatorInit:
    """Test JWKSValidator initialization."""

    def test_default_initialization(self) -> None:
        """Test default initialization with required parameters."""
        validator = JWKSValidator(auth0_domain="test.us.auth0.com")

        assert validator.auth0_domain == "test.us.auth0.com"
        assert validator.jwks_url == "https://test.us.auth0.com/.well-known/jwks.json"
        assert validator.cache_ttl == timedelta(hours=12)
        assert validator.allowed_algorithms == ["RS256", "ES256"]

    def test_custom_cache_ttl(self) -> None:
        """Test custom cache TTL."""
        validator = JWKSValidator(auth0_domain="test.us.auth0.com", cache_ttl_hours=6)
        assert validator.cache_ttl == timedelta(hours=6)

    def test_custom_algorithms(self) -> None:
        """Test custom allowed algorithms."""
        validator = JWKSValidator(
            auth0_domain="test.us.auth0.com",
            allowed_algorithms=["RS256"],
        )
        assert validator.allowed_algorithms == ["RS256"]


class TestAlgorithmPinning:
    """Test algorithm pinning security feature."""

    @pytest.mark.asyncio()
    async def test_rs256_allowed(self) -> None:
        """Test RS256 algorithm is allowed."""
        validator = JWKSValidator(auth0_domain="test.us.auth0.com")
        private_key, jwk = _generate_rsa_keypair()

        claims = {
            "sub": "user123",
            "aud": "test-client-id",
            "iss": "https://test.us.auth0.com/",
            "exp": int(time.time()) + 3600,
            "nonce": "test-nonce",
        }

        token = _create_test_token(private_key, "RS256", jwk["kid"], claims)

        with patch.object(validator, "get_jwks", new_callable=AsyncMock) as mock_jwks:
            mock_jwks.return_value = {"keys": [jwk]}

            result = await validator.validate_id_token(
                id_token=token,
                expected_nonce="test-nonce",
                expected_audience="test-client-id",
                expected_issuer="https://test.us.auth0.com/",
            )

            assert result["sub"] == "user123"

    @pytest.mark.asyncio()
    async def test_es256_allowed(self) -> None:
        """Test ES256 algorithm is allowed."""
        validator = JWKSValidator(auth0_domain="test.us.auth0.com")
        private_key, jwk = _generate_ec_keypair()

        claims = {
            "sub": "user456",
            "aud": "test-client-id",
            "iss": "https://test.us.auth0.com/",
            "exp": int(time.time()) + 3600,
            "nonce": "test-nonce",
        }

        token = _create_test_token(private_key, "ES256", jwk["kid"], claims)

        with patch.object(validator, "get_jwks", new_callable=AsyncMock) as mock_jwks:
            mock_jwks.return_value = {"keys": [jwk]}

            result = await validator.validate_id_token(
                id_token=token,
                expected_nonce="test-nonce",
                expected_audience="test-client-id",
                expected_issuer="https://test.us.auth0.com/",
            )

            assert result["sub"] == "user456"

    @pytest.mark.asyncio()
    async def test_hs256_rejected(self) -> None:
        """Test HS256 algorithm is rejected (security critical)."""
        validator = JWKSValidator(auth0_domain="test.us.auth0.com")

        # Create HS256 token (attacker tries to use symmetric algorithm)
        secret = "attacker-secret"
        claims = {
            "sub": "attacker",
            "aud": "test-client-id",
            "iss": "https://test.us.auth0.com/",
            "exp": int(time.time()) + 3600,
        }
        token = jwt.encode(claims, secret, algorithm="HS256", headers={"kid": "fake-kid", "alg": "HS256"})

        with patch.object(validator, "get_jwks", new_callable=AsyncMock) as mock_jwks:
            mock_jwks.return_value = {"keys": []}

            with pytest.raises(jwt.InvalidAlgorithmError) as exc_info:
                await validator.validate_id_token(
                    id_token=token,
                    expected_nonce=None,
                    expected_audience="test-client-id",
                    expected_issuer="https://test.us.auth0.com/",
                )

            assert "HS256" in str(exc_info.value)
            assert "not allowed" in str(exc_info.value)

    @pytest.mark.asyncio()
    async def test_none_algorithm_rejected(self) -> None:
        """Test 'none' algorithm is rejected (critical security vulnerability)."""
        validator = JWKSValidator(auth0_domain="test.us.auth0.com")

        # Create unsigned token with 'none' algorithm
        claims = {
            "sub": "attacker",
            "aud": "test-client-id",
            "iss": "https://test.us.auth0.com/",
            "exp": int(time.time()) + 3600,
        }
        # Manually construct a 'none' algorithm token
        import base64
        import json

        header = base64.urlsafe_b64encode(json.dumps({"alg": "none", "kid": "fake"}).encode()).decode().rstrip("=")
        payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
        token = f"{header}.{payload}."

        with patch.object(validator, "get_jwks", new_callable=AsyncMock) as mock_jwks:
            mock_jwks.return_value = {"keys": []}

            with pytest.raises(jwt.InvalidAlgorithmError):
                await validator.validate_id_token(
                    id_token=token,
                    expected_nonce=None,
                    expected_audience="test-client-id",
                    expected_issuer="https://test.us.auth0.com/",
                )


class TestJWKSCaching:
    """Test JWKS caching behavior."""

    @pytest.mark.asyncio()
    async def test_cache_hit(self) -> None:
        """Test JWKS cache hit returns cached data."""
        validator = JWKSValidator(auth0_domain="test.us.auth0.com")

        # Pre-populate cache
        validator._jwks_cache = {"keys": [{"kid": "cached-key"}]}
        validator._cache_expires_at = datetime.now(UTC) + timedelta(hours=6)

        result = await validator.get_jwks()

        assert result == {"keys": [{"kid": "cached-key"}]}

    @pytest.mark.asyncio()
    async def test_cache_miss_fetches_jwks(self) -> None:
        """Test cache miss triggers JWKS fetch."""
        validator = JWKSValidator(auth0_domain="test.us.auth0.com")

        mock_response = {"keys": [{"kid": "new-key"}]}

        with patch.object(validator, "_fetch_jwks", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = mock_response

            result = await validator.get_jwks()

            mock_fetch.assert_called_once()
            assert result == mock_response
            assert validator._jwks_cache == mock_response

    @pytest.mark.asyncio()
    async def test_cache_expiry_triggers_refresh(self) -> None:
        """Test expired cache triggers refresh."""
        validator = JWKSValidator(auth0_domain="test.us.auth0.com")

        # Set expired cache
        validator._jwks_cache = {"keys": [{"kid": "old-key"}]}
        validator._cache_expires_at = datetime.now(UTC) - timedelta(hours=1)

        mock_response = {"keys": [{"kid": "fresh-key"}]}

        with patch.object(validator, "_fetch_jwks", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = mock_response

            result = await validator.get_jwks()

            mock_fetch.assert_called_once()
            assert result == mock_response

    @pytest.mark.asyncio()
    async def test_force_refresh_bypasses_cache(self) -> None:
        """Test force_refresh=True bypasses valid cache."""
        validator = JWKSValidator(auth0_domain="test.us.auth0.com")

        # Set valid cache
        validator._jwks_cache = {"keys": [{"kid": "cached-key"}]}
        validator._cache_expires_at = datetime.now(UTC) + timedelta(hours=6)

        mock_response = {"keys": [{"kid": "forced-fresh-key"}]}

        with patch.object(validator, "_fetch_jwks", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = mock_response

            result = await validator.get_jwks(force_refresh=True)

            mock_fetch.assert_called_once()
            assert result == mock_response


class TestKeyRotation:
    """Test key rotation handling."""

    @pytest.mark.asyncio()
    async def test_kid_mismatch_triggers_refresh(self) -> None:
        """Test key ID mismatch triggers JWKS cache refresh."""
        validator = JWKSValidator(auth0_domain="test.us.auth0.com")
        private_key, jwk = _generate_rsa_keypair()

        claims = {
            "sub": "user789",
            "aud": "test-client-id",
            "iss": "https://test.us.auth0.com/",
            "exp": int(time.time()) + 3600,
            "nonce": "test-nonce",
        }

        token = _create_test_token(private_key, "RS256", jwk["kid"], claims)

        # First call returns empty JWKS, second call (refresh) returns the key
        call_count = 0

        async def mock_get_jwks(force_refresh: bool = False) -> dict[str, Any]:
            nonlocal call_count
            call_count += 1
            if call_count == 1 and not force_refresh:
                return {"keys": [{"kid": "old-key-id"}]}  # Key not found
            return {"keys": [jwk]}  # Key found after refresh

        with patch.object(validator, "get_jwks", side_effect=mock_get_jwks):
            result = await validator.validate_id_token(
                id_token=token,
                expected_nonce="test-nonce",
                expected_audience="test-client-id",
                expected_issuer="https://test.us.auth0.com/",
            )

            assert result["sub"] == "user789"
            # Should have called get_jwks twice (initial + refresh)
            assert call_count == 2

    @pytest.mark.asyncio()
    async def test_key_not_found_after_refresh_raises(self) -> None:
        """Test InvalidKeyError when key not found even after refresh."""
        validator = JWKSValidator(auth0_domain="test.us.auth0.com")
        private_key, jwk = _generate_rsa_keypair()

        claims = {
            "sub": "user999",
            "aud": "test-client-id",
            "iss": "https://test.us.auth0.com/",
            "exp": int(time.time()) + 3600,
        }

        token = _create_test_token(private_key, "RS256", "unknown-kid", claims)

        with patch.object(validator, "get_jwks", new_callable=AsyncMock) as mock_jwks:
            # Return empty keys even after refresh
            mock_jwks.return_value = {"keys": []}

            with pytest.raises(jwt.InvalidKeyError) as exc_info:
                await validator.validate_id_token(
                    id_token=token,
                    expected_nonce=None,
                    expected_audience="test-client-id",
                    expected_issuer="https://test.us.auth0.com/",
                )

            assert "unknown-kid" in str(exc_info.value)


class TestNonceValidation:
    """Test nonce validation for replay protection."""

    @pytest.mark.asyncio()
    async def test_nonce_match_succeeds(self) -> None:
        """Test matching nonce passes validation."""
        validator = JWKSValidator(auth0_domain="test.us.auth0.com")
        private_key, jwk = _generate_rsa_keypair()

        claims = {
            "sub": "user123",
            "aud": "test-client-id",
            "iss": "https://test.us.auth0.com/",
            "exp": int(time.time()) + 3600,
            "nonce": "expected-nonce-value",
        }

        token = _create_test_token(private_key, "RS256", jwk["kid"], claims)

        with patch.object(validator, "get_jwks", new_callable=AsyncMock) as mock_jwks:
            mock_jwks.return_value = {"keys": [jwk]}

            result = await validator.validate_id_token(
                id_token=token,
                expected_nonce="expected-nonce-value",
                expected_audience="test-client-id",
                expected_issuer="https://test.us.auth0.com/",
            )

            assert result["nonce"] == "expected-nonce-value"

    @pytest.mark.asyncio()
    async def test_nonce_mismatch_raises(self) -> None:
        """Test nonce mismatch raises InvalidTokenError."""
        validator = JWKSValidator(auth0_domain="test.us.auth0.com")
        private_key, jwk = _generate_rsa_keypair()

        claims = {
            "sub": "user123",
            "aud": "test-client-id",
            "iss": "https://test.us.auth0.com/",
            "exp": int(time.time()) + 3600,
            "nonce": "wrong-nonce",
        }

        token = _create_test_token(private_key, "RS256", jwk["kid"], claims)

        with patch.object(validator, "get_jwks", new_callable=AsyncMock) as mock_jwks:
            mock_jwks.return_value = {"keys": [jwk]}

            with pytest.raises(jwt.InvalidTokenError) as exc_info:
                await validator.validate_id_token(
                    id_token=token,
                    expected_nonce="correct-nonce",
                    expected_audience="test-client-id",
                    expected_issuer="https://test.us.auth0.com/",
                )

            assert "Nonce mismatch" in str(exc_info.value)

    @pytest.mark.asyncio()
    async def test_nonce_none_skips_validation(self) -> None:
        """Test expected_nonce=None skips nonce validation (for refresh flows)."""
        validator = JWKSValidator(auth0_domain="test.us.auth0.com")
        private_key, jwk = _generate_rsa_keypair()

        claims = {
            "sub": "user123",
            "aud": "test-client-id",
            "iss": "https://test.us.auth0.com/",
            "exp": int(time.time()) + 3600,
            # No nonce in claims
        }

        token = _create_test_token(private_key, "RS256", jwk["kid"], claims)

        with patch.object(validator, "get_jwks", new_callable=AsyncMock) as mock_jwks:
            mock_jwks.return_value = {"keys": [jwk]}

            # Should not raise even without nonce
            result = await validator.validate_id_token(
                id_token=token,
                expected_nonce=None,  # Skip nonce validation
                expected_audience="test-client-id",
                expected_issuer="https://test.us.auth0.com/",
            )

            assert result["sub"] == "user123"


class TestIssuerAudienceValidation:
    """Test issuer and audience claim validation."""

    @pytest.mark.asyncio()
    async def test_issuer_mismatch_raises(self) -> None:
        """Test issuer mismatch raises InvalidIssuerError."""
        validator = JWKSValidator(auth0_domain="test.us.auth0.com")
        private_key, jwk = _generate_rsa_keypair()

        claims = {
            "sub": "user123",
            "aud": "test-client-id",
            "iss": "https://attacker.auth0.com/",  # Wrong issuer
            "exp": int(time.time()) + 3600,
        }

        token = _create_test_token(private_key, "RS256", jwk["kid"], claims)

        with patch.object(validator, "get_jwks", new_callable=AsyncMock) as mock_jwks:
            mock_jwks.return_value = {"keys": [jwk]}

            with pytest.raises(jwt.InvalidIssuerError):
                await validator.validate_id_token(
                    id_token=token,
                    expected_nonce=None,
                    expected_audience="test-client-id",
                    expected_issuer="https://test.us.auth0.com/",
                )

    @pytest.mark.asyncio()
    async def test_audience_mismatch_raises(self) -> None:
        """Test audience mismatch raises InvalidAudienceError."""
        validator = JWKSValidator(auth0_domain="test.us.auth0.com")
        private_key, jwk = _generate_rsa_keypair()

        claims = {
            "sub": "user123",
            "aud": "wrong-client-id",  # Wrong audience
            "iss": "https://test.us.auth0.com/",
            "exp": int(time.time()) + 3600,
        }

        token = _create_test_token(private_key, "RS256", jwk["kid"], claims)

        with patch.object(validator, "get_jwks", new_callable=AsyncMock) as mock_jwks:
            mock_jwks.return_value = {"keys": [jwk]}

            with pytest.raises(jwt.InvalidAudienceError):
                await validator.validate_id_token(
                    id_token=token,
                    expected_nonce=None,
                    expected_audience="correct-client-id",
                    expected_issuer="https://test.us.auth0.com/",
                )


class TestTokenExpiry:
    """Test token expiry validation."""

    @pytest.mark.asyncio()
    async def test_expired_token_raises(self) -> None:
        """Test expired token raises ExpiredSignatureError."""
        validator = JWKSValidator(auth0_domain="test.us.auth0.com")
        private_key, jwk = _generate_rsa_keypair()

        claims = {
            "sub": "user123",
            "aud": "test-client-id",
            "iss": "https://test.us.auth0.com/",
            "exp": int(time.time()) - 3600,  # Expired 1 hour ago
        }

        token = _create_test_token(private_key, "RS256", jwk["kid"], claims)

        with patch.object(validator, "get_jwks", new_callable=AsyncMock) as mock_jwks:
            mock_jwks.return_value = {"keys": [jwk]}

            with pytest.raises(jwt.ExpiredSignatureError):
                await validator.validate_id_token(
                    id_token=token,
                    expected_nonce=None,
                    expected_audience="test-client-id",
                    expected_issuer="https://test.us.auth0.com/",
                )


class TestLoadSigningKey:
    """Test _load_signing_key method."""

    def test_load_rsa_key(self) -> None:
        """Test loading RSA key from JWK."""
        validator = JWKSValidator(auth0_domain="test.us.auth0.com")
        _, jwk = _generate_rsa_keypair()

        key = validator._load_signing_key(jwk, "RS256")

        assert key is not None

    def test_load_ec_key(self) -> None:
        """Test loading EC key from JWK."""
        validator = JWKSValidator(auth0_domain="test.us.auth0.com")
        _, jwk = _generate_ec_keypair()

        key = validator._load_signing_key(jwk, "ES256")

        assert key is not None

    def test_unsupported_algorithm_raises(self) -> None:
        """Test unsupported algorithm raises ValueError."""
        validator = JWKSValidator(auth0_domain="test.us.auth0.com")
        _, jwk = _generate_rsa_keypair()

        with pytest.raises(ValueError, match="Unsupported algorithm"):
            validator._load_signing_key(jwk, "PS256")

    def test_malformed_jwk_raises(self) -> None:
        """Test malformed JWK raises error during key loading."""
        validator = JWKSValidator(auth0_domain="test.us.auth0.com")

        # JWK with missing required fields
        malformed_jwk = {
            "kty": "RSA",
            "kid": "malformed-key",
            # Missing 'n' and 'e' which are required for RSA
        }

        with pytest.raises((ValueError, TypeError, jwt.InvalidKeyError)):
            validator._load_signing_key(malformed_jwk, "RS256")


class TestEdgeCases:
    """Test edge cases for robustness."""

    @pytest.mark.asyncio()
    async def test_missing_kid_header_raises(self) -> None:
        """Test token with missing kid header raises error."""
        validator = JWKSValidator(auth0_domain="test.us.auth0.com")
        private_key, jwk = _generate_rsa_keypair()

        claims = {
            "sub": "user123",
            "aud": "test-client-id",
            "iss": "https://test.us.auth0.com/",
            "exp": int(time.time()) + 3600,
        }

        # Create token without kid in header
        token = jwt.encode(
            claims,
            private_key,
            algorithm="RS256",
            headers={"alg": "RS256"},  # No 'kid'
        )

        with patch.object(validator, "get_jwks", new_callable=AsyncMock) as mock_jwks:
            mock_jwks.return_value = {"keys": [jwk]}

            # Should raise because kid=None won't match any key
            with pytest.raises(jwt.InvalidKeyError):
                await validator.validate_id_token(
                    id_token=token,
                    expected_nonce=None,
                    expected_audience="test-client-id",
                    expected_issuer="https://test.us.auth0.com/",
                )

    @pytest.mark.asyncio()
    async def test_empty_jwks_keys_raises(self) -> None:
        """Test empty JWKS keys list raises InvalidKeyError."""
        validator = JWKSValidator(auth0_domain="test.us.auth0.com")
        private_key, jwk = _generate_rsa_keypair()

        claims = {
            "sub": "user123",
            "aud": "test-client-id",
            "iss": "https://test.us.auth0.com/",
            "exp": int(time.time()) + 3600,
        }

        token = _create_test_token(private_key, "RS256", jwk["kid"], claims)

        with patch.object(validator, "get_jwks", new_callable=AsyncMock) as mock_jwks:
            # Return empty keys list
            mock_jwks.return_value = {"keys": []}

            with pytest.raises(jwt.InvalidKeyError):
                await validator.validate_id_token(
                    id_token=token,
                    expected_nonce=None,
                    expected_audience="test-client-id",
                    expected_issuer="https://test.us.auth0.com/",
                )

    @pytest.mark.asyncio()
    async def test_malformed_token_raises(self) -> None:
        """Test malformed token raises DecodeError."""
        validator = JWKSValidator(auth0_domain="test.us.auth0.com")
        _, jwk = _generate_rsa_keypair()

        # Completely malformed token
        malformed_token = "not.a.valid.jwt.token"

        with patch.object(validator, "get_jwks", new_callable=AsyncMock) as mock_jwks:
            mock_jwks.return_value = {"keys": [jwk]}

            with pytest.raises(jwt.DecodeError):
                await validator.validate_id_token(
                    id_token=malformed_token,
                    expected_nonce=None,
                    expected_audience="test-client-id",
                    expected_issuer="https://test.us.auth0.com/",
                )
