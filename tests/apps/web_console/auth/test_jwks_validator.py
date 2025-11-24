"""Tests for JWKS validator with RS256 and ES256 support.

Tests verify JWT ID token validation with proper signature verification,
algorithm pinning, and nonce validation.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import httpx
import jwt
import pytest
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from apps.web_console.auth.jwks_validator import JWKSValidator


@pytest.fixture()
def rsa_keypair():
    """Generate RSA key pair for RS256 testing."""
    private_key = rsa.generate_private_key(
        public_exponent=65537, key_size=2048, backend=default_backend()
    )
    public_key = private_key.public_key()
    return private_key, public_key


@pytest.fixture()
def ec_keypair():
    """Generate EC key pair for ES256 testing."""
    private_key = ec.generate_private_key(ec.SECP256R1(), backend=default_backend())
    public_key = private_key.public_key()
    return private_key, public_key


@pytest.fixture()
def mock_jwks_rs256(rsa_keypair):
    """Mock JWKS response with RS256 key."""
    _, public_key = rsa_keypair

    # Get public key in PEM format
    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM, format=serialization.PublicFormat.SubjectPublicKeyInfo
    )

    # Convert to JWK format (simplified for testing)
    from jwt.algorithms import RSAAlgorithm

    jwk = RSAAlgorithm.to_jwk(public_key)

    import json

    jwk_dict = json.loads(jwk)
    jwk_dict["kid"] = "test-rs256-kid"
    jwk_dict["use"] = "sig"
    jwk_dict["alg"] = "RS256"

    return {"keys": [jwk_dict]}


@pytest.fixture()
def mock_jwks_es256(ec_keypair):
    """Mock JWKS response with ES256 key."""
    _, public_key = ec_keypair

    # Convert to JWK format
    from jwt.algorithms import ECAlgorithm

    jwk = ECAlgorithm.to_jwk(public_key)

    import json

    jwk_dict = json.loads(jwk)
    jwk_dict["kid"] = "test-es256-kid"
    jwk_dict["use"] = "sig"
    jwk_dict["alg"] = "ES256"

    return {"keys": [jwk_dict]}


def create_id_token(private_key, algorithm: str, kid: str, claims: dict) -> str:
    """Create a signed ID token for testing.

    Args:
        private_key: Private key (RSA or EC)
        algorithm: "RS256" or "ES256"
        kid: Key ID
        claims: Token claims

    Returns:
        Signed JWT ID token
    """
    headers = {
        "kid": kid,
        "alg": algorithm,
    }

    return jwt.encode(
        claims,
        private_key,
        algorithm=algorithm,
        headers=headers,
    )


class TestJWKSValidator:
    """Test JWKS validator initialization and configuration."""

    def test_validator_initialization(self):
        """Test JWKS validator initializes with correct defaults."""
        validator = JWKSValidator(auth0_domain="test.auth0.com")

        assert validator.auth0_domain == "test.auth0.com"
        assert validator.jwks_url == "https://test.auth0.com/.well-known/jwks.json"
        assert validator.cache_ttl == timedelta(hours=12)
        assert validator.allowed_algorithms == ["RS256", "ES256"]

    def test_validator_custom_ttl(self):
        """Test JWKS validator accepts custom cache TTL."""
        validator = JWKSValidator(auth0_domain="test.auth0.com", cache_ttl_hours=6)

        assert validator.cache_ttl == timedelta(hours=6)

    def test_validator_custom_algorithms(self):
        """Test JWKS validator accepts custom allowed algorithms."""
        validator = JWKSValidator(auth0_domain="test.auth0.com", allowed_algorithms=["RS256"])

        assert validator.allowed_algorithms == ["RS256"]


class TestJWKSFetching:
    """Test JWKS fetching and caching."""

    @pytest.mark.asyncio()
    async def test_fetch_jwks_success(self, mock_jwks_rs256, respx_mock):
        """Test JWKS fetch from Auth0 endpoint."""
        validator = JWKSValidator(auth0_domain="test.auth0.com")

        # Mock the JWKS endpoint using respx
        respx_mock.get(validator.jwks_url).mock(
            return_value=httpx.Response(200, json=mock_jwks_rs256)
        )

        jwks = await validator._fetch_jwks()

        assert jwks == mock_jwks_rs256

    @pytest.mark.asyncio()
    async def test_get_jwks_caching(self, mock_jwks_rs256):
        """Test JWKS caching with 12-hour TTL."""
        validator = JWKSValidator(auth0_domain="test.auth0.com")

        with patch("httpx.AsyncClient.get") as mock_get:
            mock_response = AsyncMock()
            mock_response.json.return_value = mock_jwks_rs256
            mock_response.raise_for_status.return_value = None
            mock_get.return_value = mock_response

            # First call: should fetch from endpoint
            jwks1 = await validator.get_jwks()
            assert mock_get.call_count == 1

            # Second call: should use cache
            jwks2 = await validator.get_jwks()
            assert mock_get.call_count == 1  # No additional call
            assert jwks1 == jwks2

    @pytest.mark.asyncio()
    async def test_get_jwks_force_refresh(self, mock_jwks_rs256):
        """Test JWKS force refresh bypasses cache."""
        validator = JWKSValidator(auth0_domain="test.auth0.com")

        with patch("httpx.AsyncClient.get") as mock_get:
            mock_response = AsyncMock()
            mock_response.json.return_value = mock_jwks_rs256
            mock_response.raise_for_status.return_value = None
            mock_get.return_value = mock_response

            # First call
            await validator.get_jwks()
            assert mock_get.call_count == 1

            # Force refresh
            await validator.get_jwks(force_refresh=True)
            assert mock_get.call_count == 2  # Should fetch again


class TestRS256Validation:
    """Test RS256 ID token validation."""

    @pytest.mark.asyncio()
    async def test_validate_rs256_token_success(self, rsa_keypair, mock_jwks_rs256):
        """Test successful RS256 ID token validation."""
        private_key, _ = rsa_keypair
        validator = JWKSValidator(auth0_domain="test.auth0.com")

        # Create valid ID token
        now = datetime.now(UTC)
        claims = {
            "iss": "https://test.auth0.com/",
            "sub": "auth0|12345",
            "aud": "test-client-id",
            "exp": now + timedelta(hours=1),
            "iat": now,
            "nonce": "test-nonce-123",
            "email": "user@example.com",
        }

        id_token = create_id_token(private_key, "RS256", "test-rs256-kid", claims)

        # Mock JWKS fetch
        with patch.object(validator, "get_jwks", return_value=mock_jwks_rs256):
            result = await validator.validate_id_token(
                id_token=id_token,
                expected_nonce="test-nonce-123",
                expected_audience="test-client-id",
                expected_issuer="https://test.auth0.com/",
            )

            assert result["sub"] == "auth0|12345"
            assert result["email"] == "user@example.com"
            assert result["nonce"] == "test-nonce-123"

    @pytest.mark.asyncio()
    async def test_validate_rs256_token_expired(self, rsa_keypair, mock_jwks_rs256):
        """Test RS256 ID token validation with expired token."""
        private_key, _ = rsa_keypair
        validator = JWKSValidator(auth0_domain="test.auth0.com")

        # Create expired token
        now = datetime.now(UTC)
        claims = {
            "iss": "https://test.auth0.com/",
            "sub": "auth0|12345",
            "aud": "test-client-id",
            "exp": now - timedelta(hours=1),  # Expired
            "iat": now - timedelta(hours=2),
            "nonce": "test-nonce-123",
        }

        id_token = create_id_token(private_key, "RS256", "test-rs256-kid", claims)

        with patch.object(validator, "get_jwks", return_value=mock_jwks_rs256):
            with pytest.raises(jwt.ExpiredSignatureError):
                await validator.validate_id_token(
                    id_token=id_token,
                    expected_nonce="test-nonce-123",
                    expected_audience="test-client-id",
                    expected_issuer="https://test.auth0.com/",
                )


class TestES256Validation:
    """Test ES256 ID token validation."""

    @pytest.mark.asyncio()
    async def test_validate_es256_token_success(self, ec_keypair, mock_jwks_es256):
        """Test successful ES256 ID token validation."""
        private_key, _ = ec_keypair
        validator = JWKSValidator(auth0_domain="test.auth0.com")

        # Create valid ID token
        now = datetime.now(UTC)
        claims = {
            "iss": "https://test.auth0.com/",
            "sub": "auth0|67890",
            "aud": "test-client-id",
            "exp": now + timedelta(hours=1),
            "iat": now,
            "nonce": "test-nonce-456",
            "email": "user2@example.com",
        }

        id_token = create_id_token(private_key, "ES256", "test-es256-kid", claims)

        # Mock JWKS fetch
        with patch.object(validator, "get_jwks", return_value=mock_jwks_es256):
            result = await validator.validate_id_token(
                id_token=id_token,
                expected_nonce="test-nonce-456",
                expected_audience="test-client-id",
                expected_issuer="https://test.auth0.com/",
            )

            assert result["sub"] == "auth0|67890"
            assert result["email"] == "user2@example.com"
            assert result["nonce"] == "test-nonce-456"


class TestAlgorithmPinning:
    """Test algorithm pinning and rejection."""

    @pytest.mark.asyncio()
    async def test_reject_hs256_algorithm(self, rsa_keypair):
        """Test that HS256 algorithm is rejected."""
        private_key, _ = rsa_keypair
        validator = JWKSValidator(auth0_domain="test.auth0.com")

        # Create token with HS256 (symmetric key - insecure for public use)
        now = datetime.now(UTC)
        claims = {
            "iss": "https://test.auth0.com/",
            "sub": "auth0|12345",
            "aud": "test-client-id",
            "exp": now + timedelta(hours=1),
            "iat": now,
            "nonce": "test-nonce-123",
        }

        # Create token with HS256 header (even though using RSA key)
        headers = {"kid": "test-kid", "alg": "HS256"}
        id_token = jwt.encode(claims, "secret", algorithm="HS256", headers=headers)

        mock_jwks = {"keys": []}

        with patch.object(validator, "get_jwks", return_value=mock_jwks):
            with pytest.raises(jwt.InvalidAlgorithmError, match="HS256 not allowed"):
                await validator.validate_id_token(
                    id_token=id_token,
                    expected_nonce="test-nonce-123",
                    expected_audience="test-client-id",
                    expected_issuer="https://test.auth0.com/",
                )


class TestClaimValidation:
    """Test claim validation (nonce, audience, issuer)."""

    @pytest.mark.asyncio()
    async def test_nonce_mismatch(self, rsa_keypair, mock_jwks_rs256):
        """Test nonce mismatch rejection."""
        private_key, _ = rsa_keypair
        validator = JWKSValidator(auth0_domain="test.auth0.com")

        now = datetime.now(UTC)
        claims = {
            "iss": "https://test.auth0.com/",
            "sub": "auth0|12345",
            "aud": "test-client-id",
            "exp": now + timedelta(hours=1),
            "iat": now,
            "nonce": "wrong-nonce",  # Mismatch
        }

        id_token = create_id_token(private_key, "RS256", "test-rs256-kid", claims)

        with patch.object(validator, "get_jwks", return_value=mock_jwks_rs256):
            with pytest.raises(jwt.InvalidTokenError, match="Nonce mismatch"):
                await validator.validate_id_token(
                    id_token=id_token,
                    expected_nonce="correct-nonce",
                    expected_audience="test-client-id",
                    expected_issuer="https://test.auth0.com/",
                )

    @pytest.mark.asyncio()
    async def test_audience_mismatch(self, rsa_keypair, mock_jwks_rs256):
        """Test audience mismatch rejection."""
        private_key, _ = rsa_keypair
        validator = JWKSValidator(auth0_domain="test.auth0.com")

        now = datetime.now(UTC)
        claims = {
            "iss": "https://test.auth0.com/",
            "sub": "auth0|12345",
            "aud": "wrong-audience",  # Mismatch
            "exp": now + timedelta(hours=1),
            "iat": now,
            "nonce": "test-nonce-123",
        }

        id_token = create_id_token(private_key, "RS256", "test-rs256-kid", claims)

        with patch.object(validator, "get_jwks", return_value=mock_jwks_rs256):
            with pytest.raises(jwt.InvalidAudienceError):
                await validator.validate_id_token(
                    id_token=id_token,
                    expected_nonce="test-nonce-123",
                    expected_audience="correct-audience",
                    expected_issuer="https://test.auth0.com/",
                )


class TestKeyRotation:
    """Test key rotation and kid not found scenarios."""

    @pytest.mark.asyncio()
    async def test_kid_not_found_refreshes_jwks(self, rsa_keypair):
        """Test that missing kid triggers JWKS refresh."""
        private_key, _ = rsa_keypair
        validator = JWKSValidator(auth0_domain="test.auth0.com")

        now = datetime.now(UTC)
        claims = {
            "iss": "https://test.auth0.com/",
            "sub": "auth0|12345",
            "aud": "test-client-id",
            "exp": now + timedelta(hours=1),
            "iat": now,
            "nonce": "test-nonce-123",
        }

        id_token = create_id_token(private_key, "RS256", "missing-kid", claims)

        # Mock JWKS with no matching kid
        empty_jwks = {"keys": []}

        with patch.object(validator, "get_jwks", return_value=empty_jwks) as mock_get_jwks:
            with pytest.raises(jwt.InvalidKeyError, match="kid=missing-kid"):
                await validator.validate_id_token(
                    id_token=id_token,
                    expected_nonce="test-nonce-123",
                    expected_audience="test-client-id",
                    expected_issuer="https://test.auth0.com/",
                )

            # Verify it tried force refresh
            assert mock_get_jwks.call_count == 2
            # Second call should have force_refresh=True
            assert mock_get_jwks.call_args_list[1][1] == {"force_refresh": True}
