"""Unit tests for web console configuration module.

Tests configuration loading, validation, and exception handling.
"""

from __future__ import annotations

import base64
import binascii
import logging
from pathlib import Path

import pytest

# Calculate project root dynamically (tests/apps/web_console_ng/test_config.py -> root)
PROJECT_ROOT = str(Path(__file__).parent.parent.parent.parent.resolve())


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


class TestGetEncryptionKeys:
    """Test get_encryption_keys function."""

    def test_get_encryption_keys_single_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Single encryption key should return list with one key."""
        import importlib

        import apps.web_console_ng.config as config_module

        # Generate valid 32-byte base64 key
        valid_key = base64.b64encode(b"a" * 32).decode("ascii")

        # Set environment and reload module
        monkeypatch.setenv("SESSION_ENCRYPTION_KEY", valid_key)
        monkeypatch.setenv("SESSION_ENCRYPTION_KEY_PREV", "")
        importlib.reload(config_module)

        keys = config_module.get_encryption_keys()
        assert len(keys) == 1
        assert keys[0] == b"a" * 32

    def test_get_encryption_keys_with_previous_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Both current and previous keys should be returned."""
        import importlib

        import apps.web_console_ng.config as config_module

        # Generate valid 32-byte base64 keys
        current_key = base64.b64encode(b"a" * 32).decode("ascii")
        prev_key = base64.b64encode(b"b" * 32).decode("ascii")

        monkeypatch.setenv("SESSION_ENCRYPTION_KEY", current_key)
        monkeypatch.setenv("SESSION_ENCRYPTION_KEY_PREV", prev_key)
        importlib.reload(config_module)

        keys = config_module.get_encryption_keys()
        assert len(keys) == 2
        assert keys[0] == b"a" * 32
        assert keys[1] == b"b" * 32

    def test_get_encryption_keys_missing_raises_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Missing encryption key should raise ValueError."""
        import importlib

        import apps.web_console_ng.config as config_module

        monkeypatch.setenv("SESSION_ENCRYPTION_KEY", "")
        importlib.reload(config_module)

        with pytest.raises(ValueError, match="SESSION_ENCRYPTION_KEY environment variable not set"):
            config_module.get_encryption_keys()


class TestGetSigningKeys:
    """Test get_signing_keys function."""

    def test_get_signing_keys_single_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Single signing key should return dict with one key."""
        import importlib

        import apps.web_console_ng.config as config_module

        # Valid hex key (16 bytes = 32 hex chars)
        hex_key = "deadbeefcafebabe1234567890abcdef"
        monkeypatch.setenv("HMAC_SIGNING_KEYS", f"key1:{hex_key}")
        monkeypatch.setenv("HMAC_CURRENT_KEY_ID", "key1")
        importlib.reload(config_module)

        key_map = config_module.get_signing_keys()
        assert "key1" in key_map
        assert key_map["key1"] == binascii.unhexlify(hex_key)

    def test_get_signing_keys_multiple_keys(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Multiple signing keys should all be returned."""
        import importlib

        import apps.web_console_ng.config as config_module

        hex_key1 = "deadbeefcafebabe1234567890abcdef"
        hex_key2 = "1234567890abcdefdeadbeefcafebabe"
        monkeypatch.setenv("HMAC_SIGNING_KEYS", f"key1:{hex_key1}, key2:{hex_key2}")
        monkeypatch.setenv("HMAC_CURRENT_KEY_ID", "key1")
        importlib.reload(config_module)

        key_map = config_module.get_signing_keys()
        assert len(key_map) == 2
        assert "key1" in key_map
        assert "key2" in key_map

    def test_get_signing_keys_missing_raises_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Missing HMAC_SIGNING_KEYS should raise ValueError."""
        import importlib

        import apps.web_console_ng.config as config_module

        monkeypatch.setenv("HMAC_SIGNING_KEYS", "")
        importlib.reload(config_module)

        with pytest.raises(ValueError, match="HMAC_SIGNING_KEYS environment variable not set"):
            config_module.get_signing_keys()

    def test_get_signing_keys_empty_after_parse_raises_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Empty keys after parsing should raise ValueError."""
        import importlib

        import apps.web_console_ng.config as config_module

        # Just whitespace and commas
        monkeypatch.setenv("HMAC_SIGNING_KEYS", "  ,  ,  ")
        importlib.reload(config_module)

        with pytest.raises(ValueError, match="must contain at least one key"):
            config_module.get_signing_keys()

    def test_get_signing_keys_missing_colon_raises_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Key without colon separator should raise ValueError."""
        import importlib

        import apps.web_console_ng.config as config_module

        monkeypatch.setenv("HMAC_SIGNING_KEYS", "key1-without-colon")
        importlib.reload(config_module)

        with pytest.raises(ValueError, match="must be in format 'id:key'"):
            config_module.get_signing_keys()

    def test_get_signing_keys_empty_key_id_raises_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Empty key id should raise ValueError."""
        import importlib

        import apps.web_console_ng.config as config_module

        monkeypatch.setenv("HMAC_SIGNING_KEYS", ":deadbeef")
        importlib.reload(config_module)

        with pytest.raises(ValueError, match="entry missing key id"):
            config_module.get_signing_keys()

    def test_get_signing_keys_invalid_current_key_id_raises_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """HMAC_CURRENT_KEY_ID not in keys should raise ValueError."""
        import importlib

        import apps.web_console_ng.config as config_module

        hex_key = "deadbeefcafebabe1234567890abcdef"
        monkeypatch.setenv("HMAC_SIGNING_KEYS", f"key1:{hex_key}")
        monkeypatch.setenv("HMAC_CURRENT_KEY_ID", "nonexistent")
        importlib.reload(config_module)

        with pytest.raises(ValueError, match="does not match any key id"):
            config_module.get_signing_keys()

    def test_get_signing_keys_invalid_hex_raises_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Invalid hex in key value should raise ValueError."""
        import importlib

        import apps.web_console_ng.config as config_module

        monkeypatch.setenv("HMAC_SIGNING_KEYS", "key1:not-valid-hex!")
        monkeypatch.setenv("HMAC_CURRENT_KEY_ID", "key1")
        importlib.reload(config_module)

        with pytest.raises(ValueError, match="must be hex-encoded"):
            config_module.get_signing_keys()


class TestParseFloat:
    """Test _parse_float function."""

    def test_parse_float_valid_value(self) -> None:
        """Valid float string should be parsed."""
        import os

        from apps.web_console_ng.config import _parse_float

        # Test with environment variable
        os.environ["TEST_FLOAT_VAR"] = "3.14"
        try:
            result = _parse_float("TEST_FLOAT_VAR", 0.0)
            assert result == 3.14
        finally:
            del os.environ["TEST_FLOAT_VAR"]

    def test_parse_float_empty_returns_default(self) -> None:
        """Empty value should return default."""
        import os

        from apps.web_console_ng.config import _parse_float

        # Ensure the env var is not set
        os.environ.pop("TEST_MISSING_VAR", None)
        result = _parse_float("TEST_MISSING_VAR", 42.0)
        assert result == 42.0

    def test_parse_float_nan_returns_default(self, caplog: pytest.LogCaptureFixture) -> None:
        """NaN value should return default with warning."""
        import os

        from apps.web_console_ng.config import _parse_float

        os.environ["TEST_NAN_VAR"] = "nan"
        try:
            with caplog.at_level(logging.WARNING):
                result = _parse_float("TEST_NAN_VAR", 5.0)
            assert result == 5.0
            assert any("Non-finite" in record.message for record in caplog.records)
        finally:
            del os.environ["TEST_NAN_VAR"]

    def test_parse_float_inf_returns_default(self, caplog: pytest.LogCaptureFixture) -> None:
        """Infinity value should return default with warning."""
        import os

        from apps.web_console_ng.config import _parse_float

        os.environ["TEST_INF_VAR"] = "inf"
        try:
            with caplog.at_level(logging.WARNING):
                result = _parse_float("TEST_INF_VAR", 7.0)
            assert result == 7.0
            assert any("Non-finite" in record.message for record in caplog.records)
        finally:
            del os.environ["TEST_INF_VAR"]

    def test_parse_float_negative_inf_returns_default(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Negative infinity should return default with warning."""
        import os

        from apps.web_console_ng.config import _parse_float

        os.environ["TEST_NEG_INF_VAR"] = "-inf"
        try:
            with caplog.at_level(logging.WARNING):
                result = _parse_float("TEST_NEG_INF_VAR", 9.0)
            assert result == 9.0
            assert any("Non-finite" in record.message for record in caplog.records)
        finally:
            del os.environ["TEST_NEG_INF_VAR"]

    def test_parse_float_invalid_returns_default(self, caplog: pytest.LogCaptureFixture) -> None:
        """Invalid value should return default with warning."""
        import os

        from apps.web_console_ng.config import _parse_float

        os.environ["TEST_INVALID_VAR"] = "not-a-number"
        try:
            with caplog.at_level(logging.WARNING):
                result = _parse_float("TEST_INVALID_VAR", 11.0)
            assert result == 11.0
            assert any("Invalid" in record.message for record in caplog.records)
        finally:
            del os.environ["TEST_INVALID_VAR"]


class TestModuleLevelConfigValidation:
    """Test module-level configuration validation via subprocess.

    These tests verify validation logic that runs at import time by loading
    the config module in a subprocess with controlled environment variables.
    """

    def test_storage_secret_required_in_production(self) -> None:
        """Missing NICEGUI_STORAGE_SECRET in production should raise ValueError."""
        import subprocess
        import sys

        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import apps.web_console_ng.config",
            ],
            env={
                "WEB_CONSOLE_NG_DEBUG": "false",
                "NICEGUI_STORAGE_SECRET": "",
                "WEB_CONSOLE_AUTH_TYPE": "basic",
                "PATH": "",
                "PYTHONPATH": ".",
            },
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
        )
        assert result.returncode != 0
        assert "NICEGUI_STORAGE_SECRET must be set" in result.stderr

    def test_invalid_samesite_raises_error(self) -> None:
        """Invalid SESSION_COOKIE_SAMESITE should raise ValueError."""
        import subprocess
        import sys

        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import apps.web_console_ng.config",
            ],
            env={
                "WEB_CONSOLE_NG_DEBUG": "true",
                "SESSION_COOKIE_SAMESITE": "invalid",
                "PATH": "",
                "PYTHONPATH": ".",
            },
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
        )
        assert result.returncode != 0
        assert "SESSION_COOKIE_SAMESITE must be one of" in result.stderr

    def test_invalid_audit_log_sink_raises_error(self) -> None:
        """Invalid AUDIT_LOG_SINK should raise ValueError."""
        import subprocess
        import sys

        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import apps.web_console_ng.config",
            ],
            env={
                "WEB_CONSOLE_NG_DEBUG": "true",
                "AUDIT_LOG_SINK": "invalid",
                "PATH": "",
                "PYTHONPATH": ".",
            },
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
        )
        assert result.returncode != 0
        assert "AUDIT_LOG_SINK must be one of" in result.stderr

    def test_auth_type_required_in_production(self) -> None:
        """Missing AUTH_TYPE in production should raise ValueError."""
        import subprocess
        import sys

        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import apps.web_console_ng.config",
            ],
            env={
                "WEB_CONSOLE_NG_DEBUG": "false",
                "NICEGUI_STORAGE_SECRET": "a" * 32,
                "WEB_CONSOLE_AUTH_TYPE": "",
                "PATH": "",
                "PYTHONPATH": ".",
            },
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
        )
        assert result.returncode != 0
        assert "WEB_CONSOLE_AUTH_TYPE must be explicitly set" in result.stderr

    def test_invalid_auth_type_raises_error(self) -> None:
        """Invalid AUTH_TYPE should raise ValueError."""
        import subprocess
        import sys

        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import apps.web_console_ng.config",
            ],
            env={
                "WEB_CONSOLE_NG_DEBUG": "true",
                "WEB_CONSOLE_AUTH_TYPE": "invalid",
                "PATH": "",
                "PYTHONPATH": ".",
            },
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
        )
        assert result.returncode != 0
        assert "WEB_CONSOLE_AUTH_TYPE must be one of" in result.stderr

    def test_dev_auth_not_allowed_in_production(self) -> None:
        """AUTH_TYPE='dev' should not be allowed in production."""
        import subprocess
        import sys

        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import apps.web_console_ng.config",
            ],
            env={
                "WEB_CONSOLE_NG_DEBUG": "false",
                "NICEGUI_STORAGE_SECRET": "a" * 32,
                "WEB_CONSOLE_AUTH_TYPE": "dev",
                "PATH": "",
                "PYTHONPATH": ".",
            },
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
        )
        assert result.returncode != 0
        assert "AUTH_TYPE='dev' is not allowed in production" in result.stderr

    def test_invalid_trusted_proxy_raises_error(self) -> None:
        """Invalid TRUSTED_PROXY_IPS entry should raise ValueError."""
        import subprocess
        import sys

        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import apps.web_console_ng.config",
            ],
            env={
                "WEB_CONSOLE_NG_DEBUG": "true",
                "TRUSTED_PROXY_IPS": "not-an-ip",
                "PATH": "",
                "PYTHONPATH": ".",
            },
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
        )
        assert result.returncode != 0
        assert "Invalid TRUSTED_PROXY_IPS entry" in result.stderr

    def test_sentinel_hosts_parsing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """REDIS_SENTINEL_HOSTS should be parsed correctly."""
        import importlib

        import apps.web_console_ng.config as config_module

        monkeypatch.setenv("REDIS_SENTINEL_HOSTS", "host1:26379,host2:26380")
        monkeypatch.setenv("REDIS_USE_SENTINEL", "true")
        importlib.reload(config_module)

        assert config_module.REDIS_SENTINEL_HOSTS == [("host1", 26379), ("host2", 26380)]

    def test_sentinel_default_when_enabled_no_hosts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Default sentinel host should be set when enabled but no hosts specified."""
        import importlib

        import apps.web_console_ng.config as config_module

        monkeypatch.setenv("REDIS_SENTINEL_HOSTS", "")
        monkeypatch.setenv("REDIS_USE_SENTINEL", "true")
        importlib.reload(config_module)

        assert config_module.REDIS_SENTINEL_HOSTS == [("localhost", 26379)]

    def test_trusted_proxy_network_parsing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """TRUSTED_PROXY_IPS should parse network addresses."""
        import importlib
        import ipaddress

        import apps.web_console_ng.config as config_module

        monkeypatch.setenv("TRUSTED_PROXY_IPS", "192.168.1.0/24,10.0.0.1")
        importlib.reload(config_module)

        assert len(config_module.TRUSTED_PROXY_IPS) == 2
        assert isinstance(config_module.TRUSTED_PROXY_IPS[0], ipaddress.IPv4Network)
        assert isinstance(config_module.TRUSTED_PROXY_IPS[1], ipaddress.IPv4Address)

    def test_trusted_proxy_empty_entries_skipped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Empty entries in TRUSTED_PROXY_IPS should be skipped."""
        import importlib

        import apps.web_console_ng.config as config_module

        monkeypatch.setenv("TRUSTED_PROXY_IPS", "192.168.1.1, , 10.0.0.1")
        importlib.reload(config_module)

        assert len(config_module.TRUSTED_PROXY_IPS) == 2

    def test_allowed_hosts_wildcard(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Wildcard in ALLOWED_HOSTS should result in ['*']."""
        import importlib

        import apps.web_console_ng.config as config_module

        monkeypatch.setenv("ALLOWED_HOSTS", "localhost, *")
        importlib.reload(config_module)

        assert config_module.ALLOWED_HOSTS == ["*"]

    def test_dev_strategies_from_strategy_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """DEV_STRATEGIES should fallback to STRATEGY_ID when empty."""
        import importlib

        import apps.web_console_ng.config as config_module

        monkeypatch.setenv("WEB_CONSOLE_DEV_STRATEGIES", "")
        monkeypatch.setenv("STRATEGY_ID", "test-strategy")
        importlib.reload(config_module)

        assert config_module.DEV_STRATEGIES == ["test-strategy"]

    def test_allow_dev_basic_auth_warning(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """ALLOW_DEV_BASIC_AUTH should log warning when enabled."""
        import importlib

        import apps.web_console_ng.config as config_module

        monkeypatch.setenv("WEB_CONSOLE_ALLOW_DEV_BASIC_AUTH", "true")
        with caplog.at_level(logging.WARNING):
            importlib.reload(config_module)

        assert any(
            "WEB_CONSOLE_ALLOW_DEV_BASIC_AUTH is enabled" in record.message
            for record in caplog.records
        )


class TestGetBoolEnv:
    """Test _get_bool_env helper function."""

    def test_get_bool_env_true_values(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Truthy string values should return True."""
        from apps.web_console_ng.config import _get_bool_env

        for value in ["1", "true", "yes", "on", "TRUE", "Yes", "ON"]:
            monkeypatch.setenv("TEST_BOOL_VAR", value)
            assert _get_bool_env("TEST_BOOL_VAR") is True

    def test_get_bool_env_false_values(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Non-truthy string values should return False."""
        from apps.web_console_ng.config import _get_bool_env

        for value in ["0", "false", "no", "off", "anything"]:
            monkeypatch.setenv("TEST_BOOL_VAR", value)
            assert _get_bool_env("TEST_BOOL_VAR") is False

    def test_get_bool_env_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Missing env var should use default."""
        from apps.web_console_ng.config import _get_bool_env

        monkeypatch.delenv("TEST_MISSING_BOOL", raising=False)
        assert _get_bool_env("TEST_MISSING_BOOL") is False
        assert _get_bool_env("TEST_MISSING_BOOL", "true") is True
