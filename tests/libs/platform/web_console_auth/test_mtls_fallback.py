"""Unit tests for libs.platform.web_console_auth.mtls_fallback."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from libs.platform.web_console_auth import mtls_fallback


def _make_cert(
    cn: str,
    not_before: datetime,
    not_after: datetime,
    serial: int = 1,
) -> tuple[x509.Certificate, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(serial)
        .not_valid_before(not_before.replace(tzinfo=None))
        .not_valid_after(not_after.replace(tzinfo=None))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    return cert, pem


class _FakeCRL:
    def __init__(self, last_update: datetime, next_update: datetime | None, revoked: list[Any]):
        self.last_update = last_update
        self.next_update = next_update
        self._revoked = revoked

    def __iter__(self):
        return iter(self._revoked)


class _DummyAsyncClient:
    def __init__(self, response: httpx.Response | None = None, exc: Exception | None = None):
        self._response = response
        self._exc = exc
        self.get_calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url: str):
        self.get_calls += 1
        if self._exc:
            raise self._exc
        return self._response


def test_is_fallback_enabled_env_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENABLE_MTLS_FALLBACK", "true")
    assert mtls_fallback.is_fallback_enabled() is True
    monkeypatch.setenv("ENABLE_MTLS_FALLBACK", "false")
    assert mtls_fallback.is_fallback_enabled() is False


def test_get_admin_cn_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MTLS_ADMIN_CN_ALLOWLIST", "alice , bob, ,carol")
    assert mtls_fallback.get_admin_cn_allowlist() == ["alice", "bob", "carol"]


def test_get_admin_cn_allowlist_empty_warns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MTLS_ADMIN_CN_ALLOWLIST", raising=False)
    with patch.object(mtls_fallback.logger, "warning") as mock_warn:
        assert mtls_fallback.get_admin_cn_allowlist() == []
        mock_warn.assert_called_once()


def test_get_crl_url_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MTLS_CRL_URL", raising=False)
    assert mtls_fallback.get_crl_url().endswith("/crl/admin-ca.crl")


@pytest.mark.asyncio()
async def test_crl_cache_fetches_and_caches(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(UTC)
    fake_crl = _FakeCRL(last_update=now.replace(tzinfo=None), next_update=now, revoked=[])
    response = httpx.Response(200, content=b"fake")
    client = _DummyAsyncClient(response=response)

    monkeypatch.setattr(mtls_fallback.httpx, "AsyncClient", lambda timeout: client)
    monkeypatch.setattr(mtls_fallback.x509, "load_der_x509_crl", lambda _: fake_crl)

    cache = mtls_fallback.CRLCache("http://example/crl", cache_ttl_seconds=3600)
    first = await cache.fetch_crl()
    second = await cache.fetch_crl()

    assert first is fake_crl
    assert second is fake_crl
    assert client.get_calls == 1


@pytest.mark.asyncio()
async def test_crl_cache_rejects_stale_crl(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(UTC)
    stale = now - timedelta(days=2)
    fake_crl = _FakeCRL(last_update=stale.replace(tzinfo=None), next_update=now, revoked=[])
    response = httpx.Response(200, content=b"fake")
    client = _DummyAsyncClient(response=response)

    monkeypatch.setattr(mtls_fallback.httpx, "AsyncClient", lambda timeout: client)
    monkeypatch.setattr(mtls_fallback.x509, "load_der_x509_crl", lambda _: fake_crl)

    cache = mtls_fallback.CRLCache("http://example/crl", max_crl_age_seconds=60)
    with pytest.raises(ValueError, match="CRL too old"):
        await cache.fetch_crl()


@pytest.mark.asyncio()
async def test_crl_cache_is_revoked(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(UTC)
    cert, _ = _make_cert("admin", now - timedelta(days=1), now + timedelta(days=1), serial=123)
    revoked_entry = SimpleNamespace(
        serial_number=123,
        revocation_date=now.replace(tzinfo=None),
    )
    fake_crl = _FakeCRL(
        last_update=now.replace(tzinfo=None), next_update=now, revoked=[revoked_entry]
    )

    cache = mtls_fallback.CRLCache("http://example/crl")

    async def _fetch():
        return fake_crl

    monkeypatch.setattr(cache, "fetch_crl", _fetch)

    assert await cache.is_revoked(cert) is True


@pytest.mark.asyncio()
async def test_validate_certificate_success(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(UTC)
    _, pem = _make_cert("admin", now - timedelta(days=1), now + timedelta(days=1))
    validator = mtls_fallback.MtlsFallbackValidator(["admin"], "http://example/crl")

    async def _not_revoked(_cert):
        return False

    monkeypatch.setattr(validator.crl_cache, "is_revoked", _not_revoked)

    info = await validator.validate_certificate(pem, {"X-SSL-Client-Verify": "SUCCESS"})
    assert info.valid is True
    assert info.is_admin is True
    assert info.cn == "admin"
    assert info.crl_status == "valid"


@pytest.mark.asyncio()
async def test_validate_certificate_fails_nginx_verification() -> None:
    validator = mtls_fallback.MtlsFallbackValidator(["admin"], "http://example/crl")
    info = await validator.validate_certificate("not-used", {"X-SSL-Client-Verify": "FAILED"})
    assert info.valid is False
    assert "Client verification failed" in info.error


@pytest.mark.asyncio()
async def test_validate_certificate_fails_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(UTC)
    _, pem = _make_cert("intruder", now - timedelta(days=1), now + timedelta(days=1))
    validator = mtls_fallback.MtlsFallbackValidator(["admin"], "http://example/crl")

    async def _not_revoked(_cert):
        return False

    monkeypatch.setattr(validator.crl_cache, "is_revoked", _not_revoked)

    info = await validator.validate_certificate(pem, {"X-SSL-Client-Verify": "SUCCESS"})
    assert info.valid is False
    assert "not in admin allowlist" in info.error


@pytest.mark.asyncio()
async def test_validate_certificate_fails_lifetime_too_long(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    _, pem = _make_cert("admin", now - timedelta(days=1), now + timedelta(days=10))
    validator = mtls_fallback.MtlsFallbackValidator(["admin"], "http://example/crl")

    async def _not_revoked(_cert):
        return False

    monkeypatch.setattr(validator.crl_cache, "is_revoked", _not_revoked)

    info = await validator.validate_certificate(pem, {"X-SSL-Client-Verify": "SUCCESS"})
    assert info.valid is False
    assert "exceeds maximum" in info.error


@pytest.mark.asyncio()
async def test_validate_certificate_revoked(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(UTC)
    _, pem = _make_cert("admin", now - timedelta(days=1), now + timedelta(days=1))
    validator = mtls_fallback.MtlsFallbackValidator(["admin"], "http://example/crl")

    async def _revoked(_cert):
        return True

    monkeypatch.setattr(validator.crl_cache, "is_revoked", _revoked)

    info = await validator.validate_certificate(pem, {"X-SSL-Client-Verify": "SUCCESS"})
    assert info.valid is False
    assert info.crl_status == "revoked"


@pytest.mark.asyncio()
async def test_validate_certificate_crl_error(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(UTC)
    _, pem = _make_cert("admin", now - timedelta(days=1), now + timedelta(days=1))
    validator = mtls_fallback.MtlsFallbackValidator(["admin"], "http://example/crl")

    async def _raise(_cert):
        raise RuntimeError("boom")

    monkeypatch.setattr(validator.crl_cache, "is_revoked", _raise)

    info = await validator.validate_certificate(pem, {"X-SSL-Client-Verify": "SUCCESS"})
    assert info.valid is False
    assert info.crl_status == "error"
    assert "CRL check failed" in info.error
