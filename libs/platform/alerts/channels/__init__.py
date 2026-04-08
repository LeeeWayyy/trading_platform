"""Alert delivery channel implementations."""

from libs.platform.alerts.channels.base import BaseChannel
from libs.platform.alerts.channels.pagerduty import PagerDutyChannel
from libs.platform.alerts.channels.slack import SlackChannel

try:
    from libs.platform.alerts.channels.email import EmailChannel
except ModuleNotFoundError:
    EmailChannel = None  # type: ignore[assignment]

try:
    from libs.platform.alerts.channels.sms import SMSChannel
except ModuleNotFoundError:
    SMSChannel = None  # type: ignore[assignment]

__all__ = [
    "BaseChannel",
    "EmailChannel",
    "PagerDutyChannel",
    "SlackChannel",
    "SMSChannel",
]
