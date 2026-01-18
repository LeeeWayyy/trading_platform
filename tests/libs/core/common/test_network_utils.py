"""
Unit tests for libs.core.common.network_utils.

Covers:
- trusted proxy validation behavior
- client IP extraction logic
- user agent extraction
- environment-aware trusted proxy defaults
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import pytest
from fastapi import HTTPException

from libs.core.common.network_utils import (
    extract_client_ip_from_fastapi,
    extract_user_agent_from_fastapi,
    get_trusted_proxy_ips,
    validate_trusted_proxy,
)


@dataclass(frozen=True)
class _Request:
    headers: Mapping[str, str]


def _get_remote_addr(addr: str):
    def _getter() -> str:
        return addr

    return _getter


class TestValidateTrustedProxy:
    def test_allows_when_env_unset(self, caplog):
        with pytest.MonkeyPatch().context() as mp:
            mp.delenv("TRUSTED_PROXY_IPS", raising=False)
            request = _Request(headers={"X-Forwarded-For": "1.2.3.4"})

            validate_trusted_proxy(request, _get_remote_addr("10.0.0.1"))

            assert "TRUSTED_PROXY_IPS not set" in caplog.text

    def test_blocks_untrusted_proxy(self):
        with pytest.MonkeyPatch().context() as mp:
            mp.setenv("TRUSTED_PROXY_IPS", "10.0.0.2,10.0.0.3")
            request = _Request(headers={"X-Forwarded-For": "1.2.3.4"})

            with pytest.raises(HTTPException) as excinfo:
                validate_trusted_proxy(request, _get_remote_addr("10.0.0.1"))

            assert excinfo.value.status_code == 403
            assert "trusted proxy" in excinfo.value.detail.lower()

    def test_allows_trusted_proxy(self):
        with pytest.MonkeyPatch().context() as mp:
            mp.setenv("TRUSTED_PROXY_IPS", "10.0.0.1,10.0.0.2")
            request = _Request(headers={"X-Forwarded-For": "1.2.3.4"})

            validate_trusted_proxy(request, _get_remote_addr("10.0.0.1"))


class TestExtractClientIpFastAPI:
    def test_returns_remote_addr_when_env_unset(self):
        with pytest.MonkeyPatch().context() as mp:
            mp.delenv("TRUSTED_PROXY_IPS", raising=False)
            request = _Request(headers={"X-Forwarded-For": "1.2.3.4"})

            client_ip = extract_client_ip_from_fastapi(request, _get_remote_addr("10.0.0.1"))

            assert client_ip == "10.0.0.1"

    def test_returns_remote_addr_when_untrusted_proxy(self, caplog):
        with pytest.MonkeyPatch().context() as mp:
            mp.setenv("TRUSTED_PROXY_IPS", "10.0.0.2,10.0.0.3")
            request = _Request(headers={"X-Forwarded-For": "1.2.3.4"})

            client_ip = extract_client_ip_from_fastapi(request, _get_remote_addr("10.0.0.1"))

            assert client_ip == "10.0.0.1"
            assert "Ignoring X-Forwarded-For" in caplog.text

    def test_returns_x_forwarded_for_when_trusted_proxy(self):
        with pytest.MonkeyPatch().context() as mp:
            mp.setenv("TRUSTED_PROXY_IPS", "10.0.0.1")
            request = _Request(headers={"X-Forwarded-For": "1.2.3.4, 5.6.7.8"})

            client_ip = extract_client_ip_from_fastapi(request, _get_remote_addr("10.0.0.1"))

            assert client_ip == "1.2.3.4"

    def test_returns_remote_addr_when_no_x_forwarded_for(self):
        with pytest.MonkeyPatch().context() as mp:
            mp.setenv("TRUSTED_PROXY_IPS", "10.0.0.1")
            request = _Request(headers={})

            client_ip = extract_client_ip_from_fastapi(request, _get_remote_addr("10.0.0.1"))

            assert client_ip == "10.0.0.1"


class TestExtractUserAgent:
    def test_extracts_user_agent(self):
        request = _Request(headers={"User-Agent": "test-agent"})

        user_agent = extract_user_agent_from_fastapi(request)

        assert user_agent == "test-agent"

    def test_user_agent_default(self):
        request = _Request(headers={})

        user_agent = extract_user_agent_from_fastapi(request)

        assert user_agent == "unknown"


class TestGetTrustedProxyIps:
    def test_env_override(self):
        with pytest.MonkeyPatch().context() as mp:
            mp.setenv("TRUSTED_PROXY_IPS", "1.1.1.1, 2.2.2.2")

            ips = get_trusted_proxy_ips(env="dev")

            assert ips == ["1.1.1.1", "2.2.2.2"]

    def test_dev_defaults_when_env_not_set(self):
        with pytest.MonkeyPatch().context() as mp:
            mp.delenv("TRUSTED_PROXY_IPS", raising=False)

            ips = get_trusted_proxy_ips(env="dev")

            assert ips == ["127.0.0.1", "::1"]

    def test_prod_requires_configuration(self):
        with pytest.MonkeyPatch().context() as mp:
            mp.delenv("TRUSTED_PROXY_IPS", raising=False)

            with pytest.raises(RuntimeError, match="TRUSTED_PROXY_IPS must be configured"):
                get_trusted_proxy_ips(env="prod")

    def test_custom_fail_closed_envs_allows_empty(self):
        with pytest.MonkeyPatch().context() as mp:
            mp.delenv("TRUSTED_PROXY_IPS", raising=False)

            ips = get_trusted_proxy_ips(env="staging", fail_closed_envs=set())

            assert ips == []
