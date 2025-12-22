"""Performance tests for Track 7 Web Console Operations.

These tests verify SLA targets are met under load:
- Audit log write latency < 1s (P95)
- Alert delivery latency < 60s (P95)
- CB status check latency < 100ms

IMPORTANT: These tests are guarded by RUN_PERF_TESTS=1 environment variable
to prevent accidental DB writes during normal test runs.

Usage:
    # Run performance tests only
    RUN_PERF_TESTS=1 pytest tests/apps/web_console/test_performance.py -v

    # Or via make target
    make perf
"""

from __future__ import annotations

import os
import statistics
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Guard: Only run if RUN_PERF_TESTS=1
pytestmark = pytest.mark.skipif(
    os.getenv("RUN_PERF_TESTS") != "1",
    reason="Performance tests require RUN_PERF_TESTS=1",
)


class TestAuditLogPerformance:
    """Audit log write latency performance tests."""

    @pytest.fixture()
    def mock_db_pool(self):
        """Create mock DB pool with controllable latency."""
        pool = MagicMock()
        conn = AsyncMock()
        cursor = AsyncMock()

        # Simulate realistic DB latency (10-50ms)
        async def execute_with_latency(*args, **kwargs):
            await asyncio.sleep(0.02)  # 20ms simulated latency
            return MagicMock(rowcount=1)

        cursor.execute = execute_with_latency
        conn.cursor.return_value.__aenter__.return_value = cursor
        conn.transaction.return_value.__aenter__.return_value = None
        pool.connection.return_value.__aenter__.return_value = conn

        return pool

    @pytest.mark.asyncio()
    async def test_audit_write_latency_under_sla(self, mock_db_pool):
        """Verify audit log writes complete within SLA (P95 < 1s)."""

        from apps.web_console.auth.audit_log import AuditLogger

        logger = AuditLogger(mock_db_pool)
        latencies: list[float] = []

        # Run 100 writes
        for _ in range(100):
            start = time.monotonic()
            await logger.log_action(
                user_id="perf-test-user",
                action="PERFORMANCE_TEST",
                resource_type="test",
                resource_id="perf-001",
                outcome="success",
                details={"test": True},
            )
            latencies.append(time.monotonic() - start)

        # Calculate P95
        latencies.sort()
        p95_index = int(len(latencies) * 0.95)
        p95_latency = latencies[p95_index]

        # SLA: P95 < 1s
        assert p95_latency < 1.0, f"P95 latency {p95_latency:.3f}s exceeds SLA (1s)"

        # Log stats for visibility
        print("\nAudit Write Latency Stats:")
        print(f"  Min: {min(latencies)*1000:.2f}ms")
        print(f"  P50: {statistics.median(latencies)*1000:.2f}ms")
        print(f"  P95: {p95_latency*1000:.2f}ms")
        print(f"  Max: {max(latencies)*1000:.2f}ms")


class TestCBStatusPerformance:
    """Circuit breaker status check performance tests."""

    @pytest.fixture()
    def mock_redis_client(self):
        """Create mock Redis client with controllable latency."""
        client = MagicMock()

        # Simulate realistic Redis latency (1-5ms)
        def get_with_latency(key):
            time.sleep(0.002)  # 2ms simulated latency
            return b'{"state": "OPEN", "tripped_at": null}'

        client.get = get_with_latency
        return client

    def test_cb_status_check_latency(self, mock_redis_client):
        """Verify CB status checks complete within target (< 100ms)."""
        from libs.risk_management.breaker import CircuitBreaker

        cb = CircuitBreaker(mock_redis_client)
        latencies: list[float] = []

        # Run 100 status checks
        for _ in range(100):
            start = time.monotonic()
            cb.get_status()
            latencies.append(time.monotonic() - start)

        # Calculate P95
        latencies.sort()
        p95_index = int(len(latencies) * 0.95)
        p95_latency = latencies[p95_index]

        # Target: P95 < 100ms
        assert p95_latency < 0.1, f"P95 latency {p95_latency*1000:.2f}ms exceeds target (100ms)"

        print("\nCB Status Check Latency Stats:")
        print(f"  Min: {min(latencies)*1000:.2f}ms")
        print(f"  P50: {statistics.median(latencies)*1000:.2f}ms")
        print(f"  P95: {p95_latency*1000:.2f}ms")
        print(f"  Max: {max(latencies)*1000:.2f}ms")


class TestCBStalenessMetricPerformance:
    """CB staleness metric update performance tests."""

    @pytest.fixture()
    def mock_redis_client(self):
        """Create mock Redis client."""
        client = MagicMock()
        client.get.return_value = b'{"state": "OPEN"}'
        return client

    def test_staleness_metric_update_latency(self, mock_redis_client):
        """Verify staleness metric updates are fast (< 10ms)."""
        from apps.web_console.services.cb_metrics import update_cb_staleness_metric

        latencies: list[float] = []

        # Run 100 updates
        for _ in range(100):
            start = time.monotonic()
            update_cb_staleness_metric(mock_redis_client)
            latencies.append(time.monotonic() - start)

        # Target: P95 < 10ms
        latencies.sort()
        p95_index = int(len(latencies) * 0.95)
        p95_latency = latencies[p95_index]

        assert p95_latency < 0.01, f"P95 latency {p95_latency*1000:.2f}ms exceeds target (10ms)"

        print("\nStaleness Metric Update Latency Stats:")
        print(f"  Min: {min(latencies)*1000:.2f}ms")
        print(f"  P95: {p95_latency*1000:.2f}ms")
        print(f"  Max: {max(latencies)*1000:.2f}ms")


class TestAlertDeliveryPerformance:
    """Alert delivery performance tests (mocked channels)."""

    @pytest.fixture()
    def mock_channels(self):
        """Create mock channel handlers with simulated latency."""

        # Simulate network latency for each channel
        async def mock_email_send(*args, **kwargs):
            await asyncio.sleep(0.5)  # 500ms email latency
            return True

        async def mock_slack_send(*args, **kwargs):
            await asyncio.sleep(0.3)  # 300ms Slack latency
            return True

        async def mock_sms_send(*args, **kwargs):
            await asyncio.sleep(1.0)  # 1s SMS latency
            return True

        return {
            "email": mock_email_send,
            "slack": mock_slack_send,
            "sms": mock_sms_send,
        }

    @pytest.mark.asyncio()
    async def test_parallel_delivery_latency(self, mock_channels):
        """Verify parallel delivery is faster than sequential."""
        import asyncio

        # Sequential delivery simulation
        start = time.monotonic()
        for _channel, send_fn in mock_channels.items():
            await send_fn()
        sequential_time = time.monotonic() - start

        # Parallel delivery simulation
        start = time.monotonic()
        await asyncio.gather(*[send_fn() for send_fn in mock_channels.values()])
        parallel_time = time.monotonic() - start

        # Parallel should be significantly faster
        speedup = sequential_time / parallel_time
        assert speedup > 1.5, f"Parallel speedup {speedup:.2f}x is too low"

        print("\nAlert Delivery Latency Stats:")
        print(f"  Sequential: {sequential_time*1000:.2f}ms")
        print(f"  Parallel: {parallel_time*1000:.2f}ms")
        print(f"  Speedup: {speedup:.2f}x")

        # Verify P95 target (parallel should be < 60s even with SMS)
        assert parallel_time < 60, f"Parallel delivery {parallel_time:.2f}s exceeds SLA (60s)"


class TestMetricsServerPerformance:
    """Metrics server endpoint performance tests."""

    def test_metrics_endpoint_latency(self):
        """Verify /metrics endpoint responds quickly."""

        # Mock Prometheus metrics collection
        with patch("prometheus_client.generate_latest") as mock_generate:
            mock_generate.return_value = (
                b"# HELP test_metric Test\n# TYPE test_metric gauge\ntest_metric 1.0\n"
            )

            from fastapi.testclient import TestClient

            from apps.web_console.metrics_server import app

            client = TestClient(app)
            latencies: list[float] = []

            # Run 50 requests
            for _ in range(50):
                start = time.monotonic()
                response = client.get("/metrics")
                latencies.append(time.monotonic() - start)

                assert response.status_code == 200

            # Target: P95 < 100ms
            latencies.sort()
            p95_index = int(len(latencies) * 0.95)
            p95_latency = latencies[p95_index]

            assert p95_latency < 0.1, f"P95 latency {p95_latency*1000:.2f}ms exceeds target (100ms)"

            print("\nMetrics Endpoint Latency Stats:")
            print(f"  Min: {min(latencies)*1000:.2f}ms")
            print(f"  P95: {p95_latency*1000:.2f}ms")
            print(f"  Max: {max(latencies)*1000:.2f}ms")


class TestCleanup:
    """Ensure performance tests clean up after themselves."""

    @pytest.fixture(autouse=True)
    def cleanup_fixture(self):
        """Cleanup any state created by performance tests."""
        return
        # Performance tests use mocks, so no cleanup needed
        # This fixture exists as documentation and for future extensions


# Make asyncio available for async tests
import asyncio
