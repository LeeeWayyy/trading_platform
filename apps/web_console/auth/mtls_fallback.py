"""mTLS Fallback Authentication for Auth0 IdP Outages.

Component 6 of P2T3 Phase 3 (OAuth2/OIDC Authentication).

This module provides emergency admin-only authentication via client certificates
when Auth0 IdP is unavailable for prolonged periods. Only activates when:
1. ENABLE_MTLS_FALLBACK=true (default: disabled)
2. IdP health monitor reports 3+ consecutive failures

Security Features:
- Admin-only access (pre-distributed client certificates)
- Certificate lifetime enforcement (7-day max, rejects expired/long-lived)
- CRL enforcement (1h cache TTL, 24h freshness, fail-secure)
- Audit logging (all fallback authentications with cert fingerprint, IP, CRL status)
- Auto-disable on excessive auth failures (>10 failures/min)

References:
- docs/TASKS/P2T3-Phase3_Component6-7_Plan.md
"""

import hashlib
import logging
import os
from datetime import UTC, datetime, timedelta

import httpx
from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding
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
    multiprocess.MultiProcessCollector(registry)  # type: ignore[no-untyped-call]
else:
    from prometheus_client import REGISTRY as registry

# mTLS Fallback Authentication Metrics (9 total)
mtls_auth_total = Counter(
    "oauth2_mtls_auth_total",
    "Total mTLS fallback authentication attempts",
    ["cn", "result"],
    registry=registry,
)

mtls_auth_failures_total = Counter(
    "oauth2_mtls_auth_failures_total",
    "Total mTLS fallback authentication failures by reason",
    ["cn", "reason"],
    registry=registry,
)

mtls_cert_not_after_timestamp = Gauge(
    "oauth2_mtls_cert_not_after_timestamp",
    "Certificate expiry timestamp (Unix epoch seconds)",
    ["cn"],
    registry=registry,
)

mtls_crl_fetch_total = Counter(
    "oauth2_mtls_crl_fetch_total",
    "Total CRL fetch attempts",
    ["crl_url", "result"],
    registry=registry,
)

mtls_crl_fetch_failures = Counter(
    "oauth2_mtls_crl_fetch_failures_total",
    "CRL fetch failures (alert rule compatibility)",
    ["crl_url"],
    registry=registry,
)

mtls_crl_last_update_timestamp = Gauge(
    "oauth2_mtls_crl_last_update_timestamp",
    "CRL last update timestamp (Unix epoch seconds)",
    ["crl_url"],
    registry=registry,
)

mtls_crl_cache_age_seconds = Gauge(
    "oauth2_mtls_crl_cache_age_seconds",
    "CRL cache age in seconds",
    ["crl_url"],
    registry=registry,
)

mtls_crl_fetch_duration = Histogram(
    "oauth2_mtls_crl_fetch_duration_seconds",
    "CRL fetch duration in seconds",
    ["crl_url"],
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0],
    registry=registry,
)

mtls_cert_validation_duration = Histogram(
    "oauth2_mtls_cert_validation_duration_seconds",
    "Certificate validation duration in seconds",
    ["result"],
    buckets=[0.01, 0.05, 0.1, 0.5, 1.0, 2.0],
    registry=registry,
)

# NOTE: mtls_active_admin_sessions removed after review (never instrumented)
# mTLS sessions are tracked by global active_sessions_count in auth/__init__.py


class CertificateInfo(BaseModel):
    """Client certificate validation result."""

    valid: bool
    cn: str
    dn: str
    fingerprint: str
    not_before: datetime
    not_after: datetime
    lifetime_days: float
    is_admin: bool = False
    error: str | None = None
    crl_status: str = "unknown"  # "valid", "revoked", "unknown"


class CRLCache:
    """Certificate Revocation List cache with TTL and freshness enforcement."""

    def __init__(
        self,
        crl_url: str,
        cache_ttl_seconds: int = 3600,  # 1 hour
        max_crl_age_seconds: int = 86400,  # 24 hours
        timeout_seconds: float = 5.0,
    ):
        """Initialize CRL cache.

        Args:
            crl_url: CRL distribution point URL
            cache_ttl_seconds: Cache TTL (default: 1h)
            max_crl_age_seconds: Maximum CRL age before rejection (default: 24h)
            timeout_seconds: HTTP request timeout (default: 5.0s)
        """
        self.crl_url = crl_url
        self.cache_ttl = timedelta(seconds=cache_ttl_seconds)
        self.max_crl_age = timedelta(seconds=max_crl_age_seconds)
        self.timeout = timeout_seconds

        self._crl: x509.CertificateRevocationList | None = None
        self._cached_at: datetime | None = None

    async def fetch_crl(self) -> x509.CertificateRevocationList:
        """Fetch CRL from distribution point with caching.

        Returns:
            CertificateRevocationList object

        Raises:
            Exception: If CRL fetch fails (network error, parse error)
            ValueError: If CRL age exceeds max_crl_age (fail-secure)
        """
        # Return cached CRL if fresh
        if self._crl and self._cached_at:
            cache_age = datetime.now(UTC) - self._cached_at
            if cache_age < self.cache_ttl:
                # Update cache age metric
                mtls_crl_cache_age_seconds.labels(crl_url=self.crl_url).set(
                    cache_age.total_seconds()
                )
                logger.debug(
                    "Using cached CRL",
                    extra={"cache_age_seconds": cache_age.total_seconds()},
                )
                return self._crl

        # Fetch fresh CRL
        logger.info("Fetching CRL from distribution point", extra={"crl_url": self.crl_url})
        fetch_start = datetime.now(UTC)
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(self.crl_url)
                response.raise_for_status()

                # Parse CRL
                crl = x509.load_der_x509_crl(response.content)
                fetch_duration = (datetime.now(UTC) - fetch_start).total_seconds()

                # Validate CRL freshness (fail-secure: reject if CRL too old)
                # Note: cryptography's standard attributes return naive datetimes (UTC),
                # so we add tzinfo=UTC to make them timezone-aware for comparison
                crl_last_update = crl.last_update.replace(tzinfo=UTC)
                if crl.next_update:
                    crl_age = datetime.now(UTC) - crl_last_update
                    if crl_age > self.max_crl_age:
                        raise ValueError(
                            f"CRL too old: last_update={crl_last_update}, "
                            f"age={crl_age.total_seconds():.0f}s (max={self.max_crl_age.total_seconds():.0f}s)"
                        )

                # Update cache
                self._crl = crl
                self._cached_at = datetime.now(UTC)

                # Prometheus metrics: Record successful CRL fetch
                mtls_crl_fetch_total.labels(crl_url=self.crl_url, result="success").inc()
                mtls_crl_fetch_duration.labels(crl_url=self.crl_url).observe(fetch_duration)
                mtls_crl_last_update_timestamp.labels(crl_url=self.crl_url).set(
                    crl_last_update.timestamp()
                )
                mtls_crl_cache_age_seconds.labels(crl_url=self.crl_url).set(0)

                crl_next_update = crl.next_update.replace(tzinfo=UTC) if crl.next_update else None
                logger.info(
                    "CRL fetched successfully",
                    extra={
                        "last_update": crl_last_update.isoformat(),
                        "next_update": (crl_next_update.isoformat() if crl_next_update else "N/A"),
                        "revoked_count": len(list(crl)),
                    },
                )

                return crl

        except httpx.HTTPError as e:
            # Prometheus metrics: Record CRL fetch failure
            mtls_crl_fetch_total.labels(crl_url=self.crl_url, result="failure").inc()
            mtls_crl_fetch_failures.labels(crl_url=self.crl_url).inc()
            logger.error("CRL fetch failed (HTTP error)", extra={"error": str(e)})
            raise
        except Exception as e:
            # Prometheus metrics: Record CRL fetch failure
            mtls_crl_fetch_total.labels(crl_url=self.crl_url, result="failure").inc()
            mtls_crl_fetch_failures.labels(crl_url=self.crl_url).inc()
            logger.error("CRL fetch failed (parse error)", extra={"error": str(e)})
            raise

    async def is_revoked(self, cert: x509.Certificate) -> bool:
        """Check if certificate is revoked.

        Args:
            cert: Certificate to check

        Returns:
            True if revoked, False if valid

        Raises:
            Exception: If CRL fetch fails (fail-secure: reject auth on CRL unavailability)
        """
        try:
            crl = await self.fetch_crl()

            # Check if certificate serial number is in CRL
            for revoked in crl:
                if revoked.serial_number == cert.serial_number:
                    # Make revocation_date timezone-aware (cryptography returns naive UTC)
                    revoke_date = revoked.revocation_date.replace(tzinfo=UTC)
                    logger.warning(
                        "Certificate revoked",
                        extra={
                            "serial": hex(cert.serial_number),
                            "revoked_at": revoke_date.isoformat(),
                        },
                    )
                    return True

            return False

        except Exception:
            # Fail-secure: Reject auth if CRL check fails
            # This prevents accepting potentially revoked certificates
            logger.error("CRL check failed - failing secure (rejecting auth)")
            raise


class MtlsFallbackValidator:
    """Validates admin client certificates for fallback authentication."""

    def __init__(
        self,
        admin_cn_allowlist: list[str],
        crl_url: str,
        max_cert_lifetime_days: int = 7,
        expiry_warning_hours: int = 24,
    ):
        """Initialize mTLS fallback validator.

        Args:
            admin_cn_allowlist: List of allowed admin CNs (Common Names)
            crl_url: CRL distribution point URL
            max_cert_lifetime_days: Maximum certificate lifetime in days (default: 7)
            expiry_warning_hours: Warn if cert expires within this many hours (default: 24)
        """
        self.admin_cn_allowlist = admin_cn_allowlist
        self.max_cert_lifetime = timedelta(days=max_cert_lifetime_days)
        self.expiry_warning_threshold = timedelta(hours=expiry_warning_hours)

        # Initialize CRL cache
        self.crl_cache = CRLCache(crl_url=crl_url)

    def _record_auth_failure(self, cn: str, reason: str, validation_duration: float) -> None:
        """Record authentication failure metrics with cardinality protection."""
        # Cardinality protection: Only use CN from allowlist, else "unauthorized"
        label_cn = cn if cn in self.admin_cn_allowlist else "unauthorized"

        mtls_auth_total.labels(cn=label_cn, result="failure").inc()
        mtls_auth_failures_total.labels(cn=label_cn, reason=reason).inc()
        mtls_cert_validation_duration.labels(result="failure").observe(validation_duration)

    async def validate_certificate(self, cert_pem: str, headers: dict[str, str]) -> CertificateInfo:
        """Validate admin client certificate with comprehensive checks.

        Validation steps:
        1. Parse certificate from PEM
        2. Verify certificate lifetime (reject if > max_cert_lifetime_days)
        3. Verify not expired
        4. Warn if expiring soon (<24h)
        5. Extract CN and check against admin allowlist
        6. Check CRL status (fail-secure if CRL unavailable)
        7. Compute fingerprint for audit logging

        Args:
            cert_pem: Certificate in PEM format (from nginx X-SSL-Client-Cert header)
            headers: Request headers (for X-SSL-Client-Verify validation)

        Returns:
            CertificateInfo with validation results
        """
        validation_start = datetime.now(UTC)
        try:
            # Step 0: Verify nginx performed successful mTLS verification
            client_verify = headers.get("X-SSL-Client-Verify", "")
            if client_verify != "SUCCESS":
                validation_duration = (datetime.now(UTC) - validation_start).total_seconds()
                self._record_auth_failure("", "nginx_verification_failed", validation_duration)
                return CertificateInfo(
                    valid=False,
                    cn="",
                    dn="",
                    fingerprint="",
                    not_before=datetime.now(UTC),
                    not_after=datetime.now(UTC),
                    lifetime_days=0,
                    error=f"Client verification failed: {client_verify}",
                )

            # Step 1: Parse certificate
            cert = x509.load_pem_x509_certificate(cert_pem.encode())

            # Step 2: Extract certificate info
            cn = self._extract_cn(cert)
            dn = cert.subject.rfc4514_string()
            # Make dates timezone-aware (cryptography returns naive UTC datetimes)
            not_before = cert.not_valid_before.replace(tzinfo=UTC)
            not_after = cert.not_valid_after.replace(tzinfo=UTC)
            lifetime = not_after - not_before
            lifetime_days = lifetime.total_seconds() / 86400

            # Compute fingerprint (SHA256)
            fingerprint = hashlib.sha256(cert.public_bytes(encoding=Encoding.DER)).hexdigest()

            # Step 3: Validate certificate lifetime (reject long-lived certs)
            if lifetime > self.max_cert_lifetime:
                validation_duration = (datetime.now(UTC) - validation_start).total_seconds()
                self._record_auth_failure(cn, "lifetime_too_long", validation_duration)
                logger.warning(
                    "Certificate lifetime exceeds maximum",
                    extra={
                        "cn": cn,
                        "lifetime_days": lifetime_days,
                        "max_days": self.max_cert_lifetime.days,
                    },
                )
                return CertificateInfo(
                    valid=False,
                    cn=cn,
                    dn=dn,
                    fingerprint=fingerprint,
                    not_before=not_before,
                    not_after=not_after,
                    lifetime_days=lifetime_days,
                    error=f"Certificate lifetime ({lifetime_days:.1f} days) exceeds maximum ({self.max_cert_lifetime.days} days)",
                )

            # Step 4: Verify not expired
            now = datetime.now(UTC)
            if now < not_before:
                logger.warning(
                    "Certificate not yet valid",
                    extra={"cn": cn, "not_before": not_before.isoformat()},
                )
                return CertificateInfo(
                    valid=False,
                    cn=cn,
                    dn=dn,
                    fingerprint=fingerprint,
                    not_before=not_before,
                    not_after=not_after,
                    lifetime_days=lifetime_days,
                    error=f"Certificate not yet valid (not_before: {not_before.isoformat()})",
                )

            if now > not_after:
                logger.warning(
                    "Certificate expired", extra={"cn": cn, "not_after": not_after.isoformat()}
                )
                return CertificateInfo(
                    valid=False,
                    cn=cn,
                    dn=dn,
                    fingerprint=fingerprint,
                    not_before=not_before,
                    not_after=not_after,
                    lifetime_days=lifetime_days,
                    error=f"Certificate expired (not_after: {not_after.isoformat()})",
                )

            # Step 5: Warn if expiring soon
            time_until_expiry = not_after - now
            if time_until_expiry < self.expiry_warning_threshold:
                logger.warning(
                    "Certificate expiring soon",
                    extra={
                        "cn": cn,
                        "expires_in_hours": time_until_expiry.total_seconds() / 3600,
                    },
                )

            # Step 6: Check admin allowlist
            is_admin = cn in self.admin_cn_allowlist
            if not is_admin:
                validation_duration = (datetime.now(UTC) - validation_start).total_seconds()
                self._record_auth_failure(cn, "cn_not_allowed", validation_duration)
                logger.warning(
                    "Certificate CN not in admin allowlist",
                    extra={"cn": cn, "allowlist": self.admin_cn_allowlist},
                )
                return CertificateInfo(
                    valid=False,
                    cn=cn,
                    dn=dn,
                    fingerprint=fingerprint,
                    not_before=not_before,
                    not_after=not_after,
                    lifetime_days=lifetime_days,
                    error=f"CN '{cn}' not in admin allowlist",
                )

            # Step 7: Check CRL status (fail-secure if unavailable)
            crl_status = "unknown"
            try:
                is_revoked = await self.crl_cache.is_revoked(cert)
                if is_revoked:
                    validation_duration = (datetime.now(UTC) - validation_start).total_seconds()
                    self._record_auth_failure(cn, "revoked", validation_duration)
                    logger.error(
                        "Certificate revoked",
                        extra={"cn": cn, "fingerprint": fingerprint},
                    )
                    return CertificateInfo(
                        valid=False,
                        cn=cn,
                        dn=dn,
                        fingerprint=fingerprint,
                        not_before=not_before,
                        not_after=not_after,
                        lifetime_days=lifetime_days,
                        crl_status="revoked",
                        error="Certificate revoked (found in CRL)",
                    )
                crl_status = "valid"
            except Exception as e:
                # Fail-secure: Reject auth if CRL check fails
                validation_duration = (datetime.now(UTC) - validation_start).total_seconds()
                self._record_auth_failure(cn, "crl_error", validation_duration)
                logger.error(
                    "CRL check failed - rejecting auth",
                    extra={"cn": cn, "error": str(e)},
                )
                return CertificateInfo(
                    valid=False,
                    cn=cn,
                    dn=dn,
                    fingerprint=fingerprint,
                    not_before=not_before,
                    not_after=not_after,
                    lifetime_days=lifetime_days,
                    crl_status="error",
                    error=f"CRL check failed: {str(e)}",
                )

            # All checks passed
            validation_duration = (datetime.now(UTC) - validation_start).total_seconds()

            # Prometheus metrics: Record successful authentication (only for allowed CNs)
            mtls_auth_total.labels(cn=cn, result="success").inc()
            mtls_cert_validation_duration.labels(result="success").observe(validation_duration)
            # Only set expiry gauge for allowed CNs (cardinality protection)
            mtls_cert_not_after_timestamp.labels(cn=cn).set(not_after.timestamp())

            logger.info(
                "mTLS fallback authentication successful",
                extra={
                    "cn": cn,
                    "fingerprint": fingerprint,
                    "expires_in_hours": time_until_expiry.total_seconds() / 3600,
                    "crl_status": crl_status,
                },
            )

            return CertificateInfo(
                valid=True,
                cn=cn,
                dn=dn,
                fingerprint=fingerprint,
                not_before=not_before,
                not_after=not_after,
                lifetime_days=lifetime_days,
                is_admin=True,
                crl_status=crl_status,
            )

        except Exception as e:
            logger.error("Certificate validation exception", extra={"error": str(e)})
            return CertificateInfo(
                valid=False,
                cn="",
                dn="",
                fingerprint="",
                not_before=datetime.now(UTC),
                not_after=datetime.now(UTC),
                lifetime_days=0,
                error=f"Validation exception: {str(e)}",
            )

    def _extract_cn(self, cert: x509.Certificate) -> str:
        """Extract Common Name (CN) from certificate subject.

        Args:
            cert: X.509 certificate

        Returns:
            Common Name (CN) or empty string if not found
        """
        try:
            cn_oid = x509.oid.NameOID.COMMON_NAME
            cn_attrs = cert.subject.get_attributes_for_oid(cn_oid)
            if cn_attrs:
                value = cn_attrs[0].value
                # CN value may be str or bytes depending on encoding
                return value if isinstance(value, str) else value.decode("utf-8")
        except Exception as e:
            logger.warning("Failed to extract CN from certificate", extra={"error": str(e)})

        return ""


def is_fallback_enabled() -> bool:
    """Check if mTLS fallback is enabled via feature flag.

    Returns:
        True if ENABLE_MTLS_FALLBACK=true, False otherwise
    """
    return os.getenv("ENABLE_MTLS_FALLBACK", "false").lower() == "true"


def get_admin_cn_allowlist() -> list[str]:
    """Get admin CN allowlist from environment.

    Returns:
        List of allowed admin CNs (comma-separated from MTLS_ADMIN_CN_ALLOWLIST)
    """
    allowlist_str = os.getenv("MTLS_ADMIN_CN_ALLOWLIST", "")
    if not allowlist_str:
        logger.warning(
            "MTLS_ADMIN_CN_ALLOWLIST not configured - fallback will reject all certificates"
        )
        return []

    # Parse comma-separated list, strip whitespace
    return [cn.strip() for cn in allowlist_str.split(",") if cn.strip()]


def get_crl_url() -> str:
    """Get CRL distribution point URL from environment.

    Returns:
        CRL URL from MTLS_CRL_URL environment variable
    """
    crl_url = os.getenv(
        "MTLS_CRL_URL",
        "http://ca.trading-platform.local/crl/admin-ca.crl",  # Default for internal CA
    )
    return crl_url
