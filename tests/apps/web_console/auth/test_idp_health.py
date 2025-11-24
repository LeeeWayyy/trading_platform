"""Tests for IdP health checker (Component 1, P2T3 Phase 3).

Tests the Auth0 IdP health monitoring functionality including:
- Successful health checks
- Failure detection and consecutive failure tracking
- Fallback threshold logic
- Check interval timing

References:
- apps/web_console/auth/idp_health.py
- docs/TASKS/P2T3-Phase3_Component1_Plan.md (Task 4)
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from apps.web_console.auth.idp_health import IdPHealthChecker, IdPHealthStatus


@pytest.fixture()
def health_checker():
    """Create IdP health checker instance."""
    return IdPHealthChecker(
        auth0_domain="test.us.auth0.com",
        check_interval_seconds=60,
        failure_threshold=3,
        timeout_seconds=5.0,
    )


@pytest.mark.asyncio()
async def test_health_check_success(health_checker):
    """Test successful IdP health check."""
    # Mock successful OIDC configuration response
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "issuer": "https://test.us.auth0.com/",
        "authorization_endpoint": "https://test.us.auth0.com/authorize",
        "token_endpoint": "https://test.us.auth0.com/oauth/token",
        "jwks_uri": "https://test.us.auth0.com/.well-known/jwks.json",
        "userinfo_endpoint": "https://test.us.auth0.com/userinfo",
    }
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_response)

        status = await health_checker.check_health()

        assert status.healthy is True
        assert status.consecutive_failures == 0
        assert status.error is None
        assert status.response_time_ms > 0
        assert health_checker._consecutive_failures == 0


@pytest.mark.asyncio()
async def test_health_check_http_error(health_checker):
    """Test IdP health check with HTTP error."""
    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.get = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "500 Server Error", request=MagicMock(), response=MagicMock()
            )
        )

        status = await health_checker.check_health()

        assert status.healthy is False
        assert status.consecutive_failures == 1
        assert status.error is not None
        assert "500 Server Error" in status.error
        assert health_checker._consecutive_failures == 1


@pytest.mark.asyncio()
async def test_health_check_timeout(health_checker):
    """Test IdP health check with timeout."""
    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.get = AsyncMock(
            side_effect=httpx.TimeoutException("Request timed out")
        )

        status = await health_checker.check_health()

        assert status.healthy is False
        assert status.consecutive_failures == 1
        assert "timed out" in status.error.lower()


@pytest.mark.asyncio()
async def test_health_check_missing_oidc_fields(health_checker):
    """Test IdP health check with incomplete OIDC configuration."""
    # Missing jwks_uri field
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "issuer": "https://test.us.auth0.com/",
        "authorization_endpoint": "https://test.us.auth0.com/authorize",
        "token_endpoint": "https://test.us.auth0.com/oauth/token",
        # Missing jwks_uri and userinfo_endpoint
    }
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_response)

        status = await health_checker.check_health()

        assert status.healthy is False
        assert status.consecutive_failures == 1
        assert "Missing OIDC fields" in status.error


@pytest.mark.asyncio()
async def test_health_check_issuer_mismatch(health_checker):
    """Test IdP health check with wrong issuer (DNS poisoning protection)."""
    # Issuer doesn't match expected Auth0 domain
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "issuer": "https://malicious.auth0.com/",  # Wrong issuer!
        "authorization_endpoint": "https://test.us.auth0.com/authorize",
        "token_endpoint": "https://test.us.auth0.com/oauth/token",
        "jwks_uri": "https://test.us.auth0.com/.well-known/jwks.json",
        "userinfo_endpoint": "https://test.us.auth0.com/userinfo",
    }
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_response)

        status = await health_checker.check_health()

        assert status.healthy is False
        assert status.consecutive_failures == 1
        assert "Issuer mismatch" in status.error
        assert "expected https://test.us.auth0.com/" in status.error


@pytest.mark.asyncio()
async def test_consecutive_failures_tracking(health_checker):
    """Test consecutive failure counter increments correctly."""
    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.get = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "500 Server Error", request=MagicMock(), response=MagicMock()
            )
        )

        # First failure
        status1 = await health_checker.check_health()
        assert status1.consecutive_failures == 1

        # Second failure
        status2 = await health_checker.check_health()
        assert status2.consecutive_failures == 2

        # Third failure
        status3 = await health_checker.check_health()
        assert status3.consecutive_failures == 3


@pytest.mark.asyncio()
async def test_consecutive_failures_reset_on_success(health_checker):
    """Test consecutive failures reset to 0 on successful check."""
    with patch("httpx.AsyncClient") as mock_client:
        # Simulate 2 failures
        mock_client.return_value.__aenter__.return_value.get = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "500 Server Error", request=MagicMock(), response=MagicMock()
            )
        )
        await health_checker.check_health()
        await health_checker.check_health()
        assert health_checker._consecutive_failures == 2

        # Simulate success
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "issuer": "https://test.us.auth0.com/",
            "authorization_endpoint": "https://test.us.auth0.com/authorize",
            "token_endpoint": "https://test.us.auth0.com/oauth/token",
            "jwks_uri": "https://test.us.auth0.com/.well-known/jwks.json",
            "userinfo_endpoint": "https://test.us.auth0.com/userinfo",
        }
        mock_response.raise_for_status = MagicMock()
        mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_response)

        status = await health_checker.check_health()
        assert status.healthy is True
        assert status.consecutive_failures == 0
        assert health_checker._consecutive_failures == 0


def test_should_fallback_to_mtls_below_threshold(health_checker):
    """Test fallback not triggered below failure threshold."""
    health_checker._last_status = IdPHealthStatus(
        healthy=False,
        checked_at=datetime.now(UTC),
        response_time_ms=100.0,
        error="Connection error",
        consecutive_failures=2,  # Below threshold of 3
    )

    assert health_checker.should_fallback_to_mtls() is False


def test_should_fallback_to_mtls_at_threshold(health_checker):
    """Test fallback triggered at failure threshold."""
    health_checker._last_status = IdPHealthStatus(
        healthy=False,
        checked_at=datetime.now(UTC),
        response_time_ms=100.0,
        error="Connection error",
        consecutive_failures=3,  # At threshold
    )

    assert health_checker.should_fallback_to_mtls() is True


def test_should_fallback_to_mtls_above_threshold(health_checker):
    """Test fallback triggered above failure threshold."""
    health_checker._last_status = IdPHealthStatus(
        healthy=False,
        checked_at=datetime.now(UTC),
        response_time_ms=100.0,
        error="Connection error",
        consecutive_failures=5,  # Above threshold
    )

    assert health_checker.should_fallback_to_mtls() is True


def test_should_fallback_to_mtls_no_status(health_checker):
    """Test fallback not triggered when no status available."""
    assert health_checker._last_status is None
    assert health_checker.should_fallback_to_mtls() is False


def test_get_last_status(health_checker):
    """Test retrieving last health check status."""
    assert health_checker.get_last_status() is None

    # Set a status
    status = IdPHealthStatus(
        healthy=True,
        checked_at=datetime.now(UTC),
        response_time_ms=50.0,
        consecutive_failures=0,
    )
    health_checker._last_status = status

    assert health_checker.get_last_status() == status


def test_should_check_now_never_checked(health_checker):
    """Test should_check_now returns True when never checked."""
    assert health_checker._last_check is None
    assert health_checker.should_check_now() is True


def test_should_check_now_interval_elapsed(health_checker):
    """Test should_check_now returns True when interval elapsed."""
    # Simulate last check 61 seconds ago (interval is 60s)
    health_checker._last_check = datetime.now(UTC) - timedelta(seconds=61)
    assert health_checker.should_check_now() is True


def test_should_check_now_interval_not_elapsed(health_checker):
    """Test should_check_now returns False when interval not elapsed."""
    # Simulate last check 30 seconds ago (interval is 60s)
    health_checker._last_check = datetime.now(UTC) - timedelta(seconds=30)
    assert health_checker.should_check_now() is False
