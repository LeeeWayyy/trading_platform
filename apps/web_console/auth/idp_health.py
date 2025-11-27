"""Auth0 IdP health monitoring with hysteresis for fallback triggering.

Component 1+6 of P2T3 Phase 3 (OAuth2/OIDC Authentication).

This module monitors Auth0 IdP availability by periodically checking the
.well-known/openid-configuration endpoint. If the IdP is unhealthy for 3+
consecutive checks, it triggers automatic mTLS fallback activation (Component 6).

Hysteresis:
- Entry: 3 consecutive failures (30s sustained outage)
- Exit: 5 consecutive successes + 5min stable period
- Exponential backoff: 10s polling → 60s polling after fallback entry

References:
- docs/TASKS/P2T3-Phase3_Component1_Plan.md
- docs/TASKS/P2T3-Phase3_Component6-7_Plan.md
- docs/TASKS/P2T3_Phase3_FINAL_PLAN.md (Component 1, lines 113-126)
"""

import logging
import os
from datetime import UTC, datetime, timedelta

import httpx
from prometheus_client import Counter, Gauge, Histogram
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# ============================================================================
# Prometheus Metrics (Component 6+7: P2T3 Phase 3)
# ============================================================================
# Enable multiprocess mode for Streamlit
if os.getenv("PROMETHEUS_MULTIPROC_DIR"):
    from prometheus_client import CollectorRegistry, multiprocess

    registry = CollectorRegistry()
    multiprocess.MultiProcessCollector(registry)
else:
    from prometheus_client import REGISTRY as registry

# IdP Health Monitoring Metrics (7 total)
idp_health_checks_total = Counter(
    "oauth2_idp_health_checks_total",
    "Total IdP health checks performed",
    ["auth0_domain", "result"],
    registry=registry,
)

idp_health_consecutive_failures = Gauge(
    "oauth2_idp_health_consecutive_failures",
    "Consecutive IdP health check failures",
    ["auth0_domain"],
    registry=registry,
)

idp_health_consecutive_successes = Gauge(
    "oauth2_idp_health_consecutive_successes",
    "Consecutive IdP health check successes",
    ["auth0_domain"],
    registry=registry,
)

idp_fallback_mode = Gauge(
    "oauth2_idp_fallback_mode",
    "IdP fallback mode active (1=active, 0=inactive)",
    ["auth0_domain"],
    registry=registry,
)

idp_stability_period_active = Gauge(
    "oauth2_idp_stability_period_active",
    "IdP recovery stability period active (1=active, 0=inactive)",
    ["auth0_domain"],
    registry=registry,
)

idp_health_check_duration = Histogram(
    "oauth2_idp_health_check_duration_seconds",
    "IdP health check duration in seconds",
    ["auth0_domain"],
    buckets=[0.1, 0.25, 0.5, 1.0, 2.0, 5.0],
    registry=registry,
)

idp_health_failures_total = Counter(
    "oauth2_idp_health_failures_total",
    "Total IdP health check failures by reason",
    ["auth0_domain", "reason"],
    registry=registry,
)


class IdPHealthStatus(BaseModel):
    """IdP health check result."""

    healthy: bool
    checked_at: datetime
    response_time_ms: float
    error: str | None = None
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    fallback_mode: bool = False


class IdPHealthChecker:
    """Monitors Auth0 IdP availability with hysteresis for fallback control."""

    def __init__(
        self,
        auth0_domain: str,
        normal_check_interval_seconds: int = 10,
        fallback_check_interval_seconds: int = 60,
        failure_threshold: int = 3,
        success_threshold: int = 5,
        stable_period_seconds: int = 300,
        timeout_seconds: float = 5.0,
    ):
        """Initialize IdP health checker with hysteresis.

        Args:
            auth0_domain: Auth0 domain (e.g., "trading-platform.us.auth0.com")
            normal_check_interval_seconds: Polling interval in normal mode (default: 10s)
            fallback_check_interval_seconds: Polling interval in fallback mode (default: 60s, exponential backoff)
            failure_threshold: Consecutive failures to enter fallback (default: 3)
            success_threshold: Consecutive successes to exit fallback (default: 5)
            stable_period_seconds: Stable period after success_threshold before exit (default: 300s = 5min)
            timeout_seconds: HTTP request timeout (default: 5.0s)
        """
        self.auth0_domain = auth0_domain
        self.normal_check_interval = timedelta(seconds=normal_check_interval_seconds)
        self.fallback_check_interval = timedelta(seconds=fallback_check_interval_seconds)
        self.failure_threshold = failure_threshold
        self.success_threshold = success_threshold
        self.stable_period = timedelta(seconds=stable_period_seconds)
        self.timeout = timeout_seconds

        self._last_status: IdPHealthStatus | None = None
        self._last_check: datetime | None = None
        self._consecutive_failures = 0
        self._consecutive_successes = 0
        self._fallback_mode = False
        self._stability_start: datetime | None = None  # When success_threshold reached

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
                self._consecutive_successes += 1

                # Prometheus metrics: Record success
                idp_health_checks_total.labels(
                    auth0_domain=self.auth0_domain, result="success"
                ).inc()
                idp_health_check_duration.labels(auth0_domain=self.auth0_domain).observe(
                    response_time / 1000
                )
                idp_health_consecutive_failures.labels(auth0_domain=self.auth0_domain).set(0)
                idp_health_consecutive_successes.labels(auth0_domain=self.auth0_domain).set(
                    self._consecutive_successes
                )

                # Hysteresis exit logic (Component 6): 5 successes + 5min stability
                if self._fallback_mode:
                    if self._consecutive_successes >= self.success_threshold:
                        # Start stability timer on first success_threshold reach
                        if self._stability_start is None:
                            self._stability_start = datetime.now(UTC)
                            idp_stability_period_active.labels(auth0_domain=self.auth0_domain).set(
                                1
                            )
                            logger.info(
                                "IdP recovery: success threshold reached, starting stability period",
                                extra={
                                    "consecutive_successes": self._consecutive_successes,
                                    "stability_period_seconds": self.stable_period.total_seconds(),
                                },
                            )
                        else:
                            # Check if stable period has elapsed
                            stable_duration = datetime.now(UTC) - self._stability_start
                            if stable_duration >= self.stable_period:
                                self._fallback_mode = False
                                self._stability_start = None
                                idp_fallback_mode.labels(auth0_domain=self.auth0_domain).set(0)
                                idp_stability_period_active.labels(
                                    auth0_domain=self.auth0_domain
                                ).set(0)
                                logger.info(
                                    "IdP recovery: exiting fallback mode (hysteresis satisfied)",
                                    extra={
                                        "consecutive_successes": self._consecutive_successes,
                                        "stable_duration_seconds": stable_duration.total_seconds(),
                                    },
                                )
                    else:
                        # Reset stability timer if success_threshold not maintained
                        if self._stability_start is not None:
                            idp_stability_period_active.labels(auth0_domain=self.auth0_domain).set(
                                0
                            )
                            logger.info(
                                "IdP recovery: stability period reset (threshold not maintained)",
                                extra={"consecutive_successes": self._consecutive_successes},
                            )
                            self._stability_start = None

                status = IdPHealthStatus(
                    healthy=True,
                    checked_at=datetime.now(UTC),
                    response_time_ms=response_time,
                    consecutive_failures=0,
                    consecutive_successes=self._consecutive_successes,
                    fallback_mode=self._fallback_mode,
                )

                logger.info(
                    "IdP health check passed",
                    extra={
                        "auth0_domain": self.auth0_domain,
                        "response_time_ms": response_time,
                        "consecutive_successes": self._consecutive_successes,
                        "fallback_mode": self._fallback_mode,
                    },
                )

        except Exception as e:
            self._consecutive_failures += 1
            self._consecutive_successes = 0  # Reset success counter on any failure
            self._stability_start = None  # Reset stability timer on failure
            response_time = (datetime.now(UTC) - start).total_seconds() * 1000

            # Prometheus metrics: Record failure
            idp_health_checks_total.labels(auth0_domain=self.auth0_domain, result="failure").inc()
            idp_health_check_duration.labels(auth0_domain=self.auth0_domain).observe(
                response_time / 1000
            )
            idp_health_consecutive_failures.labels(auth0_domain=self.auth0_domain).set(
                self._consecutive_failures
            )
            idp_health_consecutive_successes.labels(auth0_domain=self.auth0_domain).set(0)

            # Determine failure reason for detailed failure counter
            if isinstance(e, httpx.TimeoutException):
                reason = "timeout"
            elif isinstance(e, httpx.HTTPStatusError):
                reason = f"http_{e.response.status_code}"
            elif isinstance(e, ValueError):
                reason = "validation_error"
            else:
                reason = "network_error"

            idp_health_failures_total.labels(auth0_domain=self.auth0_domain, reason=reason).inc()

            # Reset stability period gauge if active
            if self._stability_start is not None:
                idp_stability_period_active.labels(auth0_domain=self.auth0_domain).set(0)

            # Hysteresis entry logic (Component 6): 3 consecutive failures
            if not self._fallback_mode and self._consecutive_failures >= self.failure_threshold:
                self._fallback_mode = True
                idp_fallback_mode.labels(auth0_domain=self.auth0_domain).set(1)
                logger.error(
                    "IdP outage detected: entering fallback mode (hysteresis entry)",
                    extra={
                        "auth0_domain": self.auth0_domain,
                        "consecutive_failures": self._consecutive_failures,
                        "fallback_mode": True,
                    },
                )

            status = IdPHealthStatus(
                healthy=False,
                checked_at=datetime.now(UTC),
                response_time_ms=response_time,
                error=str(e),
                consecutive_failures=self._consecutive_failures,
                consecutive_successes=0,
                fallback_mode=self._fallback_mode,
            )

            logger.error(
                "IdP health check failed",
                extra={
                    "auth0_domain": self.auth0_domain,
                    "error": str(e),
                    "consecutive_failures": self._consecutive_failures,
                    "fallback_mode": self._fallback_mode,
                },
            )

        self._last_status = status
        self._last_check = datetime.now(UTC)

        return status

    def should_fallback_to_mtls(self) -> bool:
        """Determine if mTLS fallback should activate (hysteresis-aware).

        Returns:
            True if fallback mode active (after 3 consecutive failures), False otherwise
        """
        return self._fallback_mode

    def is_fallback_mode(self) -> bool:
        """Check if currently in fallback mode (alias for should_fallback_to_mtls).

        Returns:
            True if fallback mode active, False otherwise
        """
        return self._fallback_mode

    def get_last_status(self) -> IdPHealthStatus | None:
        """Get last health check result (cached).

        Returns:
            Last IdPHealthStatus or None if never checked
        """
        return self._last_status

    def should_check_now(self) -> bool:
        """Determine if health check is due (with adaptive interval based on fallback mode).

        Exponential backoff (Component 6):
        - Normal mode: 10s polling interval
        - Fallback mode: 60s polling interval (reduce noise during outage)

        Returns:
            True if check_interval has elapsed since last check, False otherwise
        """
        if not self._last_check:
            return True

        # Use adaptive interval based on fallback mode
        interval = (
            self.fallback_check_interval if self._fallback_mode else self.normal_check_interval
        )
        return datetime.now(UTC) - self._last_check >= interval
