"""
Webhook signature verification for Alpaca webhooks.

Implements HMAC-SHA256 signature verification to prevent webhook spoofing.
Alpaca signs webhooks with a secret key, and we verify the signature before
processing the webhook payload.

See: https://docs.alpaca.markets/docs/webhooks#webhook-signature-verification
See: ADR-0005 for security requirements
"""

import hashlib
import hmac
import logging

logger = logging.getLogger(__name__)


def verify_webhook_signature(payload: bytes, signature: str, secret: str) -> bool:
    """
    Verify webhook signature from Alpaca.

    Alpaca sends HMAC-SHA256 signature in the X-Alpaca-Signature header.
    The signature is computed as: HMAC-SHA256(secret, payload_bytes)

    Args:
        payload: Raw request body bytes
        signature: Signature from X-Alpaca-Signature header
        secret: Webhook secret key configured in Alpaca dashboard

    Returns:
        True if signature is valid, False otherwise

    Examples:
        >>> payload = b'{"event":"fill","order":{...}}'
        >>> signature = "a1b2c3d4e5f6..."
        >>> secret = "my_webhook_secret"
        >>> verify_webhook_signature(payload, signature, secret)
        True

    Security Notes:
        - Uses constant-time comparison (hmac.compare_digest) to prevent
          timing attacks
        - Secret should be stored in environment variable, not hardcoded
        - Signature should be hex-encoded string (lowercase)

    Implementation Details:
        1. Compute expected signature: HMAC-SHA256(secret, payload)
        2. Compare with provided signature using constant-time comparison
        3. Return True if match, False otherwise
    """
    if not payload or not signature or not secret:
        logger.warning("Missing required parameters for signature verification")
        return False

    try:
        # Compute expected signature
        expected_signature = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()

        # Constant-time comparison to prevent timing attacks
        is_valid = hmac.compare_digest(expected_signature, signature.lower())

        if not is_valid:
            logger.warning(
                "Webhook signature verification failed",
                extra={
                    "expected_signature_prefix": expected_signature[:8],
                    "provided_signature_prefix": signature[:8],
                },
            )

        return is_valid

    except Exception as e:
        logger.error(f"Error verifying webhook signature: {e}", exc_info=True)
        return False


def generate_webhook_signature(payload: bytes, secret: str) -> str:
    """
    Generate webhook signature for testing.

    This is the inverse of verify_webhook_signature and is used for
    generating test signatures in unit tests.

    Args:
        payload: Raw request body bytes
        secret: Webhook secret key

    Returns:
        Hex-encoded HMAC-SHA256 signature

    Examples:
        >>> payload = b'{"event":"fill","order":{...}}'
        >>> secret = "my_webhook_secret"
        >>> signature = generate_webhook_signature(payload, secret)
        >>> len(signature)
        64  # SHA256 hex digest is 64 characters

    Notes:
        - Used primarily for testing
        - Returns lowercase hex string
        - Same algorithm as Alpaca uses for signing
    """
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def extract_signature_from_header(header_value: str | None) -> str | None:
    """
    Extract signature from X-Alpaca-Signature header.

    Alpaca may send the signature in different formats:
    - Simple: "a1b2c3d4e5f6..."
    - Prefixed: "sha256=a1b2c3d4e5f6..."

    Args:
        header_value: Value from X-Alpaca-Signature header

    Returns:
        Extracted signature (hex string) or None if invalid

    Examples:
        >>> extract_signature_from_header("a1b2c3d4e5f6...")
        'a1b2c3d4e5f6...'

        >>> extract_signature_from_header("sha256=a1b2c3d4e5f6...")
        'a1b2c3d4e5f6...'

        >>> extract_signature_from_header(None)
        None
    """
    if not header_value:
        return None

    # Remove 'sha256=' prefix if present
    if header_value.startswith("sha256="):
        return header_value[7:]

    return header_value


def validate_signature_format(signature: str) -> bool:
    """
    Validate that signature has correct format.

    Signature should be a 64-character hex string (SHA256 digest).

    Args:
        signature: Signature to validate

    Returns:
        True if valid format, False otherwise

    Examples:
        >>> validate_signature_format("a" * 64)
        True

        >>> validate_signature_format("invalid")
        False

        >>> validate_signature_format("z" * 64)  # Invalid hex
        False
    """
    if not isinstance(signature, str):
        return False

    if len(signature) != 64:
        return False

    # Check if it's valid hexadecimal
    try:
        int(signature, 16)
        return True
    except ValueError:
        return False
