"""Unit tests for SQL Explorer service (P6T14/T14.1)."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections.abc import Callable
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


def _raw_path_spec(path_spec: module.TablePathSpec) -> str | tuple[str, ...]:
    if isinstance(path_spec, module.ResolvedTablePathSpec):
        return path_spec.path_spec
    return path_spec


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


async def _wait_until(predicate: Callable[[], bool], *, interval: float = 0.01) -> None:
    while not predicate():
        await asyncio.sleep(interval)


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


def test_path_validation_rejects_traversal_and_quotes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
    assert _safe_error_message("queued") is None


def test_fingerprint_query_replaces_literals_and_handles_unparseable() -> None:
    fingerprint = _fingerprint_query("SELECT * FROM crsp_daily WHERE symbol='AAPL' AND px > 12")
    assert "AAPL" not in fingerprint
    assert "12" not in fingerprint
    assert "?" in fingerprint

    assert _fingerprint_query("SELECT FROM") == "<unparseable query>"


def test_create_query_connection_sets_extension_lockdown(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResult:
        def fetchall(self) -> list[tuple[str]]:
            return [("core_functions",), ("jemalloc",), ("parquet",)]

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

    assert "LOAD core_functions" in fake_conn.statements
    assert "LOAD icu" in fake_conn.statements
    assert "LOAD jemalloc" not in fake_conn.statements
    assert "LOAD json" in fake_conn.statements
    assert "LOAD parquet" in fake_conn.statements
    assert "SET enable_extension_loading = false" in fake_conn.statements
    assert "SET enable_extension_autoloading = false" in fake_conn.statements
    assert "SET autoinstall_known_extensions = false" in fake_conn.statements
    assert "SET autoload_known_extensions = false" in fake_conn.statements
    assert "SET allow_unsigned_extensions = false" in fake_conn.statements
    assert "SET allow_community_extensions = false" in fake_conn.statements
    assert fake_conn.statements.index("SET autoinstall_known_extensions = false") < (
        fake_conn.statements.index("LOAD parquet")
    )
    assert fake_conn.statements.index("LOAD parquet") < (
        fake_conn.statements.index("SET enable_extension_loading = false")
    )


def test_pinned_duckdb_accepts_required_extension_lockdown_options() -> None:
    conn = module.duckdb.connect()
    try:
        conn.execute("SET autoinstall_known_extensions = false")
        conn.execute("SET autoload_known_extensions = false")
        conn.execute("SET allow_unsigned_extensions = false")
        conn.execute("SET allow_community_extensions = false")
    finally:
        conn.close()


def test_create_query_connection_allows_removed_legacy_lockdown_options_in_production(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data"
    partition = data_root / "wrds" / "crsp" / "daily" / "part.parquet"
    partition.parent.mkdir(parents=True)
    pl.DataFrame({"a": [1]}).write_parquet(partition)
    monkeypatch.setattr(module, "_APP_ENV", "production")
    monkeypatch.setattr(module, "_ALLOWED_DATA_ROOTS", [data_root.resolve()])
    monkeypatch.setattr(
        module,
        "_resolve_table_paths",
        lambda: {"crsp_daily": str(partition)},
    )

    conn = module._create_query_connection("crsp", available_tables={"crsp_daily"})
    try:
        result = conn.execute("SELECT count(*) FROM crsp_daily").fetchone()
    finally:
        conn.close()

    assert result == (1,)


def test_validate_table_paths_detects_alpaca_sip_snapshot_partition(
    tmp_path: Path,
) -> None:
    snapshot_dir = tmp_path / "data" / "alpaca" / "sip" / "daily" / "snapshots" / "sync-1"
    snapshot_dir.mkdir(parents=True)
    (snapshot_dir / "2024.parquet").write_bytes(b"PAR1")

    available, warnings = module._validate_table_paths(
        {
            "alpaca_sip_daily": str(
                tmp_path / "data" / "alpaca" / "sip" / "daily" / "snapshots" / "*" / "*.parquet"
            )
        }
    )

    assert available["alpaca_sip"] == {"alpaca_sip_daily"}
    assert not any("alpaca_sip_daily" in warning for warning in warnings)


def test_resolve_sql_table_availability_marks_alpaca_fallback_untrusted(
    tmp_path: Path,
) -> None:
    partition = (
        tmp_path / "data" / "alpaca" / "sip" / "daily" / "snapshots" / "fallback" / "2026.parquet"
    )
    partition.parent.mkdir(parents=True)
    partition.write_bytes(b"PAR1")

    resolutions, warnings = module.resolve_sql_table_availability(
        {"alpaca_sip_daily": str(partition)}
    )

    daily = resolutions["alpaca_sip_daily"]
    assert daily.available is True
    assert daily.fallback_only is True
    assert daily.manifest_backed is False
    assert daily.trusted_for_data_page is False
    assert any("queryable fallback only" in warning for warning in warnings)


def test_resolve_sql_table_availability_marks_manifest_pinned_alpaca_trusted(
    tmp_path: Path,
) -> None:
    partition = (
        tmp_path / "data" / "alpaca" / "sip" / "daily" / "snapshots" / "sync-1" / "2026.parquet"
    )
    partition.parent.mkdir(parents=True)
    partition.write_bytes(b"PAR1")

    resolutions, warnings = module.resolve_sql_table_availability(
        {"alpaca_sip_daily": (str(partition),)}
    )

    daily = resolutions["alpaca_sip_daily"]
    assert daily.available is True
    assert daily.fallback_only is False
    assert daily.manifest_backed is True
    assert daily.trusted_for_data_page is True
    assert not any("queryable fallback only" in warning for warning in warnings)


def test_resolve_sql_table_availability_marks_invalid_manifest_untrusted(
    tmp_path: Path,
) -> None:
    partition = (
        tmp_path / "data" / "alpaca" / "sip" / "daily" / "snapshots" / "sync-1" / "2026.parquet"
    )
    partition.parent.mkdir(parents=True)
    partition.write_bytes(b"PAR1")

    resolutions, warnings = module.resolve_sql_table_availability(
        {
            "alpaca_sip_daily": module.ResolvedTablePathSpec(
                path_spec=(str(partition),),
                manifest_backed=True,
                manifest_invalid=True,
            )
        }
    )

    daily = resolutions["alpaca_sip_daily"]
    assert daily.available is True
    assert daily.manifest_backed is True
    assert daily.manifest_invalid is True
    assert daily.trusted_for_data_page is False
    assert any("manifest is invalid" in warning for warning in warnings)


def test_resolve_sql_table_availability_honors_empty_path_override() -> None:
    resolutions, warnings = module.resolve_sql_table_availability({})

    assert resolutions
    assert all(not resolution.available for resolution in resolutions.values())
    assert any("No Parquet files found" in warning for warning in warnings)


def test_resolve_table_paths_uses_alpaca_manifest_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path
    data_root = project_root / "data"
    storage_root = data_root / "alpaca" / "sip" / "daily"
    partition = storage_root / "snapshots" / "sync-1" / "2024.parquet"
    partition.parent.mkdir(parents=True)
    partition.write_bytes(b"PAR1")
    corp_storage_root = data_root / "alpaca" / "sip" / "corp_actions"
    corp_partition = corp_storage_root / "snapshots" / "sync-1" / "corporate_actions.parquet"
    corp_partition.parent.mkdir(parents=True)
    corp_partition.write_bytes(b"PAR1")
    manifest_dir = data_root / "manifests"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "alpaca_sip_daily.json").write_text(
        json.dumps({"file_paths": [str(partition)], "validation_status": "passed"}),
        encoding="utf-8",
    )
    (manifest_dir / "alpaca_sip_corp_actions.json").write_text(
        json.dumps({"file_paths": [str(corp_partition)], "validation_status": "passed"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "_PROJECT_ROOT", project_root)

    paths = module._resolve_table_paths()

    assert _raw_path_spec(paths["alpaca_sip_daily"]) == (str(partition),)
    assert _raw_path_spec(paths["alpaca_sip_corp_actions"]) == (str(corp_partition),)
    assert isinstance(paths["alpaca_sip_daily"], module.ResolvedTablePathSpec)
    assert paths["alpaca_sip_daily"].manifest_backed is True


def test_resolve_table_paths_uses_nested_relative_manifest_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path
    data_root = project_root / "data"
    storage_root = data_root / "alpaca" / "sip" / "daily"
    partition = storage_root / "snapshots" / "sync-1" / "2024.parquet"
    partition.parent.mkdir(parents=True)
    partition.write_bytes(b"PAR1")
    manifest_dir = data_root / "manifests"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "alpaca_sip_daily.json").write_text(
        json.dumps(
            {"file_paths": ["snapshots/sync-1/2024.parquet"], "validation_status": "passed"}
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "_PROJECT_ROOT", project_root)

    paths = module._resolve_table_paths()

    assert _raw_path_spec(paths["alpaca_sip_daily"]) == (str(partition),)


def test_resolve_table_paths_rejects_partially_invalid_alpaca_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    project_root = tmp_path
    data_root = project_root / "data"
    storage_root = data_root / "alpaca" / "sip" / "daily"
    partition = storage_root / "snapshots" / "sync-1" / "2024.parquet"
    partition.parent.mkdir(parents=True)
    partition.write_bytes(b"PAR1")
    manifest_dir = data_root / "manifests"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "alpaca_sip_daily.json").write_text(
        json.dumps(
            {
                "file_paths": [
                    "snapshots/sync-1/2024.parquet",
                    "snapshots/sync-1/missing.parquet",
                ],
                "validation_status": "passed",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "_PROJECT_ROOT", project_root)

    with caplog.at_level(logging.WARNING):
        paths = module._resolve_table_paths()

    daily = paths["alpaca_sip_daily"]
    assert _raw_path_spec(daily) == ()
    assert isinstance(daily, module.ResolvedTablePathSpec)
    assert daily.manifest_invalid is True
    assert daily.manifest_backed is False
    assert "sql_explorer_alpaca_sip_manifest_invalid_paths" in caplog.text


def test_resolve_table_paths_rejects_failed_alpaca_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    project_root = tmp_path
    data_root = project_root / "data"
    storage_root = data_root / "alpaca" / "sip" / "daily"
    partition = storage_root / "snapshots" / "sync-1" / "2024.parquet"
    partition.parent.mkdir(parents=True)
    partition.write_bytes(b"PAR1")
    manifest_dir = data_root / "manifests"
    manifest_dir.mkdir(parents=True)
    manifest_path = manifest_dir / "alpaca_sip_daily.json"
    manifest_path.write_text(
        json.dumps({"file_paths": [str(partition)], "validation_status": "failed"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "_PROJECT_ROOT", project_root)
    module._ALPACA_SIP_MANIFEST_PATH_CACHE.clear()

    with caplog.at_level(logging.WARNING):
        paths = module._resolve_table_paths()

    daily = paths["alpaca_sip_daily"]
    assert _raw_path_spec(daily) == ()
    assert isinstance(daily, module.ResolvedTablePathSpec)
    assert daily.manifest_invalid is True
    assert daily.manifest_backed is False
    assert "sql_explorer_alpaca_sip_manifest_untrusted" in caplog.text
    assert str(manifest_path.resolve()) in module._ALPACA_SIP_MANIFEST_PATH_CACHE


def test_resolve_table_paths_marks_missing_alpaca_manifest_status_fallback_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    project_root = tmp_path
    data_root = project_root / "data"
    storage_root = data_root / "alpaca" / "sip" / "daily"
    partition = storage_root / "snapshots" / "sync-1" / "2024.parquet"
    partition.parent.mkdir(parents=True)
    partition.write_bytes(b"PAR1")
    manifest_dir = data_root / "manifests"
    manifest_dir.mkdir(parents=True)
    manifest_path = manifest_dir / "alpaca_sip_daily.json"
    manifest_path.write_text(
        json.dumps({"file_paths": [str(partition)]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "_PROJECT_ROOT", project_root)
    module._ALPACA_SIP_MANIFEST_PATH_CACHE.clear()

    with caplog.at_level(logging.WARNING):
        paths = module._resolve_table_paths()

    daily = paths["alpaca_sip_daily"]
    assert _raw_path_spec(daily) == f"{storage_root.resolve()}/snapshots/*/*.parquet"
    assert isinstance(daily, module.ResolvedTablePathSpec)
    assert daily.manifest_invalid is False
    assert daily.fallback_only is True
    assert daily.manifest_backed is False
    assert any(getattr(record, "validation_status", None) == "unknown" for record in caplog.records)
    assert str(manifest_path.resolve()) in module._ALPACA_SIP_MANIFEST_PATH_CACHE

    resolutions, warnings = module.resolve_sql_table_availability({"alpaca_sip_daily": daily})
    assert resolutions["alpaca_sip_daily"].available is True
    assert not any("manifest is invalid" in warning for warning in warnings)
    assert any("queryable fallback only" in warning for warning in warnings)


def test_resolve_table_paths_fail_closed_on_unreadable_alpaca_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    project_root = tmp_path
    data_root = project_root / "data"
    fallback_partition = (
        data_root / "alpaca" / "sip" / "daily" / "snapshots" / "old" / "2024.parquet"
    )
    fallback_partition.parent.mkdir(parents=True)
    fallback_partition.write_bytes(b"PAR1")
    manifest_dir = data_root / "manifests"
    manifest_dir.mkdir(parents=True)
    manifest_path = manifest_dir / "alpaca_sip_daily.json"
    manifest_path.write_text("{not-json", encoding="utf-8")
    monkeypatch.setattr(module, "_PROJECT_ROOT", project_root)
    module._ALPACA_SIP_MANIFEST_PATH_CACHE.clear()

    with caplog.at_level(logging.WARNING):
        paths = module._resolve_table_paths()

    assert _raw_path_spec(paths["alpaca_sip_daily"]) == ()
    assert isinstance(paths["alpaca_sip_daily"], module.ResolvedTablePathSpec)
    assert paths["alpaca_sip_daily"].manifest_invalid is True
    assert "sql_explorer_alpaca_sip_daily_manifest_unreadable" in caplog.text
    assert str(manifest_path.resolve()) in module._ALPACA_SIP_MANIFEST_PATH_CACHE

    caplog.clear()
    with caplog.at_level(logging.WARNING):
        cached_paths = module._resolve_table_paths()

    assert _raw_path_spec(cached_paths["alpaca_sip_daily"]) == ()
    assert "sql_explorer_alpaca_sip_daily_manifest_unreadable" not in caplog.text

    monkeypatch.setattr(module, "_ALPACA_SIP_MANIFEST_FAILURE_CACHE_TTL_SECONDS", 0.0)
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        expired_paths = module._resolve_table_paths()

    assert _raw_path_spec(expired_paths["alpaca_sip_daily"]) == ()
    assert "sql_explorer_alpaca_sip_daily_manifest_unreadable" in caplog.text


def test_resolve_table_paths_handles_manifest_stat_race(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    project_root = tmp_path
    data_root = project_root / "data"
    manifest_dir = data_root / "manifests"
    manifest_dir.mkdir(parents=True)
    manifest_path = manifest_dir / "alpaca_sip_daily.json"
    manifest_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(module, "_PROJECT_ROOT", project_root)
    module._ALPACA_SIP_MANIFEST_PATH_CACHE.clear()

    original_exists = Path.exists
    original_stat = Path.stat

    def racing_exists(path: Path) -> bool:
        if path == manifest_path:
            return True
        return original_exists(path)

    def disappearing_stat(path: Path, *args: Any, **kwargs: Any) -> Any:
        if path == manifest_path:
            raise FileNotFoundError("manifest disappeared")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "exists", racing_exists)
    monkeypatch.setattr(Path, "stat", disappearing_stat)

    with caplog.at_level(logging.WARNING):
        paths = module._resolve_table_paths()

    daily = paths["alpaca_sip_daily"]
    assert _raw_path_spec(daily) == ()
    assert isinstance(daily, module.ResolvedTablePathSpec)
    assert daily.manifest_invalid is True
    assert "sql_explorer_alpaca_sip_daily_manifest_unreadable" in caplog.text
    assert str(manifest_path.resolve()) not in module._ALPACA_SIP_MANIFEST_PATH_CACHE


def test_resolve_table_paths_fail_closed_on_non_object_alpaca_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    project_root = tmp_path
    data_root = project_root / "data"
    fallback_partition = (
        data_root / "alpaca" / "sip" / "daily" / "snapshots" / "old" / "2024.parquet"
    )
    fallback_partition.parent.mkdir(parents=True)
    fallback_partition.write_bytes(b"PAR1")
    manifest_dir = data_root / "manifests"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "alpaca_sip_daily.json").write_text("[]", encoding="utf-8")
    monkeypatch.setattr(module, "_PROJECT_ROOT", project_root)

    with caplog.at_level(logging.WARNING):
        paths = module._resolve_table_paths()

    assert _raw_path_spec(paths["alpaca_sip_daily"]) == ()
    assert isinstance(paths["alpaca_sip_daily"], module.ResolvedTablePathSpec)
    assert paths["alpaca_sip_daily"].manifest_invalid is True
    assert "sql_explorer_alpaca_sip_daily_manifest_unreadable" in caplog.text
    assert any(
        getattr(record, "error", None) == "Manifest JSON is not an object"
        for record in caplog.records
    )


def test_alpaca_manifest_path_cache_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    module._ALPACA_SIP_MANIFEST_PATH_CACHE.clear()
    monkeypatch.setattr(module, "_MAX_ALPACA_SIP_MANIFEST_PATH_CACHE_ENTRIES", 2)

    try:
        for index in range(3):
            module._cache_alpaca_sip_manifest_path(
                f"manifest-{index}",
                module._ManifestPathCacheEntry(
                    mtime_ns=index,
                    size=index,
                    path_spec=(f"/tmp/partition-{index}.parquet",),
                ),
            )

        assert list(module._ALPACA_SIP_MANIFEST_PATH_CACHE) == [
            "manifest-1",
            "manifest-2",
        ]
    finally:
        module._ALPACA_SIP_MANIFEST_PATH_CACHE.clear()


def test_create_query_connection_handles_manifest_path_list(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeResult:
        def fetchall(self) -> list[tuple[str]]:
            return [("core_functions",), ("jemalloc",), ("parquet",)]

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

    data_root = tmp_path / "data"
    partition = data_root / "alpaca" / "sip" / "daily" / "snapshots" / "sync-1" / "2024.parquet"
    partition.parent.mkdir(parents=True)
    partition.write_bytes(b"PAR1")
    corp_partition = (
        data_root
        / "alpaca"
        / "sip"
        / "corp_actions"
        / "snapshots"
        / "sync-1"
        / "corporate_actions.parquet"
    )
    corp_partition.parent.mkdir(parents=True)
    corp_partition.write_bytes(b"PAR1")
    fake_conn = FakeConn()
    monkeypatch.setattr(module.duckdb, "connect", lambda: fake_conn)
    monkeypatch.setattr(module, "_ALLOWED_DATA_ROOTS", [data_root.resolve()])
    monkeypatch.setattr(
        module,
        "_resolve_table_paths",
        lambda: {
            "alpaca_sip_daily": (str(partition),),
            "alpaca_sip_corp_actions": (str(corp_partition),),
        },
    )

    module._create_query_connection(
        "alpaca_sip",
        available_tables={"alpaca_sip_daily", "alpaca_sip_corp_actions"},
    )

    view_statements = [stmt for stmt in fake_conn.statements if "CREATE OR REPLACE VIEW" in stmt]
    assert len(view_statements) == 2
    assert any(f"read_parquet(['{partition}'])" in stmt for stmt in view_statements)
    assert any(f"read_parquet(['{corp_partition}'])" in stmt for stmt in view_statements)


def test_can_query_dataset_default_deny_for_unmapped(operator_user: dict[str, str]) -> None:
    assert can_query_dataset(operator_user, "crsp") is True
    assert can_query_dataset(operator_user, "alpaca_sip") is True

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


def test_execution_allowed_requires_attestation_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(module, "_APP_ENV", "production")
    monkeypatch.setenv("SQL_EXPLORER_DEPLOY_ATTESTED", "false")
    with pytest.raises(RuntimeError, match="deploy-attested"):
        module.ensure_sql_explorer_execution_allowed()


def test_service_init_defers_attestation_probe_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(module, "_APP_ENV", "production")
    monkeypatch.setenv("SQL_EXPLORER_DEPLOY_ATTESTED", "false")
    monkeypatch.setattr(
        module,
        "_verify_sandbox",
        lambda: pytest.fail("constructor should not run sandbox probe"),
    )

    svc = SqlExplorerService(rate_limiter=_DummyRateLimiter())

    assert svc is not None


def test_create_scoped_query_connection_requires_attestation_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(module, "_APP_ENV", "production")
    monkeypatch.setenv("SQL_EXPLORER_DEPLOY_ATTESTED", "false")
    with pytest.raises(RuntimeError, match="deploy-attested"):
        module.create_scoped_query_connection("crsp", available_tables=set())


def test_service_init_rate_limiter_guards(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "_IS_DEV_MODE", False)
    with pytest.raises(ValueError, match="requires a RateLimiter"):
        SqlExplorerService(rate_limiter=None)

    with pytest.raises(ValueError, match="fallback_mode='deny'"):
        SqlExplorerService(rate_limiter=_DummyRateLimiter(fallback_mode="allow"))


def test_execution_allowed_dev_mode_forbidden_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(module, "_APP_ENV", "production")
    monkeypatch.setattr(module, "_IS_DEV_MODE", True)
    monkeypatch.setenv("SQL_EXPLORER_DEPLOY_ATTESTED", "true")
    with pytest.raises(RuntimeError, match="forbidden"):
        module.ensure_sql_explorer_execution_allowed()


@pytest.mark.asyncio()
async def test_execute_query_viewer_allowed_single_admin(
    viewer_user: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """P6T19: Viewer can execute queries — single-admin model."""
    service = SqlExplorerService(rate_limiter=_DummyRateLimiter())
    monkeypatch.setattr(
        module, "_create_query_connection", lambda dataset, available_tables: _DummyConn()
    )

    result = await service.execute_query(viewer_user, "crsp", "SELECT * FROM crsp_daily")
    assert result is not None


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

    await asyncio.wait_for(_wait_until(lambda: conn.interrupt_called), timeout=1.0)
    await asyncio.wait_for(_wait_until(lambda: conn.closed), timeout=1.0)


@pytest.mark.asyncio()
async def test_execute_query_timeout_returns_before_worker_cleanup(
    operator_user: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    class SlowConn(_DummyConn):
        def __init__(self) -> None:
            super().__init__()
            self.finished = threading.Event()

        def execute(self, sql: str) -> _DummyConn:
            del sql
            time.sleep(0.2)
            self.finished.set()
            return self

    service = SqlExplorerService(rate_limiter=_DummyRateLimiter())
    conn = SlowConn()
    monkeypatch.setattr(module, "_create_query_connection", lambda dataset, available_tables: conn)

    started = time.monotonic()
    with pytest.raises(TimeoutError):
        await service.execute_query(
            operator_user,
            "crsp",
            "SELECT * FROM crsp_daily",
            timeout_seconds=0.01,
            available_tables={"crsp_daily"},
        )
    elapsed = time.monotonic() - started

    assert elapsed < 0.15
    await asyncio.wait_for(_wait_until(lambda: conn.interrupt_called), timeout=1.0)
    assert conn.finished.is_set() is False
    assert await asyncio.to_thread(conn.finished.wait, 1.0)
    assert conn.closed is True


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
async def test_execute_query_audit_statuses(
    operator_user: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
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
    monkeypatch.setattr(
        module, "_create_query_connection", lambda dataset, available_tables: _DummyConn()
    )
    await service.execute_query(operator_user, "crsp", "SELECT * FROM crsp_daily")

    # P6T19: authorization_denied path removed — has_permission always True

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

    original_execute_frame = module.execute_scoped_query_frame_with_timeout

    # concurrency_limit
    async def raise_concurrency(
        dataset: str,
        sql: str,
        timeout_seconds: int,
        *,
        available_tables: set[str],
        table_paths: dict[str, module.TablePathSpec] | None = None,
    ) -> pl.DataFrame:
        del dataset, sql, timeout_seconds, available_tables, table_paths
        raise ConcurrencyLimitError("busy")

    monkeypatch.setattr(module, "execute_scoped_query_frame_with_timeout", raise_concurrency)
    with pytest.raises(ConcurrencyLimitError):
        await service.execute_query(operator_user, "crsp", "SELECT * FROM crsp_daily")

    # timeout
    async def raise_timeout(
        dataset: str,
        sql: str,
        timeout_seconds: int,
        *,
        available_tables: set[str],
        table_paths: dict[str, module.TablePathSpec] | None = None,
    ) -> pl.DataFrame:
        del dataset, sql, timeout_seconds, available_tables, table_paths
        raise TimeoutError

    monkeypatch.setattr(module, "execute_scoped_query_frame_with_timeout", raise_timeout)
    with pytest.raises(TimeoutError):
        await service.execute_query(operator_user, "crsp", "SELECT * FROM crsp_daily")
    monkeypatch.setattr(
        module,
        "execute_scoped_query_frame_with_timeout",
        original_execute_frame,
    )

    # error
    monkeypatch.setattr(
        module,
        "_create_query_connection",
        lambda dataset, available_tables: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    with pytest.raises(RuntimeError):
        await service.execute_query(operator_user, "crsp", "SELECT * FROM crsp_daily")

    assert set(statuses) >= {
        "success",
        # P6T19: "authorization_denied" removed — has_permission always True
        "validation_error",
        "security_blocked",
        "rate_limited",
        "concurrency_limit",
        "timeout",
        "error",
    }


@pytest.mark.asyncio()
async def test_export_csv_all_users_allowed_single_admin(
    operator_user: dict[str, str],
    viewer_user: dict[str, str],
) -> None:
    """P6T19: All users can export CSV — single-admin model."""
    service = SqlExplorerService(rate_limiter=_DummyRateLimiter())
    df = pl.DataFrame({"a": [1]})

    # Viewer can export (single-admin: all permissions granted)
    csv_bytes_viewer = await service.export_csv(viewer_user, "crsp", df)
    assert b"a" in csv_bytes_viewer

    csv_bytes = await service.export_csv(operator_user, "crsp", df)
    assert b"a" in csv_bytes


@pytest.mark.asyncio()
async def test_export_csv_rate_limited(operator_user: dict[str, str]) -> None:
    service = SqlExplorerService(rate_limiter=_DummyRateLimiter(allowed=False))
    with pytest.raises(RateLimitExceededError):
        await service.export_csv(operator_user, "crsp", pl.DataFrame({"a": [1]}))


def test_verify_sandbox_missing_dir_not_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
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
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
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


def test_ensure_execution_allowed_rechecks_sandbox_after_ttl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def fake_verify() -> tuple[bool, list[str]]:
        nonlocal calls
        calls += 1
        return True, []

    times = iter([10.0, 10.5, 71.0])
    monkeypatch.setattr(module, "_sandbox_probe_result", None)
    monkeypatch.setattr(module, "_SANDBOX_PROBE_CACHE_TTL_SECONDS", 60.0)
    monkeypatch.setattr(module, "_verify_sandbox", fake_verify)
    monkeypatch.setattr(module.time, "monotonic", lambda: next(times))

    module.ensure_sql_explorer_execution_allowed()
    module.ensure_sql_explorer_execution_allowed()
    module.ensure_sql_explorer_execution_allowed()

    assert calls == 2


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
