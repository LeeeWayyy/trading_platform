"""Tests for CSRF protection component."""

import pytest
import streamlit as st
from unittest.mock import patch

from apps.web_console.components.csrf_protection import (
    CSRF_TOKEN_KEY,
    generate_csrf_token,
    rotate_csrf_token,
    verify_csrf_token,
)


class TestCSRFProtection:
    """Test CSRF token generation and validation."""

    def test_generate_csrf_token_creates_token(self):
        """Test token generation creates 32-byte URL-safe token."""

        with patch.object(st, "session_state", {}):
            token = generate_csrf_token()

            assert token is not None
            assert len(token) >= 32
            assert st.session_state[CSRF_TOKEN_KEY] == token

    def test_generate_csrf_token_reuses_existing(self):
        """Test repeated calls return same token."""

        with patch.object(st, "session_state", {}):
            token1 = generate_csrf_token()
            token2 = generate_csrf_token()

            assert token1 == token2

    def test_verify_csrf_token_valid(self):
        """Test valid token verification."""

        with patch.object(st, "session_state", {CSRF_TOKEN_KEY: "test_token"}):
            assert verify_csrf_token("test_token") is True

    def test_verify_csrf_token_invalid(self):
        """Test invalid token rejected."""

        with patch.object(st, "session_state", {CSRF_TOKEN_KEY: "correct"}):
            assert verify_csrf_token("wrong") is False

    def test_verify_csrf_token_missing_session(self):
        """Test missing session token fails."""

        with patch.object(st, "session_state", {}):
            assert verify_csrf_token("any") is False

    def test_verify_csrf_token_empty_submitted(self):
        """Test empty submitted token fails."""

        with patch.object(st, "session_state", {CSRF_TOKEN_KEY: "token"}):
            assert verify_csrf_token("") is False

    def test_rotate_csrf_token_changes_value(self):
        """Test rotation creates new token."""

        with patch.object(st, "session_state", {CSRF_TOKEN_KEY: "old"}):
            new_token = rotate_csrf_token()

            assert new_token != "old"
            assert st.session_state[CSRF_TOKEN_KEY] == new_token
