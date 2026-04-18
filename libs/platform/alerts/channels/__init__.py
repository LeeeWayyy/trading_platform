"""Alert delivery channel implementations."""

from __future__ import annotations

import logging as _logging
from typing import TYPE_CHECKING

from libs.platform.alerts.channels.base import BaseChannel
from libs.platform.alerts.channels.pagerduty import PagerDutyChannel
from libs.platform.alerts.channels.slack import SlackChannel

if TYPE_CHECKING:
    from libs.platform.alerts.models import ChannelType

# EmailChannel depends on aiosmtplib (optional).  Only suppress that
# specific missing package; re-raise for any other import failure so
# internal regressions are not silently hidden.
try:
    from libs.platform.alerts.channels.email import EmailChannel
except ModuleNotFoundError as _exc:
    if _exc.name is not None and (
        _exc.name == "aiosmtplib" or _exc.name.startswith("aiosmtplib.")
    ):
        EmailChannel = None  # type: ignore[assignment,misc]
    else:
        raise

# SMSChannel depends on twilio (optional).
try:
    from libs.platform.alerts.channels.sms import SMSChannel
except ModuleNotFoundError as _exc:
    if _exc.name is not None and (
        _exc.name == "twilio" or _exc.name.startswith("twilio.")
    ):
        SMSChannel = None  # type: ignore[assignment,misc]
    else:
        raise

def build_channel_handlers(
    *,
    logger: _logging.Logger | None = None,
    strict: bool = False,
) -> dict[ChannelType, BaseChannel]:
    """Build available channel handlers, skipping unconfigured channels.

    Shared factory used by both the alert worker and the web console
    alert service, eliminating duplicated initialization logic.

    Email requires ``aiosmtplib``; SMS requires ``twilio`` credentials.
    If either dependency is missing or credentials are absent, the channel
    is skipped and an error is logged so operators are alerted that email
    or SMS deliveries will fail at delivery time.  Set *strict=True* to
    raise immediately instead of degrading (useful for startup validation).

    Args:
        logger: Logger instance; defaults to module logger.
        strict: When True, raise ``RuntimeError`` if any channel
            dependency is missing instead of degrading gracefully.

    Returns:
        Mapping of ``ChannelType`` to instantiated handler.
    """
    import os

    from libs.core.common.exceptions import ConfigurationError
    from libs.platform.alerts.models import ChannelType

    _log = logger or _logging.getLogger(__name__)

    # Honour ALERT_CHANNELS_STRICT env var as a deploy-time knob
    _strict = strict or os.environ.get("ALERT_CHANNELS_STRICT", "").lower() in (
        "1",
        "true",
        "yes",
    )

    handlers: dict[ChannelType, BaseChannel] = {
        ChannelType.SLACK: SlackChannel(),
    }

    # Email — requires aiosmtplib
    if EmailChannel is None:
        msg = (
            "email_channel_disabled: aiosmtplib is not installed. "
            "Any email alert rules will fail at delivery time. "
            "Install aiosmtplib to enable SMTP email notifications."
        )
        if _strict:
            raise RuntimeError(msg)
        _log.error(
            "email_channel_disabled",
            extra={
                "reason": "aiosmtplib not installed",
                "impact": "email deliveries will fail",
                "hint": "Install aiosmtplib to enable SMTP email notifications",
            },
        )
    else:
        handlers[ChannelType.EMAIL] = EmailChannel()

    # SMS — requires twilio + credentials
    if SMSChannel is None:
        msg = (
            "sms_channel_disabled: twilio is not installed. "
            "Any SMS alert rules will fail at delivery time."
        )
        if _strict:
            raise RuntimeError(msg)
        _log.error(
            "sms_channel_disabled",
            extra={
                "reason": "twilio not installed",
                "impact": "SMS deliveries will fail",
                "hint": (
                    "Install twilio and set TWILIO_ACCOUNT_SID, "
                    "TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER"
                ),
            },
        )
    else:
        try:
            handlers[ChannelType.SMS] = SMSChannel()
        except ConfigurationError as exc:
            _log.warning(
                "sms_channel_disabled",
                extra={
                    "reason": str(exc),
                    "hint": "Set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER",
                },
            )

    # PagerDuty — routing key per-recipient, no global credentials needed
    handlers[ChannelType.PAGERDUTY] = PagerDutyChannel()

    return handlers


__all__ = [
    "BaseChannel",
    "EmailChannel",
    "PagerDutyChannel",
    "SlackChannel",
    "SMSChannel",
    "build_channel_handlers",
]
