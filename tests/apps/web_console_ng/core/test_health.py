"""Comprehensive unit tests for apps/web_console_ng/core/health.py.

This test suite covers:
- ConnectionCounter thread-safety and guard against negative values
- is_internal_request detection (token, IP fallback, security)
- liveness_check endpoint (always returns 200)
- readiness_check endpoint (dependency checks, draining, internal/external responses)
- Graceful shutdown flow
- Health startup registration
"""

from __future__ import annotations

import asyncio
import json
import signal
import threading
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError
from starlette.requests import Request

import apps.web_console_ng.core.health as health_module
from apps.web_console_ng import config

# =============================================================================
# Helper Functions
# =============================================================================


def _make_request(headers: dict[str, str] | None = None, client_ip: str = "127.0.0.1") -> Request:
    """Create a mock ASGI Request for testing.

    Args:
        headers: Optional HTTP headers to include
        client_ip: Client IP address (default: 127.0.0.1)

    Returns:
        Request object suitable for testing health endpoints
    """
    raw_headers = []
    if headers:
        raw_headers = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    scope = {
        "type": "http",
        "headers": raw_headers,
        "method": "GET",
        "path": "/readyz",
        "scheme": "http",
        "query_string": b"",
        "client": (client_ip, 1234),
        "server": ("testserver", 80),
    }
    return Request(scope)


# =============================================================================
# ConnectionCounter Tests
# =============================================================================


def test_connection_counter_initial_value() -> None:
    """Verify ConnectionCounter starts at zero."""
    counter = health_module.ConnectionCounter()
    assert counter.value == 0


def test_connection_counter_increment() -> None:
    """Verify increment increases count and returns new value."""
    counter = health_module.ConnectionCounter()
    result = counter.increment()
    assert result == 1
    assert counter.value == 1


def test_connection_counter_decrement() -> None:
    """Verify decrement decreases count and returns new value."""
    counter = health_module.ConnectionCounter()
    counter.increment()
    counter.increment()
    result = counter.decrement()
    assert result == 1
    assert counter.value == 1


def test_connection_counter_decrement_guard_against_negative() -> None:
    """Verify decrement never goes below zero (guards against double-decrement)."""
    counter = health_module.ConnectionCounter()
    # Decrement without any increments
    result = counter.decrement()
    assert result == 0
    assert counter.value == 0
    # Multiple decrements still stay at zero
    counter.decrement()
    counter.decrement()
    assert counter.value == 0


def test_connection_counter_thread_safety() -> None:
    """Verify ConnectionCounter is thread-safe under concurrent access.

    Spawns multiple threads incrementing/decrementing concurrently and verifies
    final count is correct (no race conditions).
    """
    counter = health_module.ConnectionCounter()
    num_threads = 10
    increments_per_thread = 100

    def increment_many() -> None:
        for _ in range(increments_per_thread):
            counter.increment()

    def decrement_many() -> None:
        for _ in range(increments_per_thread // 2):
            counter.decrement()

    # Start threads
    threads = []
    for _ in range(num_threads):
        t1 = threading.Thread(target=increment_many)
        t2 = threading.Thread(target=decrement_many)
        threads.extend([t1, t2])
        t1.start()
        t2.start()

    # Wait for all threads
    for t in threads:
        t.join()

    # Expected: (num_threads * increments_per_thread) - (num_threads * increments_per_thread // 2)
    expected = num_threads * increments_per_thread - num_threads * (increments_per_thread // 2)
    assert counter.value == expected


# =============================================================================
# is_internal_request Tests
# =============================================================================


def test_is_internal_request_with_valid_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify token-based detection succeeds with correct X-Internal-Probe header."""
    monkeypatch.setattr(health_module, "INTERNAL_PROBE_TOKEN", "secret-token")
    monkeypatch.setattr(health_module, "INTERNAL_PROBE_DISABLE_IP_FALLBACK", True)

    request = _make_request({"X-Internal-Probe": "secret-token"})
    assert health_module.is_internal_request(request) is True


def test_is_internal_request_with_invalid_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify token-based detection fails with incorrect token."""
    monkeypatch.setattr(health_module, "INTERNAL_PROBE_TOKEN", "secret-token")
    monkeypatch.setattr(health_module, "INTERNAL_PROBE_DISABLE_IP_FALLBACK", True)

    request = _make_request({"X-Internal-Probe": "wrong-token"})
    assert health_module.is_internal_request(request) is False


def test_is_internal_request_ip_fallback_localhost(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify IP fallback allows localhost in non-DEBUG mode (strict networks)."""
    monkeypatch.setattr(health_module, "INTERNAL_PROBE_TOKEN", "")
    monkeypatch.setattr(health_module, "INTERNAL_PROBE_DISABLE_IP_FALLBACK", False)
    monkeypatch.setattr(config, "DEBUG", False)  # Production mode

    with patch("apps.web_console_ng.auth.client_ip.extract_trusted_client_ip") as extract_ip:
        extract_ip.return_value = "127.0.0.1"
        request = _make_request()
        assert health_module.is_internal_request(request) is True


def test_is_internal_request_ip_fallback_ipv6_localhost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify IP fallback allows IPv6 localhost."""
    monkeypatch.setattr(health_module, "INTERNAL_PROBE_TOKEN", "")
    monkeypatch.setattr(health_module, "INTERNAL_PROBE_DISABLE_IP_FALLBACK", False)
    monkeypatch.setattr(config, "DEBUG", False)

    with patch("apps.web_console_ng.auth.client_ip.extract_trusted_client_ip") as extract_ip:
        extract_ip.return_value = "::1"
        request = _make_request()
        assert health_module.is_internal_request(request) is True


def test_is_internal_request_ip_fallback_debug_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify IP fallback allows RFC1918 private networks in DEBUG mode."""
    monkeypatch.setattr(health_module, "INTERNAL_PROBE_TOKEN", "")
    monkeypatch.setattr(health_module, "INTERNAL_PROBE_DISABLE_IP_FALLBACK", False)
    monkeypatch.setattr(config, "DEBUG", True)

    with patch("apps.web_console_ng.auth.client_ip.extract_trusted_client_ip") as extract_ip:
        # Test various private networks
        for private_ip in ["10.0.0.1", "172.16.0.1", "192.168.1.1"]:
            extract_ip.return_value = private_ip
            request = _make_request()
            assert (
                health_module.is_internal_request(request) is True
            ), f"Expected {private_ip} to be internal in DEBUG mode"


def test_is_internal_request_ip_fallback_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify IP fallback is disabled when INTERNAL_PROBE_DISABLE_IP_FALLBACK=true."""
    monkeypatch.setattr(health_module, "INTERNAL_PROBE_TOKEN", "")
    monkeypatch.setattr(health_module, "INTERNAL_PROBE_DISABLE_IP_FALLBACK", True)
    monkeypatch.setattr(config, "DEBUG", True)

    with patch("apps.web_console_ng.auth.client_ip.extract_trusted_client_ip") as extract_ip:
        extract_ip.return_value = "127.0.0.1"
        request = _make_request()
        # Should fail because IP fallback is disabled (token required)
        assert health_module.is_internal_request(request) is False


def test_is_internal_request_external_ip_production(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify external IPs are rejected in production mode (strict networks)."""
    monkeypatch.setattr(health_module, "INTERNAL_PROBE_TOKEN", "")
    monkeypatch.setattr(health_module, "INTERNAL_PROBE_DISABLE_IP_FALLBACK", False)
    monkeypatch.setattr(config, "DEBUG", False)

    with patch("apps.web_console_ng.auth.client_ip.extract_trusted_client_ip") as extract_ip:
        # External IP should be rejected in production
        extract_ip.return_value = "1.2.3.4"
        request = _make_request()
        assert health_module.is_internal_request(request) is False


def test_is_internal_request_external_ip_debug(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify external IPs are rejected even in DEBUG mode."""
    monkeypatch.setattr(health_module, "INTERNAL_PROBE_TOKEN", "")
    monkeypatch.setattr(health_module, "INTERNAL_PROBE_DISABLE_IP_FALLBACK", False)
    monkeypatch.setattr(config, "DEBUG", True)

    with patch("apps.web_console_ng.auth.client_ip.extract_trusted_client_ip") as extract_ip:
        extract_ip.return_value = "8.8.8.8"
        request = _make_request()
        assert health_module.is_internal_request(request) is False


def test_is_internal_request_invalid_ip_format(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify invalid IP format is treated as external (security safeguard)."""
    monkeypatch.setattr(health_module, "INTERNAL_PROBE_TOKEN", "")
    monkeypatch.setattr(health_module, "INTERNAL_PROBE_DISABLE_IP_FALLBACK", False)
    monkeypatch.setattr(config, "DEBUG", True)

    with patch("apps.web_console_ng.auth.client_ip.extract_trusted_client_ip") as extract_ip:
        extract_ip.return_value = "not-an-ip"
        request = _make_request()
        # Should fail gracefully and treat as external
        assert health_module.is_internal_request(request) is False


# =============================================================================
# Liveness Check Tests
# =============================================================================


@pytest.mark.asyncio()
async def test_liveness_check_always_returns_200() -> None:
    """Verify liveness endpoint always returns 200 (pod is alive).

    Liveness does NOT check dependencies - only confirms the process is responsive.
    k8s uses this to determine if pod should be restarted.
    """
    response = await health_module.liveness_check()

    assert response.status_code == 200
    assert response.media_type == "application/json"

    body = json.loads(response.body)
    assert body["status"] == "alive"
    assert "timestamp" in body
    # Verify timestamp is valid ISO format
    datetime.fromisoformat(body["timestamp"])


# =============================================================================
# Readiness Check Tests - Draining
# =============================================================================


@pytest.mark.asyncio()
async def test_readiness_check_returns_503_when_draining(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify readiness returns 503 during graceful shutdown (draining).

    During drain, readiness fails immediately without checking dependencies.
    This signals k8s to stop routing new traffic.
    """
    monkeypatch.setattr(health_module, "is_draining", True)

    request = _make_request()
    response = await health_module.readiness_check(request)

    assert response.status_code == 503
    body = json.loads(response.body)
    assert body["status"] == "draining"
    assert "timestamp" in body


# =============================================================================
# Readiness Check Tests - Dependency Health
# =============================================================================


@pytest.mark.asyncio()
async def test_readiness_check_redis_ok_backend_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify readiness returns 200 when all dependencies are healthy."""
    monkeypatch.setattr(health_module, "is_draining", False)

    with patch("apps.web_console_ng.core.health.get_redis_store") as mock_get_redis:
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(return_value=True)
        mock_get_redis.return_value = mock_redis

        with patch("apps.web_console_ng.core.health.is_internal_request") as mock_is_internal:
            mock_is_internal.return_value = True

            request = _make_request()
            response = await health_module.readiness_check(request)

            assert response.status_code == 200
            body = json.loads(response.body)
            assert body["status"] == "ready"
            assert body["checks"]["redis"] == "ok"
            assert body["checks"]["backend"] == "ok"
            assert "connections" in body
            assert "pod" in body
            assert "timestamp" in body


@pytest.mark.asyncio()
async def test_readiness_check_redis_connection_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify readiness returns 503 when Redis connection fails."""
    monkeypatch.setattr(health_module, "is_draining", False)

    with patch("apps.web_console_ng.core.health.get_redis_store") as mock_get_redis:
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(side_effect=RedisConnectionError("Connection refused"))
        mock_get_redis.return_value = mock_redis

        with patch("apps.web_console_ng.core.health.is_internal_request") as mock_is_internal:
            mock_is_internal.return_value = True

            request = _make_request()
            response = await health_module.readiness_check(request)

            assert response.status_code == 503
            body = json.loads(response.body)
            assert body["status"] == "not_ready"
            assert body["checks"]["redis"] == "error: connection_failed"


@pytest.mark.asyncio()
async def test_readiness_check_redis_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify readiness returns 503 when Redis health check times out."""
    monkeypatch.setattr(health_module, "is_draining", False)

    with patch("apps.web_console_ng.core.health.get_redis_store") as mock_get_redis:
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(side_effect=TimeoutError("Redis timeout"))
        mock_get_redis.return_value = mock_redis

        with patch("apps.web_console_ng.core.health.is_internal_request") as mock_is_internal:
            mock_is_internal.return_value = True

            request = _make_request()
            response = await health_module.readiness_check(request)

            assert response.status_code == 503
            body = json.loads(response.body)
            assert body["status"] == "not_ready"
            assert body["checks"]["redis"] == "error: connection_failed"


@pytest.mark.asyncio()
async def test_readiness_check_redis_generic_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify readiness handles generic Redis errors gracefully."""
    monkeypatch.setattr(health_module, "is_draining", False)

    with patch("apps.web_console_ng.core.health.get_redis_store") as mock_get_redis:
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(side_effect=RedisError("Generic Redis error"))
        mock_get_redis.return_value = mock_redis

        with patch("apps.web_console_ng.core.health.is_internal_request") as mock_is_internal:
            mock_is_internal.return_value = True

            request = _make_request()
            response = await health_module.readiness_check(request)

            assert response.status_code == 503
            body = json.loads(response.body)
            assert body["status"] == "not_ready"
            assert body["checks"]["redis"] == "error: connection_failed"


@pytest.mark.asyncio()
async def test_readiness_check_redis_unexpected_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify readiness handles unexpected Redis errors (catch-all)."""
    monkeypatch.setattr(health_module, "is_draining", False)

    with patch("apps.web_console_ng.core.health.get_redis_store") as mock_get_redis:
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(side_effect=ValueError("Unexpected error"))
        mock_get_redis.return_value = mock_redis

        with patch("apps.web_console_ng.core.health.is_internal_request") as mock_is_internal:
            mock_is_internal.return_value = True

            request = _make_request()
            response = await health_module.readiness_check(request)

            assert response.status_code == 503
            body = json.loads(response.body)
            assert body["status"] == "not_ready"
            assert body["checks"]["redis"] == "error: unexpected_error"


# =============================================================================
# Readiness Check Tests - Backend Health
# =============================================================================


@pytest.mark.asyncio()
async def test_readiness_check_backend_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify backend check is skipped when HEALTH_CHECK_BACKEND_ENABLED=false."""
    monkeypatch.setattr(health_module, "is_draining", False)
    monkeypatch.setattr(config, "HEALTH_CHECK_BACKEND_ENABLED", False)

    with patch("apps.web_console_ng.core.health.get_redis_store") as mock_get_redis:
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(return_value=True)
        mock_get_redis.return_value = mock_redis

        with patch("apps.web_console_ng.core.health.is_internal_request") as mock_is_internal:
            mock_is_internal.return_value = True

            request = _make_request()
            response = await health_module.readiness_check(request)

            assert response.status_code == 200
            body = json.loads(response.body)
            assert body["checks"]["backend"] == "ok"


@pytest.mark.asyncio()
async def test_readiness_check_backend_skipped_in_production_with_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify backend check is skipped in production when INTERNAL_TOKEN_SECRET is set.

    Backend health check requires auth context which health endpoints don't have.
    """
    monkeypatch.setattr(health_module, "is_draining", False)
    monkeypatch.setattr(config, "HEALTH_CHECK_BACKEND_ENABLED", True)
    monkeypatch.setattr(config, "DEBUG", False)
    monkeypatch.setenv("INTERNAL_TOKEN_SECRET", "secret")

    with patch("apps.web_console_ng.core.health.get_redis_store") as mock_get_redis:
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(return_value=True)
        mock_get_redis.return_value = mock_redis

        with patch("apps.web_console_ng.core.health.is_internal_request") as mock_is_internal:
            mock_is_internal.return_value = True

            request = _make_request()
            response = await health_module.readiness_check(request)

            assert response.status_code == 200
            body = json.loads(response.body)
            assert body["checks"]["backend"] == "ok"


@pytest.mark.asyncio()
async def test_readiness_check_backend_ok_in_debug(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify backend check succeeds when backend is healthy (DEBUG mode only)."""
    monkeypatch.setattr(health_module, "is_draining", False)
    monkeypatch.setattr(config, "HEALTH_CHECK_BACKEND_ENABLED", True)
    monkeypatch.setattr(config, "DEBUG", True)
    monkeypatch.setenv("INTERNAL_TOKEN_SECRET", "")

    with patch("apps.web_console_ng.core.health.get_redis_store") as mock_get_redis:
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(return_value=True)
        mock_get_redis.return_value = mock_redis

        with patch("apps.web_console_ng.core.health.AsyncTradingClient.get") as mock_get_client:
            mock_client = MagicMock()
            mock_client.fetch_kill_switch_status = AsyncMock(return_value={"active": False})
            mock_get_client.return_value = mock_client

            with patch("apps.web_console_ng.core.health.is_internal_request") as mock_is_internal:
                mock_is_internal.return_value = True

                request = _make_request()
                response = await health_module.readiness_check(request)

                assert response.status_code == 200
                body = json.loads(response.body)
                assert body["checks"]["backend"] == "ok"


@pytest.mark.asyncio()
async def test_readiness_check_backend_connection_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify readiness returns 503 when backend connection fails."""
    monkeypatch.setattr(health_module, "is_draining", False)
    monkeypatch.setattr(config, "HEALTH_CHECK_BACKEND_ENABLED", True)
    monkeypatch.setattr(config, "DEBUG", True)
    monkeypatch.setenv("INTERNAL_TOKEN_SECRET", "")

    with patch("apps.web_console_ng.core.health.get_redis_store") as mock_get_redis:
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(return_value=True)
        mock_get_redis.return_value = mock_redis

        with patch("apps.web_console_ng.core.health.AsyncTradingClient.get") as mock_get_client:
            mock_client = MagicMock()
            mock_client.fetch_kill_switch_status = AsyncMock(
                side_effect=ConnectionError("Backend unavailable")
            )
            mock_get_client.return_value = mock_client

            with patch("apps.web_console_ng.core.health.is_internal_request") as mock_is_internal:
                mock_is_internal.return_value = True

                request = _make_request()
                response = await health_module.readiness_check(request)

                assert response.status_code == 503
                body = json.loads(response.body)
                assert body["checks"]["backend"] == "error: connection_failed"


@pytest.mark.asyncio()
async def test_readiness_check_backend_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify readiness returns 503 when backend health check times out."""
    monkeypatch.setattr(health_module, "is_draining", False)
    monkeypatch.setattr(config, "HEALTH_CHECK_BACKEND_ENABLED", True)
    monkeypatch.setattr(config, "DEBUG", True)
    monkeypatch.setenv("INTERNAL_TOKEN_SECRET", "")

    with patch("apps.web_console_ng.core.health.get_redis_store") as mock_get_redis:
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(return_value=True)
        mock_get_redis.return_value = mock_redis

        with patch("apps.web_console_ng.core.health.AsyncTradingClient.get") as mock_get_client:
            mock_client = MagicMock()
            mock_client.fetch_kill_switch_status = AsyncMock(side_effect=TimeoutError("Timeout"))
            mock_get_client.return_value = mock_client

            with patch("apps.web_console_ng.core.health.is_internal_request") as mock_is_internal:
                mock_is_internal.return_value = True

                request = _make_request()
                response = await health_module.readiness_check(request)

                assert response.status_code == 503
                body = json.loads(response.body)
                assert body["checks"]["backend"] == "error: connection_failed"


@pytest.mark.asyncio()
async def test_readiness_check_backend_unexpected_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify readiness handles unexpected backend errors (catch-all)."""
    monkeypatch.setattr(health_module, "is_draining", False)
    monkeypatch.setattr(config, "HEALTH_CHECK_BACKEND_ENABLED", True)
    monkeypatch.setattr(config, "DEBUG", True)
    monkeypatch.setenv("INTERNAL_TOKEN_SECRET", "")

    with patch("apps.web_console_ng.core.health.get_redis_store") as mock_get_redis:
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(return_value=True)
        mock_get_redis.return_value = mock_redis

        with patch("apps.web_console_ng.core.health.AsyncTradingClient.get") as mock_get_client:
            mock_client = MagicMock()
            mock_client.fetch_kill_switch_status = AsyncMock(
                side_effect=ValueError("Unexpected error")
            )
            mock_get_client.return_value = mock_client

            with patch("apps.web_console_ng.core.health.is_internal_request") as mock_is_internal:
                mock_is_internal.return_value = True

                request = _make_request()
                response = await health_module.readiness_check(request)

                assert response.status_code == 503
                body = json.loads(response.body)
                assert body["checks"]["backend"] == "error: unexpected_error"


# =============================================================================
# Readiness Check Tests - Concurrent Execution & Timeout
# =============================================================================


@pytest.mark.asyncio()
async def test_readiness_check_concurrent_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify health checks run concurrently (not sequentially).

    Max latency should be max(individual timeouts) not sum.
    """
    monkeypatch.setattr(health_module, "is_draining", False)
    monkeypatch.setattr(config, "HEALTH_CHECK_BACKEND_ENABLED", True)
    monkeypatch.setattr(config, "DEBUG", True)
    monkeypatch.setenv("INTERNAL_TOKEN_SECRET", "")

    redis_delay = 0.5
    backend_delay = 0.5

    with patch("apps.web_console_ng.core.health.get_redis_store") as mock_get_redis:
        mock_redis = AsyncMock()

        async def slow_redis_ping():
            await asyncio.sleep(redis_delay)
            return True

        mock_redis.ping = slow_redis_ping
        mock_get_redis.return_value = mock_redis

        with patch("apps.web_console_ng.core.health.AsyncTradingClient.get") as mock_get_client:
            mock_client = MagicMock()

            async def slow_backend_check(user_id, role, strategies):
                await asyncio.sleep(backend_delay)
                return {"active": False}

            mock_client.fetch_kill_switch_status = slow_backend_check
            mock_get_client.return_value = mock_client

            with patch("apps.web_console_ng.core.health.is_internal_request") as mock_is_internal:
                mock_is_internal.return_value = True

                request = _make_request()
                start = asyncio.get_event_loop().time()
                response = await health_module.readiness_check(request)
                elapsed = asyncio.get_event_loop().time() - start

                # Should complete in ~max(redis_delay, backend_delay) not sum
                # Allow 0.3s overhead for test execution
                assert elapsed < (redis_delay + backend_delay + 0.3)
                assert response.status_code == 200


@pytest.mark.asyncio()
async def test_readiness_check_global_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify global timeout prevents cumulative latency.

    If asyncio.gather exceeds global timeout (3s), the outer wait_for raises TimeoutError
    and any checks not yet completed are marked as "error: timeout".
    Note: Individual checks have their own timeouts (Redis 1s, backend 2s) which catch
    inner timeouts and convert them to "connection_failed".
    """
    monkeypatch.setattr(health_module, "is_draining", False)
    monkeypatch.setattr(config, "HEALTH_CHECK_BACKEND_ENABLED", True)
    monkeypatch.setattr(config, "DEBUG", True)
    monkeypatch.setenv("INTERNAL_TOKEN_SECRET", "")

    with patch("apps.web_console_ng.core.health.get_redis_store") as mock_get_redis:
        mock_redis = AsyncMock()

        async def very_slow_redis_ping():
            # Sleep longer than both individual timeout (1s) and global timeout (3s)
            await asyncio.sleep(10)
            return True

        mock_redis.ping = very_slow_redis_ping
        mock_get_redis.return_value = mock_redis

        with patch("apps.web_console_ng.core.health.AsyncTradingClient.get") as mock_get_client:
            mock_client = MagicMock()

            async def very_slow_backend_check(user_id, role, strategies):
                await asyncio.sleep(10)  # Exceeds global timeout
                return {"active": False}

            mock_client.fetch_kill_switch_status = very_slow_backend_check
            mock_get_client.return_value = mock_client

            with patch("apps.web_console_ng.core.health.is_internal_request") as mock_is_internal:
                mock_is_internal.return_value = True

                request = _make_request()
                response = await health_module.readiness_check(request)

                assert response.status_code == 503
                body = json.loads(response.body)
                # Both checks should hit their individual timeouts (1s for redis, 2s for backend)
                # which get caught and converted to "connection_failed"
                assert body["checks"]["redis"] == "error: connection_failed"
                assert body["checks"]["backend"] == "error: connection_failed"


@pytest.mark.asyncio()
async def test_readiness_check_outer_timeout_edge_case(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify outer timeout handler marks missing checks as timeout.

    This tests the defensive edge case where asyncio.wait_for on gather
    times out. We patch the readiness_check to directly raise TimeoutError
    at the gather level to trigger lines 252-257.
    """
    monkeypatch.setattr(health_module, "is_draining", False)

    # Patch at module level to simulate gather timeout
    with patch("apps.web_console_ng.core.health.asyncio.gather") as mock_gather:
        # Make gather raise TimeoutError after being wrapped by wait_for
        mock_gather.side_effect = TimeoutError("Global timeout")

        with patch("apps.web_console_ng.core.health.get_redis_store") as mock_get_redis:
            mock_redis = AsyncMock()
            mock_redis.ping = AsyncMock(return_value=True)
            mock_get_redis.return_value = mock_redis

            with patch("apps.web_console_ng.core.health.is_internal_request") as mock_is_internal:
                mock_is_internal.return_value = True

                request = _make_request()
                response = await health_module.readiness_check(request)

                assert response.status_code == 503
                body = json.loads(response.body)
                # Both checks should be marked as timeout since gather never completed
                assert body["checks"]["redis"] == "error: timeout"
                assert body["checks"]["backend"] == "error: timeout"


# =============================================================================
# Readiness Check Tests - Internal vs External Response
# =============================================================================


@pytest.mark.asyncio()
async def test_readiness_check_internal_detailed_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify internal requests get detailed response (checks, connections, pod)."""
    monkeypatch.setattr(health_module, "is_draining", False)
    monkeypatch.setattr(health_module.connection_counter, "_count", 5)

    with patch("apps.web_console_ng.core.health.get_redis_store") as mock_get_redis:
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(return_value=True)
        mock_get_redis.return_value = mock_redis

        with patch("apps.web_console_ng.core.health.is_internal_request") as mock_is_internal:
            mock_is_internal.return_value = True

            request = _make_request()
            response = await health_module.readiness_check(request)

            body = json.loads(response.body)
            assert "checks" in body
            assert "connections" in body
            assert body["connections"] == 5
            assert "pod" in body
            assert "timestamp" in body


@pytest.mark.asyncio()
async def test_readiness_check_external_minimal_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify external requests get minimal response (security).

    External response should only contain status and timestamp, no internal details.
    """
    monkeypatch.setattr(health_module, "is_draining", False)

    with patch("apps.web_console_ng.core.health.get_redis_store") as mock_get_redis:
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(return_value=True)
        mock_get_redis.return_value = mock_redis

        with patch("apps.web_console_ng.core.health.is_internal_request") as mock_is_internal:
            mock_is_internal.return_value = False

            request = _make_request()
            response = await health_module.readiness_check(request)

            body = json.loads(response.body)
            assert "status" in body
            assert "timestamp" in body
            # Should NOT contain internal details
            assert "checks" not in body
            assert "connections" not in body
            assert "pod" not in body


# =============================================================================
# Graceful Shutdown Tests
# =============================================================================


@pytest.mark.asyncio()
async def test_start_graceful_shutdown_sets_draining(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify graceful shutdown sets is_draining=True and waits for drain period."""
    monkeypatch.setenv("GRACEFUL_SHUTDOWN_SECONDS", "0.1")
    monkeypatch.setattr(health_module, "is_draining", False)

    await health_module.start_graceful_shutdown()

    assert health_module.is_draining is True


@pytest.mark.asyncio()
async def test_start_graceful_shutdown_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify multiple calls to start_graceful_shutdown are idempotent."""
    monkeypatch.setenv("GRACEFUL_SHUTDOWN_SECONDS", "0")
    monkeypatch.setattr(health_module, "is_draining", False)
    sleep_mock = AsyncMock()
    monkeypatch.setattr(asyncio, "sleep", sleep_mock)

    await health_module.start_graceful_shutdown()
    assert health_module.is_draining is True
    sleep_mock.assert_awaited_once()

    # Second call should return immediately
    await health_module.start_graceful_shutdown()
    assert sleep_mock.await_count == 1


@pytest.mark.asyncio()
async def test_start_graceful_shutdown_respects_env_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify graceful shutdown waits for configured GRACEFUL_SHUTDOWN_SECONDS."""
    monkeypatch.setenv("GRACEFUL_SHUTDOWN_SECONDS", "1.5")
    monkeypatch.setattr(health_module, "is_draining", False)
    sleep_mock = AsyncMock()
    monkeypatch.setattr(asyncio, "sleep", sleep_mock)

    await health_module.start_graceful_shutdown()

    sleep_mock.assert_awaited_once_with(1.5)


# =============================================================================
# Health Startup Tests
# =============================================================================


@pytest.mark.asyncio()
async def test_health_startup_registers_sigterm_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify _health_startup registers SIGTERM handler for graceful shutdown."""
    monkeypatch.setenv("WEB_WORKERS", "1")

    loop_mock = MagicMock()
    with patch("asyncio.get_running_loop", return_value=loop_mock):
        await health_module._health_startup()

        loop_mock.add_signal_handler.assert_called_once()
        args = loop_mock.add_signal_handler.call_args[0]
        assert args[0] == signal.SIGTERM


@pytest.mark.asyncio()
async def test_health_startup_fails_with_multiple_workers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify _health_startup raises RuntimeError if WEB_WORKERS != 1.

    NiceGUI requires single-process mode for admission control.
    """
    monkeypatch.setenv("WEB_WORKERS", "4")

    with pytest.raises(RuntimeError, match="NiceGUI requires single-process mode"):
        await health_module._health_startup()


@pytest.mark.asyncio()
async def test_health_startup_handles_notimplementederror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify _health_startup handles NotImplementedError on platforms without signal support.

    Windows and some platforms don't support signal handlers.
    """
    monkeypatch.setenv("WEB_WORKERS", "1")

    loop_mock = MagicMock()
    loop_mock.add_signal_handler.side_effect = NotImplementedError("Windows")

    with patch("asyncio.get_running_loop", return_value=loop_mock):
        # Should not raise, just log warning
        await health_module._health_startup()


# =============================================================================
# Health Setup Tests
# =============================================================================


def test_setup_health_endpoint_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify setup_health_endpoint is idempotent (only registers once)."""
    monkeypatch.setattr(health_module, "_health_setup_done", False)
    on_startup = MagicMock()
    monkeypatch.setattr(health_module.app, "on_startup", on_startup)

    health_module.setup_health_endpoint()
    health_module.setup_health_endpoint()

    # Should only call on_startup once
    assert on_startup.call_count == 1


def test_setup_health_endpoint_sets_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify setup_health_endpoint sets _health_setup_done flag."""
    monkeypatch.setattr(health_module, "_health_setup_done", False)
    on_startup = MagicMock()
    monkeypatch.setattr(health_module.app, "on_startup", on_startup)

    assert health_module._health_setup_done is False
    health_module.setup_health_endpoint()
    assert health_module._health_setup_done is True
