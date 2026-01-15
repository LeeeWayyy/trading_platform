"""SMS delivery channel via Twilio."""

from __future__ import annotations

import asyncio
import logging
from functools import partial
from typing import Any

from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client

from libs.core.common.exceptions import ConfigurationError
from libs.platform.alerts.channels.base import BaseChannel
from libs.platform.alerts.models import DeliveryResult
from libs.platform.alerts.pii import mask_recipient
from libs.platform.secrets import SecretManager, create_secret_manager

logger = logging.getLogger(__name__)


class SMSChannel(BaseChannel):
    """SMS channel using Twilio REST API wrapped in a thread executor."""

    channel_type = "sms"
    TIMEOUT = 10  # seconds

    def __init__(
        self,
        *,
        secret_manager: SecretManager | None = None,
        account_sid: str | None = None,
        auth_token: str | None = None,
        from_number: str | None = None,
    ) -> None:
        self.secrets = secret_manager or create_secret_manager()
        self.account_sid = account_sid or self.secrets.get_secret("TWILIO_ACCOUNT_SID")
        self.auth_token = auth_token or self.secrets.get_secret("TWILIO_AUTH_TOKEN")
        self.from_number = from_number or self.secrets.get_secret("TWILIO_FROM_NUMBER")

        # Validate required credentials are present
        missing = []
        if not self.account_sid:
            missing.append("TWILIO_ACCOUNT_SID")
        if not self.auth_token:
            missing.append("TWILIO_AUTH_TOKEN")
        if not self.from_number:
            missing.append("TWILIO_FROM_NUMBER")
        if missing:
            raise ConfigurationError(f"SMS channel requires: {', '.join(missing)}")

        # Pass timeout to Twilio client to bound HTTP requests at the network level
        # This prevents runaway threads when using run_in_executor
        self.client = Client(self.account_sid, self.auth_token, timeout=self.TIMEOUT)

    async def send(
        self,
        recipient: str,
        subject: str,
        body: str,
        metadata: dict[str, Any] | None = None,
        attachments: list[str] | None = None,  # SMS ignores attachments
    ) -> DeliveryResult:
        masked = mask_recipient(recipient, self.channel_type)
        logger.info("sms_send_attempt", extra={"recipient": masked})

        loop = asyncio.get_running_loop()
        try:
            send_fut = loop.run_in_executor(
                None,
                partial(
                    self.client.messages.create,
                    to=recipient,
                    from_=self.from_number,
                    body=f"{subject}: {body}",
                ),
            )
            message = await asyncio.wait_for(send_fut, timeout=self.TIMEOUT)
            logger.info("sms_sent", extra={"recipient": masked, "sid": message.sid})
            return DeliveryResult(success=True, message_id=message.sid)

        except TimeoutError:
            logger.error("sms_timeout", extra={"recipient": masked})
            return DeliveryResult(success=False, error="timeout", retryable=True)

        except TwilioRestException as exc:
            retryable = (exc.status == 429) or (exc.status is not None and exc.status >= 500)
            sanitized_msg = self._sanitize_twilio_msg(exc.msg or str(exc), recipient)
            logger.error(
                "sms_send_failed",
                extra={
                    "recipient": masked,
                    "status": exc.status,
                    "code": exc.code,
                    "retryable": retryable,
                },
            )
            metadata_out: dict[str, str] = {}
            if exc.code is not None:
                metadata_out["twilio_code"] = str(exc.code)
            return DeliveryResult(
                success=False,
                error=f"Twilio {exc.status}: {sanitized_msg}",
                retryable=retryable,
                metadata=metadata_out,
            )

        except Exception as exc:  # pragma: no cover - safety net
            sanitized = self._sanitize_twilio_msg(str(exc), recipient)
            logger.error(
                "sms_connection_error",
                extra={"recipient": masked, "error": sanitized},
            )
            return DeliveryResult(success=False, error=sanitized, retryable=True)

    def _sanitize_twilio_msg(self, message: str, recipient: str) -> str:
        """Remove raw phone numbers from Twilio error messages."""
        sanitized = message or ""
        if recipient:
            sanitized = sanitized.replace(recipient, mask_recipient(recipient, self.channel_type))
        if self.from_number:
            sanitized = sanitized.replace(
                self.from_number,
                mask_recipient(self.from_number, self.channel_type),
            )
        return sanitized


__all__ = ["SMSChannel"]
