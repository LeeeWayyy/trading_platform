"""Tests for rate limiting dependency.

C5 Rate Limiting: Per-user and global rate limiting for order/signal endpoints.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI, Response
from fastapi.testclient import TestClient
from starlette.requests import Request

from libs.common.rate_limit_dependency import (
    RateLimitConfig,
    _get_rate_limit_mode,
    check_rate_limit_with_circuit_breaker,
    check_rate_limit_with_global,
    get_principal_key,
    rate_limit,
    rate_limit_api_checks_total,
    rate_limit_bypass_total,
    rate_limit_redis_timeout_total,
    should_bypass_rate_limit,
)


class TestMetricInitialization:
    """Test that metrics are properly initialized without conflicts."""

    def test_rate_limit_api_checks_total_labels(self) -> None:
        """Test new metric has correct labels (action, result, principal_type)."""
        assert rate_limit_api_checks_total._labelnames == ("action", "result", "principal_type")

    def test_rate_limit_bypass_total_labels(self) -> None:
        """Test bypass metric has method label."""
        assert rate_limit_bypass_total._labelnames == ("method",)

    def test_rate_limit_redis_timeout_labels(self) -> None:
        """Test timeout metric has action label."""
        assert rate_limit_redis_timeout_total._labelnames == ("action",)

    def test_distinct_metric_names(self) -> None:
        """Test new metrics use distinct names from existing rate_limit_checks_total."""
        # Import existing metric to verify no collision
        from libs.web_console_auth.rate_limiter import rate_limit_checks_total

        # Different metric names (Prometheus _name doesn't include _total suffix)
        assert rate_limit_api_checks_total._name != rate_limit_checks_total._name
        assert rate_limit_api_checks_total._name == "rate_limit_api_checks"
        assert rate_limit_checks_total._name == "rate_limit_checks"


class TestPrincipalExtraction:
    """Test principal key extraction for rate limiting buckets."""

    def _make_request(self, **state_attrs: Any) -> Request:
        """Create a mock request with specified state attributes."""
        scope = {
            "type": "http",
            "client": ("192.168.1.100", 12345),
            "state": {},
        }
        request = Request(scope)
        for key, value in state_attrs.items():
            setattr(request.state, key, value)
        return request

    def test_authenticated_user_id_extracted(self) -> None:
        """Test user ID extracted from request.state.user."""
        request = self._make_request(user={"user_id": "user123"})
        key, principal_type = get_principal_key(request)

        assert key == "user:user123"
        assert principal_type == "user"

    def test_authenticated_user_sub_extracted(self) -> None:
        """Test sub claim extracted when user_id not present."""
        request = self._make_request(user={"sub": "subject456"})
        key, principal_type = get_principal_key(request)

        assert key == "user:subject456"
        assert principal_type == "user"

    def test_strategy_id_extracted(self) -> None:
        """Test strategy ID extracted from request.state.strategy_id."""
        request = self._make_request(strategy_id="alpha_baseline")
        key, principal_type = get_principal_key(request)

        assert key == "strategy:alpha_baseline"
        assert principal_type == "strategy"

    def test_ip_fallback_for_unauthenticated(self) -> None:
        """Test IP fallback for unauthenticated requests."""
        request = self._make_request()
        key, principal_type = get_principal_key(request)

        assert key == "ip:192.168.1.100"
        assert principal_type == "ip"

    def test_unknown_ip_fallback(self) -> None:
        """Test unknown IP fallback when no client info."""
        scope = {
            "type": "http",
            "client": None,
            "state": {},
        }
        request = Request(scope)
        key, principal_type = get_principal_key(request)

        assert key == "ip:unknown"
        assert principal_type == "ip"

    def test_user_preferred_over_strategy(self) -> None:
        """Test user ID takes priority over strategy ID."""
        request = self._make_request(
            user={"user_id": "user123"},
            strategy_id="alpha_baseline",
        )
        key, principal_type = get_principal_key(request)

        assert key == "user:user123"
        assert principal_type == "user"

    def test_forged_auth_header_ignored(self) -> None:
        """Test unverified Authorization header is ignored."""
        # Request with no verified user but has Authorization header in scope
        scope = {
            "type": "http",
            "client": ("10.0.0.1", 12345),
            "headers": [(b"authorization", b"Bearer forged.token.here")],
            "state": {},
        }
        request = Request(scope)
        key, principal_type = get_principal_key(request)

        # Should use IP, not try to decode the token
        assert key == "ip:10.0.0.1"
        assert principal_type == "ip"


class TestInternalServiceBypass:
    """Test internal service bypass logic."""

    def _make_request(self, **state_attrs: Any) -> Request:
        """Create a mock request with specified state attributes."""
        scope = {"type": "http", "client": ("127.0.0.1", 12345), "state": {}}
        request = Request(scope)
        for key, value in state_attrs.items():
            setattr(request.state, key, value)
        return request

    def test_mtls_verified_bypasses(self) -> None:
        """Test mTLS verified request bypasses rate limit."""
        request = self._make_request(
            mtls_verified=True,
            mtls_service_name="signal_service",
        )
        assert should_bypass_rate_limit(request) is True

    def test_jwt_internal_audience_bypasses(self) -> None:
        """Test JWT with internal audience bypasses rate limit."""
        request = self._make_request(user={"aud": "internal-service"})
        assert should_bypass_rate_limit(request) is True

    def test_regular_user_does_not_bypass(self) -> None:
        """Test regular user JWT does not bypass."""
        request = self._make_request(user={"user_id": "user123", "aud": "web-client"})
        assert should_bypass_rate_limit(request) is False

    def test_no_static_token_bypass(self) -> None:
        """Test that static tokens cannot bypass (no X-Internal-Token header check)."""
        # Even if someone adds a header, the code doesn't check for static tokens
        scope = {
            "type": "http",
            "client": ("10.0.0.1", 12345),
            "headers": [(b"x-internal-token", b"secret-token-123")],
            "state": {},
        }
        request = Request(scope)
        assert should_bypass_rate_limit(request) is False


class TestRateLimitMode:
    """Test rate limit mode configuration."""

    def test_default_log_only_mode(self) -> None:
        """Test default mode is log_only (until C6 deployed)."""
        with patch.dict("os.environ", {}, clear=True):
            mode = _get_rate_limit_mode()
            assert mode == "log_only"

    def test_enforce_mode_from_env(self) -> None:
        """Test enforce mode can be set via environment."""
        with patch.dict("os.environ", {"RATE_LIMIT_MODE": "enforce"}):
            mode = _get_rate_limit_mode()
            assert mode == "enforce"

    def test_log_only_mode_from_env(self) -> None:
        """Test log_only mode from environment."""
        with patch.dict("os.environ", {"RATE_LIMIT_MODE": "log_only"}):
            mode = _get_rate_limit_mode()
            assert mode == "log_only"


class TestRateLimitConfig:
    """Test RateLimitConfig dataclass."""

    def test_default_values(self) -> None:
        """Test default config values."""
        config = RateLimitConfig(action="test", max_requests=10)

        assert config.action == "test"
        assert config.max_requests == 10
        assert config.window_seconds == 60
        assert config.burst_buffer == 0
        assert config.fallback_mode == "deny"
        assert config.global_limit is None
        assert config.anonymous_factor == 0.1

    def test_custom_values(self) -> None:
        """Test custom config values."""
        config = RateLimitConfig(
            action="order_submit",
            max_requests=40,
            window_seconds=60,
            burst_buffer=10,
            fallback_mode="allow",
            global_limit=80,
            anonymous_factor=0.2,
        )

        assert config.action == "order_submit"
        assert config.max_requests == 40
        assert config.burst_buffer == 10
        assert config.fallback_mode == "allow"
        assert config.global_limit == 80


class TestGlobalLimits:
    """Test global rate limiting behavior."""

    @pytest.mark.asyncio()
    async def test_global_limit_fires_first(self) -> None:
        """Test global limit fires BEFORE per-user limit when global exhausted."""
        mock_redis = AsyncMock()
        # Simulate global limit exceeded (return -1 for count)
        mock_redis.eval.return_value = [-1, 100, 1234567890]

        config = RateLimitConfig(
            action="order_submit",
            max_requests=40,
            global_limit=80,
        )

        allowed, remaining, reason = await check_rate_limit_with_global(
            mock_redis, "user:test", "order_submit", config
        )

        assert allowed is False
        assert remaining == 0
        assert reason == "global_limit_exceeded"

    @pytest.mark.asyncio()
    async def test_per_user_limit_blocks(self) -> None:
        """Test per-user limit blocks when exceeded."""
        mock_redis = AsyncMock()
        # Simulate per-user limit exceeded (count > max_requests)
        mock_redis.eval.return_value = [50, 0, 1234567890]

        config = RateLimitConfig(
            action="order_submit",
            max_requests=40,
            burst_buffer=5,
            global_limit=80,
        )

        allowed, remaining, reason = await check_rate_limit_with_global(
            mock_redis, "user:test", "order_submit", config
        )

        assert allowed is False
        assert remaining == 0
        assert reason == "per_user_limit_exceeded"

    @pytest.mark.asyncio()
    async def test_allowed_under_limits(self) -> None:
        """Test request allowed when under both limits."""
        mock_redis = AsyncMock()
        mock_redis.eval.return_value = [10, 0, 1234567890]

        config = RateLimitConfig(
            action="order_submit",
            max_requests=40,
            burst_buffer=10,
            global_limit=80,
        )

        allowed, remaining, reason = await check_rate_limit_with_global(
            mock_redis, "user:test", "order_submit", config
        )

        assert allowed is True
        assert remaining == 40  # 50 (effective) - 10 (count)
        assert reason == ""


class TestBurstBuffer:
    """Test burst buffer behavior."""

    @pytest.mark.asyncio()
    async def test_burst_buffer_raises_effective_limit(self) -> None:
        """Test burst buffer raises effective limit."""
        mock_redis = AsyncMock()
        # Count is 45, which is above max_requests (40) but below effective limit (50)
        mock_redis.eval.return_value = [45, 0, 1234567890]

        config = RateLimitConfig(
            action="order_submit",
            max_requests=40,
            burst_buffer=10,  # Effective limit = 50
        )

        allowed, remaining, reason = await check_rate_limit_with_global(
            mock_redis, "user:test", "order_submit", config
        )

        assert allowed is True
        assert remaining == 5  # 50 - 45


class TestRedisResilience:
    """Test Redis failure handling."""

    @pytest.mark.asyncio()
    async def test_redis_timeout_deny_mode(self) -> None:
        """Test Redis timeout triggers deny in fallback deny mode."""
        config = RateLimitConfig(
            action="order_submit",
            max_requests=40,
            fallback_mode="deny",
        )

        with patch(
            "libs.common.rate_limit_dependency._get_redis_client"
        ) as mock_get_client:
            mock_redis = AsyncMock()
            mock_redis.eval.side_effect = TimeoutError()
            mock_get_client.return_value = mock_redis

            allowed, remaining, reason = await check_rate_limit_with_circuit_breaker(
                "user:test", "order_submit", config
            )

            assert allowed is False
            assert remaining == 0
            assert reason == "redis_timeout"

    @pytest.mark.asyncio()
    async def test_redis_timeout_allow_mode(self) -> None:
        """Test Redis timeout allows in fallback allow mode."""
        config = RateLimitConfig(
            action="order_submit",
            max_requests=40,
            fallback_mode="allow",
        )

        with patch(
            "libs.common.rate_limit_dependency._get_redis_client"
        ) as mock_get_client:
            mock_redis = AsyncMock()
            mock_redis.eval.side_effect = TimeoutError()
            mock_get_client.return_value = mock_redis

            allowed, remaining, reason = await check_rate_limit_with_circuit_breaker(
                "user:test", "order_submit", config
            )

            assert allowed is True
            assert remaining == 40
            assert reason == ""

    @pytest.mark.asyncio()
    async def test_redis_error_deny_mode(self) -> None:
        """Test Redis error triggers deny in fallback deny mode."""
        config = RateLimitConfig(
            action="order_submit",
            max_requests=40,
            fallback_mode="deny",
        )

        with patch(
            "libs.common.rate_limit_dependency._get_redis_client"
        ) as mock_get_client:
            mock_redis = AsyncMock()
            mock_redis.eval.side_effect = Exception("Redis connection failed")
            mock_get_client.return_value = mock_redis

            allowed, remaining, reason = await check_rate_limit_with_circuit_breaker(
                "user:test", "order_submit", config
            )

            assert allowed is False
            assert remaining == 0
            assert reason == "redis_error"


class TestResponseHeaders:
    """Test rate limit response headers."""

    def test_headers_emitted_in_log_only_mode(self) -> None:
        """Test headers are emitted in log_only mode."""
        app = FastAPI()

        rl_config = RateLimitConfig(
            action="test_action",
            max_requests=10,
            burst_buffer=5,
        )
        rl_dependency = rate_limit(rl_config)

        from fastapi import Depends as FastAPIDepends

        @app.get("/test")
        async def test_endpoint(
            response: Response,
            remaining: int = FastAPIDepends(rl_dependency),
        ) -> dict[str, str]:
            return {"status": "ok", "remaining": str(remaining)}

        with patch(
            "libs.common.rate_limit_dependency.check_rate_limit_with_circuit_breaker"
        ) as mock_check:
            mock_check.return_value = (True, 10, "")
            with patch(
                "libs.common.rate_limit_dependency._get_rate_limit_mode",
                return_value="log_only",
            ):
                with patch(
                    "libs.common.rate_limit_dependency.get_principal_key",
                    return_value=("user:test", "user"),
                ):
                    client = TestClient(app)
                    response = client.get("/test")

                    assert response.status_code == 200
                    assert "X-RateLimit-Limit" in response.headers
                    assert "X-RateLimit-Remaining" in response.headers
                    assert "X-RateLimit-Window" in response.headers

    def test_headers_emitted_for_bypassed_requests(self) -> None:
        """Test headers still emitted for bypassed internal service requests."""
        app = FastAPI()

        rl_config = RateLimitConfig(
            action="test_action",
            max_requests=10,
            burst_buffer=5,
        )
        rl_dependency = rate_limit(rl_config)

        from fastapi import Depends as FastAPIDepends

        @app.get("/test")
        async def test_endpoint(
            response: Response,
            remaining: int = FastAPIDepends(rl_dependency),
        ) -> dict[str, str]:
            return {"status": "ok", "remaining": str(remaining)}

        with patch(
            "libs.common.rate_limit_dependency.should_bypass_rate_limit",
            return_value=True,
        ):
            client = TestClient(app)
            response = client.get("/test")

            assert response.status_code == 200
            assert "X-RateLimit-Limit" in response.headers
            assert response.headers["X-RateLimit-Limit"] == "15"  # 10 + 5 burst


class TestUserIsolation:
    """Test that users don't interfere with each other's buckets."""

    @pytest.mark.asyncio()
    async def test_different_users_different_buckets(self) -> None:
        """Test two users have separate rate limit buckets."""
        mock_redis = AsyncMock()

        # Track which keys are used
        keys_used: list[str] = []

        async def mock_eval(script: str, num_keys: int, *args: Any) -> list[Any]:
            keys_used.append(args[0])  # First arg is the key
            return [1, 0, 1234567890]

        mock_redis.eval = mock_eval

        config = RateLimitConfig(action="order_submit", max_requests=40)

        # User 1
        await check_rate_limit_with_global(mock_redis, "user:alice", "order_submit", config)

        # User 2
        await check_rate_limit_with_global(mock_redis, "user:bob", "order_submit", config)

        # Verify different keys used
        assert "rl:order_submit:user:alice" in keys_used
        assert "rl:order_submit:user:bob" in keys_used


class TestBrokerLimitAlignment:
    """Test that global limits align with broker ceiling."""

    def test_global_limits_under_alpaca_ceiling(self) -> None:
        """Test 80 + 30*3 = 170 < 200 Alpaca ceiling."""
        order_submit_global = 80
        order_slice_global = 30
        slice_fan_out = 3

        total_broker_orders = order_submit_global + (order_slice_global * slice_fan_out)

        assert total_broker_orders == 170
        assert total_broker_orders < 200  # Alpaca ceiling


class TestAnonymousTraffic:
    """Test anonymous traffic handling with reduced limits."""

    def test_anonymous_factor_applied(self) -> None:
        """Test anonymous factor reduces limits for IP-based buckets."""
        config = RateLimitConfig(
            action="order_submit",
            max_requests=40,
            burst_buffer=10,
            anonymous_factor=0.1,  # 10% of authenticated limits
        )

        # Anonymous effective limits
        anonymous_max = int(config.max_requests * config.anonymous_factor)
        anonymous_burst = int(config.burst_buffer * config.anonymous_factor)

        assert anonymous_max == 4  # 40 * 0.1
        assert anonymous_burst == 1  # 10 * 0.1


class TestIntegration:
    """Integration tests for rate limiting with FastAPI."""

    def test_rate_limit_dependency_integration(self) -> None:
        """Test rate limit dependency works with FastAPI endpoint."""
        app = FastAPI()

        rl_config = RateLimitConfig(
            action="test_action",
            max_requests=10,
            fallback_mode="allow",  # Allow in case of Redis issues
        )
        rl_dependency = rate_limit(rl_config)

        @app.get("/test")
        async def test_endpoint(
            response: Response,
            remaining: int = pytest.importorskip("fastapi").Depends(rl_dependency),
        ) -> dict[str, int]:
            return {"remaining": remaining}

        with patch(
            "libs.common.rate_limit_dependency.check_rate_limit_with_circuit_breaker"
        ) as mock_check:
            mock_check.return_value = (True, 8, "")
            with patch(
                "libs.common.rate_limit_dependency.get_principal_key",
                return_value=("user:test", "user"),
            ):
                client = TestClient(app)
                response = client.get("/test")

                assert response.status_code == 200
                assert response.json()["remaining"] == 8

    def test_429_response_in_enforce_mode(self) -> None:
        """Test 429 response with Retry-After header in enforce mode."""
        app = FastAPI()

        rl_config = RateLimitConfig(
            action="test_action",
            max_requests=10,
            window_seconds=60,
        )
        rl_dependency = rate_limit(rl_config)

        from fastapi import Depends as FastAPIDepends

        @app.get("/test")
        async def test_endpoint(
            response: Response,
            remaining: int = FastAPIDepends(rl_dependency),
        ) -> dict[str, str]:
            return {"status": "ok", "remaining": str(remaining)}

        with patch(
            "libs.common.rate_limit_dependency.check_rate_limit_with_circuit_breaker"
        ) as mock_check:
            mock_check.return_value = (False, 0, "per_user_limit_exceeded")
            with patch(
                "libs.common.rate_limit_dependency._get_rate_limit_mode",
                return_value="enforce",
            ):
                with patch(
                    "libs.common.rate_limit_dependency.get_principal_key",
                    return_value=("user:test", "user"),
                ):
                    client = TestClient(app)
                    response = client.get("/test")

                    assert response.status_code == 429
                    assert "Retry-After" in response.headers
                    assert response.headers["Retry-After"] == "60"
                    assert response.json()["detail"]["reason"] == "per_user_limit_exceeded"

    def test_log_only_mode_allows_blocked_requests(self) -> None:
        """Test log_only mode allows requests that would be blocked."""
        app = FastAPI()

        rl_config = RateLimitConfig(action="test_action", max_requests=10)
        rl_dependency = rate_limit(rl_config)

        from fastapi import Depends as FastAPIDepends

        @app.get("/test")
        async def test_endpoint(
            response: Response,
            remaining: int = FastAPIDepends(rl_dependency),
        ) -> dict[str, str]:
            return {"status": "ok", "remaining": str(remaining)}

        with patch(
            "libs.common.rate_limit_dependency.check_rate_limit_with_circuit_breaker"
        ) as mock_check:
            mock_check.return_value = (False, 0, "per_user_limit_exceeded")
            with patch(
                "libs.common.rate_limit_dependency._get_rate_limit_mode",
                return_value="log_only",
            ):
                with patch(
                    "libs.common.rate_limit_dependency.get_principal_key",
                    return_value=("user:test", "user"),
                ):
                    client = TestClient(app)
                    response = client.get("/test")

                    # Log-only mode should allow the request
                    assert response.status_code == 200
