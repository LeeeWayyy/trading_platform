"""Tests for SMSChannel delivery behavior."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from twilio.base.exceptions import TwilioRestException

from libs.core.common.exceptions import ConfigurationError
from libs.platform.alerts.channels.sms import SMSChannel
from libs.platform.alerts.pii import mask_recipient


@pytest.fixture()
def secrets_map() -> dict[str, str]:
    return {
        "TWILIO_ACCOUNT_SID": "AC123",
        "TWILIO_AUTH_TOKEN": "token-abc",
        "TWILIO_FROM_NUMBER": "+15551234567",
    }


@pytest.fixture()
def sms_channel(secrets_map: dict[str, str]) -> SMSChannel:
    mock_secrets = MagicMock()
    mock_secrets.get_secret.side_effect = lambda key: secrets_map.get(key)

    with patch("libs.platform.alerts.channels.sms.Client") as mock_client_class:
        mock_client = MagicMock()
        mock_client.messages.create = MagicMock()
        mock_client_class.return_value = mock_client
        channel = SMSChannel(secret_manager=mock_secrets)
        channel.client = mock_client

    return channel


def test_init_uses_secrets_and_sets_timeout(secrets_map: dict[str, str]) -> None:
    mock_secrets = MagicMock()
    mock_secrets.get_secret.side_effect = lambda key: secrets_map.get(key)

    with patch("libs.platform.alerts.channels.sms.Client") as mock_client_class:
        SMSChannel(secret_manager=mock_secrets)

        mock_client_class.assert_called_once_with(
            secrets_map["TWILIO_ACCOUNT_SID"],
            secrets_map["TWILIO_AUTH_TOKEN"],
            timeout=SMSChannel.TIMEOUT,
        )


def test_init_prefers_explicit_credentials(secrets_map: dict[str, str]) -> None:
    mock_secrets = MagicMock()
    mock_secrets.get_secret.side_effect = lambda key: secrets_map.get(key)

    with patch("libs.platform.alerts.channels.sms.Client") as mock_client_class:
        channel = SMSChannel(
            secret_manager=mock_secrets,
            account_sid="explicit-sid",
            auth_token="explicit-token",
            from_number="+15550001111",
        )

    assert channel.account_sid == "explicit-sid"
    assert channel.auth_token == "explicit-token"
    assert channel.from_number == "+15550001111"
    mock_client_class.assert_called_once_with(
        "explicit-sid",
        "explicit-token",
        timeout=SMSChannel.TIMEOUT,
    )


@pytest.mark.parametrize(
    "missing_keys",
    [
        {"TWILIO_ACCOUNT_SID"},
        {"TWILIO_AUTH_TOKEN"},
        {"TWILIO_FROM_NUMBER"},
        {"TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER"},
    ],
)
def test_init_missing_credentials_raises(missing_keys: set[str]) -> None:
    values = {
        "TWILIO_ACCOUNT_SID": "AC123",
        "TWILIO_AUTH_TOKEN": "token-abc",
        "TWILIO_FROM_NUMBER": "+15551234567",
    }
    for key in missing_keys:
        values[key] = None

    mock_secrets = MagicMock()
    mock_secrets.get_secret.side_effect = lambda key: values.get(key)

    with patch("libs.platform.alerts.channels.sms.Client"):
        with pytest.raises(ConfigurationError) as excinfo:
            SMSChannel(secret_manager=mock_secrets)

    message = str(excinfo.value)
    for key in missing_keys:
        assert key in message


def test_sanitize_twilio_msg_masks_numbers(sms_channel: SMSChannel) -> None:
    recipient = "+15550001111"
    raw = f"Error sending to {recipient} from {sms_channel.from_number}"

    sanitized = sms_channel._sanitize_twilio_msg(raw, recipient)

    assert recipient not in sanitized
    assert sms_channel.from_number not in sanitized
    assert mask_recipient(recipient, "sms") in sanitized


@pytest.mark.asyncio()
async def test_send_success_formats_message(sms_channel: SMSChannel) -> None:
    recipient = "+15550001111"
    message = MagicMock()
    message.sid = "SM123"

    loop = MagicMock()
    loop.run_in_executor.return_value = object()

    with patch("libs.platform.alerts.channels.sms.asyncio.get_running_loop", return_value=loop):
        with patch(
            "libs.platform.alerts.channels.sms.asyncio.wait_for",
            new=AsyncMock(return_value=message),
        ):
            result = await sms_channel.send(recipient, "Subject", "Body")

    assert result.success is True
    assert result.message_id == "SM123"

    args, _ = loop.run_in_executor.call_args
    assert args[0] is None
    partial_fn = args[1]
    assert partial_fn.keywords["to"] == recipient
    assert partial_fn.keywords["from_"] == sms_channel.from_number
    assert partial_fn.keywords["body"] == "Subject: Body"


@pytest.mark.asyncio()
async def test_send_timeout_returns_retryable(sms_channel: SMSChannel) -> None:
    loop = MagicMock()
    loop.run_in_executor.return_value = object()

    with patch("libs.platform.alerts.channels.sms.asyncio.get_running_loop", return_value=loop):
        with patch(
            "libs.platform.alerts.channels.sms.asyncio.wait_for",
            new=AsyncMock(side_effect=TimeoutError()),
        ):
            result = await sms_channel.send("+15550001111", "Subject", "Body")

    assert result.success is False
    assert result.retryable is True
    assert result.error == "timeout"


@pytest.mark.asyncio()
@pytest.mark.parametrize(
    ("status", "expected_retryable"),
    [
        (429, True),
        (500, True),
        (400, False),
    ],
)
async def test_send_twilio_errors_retryable(
    sms_channel: SMSChannel,
    status: int,
    expected_retryable: bool,
) -> None:
    recipient = "+15550001111"
    error_msg = f"Failed to send to {recipient} from {sms_channel.from_number}"
    exc = TwilioRestException(status, "http://twilio.test", msg=error_msg, code=20429)

    loop = MagicMock()
    loop.run_in_executor.return_value = object()

    with patch("libs.platform.alerts.channels.sms.asyncio.get_running_loop", return_value=loop):
        with patch(
            "libs.platform.alerts.channels.sms.asyncio.wait_for",
            new=AsyncMock(side_effect=exc),
        ):
            result = await sms_channel.send(recipient, "Subject", "Body")

    assert result.success is False
    assert result.retryable is expected_retryable
    assert recipient not in (result.error or "")
    assert sms_channel.from_number not in (result.error or "")
    assert mask_recipient(recipient, "sms") in (result.error or "")
    assert result.metadata == {"twilio_code": "20429"}


@pytest.mark.asyncio()
async def test_send_connection_error_masks_numbers(sms_channel: SMSChannel) -> None:
    recipient = "+15550001111"
    loop = MagicMock()
    loop.run_in_executor.return_value = object()

    with patch("libs.platform.alerts.channels.sms.asyncio.get_running_loop", return_value=loop):
        with patch(
            "libs.platform.alerts.channels.sms.asyncio.wait_for",
            new=AsyncMock(side_effect=Exception(f"Connection failed to {recipient}")),
        ):
            result = await sms_channel.send(recipient, "Subject", "Body")

    assert result.success is False
    assert result.retryable is True
    assert recipient not in (result.error or "")
    assert mask_recipient(recipient, "sms") in (result.error or "")
