"""CSRF protection for Streamlit forms.

Uses secrets.token_urlsafe for cryptographic tokens.
Stored in st.session_state, verified before mutations.
"""

from __future__ import annotations

import secrets

import streamlit as st

CSRF_TOKEN_KEY = "_csrf_token"


def generate_csrf_token() -> str:
    """Generate and store CSRF token in session state."""
    if CSRF_TOKEN_KEY not in st.session_state:
        st.session_state[CSRF_TOKEN_KEY] = secrets.token_urlsafe(32)
    return str(st.session_state[CSRF_TOKEN_KEY])


def verify_csrf_token(submitted_token: str) -> bool:
    """Verify submitted token matches session token."""
    expected = st.session_state.get(CSRF_TOKEN_KEY)
    if not expected or not submitted_token:
        return False
    return secrets.compare_digest(expected, submitted_token)


def rotate_csrf_token() -> str:
    """Rotate token after successful mutation."""
    st.session_state[CSRF_TOKEN_KEY] = secrets.token_urlsafe(32)
    return str(st.session_state[CSRF_TOKEN_KEY])


def get_csrf_input() -> str:
    """Get current CSRF token for form embedding."""
    return generate_csrf_token()


__all__ = [
    "CSRF_TOKEN_KEY",
    "generate_csrf_token",
    "verify_csrf_token",
    "rotate_csrf_token",
    "get_csrf_input",
]
