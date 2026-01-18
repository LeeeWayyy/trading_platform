"""Additional tests for EmailChannel."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import aiosmtplib
import pytest

from libs.platform.alerts.channels.email import EmailChannel
from libs.platform.alerts.models import DeliveryResult


@pytest.fixture()
def email_channel() -> EmailChannel:
    mock_secrets = MagicMock()
    mock_secrets.get_secret.return_value = None
    return EmailChannel(
        secret_manager=mock_secrets,
        smtp_host="smtp.test.com",
        smtp_port=587,
        smtp_user="user@test.com",
        smtp_password="password",
        from_email="alerts@test.com",
        sendgrid_api_key="sg-test-key",
    )


def _smtp_context(mock_smtp_class: MagicMock, mock_smtp: AsyncMock) -> None:
    mock_smtp.__aenter__ = AsyncMock(return_value=mock_smtp)
    mock_smtp.__aexit__ = AsyncMock(return_value=None)
    mock_smtp_class.return_value = mock_smtp


def test_init_from_email_falls_back_to_secret() -> None:
    mock_secrets = MagicMock()
    mock_secrets.get_secret.side_effect = [
        "smtp.test.com",  # SMTP_HOST
        "2525",  # SMTP_PORT
        "smtp-user@test.com",  # SMTP_USER
        "smtp-pass",  # SMTP_PASSWORD
        "sg-key",  # SENDGRID_API_KEY
        "alerts@test.com",  # ALERTS_FROM_EMAIL
    ]

    channel = EmailChannel(secret_manager=mock_secrets)

    assert channel.smtp_port == 2525
    assert channel.from_email == "alerts@test.com"


@pytest.mark.asyncio()
async def test_send_smtp_skips_login_when_missing_creds(email_channel: EmailChannel) -> None:
    email_channel.smtp_user = None
    email_channel.smtp_password = None

    with patch.object(aiosmtplib, "SMTP") as mock_smtp_class:
        mock_smtp = AsyncMock()
        mock_smtp.send_message = AsyncMock()
        _smtp_context(mock_smtp_class, mock_smtp)

        result = await email_channel._send_smtp("user@example.com", "Subject", "Body")

        assert result.success is True
        mock_smtp.login.assert_not_called()


@pytest.mark.asyncio()
async def test_send_sendgrid_uses_expected_headers_and_payload(
    email_channel: EmailChannel,
) -> None:
    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 202
        mock_response.headers = {}
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        result = await email_channel._send_sendgrid("user@example.com", "Subject", "Body")

        assert result.success is True
        _, kwargs = mock_client.post.call_args
        assert kwargs["headers"]["Authorization"] == "Bearer sg-test-key"
        payload = kwargs["json"]
        assert payload["from"]["email"] == "alerts@test.com"
        assert payload["personalizations"][0]["to"][0]["email"] == "user@example.com"


@pytest.mark.asyncio()
async def test_send_skips_sendgrid_on_smtp_success(email_channel: EmailChannel) -> None:
    smtp_result = DeliveryResult(success=True, message_id="smtp-123")

    with patch.object(
        email_channel, "_send_smtp", AsyncMock(return_value=smtp_result)
    ) as mock_smtp:
        with patch.object(email_channel, "_send_sendgrid", AsyncMock()) as mock_sendgrid:
            result = await email_channel.send("user@example.com", "Subject", "Body")

            assert result.success is True
            assert result.message_id == "smtp-123"
            mock_smtp.assert_awaited_once()
            mock_sendgrid.assert_not_awaited()


@pytest.mark.asyncio()
async def test_send_passes_attachments_to_smtp_only(email_channel: EmailChannel) -> None:
    smtp_result = DeliveryResult(success=False, error="smtp failed", retryable=True)
    sendgrid_result = DeliveryResult(success=True, message_id="sg-123")

    with patch.object(
        email_channel, "_send_smtp", AsyncMock(return_value=smtp_result)
    ) as mock_smtp:
        with patch.object(
            email_channel, "_send_sendgrid", AsyncMock(return_value=sendgrid_result)
        ) as mock_sendgrid:
            result = await email_channel.send(
                "user@example.com",
                "Subject",
                "Body",
                attachments=["/tmp/report.csv"],
            )

            assert result.success is True
            mock_smtp.assert_awaited_once()
            _, kwargs = mock_smtp.call_args
            assert kwargs["attachments"] == ["/tmp/report.csv"]
            mock_sendgrid.assert_awaited_once()
