"""
Unit tests for libs.web_console_services.data_sync_service.

Coverage focus:
- Permission checks and dataset-level access filtering
- Rate limit enforcement for manual sync triggers
- Schedule update validation
- Delegation to rate limiter and helper utilities
"""

from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from pydantic import ValidationError

from libs.data.data_quality.manifest import SyncManifest
from libs.platform.web_console_auth.permissions import Role
from libs.web_console_services import data_sync_service as data_sync_module
from libs.web_console_services.data_manifest_service import (
    ALPACA_SIP_CORP_ACTIONS_DATASET,
    ALPACA_SIP_DAILY_DATASET,
    DataManifestService,
)
from libs.web_console_services.data_sync_service import (
    DataSyncService,
    PreflightRequired,
    RateLimitExceeded,
)
from libs.web_console_services.schemas.data_management import (
    DataAcquisitionJobDTO,
    DataAcquisitionPreflightDTO,
    DataAcquisitionRequestDTO,
    DataAcquisitionSubmitDTO,
    SyncScheduleUpdateDTO,
)


@dataclass(frozen=True)
class DummyUser:
    """Simple user stub for permission checks."""

    user_id: str
    role: Role


class _RecordingAcquisitionExecutor:
    def __init__(self) -> None:
        self.preflights: list[Any] = []

    async def run(self, preflight: Any) -> tuple[list[str], list[str], list[str]]:
        self.preflights.append(preflight)
        return (
            ["alpaca_sip_daily@v2:checksum"],
            ["preflight_passed", "adapter_completed"],
            ["executor_completed"],
        )


class _FailingAcquisitionExecutor:
    async def run(self, _preflight: Any) -> tuple[list[str], list[str], list[str]]:
        raise RuntimeError("transient provider failure")


class _SensitiveFailingAcquisitionExecutor:
    async def run(self, _preflight: Any) -> tuple[list[str], list[str], list[str]]:
        raise data_sync_module._AcquisitionExecutionError(  # noqa: SLF001
            "provider failed token=secret-token Authorization: Bearer abc.def",
            [
                "stdout:api_key=secret-key",
                "stderr:APCA-API-SECRET-KEY: broker-secret",
            ],
        )


class _BlockingAcquisitionExecutor:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def run(self, _preflight: Any) -> tuple[list[str], list[str], list[str]]:
        self.started.set()
        await self.release.wait()
        return (
            ["alpaca_sip_daily@v2:checksum"],
            ["preflight_passed", "adapter_completed"],
            ["executor_completed"],
        )


class _FailingCompletionStore(data_sync_module._InMemoryAcquisitionStore):  # noqa: SLF001
    def __init__(self, state: data_sync_module._AcquisitionState) -> None:  # noqa: SLF001
        super().__init__(state)
        self.update_calls = 0

    async def update_job(self, job: DataAcquisitionJobDTO, now: datetime) -> None:
        self.update_calls += 1
        if self.update_calls > 1:
            raise RuntimeError("store unavailable")
        await super().update_job(job, now)


class _FakeRedisPipeline:
    def __init__(self, redis_client: _FakeRedis) -> None:
        self._redis = redis_client
        self._ops: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def hset(self, *args: Any, **kwargs: Any) -> _FakeRedisPipeline:
        self._ops.append(("hset", args, kwargs))
        return self

    def expire(self, *args: Any, **kwargs: Any) -> _FakeRedisPipeline:
        self._ops.append(("expire", args, kwargs))
        return self

    def execute(self) -> list[bool]:
        results: list[bool] = []
        for name, args, kwargs in self._ops:
            if name == "hset":
                key = str(args[0])
                mapping = dict(kwargs["mapping"])
                self._redis.hashes[key] = {str(k): str(v) for k, v in mapping.items()}
            elif name == "expire":
                self._redis.expirations[str(args[0])] = int(args[1])
            results.append(True)
        return results


class _FakeRedis:
    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}
        self.values: dict[str, str] = {}
        self.expirations: dict[str, int] = {}

    def pipeline(self) -> _FakeRedisPipeline:
        return _FakeRedisPipeline(self)

    def eval(self, script: str, _key_count: int, key: str, *args: str) -> str | None:
        if "update-if-current-job-id" in script:
            new_payload = args[0]
            job_id = args[1]
            existing = self.values.get(key)
            if existing is not None:
                existing_job = DataAcquisitionJobDTO.model_validate_json(existing)
                if existing_job.id != job_id:
                    return "0"
                replacement_job = DataAcquisitionJobDTO.model_validate_json(new_payload)
                if (
                    existing_job.status in data_sync_module._ACQUISITION_TERMINAL_JOB_STATUSES  # noqa: SLF001
                    and replacement_job.status
                    not in data_sync_module._ACQUISITION_TERMINAL_JOB_STATUSES  # noqa: SLF001
                ):
                    return "0"
            self.values[key] = new_payload
            self.expirations[key] = int(args[2])
            return "1"

        if "cjson.decode" in script:
            new_payload = args[0]
            existing = self.values.get(key)
            if existing is None:
                self.values[key] = new_payload
                self.expirations[key] = int(args[1])
                return None
            existing_job = DataAcquisitionJobDTO.model_validate_json(existing)
            if existing_job.status == "completed":
                return existing
            if existing_job.status in {"queued", "running"}:
                active_epoch_us = max(
                    int(data_sync_module._datetime_epoch_us(existing_job.started_at))  # noqa: SLF001
                    if existing_job.started_at is not None
                    else 0,
                    int(data_sync_module._datetime_epoch_us(existing_job.heartbeat_at))  # noqa: SLF001
                    if existing_job.heartbeat_at is not None
                    else 0,
                )
                if active_epoch_us >= int(args[2]):
                    return existing
            self.values[key] = new_payload
            self.expirations[key] = int(args[1])
            return None

        token_hash = args[0]
        now_epoch = int(args[1])
        payload = self.hashes.get(key)
        if payload is None or payload.get("token_hash") != token_hash:
            return None
        expires_at_epoch = int(payload.get("expires_at_epoch", "0"))
        if expires_at_epoch <= now_epoch:
            self.hashes.pop(key, None)
            return None
        self.hashes.pop(key, None)
        return payload.get("payload")

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def hget(self, key: str, field: str) -> str | None:
        payload = self.hashes.get(key)
        return None if payload is None else payload.get(field)

    def delete(self, key: str) -> int:
        removed = int(key in self.hashes) + int(key in self.values)
        self.hashes.pop(key, None)
        self.values.pop(key, None)
        return removed

    def set(
        self,
        key: str,
        value: str,
        *,
        ex: int | None = None,
        nx: bool = False,
    ) -> bool:
        if nx and key in self.values:
            return False
        self.values[key] = value
        if ex is not None:
            self.expirations[key] = ex
        return True


class _AsyncFakeRedisPipeline:
    def __init__(self, redis_client: _FakeRedis) -> None:
        self._pipeline = _FakeRedisPipeline(redis_client)

    def hset(self, *args: Any, **kwargs: Any) -> _AsyncFakeRedisPipeline:
        self._pipeline.hset(*args, **kwargs)
        return self

    def expire(self, *args: Any, **kwargs: Any) -> _AsyncFakeRedisPipeline:
        self._pipeline.expire(*args, **kwargs)
        return self

    async def execute(self) -> list[bool]:
        return self._pipeline.execute()


class _AsyncFakeRedis:
    def __init__(self) -> None:
        self._sync = _FakeRedis()

    def pipeline(self) -> _AsyncFakeRedisPipeline:
        return _AsyncFakeRedisPipeline(self._sync)

    async def eval(self, script: str, _key_count: int, key: str, *args: str) -> str | None:
        return self._sync.eval(script, _key_count, key, *args)

    async def get(self, key: str) -> str | None:
        return self._sync.get(key)

    async def hget(self, key: str, field: str) -> str | None:
        return self._sync.hget(key, field)

    async def delete(self, key: str) -> int:
        return self._sync.delete(key)


class _CancellableProcess:
    def __init__(self) -> None:
        self.returncode = 0
        self.terminated = False
        self.killed = False
        self.wait_calls = 0
        self._release = asyncio.Event()

    async def communicate(self) -> tuple[bytes, bytes]:
        await self._release.wait()
        return b"", b""

    def terminate(self) -> None:
        self.terminated = True
        self._release.set()

    def kill(self) -> None:
        self.killed = True
        self._release.set()

    async def wait(self) -> int:
        self.wait_calls += 1
        return self.returncode


class _CompletedProcess:
    returncode = 0

    async def communicate(self) -> tuple[bytes, bytes]:
        return (
            b'MANIFEST_JSON:{"dataset": "alpaca_sip_daily", '
            b'"manifest_id": "alpaca_sip_daily@v2:test"}\n',
            b"",
        )


@pytest.fixture()
def operator_user() -> DummyUser:
    return DummyUser(user_id="user-operator", role=Role.OPERATOR)


@pytest.fixture()
def viewer_user() -> DummyUser:
    return DummyUser(user_id="user-viewer", role=Role.VIEWER)


@pytest.fixture()
def admin_user() -> DummyUser:
    return DummyUser(user_id="user-admin", role=Role.ADMIN)


@pytest.fixture()
def rate_limiter() -> AsyncMock:
    limiter = AsyncMock()
    limiter.check_rate_limit = AsyncMock(return_value=(True, 0))
    return limiter


def _fresh_acquisition_state() -> data_sync_module._AcquisitionState:  # noqa: SLF001
    return data_sync_module._AcquisitionState(  # noqa: SLF001
        lock=asyncio.Lock(),
        preflight_tokens={},
        acquisition_jobs={},
    )


@pytest.fixture()
def service(rate_limiter: AsyncMock) -> DataSyncService:
    return DataSyncService(
        rate_limiter=rate_limiter,
        acquisition_state=_fresh_acquisition_state(),
    )


def test_data_sync_service_logs_acquisition_backend(
    rate_limiter: AsyncMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=data_sync_module.__name__)

    DataSyncService(
        rate_limiter=rate_limiter,
        acquisition_state=_fresh_acquisition_state(),
    )

    backend_records = [
        record for record in caplog.records if record.message == "data_acquisition_store_selected"
    ]
    assert backend_records
    assert backend_records[-1].__dict__["backend"] == "in_memory"


@pytest.mark.asyncio()
async def test_redis_acquisition_store_supports_async_redis_clients(
    rate_limiter: AsyncMock,
    operator_user: DummyUser,
) -> None:
    store = data_sync_module._RedisAcquisitionStore(_AsyncFakeRedis())  # noqa: SLF001
    service = DataSyncService(rate_limiter=rate_limiter, acquisition_store=store)
    preflight = await service.preflight_acquisition(
        operator_user,
        _daily_acquisition_request(),
    )
    job = await service.submit_acquisition(
        operator_user,
        DataAcquisitionSubmitDTO(
            idempotency_key=preflight.idempotency_key,
            submit_token=preflight.submit_token,
        ),
    )
    assert job.status == "completed"


@pytest.mark.asyncio()
async def test_get_sync_status_filters_by_dataset_permission(
    service: DataSyncService, operator_user: DummyUser
) -> None:
    results = await service.get_sync_status(operator_user)

    datasets = {item.dataset for item in results}
    assert datasets == {"crsp", "compustat", "fama_french", "taq", "alpaca_sip"}


@pytest.mark.asyncio()
async def test_get_sync_status_offloads_manifest_reads(
    service: DataSyncService,
    operator_user: DummyUser,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    async def fake_to_thread(func: Any, /, *args: Any, **kwargs: Any) -> Any:
        nonlocal called
        called = True
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)

    results = await service.get_sync_status(operator_user)

    assert called is True
    assert results


@pytest.mark.asyncio()
async def test_get_sync_status_uses_alpaca_sip_manifests(
    operator_user: DummyUser,
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    manifest_dir = data_root / "manifests"
    manifest_dir.mkdir(parents=True)
    sync_timestamp = datetime(2026, 4, 30, 12, tzinfo=UTC)
    for dataset, rows in (("alpaca_sip_daily", 10), ("alpaca_sip_corp_actions", 3)):
        manifest = SyncManifest(
            dataset=dataset,
            sync_timestamp=sync_timestamp,
            start_date=date(2026, 4, 1),
            end_date=date(2026, 4, 30),
            row_count=rows,
            checksum=f"{dataset}-checksum",
            schema_version="v1.0.0",
            wrds_query_hash=f"{dataset}-query",
            file_paths=[f"{dataset}.parquet"],
            validation_status="passed",
        )
        (manifest_dir / f"{dataset}.json").write_text(manifest.model_dump_json())
    service = DataSyncService(
        data_manifest_service=DataManifestService(data_root=data_root),
        acquisition_state=_fresh_acquisition_state(),
    )

    results = await service.get_sync_status(operator_user)

    sip = next(item for item in results if item.dataset == "alpaca_sip")
    assert sip.last_sync == sync_timestamp
    assert sip.row_count == 13
    assert sip.validation_status == "ok"


@pytest.mark.asyncio()
async def test_get_sync_status_marks_partial_alpaca_sip_manifests_missing(
    operator_user: DummyUser,
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    manifest_dir = data_root / "manifests"
    manifest_dir.mkdir(parents=True)
    sync_timestamp = datetime(2026, 4, 30, 12, tzinfo=UTC)
    manifest = SyncManifest(
        dataset="alpaca_sip_daily",
        sync_timestamp=sync_timestamp,
        start_date=date(2026, 4, 1),
        end_date=date(2026, 4, 30),
        row_count=10,
        checksum="alpaca_sip_daily-checksum",
        schema_version="v1.0.0",
        wrds_query_hash="alpaca_sip_daily-query",
        file_paths=["alpaca_sip_daily.parquet"],
        validation_status="passed",
    )
    (manifest_dir / "alpaca_sip_daily.json").write_text(manifest.model_dump_json())
    service = DataSyncService(
        data_manifest_service=DataManifestService(data_root=data_root),
        acquisition_state=_fresh_acquisition_state(),
    )

    results = await service.get_sync_status(operator_user)

    sip = next(item for item in results if item.dataset == "alpaca_sip")
    assert sip.last_sync == sync_timestamp
    assert sip.row_count == 10
    assert sip.validation_status == "missing: alpaca_sip_corp_actions"


def test_non_alpaca_placeholder_status_does_not_call_manifest_service(
    rate_limiter: AsyncMock,
) -> None:
    manifest_service = MagicMock()
    service = DataSyncService(
        rate_limiter=rate_limiter,
        data_manifest_service=manifest_service,
        acquisition_state=_fresh_acquisition_state(),
    )
    now = datetime(2026, 4, 30, 12, tzinfo=UTC)

    status = service._mock_or_manifest_status("crsp", now)  # noqa: SLF001

    assert status.dataset == "crsp"
    assert status.last_sync == now
    manifest_service.get_alpaca_sip_summary.assert_not_called()


@pytest.mark.asyncio()
async def test_get_sync_status_researcher_allowed_single_admin(service: DataSyncService) -> None:
    """P6T19: Researcher can view sync status — single-admin model."""
    user = DummyUser(user_id="researcher-1", role=Role.RESEARCHER)

    result = await service.get_sync_status(user)
    assert result is not None


@pytest.mark.asyncio()
async def test_get_sync_logs_viewer_sees_all_datasets_single_admin(
    service: DataSyncService, viewer_user: DummyUser
) -> None:
    """P6T19: Viewer sees all dataset logs — single-admin model."""
    results = await service.get_sync_logs(viewer_user, dataset=None, level=None, limit=100)

    datasets = {item.dataset for item in results}
    # Single-admin: has_dataset_permission always True, all datasets visible
    assert len(datasets) >= 1


@pytest.mark.asyncio()
async def test_get_sync_logs_viewer_can_access_any_dataset_single_admin(
    service: DataSyncService, viewer_user: DummyUser
) -> None:
    """P6T19: Viewer can access any dataset logs — single-admin model."""
    results = await service.get_sync_logs(viewer_user, dataset="crsp", level=None, limit=100)
    assert isinstance(results, list)


@pytest.mark.asyncio()
async def test_get_sync_logs_limit_applied(
    service: DataSyncService, operator_user: DummyUser
) -> None:
    results = await service.get_sync_logs(operator_user, dataset=None, level=None, limit=1)

    assert len(results) == 1


@pytest.mark.asyncio()
async def test_get_sync_schedule_viewer_sees_all_datasets_single_admin(
    service: DataSyncService, viewer_user: DummyUser
) -> None:
    """P6T19: Viewer sees all dataset schedules — single-admin model."""
    results = await service.get_sync_schedule(viewer_user)

    datasets = {item.dataset for item in results}
    # Single-admin: all datasets visible
    assert len(datasets) >= 1


@pytest.mark.asyncio()
async def test_update_sync_schedule_operator_can_update_any_single_admin(
    service: DataSyncService, operator_user: DummyUser
) -> None:
    """P6T19: Operator can update any dataset schedule — single-admin model."""
    schedule = SyncScheduleUpdateDTO(enabled=False, cron_expression="0 3 * * *")

    result = await service.update_sync_schedule(operator_user, dataset="crsp", schedule=schedule)
    assert result is not None


@pytest.mark.asyncio()
async def test_update_sync_schedule_success(
    service: DataSyncService, admin_user: DummyUser
) -> None:
    schedule = SyncScheduleUpdateDTO(enabled=False, cron_expression="15 5 * * *")

    result = await service.update_sync_schedule(admin_user, dataset="taq", schedule=schedule)

    assert result.dataset == "taq"
    assert result.enabled is False
    assert result.cron_expression == "15 5 * * *"


@pytest.mark.asyncio()
async def test_trigger_sync_success_calls_rate_limiter(
    rate_limiter: AsyncMock, operator_user: DummyUser
) -> None:
    service = DataSyncService(
        rate_limiter=rate_limiter,
        acquisition_state=_fresh_acquisition_state(),
    )

    job = await service.trigger_sync(operator_user, dataset="crsp", reason="manual")

    assert job.dataset == "crsp"
    rate_limiter.check_rate_limit.assert_awaited_once()


def _daily_acquisition_request(**overrides: Any) -> DataAcquisitionRequestDTO:
    data: dict[str, Any] = {
        "dataset": ALPACA_SIP_DAILY_DATASET,
        "start_date": date(2026, 1, 3),
        "end_date": date(2026, 4, 30),
        "symbol_source": "AAPL,MSFT",
        "mode": "backfill",
        "adjustment_mode": "raw",
        "reason": "fill missing SIP daily bars",
        "dry_run": True,
    }
    data.update(overrides)
    return DataAcquisitionRequestDTO(**data)


def _daily_acquisition_job(
    *,
    job_id: str,
    idempotency_key: str,
    status: Literal["queued", "running", "completed", "failed"],
) -> DataAcquisitionJobDTO:
    started_at = datetime(2026, 5, 1, 12, tzinfo=UTC)
    return DataAcquisitionJobDTO(
        id=job_id,
        dataset=ALPACA_SIP_DAILY_DATASET,
        status=status,
        idempotency_key=idempotency_key,
        mode="backfill",
        dry_run=False,
        provider_id="alpaca_sip",
        source_feed="sip",
        canonical_storage_mode="raw",
        read_time_adjustment_mode="unavailable",
        adjustment_mode="raw",
        started_at=started_at,
        heartbeat_at=started_at if status in {"queued", "running"} else None,
        completed_at=(
            datetime(2026, 5, 1, 12, 5, tzinfo=UTC) if status in {"completed", "failed"} else None
        ),
        submit_token_status="consumed",
        adapter="script:scripts/data/alpaca_sip_sync.py",
        produced_manifest_ids=[],
        validation_output=["preflight_passed"],
        logs=["job_queued"],
    )


@pytest.mark.asyncio()
async def test_preflight_acquisition_builds_daily_policy_and_token(
    service: DataSyncService,
    operator_user: DummyUser,
) -> None:
    preflight = await service.preflight_acquisition(
        operator_user,
        _daily_acquisition_request(),
    )

    assert preflight.dataset == ALPACA_SIP_DAILY_DATASET
    assert preflight.provider_id == "alpaca_sip"
    assert preflight.source_feed == "sip"
    assert preflight.start_date == date(2026, 1, 1)
    assert preflight.end_date == date(2026, 12, 31)
    assert preflight.adjustment_mode == "raw"
    assert preflight.canonical_storage_mode == "raw"
    assert preflight.read_time_adjustment_mode == "unavailable"
    assert preflight.idempotency_key.startswith("acq_")
    assert preflight.submit_token
    assert "alpaca_sip_daily_sync_uses_calendar_year_partitions" in (preflight.supported_semantics)
    assert "raw_sip_returns_unavailable" in preflight.warnings


@pytest.mark.asyncio()
async def test_preflight_acquisition_builds_corp_actions_policy(
    service: DataSyncService,
    operator_user: DummyUser,
) -> None:
    preflight = await service.preflight_acquisition(
        operator_user,
        _daily_acquisition_request(
            dataset=ALPACA_SIP_CORP_ACTIONS_DATASET,
            symbol_source="AAPL,MSFT",
            adjustment_mode=None,
            reason="fill corporate actions",
        ),
    )

    assert preflight.dataset == ALPACA_SIP_CORP_ACTIONS_DATASET
    assert preflight.provider_id == "alpaca_sip"
    assert preflight.source_feed == "sip"
    assert preflight.adjustment_mode is None
    assert preflight.canonical_storage_mode == "not_price_bars"
    assert preflight.read_time_adjustment_mode is None
    assert "corporate_actions_feed_has_no_price_adjustment_mode" in (preflight.supported_semantics)


@pytest.mark.asyncio()
async def test_submit_acquisition_consumes_token_and_hides_token_from_job(
    service: DataSyncService,
    operator_user: DummyUser,
) -> None:
    preflight = await service.preflight_acquisition(
        operator_user,
        _daily_acquisition_request(),
    )
    token_record = service._preflight_tokens[
        (  # noqa: SLF001
            operator_user.user_id,
            preflight.idempotency_key,
        )
    ]
    assert token_record.preflight.submit_token == ""
    assert token_record.token_hash != preflight.submit_token

    job = await service.submit_acquisition(
        operator_user,
        DataAcquisitionSubmitDTO(
            idempotency_key=preflight.idempotency_key,
            submit_token=preflight.submit_token,
        ),
    )

    serialized_job = job.model_dump_json()
    assert job.status == "completed"
    assert job.submit_token_status == "consumed"
    assert job.dry_run is True
    assert job.idempotency_key == preflight.idempotency_key
    assert job.adapter == "script:scripts/data/alpaca_sip_sync.py"
    assert "preflight_passed" in job.validation_output
    assert "dry_run_completed_without_external_fetch" in job.validation_output
    assert preflight.submit_token not in serialized_job


@pytest.mark.asyncio()
async def test_submit_acquisition_runs_non_dry_job_and_updates_state(
    rate_limiter: AsyncMock,
    operator_user: DummyUser,
) -> None:
    executor = _RecordingAcquisitionExecutor()
    service = DataSyncService(
        rate_limiter=rate_limiter,
        acquisition_state=_fresh_acquisition_state(),
        acquisition_executor=executor,
    )
    preflight = await service.preflight_acquisition(
        operator_user,
        _daily_acquisition_request(dry_run=False),
    )

    job = await service.submit_acquisition(
        operator_user,
        DataAcquisitionSubmitDTO(
            idempotency_key=preflight.idempotency_key,
            submit_token=preflight.submit_token,
        ),
    )
    if service._background_acquisition_tasks:  # noqa: SLF001
        await asyncio.gather(*service._background_acquisition_tasks)  # noqa: SLF001

    stored = service._acquisition_jobs[preflight.idempotency_key]  # noqa: SLF001
    assert job.status == "running"
    assert stored.status == "completed"
    assert stored.produced_manifest_ids == ["alpaca_sip_daily@v2:checksum"]
    assert "executor_completed" in stored.logs
    assert len(executor.preflights) == 1
    assert executor.preflights[0].idempotency_key == preflight.idempotency_key
    assert executor.preflights[0].submit_token == ""


@pytest.mark.asyncio()
async def test_background_acquisition_observes_store_update_failure(
    rate_limiter: AsyncMock,
    operator_user: DummyUser,
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = _FailingCompletionStore(_fresh_acquisition_state())
    service = DataSyncService(
        rate_limiter=rate_limiter,
        acquisition_store=store,
        acquisition_executor=_RecordingAcquisitionExecutor(),
    )
    caplog.set_level(logging.WARNING, logger=data_sync_module.__name__)
    preflight = await service.preflight_acquisition(
        operator_user,
        _daily_acquisition_request(dry_run=False),
    )

    job = await service.submit_acquisition(
        operator_user,
        DataAcquisitionSubmitDTO(
            idempotency_key=preflight.idempotency_key,
            submit_token=preflight.submit_token,
        ),
    )
    for _ in range(20):
        if not service._background_acquisition_tasks:  # noqa: SLF001
            break
        await asyncio.sleep(0)

    assert job.status == "running"
    assert not service._background_acquisition_tasks  # noqa: SLF001
    failure_records = [
        record
        for record in caplog.records
        if record.message == "background_acquisition_task_failed"
    ]
    assert failure_records
    assert failure_records[-1].__dict__["job_id"] == job.id
    assert any(
        record.message == "background_acquisition_failure_status_update_failed"
        for record in caplog.records
    )


@pytest.mark.asyncio()
async def test_script_executor_builds_command_off_event_loop_thread(
    monkeypatch: pytest.MonkeyPatch,
    rate_limiter: AsyncMock,
    operator_user: DummyUser,
) -> None:
    loop_thread_id = threading.get_ident()
    build_thread_id: int | None = None

    def fake_build_acquisition_command(
        _preflight: DataAcquisitionPreflightDTO,
    ) -> list[str]:
        nonlocal build_thread_id
        build_thread_id = threading.get_ident()
        return ["python", "-c", "pass"]

    async def fake_create_subprocess_exec(*_args: Any, **_kwargs: Any) -> _CompletedProcess:
        return _CompletedProcess()

    monkeypatch.setattr(
        data_sync_module,
        "_build_acquisition_command",
        fake_build_acquisition_command,
    )
    monkeypatch.setattr(
        asyncio,
        "create_subprocess_exec",
        fake_create_subprocess_exec,
    )
    service = DataSyncService(rate_limiter=rate_limiter)
    preflight = await service.preflight_acquisition(
        operator_user,
        _daily_acquisition_request(dry_run=False),
    )
    executor = data_sync_module._ScriptBackedAcquisitionExecutor(DataManifestService())  # noqa: SLF001

    manifest_ids, validation_output, logs = await executor.run(preflight)

    assert build_thread_id is not None
    assert build_thread_id != loop_thread_id
    assert manifest_ids == ["alpaca_sip_daily@v2:test"]
    assert "adapter_completed" in validation_output
    assert "command=python -c pass" in logs


@pytest.mark.asyncio()
async def test_script_executor_terminates_subprocess_on_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proc = _CancellableProcess()
    proc_created = asyncio.Event()

    async def fake_create_subprocess_exec(*_args: Any, **_kwargs: Any) -> _CancellableProcess:
        proc_created.set()
        return proc

    monkeypatch.setattr(
        asyncio,
        "create_subprocess_exec",
        fake_create_subprocess_exec,
    )
    executor = data_sync_module._ScriptBackedAcquisitionExecutor(DataManifestService())  # noqa: SLF001
    preflight = DataAcquisitionPreflightDTO(
        dataset=ALPACA_SIP_DAILY_DATASET,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 12, 31),
        symbol_source="AAPL,MSFT",
        mode="backfill",
        dry_run=False,
        provider_id="alpaca_sip",
        source_feed="sip",
        canonical_storage_mode="raw",
        read_time_adjustment_mode="unavailable",
        adjustment_mode="raw",
        idempotency_key="acq_cancel",
        submit_token="",
        submit_token_expires_at=datetime(2026, 5, 1, 12, tzinfo=UTC),
        supported_semantics=[],
        warnings=[],
        logs=[],
    )

    task = asyncio.create_task(executor.run(preflight))
    await asyncio.wait_for(proc_created.wait(), timeout=1)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert proc.terminated is True
    assert proc.wait_calls == 1


def test_script_executor_extracts_manifest_id_from_stdout() -> None:
    payload = (
        b"Sync complete\n"
        b'MANIFEST_JSON:{"checksum": "abc123", "dataset": "alpaca_sip_daily", '
        b'"manifest_id": "alpaca_sip_daily@v7:abc123", "manifest_version": 7}\n'
    )

    assert data_sync_module._ScriptBackedAcquisitionExecutor._manifest_ids_from_stdout(  # noqa: SLF001
        ALPACA_SIP_DAILY_DATASET,
        payload,
    ) == ["alpaca_sip_daily@v7:abc123"]


def test_script_executor_builds_manifest_id_from_stdout_fields() -> None:
    payload = (
        b"Corporate actions sync complete\n"
        b'MANIFEST_JSON:{"checksum": "def456", "dataset": "alpaca_sip_corp_actions", '
        b'"manifest_id": null, "manifest_version": 3}\n'
    )

    assert data_sync_module._ScriptBackedAcquisitionExecutor._manifest_ids_from_stdout(  # noqa: SLF001
        ALPACA_SIP_CORP_ACTIONS_DATASET,
        payload,
    ) == ["alpaca_sip_corp_actions@v3:def456"]


@pytest.mark.asyncio()
async def test_submit_acquisition_reuses_existing_inflight_job_for_same_scope(
    service: DataSyncService,
    operator_user: DummyUser,
) -> None:
    request = _daily_acquisition_request()
    first_preflight = await service.preflight_acquisition(operator_user, request)
    first_job = await service.submit_acquisition(
        operator_user,
        DataAcquisitionSubmitDTO(
            idempotency_key=first_preflight.idempotency_key,
            submit_token=first_preflight.submit_token,
        ),
    )
    second_preflight = await service.preflight_acquisition(operator_user, request)

    second_job = await service.submit_acquisition(
        operator_user,
        DataAcquisitionSubmitDTO(
            idempotency_key=second_preflight.idempotency_key,
            submit_token=second_preflight.submit_token,
        ),
    )

    assert second_job.id == first_job.id
    assert second_job.submit_token_status == "consumed"
    assert "duplicate_submission_reused_existing_job" in second_job.logs


@pytest.mark.asyncio()
async def test_duplicate_submit_reuses_job_without_charging_trigger_rate_limit(
    operator_user: DummyUser,
) -> None:
    rate_limiter = AsyncMock()
    rate_limiter.check_rate_limit = AsyncMock(
        side_effect=[
            (True, 0),
            (True, 0),
            (True, 0),
            (False, 0),
        ]
    )
    service = DataSyncService(
        rate_limiter=rate_limiter,
        acquisition_state=_fresh_acquisition_state(),
    )
    request = _daily_acquisition_request()
    first_preflight = await service.preflight_acquisition(operator_user, request)
    first_job = await service.submit_acquisition(
        operator_user,
        DataAcquisitionSubmitDTO(
            idempotency_key=first_preflight.idempotency_key,
            submit_token=first_preflight.submit_token,
        ),
    )
    second_preflight = await service.preflight_acquisition(operator_user, request)

    second_job = await service.submit_acquisition(
        operator_user,
        DataAcquisitionSubmitDTO(
            idempotency_key=second_preflight.idempotency_key,
            submit_token=second_preflight.submit_token,
        ),
    )

    assert second_job.id == first_job.id
    assert rate_limiter.check_rate_limit.await_count == 3
    actions = [
        call_kwargs.kwargs["action"]
        for call_kwargs in rate_limiter.check_rate_limit.await_args_list
    ]
    assert actions == [
        "preflight_data_acquisition",
        "trigger_data_sync",
        "preflight_data_acquisition",
    ]


@pytest.mark.asyncio()
async def test_failed_acquisition_job_can_be_retried_with_same_scope(
    rate_limiter: AsyncMock,
    operator_user: DummyUser,
) -> None:
    service = DataSyncService(
        rate_limiter=rate_limiter,
        acquisition_state=_fresh_acquisition_state(),
        acquisition_executor=_FailingAcquisitionExecutor(),
    )
    request = _daily_acquisition_request(dry_run=False)
    first_preflight = await service.preflight_acquisition(operator_user, request)
    first_job = await service.submit_acquisition(
        operator_user,
        DataAcquisitionSubmitDTO(
            idempotency_key=first_preflight.idempotency_key,
            submit_token=first_preflight.submit_token,
        ),
    )
    await service.close()
    stored_failed = service._acquisition_jobs[first_preflight.idempotency_key]  # noqa: SLF001
    assert stored_failed.status == "failed"

    service._acquisition_executor = _RecordingAcquisitionExecutor()  # noqa: SLF001
    second_preflight = await service.preflight_acquisition(operator_user, request)
    second_job = await service.submit_acquisition(
        operator_user,
        DataAcquisitionSubmitDTO(
            idempotency_key=second_preflight.idempotency_key,
            submit_token=second_preflight.submit_token,
        ),
    )
    await service.close()

    assert second_job.id != first_job.id
    assert "duplicate_submission_reused_existing_job" not in second_job.logs
    stored_retry = service._acquisition_jobs[second_preflight.idempotency_key]  # noqa: SLF001
    assert stored_retry.status == "completed"
    assert any("RuntimeError:transient provider failure" in log for log in stored_failed.logs)


@pytest.mark.asyncio()
async def test_failed_acquisition_job_redacts_sensitive_logs(
    rate_limiter: AsyncMock,
    operator_user: DummyUser,
) -> None:
    service = DataSyncService(
        rate_limiter=rate_limiter,
        acquisition_state=_fresh_acquisition_state(),
        acquisition_executor=_SensitiveFailingAcquisitionExecutor(),
    )
    preflight = await service.preflight_acquisition(
        operator_user,
        _daily_acquisition_request(dry_run=False),
    )

    await service.submit_acquisition(
        operator_user,
        DataAcquisitionSubmitDTO(
            idempotency_key=preflight.idempotency_key,
            submit_token=preflight.submit_token,
        ),
    )
    await service.close()

    stored = service._acquisition_jobs[preflight.idempotency_key]  # noqa: SLF001
    serialized_logs = "\n".join(stored.logs)
    assert stored.status == "failed"
    assert "secret-token" not in serialized_logs
    assert "secret-key" not in serialized_logs
    assert "broker-secret" not in serialized_logs
    assert "abc.def" not in serialized_logs
    assert "<redacted>" in serialized_logs


@pytest.mark.asyncio()
async def test_daily_acquisition_idempotency_uses_year_scope(
    service: DataSyncService,
    operator_user: DummyUser,
) -> None:
    today = datetime.now(UTC).date()
    year_start = date(today.year, 1, 1)
    first_end = min(today, date(today.year, 4, 30))
    first_preflight = await service.preflight_acquisition(
        operator_user,
        _daily_acquisition_request(
            start_date=year_start,
            end_date=first_end,
        ),
    )
    second_preflight = await service.preflight_acquisition(
        operator_user,
        _daily_acquisition_request(
            start_date=year_start,
            end_date=today,
        ),
    )

    assert second_preflight.idempotency_key == first_preflight.idempotency_key


@pytest.mark.asyncio()
async def test_same_scope_acquisition_jobs_are_deduplicated_across_users(
    service: DataSyncService,
    operator_user: DummyUser,
    admin_user: DummyUser,
) -> None:
    request = _daily_acquisition_request()
    operator_preflight = await service.preflight_acquisition(operator_user, request)
    admin_preflight = await service.preflight_acquisition(admin_user, request)

    operator_job = await service.submit_acquisition(
        operator_user,
        DataAcquisitionSubmitDTO(
            idempotency_key=operator_preflight.idempotency_key,
            submit_token=operator_preflight.submit_token,
        ),
    )
    admin_job = await service.submit_acquisition(
        admin_user,
        DataAcquisitionSubmitDTO(
            idempotency_key=admin_preflight.idempotency_key,
            submit_token=admin_preflight.submit_token,
        ),
    )

    assert admin_job.id == operator_job.id


@pytest.mark.asyncio()
async def test_submit_acquisition_rejects_cross_user_token(
    service: DataSyncService,
    operator_user: DummyUser,
    admin_user: DummyUser,
) -> None:
    admin_preflight = await service.preflight_acquisition(
        admin_user,
        _daily_acquisition_request(),
    )

    with pytest.raises(PreflightRequired):
        await service.submit_acquisition(
            operator_user,
            DataAcquisitionSubmitDTO(
                idempotency_key=admin_preflight.idempotency_key,
                submit_token=admin_preflight.submit_token,
            ),
        )


@pytest.mark.asyncio()
async def test_submit_acquisition_keeps_token_when_dataset_access_is_denied(
    service: DataSyncService,
    operator_user: DummyUser,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preflight = await service.preflight_acquisition(
        operator_user,
        _daily_acquisition_request(),
    )
    submission = DataAcquisitionSubmitDTO(
        idempotency_key=preflight.idempotency_key,
        submit_token=preflight.submit_token,
    )

    monkeypatch.setattr(data_sync_module, "has_dataset_permission", lambda *_args: False)
    with pytest.raises(PermissionError):
        await service.submit_acquisition(operator_user, submission)

    monkeypatch.setattr(data_sync_module, "has_dataset_permission", lambda *_args: True)
    job = await service.submit_acquisition(operator_user, submission)

    assert job.status == "completed"


@pytest.mark.asyncio()
async def test_submit_acquisition_requires_current_one_use_token(
    service: DataSyncService,
    operator_user: DummyUser,
) -> None:
    preflight = await service.preflight_acquisition(
        operator_user,
        _daily_acquisition_request(),
    )
    submission = DataAcquisitionSubmitDTO(
        idempotency_key=preflight.idempotency_key,
        submit_token=preflight.submit_token,
    )
    await service.submit_acquisition(operator_user, submission)

    with pytest.raises(PreflightRequired):
        await service.submit_acquisition(operator_user, submission)
    with pytest.raises(PreflightRequired):
        await service.submit_acquisition(
            operator_user,
            DataAcquisitionSubmitDTO(
                idempotency_key=preflight.idempotency_key,
                submit_token="forged",
            ),
        )


@pytest.mark.asyncio()
async def test_submit_acquisition_rejects_expired_token(
    service: DataSyncService,
    operator_user: DummyUser,
) -> None:
    preflight = await service.preflight_acquisition(
        operator_user,
        _daily_acquisition_request(),
    )
    service._preflight_tokens[(operator_user.user_id, preflight.idempotency_key)].expires_at = (  # noqa: SLF001
        datetime.now(UTC) - timedelta(seconds=1)
    )

    with pytest.raises(PreflightRequired):
        await service.submit_acquisition(
            operator_user,
            DataAcquisitionSubmitDTO(
                idempotency_key=preflight.idempotency_key,
                submit_token=preflight.submit_token,
            ),
        )


@pytest.mark.asyncio()
async def test_preflight_acquisition_validates_request_policy(
    service: DataSyncService,
    operator_user: DummyUser,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="Reason is required"):
        await service.preflight_acquisition(
            operator_user,
            _daily_acquisition_request(reason="   "),
        )
    with pytest.raises(ValueError, match="Start date"):
        await service.preflight_acquisition(
            operator_user,
            _daily_acquisition_request(
                start_date=date(2026, 5, 1),
                end_date=date(2026, 4, 30),
            ),
        )
    with pytest.raises(ValidationError, match="Input should be 'backfill'"):
        _daily_acquisition_request(mode="incremental")
    with pytest.raises(ValueError, match="starts at"):
        await service.preflight_acquisition(
            operator_user,
            _daily_acquisition_request(
                start_date=date(2015, 12, 31),
                end_date=date(2026, 1, 1),
            ),
        )
    tomorrow = datetime.now(UTC).date() + timedelta(days=1)
    with pytest.raises(ValueError, match="cannot be in the future"):
        await service.preflight_acquisition(
            operator_user,
            _daily_acquisition_request(
                start_date=tomorrow,
                end_date=tomorrow,
            ),
        )
    with monkeypatch.context() as patched:
        patched.setattr(data_sync_module, "_ACQUISITION_MAX_DATE_RANGE_DAYS", 1)
        with pytest.raises(ValueError, match="maximum acquisition window"):
            await service.preflight_acquisition(
                operator_user,
                _daily_acquisition_request(
                    start_date=date(2026, 1, 1),
                    end_date=date(2026, 1, 2),
                ),
            )
    with pytest.raises(ValueError, match="requires adjustment_mode=raw"):
        await service.preflight_acquisition(
            operator_user,
            _daily_acquisition_request(adjustment_mode=None),
        )
    with pytest.raises(ValueError, match="does not accept adjustment metadata"):
        await service.preflight_acquisition(
            operator_user,
            _daily_acquisition_request(
                dataset=ALPACA_SIP_CORP_ACTIONS_DATASET,
                symbol_source="AAPL",
            ),
        )
    with pytest.raises(ValueError, match="requires symbols or ids"):
        await service.preflight_acquisition(
            operator_user,
            _daily_acquisition_request(
                dataset=ALPACA_SIP_CORP_ACTIONS_DATASET,
                adjustment_mode=None,
                symbol_source="all",
            ),
        )
    with pytest.raises(ValueError, match="requires explicit symbols"):
        await service.preflight_acquisition(
            operator_user,
            _daily_acquisition_request(symbol_source="all"),
        )
    with pytest.raises(ValueError, match="Symbol source must be"):
        await service.preflight_acquisition(
            operator_user,
            _daily_acquisition_request(symbol_source="DROP TABLE"),
        )
    with pytest.raises(ValueError, match="File symbol sources"):
        await service.preflight_acquisition(
            operator_user,
            _daily_acquisition_request(symbol_source="file:../../etc/passwd"),
        )
    with pytest.raises(ValueError, match="File symbol sources"):
        await service.preflight_acquisition(
            operator_user,
            _daily_acquisition_request(symbol_source="file:./config/secrets.env"),
        )
    with pytest.raises(ValueError, match="Universe symbol sources"):
        await service.preflight_acquisition(
            operator_user,
            _daily_acquisition_request(symbol_source="universe:sp500"),
        )

    symbol_file = tmp_path / "data" / "symbols" / "core.txt"
    symbol_file.parent.mkdir(parents=True)
    symbol_file.write_text("MSFT\nAAPL\nAAPL\n")
    monkeypatch.setattr(data_sync_module, "_REPO_ROOT", tmp_path)
    file_preflight = await service.preflight_acquisition(
        operator_user,
        _daily_acquisition_request(symbol_source="file:data/symbols/core.txt"),
    )
    assert file_preflight.symbol_source.startswith("file:data/symbols/core.txt#sha256=")

    original_key = file_preflight.idempotency_key
    command = data_sync_module._build_acquisition_command(file_preflight)  # noqa: SLF001
    assert "--symbols" in command
    assert "AAPL,MSFT" in command

    symbol_file.write_text("AAPL\nGOOG\n")
    changed_preflight = await service.preflight_acquisition(
        operator_user,
        _daily_acquisition_request(symbol_source="file:data/symbols/core.txt"),
    )
    assert changed_preflight.idempotency_key != original_key
    with pytest.raises(ValueError, match="changed after preflight"):
        data_sync_module._build_acquisition_command(file_preflight)  # noqa: SLF001


@pytest.mark.asyncio()
async def test_symbol_list_idempotency_uses_canonical_scope(
    service: DataSyncService,
    operator_user: DummyUser,
) -> None:
    first_preflight = await service.preflight_acquisition(
        operator_user,
        _daily_acquisition_request(symbol_source="AAPL, MSFT"),
    )
    second_preflight = await service.preflight_acquisition(
        operator_user,
        _daily_acquisition_request(symbol_source="msft,aapl,AAPL"),
    )

    assert first_preflight.symbol_source == "AAPL,MSFT"
    assert second_preflight.idempotency_key == first_preflight.idempotency_key


@pytest.mark.asyncio()
async def test_corp_action_ids_idempotency_uses_canonical_scope(
    service: DataSyncService,
    operator_user: DummyUser,
) -> None:
    base_request = {
        "dataset": ALPACA_SIP_CORP_ACTIONS_DATASET,
        "adjustment_mode": None,
        "reason": "fill corporate actions",
    }
    first_preflight = await service.preflight_acquisition(
        operator_user,
        _daily_acquisition_request(symbol_source="ids:B,A", **base_request),
    )
    second_preflight = await service.preflight_acquisition(
        operator_user,
        _daily_acquisition_request(symbol_source="ids:A,B,A", **base_request),
    )

    assert first_preflight.symbol_source == "ids:A,B"
    assert second_preflight.idempotency_key == first_preflight.idempotency_key
    command = data_sync_module._build_acquisition_command(first_preflight)  # noqa: SLF001
    assert "--ids" in command
    assert "A,B" in command
    assert "--symbols" not in command


@pytest.mark.asyncio()
async def test_acquisition_preflight_and_submit_are_rate_limited(
    rate_limiter: AsyncMock,
    operator_user: DummyUser,
) -> None:
    service = DataSyncService(
        rate_limiter=rate_limiter,
        acquisition_state=_fresh_acquisition_state(),
    )

    preflight = await service.preflight_acquisition(
        operator_user,
        _daily_acquisition_request(),
    )
    await service.submit_acquisition(
        operator_user,
        DataAcquisitionSubmitDTO(
            idempotency_key=preflight.idempotency_key,
            submit_token=preflight.submit_token,
        ),
    )

    rate_limiter.check_rate_limit.assert_has_awaits(
        [
            call(
                user_id=operator_user.user_id,
                action="preflight_data_acquisition",
                max_requests=10,
                window_seconds=60,
            ),
            call(
                user_id=operator_user.user_id,
                action="trigger_data_sync",
                max_requests=1,
                window_seconds=60,
            ),
        ]
    )


@pytest.mark.asyncio()
async def test_acquisition_rate_limits_block_preflight_and_submit(
    operator_user: DummyUser,
    caplog: pytest.LogCaptureFixture,
) -> None:
    preflight_limiter = AsyncMock()
    preflight_limiter.check_rate_limit = AsyncMock(return_value=(False, 0))
    preflight_blocked = DataSyncService(
        rate_limiter=preflight_limiter,
        acquisition_state=_fresh_acquisition_state(),
    )

    with pytest.raises(RateLimitExceeded):
        await preflight_blocked.preflight_acquisition(
            operator_user,
            _daily_acquisition_request(),
        )

    submit_limiter = AsyncMock()
    submit_limiter.check_rate_limit = AsyncMock(side_effect=[(True, 0), (False, 0), (True, 0)])
    submit_blocked = DataSyncService(
        rate_limiter=submit_limiter,
        acquisition_state=_fresh_acquisition_state(),
    )
    preflight = await submit_blocked.preflight_acquisition(
        operator_user,
        _daily_acquisition_request(),
    )

    caplog.clear()
    caplog.set_level(logging.INFO, logger=data_sync_module.__name__)
    with pytest.raises(RateLimitExceeded):
        await submit_blocked.submit_acquisition(
            operator_user,
            DataAcquisitionSubmitDTO(
                idempotency_key=preflight.idempotency_key,
                submit_token=preflight.submit_token,
            ),
        )
    blocked_records = [
        record for record in caplog.records if record.message == "acquisition_submission_blocked"
    ]
    assert blocked_records
    assert blocked_records[-1].__dict__["dataset"] == ALPACA_SIP_DAILY_DATASET
    assert blocked_records[-1].__dict__["user_id"] == operator_user.user_id
    assert blocked_records[-1].__dict__["block_reason"] == "rate_limited"
    assert submit_blocked._acquisition_jobs == {}  # noqa: SLF001

    retry_job = await submit_blocked.submit_acquisition(
        operator_user,
        DataAcquisitionSubmitDTO(
            idempotency_key=preflight.idempotency_key,
            submit_token=preflight.submit_token,
        ),
    )
    assert retry_job.status == "completed"


@pytest.mark.asyncio()
async def test_redis_acquisition_store_consumes_preflight_token_once(
    rate_limiter: AsyncMock,
    operator_user: DummyUser,
) -> None:
    fake_redis = _FakeRedis()
    service = DataSyncService(
        rate_limiter=rate_limiter,
        acquisition_store=data_sync_module._RedisAcquisitionStore(fake_redis),  # noqa: SLF001
    )
    preflight = await service.preflight_acquisition(
        operator_user,
        _daily_acquisition_request(),
    )
    forged = DataAcquisitionSubmitDTO(
        idempotency_key=preflight.idempotency_key,
        submit_token="forged",
    )
    with pytest.raises(PreflightRequired):
        await service.submit_acquisition(operator_user, forged)

    submission = DataAcquisitionSubmitDTO(
        idempotency_key=preflight.idempotency_key,
        submit_token=preflight.submit_token,
    )
    job = await service.submit_acquisition(operator_user, submission)

    assert job.status == "completed"
    with pytest.raises(PreflightRequired):
        await service.submit_acquisition(operator_user, submission)


@pytest.mark.asyncio()
async def test_redis_acquisition_store_rejects_expired_token_in_lua(
    rate_limiter: AsyncMock,
    operator_user: DummyUser,
) -> None:
    fake_redis = _FakeRedis()
    service = DataSyncService(
        rate_limiter=rate_limiter,
        acquisition_store=data_sync_module._RedisAcquisitionStore(fake_redis),  # noqa: SLF001
    )
    preflight = await service.preflight_acquisition(
        operator_user,
        _daily_acquisition_request(),
    )
    for payload in fake_redis.hashes.values():
        payload["expires_at_epoch"] = "0"

    with pytest.raises(PreflightRequired):
        await service.submit_acquisition(
            operator_user,
            DataAcquisitionSubmitDTO(
                idempotency_key=preflight.idempotency_key,
                submit_token=preflight.submit_token,
            ),
        )
    assert not fake_redis.hashes


@pytest.mark.asyncio()
async def test_redis_acquisition_store_reuses_only_active_or_successful_jobs() -> None:
    store = data_sync_module._RedisAcquisitionStore(_FakeRedis())  # noqa: SLF001
    now = datetime.now(UTC)
    idempotency_key = "acq_redis"
    running = _daily_acquisition_job(
        job_id="job-running",
        idempotency_key=idempotency_key,
        status="running",
    ).model_copy(update={"started_at": now, "heartbeat_at": now, "completed_at": None})

    assert await store.reserve_job(running, now) is None
    duplicate = await store.reserve_job(
        _daily_acquisition_job(
            job_id="job-duplicate",
            idempotency_key=idempotency_key,
            status="queued",
        ).model_copy(update={"started_at": now, "heartbeat_at": now, "completed_at": None}),
        now,
    )
    assert duplicate is not None
    assert duplicate.id == "job-running"

    failed = running.model_copy(update={"status": "failed", "completed_at": now})
    await store.update_job(failed, now)
    assert await store.get_reusable_job(idempotency_key, now) is None

    retry = _daily_acquisition_job(
        job_id="job-retry",
        idempotency_key=idempotency_key,
        status="queued",
    ).model_copy(update={"started_at": now, "heartbeat_at": now, "completed_at": None})
    assert await store.reserve_job(retry, now) is None
    stored_retry = await store.get_reusable_job(idempotency_key, now)
    assert stored_retry is not None
    assert stored_retry.id == "job-retry"


@pytest.mark.asyncio()
async def test_in_memory_acquisition_store_reuses_fresh_heartbeat_job() -> None:
    state = _fresh_acquisition_state()
    store = data_sync_module._InMemoryAcquisitionStore(state)  # noqa: SLF001
    now = datetime.now(UTC)
    idempotency_key = "acq_in_memory_long_running"
    old_started_at = now - timedelta(
        seconds=data_sync_module._ACQUISITION_JOB_RETENTION_SECONDS + 1  # noqa: SLF001
    )
    long_running = _daily_acquisition_job(
        job_id="job-long-running",
        idempotency_key=idempotency_key,
        status="running",
    ).model_copy(update={"started_at": old_started_at, "heartbeat_at": now, "completed_at": None})

    assert await store.reserve_job(long_running, old_started_at) is None
    retry = _daily_acquisition_job(
        job_id="job-retry",
        idempotency_key=idempotency_key,
        status="queued",
    ).model_copy(update={"started_at": now, "heartbeat_at": now, "completed_at": None})
    duplicate = await store.reserve_job(retry, now)

    assert duplicate is not None
    assert duplicate.id == "job-long-running"

    mismatched_completion = retry.model_copy(update={"status": "completed", "completed_at": now})
    await store.update_job(mismatched_completion, now)

    stored = state.acquisition_jobs[idempotency_key]
    assert stored.id == "job-long-running"
    assert stored.status == "running"


@pytest.mark.asyncio()
async def test_in_memory_acquisition_store_rejects_late_heartbeat_after_terminal() -> None:
    state = _fresh_acquisition_state()
    store = data_sync_module._InMemoryAcquisitionStore(state)  # noqa: SLF001
    now = datetime.now(UTC)
    idempotency_key = "acq_in_memory_late_heartbeat"
    running = _daily_acquisition_job(
        job_id="job-terminal",
        idempotency_key=idempotency_key,
        status="running",
    ).model_copy(update={"started_at": now, "heartbeat_at": now, "completed_at": None})

    assert await store.reserve_job(running, now) is None
    completed = running.model_copy(
        update={
            "status": "completed",
            "completed_at": now,
            "produced_manifest_ids": ["manifest-1"],
        }
    )
    await store.update_job(completed, now)
    late_heartbeat = running.model_copy(update={"heartbeat_at": now + timedelta(seconds=1)})
    await store.update_job(late_heartbeat, now + timedelta(seconds=1))

    stored = state.acquisition_jobs[idempotency_key]
    assert stored.status == "completed"
    assert stored.produced_manifest_ids == ["manifest-1"]


@pytest.mark.asyncio()
async def test_redis_acquisition_store_reuses_fresh_heartbeat_job() -> None:
    store = data_sync_module._RedisAcquisitionStore(_FakeRedis())  # noqa: SLF001
    now = datetime.now(UTC)
    idempotency_key = "acq_long_running"
    old_started_at = now - timedelta(hours=8)
    long_running = _daily_acquisition_job(
        job_id="job-long-running",
        idempotency_key=idempotency_key,
        status="running",
    ).model_copy(update={"started_at": old_started_at, "heartbeat_at": now, "completed_at": None})

    assert await store.reserve_job(long_running, old_started_at) is None
    stored_long_running = await store.get_reusable_job(idempotency_key, now)
    assert stored_long_running is not None
    assert stored_long_running.id == "job-long-running"
    retry = _daily_acquisition_job(
        job_id="job-retry",
        idempotency_key=idempotency_key,
        status="queued",
    ).model_copy(update={"started_at": now, "heartbeat_at": now, "completed_at": None})

    duplicate = await store.reserve_job(retry, now)
    assert duplicate is not None
    assert duplicate.id == "job-long-running"

    mismatched_completion = retry.model_copy(update={"status": "completed", "completed_at": now})
    await store.update_job(mismatched_completion, now)
    stored_after_mismatched_update = await store.get_reusable_job(idempotency_key, now)
    assert stored_after_mismatched_update is not None
    assert stored_after_mismatched_update.id == "job-long-running"


@pytest.mark.asyncio()
async def test_redis_acquisition_store_rejects_late_heartbeat_after_terminal() -> None:
    store = data_sync_module._RedisAcquisitionStore(_FakeRedis())  # noqa: SLF001
    now = datetime.now(UTC)
    idempotency_key = "acq_redis_late_heartbeat"
    running = _daily_acquisition_job(
        job_id="job-terminal",
        idempotency_key=idempotency_key,
        status="running",
    ).model_copy(update={"started_at": now, "heartbeat_at": now, "completed_at": None})

    assert await store.reserve_job(running, now) is None
    completed = running.model_copy(
        update={
            "status": "completed",
            "completed_at": now,
            "produced_manifest_ids": ["manifest-1"],
        }
    )
    await store.update_job(completed, now)
    late_heartbeat = running.model_copy(update={"heartbeat_at": now + timedelta(seconds=1)})
    await store.update_job(late_heartbeat, now + timedelta(seconds=1))

    stored = await store.get_reusable_job(idempotency_key, now + timedelta(seconds=1))
    assert stored is not None
    assert stored.status == "completed"
    assert stored.produced_manifest_ids == ["manifest-1"]


@pytest.mark.asyncio()
async def test_redis_acquisition_store_replaces_stale_active_job() -> None:
    store = data_sync_module._RedisAcquisitionStore(_FakeRedis())  # noqa: SLF001
    now = datetime.now(UTC)
    idempotency_key = "acq_stale_running"
    stale_at = now - timedelta(seconds=data_sync_module._ACQUISITION_ACTIVE_JOB_STALE_SECONDS + 1)  # noqa: SLF001
    stale_running = _daily_acquisition_job(
        job_id="job-stale-running",
        idempotency_key=idempotency_key,
        status="running",
    ).model_copy(update={"started_at": stale_at, "heartbeat_at": stale_at, "completed_at": None})

    assert await store.reserve_job(stale_running, stale_at) is None
    assert await store.get_reusable_job(idempotency_key, now) is None
    retry = _daily_acquisition_job(
        job_id="job-retry",
        idempotency_key=idempotency_key,
        status="queued",
    ).model_copy(update={"started_at": now, "heartbeat_at": now, "completed_at": None})

    duplicate = await store.reserve_job(retry, now)
    assert duplicate is None
    stored_retry = await store.get_reusable_job(idempotency_key, now)
    assert stored_retry is not None
    assert stored_retry.id == "job-retry"


@pytest.mark.asyncio()
async def test_acquisition_state_cleanup_removes_expired_tokens_and_bounds_jobs(
    service: DataSyncService,
    operator_user: DummyUser,
) -> None:
    preflight = await service.preflight_acquisition(
        operator_user,
        _daily_acquisition_request(),
    )
    token_key = (operator_user.user_id, preflight.idempotency_key)
    service._preflight_tokens[token_key].expires_at = datetime.now(UTC) - timedelta(  # noqa: SLF001
        seconds=1
    )
    for index in range(data_sync_module._ACQUISITION_MAX_JOBS + 1):  # noqa: SLF001
        service._acquisition_jobs[f"job-{index}"] = DataAcquisitionJobDTO(  # noqa: SLF001
            id=f"job-{index}",
            dataset=ALPACA_SIP_DAILY_DATASET,
            status="queued",
            idempotency_key=f"job-{index}",
            mode="backfill",
            dry_run=True,
            provider_id="alpaca_sip",
            source_feed="sip",
            canonical_storage_mode="raw",
            read_time_adjustment_mode="unavailable",
            adjustment_mode="raw",
            started_at=datetime.now(UTC) - timedelta(seconds=index),
            submit_token_status="consumed",
            adapter="script:scripts/data/alpaca_sip_sync.py",
            produced_manifest_ids=[],
            validation_output=[],
            logs=[],
        )
    stale_key = "stale-queued"
    service._acquisition_jobs[stale_key] = DataAcquisitionJobDTO(  # noqa: SLF001
        id=stale_key,
        dataset=ALPACA_SIP_DAILY_DATASET,
        status="queued",
        idempotency_key=stale_key,
        mode="backfill",
        dry_run=True,
        provider_id="alpaca_sip",
        source_feed="sip",
        canonical_storage_mode="raw",
        read_time_adjustment_mode="unavailable",
        adjustment_mode="raw",
        started_at=datetime.now(UTC)
        - timedelta(seconds=data_sync_module._ACQUISITION_JOB_RETENTION_SECONDS + 1),  # noqa: SLF001
        submit_token_status="consumed",
        adapter="script:scripts/data/alpaca_sip_sync.py",
        produced_manifest_ids=[],
        validation_output=[],
        logs=[],
    )

    await service._acquisition_store.sweep(datetime.now(UTC))  # noqa: SLF001

    assert token_key not in service._preflight_tokens  # noqa: SLF001
    assert stale_key not in service._acquisition_jobs  # noqa: SLF001
    assert len(service._acquisition_jobs) == data_sync_module._ACQUISITION_MAX_JOBS  # noqa: SLF001


@pytest.mark.asyncio()
async def test_background_acquisition_times_out_hung_executor(
    monkeypatch: pytest.MonkeyPatch,
    rate_limiter: AsyncMock,
    operator_user: DummyUser,
) -> None:
    monkeypatch.setattr(data_sync_module, "_ACQUISITION_EXECUTION_TIMEOUT_SECONDS", 0.01)
    executor = _BlockingAcquisitionExecutor()
    service = DataSyncService(
        rate_limiter=rate_limiter,
        acquisition_state=_fresh_acquisition_state(),
        acquisition_executor=executor,
    )
    preflight = await service.preflight_acquisition(
        operator_user,
        _daily_acquisition_request(dry_run=False),
    )
    job = await service.submit_acquisition(
        operator_user,
        DataAcquisitionSubmitDTO(
            idempotency_key=preflight.idempotency_key,
            submit_token=preflight.submit_token,
        ),
    )
    await executor.started.wait()

    await asyncio.gather(
        *list(service._background_acquisition_tasks),  # noqa: SLF001
        return_exceptions=True,
    )

    stored = service._acquisition_jobs[preflight.idempotency_key]  # noqa: SLF001
    assert stored.id == job.id
    assert stored.status == "failed"
    assert "TimeoutError" in stored.validation_output
    assert not service._background_acquisition_tasks  # noqa: SLF001


@pytest.mark.asyncio()
async def test_close_can_cancel_running_background_acquisition(
    rate_limiter: AsyncMock,
    operator_user: DummyUser,
) -> None:
    executor = _BlockingAcquisitionExecutor()
    service = DataSyncService(
        rate_limiter=rate_limiter,
        acquisition_state=_fresh_acquisition_state(),
        acquisition_executor=executor,
    )
    preflight = await service.preflight_acquisition(
        operator_user,
        _daily_acquisition_request(dry_run=False),
    )
    job = await service.submit_acquisition(
        operator_user,
        DataAcquisitionSubmitDTO(
            idempotency_key=preflight.idempotency_key,
            submit_token=preflight.submit_token,
        ),
    )
    await executor.started.wait()

    await service.close(cancel_running=True)

    stored = service._acquisition_jobs[preflight.idempotency_key]  # noqa: SLF001
    assert stored.id == job.id
    assert stored.status == "failed"
    assert "job_cancelled_during_shutdown" in stored.logs
    assert not service._background_acquisition_tasks  # noqa: SLF001


@pytest.mark.asyncio()
async def test_background_acquisition_concurrency_is_shared_across_services(
    rate_limiter: AsyncMock,
    operator_user: DummyUser,
) -> None:
    first_executor = _BlockingAcquisitionExecutor()
    second_executor = _BlockingAcquisitionExecutor()
    first_service = DataSyncService(
        rate_limiter=rate_limiter,
        acquisition_state=_fresh_acquisition_state(),
        acquisition_executor=first_executor,
    )
    second_service = DataSyncService(
        rate_limiter=rate_limiter,
        acquisition_state=_fresh_acquisition_state(),
        acquisition_executor=second_executor,
    )
    first_preflight = await first_service.preflight_acquisition(
        operator_user,
        _daily_acquisition_request(dry_run=False, symbol_source="AAPL"),
    )
    second_preflight = await second_service.preflight_acquisition(
        operator_user,
        _daily_acquisition_request(dry_run=False, symbol_source="MSFT"),
    )

    try:
        await first_service.submit_acquisition(
            operator_user,
            DataAcquisitionSubmitDTO(
                idempotency_key=first_preflight.idempotency_key,
                submit_token=first_preflight.submit_token,
            ),
        )
        await first_executor.started.wait()
        await second_service.submit_acquisition(
            operator_user,
            DataAcquisitionSubmitDTO(
                idempotency_key=second_preflight.idempotency_key,
                submit_token=second_preflight.submit_token,
            ),
        )
        await asyncio.sleep(0.05)

        assert not second_executor.started.is_set()

        first_executor.release.set()
        await first_service.close()
        await asyncio.wait_for(second_executor.started.wait(), timeout=1)
    finally:
        first_executor.release.set()
        second_executor.release.set()
        await first_service.close()
        await second_service.close()


@pytest.mark.asyncio()
async def test_submit_acquisition_logs_audit_event(
    service: DataSyncService,
    operator_user: DummyUser,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=data_sync_module.__name__)
    preflight = await service.preflight_acquisition(
        operator_user,
        _daily_acquisition_request(reason="audit trail"),
    )

    job = await service.submit_acquisition(
        operator_user,
        DataAcquisitionSubmitDTO(
            idempotency_key=preflight.idempotency_key,
            submit_token=preflight.submit_token,
        ),
    )

    audit_records = [
        record for record in caplog.records if record.message == "acquisition_submitted"
    ]
    assert audit_records
    assert audit_records[-1].__dict__["dataset"] == ALPACA_SIP_DAILY_DATASET
    assert audit_records[-1].__dict__["job_id"] == job.id
    assert audit_records[-1].__dict__["user_id"] == operator_user.user_id
    assert audit_records[-1].__dict__["reason"] == "audit trail"
    assert audit_records[-1].__dict__["dry_run"] is True
    assert audit_records[-1].__dict__["start_date"] == "2026-01-01"
    assert audit_records[-1].__dict__["end_date"] == "2026-12-31"
    assert audit_records[-1].__dict__["symbol_source"] == "AAPL,MSFT"
    assert audit_records[-1].__dict__["adjustment_mode"] == "raw"


@pytest.mark.asyncio()
async def test_trigger_sync_rate_limited_raises(
    operator_user: DummyUser,
) -> None:
    rate_limiter = AsyncMock()
    rate_limiter.check_rate_limit = AsyncMock(return_value=(False, 0))
    service = DataSyncService(
        rate_limiter=rate_limiter,
        acquisition_state=_fresh_acquisition_state(),
    )

    with pytest.raises(RateLimitExceeded):
        await service.trigger_sync(operator_user, dataset="crsp", reason="manual")


@pytest.mark.asyncio()
async def test_rate_limit_check_delegates(
    rate_limiter: AsyncMock, operator_user: DummyUser
) -> None:
    service = DataSyncService(
        rate_limiter=rate_limiter,
        acquisition_state=_fresh_acquisition_state(),
    )

    allowed, remaining = await service._rate_limit_check(
        operator_user.user_id,
        action="trigger_data_sync",
        max_requests=2,
        window=60,
    )

    assert allowed is True
    assert remaining == 0
    rate_limiter.check_rate_limit.assert_awaited_once_with(
        user_id=operator_user.user_id,
        action="trigger_data_sync",
        max_requests=2,
        window_seconds=60,
    )


@pytest.mark.asyncio()
async def test_enforce_rate_limit_uses_user_id_lookup(
    rate_limiter: AsyncMock,
) -> None:
    service = DataSyncService(
        rate_limiter=rate_limiter,
        acquisition_state=_fresh_acquisition_state(),
    )

    class UserObj:
        def __init__(self) -> None:
            self.user_id = "user-obj"
            self.role = Role.OPERATOR

    await service._enforce_rate_limit(
        UserObj(), action="trigger_data_sync", max_requests=1, window=60
    )

    rate_limiter.check_rate_limit.assert_awaited_once_with(
        user_id="user-obj",
        action="trigger_data_sync",
        max_requests=1,
        window_seconds=60,
    )
