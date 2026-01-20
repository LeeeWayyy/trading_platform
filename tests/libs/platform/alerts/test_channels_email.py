"""Tests for EmailChannel."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import aiosmtplib
import httpx
import pytest

from libs.platform.alerts.channels.email import EmailChannel
from libs.platform.alerts.models import DeliveryResult


@pytest.fixture()
def email_channel():
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


def test_invalid_smtp_port_defaults_to_587() -> None:
    mock_secrets = MagicMock()
    mock_secrets.get_secret.return_value = None

    channel = EmailChannel(
        secret_manager=mock_secrets,
        smtp_host="smtp.test.com",
        smtp_port="not-a-number",
        smtp_user="user@test.com",
        smtp_password="password",
        from_email="alerts@test.com",
        sendgrid_api_key=None,
    )

    assert channel.smtp_port == 587


def test_sanitize_error_masks_addresses(email_channel: EmailChannel) -> None:
    raw = "failed for user@example.com from alerts@test.com"
    sanitized = email_channel._sanitize_error(raw, "user@example.com")

    assert "user@example.com" not in sanitized
    assert "alerts@test.com" not in sanitized
    assert "***" in sanitized


def test_build_message_with_attachments(tmp_path: Path, email_channel: EmailChannel) -> None:
    attachment = tmp_path / "report.txt"
    attachment.write_text("data")
    missing = tmp_path / "missing.txt"

    message = email_channel._build_message(
        recipient="user@example.com",
        subject="Subject",
        body="Body",
        attachments=[str(attachment), str(missing)],
    )

    attachments = list(message.iter_attachments())
    assert len(attachments) == 1
    assert attachments[0].get_filename() == "report.txt"


@pytest.mark.asyncio()
async def test_send_returns_error_when_from_email_missing() -> None:
    mock_secrets = MagicMock()
    mock_secrets.get_secret.return_value = None

    channel = EmailChannel(
        secret_manager=mock_secrets,
        smtp_host="smtp.test.com",
        smtp_port=587,
        smtp_user=None,
        smtp_password=None,
        from_email=None,
        sendgrid_api_key="sg-test-key",
    )

    result = await channel.send("user@example.com", "Subject", "Body")
    assert result.success is False
    assert result.retryable is False
    assert result.error == "from_email not configured"


@pytest.mark.asyncio()
async def test_send_smtp_success_587_uses_starttls(email_channel: EmailChannel) -> None:
    with patch.object(aiosmtplib, "SMTP") as mock_smtp_class:
        mock_smtp = AsyncMock()
        mock_smtp.send_message = AsyncMock()
        _smtp_context(mock_smtp_class, mock_smtp)

        result = await email_channel._send_smtp("user@example.com", "Subject", "Body")

        assert result.success is True
        assert result.message_id is not None
        _, kwargs = mock_smtp_class.call_args
        assert kwargs["use_tls"] is False
        assert kwargs["start_tls"] is True


@pytest.mark.asyncio()
async def test_send_smtp_success_465_uses_tls(email_channel: EmailChannel) -> None:
    email_channel.smtp_port = 465

    with patch.object(aiosmtplib, "SMTP") as mock_smtp_class:
        mock_smtp = AsyncMock()
        mock_smtp.send_message = AsyncMock()
        _smtp_context(mock_smtp_class, mock_smtp)

        result = await email_channel._send_smtp("user@example.com", "Subject", "Body")

        assert result.success is True
        _, kwargs = mock_smtp_class.call_args
        assert kwargs["use_tls"] is True
        assert kwargs["start_tls"] is False


@pytest.mark.asyncio()
async def test_send_smtp_auth_error_not_retryable(email_channel: EmailChannel) -> None:
    with patch.object(aiosmtplib, "SMTP") as mock_smtp_class:
        mock_smtp = AsyncMock()
        mock_smtp.login = AsyncMock(
            side_effect=aiosmtplib.SMTPAuthenticationError(535, "Auth failed")
        )
        _smtp_context(mock_smtp_class, mock_smtp)

        result = await email_channel._send_smtp("user@example.com", "Subject", "Body")

        assert result.success is False
        assert result.retryable is False
        assert "user@example.com" not in (result.error or "")
        assert "alerts@test.com" not in (result.error or "")


@pytest.mark.asyncio()
async def test_send_smtp_connection_error_retryable(email_channel: EmailChannel) -> None:
    with patch.object(aiosmtplib, "SMTP") as mock_smtp_class:
        mock_smtp = AsyncMock()
        mock_smtp.__aenter__ = AsyncMock(
            side_effect=aiosmtplib.SMTPConnectError("Connection failed")
        )
        mock_smtp.__aexit__ = AsyncMock(return_value=None)
        mock_smtp_class.return_value = mock_smtp

        result = await email_channel._send_smtp("user@example.com", "Subject", "Body")

        assert result.success is False
        assert result.retryable is True


@pytest.mark.asyncio()
async def test_send_smtp_recipients_refused_not_retryable(email_channel: EmailChannel) -> None:
    with patch.object(aiosmtplib, "SMTP") as mock_smtp_class:
        mock_smtp = AsyncMock()
        mock_smtp.send_message = AsyncMock(
            side_effect=aiosmtplib.SMTPRecipientsRefused({"user@example.com": 550})
        )
        _smtp_context(mock_smtp_class, mock_smtp)

        result = await email_channel._send_smtp("user@example.com", "Subject", "Body")

        assert result.success is False
        assert result.retryable is False


@pytest.mark.asyncio()
async def test_send_smtp_sender_refused_not_retryable(email_channel: EmailChannel) -> None:
    with patch.object(aiosmtplib, "SMTP") as mock_smtp_class:
        mock_smtp = AsyncMock()
        mock_smtp.send_message = AsyncMock(
            side_effect=aiosmtplib.SMTPSenderRefused(550, "Sender refused", "alerts@test.com")
        )
        _smtp_context(mock_smtp_class, mock_smtp)

        result = await email_channel._send_smtp("user@example.com", "Subject", "Body")

        assert result.success is False
        assert result.retryable is False


@pytest.mark.asyncio()
@pytest.mark.parametrize(
    ("code", "expected_retryable"),
    [
        (421, True),
        (450, True),
        (550, False),
    ],
)
async def test_send_smtp_response_exception_retryable(
    email_channel: EmailChannel,
    code: int,
    expected_retryable: bool,
) -> None:
    with patch.object(aiosmtplib, "SMTP") as mock_smtp_class:
        mock_smtp = AsyncMock()
        mock_smtp.send_message = AsyncMock(
            side_effect=aiosmtplib.SMTPResponseException(code, "Error")
        )
        _smtp_context(mock_smtp_class, mock_smtp)

        result = await email_channel._send_smtp("user@example.com", "Subject", "Body")

        assert result.success is False
        assert result.retryable is expected_retryable


@pytest.mark.asyncio()
async def test_send_falls_back_to_sendgrid(email_channel: EmailChannel) -> None:
    smtp_result = DeliveryResult(success=False, error="smtp failed", retryable=True)
    sendgrid_result = DeliveryResult(success=True, message_id="sg-123")

    with patch.object(
        email_channel, "_send_smtp", AsyncMock(return_value=smtp_result)
    ) as mock_smtp:
        with patch.object(
            email_channel, "_send_sendgrid", AsyncMock(return_value=sendgrid_result)
        ) as mock_sendgrid:
            result = await email_channel.send("user@example.com", "Subject", "Body")

            assert result.success is True
            assert result.message_id == "sg-123"
            mock_smtp.assert_awaited_once()
            mock_sendgrid.assert_awaited_once()


@pytest.mark.asyncio()
async def test_send_returns_error_without_sendgrid(email_channel: EmailChannel) -> None:
    email_channel.sendgrid_api_key = None
    smtp_result = DeliveryResult(
        success=False,
        error="smtp failed",
        retryable=False,
        metadata={"smtp": "down"},
    )

    with patch.object(email_channel, "_send_smtp", AsyncMock(return_value=smtp_result)):
        result = await email_channel.send("user@example.com", "Subject", "Body")

        assert result.success is False
        assert result.retryable is False
        assert result.error == "SMTP failed and SendGrid not configured"
        assert result.metadata == {"smtp": "down"}


@pytest.mark.asyncio()
async def test_send_sendgrid_success(email_channel: EmailChannel) -> None:
    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 202
        mock_response.headers = {"x-message-id": "sg-123", "retry-after": "5"}
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        result = await email_channel._send_sendgrid("user@example.com", "Subject", "Body")

        assert result.success is True
        assert result.message_id == "sg-123"
        assert result.metadata.get("retry_after") == "5"


@pytest.mark.asyncio()
async def test_send_sendgrid_timeout(email_channel: EmailChannel) -> None:
    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("Timeout"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        result = await email_channel._send_sendgrid("user@example.com", "Subject", "Body")

        assert result.success is False
        assert result.retryable is True
        assert result.error == "timeout"


@pytest.mark.asyncio()
async def test_send_sendgrid_request_error(email_channel: EmailChannel) -> None:
    exc = httpx.RequestError("connection failed", request=MagicMock())
    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=exc)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        result = await email_channel._send_sendgrid("user@example.com", "Subject", "Body")

        assert result.success is False
        assert result.retryable is True


@pytest.mark.asyncio()
@pytest.mark.parametrize(
    ("status", "expected_retryable"),
    [
        (429, True),
        (500, True),
        (400, False),
    ],
)
async def test_send_sendgrid_http_errors(
    email_channel: EmailChannel,
    status: int,
    expected_retryable: bool,
) -> None:
    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = status
        mock_response.headers = {"retry-after": "10"}
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        result = await email_channel._send_sendgrid("user@example.com", "Subject", "Body")

        assert result.success is False
        assert result.retryable is expected_retryable
        assert result.metadata.get("retry_after") == "10"
