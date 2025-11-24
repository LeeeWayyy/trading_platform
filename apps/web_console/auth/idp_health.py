"""Auth0 IdP health monitoring for fallback triggering.

Component 1 of P2T3 Phase 3 (OAuth2/OIDC Authentication).

This module monitors Auth0 IdP availability by periodically checking the
.well-known/openid-configuration endpoint. If the IdP is unhealthy for 3+
consecutive checks, it triggers a warning for manual mTLS fallback activation.

References:
- docs/TASKS/P2T3-Phase3_Component1_Plan.md
- docs/TASKS/P2T3_Phase3_FINAL_PLAN.md (Component 1, lines 113-126)
"""

import logging
from datetime import UTC, datetime, timedelta

import httpx
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class IdPHealthStatus(BaseModel):
    """IdP health check result."""

    healthy: bool
    checked_at: datetime
    response_time_ms: float
    error: str | None = None
    consecutive_failures: int = 0


class IdPHealthChecker:
    """Monitors Auth0 IdP availability."""

    def __init__(
        self,
        auth0_domain: str,
        check_interval_seconds: int = 60,
        failure_threshold: int = 3,
        timeout_seconds: float = 5.0,
    ):
        """Initialize IdP health checker.

        Args:
            auth0_domain: Auth0 domain (e.g., "trading-platform.us.auth0.com")
            check_interval_seconds: Time between health checks (default: 60s)
            failure_threshold: Consecutive failures before fallback warning (default: 3)
            timeout_seconds: HTTP request timeout (default: 5.0s)
        """
        self.auth0_domain = auth0_domain
        self.check_interval = timedelta(seconds=check_interval_seconds)
        self.failure_threshold = failure_threshold
        self.timeout = timeout_seconds

        self._last_status: IdPHealthStatus | None = None
        self._last_check: datetime | None = None
        self._consecutive_failures = 0

    async def check_health(self) -> IdPHealthStatus:
        """Check Auth0 .well-known/openid-configuration endpoint.

        Returns:
            IdPHealthStatus with check results
        """
        url = f"https://{self.auth0_domain}/.well-known/openid-configuration"

        start = datetime.now(UTC)
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(url)
                response.raise_for_status()

                # Validate required OIDC fields exist
                data = response.json()
                required_fields = [
                    "issuer",
                    "authorization_endpoint",
                    "token_endpoint",
                    "jwks_uri",
                    "userinfo_endpoint",
                ]
                missing = [f for f in required_fields if f not in data]

                if missing:
                    raise ValueError(f"Missing OIDC fields: {missing}")

                # Validate issuer matches expected Auth0 tenant (prevent DNS poisoning)
                expected_issuer = f"https://{self.auth0_domain}/"
                if data.get("issuer") != expected_issuer:
                    raise ValueError(
                        f"Issuer mismatch: expected {expected_issuer}, got {data.get('issuer')}"
                    )

                response_time = (datetime.now(UTC) - start).total_seconds() * 1000

                # Reset failure counter on success
                self._consecutive_failures = 0

                status = IdPHealthStatus(
                    healthy=True,
                    checked_at=datetime.now(UTC),
                    response_time_ms=response_time,
                    consecutive_failures=0,
                )

                logger.info(
                    "IdP health check passed",
                    extra={
                        "auth0_domain": self.auth0_domain,
                        "response_time_ms": response_time,
                    },
                )

        except Exception as e:
            self._consecutive_failures += 1
            response_time = (datetime.now(UTC) - start).total_seconds() * 1000

            status = IdPHealthStatus(
                healthy=False,
                checked_at=datetime.now(UTC),
                response_time_ms=response_time,
                error=str(e),
                consecutive_failures=self._consecutive_failures,
            )

            logger.error(
                "IdP health check failed",
                extra={
                    "auth0_domain": self.auth0_domain,
                    "error": str(e),
                    "consecutive_failures": self._consecutive_failures,
                },
            )

        self._last_status = status
        self._last_check = datetime.now(UTC)

        return status

    def should_fallback_to_mtls(self) -> bool:
        """Determine if mTLS fallback should activate.

        Returns:
            True if consecutive failures >= threshold, False otherwise
        """
        if not self._last_status:
            return False

        return (
            not self._last_status.healthy
            and self._last_status.consecutive_failures >= self.failure_threshold
        )

    def get_last_status(self) -> IdPHealthStatus | None:
        """Get last health check result (cached).

        Returns:
            Last IdPHealthStatus or None if never checked
        """
        return self._last_status

    def should_check_now(self) -> bool:
        """Determine if health check is due.

        Returns:
            True if check_interval has elapsed since last check, False otherwise
        """
        if not self._last_check:
            return True

        return datetime.now(UTC) - self._last_check >= self.check_interval
