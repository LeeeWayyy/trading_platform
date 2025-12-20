"""PII masking utilities for logs and UI display."""

from __future__ import annotations

import copy
import json
import logging
import re
from datetime import UTC, datetime
from types import TracebackType
from typing import Any

# Compiled patterns for fast reuse
EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
PHONE_PATTERN = re.compile(r"\+?[\d\s\-\(\)]{10,}")
# Matches prefixed keys (tp_live_XXXXXXXX) used for identification only.
# Prefixes are 16 chars total: literal "tp_live_" + 8 base32-ish chars.
API_KEY_PREFIX_PATTERN = re.compile(r"\btp_live_[A-Za-z0-9]{8}\b")
# Matches raw base64url keys (43 chars, url-safe base64 without padding)
# These are the actual secrets returned by generate_api_key()
RAW_API_KEY_PATTERN = re.compile(r"\b[A-Za-z0-9_-]{43}\b")
IP_PATTERN = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")

_RESERVED_LOGGING_FIELDS = {
    "name",
    "msg",
    "args",
    "created",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "thread",
    "threadName",
    "trace_id",
    "context",
    "exc_info",
    "exc_text",
    "stack_info",
}


def mask_email(email: str) -> str:
    """Mask an email address, preserving only the domain part."""
    if not email:
        return "***"
    _, _, domain = email.partition("@")
    return f"***@{domain}" if domain else "***"


def mask_phone(phone: str) -> str:
    """Mask a phone number, showing only the last four digits."""
    digits = "".join(char for char in phone if char.isdigit())
    last4 = digits[-4:] if digits else ""
    return f"***{last4}"


def mask_api_key(key: str) -> str:
    """Mask an API key, preserving prefix (if present) and last four characters."""
    suffix = key[-4:] if key else ""
    if key.startswith("tp_live_"):
        return f"tp_live_xxx...{suffix}"
    # Raw base64url key (43 chars) - mask with generic prefix
    return f"[key]...{suffix}"


def mask_ip(ip: str) -> str:
    """Mask an IP address by hiding the host portion."""
    parts = ip.split(".")
    if len(parts) >= 3:
        return ".".join(parts[:2] + ["***"])
    return "***"


def _sanitize_string(text: str) -> str:
    """Apply pattern-based masking to a string."""
    sanitized = EMAIL_PATTERN.sub(lambda m: mask_email(m.group(0)), text)
    # Mask prefixed keys first (tp_live_...)
    sanitized = API_KEY_PREFIX_PATTERN.sub(lambda m: mask_api_key(m.group(0)), sanitized)
    # Mask raw base64url keys (43 chars)
    sanitized = RAW_API_KEY_PATTERN.sub(lambda m: mask_api_key(m.group(0)), sanitized)
    sanitized = PHONE_PATTERN.sub(lambda m: mask_phone(m.group(0)), sanitized)
    sanitized = IP_PATTERN.sub(lambda m: mask_ip(m.group(0)), sanitized)
    return sanitized


def _sanitize_value(value: Any) -> Any:
    """Sanitize arbitrary values, preserving original types when possible."""
    if isinstance(value, dict):
        return sanitize_dict(value)
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_sanitize_value(item) for item in value)
    if isinstance(value, str):
        return _sanitize_string(value)
    return value


def _is_ip_key(key: str) -> bool:
    """Heuristically detect IP-related keys while avoiding false positives like api_key."""
    if key == "ip":
        return True
    return key.endswith("_ip") or "ip_address" in key


def sanitize_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Recursively sanitize a dictionary by masking PII values.

    Sensitive keys are masked regardless of value type. Strings are scanned
    for embedded PII patterns.
    """
    sanitized: dict[str, Any] = {}

    for raw_key, raw_value in data.items():
        key = str(raw_key).lower()

        if "email" in key:
            sanitized_value = mask_email(str(raw_value)) if isinstance(raw_value, str) else "***"
        elif "phone" in key:
            sanitized_value = mask_phone(str(raw_value)) if isinstance(raw_value, str) else "***"
        elif key.replace("-", "_") in {"api_key", "apikey"} or "api_key" in key:
            sanitized_value = mask_api_key(str(raw_value)) if isinstance(raw_value, str) else "***"
        elif _is_ip_key(key):
            sanitized_value = mask_ip(str(raw_value)) if isinstance(raw_value, str) else "***"
        elif any(token in key for token in ("password", "secret", "token")):
            sanitized_value = "***"
        else:
            sanitized_value = _sanitize_value(raw_value)

        sanitized[raw_key] = sanitized_value

    return sanitized


def sanitize_log_record(record: dict[str, Any]) -> dict[str, Any]:
    """Sanitize a log record dictionary, handling nested structures."""
    return {key: _sanitize_value(value) for key, value in record.items()}


class SanitizingFormatter(logging.Formatter):
    """Logging formatter that automatically masks PII."""

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003 - matches logging API
        record_copy = copy.copy(record)

        # Sanitize message template and args before formatting
        record_copy.msg = _sanitize_value(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record_copy.args = {k: _sanitize_value(v) for k, v in record.args.items()}
            elif isinstance(record.args, tuple):
                record_copy.args = tuple(_sanitize_value(arg) for arg in record.args)
            else:
                record_copy.args = _sanitize_value(record.args)

        # Sanitize any custom attributes
        for key, value in record.__dict__.items():
            if key in _RESERVED_LOGGING_FIELDS:
                continue
            setattr(record_copy, key, _sanitize_value(value))

        return super().format(record_copy)


class SanitizingJSONFormatter(logging.Formatter):
    """JSON formatter with PII sanitization."""

    def __init__(self, service_name: str = "unknown", *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.service_name = service_name

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003 - matches logging API
        message = _sanitize_string(record.getMessage())

        log_entry: dict[str, Any] = {
            "timestamp": self._format_timestamp(record.created),
            "level": record.levelname,
            "service": self.service_name,
            "trace_id": _sanitize_value(getattr(record, "trace_id", None)),
            "message": message,
        }

        context = self._extract_context(record)
        if context is not None:
            log_entry["context"] = _sanitize_value(context)

        if record.exc_info:
            log_entry["exception"] = self._format_exception(record.exc_info)

        log_entry["source"] = {
            "file": record.pathname,
            "line": record.lineno,
            "function": record.funcName,
        }

        return json.dumps(log_entry, default=str)

    def _format_timestamp(self, created: float) -> str:
        """Format timestamp as ISO 8601 with milliseconds."""
        dt = datetime.fromtimestamp(created, tz=UTC)
        return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    def _extract_context(self, record: logging.LogRecord) -> dict[str, Any] | None:
        """Extract and sanitize context fields from the log record."""
        context = getattr(record, "context", None)
        if context and isinstance(context, dict):
            return dict(context)

        extra = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _RESERVED_LOGGING_FIELDS
        }
        return extra if extra else None

    def _format_exception(
        self,
        exc_info: tuple[type[BaseException] | None, BaseException | None, TracebackType | None],
    ) -> dict[str, Any]:
        """Return structured, sanitized exception info."""
        exc_type, exc, tb = exc_info
        traceback_str = ""
        if exc_type is not None and exc is not None:
            traceback_str = logging.Formatter().formatException((exc_type, exc, tb))
        return {
            "type": exc_type.__name__ if exc_type else None,
            "message": _sanitize_value(str(exc)) if exc else None,
            "traceback": traceback_str,
        }


__all__ = [
    "API_KEY_PREFIX_PATTERN",
    "EMAIL_PATTERN",
    "PHONE_PATTERN",
    "RAW_API_KEY_PATTERN",
    "SanitizingFormatter",
    "SanitizingJSONFormatter",
    "mask_api_key",
    "mask_email",
    "mask_ip",
    "mask_phone",
    "sanitize_dict",
    "sanitize_log_record",
]
