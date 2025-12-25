"""Service layer for dataset exploration.

Enforces query validation, RBAC, and rate limiting.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from apps.web_console.auth.rate_limiter import RateLimiter, get_rate_limiter
from apps.web_console.schemas.data_management import (
    DataPreviewDTO,
    DatasetInfoDTO,
    ExportJobDTO,
    QueryResultDTO,
)
from apps.web_console.services.sql_validator import SQLValidator
from apps.web_console.utils.auth_helpers import get_user_id
from libs.web_console_auth.permissions import Permission, has_dataset_permission, has_permission

_SUPPORTED_DATASETS = ("crsp", "compustat", "taq", "fama_french")


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
    ) -> None:
        self._rate_limiter = rate_limiter or get_rate_limiter()
        self._sql_validator = sql_validator or SQLValidator()

    async def list_datasets(self, user: Any) -> list[DatasetInfoDTO]:
        """List available datasets with metadata.

        Permission: VIEW_DATA_SYNC (basic access)
        Dataset-level access: Filtered by user's dataset permissions
        """
        self._require_permission(user, Permission.VIEW_DATA_SYNC)

        now = datetime.now(UTC)
        mock = [
            DatasetInfoDTO(
                name=dataset,
                description="Placeholder dataset",
                row_count=1000,
                date_range={"start": "2000-01-01", "end": "2024-12-31"},
                symbol_count=500,
                last_sync=now,
            )
            for dataset in _SUPPORTED_DATASETS
        ]
        return [item for item in mock if has_dataset_permission(user, item.name)]

    async def get_dataset_preview(
        self,
        user: Any,
        dataset: str,
        limit: int = 100,
    ) -> DataPreviewDTO:
        """Get first N rows of dataset.

        Permission: QUERY_DATA + dataset-level access
        Limit: Max 1000 rows
        """
        self._require_permission(user, Permission.QUERY_DATA)
        self._require_dataset_access(user, dataset)
        if limit > 1000:
            raise ValueError("Preview limit cannot exceed 1000 rows")

        return DataPreviewDTO(columns=[], rows=[], total_count=0)

    async def execute_query(
        self,
        user: Any,
        dataset: str,
        query: str,
        timeout_seconds: int = 30,
    ) -> QueryResultDTO:
        # TODO: Pass timeout_seconds to DuckDB client when query execution is implemented
        """Execute read-only SQL query against a SINGLE dataset.

        Permission: QUERY_DATA + dataset-level access for specified dataset
        Rate limit: 10 queries/minute per user (server-side)
        Security: Query validation + table reference validation (see SQL Security section)
        Streaming: Results paginated, max 10,000 rows per page
        Audit: Logged with user, dataset, query_fingerprint, row_count, duration

        CRITICAL: Query is scoped to specified dataset only.
        Cross-dataset queries are rejected at validation time.
        """
        self._require_permission(user, Permission.QUERY_DATA)
        self._require_dataset_access(user, dataset)

        # Validate SQL before rate limiting to fail fast on invalid queries
        valid, error = self._sql_validator.validate(query, dataset)
        if not valid:
            raise ValueError(f"Invalid query: {error}")

        # Enforce row limit for interactive queries (10,000 max)
        query = self._sql_validator.enforce_row_limit(query, max_rows=10000)

        await self._enforce_rate_limit(user, action="data_query", max_requests=10, window=60)

        # TODO: Execute query against DuckDB and return actual results
        return QueryResultDTO(columns=[], rows=[], total_count=0, has_more=False, cursor=None)

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
        self._require_permission(user, Permission.EXPORT_DATA)
        self._require_dataset_access(user, dataset)

        # Validate SQL before rate limiting to fail fast on invalid queries
        valid, error = self._sql_validator.validate(query, dataset)
        if not valid:
            raise ValueError(f"Invalid query: {error}")

        # Enforce row limit for exports (100,000 max)
        query = self._sql_validator.enforce_row_limit(query, max_rows=100000)

        await self._enforce_rate_limit(user, action="data_export", max_requests=5, window=3600)

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


__all__ = ["DataExplorerService", "RateLimitExceeded"]
