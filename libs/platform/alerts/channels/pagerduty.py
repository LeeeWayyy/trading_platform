"""PagerDuty Events API v2 delivery channel."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from libs.platform.alerts.channels.base import BaseChannel
from libs.platform.alerts.models import DeliveryResult
from libs.platform.alerts.pii import mask_recipient
from libs.platform.alerts.poison_queue import _sanitize_error_for_log

logger = logging.getLogger(__name__)


class PagerDutyChannel(BaseChannel):
    """PagerDuty Events API v2 integration.

    Sends trigger events to PagerDuty using the Events API v2.
    Routing key is the PagerDuty integration key for the service.
    Inherits BaseChannel; retries handled by delivery service layer.
    """

    channel_type = "pagerduty"
    EVENTS_API_URL = "https://events.pagerduty.com/v2/enqueue"
    TIMEOUT = 10.0

    async def send(
        self,
        recipient: str,  # PagerDuty routing key
        subject: str,  # Alert summary (used as PD event summary)
        body: str,  # Alert details (used as custom_details description)
        metadata: dict[str, Any] | None = None,
        attachments: list[str] | None = None,  # Unused for PagerDuty
    ) -> DeliveryResult:
        """Conforms to BaseChannel.send(recipient, subject, body, metadata, attachments).

        Maps BaseChannel contract to PagerDuty Events API v2:
        - recipient -> routing_key
        - subject -> payload.summary
        - body -> payload.custom_details.description
        - metadata -> payload.custom_details (merged)

        Uses a short-lived httpx client per send (matches Slack/Email channels)
        to avoid cross-event-loop issues in the alert worker.
        """
        # Copy metadata to avoid mutating caller's dict
        meta = dict(metadata) if metadata else {}
        severity = meta.pop("severity", "warning")
        custom_details: dict[str, Any] = {"description": body}
        if meta:
            custom_details.update(meta)
        payload = {
            "routing_key": recipient,
            "event_action": "trigger",
            "payload": {
                "summary": subject,
                "source": "trading-platform",
                "severity": severity,
                "custom_details": custom_details,
            },
        }
        masked = mask_recipient(recipient, "pagerduty")
        try:
            async with httpx.AsyncClient(timeout=self.TIMEOUT) as client:
                resp = await client.post(self.EVENTS_API_URL, json=payload)
            message_id = None
            if resp.is_success and resp.headers.get("content-type", "").startswith(
                "application/json"
            ):
                try:
                    message_id = resp.json().get("dedup_key")
                except ValueError:
                    logger.warning("pagerduty_json_decode_error", extra={"recipient": masked})
            error_text = None
            if not resp.is_success:
                error_text = _sanitize_error_for_log(resp.text)
                if recipient:
                    error_text = error_text.replace(recipient, masked)
            return DeliveryResult(
                success=resp.is_success,
                message_id=message_id,
                error=error_text,
                retryable=resp.status_code == 429 or resp.status_code >= 500,
            )
        except httpx.TimeoutException:
            logger.error("pagerduty_timeout", extra={"recipient": masked})
            return DeliveryResult(success=False, error="timeout", retryable=True)
        except httpx.RequestError as exc:
            safe_error = str(exc).replace(recipient, masked) if recipient else str(exc)
            logger.error(
                "pagerduty_request_error",
                extra={"recipient": masked, "error": safe_error},
            )
            return DeliveryResult(success=False, error=safe_error, retryable=True)


__all__ = ["PagerDutyChannel"]
