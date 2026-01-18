from __future__ import annotations

import pytest

from apps.web_console_ng.auth.auth_result import AuthResult
from apps.web_console_ng.auth.providers.base import AuthProvider


class _DummyProvider(AuthProvider):
    async def authenticate(self, **kwargs: object) -> AuthResult:
        return AuthResult(success=True)


@pytest.mark.asyncio()
async def test_get_authorization_url_not_supported() -> None:
    provider = _DummyProvider()
    with pytest.raises(NotImplementedError) as exc:
        await provider.get_authorization_url()

    assert "does not support authorization URL generation" in str(exc.value)


@pytest.mark.asyncio()
async def test_handle_callback_not_supported() -> None:
    provider = _DummyProvider()
    with pytest.raises(NotImplementedError) as exc:
        await provider.handle_callback("code", "state")

    assert "does not support callbacks" in str(exc.value)
