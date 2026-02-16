"""Unit tests for SQL Explorer service (P6T14/T14.1)."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from libs.web_console_services import sql_explorer_service as module
from libs.web_console_services.sql_explorer_service import (
    ConcurrencyLimitError,
    RateLimitExceededError,
    SensitiveTableAccessError,
    SqlExplorerService,
    _check_sensitive_tables,
    _fingerprint_query,
    _safe_error_message,
    _validate_path_safe,
    _verify_sandbox,
    can_query_dataset,
)


class _DummyRateLimiter:
    def __init__(self, allowed: bool = True, fallback_mode: str = "deny") -> None:
        self.allowed = allowed
        self.fallback_mode = fallback_mode

    async def check_rate_limit(
        self,
        user_id: str,
        action: str,
        max_requests: int,
        window_seconds: int,
    ) -> tuple[bool, int]:
        del user_id, action, max_requests, window_seconds
        return (self.allowed, 0 if not self.allowed else 1)


class _DummyConn:
    def __init__(self, frame: pl.DataFrame | None = None, sleep_s: float = 0.0) -> None:
        self.frame = frame or pl.DataFrame({"a": [1]})
        self.sleep_s = sleep_s
        self.interrupt_called = False
        self.closed = False

    def execute(self, sql: str) -> _DummyConn:
        del sql
        if self.sleep_s:
            time.sleep(self.sleep_s)
        return self

    def pl(self) -> pl.DataFrame:
        return self.frame

    def interrupt(self) -> None:
        self.interrupt_called = True

    def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def _env_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setattr(module, "_APP_ENV", "test")
    monkeypatch.setattr(module, "_IS_DEV_MODE", True)
    monkeypatch.setattr(module, "_AUDIT_LOG_RAW_SQL", False)
    monkeypatch.setenv("SQL_EXPLORER_DEV_MODE", "true")
    monkeypatch.setenv("SQL_EXPLORER_SANDBOX_SKIP", "true")


@pytest.fixture()
def operator_user() -> dict[str, str]:
    return {"role": "operator", "user_id": "operator-1"}


@pytest.fixture()
def viewer_user() -> dict[str, str]:
    return {"role": "viewer", "user_id": "viewer-1"}


def test_sensitive_table_blocking_exact_and_prefix() -> None:
    with pytest.raises(SensitiveTableAccessError):
        _check_sensitive_tables(["users"])

    with pytest.raises(SensitiveTableAccessError):
        _check_sensitive_tables(["auth_tokens_table"])

    _check_sensitive_tables(["crsp_daily"])


def test_path_validation_rejects_traversal_and_quotes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    allowed_root = (tmp_path / "data").resolve()
    allowed_root.mkdir(parents=True)
    monkeypatch.setattr(module, "_ALLOWED_DATA_ROOTS", [allowed_root])

    good_path = str((allowed_root / "wrds" / "crsp" / "daily" / "*.parquet").resolve())
    _validate_path_safe(good_path)

    with pytest.raises(ValueError, match="Path traversal rejected"):
        _validate_path_safe("data/../etc/passwd")

    with pytest.raises(ValueError, match="Unsafe characters"):
        _validate_path_safe("data/wrds/'evil'.parquet")

    with pytest.raises(ValueError, match="Path not under allowed data root"):
        _validate_path_safe("/tmp/other/*.parquet")


def test_safe_error_message_returns_canonical_codes() -> None:
    assert _safe_error_message("validation_error", "raw") == "Query failed validation"
    assert _safe_error_message("unknown", "raw") == "Internal execution error"
    assert _safe_error_message("success") is None


def test_fingerprint_query_replaces_literals_and_handles_unparseable() -> None:
    fingerprint = _fingerprint_query("SELECT * FROM crsp_daily WHERE symbol='AAPL' AND px > 12")
    assert "AAPL" not in fingerprint
    assert "12" not in fingerprint
    assert "?" in fingerprint

    assert _fingerprint_query("SELECT FROM") == "<unparseable query>"


def test_create_query_connection_sets_extension_lockdown(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResult:
        def fetchall(self) -> list[tuple[str]]:
            return [("core_functions",), ("parquet",)]

    class FakeConn:
        def __init__(self) -> None:
            self.statements: list[str] = []

        def execute(self, sql: str) -> FakeResult | None:
            self.statements.append(sql)
            if "duckdb_extensions" in sql:
                return FakeResult()
            return None

        def close(self) -> None:
            return None

    fake_conn = FakeConn()
    monkeypatch.setattr(module.duckdb, "connect", lambda: fake_conn)
    monkeypatch.setattr(module, "_resolve_table_paths", lambda: {})

    module._create_query_connection("crsp", available_tables=set())

    assert "SET enable_extension_autoloading = false" in fake_conn.statements
    assert "SET enable_extension_loading = false" in fake_conn.statements


def test_can_query_dataset_default_deny_for_unmapped(operator_user: dict[str, str]) -> None:
    assert can_query_dataset(operator_user, "crsp") is True

    module.DATASET_TABLES["new_dataset"] = ["new_table"]
    try:
        assert can_query_dataset(operator_user, "new_dataset") is False
    finally:
        del module.DATASET_TABLES["new_dataset"]


def test_service_init_unset_app_env_defaults_to_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # When APP_ENV is unset, _APP_ENV defaults to "local" (safe for dev).
    monkeypatch.setattr(module, "_APP_ENV", "local")
    svc = SqlExplorerService(rate_limiter=_DummyRateLimiter())
    assert svc is not None


def test_service_init_requires_attestation_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "_APP_ENV", "production")
    monkeypatch.setenv("SQL_EXPLORER_DEPLOY_ATTESTED", "false")
    with pytest.raises(RuntimeError, match="deploy-attested"):
        SqlExplorerService(rate_limiter=_DummyRateLimiter())


def test_service_init_rate_limiter_guards(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "_IS_DEV_MODE", False)
    with pytest.raises(ValueError, match="requires a RateLimiter"):
        SqlExplorerService(rate_limiter=None)

    with pytest.raises(ValueError, match="fallback_mode='deny'"):
        SqlExplorerService(rate_limiter=_DummyRateLimiter(fallback_mode="allow"))


def test_service_init_dev_mode_forbidden_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "_APP_ENV", "production")
    monkeypatch.setattr(module, "_IS_DEV_MODE", True)
    monkeypatch.setenv("SQL_EXPLORER_DEPLOY_ATTESTED", "true")
    with pytest.raises(RuntimeError, match="forbidden"):
        SqlExplorerService(rate_limiter=_DummyRateLimiter())


@pytest.mark.asyncio()
async def test_execute_query_permission_denied(viewer_user: dict[str, str]) -> None:
    service = SqlExplorerService(rate_limiter=None)

    with pytest.raises(PermissionError):
        await service.execute_query(viewer_user, "crsp", "SELECT * FROM crsp_daily")


@pytest.mark.asyncio()
async def test_execute_query_rate_limited(operator_user: dict[str, str]) -> None:
    service = SqlExplorerService(rate_limiter=_DummyRateLimiter(allowed=False))

    with pytest.raises(RateLimitExceededError):
        await service.execute_query(operator_user, "crsp", "SELECT * FROM crsp_daily")


@pytest.mark.asyncio()
async def test_execute_query_timeout_interrupts_connection(
    operator_user: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    service = SqlExplorerService(rate_limiter=_DummyRateLimiter())
    conn = _DummyConn(sleep_s=0.2)

    monkeypatch.setattr(module, "_create_query_connection", lambda dataset, available_tables: conn)

    with pytest.raises(TimeoutError):
        await service.execute_query(
            operator_user,
            "crsp",
            "SELECT * FROM crsp_daily",
            timeout_seconds=0,
            available_tables={"crsp_daily"},
        )

    assert conn.interrupt_called is True


@pytest.mark.asyncio()
async def test_execute_query_concurrency_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _DummyConn(sleep_s=0.15)

    async def run_one() -> Any:
        return await module._execute_query_with_timeout(conn, "SELECT 1", timeout_seconds=2)

    tasks = [asyncio.create_task(run_one()) for _ in range(4)]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    concurrency_errors = [r for r in results if isinstance(r, ConcurrencyLimitError)]
    assert concurrency_errors


@pytest.mark.asyncio()
async def test_execute_query_audit_statuses(operator_user: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    statuses: list[str] = []

    def capture_log(
        user: Any,
        dataset: str,
        original_query: str,
        executed_query: str | None,
        row_count: int,
        execution_ms: int,
        status: str,
        error_message: str | None,
    ) -> None:
        del user, dataset, original_query, executed_query, row_count, execution_ms, error_message
        statuses.append(status)

    monkeypatch.setattr(module, "_log_query", capture_log)

    service = SqlExplorerService(rate_limiter=_DummyRateLimiter())

    # success
    monkeypatch.setattr(module, "_create_query_connection", lambda dataset, available_tables: _DummyConn())
    await service.execute_query(operator_user, "crsp", "SELECT * FROM crsp_daily")

    # authorization_denied
    with pytest.raises(PermissionError):
        await service.execute_query({"role": "viewer", "user_id": "v"}, "crsp", "SELECT * FROM crsp_daily")

    # validation_error
    monkeypatch.setattr(service._validator, "validate", lambda query, dataset: (False, "bad query"))
    with pytest.raises(ValueError, match="bad query"):
        await service.execute_query(operator_user, "crsp", "SELECT * FROM crsp_daily")

    monkeypatch.setattr(service._validator, "validate", lambda query, dataset: (True, None))

    # security_blocked
    monkeypatch.setattr(service._validator, "extract_tables", lambda query: ["users"])
    with pytest.raises(SensitiveTableAccessError):
        await service.execute_query(operator_user, "crsp", "SELECT * FROM crsp_daily")
    monkeypatch.setattr(service._validator, "extract_tables", lambda query: ["crsp_daily"])

    # rate_limited
    service_rl = SqlExplorerService(rate_limiter=_DummyRateLimiter(allowed=False))
    with pytest.raises(RateLimitExceededError):
        await service_rl.execute_query(operator_user, "crsp", "SELECT * FROM crsp_daily")

    # concurrency_limit
    async def raise_concurrency(conn: Any, sql: str, timeout_seconds: int) -> pl.DataFrame:
        del conn, sql, timeout_seconds
        raise ConcurrencyLimitError("busy")

    monkeypatch.setattr(module, "_execute_query_with_timeout", raise_concurrency)
    with pytest.raises(ConcurrencyLimitError):
        await service.execute_query(operator_user, "crsp", "SELECT * FROM crsp_daily")

    # timeout
    async def raise_timeout(conn: Any, sql: str, timeout_seconds: int) -> pl.DataFrame:
        del conn, sql, timeout_seconds
        raise TimeoutError

    monkeypatch.setattr(module, "_execute_query_with_timeout", raise_timeout)
    with pytest.raises(TimeoutError):
        await service.execute_query(operator_user, "crsp", "SELECT * FROM crsp_daily")

    # error
    monkeypatch.setattr(module, "_create_query_connection", lambda dataset, available_tables: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        await service.execute_query(operator_user, "crsp", "SELECT * FROM crsp_daily")

    assert set(statuses) >= {
        "success",
        "authorization_denied",
        "validation_error",
        "security_blocked",
        "rate_limited",
        "concurrency_limit",
        "timeout",
        "error",
    }


@pytest.mark.asyncio()
async def test_export_csv_permission_and_dataset_checks(
    operator_user: dict[str, str],
    viewer_user: dict[str, str],
) -> None:
    service = SqlExplorerService(rate_limiter=_DummyRateLimiter())
    df = pl.DataFrame({"a": [1]})

    with pytest.raises(PermissionError):
        await service.export_csv(viewer_user, "crsp", df)

    csv_bytes = await service.export_csv(operator_user, "crsp", df)
    assert b"a" in csv_bytes


@pytest.mark.asyncio()
async def test_export_csv_rate_limited(operator_user: dict[str, str]) -> None:
    service = SqlExplorerService(rate_limiter=_DummyRateLimiter(allowed=False))
    with pytest.raises(RateLimitExceededError):
        await service.export_csv(operator_user, "crsp", pl.DataFrame({"a": [1]}))


def test_verify_sandbox_missing_dir_not_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """FileNotFoundError from missing probe directory should not count as failure."""
    import socket as _socket

    # Point forbidden paths to a directory that doesn't exist
    nonexistent = tmp_path / "does_not_exist"
    monkeypatch.setattr(module, "_SQL_EXPLORER_SANDBOX_SKIP", False)
    monkeypatch.setenv("SQL_EXPLORER_FORBIDDEN_WRITE_PATHS", str(nonexistent))

    # Mock network probe to always fail (simulate blocked egress)
    def _blocked_connect(*_args: object, **_kwargs: object) -> None:
        raise OSError("blocked")

    monkeypatch.setattr(_socket, "create_connection", _blocked_connect)

    safe, failures = _verify_sandbox()
    # Missing directory = not writable = should be fully safe
    assert safe is True
    assert failures == []


def test_verify_sandbox_writable_dir_is_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """A writable directory should be reported as a sandbox failure."""
    import socket as _socket

    monkeypatch.setattr(module, "_SQL_EXPLORER_SANDBOX_SKIP", False)
    monkeypatch.setenv("SQL_EXPLORER_FORBIDDEN_WRITE_PATHS", str(tmp_path))

    def _blocked_connect(*_args: object, **_kwargs: object) -> None:
        raise OSError("blocked")

    monkeypatch.setattr(_socket, "create_connection", _blocked_connect)

    safe, failures = _verify_sandbox()
    assert safe is False
    assert len(failures) == 1
    assert "filesystem_write_allowed" in failures[0]


def test_log_query_no_raw_sql_by_default(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO):
        module._log_query(
            user={"role": "operator", "user_id": "u1"},
            dataset="crsp",
            original_query="SELECT * FROM crsp_daily WHERE symbol='AAPL'",
            executed_query="SELECT * FROM crsp_daily WHERE symbol='AAPL' LIMIT 10",
            row_count=1,
            execution_ms=10,
            status="success",
            error_message=None,
        )

    record = next(r for r in caplog.records if r.msg == "sql_query_executed")
    assert hasattr(record, "query_fingerprint")
    assert not hasattr(record, "original_query")
