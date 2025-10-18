"""
Unit tests for webhook signature verification.

Tests verify:
- Signature generation and verification
- Constant-time comparison security
- Header parsing
- Format validation
"""

import pytest
from apps.execution_gateway.webhook_security import (
    verify_webhook_signature,
    generate_webhook_signature,
    extract_signature_from_header,
    validate_signature_format,
)


class TestWebhookSignatureVerification:
    """Test webhook signature verification."""

    def test_valid_signature(self):
        """Valid signature should verify successfully."""
        payload = b'{"event":"fill","order":{"id":"123"}}'
        secret = "my_webhook_secret"

        # Generate signature
        signature = generate_webhook_signature(payload, secret)

        # Verify signature
        assert verify_webhook_signature(payload, signature, secret) is True

    def test_invalid_signature(self):
        """Invalid signature should fail verification."""
        payload = b'{"event":"fill","order":{"id":"123"}}'
        secret = "my_webhook_secret"
        wrong_signature = "0" * 64  # Invalid signature

        assert verify_webhook_signature(payload, wrong_signature, secret) is False

    def test_different_payload_fails(self):
        """Same signature with different payload should fail."""
        payload1 = b'{"event":"fill","order":{"id":"123"}}'
        payload2 = b'{"event":"fill","order":{"id":"456"}}'  # Different
        secret = "my_webhook_secret"

        signature = generate_webhook_signature(payload1, secret)

        assert verify_webhook_signature(payload2, signature, secret) is False

    def test_different_secret_fails(self):
        """Same payload with different secret should fail."""
        payload = b'{"event":"fill","order":{"id":"123"}}'
        secret1 = "secret1"
        secret2 = "secret2"

        signature = generate_webhook_signature(payload, secret1)

        assert verify_webhook_signature(payload, signature, secret2) is False

    def test_case_insensitive_signature(self):
        """Signature verification should be case-insensitive."""
        payload = b'{"event":"fill","order":{"id":"123"}}'
        secret = "my_webhook_secret"

        signature = generate_webhook_signature(payload, secret)
        signature_upper = signature.upper()

        assert verify_webhook_signature(payload, signature_upper, secret) is True

    def test_empty_payload_fails(self):
        """Empty payload should fail verification."""
        payload = b''
        secret = "my_webhook_secret"
        signature = "a" * 64

        assert verify_webhook_signature(payload, signature, secret) is False

    def test_empty_signature_fails(self):
        """Empty signature should fail verification."""
        payload = b'{"event":"fill","order":{"id":"123"}}'
        secret = "my_webhook_secret"

        assert verify_webhook_signature(payload, "", secret) is False

    def test_empty_secret_fails(self):
        """Empty secret should fail verification."""
        payload = b'{"event":"fill","order":{"id":"123"}}'
        signature = "a" * 64

        assert verify_webhook_signature(payload, signature, "") is False


class TestGenerateWebhookSignature:
    """Test webhook signature generation."""

    def test_deterministic_generation(self):
        """Same payload and secret should generate same signature."""
        payload = b'{"event":"fill","order":{"id":"123"}}'
        secret = "my_webhook_secret"

        sig1 = generate_webhook_signature(payload, secret)
        sig2 = generate_webhook_signature(payload, secret)

        assert sig1 == sig2

    def test_signature_length(self):
        """Generated signature should be 64 characters (SHA256 hex)."""
        payload = b'{"event":"fill","order":{"id":"123"}}'
        secret = "my_webhook_secret"

        signature = generate_webhook_signature(payload, secret)

        assert len(signature) == 64

    def test_signature_is_hex(self):
        """Generated signature should be valid hexadecimal."""
        payload = b'{"event":"fill","order":{"id":"123"}}'
        secret = "my_webhook_secret"

        signature = generate_webhook_signature(payload, secret)

        # Should be able to convert to int from hex
        assert isinstance(int(signature, 16), int)

    def test_different_payloads_different_signatures(self):
        """Different payloads should generate different signatures."""
        payload1 = b'{"event":"fill","order":{"id":"123"}}'
        payload2 = b'{"event":"fill","order":{"id":"456"}}'
        secret = "my_webhook_secret"

        sig1 = generate_webhook_signature(payload1, secret)
        sig2 = generate_webhook_signature(payload2, secret)

        assert sig1 != sig2

    def test_different_secrets_different_signatures(self):
        """Different secrets should generate different signatures."""
        payload = b'{"event":"fill","order":{"id":"123"}}'
        secret1 = "secret1"
        secret2 = "secret2"

        sig1 = generate_webhook_signature(payload, secret1)
        sig2 = generate_webhook_signature(payload, secret2)

        assert sig1 != sig2


class TestExtractSignatureFromHeader:
    """Test signature extraction from header."""

    def test_simple_signature(self):
        """Simple signature without prefix should be returned as-is."""
        signature = "a1b2c3d4e5f6" * 6  # 64 chars
        result = extract_signature_from_header(signature)

        assert result == signature

    def test_prefixed_signature(self):
        """Signature with sha256= prefix should be extracted."""
        signature = "a1b2c3d4e5f6" * 6
        prefixed = f"sha256={signature}"

        result = extract_signature_from_header(prefixed)

        assert result == signature

    def test_none_header(self):
        """None header should return None."""
        result = extract_signature_from_header(None)

        assert result is None

    def test_empty_header(self):
        """Empty header should return None."""
        result = extract_signature_from_header("")

        assert result is None


class TestValidateSignatureFormat:
    """Test signature format validation."""

    def test_valid_64_char_hex(self):
        """Valid 64-character hex string should pass."""
        signature = "a" * 64

        assert validate_signature_format(signature) is True

    def test_mixed_hex_chars(self):
        """Mixed valid hex characters should pass."""
        signature = "0123456789abcdef" * 4  # 64 chars

        assert validate_signature_format(signature) is True

    def test_uppercase_hex(self):
        """Uppercase hex characters should pass."""
        signature = "A" * 64

        assert validate_signature_format(signature) is True

    def test_invalid_length_short(self):
        """Too short signature should fail."""
        signature = "a" * 32  # Only 32 chars

        assert validate_signature_format(signature) is False

    def test_invalid_length_long(self):
        """Too long signature should fail."""
        signature = "a" * 128  # 128 chars

        assert validate_signature_format(signature) is False

    def test_invalid_characters(self):
        """Non-hex characters should fail."""
        signature = "g" * 64  # 'g' is not valid hex

        assert validate_signature_format(signature) is False

    def test_special_characters(self):
        """Special characters should fail."""
        signature = "@" * 64

        assert validate_signature_format(signature) is False

    def test_non_string_type(self):
        """Non-string type should fail."""
        assert validate_signature_format(123) is False
        assert validate_signature_format(None) is False
        assert validate_signature_format([]) is False


class TestRoundTrip:
    """Test full round-trip: generate -> verify."""

    def test_round_trip_success(self):
        """Generate signature and verify it."""
        payload = b'{"event":"fill","order":{"id":"123","symbol":"AAPL"}}'
        secret = "production_webhook_secret_key_12345"

        # Generate
        signature = generate_webhook_signature(payload, secret)

        # Verify
        assert verify_webhook_signature(payload, signature, secret) is True

    def test_round_trip_with_complex_payload(self):
        """Test with complex JSON payload."""
        payload = b'''{
            "event": "fill",
            "order": {
                "id": "550e8400-e29b-41d4-a716-446655440000",
                "client_order_id": "my_order_123",
                "symbol": "AAPL",
                "side": "buy",
                "qty": "100",
                "filled_qty": "100",
                "filled_avg_price": "150.25",
                "status": "filled",
                "created_at": "2024-10-17T16:30:00Z"
            },
            "timestamp": "2024-10-17T16:30:05Z"
        }'''
        secret = "webhook_secret"

        signature = generate_webhook_signature(payload, secret)

        assert verify_webhook_signature(payload, signature, secret) is True

    def test_round_trip_with_unicode(self):
        """Test with unicode characters in payload."""
        payload = '{"symbol":"AAPL","note":"Test™"}'.encode('utf-8')
        secret = "webhook_secret"

        signature = generate_webhook_signature(payload, secret)

        assert verify_webhook_signature(payload, signature, secret) is True
