"""NiceGUI Web Console configuration.

Minimal C0 configuration for the NiceGUI-based web console. Later components
extend this with session store, auth middleware, and audit logging settings.
"""

from __future__ import annotations

import base64
import binascii
import ipaddress
import logging
import os
from typing import Literal, cast

# =============================================================================
# Server settings
# =============================================================================

HOST = os.getenv("WEB_CONSOLE_NG_HOST", "0.0.0.0")
PORT = int(os.getenv("WEB_CONSOLE_NG_PORT", "8080"))
DEBUG = os.getenv("WEB_CONSOLE_NG_DEBUG", "false").lower() in {"1", "true", "yes", "on"}
PAGE_TITLE = os.getenv(
    "WEB_CONSOLE_NG_PAGE_TITLE", "Trading Platform - Web Console (NiceGUI)"
)

logger = logging.getLogger(__name__)

# =============================================================================
# Backend endpoints
# =============================================================================

EXECUTION_GATEWAY_URL = os.getenv("EXECUTION_GATEWAY_URL", "http://localhost:8002")

# =============================================================================
# Redis
# =============================================================================

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/1")

# =============================================================================
# Session configuration (timeouts + cookies)
# =============================================================================

SESSION_IDLE_TIMEOUT_MINUTES = int(os.getenv("SESSION_IDLE_TIMEOUT_MINUTES", "15"))
SESSION_ABSOLUTE_TIMEOUT_HOURS = int(os.getenv("SESSION_ABSOLUTE_TIMEOUT_HOURS", "4"))

SESSION_COOKIE_SECURE = os.getenv(
    "SESSION_COOKIE_SECURE",
    "true" if not DEBUG else "false",
).lower() in {
    "1",
    "true",
    "yes",
    "on",
}
SESSION_COOKIE_SAMESITE = os.getenv("SESSION_COOKIE_SAMESITE", "lax").lower()
if SESSION_COOKIE_SAMESITE not in {"lax", "strict", "none"}:
    raise ValueError(
        "SESSION_COOKIE_SAMESITE must be one of: lax, strict, none"
    )
SESSION_COOKIE_PATH = os.getenv("SESSION_COOKIE_PATH", "/")
SESSION_COOKIE_DOMAIN = os.getenv("SESSION_COOKIE_DOMAIN", "") or None
SESSION_COOKIE_HTTPONLY = os.getenv("SESSION_COOKIE_HTTPONLY", "true").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
SESSION_COOKIE_NAME = (
    "__Host-nicegui_session" if SESSION_COOKIE_SECURE else "nicegui_session"
)

# =============================================================================
# Device binding + trusted proxies
# =============================================================================

DEVICE_BINDING_ENABLED = os.getenv(
    "DEVICE_BINDING_ENABLED",
    "true" if SESSION_COOKIE_SECURE else "false",
).lower() in {"1", "true", "yes", "on"}

DEVICE_BINDING_SUBNET_MASK = int(os.getenv("DEVICE_BINDING_SUBNET_MASK", "24"))

_RAW_TRUSTED_PROXIES = os.getenv("TRUSTED_PROXY_IPS", "").strip()
TrustedProxy = (
    ipaddress.IPv4Network
    | ipaddress.IPv6Network
    | ipaddress.IPv4Address
    | ipaddress.IPv6Address
)

TRUSTED_PROXY_IPS: list[TrustedProxy] = []
if _RAW_TRUSTED_PROXIES:
    for entry in _RAW_TRUSTED_PROXIES.split(","):
        value = entry.strip()
        if not value:
            continue
        try:
            if "/" in value:
                TRUSTED_PROXY_IPS.append(ipaddress.ip_network(value, strict=False))
            else:
                TRUSTED_PROXY_IPS.append(ipaddress.ip_address(value))
        except ValueError:
            raise ValueError(f"Invalid TRUSTED_PROXY_IPS entry: {value}") from None

# =============================================================================
# Trusted hosts
# =============================================================================

_RAW_ALLOWED_HOSTS = os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1,::1")
if "*" in {h.strip() for h in _RAW_ALLOWED_HOSTS.split(",")}:
    ALLOWED_HOSTS = ["*"]
else:
    ALLOWED_HOSTS = [h.strip() for h in _RAW_ALLOWED_HOSTS.split(",") if h.strip()]

# =============================================================================
# Authentication configuration
# =============================================================================

def _load_auth_type() -> Literal["dev", "basic", "mtls", "oauth2"]:
    value = os.getenv("WEB_CONSOLE_AUTH_TYPE", "dev").lower()
    allowed = {"dev", "basic", "mtls", "oauth2"}
    if value not in allowed:
        raise ValueError("WEB_CONSOLE_AUTH_TYPE must be one of: dev, basic, mtls, oauth2")
    return cast(Literal["dev", "basic", "mtls", "oauth2"], value)


AUTH_TYPE = _load_auth_type()
# UI flag for login page to show/hide selector
SHOW_AUTH_TYPE_SELECTOR = os.getenv("SHOW_AUTH_TYPE_SELECTOR", "false").lower() in {"1", "true", "yes", "on"}
ALLOW_DEV_BASIC_AUTH = os.getenv("WEB_CONSOLE_ALLOW_DEV_BASIC_AUTH", "false").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
if ALLOW_DEV_BASIC_AUTH:
    logger.warning(
        "WEB_CONSOLE_ALLOW_DEV_BASIC_AUTH is enabled; dev credentials are active."
    )

DEV_USER_ID = os.getenv("WEB_CONSOLE_DEV_USER_ID", "dev-user")
DEV_ROLE = os.getenv("WEB_CONSOLE_DEV_ROLE", "admin")
DEV_STRATEGIES = [
    s.strip() for s in os.getenv("WEB_CONSOLE_DEV_STRATEGIES", "").split(",") if s.strip()
]
if not DEV_STRATEGIES:
    default_strategy = os.getenv("STRATEGY_ID", "").strip()
    if default_strategy:
        DEV_STRATEGIES = [default_strategy]

# =============================================================================
# Audit logging
# =============================================================================

AUDIT_LOG_DB_ENABLED = os.getenv(
    "AUDIT_LOG_DB_ENABLED", "true" if not DEBUG else "false"
).lower() in {"1", "true", "yes", "on"}

AUDIT_LOG_SINK = os.getenv(
    "AUDIT_LOG_SINK", "both" if not DEBUG else "log"
).lower()
if AUDIT_LOG_SINK not in {"log", "db", "both"}:
    raise ValueError("AUDIT_LOG_SINK must be one of: log, db, both")

AUDIT_LOG_RETENTION_DAYS = int(os.getenv("AUDIT_LOG_RETENTION_DAYS", "90"))

# =============================================================================
# Encryption / signing keys (C2 wiring)
# =============================================================================

SESSION_ENCRYPTION_KEY = os.getenv("SESSION_ENCRYPTION_KEY", "").strip()
SESSION_ENCRYPTION_KEY_PREV = os.getenv("SESSION_ENCRYPTION_KEY_PREV", "").strip()

HMAC_SIGNING_KEYS = os.getenv("HMAC_SIGNING_KEYS", "").strip()
HMAC_CURRENT_KEY_ID = os.getenv("HMAC_CURRENT_KEY_ID", "").strip()


def _decode_base64_key(value: str, env_name: str) -> bytes:
    if not value:
        raise ValueError(f"{env_name} environment variable not set")
    try:
        key_bytes = base64.b64decode(value)
    except Exception as exc:  # pragma: no cover - defensive
        raise ValueError(f"{env_name} must be base64-encoded: {exc}") from exc
    if len(key_bytes) != 32:
        raise ValueError(
            f"{env_name} must decode to 32 bytes (got {len(key_bytes)})"
        )
    return key_bytes


def _decode_hex_key(value: str, env_name: str) -> bytes:
    if not value:
        raise ValueError(f"{env_name} value is empty")
    try:
        return binascii.unhexlify(value)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"{env_name} must be hex-encoded: {exc}") from exc


def get_encryption_keys() -> list[bytes]:
    """Return encryption keys in priority order (current -> previous)."""

    keys = [_decode_base64_key(SESSION_ENCRYPTION_KEY, "SESSION_ENCRYPTION_KEY")]
    if SESSION_ENCRYPTION_KEY_PREV:
        keys.append(
            _decode_base64_key(
                SESSION_ENCRYPTION_KEY_PREV, "SESSION_ENCRYPTION_KEY_PREV"
            )
        )
    return keys


def get_signing_keys() -> dict[str, bytes]:
    """Return signing keys indexed by key id.

    Format: HMAC_SIGNING_KEYS="01:abcd...,02:dead..." (hex-encoded keys)
    """

    if not HMAC_SIGNING_KEYS:
        raise ValueError("HMAC_SIGNING_KEYS environment variable not set")

    key_map: dict[str, bytes] = {}
    for pair in HMAC_SIGNING_KEYS.split(","):
        pair = pair.strip()
        if not pair:
            continue
        if ":" not in pair:
            raise ValueError(
                "HMAC_SIGNING_KEYS must be in format 'id:key' separated by commas"
            )
        key_id, key_hex = pair.split(":", 1)
        key_id = key_id.strip()
        key_hex = key_hex.strip()
        if not key_id:
            raise ValueError("HMAC_SIGNING_KEYS entry missing key id")
        key_map[key_id] = _decode_hex_key(key_hex, f"HMAC_SIGNING_KEYS[{key_id}]")

    if not key_map:
        raise ValueError("HMAC_SIGNING_KEYS must contain at least one key")

    if HMAC_CURRENT_KEY_ID and HMAC_CURRENT_KEY_ID not in key_map:
        raise ValueError(
            "HMAC_CURRENT_KEY_ID does not match any key id in HMAC_SIGNING_KEYS"
        )

    return key_map
