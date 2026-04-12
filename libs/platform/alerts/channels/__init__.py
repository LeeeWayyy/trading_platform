"""Alert delivery channel implementations."""

from libs.platform.alerts.channels.base import BaseChannel
from libs.platform.alerts.channels.pagerduty import PagerDutyChannel
from libs.platform.alerts.channels.slack import SlackChannel

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

__all__ = [
    "BaseChannel",
    "EmailChannel",
    "PagerDutyChannel",
    "SlackChannel",
    "SMSChannel",
]
