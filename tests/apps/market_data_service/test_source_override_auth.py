"""Focused tests for source-override ownership authorization."""

from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi import HTTPException

from apps.market_data_service.main import _authorize_source_override
from libs.core.common.api_auth_dependency import AuthContext, InternalTokenClaims


@pytest.fixture()
def signed_internal_auth_context() -> AuthContext:
    """Internal-token context for web_console_ng service."""
    return AuthContext(
        user=None,
        internal_claims=InternalTokenClaims(
            service_id="web_console_ng",
            user_id=None,
            strategy_id=None,
            nonce="nonce-1",
            timestamp=1,
        ),
        auth_type="internal_token",
        is_authenticated=True,
    )


@pytest.mark.asyncio()
async def test_authorize_source_override_allows_mixed_case_owner_prefix(
    monkeypatch: pytest.MonkeyPatch,
    signed_internal_auth_context: AuthContext,
) -> None:
    """Signed owner requests should accept mixed-case source prefixes."""
    monkeypatch.setenv("INTERNAL_TOKEN_SECRET_WEB_CONSOLE_NG", "s" * 64)

    await _authorize_source_override(
        signed_internal_auth_context,
        "WEB_CONSOLE:User-1:ABC",
    )


@pytest.mark.asyncio()
async def test_authorize_source_override_rejects_foreign_service_prefix(
    monkeypatch: pytest.MonkeyPatch,
    signed_internal_auth_context: AuthContext,
) -> None:
    """Signed requests must still fail when source prefix is not service-owned."""
    monkeypatch.setenv("INTERNAL_TOKEN_SECRET_WEB_CONSOLE_NG", "s" * 64)
    foreign_context = replace(
        signed_internal_auth_context,
        internal_claims=replace(
            signed_internal_auth_context.internal_claims,
            service_id="signal_service",
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        await _authorize_source_override(
            foreign_context,
            "WEB_CONSOLE:User-1:ABC",
        )

    assert exc_info.value.status_code == 403
