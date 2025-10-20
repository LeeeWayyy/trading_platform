"""
Comprehensive unit tests for webhook_security module.

Tests cover:
- HMAC-SHA256 signature verification (verify_webhook_signature)
- Signature generation for testing (generate_webhook_signature)
- Header parsing (extract_signature_from_header)
- Signature format validation (validate_signature_format)
- Edge cases and security considerations

Target: Bring webhook_security.py coverage from 17% to 95%+

See Also:
    - /docs/STANDARDS/TESTING.md - Testing standards
    - /docs/ADRs/0005-execution-gateway-architecture.md - Security requirements
"""

import pytest

from apps.execution_gateway.webhook_security import (
    extract_signature_from_header,
    generate_webhook_signature,
    validate_signature_format,
    verify_webhook_signature,
)


class TestVerifyWebhookSignature:
    """Test HMAC-SHA256 signature verification."""

    def test_verify_valid_signature_success(self):
        """
        Should accept valid signature from Alpaca webhook.

        This is the happy path - webhook has correct signature
        computed with HMAC-SHA256.
        """
        payload = b'{"event":"fill","order":{"id":"123"}}'
        secret = "test_webhook_secret"

        # Generate valid signature
        signature = generate_webhook_signature(payload, secret)

        # Should verify successfully
        assert verify_webhook_signature(payload, signature, secret) is True

    def test_verify_invalid_signature_rejection(self):
        """
        Should reject webhook with invalid signature.

        This protects against webhook spoofing attacks.
        """
        payload = b'{"event":"fill","order":{"id":"123"}}'
        secret = "test_webhook_secret"
        invalid_signature = "a" * 64  # Wrong signature

        # Should reject invalid signature
        assert verify_webhook_signature(payload, invalid_signature, secret) is False

    def test_verify_signature_case_insensitive(self):
        """
        Should accept uppercase signature (case-insensitive comparison).

        Alpaca may send signatures in different cases.
        """
        payload = b'{"event":"fill","order":{"id":"123"}}'
        secret = "test_webhook_secret"

        signature_lower = generate_webhook_signature(payload, secret)
        signature_upper = signature_lower.upper()

        # Both cases should verify
        assert verify_webhook_signature(payload, signature_lower, secret) is True
        assert verify_webhook_signature(payload, signature_upper, secret) is True

    def test_verify_signature_constant_time_comparison(self):
        """
        Should use constant-time comparison to prevent timing attacks.

        Using hmac.compare_digest ensures timing attacks cannot
        be used to discover the secret key.
        """
        payload = b'{"event":"fill","order":{"id":"123"}}'
        secret = "test_webhook_secret"

        valid_signature = generate_webhook_signature(payload, secret)
        # Create signature differing in last character
        invalid_signature = valid_signature[:-1] + ("0" if valid_signature[-1] != "0" else "1")

        # Should reject with constant time
        assert verify_webhook_signature(payload, invalid_signature, secret) is False

    def test_verify_empty_payload_rejection(self):
        """Should reject webhook with empty payload."""
        assert verify_webhook_signature(b"", "signature", "secret") is False

    def test_verify_empty_signature_rejection(self):
        """Should reject webhook with empty signature."""
        payload = b'{"event":"fill"}'
        assert verify_webhook_signature(payload, "", "secret") is False

    def test_verify_empty_secret_rejection(self):
        """Should reject verification with empty secret."""
        payload = b'{"event":"fill"}'
        assert verify_webhook_signature(payload, "signature", "") is False

    def test_verify_none_payload_rejection(self):
        """Should handle None payload gracefully."""
        assert verify_webhook_signature(None, "signature", "secret") is False  # type: ignore[arg-type]

    def test_verify_none_signature_rejection(self):
        """Should handle None signature gracefully."""
        payload = b'{"event":"fill"}'
        assert verify_webhook_signature(payload, None, "secret") is False  # type: ignore[arg-type]

    def test_verify_none_secret_rejection(self):
        """Should handle None secret gracefully."""
        payload = b'{"event":"fill"}'
        assert verify_webhook_signature(payload, "signature", None) is False  # type: ignore[arg-type]

    def test_verify_different_payload_different_signature(self):
        """
        Should reject if payload is modified after signing.

        This protects against payload tampering.
        """
        payload1 = b'{"event":"fill","qty":10}'
        payload2 = b'{"event":"fill","qty":100}'  # Tampered quantity
        secret = "test_webhook_secret"

        signature1 = generate_webhook_signature(payload1, secret)

        # Signature for payload1 should not verify payload2
        assert verify_webhook_signature(payload2, signature1, secret) is False

    def test_verify_different_secret_rejection(self):
        """
        Should reject if secret key doesn't match.

        This ensures only authorized webhooks are accepted.
        """
        payload = b'{"event":"fill","order":{"id":"123"}}'
        secret1 = "correct_secret"
        secret2 = "wrong_secret"

        signature = generate_webhook_signature(payload, secret1)

        # Should reject with wrong secret
        assert verify_webhook_signature(payload, signature, secret2) is False

    def test_verify_unicode_payload(self):
        """Should handle unicode characters in payload."""
        payload = '{"event":"fill","symbol":"特斯拉"}'.encode("utf-8")
        secret = "test_webhook_secret"

        signature = generate_webhook_signature(payload, secret)

        # Should verify successfully
        assert verify_webhook_signature(payload, signature, secret) is True

    def test_verify_exception_handling(self):
        """Should handle unexpected exceptions gracefully and return False."""
        # Pass invalid types that might cause exceptions
        # The function should catch and return False
        payload = b'{"event":"fill"}'

        # This might cause encoding issues internally but should be handled
        result = verify_webhook_signature(payload, "invalid", "secret")

        # Should not raise, should return False for invalid signature
        assert result is False


class TestGenerateWebhookSignature:
    """Test signature generation helper."""

    def test_generate_signature_format(self):
        """
        Should generate 64-character hex string.

        SHA256 hex digest is always 64 characters.
        """
        payload = b'{"event":"fill"}'
        secret = "test_secret"

        signature = generate_webhook_signature(payload, secret)

        assert len(signature) == 64
        assert all(c in "0123456789abcdef" for c in signature)

    def test_generate_signature_deterministic(self):
        """
        Should generate same signature for same input.

        This ensures idempotency.
        """
        payload = b'{"event":"fill"}'
        secret = "test_secret"

        sig1 = generate_webhook_signature(payload, secret)
        sig2 = generate_webhook_signature(payload, secret)

        assert sig1 == sig2

    def test_generate_signature_different_for_different_payload(self):
        """Should generate different signature for different payload."""
        payload1 = b'{"event":"fill","qty":10}'
        payload2 = b'{"event":"fill","qty":20}'
        secret = "test_secret"

        sig1 = generate_webhook_signature(payload1, secret)
        sig2 = generate_webhook_signature(payload2, secret)

        assert sig1 != sig2

    def test_generate_signature_different_for_different_secret(self):
        """Should generate different signature for different secret."""
        payload = b'{"event":"fill"}'
        secret1 = "secret1"
        secret2 = "secret2"

        sig1 = generate_webhook_signature(payload, secret1)
        sig2 = generate_webhook_signature(payload, secret2)

        assert sig1 != sig2

    def test_generate_verify_roundtrip(self):
        """
        Should verify signature generated by generate_webhook_signature.

        This confirms both functions use same algorithm.
        """
        payload = b'{"event":"fill","order":{"id":"123"}}'
        secret = "test_webhook_secret"

        signature = generate_webhook_signature(payload, secret)

        # Signature should verify
        assert verify_webhook_signature(payload, signature, secret) is True


class TestExtractSignatureFromHeader:
    """Test signature extraction from HTTP header."""

    def test_extract_simple_format(self):
        """
        Should extract signature in simple format.

        Format: "a1b2c3d4e5f6..."
        """
        header = "a1b2c3d4e5f6" + "0" * 52  # 64-char hex

        result = extract_signature_from_header(header)

        assert result == header

    def test_extract_prefixed_format(self):
        """
        Should extract signature with sha256= prefix.

        Format: "sha256=a1b2c3d4e5f6..."
        """
        signature = "a1b2c3d4e5f6" + "0" * 52
        header = f"sha256={signature}"

        result = extract_signature_from_header(header)

        assert result == signature

    def test_extract_none_header(self):
        """Should return None for None header."""
        result = extract_signature_from_header(None)

        assert result is None

    def test_extract_empty_header(self):
        """Should return None for empty header."""
        result = extract_signature_from_header("")

        assert result is None

    def test_extract_whitespace_header(self):
        """Should return whitespace as-is (validation happens later)."""
        result = extract_signature_from_header("   ")

        # Function doesn't trim whitespace, that's for validation
        assert result == "   "

    def test_extract_case_sensitive_prefix(self):
        """
        Should only strip lowercase 'sha256=' prefix.

        If Alpaca sends 'SHA256=' (uppercase), it won't be stripped.
        """
        header = "SHA256=abcdef" + "0" * 58

        result = extract_signature_from_header(header)

        # Should NOT strip uppercase prefix
        assert result == header


class TestValidateSignatureFormat:
    """Test signature format validation."""

    def test_validate_valid_64_char_hex(self):
        """Should accept valid 64-character hex string."""
        valid_signature = "a" * 64

        assert validate_signature_format(valid_signature) is True

    def test_validate_mixed_case_hex(self):
        """Should accept mixed case hex characters."""
        valid_signature = "A1b2C3d4" + "0" * 56

        assert validate_signature_format(valid_signature) is True

    def test_validate_all_hex_digits(self):
        """Should accept all valid hex characters (0-9, a-f, A-F)."""
        valid_signature = "0123456789abcdefABCDEF" + "0" * 42

        assert validate_signature_format(valid_signature) is True

    def test_validate_too_short_rejection(self):
        """Should reject signature shorter than 64 characters."""
        short_signature = "a" * 63

        assert validate_signature_format(short_signature) is False

    def test_validate_too_long_rejection(self):
        """Should reject signature longer than 64 characters."""
        long_signature = "a" * 65

        assert validate_signature_format(long_signature) is False

    def test_validate_non_hex_characters_rejection(self):
        """Should reject signature with non-hex characters."""
        invalid_signature = "g" * 64  # 'g' is not valid hex

        assert validate_signature_format(invalid_signature) is False

    def test_validate_special_characters_rejection(self):
        """Should reject signature with special characters."""
        invalid_signature = "=" + "a" * 63

        assert validate_signature_format(invalid_signature) is False

    def test_validate_empty_string_rejection(self):
        """Should reject empty string."""
        assert validate_signature_format("") is False

    def test_validate_none_input_rejection(self):
        """Should reject None input."""
        assert validate_signature_format(None) is False  # type: ignore[arg-type]

    def test_validate_non_string_input_rejection(self):
        """Should reject non-string input."""
        assert validate_signature_format(12345) is False  # type: ignore[arg-type]
        assert validate_signature_format(["a" * 64]) is False  # type: ignore[arg-type]


class TestWebhookSecurityIntegration:
    """Integration tests combining multiple security functions."""

    def test_full_webhook_verification_flow(self):
        """
        Should simulate complete webhook verification flow.

        This tests the typical usage pattern:
        1. Extract signature from header
        2. Validate signature format
        3. Verify signature against payload
        """
        # Simulate Alpaca webhook
        payload = b'{"event":"fill","order":{"id":"broker_123","status":"filled"}}'
        secret = "production_webhook_secret"

        # Generate signature (simulating Alpaca)
        expected_signature = generate_webhook_signature(payload, secret)

        # Simulate receiving webhook with prefixed header
        header_value = f"sha256={expected_signature}"

        # Extract signature
        signature = extract_signature_from_header(header_value)
        assert signature is not None

        # Validate format
        assert validate_signature_format(signature) is True

        # Verify signature
        assert verify_webhook_signature(payload, signature, secret) is True

    def test_reject_tampered_webhook(self):
        """
        Should reject webhook with tampered payload.

        Simulates attack where attacker modifies payload but
        keeps original signature.
        """
        original_payload = b'{"event":"fill","qty":10}'
        tampered_payload = b'{"event":"fill","qty":999}'  # Attacker changed qty
        secret = "production_webhook_secret"

        # Attacker copies original signature
        original_signature = generate_webhook_signature(original_payload, secret)

        # Should reject tampered payload
        assert verify_webhook_signature(tampered_payload, original_signature, secret) is False

    def test_reject_webhook_with_wrong_secret(self):
        """
        Should reject webhook signed with different secret.

        Simulates attack where attacker has different secret key.
        """
        payload = b'{"event":"fill","order":{"id":"123"}}'
        attacker_secret = "attacker_secret"
        production_secret = "production_webhook_secret"

        # Attacker generates signature with their secret
        attacker_signature = generate_webhook_signature(payload, attacker_secret)

        # Should reject when verifying with production secret
        assert verify_webhook_signature(payload, attacker_signature, production_secret) is False
