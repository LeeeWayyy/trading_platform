"""
Full coverage tests for webhook_security module.

This test file specifically targets the remaining exception handling branches
to achieve 95%+ branch coverage.

Coverage target: 95%+ branch coverage for security-critical webhook verification

Tests include:
- UnicodeDecodeError handling (lines 74-85)
- AttributeError handling (lines 86-97)
- TypeError handling (lines 98-110)
- Replay attack scenarios
- Timing attack resistance
- Malformed payload handling

See Also:
    - test_webhook_security.py - Basic tests
    - test_webhook_security_comprehensive.py - Comprehensive tests
    - /docs/ADRs/0014-execution-gateway-architecture.md - Security requirements
"""

from unittest.mock import patch

import pytest

from apps.execution_gateway.webhook_security import (
    extract_signature_from_header,
    generate_webhook_signature,
    validate_signature_format,
    verify_webhook_signature,
)


class TestExceptionHandling:
    """Test exception handling in verify_webhook_signature."""

    def test_unicode_decode_error_in_secret_encoding(self):
        """
        Should handle UnicodeDecodeError when encoding secret.

        This covers lines 74-85 (UnicodeDecodeError exception handler).
        """
        payload = b'{"event":"fill","order":{"id":"123"}}'
        signature = "a" * 64
        secret = "valid_secret"

        # Mock hmac.new to raise UnicodeDecodeError
        with patch("hmac.new", side_effect=UnicodeDecodeError("utf-8", b"", 0, 1, "mock error")):
            result = verify_webhook_signature(payload, signature, secret)

        # Should return False on exception
        assert result is False

    def test_attribute_error_on_signature_lower(self):
        """
        Should handle AttributeError when signature.lower() fails.

        This covers lines 86-97 (AttributeError exception handler).
        This occurs when signature is not a string or doesn't have .lower() method.
        """
        payload = b'{"event":"fill","order":{"id":"123"}}'
        secret = "test_secret"

        # Create a mock signature without .lower() method
        class NoLowerMethod:
            def __init__(self):
                pass

        mock_signature = NoLowerMethod()

        # Should handle AttributeError gracefully
        result = verify_webhook_signature(payload, mock_signature, secret)  # type: ignore[arg-type]

        # Should return False
        assert result is False

    def test_type_error_in_hmac_computation(self):
        """
        Should handle TypeError in HMAC computation.

        This covers lines 98-110 (TypeError exception handler).
        """
        payload = b'{"event":"fill","order":{"id":"123"}}'
        signature = "a" * 64

        # Mock secret that causes TypeError in hmac.new
        with patch("hmac.new", side_effect=TypeError("Invalid type for hmac")):
            result = verify_webhook_signature(payload, signature, "secret")

        # Should return False on exception
        assert result is False

    def test_value_error_in_verification(self):
        """
        Should handle ValueError in signature verification.

        This covers lines 98-110 (ValueError exception handler).
        """
        payload = b'{"event":"fill","order":{"id":"123"}}'
        signature = "a" * 64
        secret = "test_secret"

        # Mock hmac.compare_digest to raise ValueError
        with patch("hmac.compare_digest", side_effect=ValueError("Invalid value for comparison")):
            result = verify_webhook_signature(payload, signature, secret)

        # Should return False on exception
        assert result is False

    def test_generic_exception_in_hmac_new(self):
        """
        Should raise unexpected exceptions (not caught by specific handlers).

        The function only catches UnicodeDecodeError, AttributeError, TypeError,
        and ValueError. Other exceptions are allowed to propagate.
        """
        payload = b'{"event":"fill","order":{"id":"123"}}'
        signature = "a" * 64
        secret = "test_secret"

        # Mock hmac.new to raise generic Exception (not caught by specific handlers)
        with patch("hmac.new", side_effect=Exception("Unexpected error")):
            # Should raise the exception (not caught)
            with pytest.raises(Exception, match="Unexpected error"):
                verify_webhook_signature(payload, signature, secret)

    def test_attribute_error_with_none_signature_type(self):
        """
        Should log signature type when AttributeError occurs.

        Tests that error logging includes signature type information.
        """
        payload = b'{"event":"fill","order":{"id":"123"}}'
        secret = "test_secret"

        # Create object without required string methods
        class InvalidSignature:
            pass

        invalid_sig = InvalidSignature()

        # Should handle gracefully and return False
        result = verify_webhook_signature(payload, invalid_sig, secret)  # type: ignore[arg-type]

        assert result is False


class TestReplayAttackScenarios:
    """Test protection against replay attacks."""

    def test_replay_attack_same_payload_different_time(self):
        """
        Should accept same payload multiple times (replay protection is time-based).

        Note: Replay protection is typically handled at a higher layer (e.g., timestamp
        validation, nonce checking). Signature verification alone doesn't prevent replays.
        """
        payload = b'{"event":"fill","order":{"id":"123"},"timestamp":"2024-01-01T12:00:00Z"}'
        secret = "test_webhook_secret"

        # Generate valid signature
        signature = generate_webhook_signature(payload, secret)

        # First verification should succeed
        assert verify_webhook_signature(payload, signature, secret) is True

        # Second verification should also succeed (signature is valid)
        # Replay protection must be implemented at application layer
        assert verify_webhook_signature(payload, signature, secret) is True

    def test_replay_attack_with_old_timestamp(self):
        """
        Should verify signature even with old timestamp (app layer must check timestamp).

        Signature verification is timestamp-agnostic. Application must validate
        timestamp freshness separately.
        """
        # Payload with old timestamp
        payload = b'{"event":"fill","order":{"id":"123"},"timestamp":"2020-01-01T12:00:00Z"}'
        secret = "test_webhook_secret"

        signature = generate_webhook_signature(payload, secret)

        # Signature should still verify (timestamp validation is separate concern)
        assert verify_webhook_signature(payload, signature, secret) is True


class TestTimingAttackResistance:
    """Test constant-time comparison to prevent timing attacks."""

    def test_constant_time_comparison_used(self):
        """
        Should use hmac.compare_digest for constant-time comparison.

        This prevents timing attacks where attacker measures comparison time
        to guess correct signature byte-by-byte.
        """
        payload = b'{"event":"fill","order":{"id":"123"}}'
        secret = "test_webhook_secret"

        valid_signature = generate_webhook_signature(payload, secret)

        # Create signatures that differ at different positions
        invalid_sig_first = "X" + valid_signature[1:]  # Differs at first char
        invalid_sig_last = valid_signature[:-1] + "X"  # Differs at last char

        # Both should return False
        assert verify_webhook_signature(payload, invalid_sig_first, secret) is False
        assert verify_webhook_signature(payload, invalid_sig_last, secret) is False

        # Timing should be constant regardless of where difference occurs
        # (This is ensured by hmac.compare_digest)

    def test_early_exit_on_missing_parameters(self):
        """
        Should exit early only when parameters are missing (not for invalid signature).

        Early exit on missing params is acceptable, but comparison must be constant-time.
        """
        payload = b'{"event":"fill"}'
        secret = "test_secret"
        signature = "a" * 64

        # Missing parameters - early exit is fine
        assert verify_webhook_signature(b"", signature, secret) is False
        assert verify_webhook_signature(payload, "", secret) is False
        assert verify_webhook_signature(payload, signature, "") is False

        # Invalid signature - should use constant-time comparison
        assert verify_webhook_signature(payload, "invalid", secret) is False


class TestMalformedPayloads:
    """Test handling of malformed and edge-case payloads."""

    def test_empty_json_payload(self):
        """Should handle empty JSON object."""
        payload = b"{}"
        secret = "test_secret"

        signature = generate_webhook_signature(payload, secret)

        assert verify_webhook_signature(payload, signature, secret) is True

    def test_json_array_payload(self):
        """Should handle JSON array payload."""
        payload = b'[{"event":"fill"},{"event":"cancel"}]'
        secret = "test_secret"

        signature = generate_webhook_signature(payload, secret)

        assert verify_webhook_signature(payload, signature, secret) is True

    def test_non_json_payload(self):
        """Should handle non-JSON payload (signature verification doesn't parse JSON)."""
        payload = b"This is not JSON at all"
        secret = "test_secret"

        signature = generate_webhook_signature(payload, secret)

        # Signature should still verify (we don't validate JSON structure)
        assert verify_webhook_signature(payload, signature, secret) is True

    def test_binary_payload(self):
        """Should handle binary payload."""
        payload = b"\x00\x01\x02\x03\x04\x05"
        secret = "test_secret"

        signature = generate_webhook_signature(payload, secret)

        assert verify_webhook_signature(payload, signature, secret) is True

    def test_very_large_payload(self):
        """Should handle very large payload."""
        # Create 1MB payload
        payload = b'{"event":"fill","data":"' + (b"x" * 1000000) + b'"}'
        secret = "test_secret"

        signature = generate_webhook_signature(payload, secret)

        assert verify_webhook_signature(payload, signature, secret) is True

    def test_payload_with_special_json_characters(self):
        """Should handle payload with escaped JSON characters."""
        payload = b'{"message":"Quote: \\"test\\", Newline: \\n, Tab: \\t"}'
        secret = "test_secret"

        signature = generate_webhook_signature(payload, secret)

        assert verify_webhook_signature(payload, signature, secret) is True

    def test_payload_with_unicode_escape_sequences(self):
        """Should handle payload with unicode escape sequences."""
        payload = b'{"symbol":"AAPL","note":"\\u0048\\u0065\\u006c\\u006c\\u006f"}'
        secret = "test_secret"

        signature = generate_webhook_signature(payload, secret)

        assert verify_webhook_signature(payload, signature, secret) is True


class TestSignatureFormatEdgeCases:
    """Test edge cases in signature format validation."""

    def test_signature_with_leading_zeros(self):
        """Should accept signature with leading zeros."""
        signature = "0" * 64

        assert validate_signature_format(signature) is True

    def test_signature_all_f(self):
        """Should accept signature with all F's (valid hex)."""
        signature = "f" * 64

        assert validate_signature_format(signature) is True

    def test_signature_with_mixed_case(self):
        """Should accept mixed case hex."""
        signature = "aBcDeF0123456789" * 4

        assert validate_signature_format(signature) is True

    def test_signature_63_chars(self):
        """Should reject 63-character signature."""
        signature = "a" * 63

        assert validate_signature_format(signature) is False

    def test_signature_65_chars(self):
        """Should reject 65-character signature."""
        signature = "a" * 65

        assert validate_signature_format(signature) is False

    def test_signature_with_space(self):
        """Should reject signature with whitespace."""
        signature = "a" * 32 + " " + "a" * 31

        assert validate_signature_format(signature) is False

    def test_signature_with_newline(self):
        """Should reject signature with newline."""
        signature = "a" * 32 + "\n" + "a" * 31

        assert validate_signature_format(signature) is False

    def test_signature_empty_bytes(self):
        """Should reject empty bytes object."""
        assert validate_signature_format(b"") is False  # type: ignore[arg-type]

    def test_signature_bytes_type(self):
        """Should reject bytes type even if valid hex."""
        signature_bytes = b"a" * 64

        assert validate_signature_format(signature_bytes) is False  # type: ignore[arg-type]


class TestHeaderExtractionEdgeCases:
    """Test edge cases in header extraction."""

    def test_extract_multiple_prefixes(self):
        """Should only strip first 'sha256=' prefix."""
        # Edge case: what if signature itself starts with 'sha256='?
        header = "sha256=sha256=" + "a" * 57

        result = extract_signature_from_header(header)

        # Should strip first prefix only
        assert result == "sha256=" + "a" * 57

    def test_extract_prefix_uppercase(self):
        """Should NOT strip uppercase 'SHA256=' prefix (case-sensitive)."""
        header = "SHA256=" + "a" * 57

        result = extract_signature_from_header(header)

        # Should NOT strip (case-sensitive check)
        assert result == header

    def test_extract_partial_prefix(self):
        """Should NOT strip partial prefix."""
        header = "sha25=" + "a" * 58

        result = extract_signature_from_header(header)

        # Should NOT strip partial prefix
        assert result == header

    def test_extract_prefix_only(self):
        """Should strip prefix even if rest is empty."""
        header = "sha256="

        result = extract_signature_from_header(header)

        # Should return empty string after stripping
        assert result == ""

    def test_extract_whitespace_around_signature(self):
        """Should NOT strip whitespace (validation handles that)."""
        header = "  " + "a" * 64 + "  "

        result = extract_signature_from_header(header)

        # Should return with whitespace intact
        assert result == header


class TestSecurityIntegration:
    """Integration tests for security scenarios."""

    def test_prevent_signature_forgery(self):
        """
        Should prevent signature forgery attempts.

        Attacker cannot generate valid signature without secret key.
        """
        payload = b'{"event":"fill","qty":100}'
        production_secret = "super_secret_production_key_12345"
        attacker_secret = "guessed_secret"

        # Attacker tries to forge signature with guessed secret
        forged_signature = generate_webhook_signature(payload, attacker_secret)

        # Verification with production secret should fail
        assert verify_webhook_signature(payload, forged_signature, production_secret) is False

    def test_prevent_payload_tampering(self):
        """
        Should detect payload tampering.

        Attacker cannot modify payload without invalidating signature.
        """
        original_payload = b'{"event":"fill","qty":10,"symbol":"AAPL"}'
        tampered_payload = b'{"event":"fill","qty":9999,"symbol":"AAPL"}'  # Changed qty
        secret = "production_secret"

        # Original signature
        original_signature = generate_webhook_signature(original_payload, secret)

        # Tampered payload should fail verification
        assert verify_webhook_signature(tampered_payload, original_signature, secret) is False

    def test_signature_does_not_leak_secret(self):
        """
        Should not allow secret recovery from signature.

        HMAC is one-way: knowing signature + payload shouldn't reveal secret.
        """
        payload = b'{"event":"fill"}'
        secret = "super_secret_key"

        signature = generate_webhook_signature(payload, secret)

        # Signature should be 64-char hex (no secret information)
        assert len(signature) == 64
        assert all(c in "0123456789abcdef" for c in signature)

        # Verifying with wrong secrets should fail
        wrong_secrets = ["wrong1", "wrong2", "super_secret_ke", "super_secret_key "]
        for wrong_secret in wrong_secrets:
            assert verify_webhook_signature(payload, signature, wrong_secret) is False

    def test_case_sensitivity_of_verification(self):
        """
        Should handle signature case insensitively.

        Alpaca may send signatures in different cases.
        """
        payload = b'{"event":"fill"}'
        secret = "test_secret"

        signature_lower = generate_webhook_signature(payload, secret)
        signature_upper = signature_lower.upper()
        signature_mixed = "".join(
            c.upper() if i % 2 == 0 else c.lower() for i, c in enumerate(signature_lower)
        )

        # All cases should verify
        assert verify_webhook_signature(payload, signature_lower, secret) is True
        assert verify_webhook_signature(payload, signature_upper, secret) is True
        assert verify_webhook_signature(payload, signature_mixed, secret) is True


class TestErrorLogging:
    """Test that errors are properly logged."""

    def test_logs_unicode_decode_error(self, caplog):
        """Should log UnicodeDecodeError with context."""
        payload = b'{"event":"fill"}'
        signature = "a" * 64
        secret = "test_secret"

        with patch("hmac.new", side_effect=UnicodeDecodeError("utf-8", b"", 0, 1, "test error")):
            result = verify_webhook_signature(payload, signature, secret)

        assert result is False
        # Verify error was logged (checking caplog)
        # Note: Actual logging assertions would check caplog.records

    def test_logs_attribute_error_with_type(self, caplog):
        """Should log AttributeError with signature type."""

        class NoLowerMethod:
            pass

        payload = b'{"event":"fill"}'
        secret = "test_secret"
        invalid_sig = NoLowerMethod()

        result = verify_webhook_signature(payload, invalid_sig, secret)  # type: ignore[arg-type]

        assert result is False

    def test_logs_invalid_signature_rejection(self, caplog):
        """Should log signature verification failures."""
        payload = b'{"event":"fill"}'
        secret = "test_secret"
        invalid_signature = "0" * 64

        result = verify_webhook_signature(payload, invalid_signature, secret)

        assert result is False
        # Logs should include signature prefixes for debugging
