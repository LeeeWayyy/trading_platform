"""Tests for alert delivery channel handlers."""

from unittest.mock import AsyncMock, MagicMock, patch

import aiosmtplib
import httpx
import pytest

from libs.alerts.channels import EmailChannel, SlackChannel, SMSChannel


class TestEmailChannel:
    """Test EmailChannel handler."""

    @pytest.fixture()
    def email_channel(self):
        """Create email channel with test credentials."""
        return EmailChannel(
            smtp_host="smtp.test.com",
            smtp_port=587,
            smtp_user="test@test.com",
            smtp_password="password",
            from_email="alerts@test.com",
            sendgrid_api_key="sg-test-key",
        )

    @pytest.mark.asyncio()
    async def test_channel_type(self, email_channel):
        """Test channel type is email."""
        assert email_channel.channel_type == "email"

    @pytest.mark.asyncio()
    async def test_send_smtp_success(self, email_channel):
        """Test successful SMTP delivery."""
        with patch.object(aiosmtplib, "SMTP") as mock_smtp_class:
            mock_smtp = AsyncMock()
            mock_smtp.__aenter__ = AsyncMock(return_value=mock_smtp)
            mock_smtp.__aexit__ = AsyncMock(return_value=None)
            mock_smtp_class.return_value = mock_smtp

            result = await email_channel.send(
                recipient="user@example.com",
                subject="Test Alert",
                body="Alert body",
            )

            assert result.success is True
            assert result.message_id is not None

    @pytest.mark.asyncio()
    async def test_send_smtp_auth_error(self, email_channel):
        """Test SMTP authentication error."""
        with patch.object(aiosmtplib, "SMTP") as mock_smtp_class:
            mock_smtp = AsyncMock()
            mock_smtp.__aenter__ = AsyncMock(return_value=mock_smtp)
            mock_smtp.__aexit__ = AsyncMock(return_value=None)
            mock_smtp.login = AsyncMock(
                side_effect=aiosmtplib.SMTPAuthenticationError(535, "Auth failed")
            )
            mock_smtp_class.return_value = mock_smtp

            result = await email_channel.send(
                recipient="user@example.com",
                subject="Test",
                body="Body",
            )

            assert result.success is False
            assert result.retryable is False

    @pytest.mark.asyncio()
    async def test_send_smtp_connection_error_falls_back_to_sendgrid(self, email_channel):
        """Test SMTP connection error triggers SendGrid fallback."""
        with patch.object(aiosmtplib, "SMTP") as mock_smtp_class:
            mock_smtp = AsyncMock()
            mock_smtp.__aenter__ = AsyncMock(
                side_effect=aiosmtplib.SMTPConnectError("Connection failed")
            )
            mock_smtp_class.return_value = mock_smtp

            with patch("httpx.AsyncClient") as mock_client_class:
                mock_client = AsyncMock()
                mock_response = MagicMock()
                mock_response.status_code = 202
                mock_response.headers = {"x-message-id": "sg-123"}
                mock_client.post = AsyncMock(return_value=mock_response)
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=None)
                mock_client_class.return_value = mock_client

                result = await email_channel.send(
                    recipient="user@example.com",
                    subject="Test",
                    body="Body",
                )

                assert result.success is True
                assert result.message_id == "sg-123"

    @pytest.mark.asyncio()
    async def test_no_from_email_returns_error(self):
        """Test missing from_email returns error."""
        secret_manager = MagicMock()
        secret_manager.get_secret.return_value = None

        channel = EmailChannel(
            smtp_host="smtp.test.com",
            smtp_port=587,
            smtp_user=None,
            smtp_password=None,
            from_email=None,
            sendgrid_api_key="sg-test-key",
            secret_manager=secret_manager,
        )

        result = await channel.send(
            recipient="user@example.com",
            subject="Test",
            body="Body",
        )

        assert result.success is False
        assert result.retryable is False
        assert "from_email not configured" in result.error


class TestSlackChannel:
    """Test SlackChannel handler."""

    @pytest.fixture()
    def slack_channel(self):
        """Create Slack channel with test webhook."""
        return SlackChannel(webhook_url="https://hooks.slack.com/services/XXX/YYY/ZZZ")

    @pytest.mark.asyncio()
    async def test_channel_type(self, slack_channel):
        """Test channel type is slack."""
        assert slack_channel.channel_type == "slack"

    @pytest.mark.asyncio()
    async def test_send_success(self, slack_channel):
        """Test successful Slack delivery."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = "ok"
            mock_response.headers = {}
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            result = await slack_channel.send(
                recipient="https://hooks.slack.com/services/XXX/YYY/ZZZ",
                subject="Test Alert",
                body="Alert body",
            )

            assert result.success is True

    @pytest.mark.asyncio()
    async def test_send_rate_limited(self, slack_channel):
        """Test Slack rate limiting."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_response = MagicMock()
            mock_response.status_code = 429
            mock_response.headers = {"retry-after": "30"}
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            result = await slack_channel.send(
                recipient="https://hooks.slack.com/services/XXX/YYY/ZZZ",
                subject="Test",
                body="Body",
            )

            assert result.success is False
            assert result.retryable is True
            assert result.metadata.get("retry_after") == "30"

    @pytest.mark.asyncio()
    async def test_send_timeout(self, slack_channel):
        """Test Slack timeout handling."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("Timeout"))
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            result = await slack_channel.send(
                recipient="https://hooks.slack.com/services/XXX/YYY/ZZZ",
                subject="Test",
                body="Body",
            )

            assert result.success is False
            assert result.retryable is True


class TestSMSChannel:
    """Test SMSChannel handler."""

    def test_channel_type(self):
        """Test channel type constant is sms."""
        assert SMSChannel.channel_type == "sms"

    @pytest.mark.asyncio()
    async def test_send_success(self):
        """Test successful SMS delivery with mocked Twilio client."""
        with patch("libs.alerts.channels.sms.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_message = MagicMock()
            mock_message.sid = "SM123"
            mock_client.messages.create = MagicMock(return_value=mock_message)
            mock_client_class.return_value = mock_client

            channel = SMSChannel(
                account_sid="ACTEST123",
                auth_token="test-token",
                from_number="+15551234567",
            )

            result = await channel.send(
                recipient="+15559876543",
                subject="Test Alert",
                body="Alert body",
            )

            assert result.success is True
            assert result.message_id == "SM123"

    @pytest.mark.asyncio()
    async def test_send_timeout(self):
        """Test SMS timeout handling."""
        with patch("libs.alerts.channels.sms.Client") as mock_client_class:
            mock_client = MagicMock()

            def slow_send(*args, **kwargs):
                import time

                time.sleep(20)  # Longer than timeout

            mock_client.messages.create = slow_send
            mock_client_class.return_value = mock_client

            channel = SMSChannel(
                account_sid="ACTEST123",
                auth_token="test-token",
                from_number="+15551234567",
            )

            # This will timeout
            result = await channel.send(
                recipient="+15559876543",
                subject="Test",
                body="Body",
            )

            assert result.success is False
            assert result.retryable is True
