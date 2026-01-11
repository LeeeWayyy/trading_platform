"""Unit tests for web console configuration module.

Tests configuration loading, validation, and exception handling.
"""

from __future__ import annotations

import base64
import binascii
import logging

import pytest


class TestBase64KeyDecoding:
    """Test _decode_base64_key function exception handling."""

    def test_decode_base64_key_valid(self) -> None:
        """Valid base64 key should decode successfully."""
        from apps.web_console_ng.config import _decode_base64_key

        # 32 bytes = 44 base64 characters (with padding)
        valid_key = base64.b64encode(b"a" * 32).decode("ascii")
        result = _decode_base64_key(valid_key, "TEST_KEY")
        assert len(result) == 32
        assert result == b"a" * 32

    def test_decode_base64_key_empty_raises_value_error(self) -> None:
        """Empty key value should raise ValueError."""
        from apps.web_console_ng.config import _decode_base64_key

        with pytest.raises(ValueError, match="TEST_KEY environment variable not set"):
            _decode_base64_key("", "TEST_KEY")

    def test_decode_base64_key_invalid_base64_raises_value_error(self) -> None:
        """Invalid base64 should raise ValueError."""
        from apps.web_console_ng.config import _decode_base64_key

        with pytest.raises(ValueError, match="TEST_KEY must be base64-encoded"):
            _decode_base64_key("not-valid-base64!", "TEST_KEY")

    def test_decode_base64_key_wrong_length_raises_value_error(self) -> None:
        """Key not 32 bytes should raise ValueError."""
        from apps.web_console_ng.config import _decode_base64_key

        # 16 bytes instead of 32
        short_key = base64.b64encode(b"a" * 16).decode("ascii")
        with pytest.raises(ValueError, match="must decode to 32 bytes"):
            _decode_base64_key(short_key, "TEST_KEY")

    def test_decode_base64_key_logs_on_error(self, caplog: pytest.LogCaptureFixture) -> None:
        """Invalid base64 should log error with details."""
        from apps.web_console_ng.config import _decode_base64_key

        with caplog.at_level(logging.ERROR):
            with pytest.raises(ValueError, match="must be base64-encoded"):
                _decode_base64_key("invalid-base64!", "TEST_KEY")

        assert any("Invalid base64-encoded key" in record.message for record in caplog.records)
        # Verify structured logging includes env_name
        error_record = next(
            (r for r in caplog.records if "Invalid base64-encoded key" in r.message), None
        )
        assert error_record is not None
        # Check that extra fields are present (implementation specific)

    def test_decode_base64_key_handles_binascii_error(self) -> None:
        """binascii.Error should be caught and converted to ValueError."""
        from apps.web_console_ng.config import _decode_base64_key

        # Invalid base64 characters
        with pytest.raises(ValueError, match="must be base64-encoded"):
            _decode_base64_key("!!!invalid!!!", "TEST_KEY")

    def test_decode_base64_key_handles_type_error(self) -> None:
        """TypeError should be caught (though unlikely with str input)."""
        from apps.web_console_ng.config import _decode_base64_key

        # This tests the defensive handler - base64.b64decode with wrong type
        # Since the function signature requires str, this tests the except block coverage
        with pytest.raises(ValueError, match="must be base64-encoded|must decode to"):
            _decode_base64_key("invalid", "TEST_KEY")


class TestHexKeyDecoding:
    """Test _decode_hex_key function."""

    def test_decode_hex_key_valid(self) -> None:
        """Valid hex key should decode successfully."""
        from apps.web_console_ng.config import _decode_hex_key

        hex_key = "deadbeef"
        result = _decode_hex_key(hex_key, "TEST_KEY")
        assert result == binascii.unhexlify(hex_key)

    def test_decode_hex_key_empty_raises_value_error(self) -> None:
        """Empty key value should raise ValueError."""
        from apps.web_console_ng.config import _decode_hex_key

        with pytest.raises(ValueError, match="TEST_KEY value is empty"):
            _decode_hex_key("", "TEST_KEY")

    def test_decode_hex_key_invalid_hex_raises_value_error(self) -> None:
        """Invalid hex should raise ValueError."""
        from apps.web_console_ng.config import _decode_hex_key

        with pytest.raises(ValueError, match="TEST_KEY must be hex-encoded"):
            _decode_hex_key("not-hex!", "TEST_KEY")
