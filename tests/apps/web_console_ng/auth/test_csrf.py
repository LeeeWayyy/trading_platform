from __future__ import annotations

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from apps.web_console_ng.auth import csrf
from apps.web_console_ng.auth.csrf import (
    CSRF_COOKIE_NAME,
    CSRF_EXEMPT_PATHS,
    CSRF_HEADER_NAME,
    verify_csrf_token,
)


def _build_request(
    *,
    path: str,
    cookies: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> Request:
    header_items: list[tuple[bytes, bytes]] = []
    if headers:
        for key, value in headers.items():
            header_items.append((key.lower().encode(), value.encode()))
    if cookies:
        cookie_value = "; ".join(f"{key}={value}" for key, value in cookies.items())
        header_items.append((b"cookie", cookie_value.encode()))

    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": header_items,
        "query_string": b"",
        "client": ("127.0.0.1", 123),
        "server": ("testserver", 80),
        "scheme": "http",
    }

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request(scope, receive)


@pytest.mark.asyncio()
async def test_verify_csrf_token_allows_exempt_path() -> None:
    exempt_path = next(iter(CSRF_EXEMPT_PATHS))
    request = _build_request(path=exempt_path)

    await verify_csrf_token(request)


@pytest.mark.asyncio()
async def test_verify_csrf_token_blocks_missing_values() -> None:
    request = _build_request(path="/auth/logout")

    with pytest.raises(HTTPException) as exc_info:
        await verify_csrf_token(request)

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "csrf_missing"


@pytest.mark.asyncio()
async def test_verify_csrf_token_blocks_mismatched_values() -> None:
    request = _build_request(
        path="/auth/logout",
        cookies={CSRF_COOKIE_NAME: "cookie-token"},
        headers={CSRF_HEADER_NAME: "header-token"},
    )

    with pytest.raises(HTTPException) as exc_info:
        await verify_csrf_token(request)

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "csrf_invalid"


@pytest.mark.asyncio()
async def test_verify_csrf_token_allows_matching_values() -> None:
    request = _build_request(
        path="/auth/logout",
        cookies={CSRF_COOKIE_NAME: "token"},
        headers={CSRF_HEADER_NAME: "token"},
    )

    await verify_csrf_token(request)


@pytest.mark.asyncio()
async def test_verify_csrf_token_uses_compare_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    called = {"value": False}

    def _compare(a: str, b: str) -> bool:
        called["value"] = True
        return True

    monkeypatch.setattr(csrf.hmac, "compare_digest", _compare)

    request = _build_request(
        path="/auth/logout",
        cookies={CSRF_COOKIE_NAME: "token"},
        headers={CSRF_HEADER_NAME: "token"},
    )

    await verify_csrf_token(request)

    assert called["value"] is True
