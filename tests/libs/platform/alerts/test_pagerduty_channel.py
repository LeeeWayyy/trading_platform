"""Unit tests for PagerDuty channel implementation.

Tests cover:
- Events API v2 payload format
- PII masking for routing keys
- Error handling (timeout, request error)
- DeliveryResult fields
- Metadata severity override
- JSON decode error handling
- Per-send httpx client lifecycle (no persistent client)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest

from libs.platform.alerts.channels.pagerduty import PagerDutyChannel


@pytest.fixture()
def channel() -> PagerDutyChannel:
    return PagerDutyChannel()


def _mock_client_post(mock_response: Mock) -> tuple[AsyncMock, AsyncMock]:
    """Create a mock httpx.AsyncClient context manager that returns mock_response on post."""
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_cm = AsyncMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_client)
    mock_cm.__aexit__ = AsyncMock(return_value=False)
    return mock_cm, mock_client


class TestPagerDutyChannelType:
    def test_channel_type(self, channel: PagerDutyChannel) -> None:
        assert channel.channel_type == "pagerduty"

    def test_events_api_url(self, channel: PagerDutyChannel) -> None:
        assert channel.EVENTS_API_URL == "https://events.pagerduty.com/v2/enqueue"


class TestPagerDutySend:
    @pytest.mark.asyncio()
    async def test_successful_delivery(self, channel: PagerDutyChannel) -> None:
        mock_response = Mock()
        mock_response.is_success = True
        mock_response.status_code = 202
        mock_response.headers = {"content-type": "application/json"}
        mock_response.json.return_value = {"dedup_key": "abc123"}

        mock_cm, mock_client = _mock_client_post(mock_response)

        with patch(
            "libs.platform.alerts.channels.pagerduty.httpx.AsyncClient", return_value=mock_cm
        ):
            result = await channel.send(
                recipient="routing-key-12345",
                subject="Test Alert",
                body="Something happened",
            )

        assert result.success is True
        assert result.message_id == "abc123"
        assert result.error is None

        # Verify payload format
        call_args = mock_client.post.call_args
        payload = call_args.kwargs["json"]
        assert payload["routing_key"] == "routing-key-12345"
        assert payload["event_action"] == "trigger"
        assert payload["payload"]["summary"] == "Test Alert"
        assert payload["payload"]["source"] == "trading-platform"
        assert payload["payload"]["severity"] == "warning"  # default
        assert payload["payload"]["custom_details"]["description"] == "Something happened"

    @pytest.mark.asyncio()
    async def test_severity_override_via_metadata(self, channel: PagerDutyChannel) -> None:
        mock_response = Mock()
        mock_response.is_success = True
        mock_response.status_code = 202
        mock_response.headers = {"content-type": "application/json"}
        mock_response.json.return_value = {"dedup_key": "abc123"}

        mock_cm, mock_client = _mock_client_post(mock_response)

        with patch(
            "libs.platform.alerts.channels.pagerduty.httpx.AsyncClient", return_value=mock_cm
        ):
            await channel.send(
                recipient="key",
                subject="Critical",
                body="Down",
                metadata={"severity": "critical", "rule_name": "test"},
            )

        payload = mock_client.post.call_args.kwargs["json"]
        assert payload["payload"]["severity"] == "critical"
        assert payload["payload"]["custom_details"]["rule_name"] == "test"
        # severity should NOT be in custom_details (popped)
        assert "severity" not in payload["payload"]["custom_details"]

    @pytest.mark.asyncio()
    async def test_timeout_returns_retryable(self, channel: PagerDutyChannel) -> None:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cm.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "libs.platform.alerts.channels.pagerduty.httpx.AsyncClient", return_value=mock_cm
        ):
            result = await channel.send("key", "subject", "body")

        assert result.success is False
        assert result.error == "timeout"
        assert result.retryable is True

    @pytest.mark.asyncio()
    async def test_request_error_returns_retryable(self, channel: PagerDutyChannel) -> None:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.RequestError("connection failed"))
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cm.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "libs.platform.alerts.channels.pagerduty.httpx.AsyncClient", return_value=mock_cm
        ):
            result = await channel.send("my-routing-key", "subject", "body")

        assert result.success is False
        assert result.retryable is True
        # Routing key should be masked in error
        assert "my-routing-key" not in (result.error or "")

    @pytest.mark.asyncio()
    async def test_api_error_retryable_for_500(self, channel: PagerDutyChannel) -> None:
        mock_response = Mock()
        mock_response.is_success = False
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        mock_response.headers = {}

        mock_cm, mock_client = _mock_client_post(mock_response)

        with patch(
            "libs.platform.alerts.channels.pagerduty.httpx.AsyncClient", return_value=mock_cm
        ):
            result = await channel.send("key", "subject", "body")

        assert result.success is False
        assert result.retryable is True

    @pytest.mark.asyncio()
    async def test_api_error_not_retryable_for_400(self, channel: PagerDutyChannel) -> None:
        mock_response = Mock()
        mock_response.is_success = False
        mock_response.status_code = 400
        mock_response.text = "Bad Request"
        mock_response.headers = {}

        mock_cm, mock_client = _mock_client_post(mock_response)

        with patch(
            "libs.platform.alerts.channels.pagerduty.httpx.AsyncClient", return_value=mock_cm
        ):
            result = await channel.send("key", "subject", "body")

        assert result.success is False
        assert result.retryable is False

    @pytest.mark.asyncio()
    async def test_rate_limit_retryable(self, channel: PagerDutyChannel) -> None:
        mock_response = Mock()
        mock_response.is_success = False
        mock_response.status_code = 429
        mock_response.text = "Rate limited"
        mock_response.headers = {}

        mock_cm, mock_client = _mock_client_post(mock_response)

        with patch(
            "libs.platform.alerts.channels.pagerduty.httpx.AsyncClient", return_value=mock_cm
        ):
            result = await channel.send("key", "subject", "body")

        assert result.retryable is True

    @pytest.mark.asyncio()
    async def test_error_response_masks_routing_key(self, channel: PagerDutyChannel) -> None:
        """Routing key echoed in error response body must be masked."""
        routing_key = "my-secret-routing-key-12345"
        mock_response = Mock()
        mock_response.is_success = False
        mock_response.status_code = 400
        mock_response.text = f"Invalid routing key: {routing_key}"
        mock_response.headers = {}

        mock_cm, mock_client = _mock_client_post(mock_response)

        with patch(
            "libs.platform.alerts.channels.pagerduty.httpx.AsyncClient", return_value=mock_cm
        ):
            result = await channel.send(routing_key, "subject", "body")

        assert result.success is False
        assert routing_key not in (result.error or "")

    @pytest.mark.asyncio()
    async def test_json_decode_error_returns_success_without_message_id(
        self, channel: PagerDutyChannel
    ) -> None:
        """Malformed JSON in response body should not crash; message_id is None."""
        mock_response = Mock()
        mock_response.is_success = True
        mock_response.status_code = 202
        mock_response.headers = {"content-type": "application/json"}
        mock_response.json.side_effect = ValueError("malformed JSON")
        mock_response.text = "not json"

        mock_cm, mock_client = _mock_client_post(mock_response)

        with patch(
            "libs.platform.alerts.channels.pagerduty.httpx.AsyncClient", return_value=mock_cm
        ):
            result = await channel.send("key", "subject", "body")

        assert result.success is True
        assert result.message_id is None
        assert result.error is None


class TestPagerDutyPIIMasking:
    def test_routing_key_masked(self) -> None:
        """PagerDuty routing keys use last-4-chars convention."""
        from libs.platform.alerts.pii import mask_recipient

        masked = mask_recipient("abcdef1234567890", "pagerduty")
        assert masked == "***7890"

    def test_short_routing_key_masked(self) -> None:
        from libs.platform.alerts.pii import mask_recipient

        masked = mask_recipient("ab", "pagerduty")
        assert masked == "***"
