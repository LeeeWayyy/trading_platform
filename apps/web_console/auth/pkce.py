"""PKCE (Proof Key for Code Exchange) utilities for OAuth2.

Implements RFC 7636 for OAuth2 authorization code flow security.

Security guarantees:
- code_verifier: 64 bytes (512 bits) of cryptographic randomness
- code_challenge_method: S256 (SHA256, NOT plain text)
- Base64-URL encoding per RFC 4648 Section 5
- State/nonce: 32 bytes (256 bits) for CSRF and replay protection
"""

import base64
import hashlib
import os
from typing import NamedTuple


class PKCEChallenge(NamedTuple):
    """PKCE challenge pair for OAuth2 authorization."""

    code_verifier: str  # 43-128 char random string
    code_challenge: str  # Base64-URL(SHA256(code_verifier))


def generate_pkce_challenge() -> PKCEChallenge:
    """Generate PKCE code_verifier and code_challenge (S256 method).

    Returns:
        PKCEChallenge with code_verifier and code_challenge

    Security:
        - code_verifier: 64 bytes (512 bits) of cryptographic randomness
        - code_challenge_method: S256 (SHA256, NOT plain text)
        - Base64-URL encoding (RFC 4648 Section 5)
    """
    # Generate 64 bytes of cryptographic randomness (512 bits)
    code_verifier_bytes = os.urandom(64)

    # Base64-URL encode (no padding)
    code_verifier = base64.urlsafe_b64encode(code_verifier_bytes).decode("utf-8").rstrip("=")

    # Generate code_challenge: Base64-URL(SHA256(code_verifier))
    challenge_hash = hashlib.sha256(code_verifier.encode("utf-8")).digest()
    code_challenge = base64.urlsafe_b64encode(challenge_hash).decode("utf-8").rstrip("=")

    return PKCEChallenge(code_verifier=code_verifier, code_challenge=code_challenge)


def generate_state() -> str:
    """Generate cryptographically random state for CSRF protection.

    Returns:
        32-byte random string (Base64-URL encoded)
    """
    state_bytes = os.urandom(32)  # 256 bits
    return base64.urlsafe_b64encode(state_bytes).decode("utf-8").rstrip("=")


def generate_nonce() -> str:
    """Generate cryptographically random nonce for replay protection.

    Returns:
        32-byte random string (Base64-URL encoded)
    """
    nonce_bytes = os.urandom(32)  # 256 bits
    return base64.urlsafe_b64encode(nonce_bytes).decode("utf-8").rstrip("=")


def generate_session_id() -> str:
    """Generate cryptographically random session ID.

    Returns:
        32-byte random string (Base64-URL encoded, 256 bits)
    """
    session_bytes = os.urandom(32)  # 256 bits
    return base64.urlsafe_b64encode(session_bytes).decode("utf-8").rstrip("=")
