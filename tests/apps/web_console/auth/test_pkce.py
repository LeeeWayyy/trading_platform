"""Tests for PKCE (Proof Key for Code Exchange) utilities.

Tests verify RFC 7636 compliance for OAuth2 PKCE implementation.
"""

import base64
import hashlib

from apps.web_console.auth.pkce import (
    PKCEChallenge,
    generate_nonce,
    generate_pkce_challenge,
    generate_session_id,
    generate_state,
)


class TestPKCEChallenge:
    """Test PKCE challenge generation."""

    def test_generate_pkce_challenge_returns_valid_structure(self):
        """Test that PKCE challenge has correct structure."""
        challenge = generate_pkce_challenge()

        assert isinstance(challenge, PKCEChallenge)
        assert isinstance(challenge.code_verifier, str)
        assert isinstance(challenge.code_challenge, str)

    def test_code_verifier_length_within_rfc_bounds(self):
        """Test code_verifier length is 43-128 characters (RFC 7636)."""
        challenge = generate_pkce_challenge()

        # RFC 7636 Section 4.1: code_verifier must be 43-128 chars
        assert 43 <= len(challenge.code_verifier) <= 128

    def test_code_challenge_is_sha256_of_verifier(self):
        """Test that code_challenge = Base64-URL(SHA256(code_verifier))."""
        challenge = generate_pkce_challenge()

        # Manually compute expected challenge
        verifier_hash = hashlib.sha256(challenge.code_verifier.encode("utf-8")).digest()
        expected_challenge = base64.urlsafe_b64encode(verifier_hash).decode("utf-8").rstrip("=")

        assert challenge.code_challenge == expected_challenge

    def test_code_verifier_uses_base64_url_encoding(self):
        """Test that code_verifier uses Base64-URL encoding (no +, /, =)."""
        challenge = generate_pkce_challenge()

        # Base64-URL should not contain +, /, or =
        assert "+" not in challenge.code_verifier
        assert "/" not in challenge.code_verifier
        assert "=" not in challenge.code_verifier

    def test_code_challenge_uses_base64_url_encoding(self):
        """Test that code_challenge uses Base64-URL encoding (no +, /, =)."""
        challenge = generate_pkce_challenge()

        # Base64-URL should not contain +, /, or =
        assert "+" not in challenge.code_challenge
        assert "/" not in challenge.code_challenge
        assert "=" not in challenge.code_challenge

    def test_challenges_are_unique(self):
        """Test that multiple challenges are cryptographically unique."""
        challenges = [generate_pkce_challenge() for _ in range(10)]

        # All code_verifiers should be unique
        verifiers = [c.code_verifier for c in challenges]
        assert len(verifiers) == len(set(verifiers))

        # All code_challenges should be unique
        codes = [c.code_challenge for c in challenges]
        assert len(codes) == len(set(codes))


class TestStateGeneration:
    """Test OAuth2 state parameter generation."""

    def test_generate_state_returns_string(self):
        """Test that state is a string."""
        state = generate_state()
        assert isinstance(state, str)

    def test_state_length_is_32_bytes_encoded(self):
        """Test that state is 32 bytes of randomness (Base64-URL encoded)."""
        state = generate_state()

        # 32 bytes base64-url encoded = 43 chars (without padding)
        assert len(state) == 43

    def test_state_uses_base64_url_encoding(self):
        """Test that state uses Base64-URL encoding."""
        state = generate_state()

        assert "+" not in state
        assert "/" not in state
        assert "=" not in state

    def test_states_are_unique(self):
        """Test that multiple states are cryptographically unique."""
        states = [generate_state() for _ in range(100)]

        assert len(states) == len(set(states))


class TestNonceGeneration:
    """Test OAuth2 nonce parameter generation."""

    def test_generate_nonce_returns_string(self):
        """Test that nonce is a string."""
        nonce = generate_nonce()
        assert isinstance(nonce, str)

    def test_nonce_length_is_32_bytes_encoded(self):
        """Test that nonce is 32 bytes of randomness (Base64-URL encoded)."""
        nonce = generate_nonce()

        # 32 bytes base64-url encoded = 43 chars (without padding)
        assert len(nonce) == 43

    def test_nonce_uses_base64_url_encoding(self):
        """Test that nonce uses Base64-URL encoding."""
        nonce = generate_nonce()

        assert "+" not in nonce
        assert "/" not in nonce
        assert "=" not in nonce

    def test_nonces_are_unique(self):
        """Test that multiple nonces are cryptographically unique."""
        nonces = [generate_nonce() for _ in range(100)]

        assert len(nonces) == len(set(nonces))


class TestSessionIdGeneration:
    """Test session ID generation."""

    def test_generate_session_id_returns_string(self):
        """Test that session_id is a string."""
        session_id = generate_session_id()
        assert isinstance(session_id, str)

    def test_session_id_length_is_32_bytes_encoded(self):
        """Test that session_id is 32 bytes of randomness (Base64-URL encoded)."""
        session_id = generate_session_id()

        # 32 bytes base64-url encoded = 43 chars (without padding)
        assert len(session_id) == 43

    def test_session_id_uses_base64_url_encoding(self):
        """Test that session_id uses Base64-URL encoding."""
        session_id = generate_session_id()

        assert "+" not in session_id
        assert "/" not in session_id
        assert "=" not in session_id

    def test_session_ids_are_unique(self):
        """Test that multiple session_ids are cryptographically unique."""
        session_ids = [generate_session_id() for _ in range(100)]

        assert len(session_ids) == len(set(session_ids))
