"""Unit tests for LatencyMonitor class."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from apps.web_console_ng.core.latency_monitor import (
    LATENCY_GREEN_MAX,
    LATENCY_ORANGE_MAX,
    LatencyMonitor,
    LatencyStatus,
)


class TestLatencyStatus:
    """Tests for LatencyStatus enum."""

    def test_status_values(self) -> None:
        assert LatencyStatus.GOOD.value == "good"
        assert LatencyStatus.DEGRADED.value == "degraded"
        assert LatencyStatus.POOR.value == "poor"
        assert LatencyStatus.DISCONNECTED.value == "disconnected"


class TestLatencyMonitorInit:
    """Tests for LatencyMonitor initialization."""

    def test_initial_state(self) -> None:
        monitor = LatencyMonitor()
        assert monitor.get_current_latency() is None
        assert monitor.get_rolling_average() is None
        assert monitor.get_latency_status() == LatencyStatus.DISCONNECTED
        assert len(monitor.get_history()) == 0


class TestGetLatencyStatus:
    """Tests for get_latency_status method."""

    def test_disconnected_when_no_latency(self) -> None:
        monitor = LatencyMonitor()
        assert monitor.get_latency_status() == LatencyStatus.DISCONNECTED

    def test_good_status_under_threshold(self) -> None:
        monitor = LatencyMonitor()
        monitor._current_latency = 50.0  # < 100ms
        assert monitor.get_latency_status() == LatencyStatus.GOOD

    def test_good_status_at_boundary(self) -> None:
        monitor = LatencyMonitor()
        monitor._current_latency = LATENCY_GREEN_MAX - 0.1  # Just under 100ms
        assert monitor.get_latency_status() == LatencyStatus.GOOD

    def test_degraded_status_at_threshold(self) -> None:
        monitor = LatencyMonitor()
        monitor._current_latency = LATENCY_GREEN_MAX  # Exactly 100ms
        assert monitor.get_latency_status() == LatencyStatus.DEGRADED

    def test_degraded_status_in_range(self) -> None:
        monitor = LatencyMonitor()
        monitor._current_latency = 200.0  # 100-300ms
        assert monitor.get_latency_status() == LatencyStatus.DEGRADED

    def test_poor_status_at_threshold(self) -> None:
        monitor = LatencyMonitor()
        monitor._current_latency = LATENCY_ORANGE_MAX  # Exactly 300ms
        assert monitor.get_latency_status() == LatencyStatus.POOR

    def test_poor_status_above_threshold(self) -> None:
        monitor = LatencyMonitor()
        monitor._current_latency = 500.0  # > 300ms
        assert monitor.get_latency_status() == LatencyStatus.POOR

    def test_disconnected_after_consecutive_failures(self) -> None:
        monitor = LatencyMonitor()
        monitor._current_latency = 50.0  # Has a latency value
        monitor._consecutive_failures = 3  # But 3+ failures
        assert monitor.get_latency_status() == LatencyStatus.DISCONNECTED


class TestGetStatusColorClass:
    """Tests for get_status_color_class method."""

    def test_good_color(self) -> None:
        monitor = LatencyMonitor()
        monitor._current_latency = 50.0
        assert "green" in monitor.get_status_color_class().lower()

    def test_degraded_color(self) -> None:
        monitor = LatencyMonitor()
        monitor._current_latency = 200.0
        assert "orange" in monitor.get_status_color_class().lower()

    def test_poor_color(self) -> None:
        monitor = LatencyMonitor()
        monitor._current_latency = 500.0
        assert "red" in monitor.get_status_color_class().lower()

    def test_disconnected_color(self) -> None:
        monitor = LatencyMonitor()
        # No latency set
        assert "gray" in monitor.get_status_color_class().lower()


class TestRollingAverage:
    """Tests for rolling average calculation."""

    def test_single_measurement(self) -> None:
        monitor = LatencyMonitor()
        monitor._measurements.append(100.0)
        assert monitor.get_rolling_average() == 100.0

    def test_multiple_measurements(self) -> None:
        monitor = LatencyMonitor()
        monitor._measurements.extend([100.0, 200.0, 300.0])
        assert monitor.get_rolling_average() == 200.0  # (100+200+300)/3

    def test_rolling_window_size(self) -> None:
        monitor = LatencyMonitor()
        # Add 15 measurements (window is 10)
        for i in range(15):
            monitor._measurements.append(float(i * 10))
        # Should only have last 10 measurements (50-140)
        assert len(monitor._measurements) == 10
        expected_avg = sum(range(5, 15)) * 10 / 10  # 50+60+...+140 / 10 = 95
        assert monitor.get_rolling_average() == expected_avg


class TestFormatDisplay:
    """Tests for format_display method."""

    def test_format_with_latency(self) -> None:
        monitor = LatencyMonitor()
        monitor._current_latency = 24.7
        assert monitor.format_display() == "24ms"

    def test_format_disconnected(self) -> None:
        monitor = LatencyMonitor()
        assert monitor.format_display() == "--"


class TestFormatTooltip:
    """Tests for format_tooltip method."""

    def test_tooltip_with_average(self) -> None:
        monitor = LatencyMonitor()
        monitor._current_latency = 24.0
        monitor._measurements.extend([24.0, 28.0, 32.0])
        tooltip = monitor.format_tooltip()
        assert "24ms" in tooltip
        assert "28ms" in tooltip  # Average is 28
        assert "avg" in tooltip.lower()

    def test_tooltip_without_average(self) -> None:
        monitor = LatencyMonitor()
        monitor._current_latency = 24.0
        tooltip = monitor.format_tooltip()
        assert "24ms" in tooltip
        assert "avg" not in tooltip.lower()

    def test_tooltip_disconnected(self) -> None:
        monitor = LatencyMonitor()
        tooltip = monitor.format_tooltip()
        assert "--" in tooltip


class TestMeasure:
    """Tests for measure method."""

    @pytest.mark.asyncio()
    async def test_successful_measurement(self) -> None:
        monitor = LatencyMonitor()

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()

        mock_http_client = AsyncMock()
        mock_http_client.get = AsyncMock(return_value=mock_response)
        mock_http_client.is_closed = False

        with patch.object(monitor, "_get_http_client", return_value=mock_http_client):
            latency = await monitor.measure()

        assert latency is not None
        assert latency > 0
        assert monitor.get_current_latency() is not None
        assert len(monitor._measurements) == 1
        assert len(monitor.get_history()) == 1
        assert monitor._consecutive_failures == 0

    @pytest.mark.asyncio()
    async def test_timeout_handling(self) -> None:
        monitor = LatencyMonitor()
        monitor._current_latency = 50.0  # Had a previous successful measurement

        mock_http_client = AsyncMock()
        mock_http_client.get = AsyncMock(side_effect=httpx.TimeoutException(""))
        mock_http_client.is_closed = False

        with patch.object(monitor, "_get_http_client", return_value=mock_http_client):
            latency = await monitor.measure()

        assert latency is None
        assert monitor._consecutive_failures == 1
        # Latency should still be retained (not cleared until 3 failures)
        assert monitor._current_latency == 50.0

    @pytest.mark.asyncio()
    async def test_three_failures_clears_latency(self) -> None:
        monitor = LatencyMonitor()
        monitor._current_latency = 50.0

        mock_http_client = AsyncMock()
        mock_http_client.get = AsyncMock(side_effect=httpx.TimeoutException(""))
        mock_http_client.is_closed = False

        with patch.object(monitor, "_get_http_client", return_value=mock_http_client):
            # Three consecutive failures
            await monitor.measure()
            await monitor.measure()
            await monitor.measure()

        assert monitor._consecutive_failures == 3
        assert monitor._current_latency is None
        assert monitor.get_latency_status() == LatencyStatus.DISCONNECTED

    @pytest.mark.asyncio()
    async def test_http_error_handling(self) -> None:
        monitor = LatencyMonitor()

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError(
                "", request=MagicMock(), response=mock_response
            )
        )

        mock_http_client = AsyncMock()
        mock_http_client.get = AsyncMock(return_value=mock_response)
        mock_http_client.is_closed = False

        with patch.object(monitor, "_get_http_client", return_value=mock_http_client):
            latency = await monitor.measure()

        assert latency is None
        assert monitor._consecutive_failures == 1

    @pytest.mark.asyncio()
    async def test_successful_measurement_resets_failures(self) -> None:
        monitor = LatencyMonitor()
        monitor._consecutive_failures = 2  # Had some failures

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()

        mock_http_client = AsyncMock()
        mock_http_client.get = AsyncMock(return_value=mock_response)
        mock_http_client.is_closed = False

        with patch.object(monitor, "_get_http_client", return_value=mock_http_client):
            await monitor.measure()

        assert monitor._consecutive_failures == 0


class TestHttpClientManagement:
    """Tests for HTTP client lifecycle management."""

    def test_get_http_client_creates_client(self) -> None:
        monitor = LatencyMonitor()
        assert monitor._http_client is None

        client = monitor._get_http_client()

        assert client is not None
        assert monitor._http_client is client

    def test_get_http_client_reuses_existing(self) -> None:
        monitor = LatencyMonitor()
        client1 = monitor._get_http_client()
        client2 = monitor._get_http_client()

        assert client1 is client2

    def test_get_http_client_recreates_after_close(self) -> None:
        monitor = LatencyMonitor()
        client1 = monitor._get_http_client()

        # Simulate client being closed
        monitor._http_client = MagicMock()
        monitor._http_client.is_closed = True

        client2 = monitor._get_http_client()

        assert client2 is not client1

    @pytest.mark.asyncio()
    async def test_close_closes_client(self) -> None:
        monitor = LatencyMonitor()
        mock_client = AsyncMock()
        mock_client.is_closed = False
        monitor._http_client = mock_client

        await monitor.close()

        mock_client.aclose.assert_called_once()
        assert monitor._http_client is None

    @pytest.mark.asyncio()
    async def test_close_noop_when_no_client(self) -> None:
        monitor = LatencyMonitor()
        assert monitor._http_client is None

        # Should not raise
        await monitor.close()

        assert monitor._http_client is None

    @pytest.mark.asyncio()
    async def test_close_noop_when_already_closed(self) -> None:
        monitor = LatencyMonitor()
        mock_client = MagicMock()
        mock_client.is_closed = True
        monitor._http_client = mock_client

        await monitor.close()

        mock_client.aclose.assert_not_called()


class TestHistory:
    """Tests for history tracking."""

    def test_history_contains_timestamp_and_latency(self) -> None:
        monitor = LatencyMonitor()
        monitor._history.append((1000.0, 50.0))
        history = monitor.get_history()
        assert len(history) == 1
        timestamp, latency = history[0]
        assert timestamp == 1000.0
        assert latency == 50.0

    def test_history_size_limit(self) -> None:
        monitor = LatencyMonitor()
        # Add 150 entries (limit is 100)
        for i in range(150):
            monitor._history.append((float(i), float(i * 10)))

        history = monitor.get_history()
        assert len(history) == 100
        # Should have entries 50-149
        assert history[0] == (50.0, 500.0)
        assert history[-1] == (149.0, 1490.0)
