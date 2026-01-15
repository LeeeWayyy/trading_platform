"""Service layer for data sync operations.

Enforces RBAC, dataset-level access, and rate limiting at server-side.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from libs.platform.web_console_auth.helpers import get_user_id
from libs.platform.web_console_auth.permissions import (
    Permission,
    has_dataset_permission,
    has_permission,
)
from libs.platform.web_console_auth.rate_limiter import RateLimiter, get_rate_limiter

from .schemas.data_management import (
    SyncJobDTO,
    SyncLogEntry,
    SyncScheduleDTO,
    SyncScheduleUpdateDTO,
    SyncStatusDTO,
)

_SUPPORTED_DATASETS = ("crsp", "compustat", "taq", "fama_french")


class RateLimitExceeded(RuntimeError):
    """Raised when a rate limit is exceeded."""


class DataSyncService:
    """Service layer for data sync operations.

    Enforces RBAC, dataset-level access, and rate limiting at server-side.

    IMPORTANT: ALL read paths filter by user's dataset permissions.
    Users only see sync status/logs/schedules for datasets they have access to.

    NOTE: Current implementation uses mock data for interface validation.
    Production implementation requires:
    - DB queries against data_sync_logs table (migration 0012)
    - DB queries against data_sync_schedules table (migration 0013)
    - Integration with actual data pipeline sync infrastructure
    """

    def __init__(self, rate_limiter: RateLimiter | None = None) -> None:
        self._rate_limiter = rate_limiter or get_rate_limiter()

    async def get_sync_status(self, user: Any) -> list[SyncStatusDTO]:
        """Get sync status for datasets user has access to.

        Permission: VIEW_DATA_SYNC + dataset-level access (filtered)
        Returns: List of SyncStatusDTO with dataset, last_sync, row_count, status
        Filtering: Only datasets matching user's DatasetPermission set
        """
        self._require_permission(user, Permission.VIEW_DATA_SYNC)

        now = datetime.now(UTC)
        mock = [
            SyncStatusDTO(
                dataset=name,
                last_sync=now,
                row_count=1000,
                validation_status="ok",
                schema_version="v1",
            )
            for name in _SUPPORTED_DATASETS
        ]
        return [item for item in mock if has_dataset_permission(user, item.dataset)]

    async def get_sync_logs(
        self,
        user: Any,
        dataset: str | None,
        level: str | None,
        limit: int = 100,
    ) -> list[SyncLogEntry]:
        """Get recent sync log entries with optional filters.

        Permission: VIEW_DATA_SYNC + dataset-level access (filtered)
        Rate limit: N/A (read-only)
        Filtering: If dataset specified, validate access; otherwise filter to accessible datasets
        """
        self._require_permission(user, Permission.VIEW_DATA_SYNC)
        if dataset:
            self._require_dataset_access(user, dataset)

        now = datetime.now(UTC)
        mock = [
            SyncLogEntry(
                id=f"log-{idx}",
                dataset=name,
                level=level or "info",
                message="Sync completed (placeholder)",
                extra={"placeholder": True},
                sync_run_id=f"run-{idx}",
                created_at=now,
            )
            for idx, name in enumerate(_SUPPORTED_DATASETS, start=1)
        ]
        filtered = (
            [item for item in mock if item.dataset == dataset]
            if dataset
            else [item for item in mock if has_dataset_permission(user, item.dataset)]
        )
        return filtered[:limit]

    async def get_sync_schedule(self, user: Any) -> list[SyncScheduleDTO]:
        """Get sync schedule configuration for accessible datasets.

        Permission: VIEW_DATA_SYNC + dataset-level access (filtered)
        Filtering: Only schedules for datasets user has access to
        """
        self._require_permission(user, Permission.VIEW_DATA_SYNC)

        now = datetime.now(UTC)
        mock = [
            SyncScheduleDTO(
                id=f"schedule-{idx}",
                dataset=name,
                enabled=True,
                cron_expression="0 2 * * *",
                last_scheduled_run=now,
                next_scheduled_run=now,
                version=1,
            )
            for idx, name in enumerate(_SUPPORTED_DATASETS, start=1)
        ]
        return [item for item in mock if has_dataset_permission(user, item.dataset)]

    async def update_sync_schedule(
        self,
        user: Any,
        dataset: str,
        schedule: SyncScheduleUpdateDTO,
    ) -> SyncScheduleDTO:
        """Update sync schedule (cron expression, enabled) for a specific dataset.

        Permission: MANAGE_SYNC_SCHEDULE + dataset-level access for specified dataset
        Security: Validate user has access to the dataset being updated (licensing compliance)
        Audit: Logged with user, dataset, old/new values
        """
        self._require_permission(user, Permission.MANAGE_SYNC_SCHEDULE)
        self._require_dataset_access(user, dataset)

        now = datetime.now(UTC)
        return SyncScheduleDTO(
            id=f"schedule-{dataset}",
            dataset=dataset,
            enabled=bool(schedule.enabled) if schedule.enabled is not None else True,
            cron_expression=schedule.cron_expression or "0 2 * * *",
            last_scheduled_run=now,
            next_scheduled_run=now,
            version=1,
        )

    async def trigger_sync(self, user: Any, dataset: str, reason: str) -> SyncJobDTO:
        """Trigger manual incremental sync.

        Permission: TRIGGER_DATA_SYNC + dataset-level access for specified dataset
        Security: Validate user has access to the dataset being synced (licensing compliance)
        Rate limit: 1/minute global (server-side enforced)
        Audit: Logged with user, dataset, reason
        """
        self._require_permission(user, Permission.TRIGGER_DATA_SYNC)
        self._require_dataset_access(user, dataset)
        await self._enforce_rate_limit(user, action="trigger_data_sync", max_requests=1, window=60)

        now = datetime.now(UTC)
        return SyncJobDTO(
            id=str(uuid4()),
            dataset=dataset,
            status="queued",
            started_at=now,
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


__all__ = ["DataSyncService", "RateLimitExceeded"]
