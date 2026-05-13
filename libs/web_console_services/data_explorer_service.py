"""Service layer for dataset exploration.

Enforces query validation, RBAC, and rate limiting.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal, TypedDict, cast
from urllib.parse import urlencode
from uuid import uuid4

import polars as pl

from libs.data.data_pipeline.helpers import normalized_symbols_from_frame
from libs.data.data_pipeline.read_time_adjustment import (
    READ_TIME_ADJUSTMENT_AVAILABLE_REASON,
    READ_TIME_ADJUSTMENT_MODE_SPLIT_ADJUSTED,
    READ_TIME_ADJUSTMENT_MODE_UNAVAILABLE,
    READ_TIME_INVALID_SPLIT_ACTIONS_SKIPPED_REASON,
    READ_TIME_NO_SPLIT_ACTIONS_REASON,
    derive_split_adjusted_prices,
)
from libs.platform.web_console_auth.helpers import get_user_id
from libs.platform.web_console_auth.permissions import (
    Permission,
    has_dataset_permission,
    has_permission,
)
from libs.platform.web_console_auth.rate_limiter import RateLimiter, get_rate_limiter

from .alpaca_sip_manifest_helpers import (
    manifest_has_native_returns,
    summary_supports_split_adjustment,
)
from .data_manifest_service import (
    ALPACA_SIP_CORP_ACTIONS_DATASET,
    ALPACA_SIP_DAILY_DATASET,
    ALPACA_SIP_DATASET_KEY,
    AlpacaSipManifestSummaryDTO,
    DataManifestService,
    ManifestSummaryDTO,
)
from .schemas.data_management import (
    BacktestHandoffDTO,
    BacktestRoleProvenanceDTO,
    DataPreviewDTO,
    DatasetInfoDTO,
    ExportJobDTO,
    QueryResultDTO,
    QueryTemplateDTO,
)
from .sql_explorer_service import (
    ConcurrencyLimitError,
    SensitiveTableAccessError,
    SqlTableResolution,
    TablePathSpec,
    check_sensitive_tables,
    ensure_sql_explorer_execution_allowed,
    execute_scoped_query_frame_with_timeout,
    fingerprint_sql_query,
    log_sql_query_audit,
    resolve_sql_table_availability,
)
from .sql_validator import DATASET_TABLES, SQLValidator

logger = logging.getLogger(__name__)

_SUPPORTED_DATASETS = tuple(DATASET_TABLES)
_PREVIEW_TIMEOUT_SECONDS = 10
_PREVIEW_RATE_LIMIT = 30
_ADJUSTED_PREVIEW_LOOKBACK_DAYS = 31
_INTERACTIVE_ROW_LIMIT = 10_000
_INTERACTIVE_FETCH_LIMIT = _INTERACTIVE_ROW_LIMIT + 1
DATA_EXPORT_RATE_LIMIT = 5
DATA_EXPORT_WINDOW_SECONDS = 3600
_EXPORT_ROW_LIMIT = 100_000
# Keep availability scans cached while allowing fresh sync/manifest states to surface quickly.
_TABLE_AVAILABILITY_CACHE_TTL_SECONDS = 30
_MANIFEST_SUMMARY_CACHE_TTL_SECONDS = 30
_MANIFEST_SUMMARY_FAILURE_CACHE_TTL_SECONDS = 5
_MANIFEST_SUMMARY_TIMEOUT_SECONDS = 5.0
_TableAvailabilityCache = tuple[float, tuple[dict[str, SqlTableResolution], list[str]]]
_AlpacaSummaryCache = tuple[float, AlpacaSipManifestSummaryDTO | None, bool]
_SHARED_TABLE_AVAILABILITY_CACHE: _TableAvailabilityCache | None = None
_SHARED_TABLE_AVAILABILITY_LOCK = asyncio.Lock()
_SHARED_ALPACA_SUMMARY_CACHE: _AlpacaSummaryCache | None = None
_SHARED_ALPACA_SUMMARY_LOCK = asyncio.Lock()


class _DatasetAdjustmentMetadata(TypedDict, total=False):
    adjustment_mode: str | None
    canonical_storage_mode: str | None
    read_time_adjustment_mode: str | None
    null_column_reasons: dict[str, str]
    warnings: list[str]
    backtest_handoff: BacktestHandoffDTO | None


_DATASET_DESCRIPTIONS: dict[str, str] = {
    "crsp": "CRSP stock and index history",
    "compustat": "Compustat fundamentals",
    "taq": "TAQ intraday market data",
    "fama_french": "Fama-French factor datasets",
    ALPACA_SIP_DATASET_KEY: "Alpaca SIP local canonical data",
}
_PREFERRED_PREVIEW_TABLES: dict[str, tuple[str, ...]] = {
    ALPACA_SIP_DATASET_KEY: ("alpaca_sip_daily", "alpaca_sip_corp_actions"),
}
_ALPACA_SIP_TABLE_MANIFEST_DATASETS: dict[str, str] = {
    "alpaca_sip_daily": ALPACA_SIP_DAILY_DATASET,
    "alpaca_sip_corp_actions": ALPACA_SIP_CORP_ACTIONS_DATASET,
}
_PREFERRED_HANDOFF_QUERY_LABELS: dict[str, tuple[str, ...]] = {
    "crsp": ("Preview crsp_daily",),
    "compustat": ("Preview compustat_annual",),
    "fama_french": ("Preview ff_factors_daily",),
    "taq": ("Preview taq_trades",),
    ALPACA_SIP_DATASET_KEY: ("Latest daily bars", "Recent corporate actions"),
}
_NULL_COLUMN_REASONS_BY_TABLE: dict[str, dict[str, str]] = {
    "alpaca_sip_daily": {
        "adj_close": "raw_sip_returns_unavailable",
        "ret": "raw_sip_returns_unavailable",
    }
}
_RAW_SIP_RETURNS_UNAVAILABLE = "raw_sip_returns_unavailable"
_ALPACA_SIP_BACKTEST_ROLE_TABLES: dict[str, str] = {
    "universe": "alpaca_sip_daily",
    "prices": "alpaca_sip_daily",
    "corp_actions": "alpaca_sip_corp_actions",
}
_ALPACA_SIP_DAILY_ADJUSTMENT_MODE = "raw"
_ALPACA_SIP_DAILY_CANONICAL_STORAGE_MODE = "raw"
_ALPACA_SIP_DAILY_READ_TIME_ADJUSTMENT_MODE = READ_TIME_ADJUSTMENT_MODE_UNAVAILABLE
_ALPACA_SIP_CORP_ACTIONS_STORAGE_MODE = "read_only_adjustment_input"
_ADJUSTED_PREVIEW_UNAVAILABLE_REASON = "read_time_adjustment_layer_not_defined"
_ALPACA_SIP_MANIFEST_VALIDATION_FAILED_REASON = "alpaca_sip_manifest_validation_failed"
_ALPACA_SIP_UNTRUSTED_REASON = "alpaca_sip_untrusted_without_manifest"
_ALPACA_SIP_COMPANION_MANIFEST_STALE_REASON = "alpaca_sip_companion_manifest_stale"
_ALPACA_SIP_COMPANION_SYMBOL_SET_MISMATCH_REASON = "alpaca_sip_companion_symbol_set_mismatch"
_ALPACA_SIP_COMPANION_BLOCKING_REASONS = frozenset(
    {
        _ALPACA_SIP_COMPANION_MANIFEST_STALE_REASON,
        _ALPACA_SIP_COMPANION_SYMBOL_SET_MISMATCH_REASON,
    }
)


class RateLimitExceeded(RuntimeError):
    """Raised when a rate limit is exceeded."""


class DataExplorerService:
    """Service layer for dataset exploration.

    Enforces query validation, RBAC, and rate limiting.
    """

    def __init__(
        self,
        rate_limiter: RateLimiter | None = None,
        sql_validator: SQLValidator | None = None,
        manifest_service: DataManifestService | None = None,
        table_paths: dict[str, TablePathSpec] | None = None,
    ) -> None:
        self._rate_limiter = rate_limiter or get_rate_limiter()
        self._sql_validator = sql_validator or SQLValidator()
        self._uses_shared_alpaca_summary_cache = manifest_service is None
        self._manifest_service = manifest_service or DataManifestService()
        self._table_paths = table_paths
        self._uses_shared_table_availability_cache = table_paths is None
        self._table_availability_cache: _TableAvailabilityCache | None = None
        self._table_availability_lock = asyncio.Lock()
        self._alpaca_summary_cache: _AlpacaSummaryCache | None = None
        self._alpaca_summary_lock = asyncio.Lock()

    async def list_datasets(self, user: Any) -> list[DatasetInfoDTO]:
        """List available datasets with metadata.

        Permission: VIEW_DATA_SYNC (basic access)
        Dataset-level access: Filtered by user's dataset permissions
        """
        self._require_permission(user, Permission.VIEW_DATA_SYNC)

        (
            (table_resolutions, _warnings),
            (
                alpaca_summary,
                alpaca_summary_error,
            ),
        ) = await asyncio.gather(
            self._resolve_table_availability(),
            self._get_alpaca_summary(),
        )

        datasets: list[DatasetInfoDTO] = []
        for dataset in _SUPPORTED_DATASETS:
            if not has_dataset_permission(user, dataset):
                continue
            dataset_resolutions = [
                resolution
                for resolution in table_resolutions.values()
                if resolution.dataset == dataset
            ]
            available_tables = sorted(
                resolution.table
                for resolution in dataset_resolutions
                if resolution.available and not resolution.manifest_invalid
            )
            trusted_tables = sorted(
                resolution.table
                for resolution in dataset_resolutions
                if resolution.trusted_for_data_page
            )
            fallback_tables = sorted(
                resolution.table
                for resolution in dataset_resolutions
                if resolution.available
                and resolution.fallback_only
                and not resolution.manifest_invalid
            )
            invalid_manifest_tables = sorted(
                resolution.table
                for resolution in dataset_resolutions
                if resolution.manifest_invalid
            )
            queryable_state = _queryable_state(
                available_tables=available_tables,
                trusted_tables=trusted_tables,
                fallback_tables=fallback_tables,
                manifest_required=any(
                    resolution.manifest_required for resolution in dataset_resolutions
                ),
            )
            row_count: int | None = None
            date_range: dict[str, str] | None = None
            last_sync = None
            if (
                dataset == ALPACA_SIP_DATASET_KEY
                and alpaca_summary is not None
                and alpaca_summary.has_any_manifest
                and trusted_tables
            ):
                trusted_manifests = _trusted_alpaca_summary_manifests(
                    alpaca_summary,
                    trusted_tables,
                )
                if trusted_manifests:
                    row_count = sum(manifest.row_count for manifest in trusted_manifests)
                    sync_timestamps = [
                        manifest.sync_timestamp
                        for manifest in trusted_manifests
                        if manifest.sync_timestamp is not None
                    ]
                    last_sync = max(sync_timestamps, default=None)
                    starts = [manifest.start_date for manifest in trusted_manifests]
                    ends = [manifest.end_date for manifest in trusted_manifests]
                    if starts and ends:
                        date_range = {
                            "start": min(starts).isoformat(),
                            "end": max(ends).isoformat(),
                        }

            datasets.append(
                DatasetInfoDTO(
                    name=dataset,
                    description=_dataset_description(dataset, queryable_state),
                    row_count=row_count,
                    date_range=date_range,
                    symbol_count=None,
                    last_sync=last_sync,
                    tables=trusted_tables or available_tables,
                    queryable_state=queryable_state,
                    trusted_manifest_backed=any(
                        resolution.trusted_for_data_page and resolution.manifest_backed
                        for resolution in dataset_resolutions
                    ),
                    manifest_required=any(
                        resolution.manifest_required for resolution in dataset_resolutions
                    ),
                    availability_reason=_availability_reason(
                        queryable_state=queryable_state,
                        fallback_tables=fallback_tables,
                        invalid_manifest_tables=invalid_manifest_tables,
                        alpaca_summary_unavailable=(
                            dataset == ALPACA_SIP_DATASET_KEY and alpaca_summary_error
                        ),
                    ),
                    sql_handoff_url=_sql_handoff_url(dataset, trusted_tables),
                    query_templates=_query_templates_for_dataset(dataset, trusted_tables),
                    **_dataset_adjustment_metadata(
                        dataset,
                        trusted_tables=trusted_tables,
                        queryable_state=queryable_state,
                        alpaca_summary=alpaca_summary,
                        alpaca_summary_unavailable=alpaca_summary_error,
                    ),
                )
            )

        return datasets

    async def get_dataset_preview(
        self,
        user: Any,
        dataset: str,
        limit: int = 100,
        table: str | None = None,
        read_time_adjustment_mode: Literal["raw", "split_adjusted"] = "raw",
    ) -> DataPreviewDTO:
        """Get first N rows of dataset.

        Permission: QUERY_DATA + dataset-level access
        Limit: Max 1000 rows
        """
        started = time.monotonic()
        query = ""
        try:
            self._require_permission(user, Permission.QUERY_DATA)
            self._require_dataset_access(user, dataset)
            if limit > 1000:
                raise ValueError("Preview limit cannot exceed 1000 rows")
            if limit <= 0:
                raise ValueError("Preview limit must be positive")
            if read_time_adjustment_mode == READ_TIME_ADJUSTMENT_MODE_SPLIT_ADJUSTED:
                return await self._get_split_adjusted_preview(
                    user,
                    dataset=dataset,
                    limit=limit,
                    table=table,
                    started=started,
                )
            if read_time_adjustment_mode != "raw":
                raise ValueError(
                    f"Unsupported read-time adjustment mode: {read_time_adjustment_mode}"
                )

            (
                table_name,
                trusted_table_paths,
                resolution,
                provenance_trusted_tables,
            ) = await self._select_preview_table(dataset, requested_table=table)
            await self._enforce_rate_limit(
                user,
                action="data_preview",
                max_requests=_PREVIEW_RATE_LIMIT,
                window=60,
            )
            fetch_limit = limit + 1
            query = f"SELECT * FROM {table_name} LIMIT {fetch_limit}"
            frame = await self._execute_sql_frame(
                dataset=dataset,
                sql=query,
                table_paths=trusted_table_paths,
                timeout_seconds=_PREVIEW_TIMEOUT_SECONDS,
            )
            fetched_row_count = len(frame)
            has_more = fetched_row_count > limit
            if has_more:
                frame = frame.head(limit)
            preview_provenance = await self._preview_provenance_for_table(
                table_name,
                dataset=dataset,
                trusted_tables=provenance_trusted_tables,
            )
            execution_ms = int((time.monotonic() - started) * 1000)
            log_sql_query_audit(
                user,
                dataset,
                query,
                query,
                len(frame),
                execution_ms,
                "success",
                None,
            )

            return DataPreviewDTO(
                columns=list(frame.columns),
                rows=frame.to_dicts(),
                total_count=fetched_row_count,
                has_more=has_more,
                table=table_name,
                queryable_state=_queryable_state_for_table(resolution),
                trusted_manifest_backed=resolution.manifest_backed,
                sql_handoff_url=_sql_handoff_url(dataset, [table_name]),
                **preview_provenance,
            )
        except PermissionError:
            self._audit_query_failure(
                user,
                dataset,
                query,
                None,
                started,
                "authorization_denied",
                None,
            )
            raise
        except RateLimitExceeded as exc:
            self._audit_query_failure(
                user,
                dataset,
                query,
                None,
                started,
                "rate_limited",
                str(exc),
            )
            raise
        except ValueError as exc:
            self._audit_query_failure(
                user,
                dataset,
                query,
                None,
                started,
                "validation_error",
                str(exc),
            )
            raise
        except TimeoutError:
            self._audit_query_failure(user, dataset, query, None, started, "timeout", None)
            raise
        except ConcurrencyLimitError as exc:
            self._audit_query_failure(
                user,
                dataset,
                query,
                None,
                started,
                "concurrency_limit",
                str(exc),
            )
            raise
        except Exception as exc:
            self._audit_query_failure(user, dataset, query, None, started, "error", str(exc))
            raise

    async def execute_query(
        self,
        user: Any,
        dataset: str,
        query: str,
        timeout_seconds: int = 30,
        max_rows: int = _INTERACTIVE_ROW_LIMIT,
    ) -> QueryResultDTO:
        """Execute read-only SQL query against a SINGLE dataset.

        Permission: QUERY_DATA + dataset-level access for specified dataset
        Rate limit: 10 queries/minute per user (server-side)
        Security: Query validation + table reference validation (see SQL Security section)
        Streaming: Results paginated, max 10,000 rows per page
        Audit: Logged with user, dataset, query_fingerprint, row_count, duration

        CRITICAL: Query is scoped to specified dataset only.
        Cross-dataset queries are rejected at validation time.
        """
        started = time.monotonic()
        limited_query: str | None = None
        try:
            self._require_permission(user, Permission.QUERY_DATA)
            self._require_dataset_access(user, dataset)

            if max_rows <= 0:
                raise ValueError("Query row limit must be positive")

            # Validate SQL before rate limiting to fail fast on invalid queries
            valid, error = self._sql_validator.validate(query, dataset)
            if not valid:
                raise ValueError(f"Invalid query: {error}")
            result_row_limit = min(max_rows, _INTERACTIVE_ROW_LIMIT)

            tables = list(self._sql_validator.extract_tables(query) or [])
            check_sensitive_tables(tables)
            self._require_literal_smoke_query_when_tableless(query, tables)

            trusted_table_paths = await self._trusted_available_tables_for_query(dataset, tables)

            await self._enforce_rate_limit(user, action="data_query", max_requests=10, window=60)

            fetch_limit = min(result_row_limit + 1, _INTERACTIVE_FETCH_LIMIT)
            limited_query = self._sql_validator.enforce_row_limit(
                query,
                max_rows=fetch_limit,
            )

            frame = await self._execute_sql_frame(
                dataset=dataset,
                sql=limited_query,
                table_paths=trusted_table_paths,
                timeout_seconds=timeout_seconds,
            )
            fetched_row_count = len(frame)
            has_more = fetched_row_count > result_row_limit
            if has_more:
                frame = frame.head(result_row_limit)
            execution_ms = int((time.monotonic() - started) * 1000)
            log_sql_query_audit(
                user,
                dataset,
                query,
                limited_query,
                fetched_row_count,
                execution_ms,
                "success",
                None,
            )
            return QueryResultDTO(
                columns=list(frame.columns),
                rows=frame.to_dicts(),
                total_count=fetched_row_count,
                has_more=has_more,
                cursor=None,
                execution_ms=execution_ms,
                fingerprint=fingerprint_sql_query(query),
            )
        except PermissionError:
            self._audit_query_failure(
                user,
                dataset,
                query,
                limited_query,
                started,
                "authorization_denied",
                None,
            )
            raise
        # SensitiveTableAccessError subclasses ValueError; keep this branch first.
        except SensitiveTableAccessError as exc:
            self._audit_query_failure(
                user,
                dataset,
                query,
                limited_query,
                started,
                "security_blocked",
                str(exc),
            )
            raise
        except RateLimitExceeded as exc:
            self._audit_query_failure(
                user,
                dataset,
                query,
                limited_query,
                started,
                "rate_limited",
                str(exc),
            )
            raise
        except TimeoutError:
            self._audit_query_failure(
                user,
                dataset,
                query,
                limited_query,
                started,
                "timeout",
                None,
            )
            raise
        except ConcurrencyLimitError as exc:
            self._audit_query_failure(
                user,
                dataset,
                query,
                limited_query,
                started,
                "concurrency_limit",
                str(exc),
            )
            raise
        except ValueError as exc:
            self._audit_query_failure(
                user,
                dataset,
                query,
                limited_query,
                started,
                "validation_error",
                str(exc),
            )
            raise
        except Exception as exc:
            self._audit_query_failure(
                user,
                dataset,
                query,
                limited_query,
                started,
                "error",
                str(exc),
            )
            raise

    async def export_data(
        self,
        user: Any,
        dataset: str,
        query: str,
        format: Literal["csv", "parquet"],
    ) -> ExportJobDTO:
        """Export query results to file from a SINGLE dataset.

        Permission: EXPORT_DATA + dataset-level access for specified dataset
        Rate limit: 5 exports/hour per user (server-side)
        Limit: Max 100,000 rows
        Audit: Logged with user, dataset, query_fingerprint, row_count, format
        Storage: Temp directory with 24-hour TTL, auto-cleanup via cron job
        """
        started = time.monotonic()
        limited_query: str | None = None
        try:
            self._require_permission(user, Permission.EXPORT_DATA)
            self._require_dataset_access(user, dataset)

            # Validate SQL before rate limiting to fail fast on invalid queries
            valid, error = self._sql_validator.validate(query, dataset)
            if not valid:
                raise ValueError(f"Invalid query: {error}")

            tables = list(self._sql_validator.extract_tables(query) or [])
            check_sensitive_tables(tables)
            self._require_literal_smoke_query_when_tableless(query, tables)

            await self._trusted_available_tables_for_query(dataset, tables)

            await self._enforce_rate_limit(
                user,
                action="data_export",
                max_requests=DATA_EXPORT_RATE_LIMIT,
                window=DATA_EXPORT_WINDOW_SECONDS,
            )

            limited_query = self._sql_validator.enforce_row_limit(query, max_rows=_EXPORT_ROW_LIMIT)

            execution_ms = int((time.monotonic() - started) * 1000)
            log_sql_query_audit(
                user,
                dataset,
                query,
                limited_query,
                0,
                execution_ms,
                "queued",
                None,
            )

            # TODO: Queue export job to background worker
            now = datetime.now(UTC)
            return ExportJobDTO(
                id=str(uuid4()),
                status="queued",
                format=format,
                row_count=None,
                file_path=None,
                expires_at=now,
            )
        except PermissionError:
            self._audit_query_failure(
                user,
                dataset,
                query,
                limited_query,
                started,
                "authorization_denied",
                None,
            )
            raise
        # SensitiveTableAccessError subclasses ValueError; keep this branch first.
        except SensitiveTableAccessError as exc:
            self._audit_query_failure(
                user,
                dataset,
                query,
                limited_query,
                started,
                "security_blocked",
                str(exc),
            )
            raise
        except RateLimitExceeded as exc:
            self._audit_query_failure(
                user,
                dataset,
                query,
                limited_query,
                started,
                "rate_limited",
                str(exc),
            )
            raise
        except ValueError as exc:
            self._audit_query_failure(
                user,
                dataset,
                query,
                limited_query,
                started,
                "validation_error",
                str(exc),
            )
            raise
        except Exception as exc:
            self._audit_query_failure(
                user,
                dataset,
                query,
                limited_query,
                started,
                "error",
                str(exc),
            )
            raise

    async def _get_split_adjusted_preview(
        self,
        user: Any,
        *,
        dataset: str,
        limit: int,
        table: str | None,
        started: float,
    ) -> DataPreviewDTO:
        if dataset != ALPACA_SIP_DATASET_KEY:
            raise ValueError("Split-adjusted preview is only available for alpaca_sip")
        if table not in {None, "alpaca_sip_daily"}:
            raise ValueError("Split-adjusted preview is only available for alpaca_sip_daily")

        referenced_tables = ["alpaca_sip_daily", "alpaca_sip_corp_actions"]
        trusted_table_paths = await self._trusted_available_tables_for_query(
            dataset,
            referenced_tables,
        )
        table_resolutions, _warnings = await self._resolve_table_availability()
        daily_resolution = table_resolutions["alpaca_sip_daily"]
        provenance_trusted_tables = sorted(trusted_table_paths)

        alpaca_summary, alpaca_summary_unavailable = await self._get_alpaca_summary()
        if not _read_time_adjustment_available(
            trusted_tables=provenance_trusted_tables,
            alpaca_summary=alpaca_summary,
            alpaca_summary_unavailable=alpaca_summary_unavailable,
        ):
            handoff = _build_backtest_handoff(
                dataset,
                trusted_tables=provenance_trusted_tables,
                alpaca_summary=alpaca_summary,
                alpaca_summary_unavailable=alpaca_summary_unavailable,
                queryable_state=_queryable_state_for_table(daily_resolution),
            )
            reasons = (
                ", ".join(handoff.reason_codes)
                if handoff is not None and handoff.reason_codes
                else _ADJUSTED_PREVIEW_UNAVAILABLE_REASON
            )
            raise ValueError(f"Split-adjusted preview unavailable: {reasons}")

        await self._enforce_rate_limit(
            user,
            action="data_preview",
            max_requests=_PREVIEW_RATE_LIMIT,
            window=60,
        )

        fetch_limit = limit + 1
        price_query_parameters: list[Any] | None = None
        preview_start_date = _split_adjusted_preview_start_date(
            alpaca_summary,
            provenance_trusted_tables,
        )
        if preview_start_date is None:
            price_query = (
                f"SELECT * FROM alpaca_sip_daily ORDER BY symbol, date LIMIT {fetch_limit}"
            )
        else:
            price_query_parameters = [preview_start_date]
            price_query = (
                "SELECT * FROM alpaca_sip_daily "
                "WHERE date >= CAST(? AS DATE) "
                f"ORDER BY symbol, date LIMIT {fetch_limit}"
            )
        prices = await self._execute_sql_frame(
            dataset=dataset,
            sql=price_query,
            table_paths={"alpaca_sip_daily": trusted_table_paths["alpaca_sip_daily"]},
            timeout_seconds=_PREVIEW_TIMEOUT_SECONDS,
            parameters=price_query_parameters,
        )
        fetched_row_count = len(prices)
        has_more = fetched_row_count > limit
        warmup_prices = await self._load_split_adjusted_warmup_prices(
            dataset=dataset,
            prices=prices,
            preview_start_date=preview_start_date,
            table_paths=trusted_table_paths,
        )
        adjustment_prices = _prepend_split_adjusted_warmup_prices(
            prices=prices,
            warmup_prices=warmup_prices,
        )

        corp_actions = await self._load_corporate_actions_for_prices(
            dataset=dataset,
            prices=adjustment_prices,
            table_paths=trusted_table_paths,
        )
        adjustment_result = derive_split_adjusted_prices(adjustment_prices, corp_actions)
        frame = adjustment_result.frame
        if preview_start_date is not None and not warmup_prices.is_empty():
            frame = _drop_split_adjusted_warmup_rows(frame, preview_start_date)
        if has_more:
            frame = frame.head(limit)

        provenance = await self._preview_provenance_for_table(
            "alpaca_sip_daily",
            dataset=dataset,
            trusted_tables=provenance_trusted_tables,
        )
        warnings = sorted(
            {
                *(
                    str(item)
                    for item in provenance.get("warnings", [])
                    if str(item) != _RAW_SIP_RETURNS_UNAVAILABLE
                ),
                *(
                    code
                    for code in adjustment_result.reason_codes
                    if code
                    in {
                        READ_TIME_INVALID_SPLIT_ACTIONS_SKIPPED_REASON,
                        READ_TIME_NO_SPLIT_ACTIONS_REASON,
                    }
                ),
            }
        )
        provenance.update(
            {
                "read_time_adjustment_mode": READ_TIME_ADJUSTMENT_MODE_SPLIT_ADJUSTED,
                "null_column_reasons": {},
                "warnings": warnings,
                "derived": True,
                "derivation_mode": READ_TIME_ADJUSTMENT_MODE_SPLIT_ADJUSTED,
                "derivation_reason_codes": list(adjustment_result.reason_codes),
            }
        )

        execution_ms = int((time.monotonic() - started) * 1000)
        log_sql_query_audit(
            user,
            dataset,
            "READ_TIME_ADJUSTMENT_PREVIEW split_adjusted alpaca_sip_daily",
            price_query,
            len(frame),
            execution_ms,
            "success",
            None,
        )
        return DataPreviewDTO(
            columns=list(frame.columns),
            rows=frame.to_dicts(),
            total_count=fetched_row_count,
            has_more=has_more,
            table="alpaca_sip_daily",
            queryable_state=_queryable_state_for_table(daily_resolution),
            trusted_manifest_backed=daily_resolution.manifest_backed,
            sql_handoff_url=_sql_handoff_url(dataset, ["alpaca_sip_daily"]),
            **provenance,
        )

    async def _load_split_adjusted_warmup_prices(
        self,
        *,
        dataset: str,
        prices: pl.DataFrame,
        preview_start_date: date | None,
        table_paths: dict[str, TablePathSpec],
    ) -> pl.DataFrame:
        if preview_start_date is None or prices.is_empty():
            return pl.DataFrame()

        symbols = normalized_symbols_from_frame(prices)
        if not symbols:
            return pl.DataFrame()

        symbol_placeholders = ", ".join("?" for _symbol in symbols)
        warmup_query = (
            "SELECT * FROM ("
            "SELECT *, row_number() OVER (PARTITION BY symbol ORDER BY date DESC) "
            "AS __rta_warmup_rank "
            "FROM alpaca_sip_daily "
            f"WHERE symbol IN ({symbol_placeholders}) "
            "AND date < CAST(? AS DATE)"
            ") WHERE __rta_warmup_rank = 1 "
            "ORDER BY symbol, date"
        )
        warmup = cast(
            pl.DataFrame,
            await self._execute_sql_frame(
                dataset=dataset,
                sql=warmup_query,
                table_paths={"alpaca_sip_daily": table_paths["alpaca_sip_daily"]},
                timeout_seconds=_PREVIEW_TIMEOUT_SECONDS,
                parameters=[*symbols, preview_start_date],
            ),
        )
        if "__rta_warmup_rank" in warmup.columns:
            return warmup.drop("__rta_warmup_rank")
        return warmup

    async def _load_corporate_actions_for_prices(
        self,
        *,
        dataset: str,
        prices: pl.DataFrame,
        table_paths: dict[str, TablePathSpec],
    ) -> pl.DataFrame:
        corp_query_parameters: list[Any] | None = None
        if prices.is_empty():
            corp_query = "SELECT * FROM alpaca_sip_corp_actions LIMIT 0"
        else:
            date_bounds = _price_date_bounds(prices)
            symbols = normalized_symbols_from_frame(prices)
            if date_bounds is None or not symbols:
                corp_query = "SELECT * FROM alpaca_sip_corp_actions LIMIT 0"
            else:
                start_date, _end_date = date_bounds
                symbol_placeholders = ", ".join("?" for _symbol in symbols)
                corp_query_parameters = [*symbols, start_date]
                corp_query = (
                    "SELECT * FROM alpaca_sip_corp_actions "
                    f"WHERE symbol IN ({symbol_placeholders}) "
                    "AND coalesce(ex_date, process_date) >= CAST(? AS DATE) "
                    "ORDER BY symbol, coalesce(ex_date, process_date)"
                )
        return cast(
            pl.DataFrame,
            await self._execute_sql_frame(
                dataset=dataset,
                sql=corp_query,
                table_paths={"alpaca_sip_corp_actions": table_paths["alpaca_sip_corp_actions"]},
                timeout_seconds=_PREVIEW_TIMEOUT_SECONDS,
                parameters=corp_query_parameters,
            ),
        )

    def _require_permission(self, user: Any, permission: Permission) -> None:
        if not has_permission(user, permission):
            raise PermissionError(f"Permission {permission.value} required")

    def _require_dataset_access(self, user: Any, dataset: str) -> None:
        if not has_dataset_permission(user, dataset):
            raise PermissionError(f"Dataset access required for {dataset}")

    async def _enforce_rate_limit(
        self,
        user: Any,
        *,
        action: str,
        max_requests: int,
        window: int,
    ) -> None:
        user_id = get_user_id(user)
        allowed, _remaining = await self._rate_limit_check(
            user_id, action=action, max_requests=max_requests, window=window
        )
        if not allowed:
            raise RateLimitExceeded(
                f"Rate limit exceeded for {action}: {max_requests} per {window} seconds"
            )

    async def _rate_limit_check(
        self,
        user_id: str,
        *,
        action: str,
        max_requests: int,
        window: int,
    ) -> tuple[bool, int]:
        return await self._rate_limiter.check_rate_limit(
            user_id=user_id,
            action=action,
            max_requests=max_requests,
            window_seconds=window,
        )

    async def _resolve_table_availability(
        self,
    ) -> tuple[dict[str, SqlTableResolution], list[str]]:
        global _SHARED_TABLE_AVAILABILITY_CACHE

        now = time.monotonic()
        cache = (
            _SHARED_TABLE_AVAILABILITY_CACHE
            if self._uses_shared_table_availability_cache
            else self._table_availability_cache
        )
        if cache is not None and now - cache[0] < _TABLE_AVAILABILITY_CACHE_TTL_SECONDS:
            return cache[1]

        lock = (
            _SHARED_TABLE_AVAILABILITY_LOCK
            if self._uses_shared_table_availability_cache
            else self._table_availability_lock
        )
        async with lock:
            now = time.monotonic()
            cache = (
                _SHARED_TABLE_AVAILABILITY_CACHE
                if self._uses_shared_table_availability_cache
                else self._table_availability_cache
            )
            if cache is not None and now - cache[0] < _TABLE_AVAILABILITY_CACHE_TTL_SECONDS:
                return cache[1]

            result = await asyncio.to_thread(resolve_sql_table_availability, self._table_paths)
            if self._uses_shared_table_availability_cache:
                _SHARED_TABLE_AVAILABILITY_CACHE = (now, result)
            else:
                self._table_availability_cache = (now, result)
            return result

    async def _get_alpaca_summary(self) -> tuple[AlpacaSipManifestSummaryDTO | None, bool]:
        global _SHARED_ALPACA_SUMMARY_CACHE

        now = time.monotonic()
        cache = (
            _SHARED_ALPACA_SUMMARY_CACHE
            if self._uses_shared_alpaca_summary_cache
            else self._alpaca_summary_cache
        )
        cache_ttl = (
            _MANIFEST_SUMMARY_FAILURE_CACHE_TTL_SECONDS
            if cache is not None and cache[2]
            else _MANIFEST_SUMMARY_CACHE_TTL_SECONDS
        )
        if cache is not None and now - cache[0] < cache_ttl:
            return cache[1], cache[2]

        lock = (
            _SHARED_ALPACA_SUMMARY_LOCK
            if self._uses_shared_alpaca_summary_cache
            else self._alpaca_summary_lock
        )
        async with lock:
            now = time.monotonic()
            cache = (
                _SHARED_ALPACA_SUMMARY_CACHE
                if self._uses_shared_alpaca_summary_cache
                else self._alpaca_summary_cache
            )
            cache_ttl = (
                _MANIFEST_SUMMARY_FAILURE_CACHE_TTL_SECONDS
                if cache is not None and cache[2]
                else _MANIFEST_SUMMARY_CACHE_TTL_SECONDS
            )
            if cache is not None and now - cache[0] < cache_ttl:
                return cache[1], cache[2]

            try:
                alpaca_summary = await asyncio.wait_for(
                    asyncio.to_thread(self._manifest_service.get_alpaca_sip_summary),
                    timeout=_MANIFEST_SUMMARY_TIMEOUT_SECONDS,
                )
                next_cache: _AlpacaSummaryCache = (time.monotonic(), alpaca_summary, False)
                if self._uses_shared_alpaca_summary_cache:
                    _SHARED_ALPACA_SUMMARY_CACHE = next_cache
                else:
                    self._alpaca_summary_cache = next_cache
                return alpaca_summary, False
            except Exception as exc:
                logger.warning(
                    "data_explorer_alpaca_manifest_summary_unavailable",
                    extra={"error_type": type(exc).__name__},
                    exc_info=True,
                )
                next_cache = (time.monotonic(), None, True)
                if self._uses_shared_alpaca_summary_cache:
                    _SHARED_ALPACA_SUMMARY_CACHE = next_cache
                else:
                    self._alpaca_summary_cache = next_cache
                return None, True

    async def _preview_provenance_for_table(
        self,
        table_name: str,
        *,
        dataset: str | None = None,
        trusted_tables: list[str] | None = None,
    ) -> dict[str, Any]:
        null_column_reasons = dict(_NULL_COLUMN_REASONS_BY_TABLE.get(table_name, {}))
        warnings = sorted(set(null_column_reasons.values()))
        manifest_dataset = _ALPACA_SIP_TABLE_MANIFEST_DATASETS.get(table_name)
        if manifest_dataset is None:
            return {
                "null_column_reasons": null_column_reasons,
                "warnings": warnings,
            }

        alpaca_summary, summary_unavailable = await self._get_alpaca_summary()
        if alpaca_summary is None or summary_unavailable:
            return {
                "null_column_reasons": null_column_reasons,
                "warnings": [*warnings, "alpaca_sip_manifest_summary_unavailable"],
                "backtest_handoff": _build_backtest_handoff(
                    dataset or "",
                    trusted_tables=trusted_tables or [],
                    alpaca_summary=None,
                    alpaca_summary_unavailable=True,
                    queryable_state="missing",
                ),
            }

        trusted_table_names = trusted_tables if trusted_tables is not None else [table_name]
        manifest = _trusted_manifest_for_table(
            alpaca_summary,
            table_name,
            trusted_table_names,
        )
        if manifest is None:
            warning_code = _preview_manifest_warning_code(
                alpaca_summary,
                table_name,
                trusted_table_names,
            )
            return {
                "null_column_reasons": null_column_reasons,
                "warnings": [*warnings, warning_code],
                "backtest_handoff": _build_backtest_handoff(
                    dataset or "",
                    trusted_tables=trusted_table_names,
                    alpaca_summary=alpaca_summary,
                    alpaca_summary_unavailable=False,
                    queryable_state="missing",
                ),
            }

        if table_name == "alpaca_sip_daily" and manifest.read_time_adjustment_mode == "available":
            null_column_reasons = {}
            warnings = []

        return {
            "manifest_id": manifest.manifest_id,
            "manifest_reference": manifest.manifest_reference,
            "manifest_checksum": manifest.manifest_checksum,
            "manifest_version": str(manifest.manifest_version),
            "provider_id": manifest.provider_id,
            "provider_version": manifest.provider_version,
            "source_feed": manifest.source_feed,
            "adjustment_mode": manifest.adjustment_mode,
            "canonical_storage_mode": manifest.canonical_storage_mode,
            "read_time_adjustment_mode": manifest.read_time_adjustment_mode,
            "provider_signature": manifest.provider_signature,
            "null_column_reasons": null_column_reasons,
            "warnings": warnings,
            "backtest_handoff": _build_backtest_handoff(
                dataset or "",
                trusted_tables=trusted_table_names,
                alpaca_summary=alpaca_summary,
                alpaca_summary_unavailable=False,
                queryable_state="trusted_manifest_backed",
            ),
        }

    def _audit_query_failure(
        self,
        user: Any,
        dataset: str,
        query: str,
        limited_query: str | None,
        started: float,
        status: str,
        error_message: str | None,
    ) -> None:
        execution_ms = int((time.monotonic() - started) * 1000)
        log_sql_query_audit(
            user,
            dataset,
            query,
            limited_query,
            0,
            execution_ms,
            status,
            error_message,
        )

    async def _select_preview_table(
        self,
        dataset: str,
        *,
        requested_table: str | None,
    ) -> tuple[str, dict[str, TablePathSpec], SqlTableResolution, list[str]]:
        table_resolutions, _warnings = await self._resolve_table_availability()
        allowed_tables = DATASET_TABLES.get(dataset)
        if not allowed_tables:
            raise ValueError(f"Unknown dataset: {dataset}")
        candidate_tables = (
            [requested_table]
            if requested_table
            else _preview_candidate_tables(dataset, allowed_tables)
        )
        trusted_table_paths = _trusted_table_paths_for_dataset(table_resolutions, dataset)
        saw_fallback_only = False
        saw_manifest_invalid = False
        for table in candidate_tables:
            if table not in allowed_tables:
                raise ValueError(f"Table {table} is not available for dataset {dataset}")
            resolution = table_resolutions.get(table)
            if resolution is None or not resolution.available:
                if resolution is not None and resolution.manifest_invalid:
                    saw_manifest_invalid = True
                    if requested_table is not None:
                        raise ValueError(
                            f"Trusted manifest is invalid for table {table}; re-run validation"
                        )
                if requested_table is not None:
                    raise ValueError(f"No local data available for table {table}")
                continue
            if not resolution.trusted_for_data_page:
                saw_fallback_only = saw_fallback_only or resolution.fallback_only
                saw_manifest_invalid = saw_manifest_invalid or resolution.manifest_invalid
                if requested_table is not None:
                    if resolution.manifest_invalid:
                        raise ValueError(
                            f"Trusted manifest is invalid for table {table}; re-run validation"
                        )
                    if resolution.fallback_only:
                        raise ValueError(
                            f"{table} is queryable fallback only; trusted manifest required for /data"
                        )
                    raise ValueError(f"No trusted local data available for table {table}")
                continue
            provenance_trusted_tables = (
                sorted(trusted_table_paths) if dataset == ALPACA_SIP_DATASET_KEY else [table]
            )
            return table, {table: trusted_table_paths[table]}, resolution, provenance_trusted_tables

        if saw_manifest_invalid:
            raise ValueError(f"Trusted manifest is invalid for {dataset}; re-run validation")
        if saw_fallback_only:
            raise ValueError(f"Fallback parquet exists for {dataset}; trusted manifest required")
        raise ValueError(f"No trusted local data available for {dataset}")

    async def _trusted_available_tables_for_query(
        self,
        dataset: str,
        referenced_tables: list[str],
    ) -> dict[str, TablePathSpec]:
        if not referenced_tables:
            return {}

        table_resolutions, _warnings = await self._resolve_table_availability()
        trusted_table_paths = _trusted_table_paths_for_dataset(table_resolutions, dataset)

        for table in referenced_tables:
            resolution = table_resolutions.get(table)
            if resolution is None or resolution.dataset != dataset:
                raise ValueError(f"Table {table} is not available for dataset {dataset}")
            if not resolution.available:
                if resolution.manifest_invalid:
                    raise ValueError(
                        f"Trusted manifest is invalid for table {table}; re-run validation"
                    )
                raise ValueError(f"No local data available for table {table}")
            if not resolution.trusted_for_data_page:
                if resolution.manifest_invalid:
                    raise ValueError(
                        f"Trusted manifest is invalid for table {table}; re-run validation"
                    )
                if resolution.fallback_only:
                    raise ValueError(
                        f"{table} is queryable fallback only; trusted manifest required for /data"
                    )
                raise ValueError(f"No trusted local data available for table {table}")
        missing_trusted = sorted(set(referenced_tables) - set(trusted_table_paths))
        if missing_trusted:
            raise ValueError(
                "Trusted local data required for tables: " + ", ".join(missing_trusted)
            )
        return {table: trusted_table_paths[table] for table in sorted(set(referenced_tables))}

    def _require_literal_smoke_query_when_tableless(
        self,
        query: str,
        referenced_tables: list[str],
    ) -> None:
        if referenced_tables:
            return
        if not self._sql_validator.is_literal_smoke_query(query):
            raise ValueError(
                "Tableless queries must be a single literal SELECT, such as SELECT 1; "
                "reference an approved dataset table for data access"
            )

    async def _execute_sql_frame(
        self,
        *,
        dataset: str,
        sql: str,
        table_paths: dict[str, TablePathSpec],
        timeout_seconds: int,
        parameters: list[Any] | None = None,
    ) -> Any:
        await asyncio.to_thread(ensure_sql_explorer_execution_allowed)
        return await execute_scoped_query_frame_with_timeout(
            dataset,
            sql,
            timeout_seconds,
            available_tables=set(table_paths),
            table_paths=table_paths,
            parameters=parameters,
        )


def _trusted_table_paths_for_dataset(
    table_resolutions: dict[str, SqlTableResolution],
    dataset: str,
) -> dict[str, TablePathSpec]:
    trusted_paths: dict[str, TablePathSpec] = {}
    for item in table_resolutions.values():
        if item.dataset != dataset or not item.trusted_for_data_page or item.path_spec is None:
            continue
        trusted_paths[item.table] = item.path_spec
    return trusted_paths


def _price_date_bounds(prices: pl.DataFrame) -> tuple[date, date] | None:
    if "date" not in prices.columns or prices.is_empty():
        return None
    dates = prices.select(pl.col("date").cast(pl.Date))
    min_date = dates.select(pl.col("date").min()).item()
    max_date = dates.select(pl.col("date").max()).item()
    if not isinstance(min_date, date) or not isinstance(max_date, date):
        return None
    return min_date, max_date


def _split_adjusted_preview_start_date(
    alpaca_summary: AlpacaSipManifestSummaryDTO | None,
    trusted_tables: list[str],
) -> date | None:
    daily_manifest = _trusted_manifest_for_table(
        alpaca_summary,
        "alpaca_sip_daily",
        trusted_tables,
    )
    if daily_manifest is None:
        return None
    lookback_start = daily_manifest.end_date - timedelta(days=_ADJUSTED_PREVIEW_LOOKBACK_DAYS)
    return max(daily_manifest.start_date, lookback_start)


def _prepend_split_adjusted_warmup_prices(
    *,
    prices: pl.DataFrame,
    warmup_prices: pl.DataFrame,
) -> pl.DataFrame:
    if prices.is_empty() or warmup_prices.is_empty():
        return prices
    return pl.concat([warmup_prices, prices], how="vertical_relaxed")


def _drop_split_adjusted_warmup_rows(
    frame: pl.DataFrame,
    preview_start_date: date,
) -> pl.DataFrame:
    if frame.is_empty() or "date" not in frame.columns:
        return frame
    return frame.filter(pl.col("date").cast(pl.Date, strict=False) >= preview_start_date)


def _preview_candidate_tables(dataset: str, allowed_tables: list[str]) -> list[str]:
    allowed_set = set(allowed_tables)
    preferred = _PREFERRED_PREVIEW_TABLES.get(dataset, ())
    ordered = [table for table in preferred if table in allowed_set]
    ordered.extend(sorted(allowed_set - set(ordered)))
    return ordered


def _queryable_state(
    *,
    available_tables: list[str],
    trusted_tables: list[str],
    fallback_tables: list[str],
    manifest_required: bool,
) -> str:
    if trusted_tables:
        return "trusted_manifest_backed" if manifest_required else "queryable"
    if fallback_tables:
        return "queryable_fallback_only"
    if available_tables:
        return "queryable"
    return "missing"


def _queryable_state_for_table(resolution: SqlTableResolution) -> str:
    if resolution.manifest_backed:
        return "trusted_manifest_backed"
    if resolution.fallback_only:
        return "queryable_fallback_only"
    if resolution.available:
        return "queryable"
    return "missing"


def _trusted_alpaca_summary_manifests(
    alpaca_summary: AlpacaSipManifestSummaryDTO,
    trusted_tables: list[str],
) -> list[ManifestSummaryDTO]:
    trusted_manifest_datasets = {
        _ALPACA_SIP_TABLE_MANIFEST_DATASETS.get(table, table) for table in trusted_tables
    }
    return [
        manifest
        for manifest in alpaca_summary.manifests
        if manifest.dataset in trusted_manifest_datasets
        and manifest.validation_status.lower() == "passed"
    ]


def _read_time_adjustment_available(
    *,
    trusted_tables: list[str],
    alpaca_summary: AlpacaSipManifestSummaryDTO | None,
    alpaca_summary_unavailable: bool,
) -> bool:
    if alpaca_summary is None or alpaca_summary_unavailable:
        return False
    if not summary_supports_split_adjustment(alpaca_summary):
        return False
    return (
        _trusted_manifest_for_table(alpaca_summary, "alpaca_sip_daily", trusted_tables) is not None
        and _trusted_manifest_for_table(
            alpaca_summary,
            "alpaca_sip_corp_actions",
            trusted_tables,
        )
        is not None
    )


def _dataset_adjustment_metadata(
    dataset: str,
    *,
    trusted_tables: list[str],
    queryable_state: str,
    alpaca_summary: AlpacaSipManifestSummaryDTO | None,
    alpaca_summary_unavailable: bool,
) -> _DatasetAdjustmentMetadata:
    if dataset != ALPACA_SIP_DATASET_KEY:
        return {}

    read_time_available = _read_time_adjustment_available(
        trusted_tables=trusted_tables,
        alpaca_summary=alpaca_summary,
        alpaca_summary_unavailable=alpaca_summary_unavailable,
    )
    daily_manifest = _trusted_manifest_for_table(
        alpaca_summary,
        "alpaca_sip_daily",
        trusted_tables,
    )
    daily_returns_available = manifest_has_native_returns(daily_manifest)
    returns_available = read_time_available or daily_returns_available
    warnings: set[str] = set()
    if not returns_available:
        warnings.update(_NULL_COLUMN_REASONS_BY_TABLE["alpaca_sip_daily"].values())
    if alpaca_summary_unavailable:
        warnings.add("alpaca_sip_manifest_summary_unavailable")
    if alpaca_summary is not None:
        warnings.update(str(warning) for warning in alpaca_summary.warnings)

    return {
        "adjustment_mode": _manifest_text(
            daily_manifest.adjustment_mode if daily_manifest is not None else None,
            _ALPACA_SIP_DAILY_ADJUSTMENT_MODE,
        ),
        "canonical_storage_mode": _manifest_text(
            daily_manifest.canonical_storage_mode if daily_manifest is not None else None,
            _ALPACA_SIP_DAILY_CANONICAL_STORAGE_MODE,
        ),
        "read_time_adjustment_mode": (
            READ_TIME_ADJUSTMENT_MODE_SPLIT_ADJUSTED
            if read_time_available
            else _manifest_text(
                daily_manifest.read_time_adjustment_mode if daily_manifest is not None else None,
                _ALPACA_SIP_DAILY_READ_TIME_ADJUSTMENT_MODE,
            )
        ),
        "null_column_reasons": (
            {} if returns_available else dict(_NULL_COLUMN_REASONS_BY_TABLE["alpaca_sip_daily"])
        ),
        "warnings": sorted(warnings),
        "backtest_handoff": _build_backtest_handoff(
            dataset,
            trusted_tables=trusted_tables,
            alpaca_summary=alpaca_summary,
            alpaca_summary_unavailable=alpaca_summary_unavailable,
            queryable_state=queryable_state,
        ),
    }


def _build_backtest_handoff(
    dataset: str,
    *,
    trusted_tables: list[str],
    alpaca_summary: AlpacaSipManifestSummaryDTO | None,
    alpaca_summary_unavailable: bool,
    queryable_state: str,
) -> BacktestHandoffDTO | None:
    if dataset != ALPACA_SIP_DATASET_KEY:
        return None

    read_time_available = _read_time_adjustment_available(
        trusted_tables=trusted_tables,
        alpaca_summary=alpaca_summary,
        alpaca_summary_unavailable=alpaca_summary_unavailable,
    )
    reason_codes: set[str] = (
        {READ_TIME_ADJUSTMENT_AVAILABLE_REASON}
        if read_time_available
        else {_ADJUSTED_PREVIEW_UNAVAILABLE_REASON}
    )
    if queryable_state == "queryable_fallback_only":
        reason_codes.add("alpaca_sip_untrusted_without_manifest")
    if alpaca_summary_unavailable:
        reason_codes.add("alpaca_sip_manifest_summary_unavailable")
    if alpaca_summary is not None:
        reason_codes.update(
            str(warning)
            for warning in alpaca_summary.warnings
            if warning in _ALPACA_SIP_COMPANION_BLOCKING_REASONS
        )

    daily_manifest = _trusted_manifest_for_table(
        alpaca_summary,
        "alpaca_sip_daily",
        trusted_tables,
    )
    selected_read_time_adjustment_mode = (
        READ_TIME_ADJUSTMENT_MODE_SPLIT_ADJUSTED
        if read_time_available
        else _manifest_text(
            daily_manifest.read_time_adjustment_mode if daily_manifest is not None else None,
            _ALPACA_SIP_DAILY_READ_TIME_ADJUSTMENT_MODE,
        )
    )

    data_roles: dict[str, BacktestRoleProvenanceDTO] = {}
    for role, table in _ALPACA_SIP_BACKTEST_ROLE_TABLES.items():
        manifest = _trusted_manifest_for_table(alpaca_summary, table, trusted_tables)
        if manifest is None:
            reason = _backtest_manifest_unavailable_reason(
                alpaca_summary,
                table,
                trusted_tables,
                alpaca_summary_unavailable=alpaca_summary_unavailable,
            )
            reason_codes.add(reason)
            data_roles[role] = _missing_role_provenance(role, table, reason)
            if table == "alpaca_sip_daily":
                reason_codes.add("raw_sip_returns_unavailable")
            continue

        data_roles[role] = _role_provenance_from_manifest(
            role,
            table,
            manifest,
            read_time_adjustment_mode_override=(
                READ_TIME_ADJUSTMENT_MODE_SPLIT_ADJUSTED
                if read_time_available and table == "alpaca_sip_daily"
                else None
            ),
        )
        if table == "alpaca_sip_daily":
            read_mode = _manifest_text(
                manifest.read_time_adjustment_mode,
                _ALPACA_SIP_DAILY_READ_TIME_ADJUSTMENT_MODE,
            )
            if read_mode != "available" and not read_time_available:
                reason_codes.add("raw_sip_returns_unavailable")

    return BacktestHandoffDTO(
        dataset=dataset,
        data_roles=data_roles,
        selected_read_time_adjustment_mode=selected_read_time_adjustment_mode,
        derived=read_time_available,
        adjusted_preview_available=read_time_available,
        adjusted_preview_unavailable_reason=(
            None if read_time_available else _ADJUSTED_PREVIEW_UNAVAILABLE_REASON
        ),
        reason_codes=sorted(reason_codes),
    )


def _trusted_manifest_for_table(
    alpaca_summary: AlpacaSipManifestSummaryDTO | None,
    table: str,
    trusted_tables: list[str],
) -> ManifestSummaryDTO | None:
    if alpaca_summary is None or table not in trusted_tables:
        return None
    manifest_dataset = _ALPACA_SIP_TABLE_MANIFEST_DATASETS.get(table, table)
    return next(
        (
            candidate
            for candidate in alpaca_summary.manifests
            if candidate.dataset == manifest_dataset
            and candidate.validation_status.lower() == "passed"
        ),
        None,
    )


def _manifest_candidates_for_table(
    alpaca_summary: AlpacaSipManifestSummaryDTO | None,
    table: str,
) -> list[ManifestSummaryDTO]:
    if alpaca_summary is None:
        return []
    manifest_dataset = _ALPACA_SIP_TABLE_MANIFEST_DATASETS.get(table, table)
    return [
        candidate for candidate in alpaca_summary.manifests if candidate.dataset == manifest_dataset
    ]


def _preview_manifest_warning_code(
    alpaca_summary: AlpacaSipManifestSummaryDTO | None,
    table: str,
    trusted_tables: list[str],
) -> str:
    manifest_dataset = _ALPACA_SIP_TABLE_MANIFEST_DATASETS.get(table, table)
    candidates = _manifest_candidates_for_table(alpaca_summary, table)
    if any(candidate.validation_status.lower() != "passed" for candidate in candidates):
        return _ALPACA_SIP_MANIFEST_VALIDATION_FAILED_REASON
    if table not in trusted_tables:
        return _ALPACA_SIP_UNTRUSTED_REASON
    return f"alpaca_sip_missing_manifest:{manifest_dataset}"


def _backtest_manifest_unavailable_reason(
    alpaca_summary: AlpacaSipManifestSummaryDTO | None,
    table: str,
    trusted_tables: list[str],
    *,
    alpaca_summary_unavailable: bool,
) -> str:
    if alpaca_summary_unavailable:
        return "alpaca_sip_manifest_summary_unavailable"
    return _preview_manifest_warning_code(alpaca_summary, table, trusted_tables)


def _role_provenance_from_manifest(
    role: str,
    table: str,
    manifest: ManifestSummaryDTO,
    *,
    read_time_adjustment_mode_override: str | None = None,
) -> BacktestRoleProvenanceDTO:
    return BacktestRoleProvenanceDTO(
        role=role,
        dataset=_optional_text(manifest.dataset),
        table=table,
        available=True,
        manifest_ids=_optional_text_list(manifest.manifest_id),
        manifest_references=_optional_text_list(manifest.manifest_reference),
        manifest_checksums=_optional_text_list(manifest.manifest_checksum),
        provider_id=_optional_text(manifest.provider_id),
        provider_version=_optional_text(manifest.provider_version),
        # Manifest summaries sanitize provider signatures before this handoff layer.
        provider_signature=manifest.provider_signature,
        source_feed=_optional_text(manifest.source_feed),
        adjustment_mode=_optional_text(manifest.adjustment_mode),
        canonical_storage_mode=_role_canonical_storage_mode(table, manifest),
        read_time_adjustment_mode=(
            read_time_adjustment_mode_override or _role_read_time_adjustment_mode(table, manifest)
        ),
    )


def _missing_role_provenance(
    role: str,
    table: str,
    reason: str,
) -> BacktestRoleProvenanceDTO:
    return BacktestRoleProvenanceDTO(
        role=role,
        dataset=_ALPACA_SIP_TABLE_MANIFEST_DATASETS.get(table, table),
        table=table,
        available=False,
        unavailable_reason=reason,
        canonical_storage_mode=_role_canonical_storage_mode(table, None),
        read_time_adjustment_mode=_role_read_time_adjustment_mode(table, None),
    )


def _role_canonical_storage_mode(table: str, manifest: ManifestSummaryDTO | None) -> str:
    storage_mode = manifest.canonical_storage_mode if manifest is not None else None
    if table == "alpaca_sip_daily":
        return _manifest_text(
            storage_mode,
            _ALPACA_SIP_DAILY_CANONICAL_STORAGE_MODE,
        )
    if table == "alpaca_sip_corp_actions":
        return _manifest_text(
            storage_mode,
            _ALPACA_SIP_CORP_ACTIONS_STORAGE_MODE,
        )
    return _manifest_text(storage_mode, "unknown")


def _role_read_time_adjustment_mode(table: str, manifest: ManifestSummaryDTO | None) -> str:
    adjustment_mode = manifest.read_time_adjustment_mode if manifest is not None else None
    if table == "alpaca_sip_daily":
        return _manifest_text(
            adjustment_mode,
            _ALPACA_SIP_DAILY_READ_TIME_ADJUSTMENT_MODE,
        )
    if table == "alpaca_sip_corp_actions":
        return _manifest_text(adjustment_mode, "not_applicable")
    return _manifest_text(adjustment_mode, "unknown")


def _manifest_text(value: object | None, default: str) -> str:
    return str(value) if value is not None and str(value) else default


def _optional_text(value: Any) -> str | None:
    return str(value) if value is not None and str(value) else None


def _optional_text_list(value: Any) -> list[str]:
    if isinstance(value, list | tuple):
        return [text for item in value if (text := _optional_text(item)) is not None]
    text = _optional_text(value)
    return [text] if text is not None else []


def _dataset_description(dataset: str, queryable_state: str) -> str:
    base = _DATASET_DESCRIPTIONS.get(dataset, dataset)
    return f"{base}; state={queryable_state}"


def _availability_reason(
    *,
    queryable_state: str,
    fallback_tables: list[str],
    invalid_manifest_tables: list[str],
    alpaca_summary_unavailable: bool = False,
) -> str | None:
    if invalid_manifest_tables:
        return (
            "Trusted manifest is invalid or has no valid parquet files "
            f"({', '.join(invalid_manifest_tables)})"
        )
    if queryable_state == "queryable_fallback_only":
        return (
            "Fallback parquet exists without a trusted manifest; use standalone SQL Explorer "
            f"for local inspection only ({', '.join(fallback_tables)})"
        )
    if alpaca_summary_unavailable:
        return (
            "Manifest summary temporarily unavailable; row count and date range may be incomplete"
        )
    if queryable_state == "missing":
        return "No local parquet files discovered"
    return None


def _sql_handoff_url(dataset: str, tables: list[str]) -> str | None:
    if not tables:
        return None
    query = _handoff_query_for_dataset(dataset, tables)
    return "/data/sql-explorer?" + urlencode({"dataset": dataset, "query": query})


def _handoff_query_for_dataset(dataset: str, tables: list[str]) -> str:
    templates = _query_templates_for_dataset(dataset, tables)
    preferred_labels = _PREFERRED_HANDOFF_QUERY_LABELS.get(dataset, ())
    for label in preferred_labels:
        for template in templates:
            if template.label == label:
                return template.sql
    for table in tables:
        for template in templates:
            if template.table == table:
                return template.sql
    return templates[0].sql


def _query_templates_for_dataset(dataset: str, tables: list[str]) -> list[QueryTemplateDTO]:
    if not tables:
        return []
    templates: list[QueryTemplateDTO] = []
    table_set = set(tables)
    if "alpaca_sip_daily" in table_set:
        templates.extend(
            [
                QueryTemplateDTO(
                    label="Latest daily bars",
                    table="alpaca_sip_daily",
                    sql="SELECT * FROM alpaca_sip_daily ORDER BY date DESC, symbol LIMIT 100",
                ),
                QueryTemplateDTO(
                    label="Date coverage by symbol",
                    table="alpaca_sip_daily",
                    sql=(
                        "SELECT symbol, min(date) AS start_date, max(date) AS end_date, "
                        "count(*) AS rows FROM alpaca_sip_daily "
                        "GROUP BY symbol ORDER BY symbol LIMIT 100"
                    ),
                ),
                QueryTemplateDTO(
                    label="Rows by partition year",
                    table="alpaca_sip_daily",
                    sql=(
                        "SELECT year(date) AS year, count(*) AS rows "
                        "FROM alpaca_sip_daily GROUP BY year ORDER BY year"
                    ),
                ),
                QueryTemplateDTO(
                    label="Null adjusted-return columns",
                    table="alpaca_sip_daily",
                    sql=(
                        "SELECT date, symbol, adj_close, ret FROM alpaca_sip_daily "
                        "WHERE adj_close IS NULL OR ret IS NULL LIMIT 100"
                    ),
                ),
            ]
        )
    if "alpaca_sip_corp_actions" in table_set:
        templates.append(
            QueryTemplateDTO(
                label="Recent corporate actions",
                table="alpaca_sip_corp_actions",
                sql=(
                    "SELECT * FROM alpaca_sip_corp_actions "
                    "ORDER BY ex_date DESC, symbol LIMIT 100"
                ),
            )
        )
    for table in tables:
        if table in {"alpaca_sip_daily", "alpaca_sip_corp_actions"}:
            continue
        templates.append(
            QueryTemplateDTO(
                label=f"Preview {table}",
                table=table,
                sql=f"SELECT * FROM {table} LIMIT 100",
            )
        )
    return templates


__all__ = ["DATA_EXPORT_RATE_LIMIT", "DataExplorerService", "RateLimitExceeded"]
