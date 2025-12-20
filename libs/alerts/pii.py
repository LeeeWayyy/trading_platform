"""Centralized PII masking for alerts.

All components MUST import from here - DO NOT duplicate masking logic.
Convention: raw recipient stored in DB for delivery, masked in logs/UI.
"""


def mask_email(email: str) -> str:
    """Mask email showing ONLY last 4 chars: user@domain.com -> ***.com"""
    if len(email) >= 4:
        return f"***{email[-4:]}"
    return "***"


def mask_phone(phone: str) -> str:
    """Mask phone showing ONLY last 4 chars: +1234567890 -> ***7890"""
    if len(phone) >= 4:
        return f"***{phone[-4:]}"
    return "***"


def mask_webhook(url: str) -> str:
    """Mask webhook URL showing ONLY last 4 chars: https://...xxxx -> ***xxxx"""
    if len(url) >= 4:
        return f"***{url[-4:]}"
    return "***"


def mask_recipient(value: str, channel_type: str) -> str:
    """Mask recipient based on channel type."""
    if channel_type == "email":
        return mask_email(value)
    elif channel_type == "sms":
        return mask_phone(value)
    elif channel_type == "slack":
        return mask_webhook(value)
    return mask_email(value)  # Default to email masking


__all__ = [
    "mask_email",
    "mask_phone",
    "mask_webhook",
    "mask_recipient",
]
