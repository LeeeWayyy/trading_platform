"""Tests for SlackChannel delivery behavior."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from libs.platform.alerts.channels.slack import SlackChannel


@pytest.fixture()
def webhook_url():
    return "https://hooks.slack.com/services/AAA/BBB/CCCC"


def _build_channel_with_secret(webhook: str | None) -> SlackChannel:
    mock_secrets = MagicMock()
    mock_secrets.get_secret.return_value = webhook
    return SlackChannel(secret_manager=mock_secrets)


def test_init_uses_secret_manager_default(webhook_url):
    channel = _build_channel_with_secret(webhook_url)

    assert channel.default_webhook_url == webhook_url


@pytest.mark.asyncio()
async def test_send_returns_error_when_webhook_missing():
    channel = _build_channel_with_secret(None)

    result = await channel.send(recipient="", subject="Test", body="Body")

    assert result.success is False
    assert result.retryable is False
    assert result.error == "Slack webhook not configured"


@pytest.mark.asyncio()
async def test_send_uses_recipient_over_default(webhook_url):
    channel = _build_channel_with_secret("https://hooks.slack.com/services/DEFAULT")
    recipient = webhook_url

    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {}
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        result = await channel.send(
            recipient=recipient,
            subject="Subject",
            body="Body",
        )

    assert result.success is True
    mock_client.post.assert_awaited_once_with(
        recipient,
        json={"text": "*Subject*\nBody"},
    )


@pytest.mark.asyncio()
async def test_send_request_error_masks_webhook(webhook_url):
    channel = _build_channel_with_secret(webhook_url)
    error_message = f"Connection failed: {webhook_url}"

    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(
            side_effect=httpx.RequestError(error_message, request=MagicMock())
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        result = await channel.send(
            recipient="",
            subject="Subject",
            body="Body",
        )

    assert result.success is False
    assert result.retryable is True
    assert "***" in result.error
    assert webhook_url not in result.error


@pytest.mark.asyncio()
async def test_send_timeout_is_retryable(webhook_url):
    channel = _build_channel_with_secret(webhook_url)

    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("Timeout"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        result = await channel.send(
            recipient="",
            subject="Subject",
            body="Body",
        )

    assert result.success is False
    assert result.retryable is True
    assert result.error == "timeout"


@pytest.mark.asyncio()
@pytest.mark.parametrize(
    "status_code,expected_retryable",
    [
        (500, True),
        (429, True),
        (400, False),
    ],
)
async def test_send_non_200_status_retryable(status_code, expected_retryable, webhook_url):
    channel = _build_channel_with_secret(webhook_url)

    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = status_code
        mock_response.headers = {"retry-after": "25"}
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        result = await channel.send(
            recipient="",
            subject="Subject",
            body="Body",
        )

    assert result.success is False
    assert result.retryable is expected_retryable
    assert result.metadata.get("retry_after") == "25"
