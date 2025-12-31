"""Slack webhook delivery channel."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from libs.alerts.channels.base import BaseChannel
from libs.alerts.models import DeliveryResult
from libs.alerts.pii import mask_recipient, mask_webhook
from libs.secrets import SecretManager, create_secret_manager

logger = logging.getLogger(__name__)


class SlackChannel(BaseChannel):
    """Slack channel using an incoming webhook URL."""

    channel_type = "slack"
    TIMEOUT = 10  # seconds

    def __init__(
        self, *, webhook_url: str | None = None, secret_manager: SecretManager | None = None
    ) -> None:
        self.secrets = secret_manager or create_secret_manager()
        self.default_webhook_url = webhook_url or self.secrets.get_secret("SLACK_WEBHOOK_URL")

    async def send(
        self,
        recipient: str,
        subject: str,
        body: str,
        metadata: dict[str, Any] | None = None,
        attachments: list[str] | None = None,  # Slack ignores attachments
    ) -> DeliveryResult:
        webhook_url = recipient or self.default_webhook_url
        if not webhook_url:
            return DeliveryResult(
                success=False,
                error="Slack webhook not configured",
                retryable=False,
            )

        masked = mask_recipient(webhook_url, self.channel_type)
        logger.info("slack_send_attempt", extra={"recipient": masked})

        try:
            async with httpx.AsyncClient(timeout=self.TIMEOUT) as client:
                response = await client.post(
                    webhook_url,
                    json={"text": f"*{subject}*\n{body}"},
                )
        except httpx.TimeoutException:
            logger.error("slack_timeout", extra={"recipient": masked})
            return DeliveryResult(success=False, error="timeout", retryable=True)
        except httpx.RequestError as exc:
            raw_error = str(exc)
            safe_error = (
                raw_error.replace(webhook_url, mask_webhook(webhook_url))
                if webhook_url
                else raw_error
            )
            logger.error(
                "slack_connection_error",
                extra={"recipient": masked, "error": safe_error},
            )
            return DeliveryResult(success=False, error=safe_error, retryable=True)

        metadata_out: dict[str, str] = {}
        retry_after = response.headers.get("retry-after")
        if retry_after:
            metadata_out["retry_after"] = retry_after

        if response.status_code == 200:
            logger.info("slack_sent", extra={"recipient": masked})
            return DeliveryResult(success=True, metadata=metadata_out)

        retryable = response.status_code == 429 or response.status_code >= 500
        logger.error(
            "slack_send_failed",
            extra={
                "recipient": masked,
                "status": response.status_code,
                "retryable": retryable,
            },
        )
        return DeliveryResult(
            success=False,
            error=f"HTTP {response.status_code}",
            retryable=retryable,
            metadata=metadata_out,
        )


__all__ = ["SlackChannel"]
