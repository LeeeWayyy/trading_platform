"""Email delivery channel with SMTP primary and SendGrid fallback."""

from __future__ import annotations

import logging
import mimetypes
from email.message import EmailMessage
from email.utils import make_msgid
from pathlib import Path
from typing import Any

import aiosmtplib
import httpx

from libs.alerts.channels.base import BaseChannel
from libs.alerts.models import DeliveryResult
from libs.alerts.pii import mask_recipient
from libs.secrets import SecretManager, create_secret_manager

logger = logging.getLogger(__name__)


class EmailChannel(BaseChannel):
    """Email channel that prefers SMTP and falls back to SendGrid."""

    channel_type = "email"
    TIMEOUT = 10  # seconds

    def __init__(
        self,
        *,
        secret_manager: SecretManager | None = None,
        smtp_host: str | None = None,
        smtp_port: int | str | None = None,
        smtp_user: str | None = None,
        smtp_password: str | None = None,
        sendgrid_api_key: str | None = None,
        from_email: str | None = None,
    ) -> None:
        self.secrets = secret_manager or create_secret_manager()
        self.smtp_host = smtp_host or self.secrets.get_secret("SMTP_HOST")

        port_value: Any = smtp_port or self.secrets.get_secret("SMTP_PORT") or 587
        try:
            self.smtp_port = int(port_value)
        except (TypeError, ValueError):
            self.smtp_port = 587

        self.smtp_user = smtp_user or self.secrets.get_secret("SMTP_USER")
        self.smtp_password = smtp_password or self.secrets.get_secret("SMTP_PASSWORD")
        self.sendgrid_api_key = sendgrid_api_key or self.secrets.get_secret("SENDGRID_API_KEY")

        # Prefer explicit from_email, fall back to secret, then SMTP user.
        self.from_email = (
            from_email or self.secrets.get_secret("ALERTS_FROM_EMAIL") or self.smtp_user
        )

    def _sanitize_error(self, message: str, recipient: str) -> str:
        """Remove raw email addresses from error messages."""
        sanitized = message or ""
        if recipient:
            sanitized = sanitized.replace(recipient, mask_recipient(recipient, self.channel_type))
        if self.from_email:
            sanitized = sanitized.replace(
                self.from_email,
                mask_recipient(self.from_email, self.channel_type),
            )
        return sanitized

    async def send(
        self,
        recipient: str,
        subject: str,
        body: str,
        metadata: dict[str, Any] | None = None,
        attachments: list[str] | None = None,
    ) -> DeliveryResult:
        masked = mask_recipient(recipient, self.channel_type)
        logger.info("email_send_attempt", extra={"recipient": masked})

        # Validate from_email is configured before attempting send
        if not self.from_email:
            return DeliveryResult(
                success=False,
                error="from_email not configured",
                retryable=False,
            )

        smtp_result = await self._send_smtp(recipient, subject, body, attachments)
        if smtp_result.success:
            logger.info(
                "email_smtp_sent",
                extra={"recipient": masked, "message_id": smtp_result.message_id},
            )
            return smtp_result

        logger.warning(
            "email_smtp_failed_fallback_sendgrid",
            extra={
                "recipient": masked,
                "retryable": smtp_result.retryable,
                "error": smtp_result.error,
            },
        )

        if not self.sendgrid_api_key:
            return DeliveryResult(
                success=False,
                error="SMTP failed and SendGrid not configured",
                retryable=smtp_result.retryable,
                metadata=smtp_result.metadata,
            )

        if attachments:
             logger.warning(
                 "SendGrid fallback does not support attachments yet, sending without attachments",
                 extra={"recipient": masked}
             )

        sendgrid_result = await self._send_sendgrid(recipient, subject, body)
        if sendgrid_result.success:
            logger.info(
                "email_sendgrid_sent",
                extra={"recipient": masked, "message_id": sendgrid_result.message_id},
            )
        else:
            logger.error(
                "email_sendgrid_failed",
                extra={
                    "recipient": masked,
                    "error": sendgrid_result.error,
                    "retryable": sendgrid_result.retryable,
                },
            )

        return sendgrid_result

    def _build_message(
        self,
        recipient: str,
        subject: str,
        body: str,
        attachments: list[str] | None = None
    ) -> EmailMessage:
        message = EmailMessage()
        message["From"] = self.from_email
        message["To"] = recipient
        message["Subject"] = subject
        message["Message-ID"] = make_msgid()
        message.set_content(body)

        if attachments:
            for attachment_path in attachments:
                path = Path(attachment_path)
                if not path.exists():
                    logger.warning(f"Attachment not found: {path}")
                    continue

                ctype, encoding = mimetypes.guess_type(path)
                if ctype is None or encoding is not None:
                    # No guess could be made, or the file is encoded (compressed), so
                    # use a generic bag-of-bits type.
                    ctype = "application/octet-stream"

                maintype, subtype = ctype.split("/", 1)

                try:
                    file_data = path.read_bytes()
                    message.add_attachment(
                        file_data,
                        maintype=maintype,
                        subtype=subtype,
                        filename=path.name
                    )
                except Exception as e:
                    logger.error(f"Failed to attach file {path}: {e}")

        return message

    async def _send_smtp(
        self,
        recipient: str,
        subject: str,
        body: str,
        attachments: list[str] | None = None
    ) -> DeliveryResult:
        message = self._build_message(recipient, subject, body, attachments)
        masked = mask_recipient(recipient, self.channel_type)

        try:
            # Enable TLS: port 587 uses STARTTLS, port 465 uses implicit TLS
            use_tls = self.smtp_port == 465
            start_tls = self.smtp_port == 587

            async with aiosmtplib.SMTP(
                hostname=self.smtp_host,
                port=self.smtp_port,
                timeout=self.TIMEOUT,
                use_tls=use_tls,
                start_tls=start_tls,
            ) as smtp:
                if self.smtp_user and self.smtp_password:
                    await smtp.login(self.smtp_user, self.smtp_password)
                await smtp.send_message(message)
                return DeliveryResult(success=True, message_id=message["Message-ID"])

        except aiosmtplib.SMTPAuthenticationError as exc:
            logger.error("email_smtp_auth_error", extra={"recipient": masked})
            sanitized = self._sanitize_error(str(exc), recipient)
            return DeliveryResult(success=False, error=sanitized, retryable=False)

        except (aiosmtplib.SMTPConnectError, TimeoutError) as exc:
            logger.error("email_smtp_connection_error", extra={"recipient": masked})
            sanitized = self._sanitize_error(str(exc), recipient)
            return DeliveryResult(success=False, error=sanitized, retryable=True)

        except aiosmtplib.SMTPRecipientsRefused as exc:
            # Permanent failure - invalid recipient (e.g., 550)
            logger.error(
                "email_smtp_recipients_refused",
                extra={"recipient": masked, "retryable": False},
            )
            sanitized = self._sanitize_error(str(exc), recipient)
            return DeliveryResult(success=False, error=sanitized, retryable=False)

        except aiosmtplib.SMTPSenderRefused as exc:
            # Permanent failure - sender rejected
            logger.error(
                "email_smtp_sender_refused",
                extra={"recipient": masked, "retryable": False},
            )
            sanitized = self._sanitize_error(str(exc), recipient)
            return DeliveryResult(success=False, error=sanitized, retryable=False)

        except aiosmtplib.SMTPResponseException as exc:
            # SMTP RFC 5321: 4xx = Transient failure (retryable)
            #                5xx = Permanent failure (not retryable)
            # Note: Different from HTTP where 5xx is retryable
            retryable = 400 <= exc.code < 500
            logger.error(
                "email_smtp_response_error",
                extra={"recipient": masked, "status": exc.code, "retryable": retryable},
            )
            sanitized = self._sanitize_error(str(exc), recipient)
            return DeliveryResult(success=False, error=sanitized, retryable=retryable)

        except Exception as exc:  # pragma: no cover - safety net
            logger.error("email_smtp_unknown_error", extra={"recipient": masked})
            sanitized = self._sanitize_error(str(exc), recipient)
            return DeliveryResult(success=False, error=sanitized, retryable=True)

    async def _send_sendgrid(self, recipient: str, subject: str, body: str) -> DeliveryResult:
        masked = mask_recipient(recipient, self.channel_type)
        headers = {
            "Authorization": f"Bearer {self.sendgrid_api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "personalizations": [{"to": [{"email": recipient}]}],
            "from": {"email": self.from_email},
            "subject": subject,
            "content": [{"type": "text/plain", "value": body}],
        }

        try:
            async with httpx.AsyncClient(timeout=self.TIMEOUT) as client:
                response = await client.post(
                    "https://api.sendgrid.com/v3/mail/send",
                    headers=headers,
                    json=payload,
                )
        except httpx.TimeoutException:
            logger.error("email_sendgrid_timeout", extra={"recipient": masked})
            return DeliveryResult(success=False, error="timeout", retryable=True)
        except httpx.RequestError as exc:
            sanitized = self._sanitize_error(str(exc), recipient)
            logger.error(
                "email_sendgrid_connection_error",
                extra={"recipient": masked, "error": sanitized},
            )
            return DeliveryResult(success=False, error=sanitized, retryable=True)

        metadata: dict[str, str] = {}
        retry_after = response.headers.get("retry-after")
        if retry_after:
            metadata["retry_after"] = retry_after

        if response.status_code == 202:
            msg_id = response.headers.get("x-message-id")
            return DeliveryResult(success=True, message_id=msg_id, metadata=metadata)

        retryable = response.status_code == 429 or response.status_code >= 500
        logger.error(
            "email_sendgrid_failed",
            extra={
                "recipient": masked,
                "status": response.status_code,
                "retryable": retryable,
            },
        )
        return DeliveryResult(
            success=False,
            error=f"SendGrid HTTP {response.status_code}",
            retryable=retryable,
            metadata=metadata,
        )


__all__ = ["EmailChannel"]
