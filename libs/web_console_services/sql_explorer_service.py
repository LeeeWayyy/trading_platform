"""SQL Explorer service with defense-in-depth query controls."""

from __future__ import annotations

import asyncio
import glob as _glob_module
import logging
import os
import re
import socket
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path as _Path
from typing import Any
from uuid import uuid4

import duckdb
import polars as pl
import sqlglot
from sqlglot import exp

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
}

_AUDIT_LOG_RAW_SQL = os.getenv("SQL_EXPLORER_AUDIT_RAW_SQL", "false").lower() == "true"
_AUDIT_RAW_SQL_EMERGENCY_OVERRIDE = (
    os.getenv("SQL_EXPLORER_AUDIT_RAW_SQL_EMERGENCY_OVERRIDE", "false").lower() == "true"
)

_SQL_EXPLORER_SANDBOX_SKIP = os.getenv("SQL_EXPLORER_SANDBOX_SKIP", "").lower() == "true"

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
        _is_dev = os.getenv("SQL_EXPLORER_DEV_MODE", "").lower() == "true"
        _app_env = os.getenv("APP_ENV", "").lower()
        if _is_dev and _app_env == "production":
            raise RuntimeError("SQL_EXPLORER_DEV_MODE=true is forbidden when APP_ENV=production.")
        if _is_dev:
            _PROJECT_ROOT = _Path.cwd()
            logger.warning(
                "sql_explorer_project_root_fallback_cwd_dev",
                extra={"cwd": str(_PROJECT_ROOT)},
            )
        else:
            logger.error(
                "sql_explorer_project_root_not_found",
                extra={
                    "hint": (
                        "Set PROJECT_ROOT env var or SQL_EXPLORER_DEV_MODE=true for local dev"
                    )
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

if _AUDIT_LOG_RAW_SQL and os.getenv("APP_ENV", "").lower() == "production":
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


class SensitiveTableAccessError(ValueError):
    """Raised when restricted table names are referenced."""


class ConcurrencyLimitError(RuntimeError):
    """Raised when per-process query concurrency cap is reached."""


class RateLimitExceededError(RuntimeError):
    """Raised when a query/export action exceeds rate limit."""


@dataclass(frozen=True)
class QueryResult:
    """Typed query result envelope for SQL Explorer UI."""

    df: pl.DataFrame
    execution_ms: int
    fingerprint: str


def _safe_error_message(status: str, _raw_error: str | None = None) -> str:
    """Return canonical, non-sensitive error messages only.

    The _raw_error parameter is intentionally unused — it exists in the
    signature so callers pass it for documentation/audit purposes, but
    its value is never included in the returned message to prevent
    leaking SQL fragments or internal details.
    """
    return _ERROR_CODES.get(status, "Internal execution error")


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
    return bool(_glob_module.glob(pattern))


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


def _resolve_table_paths() -> dict[str, str]:
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
    }


def _validate_table_paths(
    table_paths: dict[str, str] | None = None,
) -> tuple[dict[str, set[str]], list[str]]:
    """Validate discovered table paths and return per-dataset availability."""

    paths = table_paths or _resolve_table_paths()
    available_tables_by_dataset: dict[str, set[str]] = {}
    warnings: list[str] = []

    for dataset, tables in DATASET_TABLES.items():
        available_tables: set[str] = set()
        for table in tables:
            pattern = paths.get(table)
            if pattern and _glob_has_match(pattern):
                available_tables.add(table)
            else:
                warnings.append(f"No Parquet files found for {table}: {pattern}")

        if available_tables:
            available_tables_by_dataset[dataset] = available_tables
        else:
            warnings.append(f"Dataset '{dataset}' has no available data — excluded from UI")

    return available_tables_by_dataset, warnings


def _create_query_connection(dataset: str, available_tables: set[str]) -> duckdb.DuckDBPyConnection:
    """Create hardened DuckDB connection scoped to available dataset tables."""

    conn = duckdb.connect()
    conn.execute("SET enable_extension_autoloading = false")
    conn.execute("SET enable_extension_loading = false")

    _allowed_extensions = frozenset({"core_functions", "icu", "json", "parquet"})
    _denied_extensions = frozenset(
        {"httpfs", "postgres_scanner", "sqlite_scanner", "mysql_scanner", "azure", "aws"}
    )
    _strict_extensions = (
        os.getenv("SQL_EXPLORER_STRICT_EXTENSIONS", "true").lower() == "true"
    )
    _app_env = os.getenv("APP_ENV", "").lower()
    if not _strict_extensions and _app_env == "production":
        conn.close()
        raise RuntimeError(
            "SQL_EXPLORER_STRICT_EXTENSIONS=false is forbidden when APP_ENV=production."
        )

    loaded_extensions = conn.execute(
        "SELECT extension_name FROM duckdb_extensions() WHERE loaded = true"
    ).fetchall()

    for (ext_name,) in loaded_extensions:
        if ext_name in _denied_extensions:
            conn.close()
            raise RuntimeError(f"Denied DuckDB extension loaded: {ext_name}")
        if ext_name not in _allowed_extensions:
            if _strict_extensions:
                conn.close()
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

    table_paths = _resolve_table_paths()
    for table_name in DATASET_TABLES[dataset]:
        if table_name not in available_tables:
            continue
        if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", table_name):
            conn.close()
            raise ValueError(f"Invalid table identifier: {table_name}")

        parquet_path = table_paths.get(table_name)
        if parquet_path is None:
            continue
        _validate_path_safe(parquet_path)
        conn.execute(
            f'CREATE OR REPLACE VIEW "{table_name}" AS '
            f"SELECT * FROM read_parquet('{parquet_path}')"
        )

    return conn


def _verify_sandbox() -> tuple[bool, list[str]]:
    """Advisory deployment probe for egress/filesystem hardening."""

    if _SQL_EXPLORER_SANDBOX_SKIP:
        if os.getenv("APP_ENV", "").lower() == "production":
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

    forbidden_raw = os.getenv(
        "SQL_EXPLORER_FORBIDDEN_WRITE_PATHS", "data/,libs/,apps/,config/"
    )
    forbidden_paths: list[_Path] = []
    for raw in [segment.strip() for segment in forbidden_raw.split(",") if segment.strip()]:
        candidate = _Path(raw)
        resolved = candidate.resolve() if candidate.is_absolute() else (_PROJECT_ROOT / raw).resolve()
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
            failures.append(f"filesystem_probe_path_missing:{forbidden_dir}")
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


async def _execute_query_with_timeout(
    conn: duckdb.DuckDBPyConnection,
    sql: str,
    timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
) -> pl.DataFrame:
    """Execute query with bounded concurrency and hard timeout."""

    global _active_queries

    acquired_slot = False
    async with _active_queries_lock:
        if _active_queries >= _MAX_CONCURRENT_QUERIES:
            raise ConcurrencyLimitError("Too many concurrent queries. Please try again later.")
        _active_queries += 1
        acquired_slot = True

    clamped_timeout = min(timeout_seconds, _MAX_TIMEOUT_SECONDS)

    try:
        def _run() -> pl.DataFrame:
            result = conn.execute(sql)
            df = result.pl()
            cell_count = len(df) * len(df.columns)
            if cell_count > _MAX_CELLS:
                raise ValueError(
                    f"Result too large: {cell_count:,} cells exceeds limit of {_MAX_CELLS:,}. "
                    "Add filters or reduce columns."
                )
            return df

        return await asyncio.wait_for(asyncio.to_thread(_run), timeout=clamped_timeout)
    except TimeoutError:
        try:
            conn.interrupt()
        except Exception:
            logger.warning("duckdb_interrupt_failed", extra={"timeout": clamped_timeout})
        raise
    finally:
        if acquired_slot:
            async with _active_queries_lock:
                _active_queries -= 1


class SqlExplorerService:
    """Service encapsulating SQL Explorer security and execution pipeline."""

    def __init__(self, rate_limiter: RateLimiter | None = None) -> None:
        app_env = os.getenv("APP_ENV", "").lower()
        if app_env not in _KNOWN_ENVS:
            raise RuntimeError(
                f"SQL_EXPLORER requires explicit APP_ENV in {_KNOWN_ENVS}, got: '{app_env}'"
            )

        if app_env == "production":
            if os.getenv("SQL_EXPLORER_DEPLOY_ATTESTED", "false").lower() != "true":
                raise RuntimeError("SQL Explorer requires deploy-attested sandbox in production")

        is_dev_mode = os.getenv("SQL_EXPLORER_DEV_MODE", "").lower() == "true"
        if is_dev_mode and app_env == "production":
            raise RuntimeError("SQL_EXPLORER_DEV_MODE=true is forbidden when APP_ENV=production.")

        if rate_limiter is None:
            if not is_dev_mode:
                raise ValueError(
                    "SqlExplorerService requires a RateLimiter in production-like environments. "
                    "Set SQL_EXPLORER_DEV_MODE=true for local development only."
                )
            logger.warning("sql_explorer_rate_limiter_disabled_dev_mode")
        elif rate_limiter.fallback_mode != "deny":
            raise ValueError(
                "SqlExplorerService requires RateLimiter with fallback_mode='deny'."
            )

        self._rate_limiter = rate_limiter
        self._validator = SQLValidator()

        safe, failures = _verify_sandbox()
        if not safe:
            if app_env == "production":
                raise RuntimeError(
                    f"SQL Explorer sandbox probe failed in production: {', '.join(failures)}. "
                    "Ensure network egress is blocked and filesystem is read-only."
                )
            logger.warning("sql_explorer_sandbox_probe_failed", extra={"failures": failures})

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
            conn = _create_query_connection(dataset, available_tables=resolved_tables)
            try:
                result = await _execute_query_with_timeout(conn, limited_sql, timeout_seconds)
            finally:
                conn.close()

            execution_ms = int((time.monotonic() - start) * 1000)
            fingerprint = _fingerprint_query(query)
            _log_query(user, dataset, query, limited_sql, len(result), execution_ms, "success", None)
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
    "SensitiveTableAccessError",
    "ConcurrencyLimitError",
    "RateLimitExceededError",
    "can_query_dataset",
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
