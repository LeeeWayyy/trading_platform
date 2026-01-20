"""Unit tests for libs.platform.web_console_auth.pkce."""

from __future__ import annotations

import base64
import hashlib
import re
from unittest.mock import patch

from libs.platform.web_console_auth import pkce

_URLSAFE_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def test_generate_pkce_challenge_deterministic() -> None:
    seed = bytes(range(64))
    with patch("libs.platform.web_console_auth.pkce.os.urandom", return_value=seed):
        challenge = pkce.generate_pkce_challenge()

    expected_verifier = base64.urlsafe_b64encode(seed).decode("utf-8").rstrip("=")
    expected_hash = hashlib.sha256(expected_verifier.encode("utf-8")).digest()
    expected_challenge = base64.urlsafe_b64encode(expected_hash).decode("utf-8").rstrip("=")

    assert challenge.code_verifier == expected_verifier
    assert challenge.code_challenge == expected_challenge
    assert "=" not in challenge.code_verifier
    assert "=" not in challenge.code_challenge


def test_generate_state_nonce_and_session_id_lengths() -> None:
    state = pkce.generate_state()
    nonce = pkce.generate_nonce()
    session_id = pkce.generate_session_id()

    for value in (state, nonce, session_id):
        assert len(value) == 43
        assert "=" not in value
        assert _URLSAFE_RE.match(value) is not None
