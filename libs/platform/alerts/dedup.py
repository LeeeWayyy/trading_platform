"""Deduplication key computation for alert deliveries.

Uses HMAC-SHA256 for recipient hashing to prevent PII exposure in database.
Hour bucket ensures same alert to same recipient is deduplicated within the hour.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime

from libs.platform.secrets import SecretManager, create_secret_manager


def get_recipient_hash_secret(secret_manager: SecretManager | None = None) -> str:
    """Get recipient hash secret from secrets manager.

    Per task doc:
    - Secret stored in secrets manager (NOT env var for production)
    - Rotated quarterly
    - NEVER logged

    Raises:
        ValueError: If ALERT_RECIPIENT_HASH_SECRET is not configured
    """
    secrets = secret_manager or create_secret_manager()
    secret = secrets.get_secret("ALERT_RECIPIENT_HASH_SECRET")
    if not secret:
        raise ValueError("Missing ALERT_RECIPIENT_HASH_SECRET in secrets manager")
    return secret


def compute_recipient_hash(recipient: str, channel_type: str, hash_secret: str) -> str:
    """Compute HMAC hash of recipient for rate limiting and dedup.

    Includes channel_type to differentiate email vs phone limits.
    Returns first 16 chars of hex digest per spec.
    """

    data = f"{channel_type}:{recipient}"
    return hmac.new(hash_secret.encode(), data.encode(), hashlib.sha256).hexdigest()[:16]


def compute_dedup_key(
    rule_id: str,
    channel: str,
    recipient: str,
    triggered_at: datetime,
    hash_secret: str,
) -> str:
    """Compute idempotent dedup key with hashed recipient.

    Uses rule_id (not alert event id) so repeated triggers of the same rule
    to the same recipient within the same hour coalesce via ON CONFLICT DO NOTHING.

    Security: Uses recipient_hash instead of raw recipient to prevent PII
    exposure in database. Per task doc PII requirement: 'never store raw PII'.

    Hour bucket derived from original trigger timestamp (not current time).
    Format: {rule_id}:{channel}:{recipient_hash}:{hour_bucket}
    """

    hour_bucket = triggered_at.replace(minute=0, second=0, microsecond=0).isoformat()
    recipient_hash = compute_recipient_hash(recipient, channel, hash_secret)
    return f"{rule_id}:{channel}:{recipient_hash}:{hour_bucket}"
