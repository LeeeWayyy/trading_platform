"""Tests for PII log sanitizer utilities."""

from __future__ import annotations

import io
import json
import logging

import pytest

from libs.common.log_sanitizer import (
    API_KEY_PREFIX_PATTERN,
    PHONE_PATTERN,
    RAW_API_KEY_PATTERN,
    SanitizingFormatter,
    SanitizingJSONFormatter,
    mask_api_key,
    mask_email,
    mask_ip,
    mask_phone,
    sanitize_dict,
    sanitize_log_record,
)


class TestMaskingFunctions:
    @pytest.mark.parametrize(
        ("email", "expected"),
        [
            ("user@example.com", "***@example.com"),
            ("first.last@sub.domain.co", "***@sub.domain.co"),
            ("name+alias@service.io", "***@service.io"),
        ],
    )
    def test_mask_email(self, email: str, expected: str) -> None:
        assert mask_email(email) == expected

    @pytest.mark.parametrize(
        ("phone", "expected_suffix"),
        [
            ("+1 (415) 555-1234", "1234"),
            ("415-555-9876", "9876"),
            ("+44 7700 900123", "0123"),
        ],
    )
    def test_mask_phone(self, phone: str, expected_suffix: str) -> None:
        masked = mask_phone(phone)
        assert masked.startswith("***")
        assert masked.endswith(expected_suffix)
        assert PHONE_PATTERN.search(masked) is None

    def test_mask_api_key_prefixed(self) -> None:
        """Test masking of prefixed API keys (tp_live_...)."""
        full_key = "tp_live_" + "a" * 31 + "wxyz"
        masked = mask_api_key(full_key)
        assert masked.startswith("tp_live_xxx...")
        assert masked.endswith("wxyz")
        assert API_KEY_PREFIX_PATTERN.search(masked) is None

    def test_mask_api_key_raw(self) -> None:
        """Test masking of raw base64url API keys (43 chars)."""
        # 43-char base64url key (no padding)
        raw_key = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnXYZ"
        masked = mask_api_key(raw_key)
        assert masked == "[key]...nXYZ"
        assert RAW_API_KEY_PATTERN.search(masked) is None

    def test_mask_ip(self) -> None:
        assert mask_ip("192.168.1.42") == "192.168.***"
        assert mask_ip("10.0.0.5") == "10.0.***"


class TestDictSanitization:
    def test_nested_dict_sanitization(self) -> None:
        data = {
            "user": {
                "email": "user@example.com",
                "contact": {"phone": "+1 (650) 123-4567"},
            }
        }
        sanitized = sanitize_dict(data)
        assert sanitized["user"]["email"] == "***@example.com"
        assert sanitized["user"]["contact"]["phone"].startswith("***")
        assert "123-4567"[-4:] in sanitized["user"]["contact"]["phone"]

    def test_list_inside_dict_sanitization(self) -> None:
        contacts = {
            "contacts": [
                {"email": "one@test.com"},
                {"phone": "555 111 2222"},
                {"notes": "call at +1 555 111 2222"},
            ]
        }
        sanitized = sanitize_dict(contacts)
        assert sanitized["contacts"][0]["email"] == "***@test.com"
        assert sanitized["contacts"][1]["phone"].endswith("2222")
        assert "***" in sanitized["contacts"][2]["notes"]

    def test_sensitive_key_detection(self) -> None:
        data = {
            "password": "supersecret",
            "token": "abcd1234",
            "secret_key": "hidden",
        }
        sanitized = sanitize_dict(data)
        assert sanitized["password"] == "***"
        assert sanitized["token"] == "***"
        assert sanitized["secret_key"] == "***"


class TestLogSanitization:
    def test_sanitize_log_record(self) -> None:
        record = {
            "message": "Email user@example.com",
            "extra": {"api_key": "tp_live_" + "b" * 35, "ip": "10.0.0.5"},
        }
        sanitized = sanitize_log_record(record)
        assert "user@example.com" not in json.dumps(sanitized)
        assert sanitized["message"].endswith("***@example.com")
        assert sanitized["extra"]["api_key"].startswith("tp_live_xxx...")
        assert sanitized["extra"]["ip"] == "10.0.***"

    def test_negative_no_raw_pii_present(self) -> None:
        data = {
            "message": "Contact user@example.com or call 415-555-1234",
            "details": {"phone": "+1 415 555 1234"},
        }
        sanitized = sanitize_log_record(data)
        output = json.dumps(sanitized)
        assert "user@example.com" not in output
        assert "415-555-1234" not in output
        assert "4155551234" not in output
        assert "***" in output

    def test_sanitizing_formatter_integration(self) -> None:
        logger = logging.getLogger("sanitizer-test")
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(SanitizingFormatter("%(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False

        try:
            logger.info("Contact %s at %s", "user@example.com", "+1 415 555 1234")
            output = stream.getvalue().strip()
        finally:
            logger.removeHandler(handler)

        assert "***@example.com" in output
        assert "user@example.com" not in output
        assert "***1234" in output

    def test_sanitizing_json_formatter(self) -> None:
        formatter = SanitizingJSONFormatter(service_name="test")
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="/tmp/file.py",
            lineno=10,
            msg="User %s logged in from %s",
            args=("user@example.com", "192.168.1.42"),
            exc_info=None,
        )
        record.trace_id = "trace-1"
        record.context = {"ip": "192.168.1.42", "api_key": "tp_live_" + "c" * 35}

        formatted = formatter.format(record)
        payload = json.loads(formatted)

        assert payload["service"] == "test"
        assert payload["message"] == "User ***@example.com logged in from 192.168.***"
        assert payload["context"]["ip"] == "192.168.***"
        assert payload["context"]["api_key"].startswith("tp_live_xxx...")
        assert "user@example.com" not in formatted
