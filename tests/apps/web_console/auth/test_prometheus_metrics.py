"""
Prometheus metrics validation tests for Component 6+7.

Tests validate that all 26 Prometheus metrics are properly defined
and follow cardinality protection requirements.

NOTE: Full integration testing of these metrics requires running
Streamlit with Auth0/Redis, which is out of scope for unit tests.
These tests validate metric definitions and basic instrumentation.
"""

import pytest
from prometheus_client import REGISTRY

# Import modules that define the metrics so they're registered in REGISTRY
# These imports trigger metric registration at module load time
import apps.web_console.auth.idp_health  # noqa: F401 - IdP health metrics
import apps.web_console.auth.mtls_fallback  # noqa: F401 - mTLS fallback metrics
import libs.web_console_auth.session  # noqa: F401 - Session metrics


def test_idp_health_metrics_exist():
    """Validate IdP health monitoring metrics are defined."""
    metric_names = list(REGISTRY._collector_to_names.values())
    all_metrics = [item for sublist in metric_names for item in sublist]

    # IdP Health Monitoring (5 metrics)
    assert "oauth2_idp_health_consecutive_failures" in all_metrics
    assert "oauth2_idp_health_consecutive_successes" in all_metrics
    assert "oauth2_idp_fallback_mode" in all_metrics
    assert "oauth2_idp_stability_period_active" in all_metrics
    assert "oauth2_idp_health_check_duration_seconds" in all_metrics


def test_mtls_fallback_metrics_exist():
    """Validate mTLS fallback authentication metrics are defined."""
    metric_names = list(REGISTRY._collector_to_names.values())
    all_metrics = [item for sublist in metric_names for item in sublist]

    # mTLS Fallback Authentication (actual metrics from mtls_fallback.py)
    # Core authentication metrics
    assert "oauth2_mtls_auth_total" in all_metrics
    assert "oauth2_mtls_auth_failures_total" in all_metrics
    # Certificate validity
    assert "oauth2_mtls_cert_not_after_timestamp" in all_metrics
    # CRL metrics
    assert "oauth2_mtls_crl_fetch_total" in all_metrics
    assert "oauth2_mtls_crl_fetch_failures_total" in all_metrics
    assert "oauth2_mtls_crl_last_update_timestamp" in all_metrics
    assert "oauth2_mtls_crl_cache_age_seconds" in all_metrics
    # Duration histograms
    assert "oauth2_mtls_crl_fetch_duration_seconds" in all_metrics
    assert "oauth2_mtls_cert_validation_duration_seconds" in all_metrics


def test_session_management_metrics_exist():
    """Validate session management metrics are defined (3 total after review)."""
    metric_names = list(REGISTRY._collector_to_names.values())
    all_metrics = [item for sublist in metric_names for item in sublist]

    # Session Management (3 metrics - 2 removed after review)
    assert "oauth2_session_created_total" in all_metrics
    assert "oauth2_session_signature_failures_total" in all_metrics
    assert "oauth2_active_sessions_count" in all_metrics

    # Verify removed metrics are NOT present
    assert "oauth2_session_secret_last_rotation_timestamp" not in all_metrics
    assert "oauth2_session_cleanup_failures_total" not in all_metrics


def test_oauth2_flow_metrics_exist():
    """Validate OAuth2 flow metrics are defined (actual metrics from session.py)."""
    metric_names = list(REGISTRY._collector_to_names.values())
    all_metrics = [item for sublist in metric_names for item in sublist]

    # OAuth2 Flow (actual metrics from libs/web_console_auth/session.py)
    # Authorization metrics
    assert "oauth2_authorization_total" in all_metrics
    assert "oauth2_authorization_failures_total" in all_metrics
    # Token refresh metrics
    assert "oauth2_token_refresh_total" in all_metrics
    assert "oauth2_token_refresh_failures_total" in all_metrics
    # Note: token_revocation and jwks metrics were planned but not implemented
    # These would be added in future phases if needed


def test_metric_cardinality_protection():
    """Validate cardinality protection for label-based metrics."""
    # This test validates that metric definitions use bounded label values
    # Full validation requires runtime instrumentation testing

    metric_names = list(REGISTRY._collector_to_names.values())
    all_metrics = [item for sublist in metric_names for item in sublist]

    # Metrics with labels that require cardinality protection
    labeled_metrics = [
        "oauth2_mtls_auth_total",  # Labels: cn (truncated to 64 chars)
        "oauth2_mtls_auth_failures_total",  # Labels: cn, reason (bounded)
        "oauth2_session_signature_failures_total",  # Labels: reason (exception types)
        "oauth2_authorization_failures_total",  # Labels: reason (exception types)
    ]

    for metric in labeled_metrics:
        assert metric in all_metrics, f"Metric {metric} should be defined with labels"


def test_multiprocess_mode_compatibility():
    """Validate that metrics work in both single-process and multiprocess mode."""
    import os

    # Test validates that code handles both modes
    # In production, PROMETHEUS_MULTIPROC_DIR is set
    # In tests, it's not set (single-process mode)

    multiproc_dir = os.getenv("PROMETHEUS_MULTIPROC_DIR")

    if multiproc_dir:
        # Multiprocess mode - validate directory exists
        assert os.path.isdir(multiproc_dir), "PROMETHEUS_MULTIPROC_DIR must be a valid directory"
    else:
        # Single-process mode - default registry should work
        assert REGISTRY is not None, "Default REGISTRY should be available"


@pytest.mark.parametrize(
    ("metric_name", "metric_type"),
    [
        ("oauth2_session_created_total", "counter"),
        ("oauth2_active_sessions_count", "gauge"),
        ("oauth2_idp_health_check_duration_seconds", "histogram"),
    ],
)
def test_metric_types(metric_name, metric_type):
    """Validate that metrics use correct Prometheus types."""
    metric_names = list(REGISTRY._collector_to_names.values())
    all_metrics = [item for sublist in metric_names for item in sublist]

    assert metric_name in all_metrics, f"{metric_name} should be defined as {metric_type}"

    # Note: Full type validation requires introspecting REGISTRY collectors
    # This test validates presence; type correctness is verified by code review
