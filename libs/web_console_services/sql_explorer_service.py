"""SQL Explorer service with defense-in-depth query controls."""

from __future__ import annotations

import asyncio
import glob as _glob_module
import json
import logging
import os
import re
import socket
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path as _Path
from typing import Any
from uuid import uuid4

import duckdb
import polars as pl
import sqlglot
from pydantic import BaseModel, ConfigDict
from sqlglot import exp

from libs.data.data_providers.alpaca_sip_paths import resolve_alpaca_sip_manifest_path
from libs.platform.web_console_auth.helpers import get_user_id
from libs.platform.web_console_auth.permissions import (
    DatasetPermission,
    Permission,
    has_dataset_permission,
    has_permission,
)
from libs.platform.web_console_auth.rate_limiter import RateLimiter
from libs.web_console_services.sql_validator import DATASET_TABLES, SQLValidator

logger = logging.getLogger(__name__)
_audit_logger = logging.getLogger("sql_explorer.audit")

_DEFAULT_TIMEOUT_SECONDS = 30
_MAX_TIMEOUT_SECONDS = 120
_DEFAULT_MAX_ROWS = 10_000
_MAX_ROWS_LIMIT = 50_000
_MAX_CONCURRENT_QUERIES = 3
_QUERY_RATE_LIMIT = 10
_EXPORT_RATE_LIMIT = 5
_MAX_CELLS = 1_000_000
_MAX_ALPACA_SIP_MANIFEST_PATH_CACHE_ENTRIES = 64
_ALPACA_SIP_MANIFEST_FAILURE_CACHE_TTL_SECONDS = 5.0
_DUCKDB_LOCKDOWN_MIN_VERSION = (1, 4, 0)
_SANDBOX_PROBE_CACHE_TTL_SECONDS = 60.0
_DUCKDB_LOADABLE_EXTENSIONS = frozenset({"core_functions", "icu", "json", "parquet"})
_DUCKDB_ALLOWED_LOADED_EXTENSIONS = _DUCKDB_LOADABLE_EXTENSIONS | frozenset({"jemalloc"})
_DUCKDB_DENIED_EXTENSIONS = frozenset(
    {"httpfs", "postgres_scanner", "sqlite_scanner", "mysql_scanner", "azure", "aws"}
)
_RawTablePathSpec = str | tuple[str, ...]


class ResolvedTablePathSpec(BaseModel, frozen=True):
    """Physical table path plus explicit provenance for trust decisions."""

    path_spec: _RawTablePathSpec
    manifest_backed: bool = False
    fallback_only: bool = False
    manifest_invalid: bool = False


_TablePathSpec = _RawTablePathSpec | ResolvedTablePathSpec
TablePathSpec = _TablePathSpec

_ALPACA_SIP_TABLE_MANIFESTS: dict[str, str] = {
    "alpaca_sip_daily": "alpaca_sip_daily.json",
    "alpaca_sip_corp_actions": "alpaca_sip_corp_actions.json",
}


class _ManifestPathCacheEntry(BaseModel, frozen=True):
    mtime_ns: int
    size: int
    path_spec: _TablePathSpec
    failure_cached_at: float | None = None


class SqlTableResolution(BaseModel, frozen=True):
    """Resolved local table state for SQL-backed data surfaces."""

    dataset: str
    table: str
    path_spec: TablePathSpec | None
    available: bool
    manifest_required: bool
    manifest_backed: bool
    manifest_invalid: bool
    fallback_only: bool
    trusted_for_data_page: bool


_ALPACA_SIP_MANIFEST_PATH_CACHE: OrderedDict[str, _ManifestPathCacheEntry] = OrderedDict()
_ALPACA_SIP_MANIFEST_PATH_CACHE_LOCK = threading.Lock()


def _cache_alpaca_sip_manifest_path(
    cache_key: str,
    entry: _ManifestPathCacheEntry,
) -> None:
    """Store a manifest cache entry while bounding global cache growth."""
    with _ALPACA_SIP_MANIFEST_PATH_CACHE_LOCK:
        _ALPACA_SIP_MANIFEST_PATH_CACHE[cache_key] = entry
        _ALPACA_SIP_MANIFEST_PATH_CACHE.move_to_end(cache_key)
        while len(_ALPACA_SIP_MANIFEST_PATH_CACHE) > _MAX_ALPACA_SIP_MANIFEST_PATH_CACHE_ENTRIES:
            _ALPACA_SIP_MANIFEST_PATH_CACHE.popitem(last=False)


def _cache_resolved_alpaca_sip_manifest_path(
    cache_key: str,
    manifest_stat: os.stat_result,
    path_spec: _TablePathSpec,
    *,
    failure_cache: bool = False,
) -> _TablePathSpec:
    _cache_alpaca_sip_manifest_path(
        cache_key,
        _ManifestPathCacheEntry(
            mtime_ns=manifest_stat.st_mtime_ns,
            size=manifest_stat.st_size,
            path_spec=path_spec,
            failure_cached_at=time.monotonic() if failure_cache else None,
        ),
    )
    return path_spec


_KNOWN_ENVS = {"production", "staging", "development", "test", "local"}

_ERROR_CODES: dict[str, str] = {
    "validation_error": "Query failed validation",
    "authorization_denied": "Dataset access denied",
    "security_blocked": "Restricted table access denied",
    "rate_limited": "Query rate limit exceeded",
    "timeout": "Query execution timed out",
    "concurrency_limit": "Too many concurrent queries",
    "error": "Query execution failed",
}
_NON_ERROR_AUDIT_STATUSES = {"success", "queued"}

_SENSITIVE_TABLES_EXACT = frozenset(
    {
        "users",
        "api_keys",
        "secrets",
        "sessions",
        "credentials",
        "auth_tokens",
        "password_hashes",
    }
)
_SENSITIVE_TABLE_PREFIXES = (
    "user",
    "auth",
    "secret",
    "credential",
    "password",
    "session",
    "api_key",
)

_DATASET_PERMISSION_MAP: dict[str, DatasetPermission] = {
    "crsp": DatasetPermission.CRSP_ACCESS,
    "compustat": DatasetPermission.COMPUSTAT_ACCESS,
    "taq": DatasetPermission.TAQ_ACCESS,
    "fama_french": DatasetPermission.FAMA_FRENCH_ACCESS,
    "alpaca_sip": DatasetPermission.ALPACA_SIP_ACCESS,
}

_AUDIT_LOG_RAW_SQL = os.getenv("SQL_EXPLORER_AUDIT_RAW_SQL", "false").lower() == "true"
_AUDIT_RAW_SQL_EMERGENCY_OVERRIDE = (
    os.getenv("SQL_EXPLORER_AUDIT_RAW_SQL_EMERGENCY_OVERRIDE", "false").lower() == "true"
)

_SQL_EXPLORER_SANDBOX_SKIP = os.getenv("SQL_EXPLORER_SANDBOX_SKIP", "").lower() == "true"

# Centralized dev-mode and APP_ENV resolution at module level.
_IS_DEV_MODE = os.getenv("SQL_EXPLORER_DEV_MODE", "").lower() == "true"

# Centralized APP_ENV resolution: default to "local" only when truly unset.
# Unknown values are rejected (fail-closed) to prevent misconfigured production.
_raw_app_env = os.getenv("APP_ENV")
if _raw_app_env is None:
    _APP_ENV = "local"
elif _raw_app_env.lower() in _KNOWN_ENVS:
    _APP_ENV = _raw_app_env.lower()
else:
    raise RuntimeError(
        f"SQL_EXPLORER: APP_ENV='{_raw_app_env}' is not recognized. "
        f"Must be one of {sorted(_KNOWN_ENVS)} or unset (defaults to 'local')."
    )

_PROJECT_ROOT_ENV = os.getenv("PROJECT_ROOT")
if _PROJECT_ROOT_ENV:
    _PROJECT_ROOT = _Path(_PROJECT_ROOT_ENV).resolve()
else:
    _candidate = _Path(__file__).resolve().parent
    _found_marker = False
    while _candidate != _candidate.parent:
        if (_candidate / "pyproject.toml").exists() or (_candidate / ".git").exists():
            _found_marker = True
            break
        _candidate = _candidate.parent

    if _found_marker:
        _PROJECT_ROOT = _candidate
    else:
        if _IS_DEV_MODE and _APP_ENV == "production":
            raise RuntimeError("SQL_EXPLORER_DEV_MODE=true is forbidden when APP_ENV=production.")
        if _IS_DEV_MODE:
            _PROJECT_ROOT = _Path.cwd()
            logger.warning(
                "sql_explorer_project_root_fallback_cwd_dev",
                extra={"cwd": str(_PROJECT_ROOT)},
            )
        else:
            logger.error(
                "sql_explorer_project_root_not_found",
                extra={
                    "hint": ("Set PROJECT_ROOT env var or SQL_EXPLORER_DEV_MODE=true for local dev")
                },
            )
            _PROJECT_ROOT = _Path("/nonexistent")

_ALLOWED_DATA_ROOTS: list[_Path] = [(_PROJECT_ROOT / "data").resolve()]
_DATA_ROOT_AVAILABLE = (_PROJECT_ROOT / "data").is_dir()
if not _DATA_ROOT_AVAILABLE:
    logger.warning(
        "sql_explorer_data_root_missing",
        extra={"expected": str(_PROJECT_ROOT / "data")},
    )

if _AUDIT_LOG_RAW_SQL and _APP_ENV == "production":
    if not _AUDIT_RAW_SQL_EMERGENCY_OVERRIDE:
        raise RuntimeError(
            "SQL_EXPLORER_AUDIT_RAW_SQL=true is forbidden when APP_ENV=production. "
            "Set SQL_EXPLORER_AUDIT_RAW_SQL_EMERGENCY_OVERRIDE=true for emergency investigations."
        )

if _AUDIT_LOG_RAW_SQL:
    logger.warning(
        "sql_explorer_raw_sql_audit_enabled",
        extra={"emergency_override": _AUDIT_RAW_SQL_EMERGENCY_OVERRIDE},
    )

_active_queries = 0
_active_queries_lock = asyncio.Lock()
_sandbox_probe_result: tuple[float, bool, tuple[str, ...]] | None = None
_sandbox_probe_lock = threading.Lock()


class SensitiveTableAccessError(ValueError):
    """Raised when restricted table names are referenced."""


class ConcurrencyLimitError(RuntimeError):
    """Raised when per-process query concurrency cap is reached."""


class RateLimitExceededError(RuntimeError):
    """Raised when a query/export action exceeds rate limit."""


class QueryResult(BaseModel, frozen=True):
    """Typed query result envelope for SQL Explorer UI."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    df: pl.DataFrame
    execution_ms: int
    fingerprint: str


def _safe_error_message(status: str, _raw_error: str | None = None) -> str | None:
    """Return canonical, non-sensitive error messages only.

    Returns None for non-error statuses to avoid misleading audit log entries.
    The _raw_error parameter is intentionally unused — it exists in the
    signature so callers pass it for documentation/audit purposes, but
    its value is never included in the returned message to prevent
    leaking SQL fragments or internal details.
    """
    if status in _NON_ERROR_AUDIT_STATUSES:
        return None
    return _ERROR_CODES.get(status, "Internal execution error")


def safe_sql_error_message(status: str) -> str | None:
    """Return canonical, non-sensitive SQL Explorer audit error text."""
    return _safe_error_message(status)


def _fingerprint_query(sql: str) -> str:
    """Normalize query by replacing literals with placeholders."""

    try:
        parsed = sqlglot.parse_one(sql, read="duckdb")
        for literal in parsed.find_all(exp.Literal):
            literal.replace(exp.Placeholder())
        return parsed.sql(dialect="duckdb")
    except Exception:
        return "<unparseable query>"


def _check_sensitive_tables(tables: list[str]) -> None:
    """Raise if any table is obviously sensitive."""

    blocked: set[str] = set()
    for table in tables:
        t_lower = table.lower()
        if t_lower in _SENSITIVE_TABLES_EXACT:
            blocked.add(t_lower)
        elif any(t_lower.startswith(prefix) for prefix in _SENSITIVE_TABLE_PREFIXES):
            blocked.add(t_lower)
    if blocked:
        blocked_str = ", ".join(sorted(blocked))
        raise SensitiveTableAccessError(f"Access to restricted tables denied: {blocked_str}")


def _log_query(
    user: Any,
    dataset: str,
    original_query: str,
    executed_query: str | None,
    row_count: int,
    execution_ms: int,
    status: str,
    error_message: str | None,
) -> None:
    """Structured query audit logging."""

    query_id = str(uuid4())
    fingerprint = _fingerprint_query(original_query) if original_query else None
    log_extra: dict[str, Any] = {
        "query_id": query_id,
        "user_id": get_user_id(user),
        "user_role": user.get("role") if isinstance(user, dict) else getattr(user, "role", None),
        "dataset": dataset,
        "query_fingerprint": fingerprint,
        "query_modified": original_query != executed_query if executed_query else False,
        "row_count": row_count,
        "execution_ms": execution_ms,
        "status": status,
        "error_message": _safe_error_message(status, error_message),
        "timestamp": datetime.now(UTC).isoformat(),
    }

    if _AUDIT_LOG_RAW_SQL:
        max_query_len = 2000
        raw_extra = {
            **log_extra,
            "original_query": original_query[:max_query_len] if original_query else None,
            "executed_query": executed_query[:max_query_len] if executed_query else None,
        }
        _audit_logger.info("sql_query_executed_raw", extra=raw_extra)

    logger.info("sql_query_executed", extra=log_extra)


def _glob_has_match(pattern: str) -> bool:
    return bool(_glob_module.glob(pattern, recursive=True))


def _raw_path_spec(path_spec: _TablePathSpec) -> _RawTablePathSpec:
    if isinstance(path_spec, ResolvedTablePathSpec):
        return path_spec.path_spec
    return path_spec


def _path_spec_has_match(path_spec: _TablePathSpec) -> bool:
    raw_path_spec = _raw_path_spec(path_spec)
    if isinstance(raw_path_spec, str):
        return _glob_has_match(raw_path_spec)
    return any(_Path(path).exists() for path in raw_path_spec)


def _validate_path_safe(path: str) -> None:
    """Validate path safety for interpolated CREATE VIEW statements."""

    if not path:
        raise ValueError("Empty path rejected")

    if "'" in path or '"' in path or "\\" in path or any(ord(ch) < 32 for ch in path):
        raise ValueError(f"Unsafe characters in path: {path}")

    for segment in path.replace("\\", "/").split("/"):
        if segment == "..":
            raise ValueError(f"Path traversal rejected: {path}")

    base_path = path.split("*")[0].rstrip("/") if "*" in path else path
    if not base_path:
        raise ValueError(f"Unsafe path rejected: {path}")

    resolved = _Path(base_path).resolve()
    if not any(resolved.is_relative_to(root) for root in _ALLOWED_DATA_ROOTS):
        raise ValueError(f"Path not under allowed data root: {path} (resolved to {resolved})")


def _resolve_alpaca_sip_snapshot_paths(
    data_root: str,
    *,
    storage_leaf: str,
    manifest_name: str,
    unreadable_log_event: str,
) -> _TablePathSpec:
    """Return manifest-pinned SIP snapshot partitions, with glob fallback only without manifest."""
    data_root_path = _Path(data_root).resolve()
    storage_root = (data_root_path / "alpaca" / "sip" / storage_leaf).resolve()
    manifest_path = (data_root_path / "manifests" / manifest_name).resolve()
    fallback_path_spec = f"{storage_root}/snapshots/*/*.parquet"

    if manifest_path.exists():
        cache_key = str(manifest_path.resolve())
        manifest_stat: os.stat_result | None = None
        try:
            manifest_stat = manifest_path.stat()
            with _ALPACA_SIP_MANIFEST_PATH_CACHE_LOCK:
                cached = _ALPACA_SIP_MANIFEST_PATH_CACHE.get(cache_key)
                if (
                    cached is not None
                    and cached.mtime_ns == manifest_stat.st_mtime_ns
                    and cached.size == manifest_stat.st_size
                ):
                    failure_expired = (
                        cached.failure_cached_at is not None
                        and time.monotonic() - cached.failure_cached_at
                        >= _ALPACA_SIP_MANIFEST_FAILURE_CACHE_TTL_SECONDS
                    )
                    if not failure_expired:
                        _ALPACA_SIP_MANIFEST_PATH_CACHE.move_to_end(cache_key)
                        return cached.path_spec
                    _ALPACA_SIP_MANIFEST_PATH_CACHE.pop(cache_key, None)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(manifest, dict):
                logger.warning(
                    unreadable_log_event,
                    extra={
                        "manifest_path": str(manifest_path),
                        "error": "Manifest JSON is not an object",
                    },
                )
                return _cache_resolved_alpaca_sip_manifest_path(
                    cache_key,
                    manifest_stat,
                    ResolvedTablePathSpec(path_spec=(), manifest_invalid=True),
                )
            raw_validation_status = manifest.get("validation_status")
            validation_status = (
                str(raw_validation_status).lower()
                if raw_validation_status is not None
                else "unknown"
            )
            if validation_status != "passed":
                logger.warning(
                    "sql_explorer_alpaca_sip_manifest_untrusted",
                    extra={
                        "manifest_path": str(manifest_path),
                        "validation_status": validation_status,
                    },
                )
                if raw_validation_status is None:
                    return _cache_resolved_alpaca_sip_manifest_path(
                        cache_key,
                        manifest_stat,
                        ResolvedTablePathSpec(
                            path_spec=fallback_path_spec,
                            fallback_only=True,
                        ),
                    )
                return _cache_resolved_alpaca_sip_manifest_path(
                    cache_key,
                    manifest_stat,
                    ResolvedTablePathSpec(path_spec=(), manifest_invalid=True),
                )
            file_paths = manifest.get("file_paths", [])
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(
                unreadable_log_event,
                extra={"manifest_path": str(manifest_path), "error": str(exc)},
            )
            if manifest_stat is None:
                return ResolvedTablePathSpec(path_spec=(), manifest_invalid=True)
            return _cache_resolved_alpaca_sip_manifest_path(
                cache_key,
                manifest_stat,
                ResolvedTablePathSpec(path_spec=(), manifest_invalid=True),
                failure_cache=True,
            )
        else:
            resolved_paths: list[str] = []
            if isinstance(file_paths, list):
                invalid_paths: list[str] = []
                for raw_path in file_paths:
                    if not isinstance(raw_path, str):
                        invalid_paths.append(repr(raw_path))
                        continue
                    resolved = resolve_alpaca_sip_manifest_path(
                        _Path(raw_path),
                        data_root=data_root_path,
                        storage_root=storage_root,
                    )
                    if not resolved.is_relative_to(storage_root) or resolved.suffix != ".parquet":
                        invalid_paths.append(raw_path)
                        continue
                    if not resolved.exists():
                        invalid_paths.append(raw_path)
                        continue
                    resolved_paths.append(str(resolved))

                if invalid_paths:
                    logger.warning(
                        "sql_explorer_alpaca_sip_manifest_invalid_paths",
                        extra={
                            "manifest_path": str(manifest_path),
                            "invalid_paths": invalid_paths,
                        },
                    )
                    path_spec = ResolvedTablePathSpec(path_spec=(), manifest_invalid=True)
                    return _cache_resolved_alpaca_sip_manifest_path(
                        cache_key,
                        manifest_stat,
                        path_spec,
                    )

                raw_path_spec = tuple(sorted(resolved_paths))
                path_spec = ResolvedTablePathSpec(
                    path_spec=raw_path_spec,
                    manifest_backed=bool(raw_path_spec),
                    manifest_invalid=not raw_path_spec,
                )
                return _cache_resolved_alpaca_sip_manifest_path(
                    cache_key,
                    manifest_stat,
                    path_spec,
                )

            logger.warning(
                "sql_explorer_alpaca_sip_manifest_invalid_file_paths",
                extra={"manifest_path": str(manifest_path)},
            )
            path_spec = ResolvedTablePathSpec(path_spec=(), manifest_invalid=True)
            return _cache_resolved_alpaca_sip_manifest_path(
                cache_key,
                manifest_stat,
                path_spec,
            )

    return ResolvedTablePathSpec(
        path_spec=fallback_path_spec,
        fallback_only=True,
    )


def _resolve_alpaca_sip_daily_paths(data_root: str) -> _TablePathSpec:
    """Return manifest-pinned SIP daily partitions, with snapshot glob as fallback."""
    return _resolve_alpaca_sip_snapshot_paths(
        data_root,
        storage_leaf="daily",
        manifest_name="alpaca_sip_daily.json",
        unreadable_log_event="sql_explorer_alpaca_sip_daily_manifest_unreadable",
    )


def _resolve_alpaca_sip_corp_actions_paths(data_root: str) -> _TablePathSpec:
    """Return manifest-pinned SIP corporate-action partitions, with snapshot glob fallback."""
    return _resolve_alpaca_sip_snapshot_paths(
        data_root,
        storage_leaf="corp_actions",
        manifest_name="alpaca_sip_corp_actions.json",
        unreadable_log_event="sql_explorer_alpaca_sip_corp_actions_manifest_unreadable",
    )


def _duckdb_read_parquet_arg(path_spec: _TablePathSpec) -> str:
    """Build a safe DuckDB read_parquet argument from validated path specs."""
    raw_path_spec = _raw_path_spec(path_spec)
    paths = (raw_path_spec,) if isinstance(raw_path_spec, str) else raw_path_spec
    for path in paths:
        _validate_path_safe(path)

    if isinstance(raw_path_spec, str):
        return f"'{raw_path_spec}'"
    if not raw_path_spec:
        raise ValueError("Empty path list rejected")
    quoted_paths = ", ".join(f"'{path}'" for path in raw_path_spec)
    return f"[{quoted_paths}]"


def _set_duckdb_option(
    conn: duckdb.DuckDBPyConnection,
    sql: str,
    *,
    option_name: str,
    allow_unrecognized: bool = False,
) -> None:
    try:
        conn.execute(sql)
    except duckdb.CatalogException as exc:
        if allow_unrecognized and "unrecognized configuration parameter" in str(exc):
            logger.warning(
                "duckdb_option_unavailable",
                extra={"option": option_name},
            )
            return
        raise


def _duckdb_version_tuple(version: str) -> tuple[int, int, int]:
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)", version)
    if match is None:
        return (0, 0, 0)
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def _ensure_duckdb_lockdown_supported() -> None:
    version = _duckdb_version_tuple(str(getattr(duckdb, "__version__", "")))
    if version < _DUCKDB_LOCKDOWN_MIN_VERSION:
        raise RuntimeError(
            "SQL Explorer requires DuckDB >= 1.4.0 for hardened extension lockdown "
            f"(found {getattr(duckdb, '__version__', 'unknown')})."
        )


def _resolve_table_paths() -> dict[str, _TablePathSpec]:
    """Resolve logical tables to physical parquet/glob paths."""

    data_root = str((_PROJECT_ROOT / "data").resolve())
    return {
        "crsp_daily": f"{data_root}/wrds/crsp/daily/*.parquet",
        "crsp_monthly": f"{data_root}/wrds/crsp/monthly/*.parquet",
        "compustat_annual": f"{data_root}/wrds/compustat_annual/*.parquet",
        "compustat_quarterly": f"{data_root}/wrds/compustat_quarterly/*.parquet",
        "ff_factors_daily": f"{data_root}/fama_french/factors/factors_*_daily.parquet",
        "ff_factors_monthly": f"{data_root}/fama_french/factors/factors_*_monthly.parquet",
        "taq_trades": f"{data_root}/taq/aggregates/1min_bars/*.parquet",
        "taq_quotes": f"{data_root}/taq/aggregates/spread_stats/*.parquet",
        "alpaca_sip_daily": _resolve_alpaca_sip_daily_paths(data_root),
        "alpaca_sip_corp_actions": _resolve_alpaca_sip_corp_actions_paths(data_root),
    }


def _validate_table_paths(
    table_paths: dict[str, _TablePathSpec] | None = None,
) -> tuple[dict[str, set[str]], list[str]]:
    """Validate discovered table paths and return per-dataset availability."""

    paths = _resolve_table_paths() if table_paths is None else table_paths
    available_tables_by_dataset: dict[str, set[str]] = {}
    warnings: list[str] = []

    for dataset, tables in DATASET_TABLES.items():
        available_tables: set[str] = set()
        for table in tables:
            path_spec = paths.get(table)
            if path_spec and _path_spec_has_match(path_spec):
                available_tables.add(table)
            else:
                warnings.append(f"No Parquet files found for {table}: {path_spec}")

        if available_tables:
            available_tables_by_dataset[dataset] = available_tables
        else:
            warnings.append(f"Dataset '{dataset}' has no available data — excluded from UI")

    return available_tables_by_dataset, warnings


def resolve_sql_table_paths() -> dict[str, TablePathSpec]:
    """Resolve logical SQL Explorer tables to local parquet path specs."""
    return _resolve_table_paths()


def validate_sql_table_paths(
    table_paths: dict[str, TablePathSpec] | None = None,
) -> tuple[dict[str, set[str]], list[str]]:
    """Return queryable local tables grouped by dataset."""
    return _validate_table_paths(table_paths)


def resolve_sql_table_availability(
    table_paths: dict[str, TablePathSpec] | None = None,
) -> tuple[dict[str, SqlTableResolution], list[str]]:
    """Return table availability with Data-page trust metadata.

    SQL Explorer can inspect fallback Alpaca SIP parquet files, but the main
    data page requires manifest-pinned paths for preview and handoff actions.
    """

    paths = resolve_sql_table_paths() if table_paths is None else table_paths
    resolutions: dict[str, SqlTableResolution] = {}
    warnings: list[str] = []

    for dataset, tables in DATASET_TABLES.items():
        for table in tables:
            path_spec = paths.get(table)
            available = bool(path_spec and _path_spec_has_match(path_spec))
            manifest_required = table in _ALPACA_SIP_TABLE_MANIFESTS
            if isinstance(path_spec, ResolvedTablePathSpec):
                manifest_backed = bool(path_spec.manifest_backed and available)
                fallback_only = bool(path_spec.fallback_only)
                manifest_invalid = bool(path_spec.manifest_invalid)
            else:
                manifest_backed = bool(
                    manifest_required and isinstance(path_spec, tuple) and available
                )
                fallback_only = bool(manifest_required and isinstance(path_spec, str))
                manifest_invalid = False
            trusted_for_data_page = bool(
                available
                and not manifest_invalid
                and (not manifest_required or manifest_backed)
            )
            if manifest_invalid:
                warnings.append(
                    f"{table} manifest is invalid or has no valid parquet files; /data disabled"
                )
            if fallback_only:
                warnings.append(
                    f"{table} is queryable fallback only; manifest-backed /data preview disabled"
                )
            elif not available and not manifest_invalid:
                warnings.append(f"No Parquet files found for {table}: {path_spec}")

            resolutions[table] = SqlTableResolution(
                dataset=dataset,
                table=table,
                path_spec=path_spec,
                available=available,
                manifest_required=manifest_required,
                manifest_backed=manifest_backed,
                manifest_invalid=manifest_invalid,
                fallback_only=fallback_only,
                trusted_for_data_page=trusted_for_data_page,
            )

    return resolutions, warnings


def _create_query_connection(
    dataset: str,
    available_tables: set[str],
    table_paths: dict[str, TablePathSpec] | None = None,
) -> duckdb.DuckDBPyConnection:
    """Create hardened DuckDB connection scoped to available dataset tables."""

    _ensure_duckdb_lockdown_supported()
    conn = duckdb.connect()
    try:
        _strict_extensions = os.getenv("SQL_EXPLORER_STRICT_EXTENSIONS", "true").lower() == "true"
        if not _strict_extensions and _APP_ENV == "production":
            raise RuntimeError(
                "SQL_EXPLORER_STRICT_EXTENSIONS=false is forbidden when APP_ENV=production."
            )

        _set_duckdb_option(
            conn,
            "SET autoinstall_known_extensions = false",
            option_name="autoinstall_known_extensions",
        )
        _set_duckdb_option(
            conn,
            "SET autoload_known_extensions = false",
            option_name="autoload_known_extensions",
        )
        _set_duckdb_option(
            conn,
            "SET allow_unsigned_extensions = false",
            option_name="allow_unsigned_extensions",
        )
        _set_duckdb_option(
            conn,
            "SET allow_community_extensions = false",
            option_name="allow_community_extensions",
        )

        for extension in sorted(_DUCKDB_LOADABLE_EXTENSIONS):
            conn.execute(f"LOAD {extension}")

        # Legacy aliases are removed in the locked DuckDB 1.4.4 build, so they
        # are advisory only. Apply them after loading the required built-ins for
        # builds that still support the broader extension-loading switch.
        _set_duckdb_option(
            conn,
            "SET enable_extension_loading = false",
            option_name="enable_extension_loading",
            allow_unrecognized=True,
        )
        _set_duckdb_option(
            conn,
            "SET enable_extension_autoloading = false",
            option_name="enable_extension_autoloading",
            allow_unrecognized=True,
        )

        loaded_extensions = conn.execute(
            "SELECT extension_name FROM duckdb_extensions() WHERE loaded = true"
        ).fetchall()

        for (ext_name,) in loaded_extensions:
            if ext_name in _DUCKDB_DENIED_EXTENSIONS:
                raise RuntimeError(f"Denied DuckDB extension loaded: {ext_name}")
            if ext_name not in _DUCKDB_ALLOWED_LOADED_EXTENSIONS:
                if _strict_extensions:
                    raise RuntimeError(f"Unknown DuckDB extension loaded: {ext_name}")
                logger.warning("duckdb_unknown_extension_advisory", extra={"extension": ext_name})

        default_memory_mb = 512
        raw_memory_mb = os.getenv("SQL_EXPLORER_MAX_MEMORY_MB", str(default_memory_mb))
        try:
            max_memory_mb = max(64, int(raw_memory_mb))
        except (TypeError, ValueError):
            logger.warning(
                "sql_explorer_invalid_max_memory",
                extra={"raw_value": raw_memory_mb, "fallback": default_memory_mb},
            )
            max_memory_mb = default_memory_mb

        conn.execute(f"SET max_memory = '{max_memory_mb}MB'")
        conn.execute("SET threads = 1")

        resolved_table_paths = _resolve_table_paths() if table_paths is None else table_paths
        for table_name in DATASET_TABLES[dataset]:
            if table_name not in available_tables:
                continue
            if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", table_name):
                raise ValueError(f"Invalid table identifier: {table_name}")

            parquet_path_spec = resolved_table_paths.get(table_name)
            if parquet_path_spec is None:
                continue
            parquet_arg = _duckdb_read_parquet_arg(parquet_path_spec)
            conn.execute(
                f'CREATE OR REPLACE VIEW "{table_name}" AS '
                f"SELECT * FROM read_parquet({parquet_arg})"
            )

        return conn
    except Exception:
        conn.close()
        raise


def create_scoped_query_connection(
    dataset: str,
    available_tables: set[str],
    table_paths: dict[str, TablePathSpec] | None = None,
) -> duckdb.DuckDBPyConnection:
    """Create a hardened DuckDB connection scoped to approved tables."""
    ensure_sql_explorer_execution_allowed()
    if table_paths is None:
        return _create_query_connection(dataset, available_tables)
    return _create_query_connection(dataset, available_tables, table_paths=table_paths)


def _verify_sandbox() -> tuple[bool, list[str]]:
    """Advisory deployment probe for egress/filesystem hardening."""

    if _SQL_EXPLORER_SANDBOX_SKIP:
        if _APP_ENV == "production":
            raise RuntimeError(
                "SQL_EXPLORER_SANDBOX_SKIP=true is forbidden when APP_ENV=production."
            )
        logger.warning("sql_explorer_sandbox_skip_dev_mode")
        return True, []

    failures: list[str] = []
    probe_host = os.getenv("SQL_EXPLORER_PROBE_HOST", "1.1.1.1")
    probe_port = int(os.getenv("SQL_EXPLORER_PROBE_PORT", "53"))

    try:
        sock = socket.create_connection((probe_host, probe_port), timeout=3)
        sock.close()
        failures.append("network_egress_allowed")
    except (OSError, TimeoutError):
        pass

    forbidden_raw = os.getenv("SQL_EXPLORER_FORBIDDEN_WRITE_PATHS", "data/,libs/,apps/,config/")
    forbidden_paths: list[_Path] = []
    for raw in [segment.strip() for segment in forbidden_raw.split(",") if segment.strip()]:
        candidate = _Path(raw)
        resolved = (
            candidate.resolve() if candidate.is_absolute() else (_PROJECT_ROOT / raw).resolve()
        )
        forbidden_paths.append(resolved)

    probe_suffix = f".sql_explorer_probe_{os.getpid()}"
    for forbidden_dir in forbidden_paths:
        probe_path = forbidden_dir / probe_suffix
        try:
            probe_path.write_text("probe", encoding="utf-8")
            probe_path.unlink()
            failures.append(f"filesystem_write_allowed:{forbidden_dir}")
        except PermissionError:
            pass  # Expected: write denied = secure
        except FileNotFoundError:
            pass  # Directory absent = not writable (safe)
        except OSError as exc:
            failures.append(f"filesystem_probe_unexpected:{forbidden_dir}:{exc}")

    return (len(failures) == 0, failures)


def can_query_dataset(user: Any, dataset: str) -> bool:
    """Authorize dataset query with explicit default-deny mapping."""

    if not has_permission(user, Permission.QUERY_DATA):
        return False
    if dataset not in DATASET_TABLES:
        return False
    permission = _DATASET_PERMISSION_MAP.get(dataset)
    if permission is None:
        logger.warning("sql_explorer_unmapped_dataset_denied", extra={"dataset": dataset})
        return False
    return has_dataset_permission(user, permission)


def _query_frame_from_connection(
    conn: duckdb.DuckDBPyConnection,
    sql: str,
) -> pl.DataFrame:
    result = conn.execute(sql)
    df = result.pl()
    cell_count = len(df) * len(df.columns)
    if cell_count > _MAX_CELLS:
        raise ValueError(
            f"Result too large: {cell_count:,} cells exceeds limit of {_MAX_CELLS:,}. "
            "Add filters or reduce columns."
        )
    return df


async def _execute_query_callable_with_timeout(
    run_query: Callable[[], pl.DataFrame],
    timeout_seconds: int,
    interrupt: Callable[[], None],
) -> pl.DataFrame:
    """Execute query work with bounded concurrency and cooperative timeout cleanup."""

    global _active_queries

    acquired_slot = False
    async with _active_queries_lock:
        if _active_queries >= _MAX_CONCURRENT_QUERIES:
            raise ConcurrencyLimitError("Too many concurrent queries. Please try again later.")
        _active_queries += 1
        acquired_slot = True

    clamped_timeout = min(timeout_seconds, _MAX_TIMEOUT_SECONDS)

    async def _release_slot_after_cleanup(task: asyncio.Task[pl.DataFrame]) -> None:
        global _active_queries

        try:
            await asyncio.shield(task)
        except Exception:
            pass
        finally:
            async with _active_queries_lock:
                _active_queries -= 1

    try:
        task = asyncio.create_task(asyncio.to_thread(run_query))
        try:
            return await asyncio.wait_for(asyncio.shield(task), timeout=clamped_timeout)
        except TimeoutError as exc:
            if not task.done():
                try:
                    interrupt()
                except Exception:
                    logger.warning("duckdb_interrupt_failed", extra={"timeout": clamped_timeout})
                asyncio.create_task(_release_slot_after_cleanup(task))
                acquired_slot = False
            raise TimeoutError("SQL query timed out") from exc
    finally:
        if acquired_slot:
            async with _active_queries_lock:
                _active_queries -= 1


async def _execute_query_with_timeout(
    conn: duckdb.DuckDBPyConnection,
    sql: str,
    timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
) -> pl.DataFrame:
    """Execute query with bounded concurrency and timeout-safe cleanup."""

    return await _execute_query_callable_with_timeout(
        lambda: _query_frame_from_connection(conn, sql),
        timeout_seconds,
        conn.interrupt,
    )


class _ScopedQueryRunner:
    """Own a scoped DuckDB connection inside the worker thread that executes it."""

    def __init__(
        self,
        *,
        dataset: str,
        available_tables: set[str],
        table_paths: dict[str, TablePathSpec] | None,
        sql: str,
    ) -> None:
        self._dataset = dataset
        self._available_tables = available_tables
        self._table_paths = table_paths
        self._sql = sql
        self._conn: duckdb.DuckDBPyConnection | None = None
        self._conn_lock = threading.Lock()
        self._interrupt_requested = threading.Event()

    def run(self) -> pl.DataFrame:
        conn = create_scoped_query_connection(
            self._dataset,
            available_tables=self._available_tables,
            table_paths=self._table_paths,
        )
        with self._conn_lock:
            self._conn = conn
        try:
            if self._interrupt_requested.is_set():
                conn.interrupt()
            return _query_frame_from_connection(conn, self._sql)
        finally:
            try:
                conn.close()
            finally:
                with self._conn_lock:
                    if self._conn is conn:
                        self._conn = None

    def interrupt(self) -> None:
        self._interrupt_requested.set()
        with self._conn_lock:
            conn = self._conn
        if conn is not None:
            conn.interrupt()


async def execute_scoped_query_frame_with_timeout(
    dataset: str,
    sql: str,
    timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
    *,
    available_tables: set[str],
    table_paths: dict[str, TablePathSpec] | None = None,
) -> pl.DataFrame:
    """Execute a scoped DuckDB query while owning close in the query worker thread."""

    runner = _ScopedQueryRunner(
        dataset=dataset,
        available_tables=available_tables,
        table_paths=table_paths,
        sql=sql,
    )
    return await _execute_query_callable_with_timeout(
        runner.run,
        timeout_seconds,
        runner.interrupt,
    )


async def execute_scoped_query_with_timeout(
    conn: duckdb.DuckDBPyConnection,
    sql: str,
    timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
) -> pl.DataFrame:
    """Execute a scoped DuckDB query with shared timeout/concurrency limits."""
    return await _execute_query_with_timeout(conn, sql, timeout_seconds)


def check_sensitive_tables(tables: list[str]) -> None:
    """Raise if a query references obviously sensitive tables."""
    _check_sensitive_tables(tables)


def fingerprint_sql_query(sql: str) -> str:
    """Return a normalized audit/display fingerprint for SQL text."""
    return _fingerprint_query(sql)


def ensure_sql_explorer_execution_allowed() -> None:
    """Enforce SQL Explorer production attestation and sandbox checks."""
    global _sandbox_probe_result

    if _APP_ENV == "production":
        if os.getenv("SQL_EXPLORER_DEPLOY_ATTESTED", "false").lower() != "true":
            raise RuntimeError("SQL Explorer requires deploy-attested sandbox in production")

    if _IS_DEV_MODE and _APP_ENV == "production":
        raise RuntimeError("SQL_EXPLORER_DEV_MODE=true is forbidden when APP_ENV=production.")

    now = time.monotonic()
    with _sandbox_probe_lock:
        if (
            _sandbox_probe_result is None
            or now - _sandbox_probe_result[0] >= _SANDBOX_PROBE_CACHE_TTL_SECONDS
        ):
            safe, failures = _verify_sandbox()
            _sandbox_probe_result = (now, safe, tuple(failures))
        else:
            _checked_at, safe, failures_tuple = _sandbox_probe_result
            failures = list(failures_tuple)

    if not safe:
        if _APP_ENV == "production":
            raise RuntimeError(
                f"SQL Explorer sandbox probe failed in production: {', '.join(failures)}. "
                "Ensure network egress is blocked and filesystem is read-only."
            )
        logger.warning("sql_explorer_sandbox_probe_failed", extra={"failures": failures})


def log_sql_query_audit(
    user: Any,
    dataset: str,
    original_query: str,
    executed_query: str | None,
    row_count: int,
    execution_ms: int,
    status: str,
    error_message: str | None,
) -> None:
    """Write a SQL Explorer-compatible query audit record."""
    _log_query(
        user,
        dataset,
        original_query,
        executed_query,
        row_count,
        execution_ms,
        status,
        error_message,
    )


class SqlExplorerService:
    """Service encapsulating SQL Explorer security and execution pipeline."""

    def __init__(self, rate_limiter: RateLimiter | None = None) -> None:
        if rate_limiter is None:
            if not _IS_DEV_MODE:
                raise ValueError(
                    "SqlExplorerService requires a RateLimiter in production-like environments. "
                    "Set SQL_EXPLORER_DEV_MODE=true for local development only."
                )
            logger.warning("sql_explorer_rate_limiter_disabled_dev_mode")
        elif rate_limiter.fallback_mode != "deny":
            raise ValueError("SqlExplorerService requires RateLimiter with fallback_mode='deny'.")

        self._rate_limiter = rate_limiter
        self._validator = SQLValidator()

    async def execute_query(
        self,
        user: Any,
        dataset: str,
        query: str,
        timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
        max_rows: int = _DEFAULT_MAX_ROWS,
        available_tables: set[str] | None = None,
    ) -> QueryResult:
        """Validate, run, and audit a query with centralized security controls."""

        start = time.monotonic()
        limited_sql: str | None = None

        try:
            if not can_query_dataset(user, dataset):
                raise PermissionError(f"Not authorized for dataset '{dataset}'")

            if self._rate_limiter is not None:
                allowed, _ = await self._rate_limiter.check_rate_limit(
                    user_id=get_user_id(user),
                    action="sql_query",
                    max_requests=_QUERY_RATE_LIMIT,
                    window_seconds=60,
                )
                if not allowed:
                    raise RateLimitExceededError("Query rate limit exceeded")

            valid, error = self._validator.validate(query, dataset)
            if not valid:
                raise ValueError(error or "Query validation failed")

            tables = self._validator.extract_tables(query)
            _check_sensitive_tables(tables)

            clamped_rows = min(max_rows, _MAX_ROWS_LIMIT)
            limited_sql = self._validator.enforce_row_limit(query, clamped_rows)

            resolved_tables = (
                available_tables if available_tables is not None else set(DATASET_TABLES[dataset])
            )
            result = await execute_scoped_query_frame_with_timeout(
                dataset,
                limited_sql,
                timeout_seconds,
                available_tables=resolved_tables,
            )

            execution_ms = int((time.monotonic() - start) * 1000)
            fingerprint = _fingerprint_query(query)
            _log_query(
                user, dataset, query, limited_sql, len(result), execution_ms, "success", None
            )
            return QueryResult(df=result, execution_ms=execution_ms, fingerprint=fingerprint)

        except PermissionError:
            execution_ms = int((time.monotonic() - start) * 1000)
            _log_query(
                user,
                dataset,
                query,
                limited_sql,
                0,
                execution_ms,
                "authorization_denied",
                None,
            )
            raise
        except SensitiveTableAccessError as exc:
            execution_ms = int((time.monotonic() - start) * 1000)
            _log_query(
                user,
                dataset,
                query,
                limited_sql,
                0,
                execution_ms,
                "security_blocked",
                str(exc),
            )
            raise
        except ValueError as exc:
            execution_ms = int((time.monotonic() - start) * 1000)
            _log_query(
                user,
                dataset,
                query,
                limited_sql,
                0,
                execution_ms,
                "validation_error",
                str(exc),
            )
            raise
        except ConcurrencyLimitError as exc:
            execution_ms = int((time.monotonic() - start) * 1000)
            _log_query(
                user,
                dataset,
                query,
                limited_sql,
                0,
                execution_ms,
                "concurrency_limit",
                str(exc),
            )
            raise
        except RateLimitExceededError as exc:
            execution_ms = int((time.monotonic() - start) * 1000)
            _log_query(
                user,
                dataset,
                query,
                limited_sql,
                0,
                execution_ms,
                "rate_limited",
                str(exc),
            )
            raise
        except TimeoutError:
            execution_ms = int((time.monotonic() - start) * 1000)
            _log_query(user, dataset, query, limited_sql, 0, execution_ms, "timeout", None)
            raise
        except Exception as exc:
            execution_ms = int((time.monotonic() - start) * 1000)
            _log_query(user, dataset, query, limited_sql, 0, execution_ms, "error", str(exc))
            raise

    async def export_csv(self, user: Any, dataset: str, df: pl.DataFrame) -> bytes:
        """Export query result as CSV with RBAC and rate limiting."""

        def _log_export(status: str, row_count: int = 0) -> None:
            logger.info(
                "sql_export_csv",
                extra={
                    "user_id": get_user_id(user),
                    "dataset": dataset,
                    "row_count": row_count,
                    "status": status,
                },
            )

        try:
            if not has_permission(user, Permission.EXPORT_DATA):
                _log_export("authorization_denied")
                raise PermissionError("Export permission required")
            if not can_query_dataset(user, dataset):
                _log_export("authorization_denied")
                raise PermissionError(f"Not authorized for dataset '{dataset}'")

            if self._rate_limiter is not None:
                allowed, _ = await self._rate_limiter.check_rate_limit(
                    user_id=get_user_id(user),
                    action="sql_export",
                    max_requests=_EXPORT_RATE_LIMIT,
                    window_seconds=3600,
                )
                if not allowed:
                    _log_export("rate_limited")
                    raise RateLimitExceededError("Export rate limit exceeded")

            csv_bytes = df.write_csv().encode("utf-8")
            _log_export("success", len(df))
            return csv_bytes

        except (PermissionError, RateLimitExceededError):
            raise
        except Exception:
            _log_export("error")
            raise


__all__ = [
    "SqlExplorerService",
    "QueryResult",
    "SqlTableResolution",
    "TablePathSpec",
    "ResolvedTablePathSpec",
    "SensitiveTableAccessError",
    "ConcurrencyLimitError",
    "RateLimitExceededError",
    "can_query_dataset",
    "check_sensitive_tables",
    "create_scoped_query_connection",
    "ensure_sql_explorer_execution_allowed",
    "execute_scoped_query_frame_with_timeout",
    "execute_scoped_query_with_timeout",
    "fingerprint_sql_query",
    "log_sql_query_audit",
    "safe_sql_error_message",
    "resolve_sql_table_availability",
    "resolve_sql_table_paths",
    "validate_sql_table_paths",
    "_DEFAULT_TIMEOUT_SECONDS",
    "_MAX_TIMEOUT_SECONDS",
    "_DEFAULT_MAX_ROWS",
    "_MAX_ROWS_LIMIT",
    "_MAX_CONCURRENT_QUERIES",
    "_QUERY_RATE_LIMIT",
    "_EXPORT_RATE_LIMIT",
    "_MAX_CELLS",
    "_safe_error_message",
    "_fingerprint_query",
    "_check_sensitive_tables",
    "_validate_path_safe",
    "_resolve_table_paths",
    "_validate_table_paths",
    "_verify_sandbox",
    "_create_query_connection",
    "_execute_query_with_timeout",
    "_DATA_ROOT_AVAILABLE",
]
