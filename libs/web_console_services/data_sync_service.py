"""Service layer for data sync operations.

Enforces RBAC, dataset-level access, and rate limiting at server-side.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import sys
import weakref
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from functools import partial
from inspect import isawaitable, iscoroutinefunction
from pathlib import Path, PurePosixPath
from typing import Any, Protocol
from uuid import uuid4

from config.settings import get_settings
from libs.data.data_providers.registry import ProviderType, get_provider_spec
from libs.platform.web_console_auth.helpers import get_user_id
from libs.platform.web_console_auth.permissions import (
    Permission,
    has_dataset_permission,
    has_permission,
)
from libs.platform.web_console_auth.rate_limiter import RateLimiter, get_rate_limiter
from libs.web_console_services.data_manifest_service import (
    ALPACA_SIP_CORP_ACTIONS_DATASET,
    ALPACA_SIP_DAILY_DATASET,
    DataManifestService,
)

from .schemas.data_management import (
    DataAcquisitionJobDTO,
    DataAcquisitionPreflightDTO,
    DataAcquisitionRequestDTO,
    DataAcquisitionSubmitDTO,
    SyncJobDTO,
    SyncLogEntry,
    SyncScheduleDTO,
    SyncScheduleUpdateDTO,
    SyncStatusDTO,
)

_SUPPORTED_DATASETS = ("crsp", "compustat", "taq", "fama_french", "alpaca_sip")
_SUPPORTED_ACQUISITION_DATASETS = (
    ALPACA_SIP_DAILY_DATASET,
    ALPACA_SIP_CORP_ACTIONS_DATASET,
)
_ACQUISITION_TOKEN_TTL_SECONDS = 300
_ACQUISITION_JOB_RETENTION_SECONDS = 86_400
_ACQUISITION_MAX_PREFLIGHT_TOKENS = 512
_ACQUISITION_MAX_JOBS = 256
_ACQUISITION_MAX_DATE_RANGE_DAYS = 5_500
_ACQUISITION_REUSABLE_JOB_STATUSES = frozenset({"queued", "running", "completed"})
_ACQUISITION_TERMINAL_JOB_STATUSES = frozenset({"completed", "failed"})
_ACQUISITION_BACKGROUND_CONCURRENCY = max(
    1,
    int(os.getenv("DATA_ACQUISITION_BACKGROUND_CONCURRENCY", "1")),
)
_ACQUISITION_HEARTBEAT_INTERVAL_SECONDS = max(
    1,
    int(os.getenv("DATA_ACQUISITION_HEARTBEAT_INTERVAL_SECONDS", "30")),
)
_ACQUISITION_EXECUTION_TIMEOUT_SECONDS = max(
    _ACQUISITION_HEARTBEAT_INTERVAL_SECONDS,
    int(os.getenv("DATA_ACQUISITION_EXECUTION_TIMEOUT_SECONDS", "21600")),
)
_ACQUISITION_ACTIVE_JOB_STALE_SECONDS = max(
    _ACQUISITION_HEARTBEAT_INTERVAL_SECONDS * 3,
    int(os.getenv("DATA_ACQUISITION_ACTIVE_JOB_STALE_SECONDS", "300")),
)
_ACQUISITION_MAX_SYMBOLS_FROM_FILE = 5_000
_SYMBOL_FILE_PREFIX = "data/symbols/"
_SYMBOL_FILE_EXTENSIONS = (".csv", ".txt")
_SYMBOL_FILE_FINGERPRINT_SEPARATOR = "#sha256="
_SYMBOL_TOKEN_PATTERN = re.compile(r"^[A-Z][A-Z0-9.-]{0,14}$")
_SOURCE_VALUE_PATTERN = re.compile(r"^[A-Za-z0-9_./,-]{1,200}$")
_SYMBOL_SOURCE_PREFIXES = ("file:", "universe:", "ids:")
_ACQUISITION_REDIS_PREFIX = "web-console:data-acquisition"
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SENSITIVE_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(api[_-]?key|authorization|secret[_-]?key|token|"
    r"APCA-API-KEY-ID|APCA-API-SECRET-KEY)(=|:)\s*(?:Bearer\s+)?[^,\s]+"
)
_SENSITIVE_BEARER_PATTERN = re.compile(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/=-]+")
_MANIFEST_OUTPUT_PREFIX = "MANIFEST_JSON:"

logger = logging.getLogger(__name__)
_BACKGROUND_ACQUISITION_SEMAPHORES: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop, asyncio.Semaphore
] = weakref.WeakKeyDictionary()


class RateLimitExceeded(RuntimeError):
    """Raised when a rate limit is exceeded."""


class PreflightRequired(RuntimeError):
    """Raised when an acquisition submit lacks a current one-use preflight token."""


@dataclass
class _SubmitTokenRecord:
    user_id: str
    token_hash: str
    expires_at: datetime
    preflight: DataAcquisitionPreflightDTO
    reason: str
    consumed: bool = False


@dataclass
class _AcquisitionState:
    lock: asyncio.Lock
    preflight_tokens: dict[tuple[str, str], _SubmitTokenRecord]
    acquisition_jobs: dict[str, DataAcquisitionJobDTO]


class _AcquisitionStore(Protocol):
    async def save_preflight_token(
        self,
        key: tuple[str, str],
        record: _SubmitTokenRecord,
        now: datetime,
    ) -> None:
        """Persist a one-use preflight token record."""

    async def consume_preflight_token(
        self,
        *,
        user_id: str,
        idempotency_key: str,
        token_hash: str,
        now: datetime,
    ) -> _SubmitTokenRecord | None:
        """Atomically validate and consume a preflight token."""

    async def get_preflight_token(
        self,
        *,
        user_id: str,
        idempotency_key: str,
        token_hash: str,
        now: datetime,
    ) -> _SubmitTokenRecord | None:
        """Validate a preflight token without consuming it."""

    async def get_reusable_job(
        self,
        idempotency_key: str,
        now: datetime,
    ) -> DataAcquisitionJobDTO | None:
        """Return a retained job that should satisfy duplicate submits."""

    async def reserve_job(
        self,
        job: DataAcquisitionJobDTO,
        now: datetime,
    ) -> DataAcquisitionJobDTO | None:
        """Persist a new job if absent; return an existing retained job on conflict."""

    async def update_job(self, job: DataAcquisitionJobDTO, now: datetime) -> None:
        """Persist updated job state."""

    async def sweep(self, now: datetime) -> None:
        """Remove expired local state when the backend requires explicit cleanup."""


class _InMemoryAcquisitionStore:
    def __init__(self, state: _AcquisitionState) -> None:
        self.lock = state.lock
        self.preflight_tokens = state.preflight_tokens
        self.acquisition_jobs = state.acquisition_jobs

    async def save_preflight_token(
        self,
        key: tuple[str, str],
        record: _SubmitTokenRecord,
        now: datetime,
    ) -> None:
        async with self.lock:
            self._sweep_locked(now)
            self.preflight_tokens[key] = record
            self._trim_preflight_tokens_locked()

    async def consume_preflight_token(
        self,
        *,
        user_id: str,
        idempotency_key: str,
        token_hash: str,
        now: datetime,
    ) -> _SubmitTokenRecord | None:
        async with self.lock:
            self._sweep_locked(now)
            record = self.preflight_tokens.get((user_id, idempotency_key))
            if (
                record is None
                or record.user_id != user_id
                or record.consumed
                or record.expires_at <= now
                or not hmac.compare_digest(record.token_hash, token_hash)
            ):
                return None
            record.consumed = True
            return record

    async def get_preflight_token(
        self,
        *,
        user_id: str,
        idempotency_key: str,
        token_hash: str,
        now: datetime,
    ) -> _SubmitTokenRecord | None:
        async with self.lock:
            self._sweep_locked(now)
            record = self.preflight_tokens.get((user_id, idempotency_key))
            if (
                record is None
                or record.user_id != user_id
                or record.consumed
                or record.expires_at <= now
                or not hmac.compare_digest(record.token_hash, token_hash)
            ):
                return None
            return record

    async def get_reusable_job(
        self,
        idempotency_key: str,
        now: datetime,
    ) -> DataAcquisitionJobDTO | None:
        async with self.lock:
            self._sweep_locked(now)
            job = self.acquisition_jobs.get(idempotency_key)
            return job if job is not None and _is_reusable_acquisition_job(job, now) else None

    async def reserve_job(
        self,
        job: DataAcquisitionJobDTO,
        now: datetime,
    ) -> DataAcquisitionJobDTO | None:
        async with self.lock:
            self._sweep_locked(now)
            existing = self.acquisition_jobs.get(job.idempotency_key)
            if existing is not None and _is_reusable_acquisition_job(existing, now):
                return existing
            self.acquisition_jobs[job.idempotency_key] = job
            self._trim_acquisition_jobs_locked()
            return None

    async def update_job(self, job: DataAcquisitionJobDTO, now: datetime) -> None:
        async with self.lock:
            self._sweep_locked(now)
            existing = self.acquisition_jobs.get(job.idempotency_key)
            if existing is not None and existing.id != job.id:
                return
            if (
                existing is not None
                and existing.status in _ACQUISITION_TERMINAL_JOB_STATUSES
                and job.status not in _ACQUISITION_TERMINAL_JOB_STATUSES
            ):
                return
            self.acquisition_jobs[job.idempotency_key] = job
            self._trim_acquisition_jobs_locked()

    async def sweep(self, now: datetime) -> None:
        async with self.lock:
            self._sweep_locked(now)

    def _sweep_locked(self, now: datetime) -> None:
        expired_token_keys = [
            key for key, record in self.preflight_tokens.items() if record.expires_at <= now
        ]
        for token_key in expired_token_keys:
            del self.preflight_tokens[token_key]

        job_cutoff = now - timedelta(seconds=_ACQUISITION_JOB_RETENTION_SECONDS)
        stale_job_keys = [
            key
            for key, job in self.acquisition_jobs.items()
            if _retention_anchor_for_acquisition_job(job, now) <= job_cutoff
        ]
        for job_key in stale_job_keys:
            del self.acquisition_jobs[job_key]

        self._trim_acquisition_jobs_locked()

    def _trim_preflight_tokens_locked(self) -> None:
        overflow = len(self.preflight_tokens) - _ACQUISITION_MAX_PREFLIGHT_TOKENS
        if overflow <= 0:
            return
        oldest_keys = sorted(
            self.preflight_tokens,
            key=lambda key: self.preflight_tokens[key].expires_at,
        )[:overflow]
        for key in oldest_keys:
            del self.preflight_tokens[key]

    def _trim_acquisition_jobs_locked(self) -> None:
        overflow = len(self.acquisition_jobs) - _ACQUISITION_MAX_JOBS
        if overflow <= 0:
            return
        oldest_keys = sorted(
            self.acquisition_jobs,
            key=lambda key: self.acquisition_jobs[key].started_at
            or datetime.min.replace(tzinfo=UTC),
        )[:overflow]
        for key in oldest_keys:
            del self.acquisition_jobs[key]


def _is_async_redis_client(redis_client: Any) -> bool:
    with suppress(ImportError):
        from redis.asyncio import Redis as AsyncRedis

        if isinstance(redis_client, AsyncRedis):
            return True

    return any(
        iscoroutinefunction(getattr(redis_client, method_name, None))
        for method_name in ("eval", "get", "hget", "delete")
    )


class _RedisAcquisitionStore:
    _CONSUME_TOKEN_SCRIPT = """
local token_hash = redis.call("HGET", KEYS[1], "token_hash")
if not token_hash then
  return nil
end
if token_hash ~= ARGV[1] then
  return nil
end
local expires_at_epoch = tonumber(redis.call("HGET", KEYS[1], "expires_at_epoch") or "0")
if expires_at_epoch <= tonumber(ARGV[2]) then
  redis.call("DEL", KEYS[1])
  return nil
end
local payload = redis.call("HGET", KEYS[1], "payload")
redis.call("DEL", KEYS[1])
return payload
"""
    _RESERVE_JOB_SCRIPT = """
local existing = redis.call("GET", KEYS[1])
if not existing then
  redis.call("SET", KEYS[1], ARGV[1], "EX", tonumber(ARGV[2]))
  return nil
end

local decoded = cjson.decode(existing)
local status = decoded["status"]
if status == "completed" then
  return existing
end
if status == "queued" or status == "running" then
  local active_epoch_us = tonumber(decoded["heartbeat_at_epoch_us"] or decoded["started_at_epoch_us"] or "0")
  if active_epoch_us >= tonumber(ARGV[3]) then
    return existing
  end
end

redis.call("SET", KEYS[1], ARGV[1], "EX", tonumber(ARGV[2]))
return nil
"""
    _UPDATE_JOB_IF_CURRENT_SCRIPT = """
-- update-if-current-job-id
local existing = redis.call("GET", KEYS[1])
if existing then
  local decoded = cjson.decode(existing)
  if decoded["id"] ~= ARGV[2] then
    return 0
  end
  local replacement = cjson.decode(ARGV[1])
  local existing_status = decoded["status"]
  local replacement_status = replacement["status"]
  if (existing_status == "completed" or existing_status == "failed") and
     replacement_status ~= "completed" and replacement_status ~= "failed" then
    return 0
  end
end
redis.call("SET", KEYS[1], ARGV[1], "EX", tonumber(ARGV[3]))
return 1
"""

    def __init__(self, redis_client: Any) -> None:
        self._redis = redis_client
        self._redis_is_async = _is_async_redis_client(redis_client)

    @classmethod
    def from_env(cls) -> _RedisAcquisitionStore:
        from redis import Redis

        redis_url = os.getenv("DATA_ACQUISITION_REDIS_URL")
        if redis_url:
            return cls(Redis.from_url(redis_url, decode_responses=True))

        redis_url = os.getenv("REDIS_URL") or get_settings().redis_url
        return cls(Redis.from_url(redis_url, decode_responses=True))

    async def save_preflight_token(
        self,
        key: tuple[str, str],
        record: _SubmitTokenRecord,
        _now: datetime,
    ) -> None:
        redis_key = self._token_key(*key)
        payload = _serialize_token_record(record)
        await self._save_token(redis_key, payload, record.token_hash, record.expires_at)

    async def consume_preflight_token(
        self,
        *,
        user_id: str,
        idempotency_key: str,
        token_hash: str,
        now: datetime,
    ) -> _SubmitTokenRecord | None:
        redis_key = self._token_key(user_id, idempotency_key)
        payload = await self._call_redis(
            "eval",
            self._CONSUME_TOKEN_SCRIPT,
            1,
            redis_key,
            token_hash,
            str(int(now.timestamp())),
        )
        if not payload:
            return None
        record = _deserialize_token_record(str(payload))
        if record.expires_at <= now:
            return None
        record.consumed = True
        return record

    async def get_preflight_token(
        self,
        *,
        user_id: str,
        idempotency_key: str,
        token_hash: str,
        now: datetime,
    ) -> _SubmitTokenRecord | None:
        redis_key = self._token_key(user_id, idempotency_key)
        payload = await self._get_token_payload(redis_key, token_hash, now)
        if payload is None:
            return None
        record = _deserialize_token_record(payload)
        if record.expires_at <= now:
            return None
        return record

    async def get_reusable_job(
        self,
        idempotency_key: str,
        now: datetime,
    ) -> DataAcquisitionJobDTO | None:
        payload = await self._call_redis("get", self._job_key(idempotency_key))
        if payload is None:
            return None
        job = DataAcquisitionJobDTO.model_validate_json(str(payload))
        return job if _is_reusable_acquisition_job(job, now) else None

    async def reserve_job(
        self,
        job: DataAcquisitionJobDTO,
        _now: datetime,
    ) -> DataAcquisitionJobDTO | None:
        redis_key = self._job_key(job.idempotency_key)
        payload = _serialize_acquisition_job(job)
        existing = await self._call_redis(
            "eval",
            self._RESERVE_JOB_SCRIPT,
            1,
            redis_key,
            payload,
            str(_ACQUISITION_JOB_RETENTION_SECONDS),
            str(
                _datetime_epoch_us(_now - timedelta(seconds=_ACQUISITION_ACTIVE_JOB_STALE_SECONDS))
            ),
        )
        if existing is None:
            return None
        existing_job = DataAcquisitionJobDTO.model_validate_json(str(existing))
        return existing_job if _is_reusable_acquisition_job(existing_job, _now) else None

    async def update_job(self, job: DataAcquisitionJobDTO, _now: datetime) -> None:
        await self._call_redis(
            "eval",
            self._UPDATE_JOB_IF_CURRENT_SCRIPT,
            1,
            self._job_key(job.idempotency_key),
            _serialize_acquisition_job(job),
            job.id,
            str(_ACQUISITION_JOB_RETENTION_SECONDS),
        )

    async def sweep(self, _now: datetime) -> None:
        return None

    async def _call_redis(self, method_name: str, *args: Any, **kwargs: Any) -> Any:
        method = getattr(self._redis, method_name)
        if not self._redis_is_async:
            return await asyncio.to_thread(method, *args, **kwargs)

        result = method(*args, **kwargs)
        if isawaitable(result):
            return await result
        return result

    async def _save_token(
        self,
        redis_key: str,
        payload: str,
        token_hash: str,
        expires_at: datetime,
    ) -> None:
        if not self._redis_is_async:
            await asyncio.to_thread(
                self._save_token_sync,
                redis_key,
                payload,
                token_hash,
                expires_at,
            )
            return

        pipe = self._redis.pipeline()
        pipe.hset(
            redis_key,
            mapping={
                "payload": payload,
                "token_hash": token_hash,
                "expires_at_epoch": str(int(expires_at.timestamp())),
            },
        )
        pipe.expire(redis_key, _ACQUISITION_TOKEN_TTL_SECONDS)
        result = pipe.execute()
        if isawaitable(result):
            await result

    async def _get_token_payload(
        self,
        redis_key: str,
        token_hash: str,
        now: datetime,
    ) -> str | None:
        if not self._redis_is_async:
            return await asyncio.to_thread(
                self._get_token_payload_sync,
                redis_key,
                token_hash,
                now,
            )

        stored_hash = await self._call_redis("hget", redis_key, "token_hash")
        if stored_hash is None or not hmac.compare_digest(str(stored_hash), token_hash):
            return None
        expires_at_epoch = int(
            str(await self._call_redis("hget", redis_key, "expires_at_epoch") or "0")
        )
        if expires_at_epoch <= int(now.timestamp()):
            await self._call_redis("delete", redis_key)
            return None
        payload = await self._call_redis("hget", redis_key, "payload")
        return str(payload) if payload is not None else None

    def _save_token_sync(
        self,
        redis_key: str,
        payload: str,
        token_hash: str,
        expires_at: datetime,
    ) -> None:
        pipe = self._redis.pipeline()
        pipe.hset(
            redis_key,
            mapping={
                "payload": payload,
                "token_hash": token_hash,
                "expires_at_epoch": str(int(expires_at.timestamp())),
            },
        )
        pipe.expire(redis_key, _ACQUISITION_TOKEN_TTL_SECONDS)
        pipe.execute()

    def _get_token_payload_sync(
        self,
        redis_key: str,
        token_hash: str,
        now: datetime,
    ) -> str | None:
        stored_hash = self._redis.hget(redis_key, "token_hash")
        if stored_hash is None or not hmac.compare_digest(str(stored_hash), token_hash):
            return None
        expires_at_epoch = int(str(self._redis.hget(redis_key, "expires_at_epoch") or "0"))
        if expires_at_epoch <= int(now.timestamp()):
            self._redis.delete(redis_key)
            return None
        payload = self._redis.hget(redis_key, "payload")
        return str(payload) if payload is not None else None

    @staticmethod
    def _token_key(user_id: str, idempotency_key: str) -> str:
        return f"{_ACQUISITION_REDIS_PREFIX}:token:{user_id}:{idempotency_key}"

    @staticmethod
    def _job_key(idempotency_key: str) -> str:
        return f"{_ACQUISITION_REDIS_PREFIX}:job:{idempotency_key}"


class _AcquisitionExecutionError(RuntimeError):
    def __init__(self, message: str, logs: list[str]) -> None:
        super().__init__(message)
        self.logs = logs


class _AcquisitionExecutor(Protocol):
    async def run(
        self,
        preflight: DataAcquisitionPreflightDTO,
    ) -> tuple[list[str], list[str], list[str]]:
        """Execute an acquisition and return manifests, validation output, and logs."""


class _ScriptBackedAcquisitionExecutor:
    def __init__(self, data_manifest_service: DataManifestService) -> None:
        self._data_manifest_service = data_manifest_service

    async def run(
        self,
        preflight: DataAcquisitionPreflightDTO,
    ) -> tuple[list[str], list[str], list[str]]:
        if preflight.dry_run:
            return (
                [],
                ["preflight_passed", "dry_run_completed_without_external_fetch"],
                ["dry_run_completed", f"adapter={_adapter_for_dataset(preflight.dataset)}"],
            )

        command = await asyncio.to_thread(_build_acquisition_command, preflight)
        proc = await asyncio.create_subprocess_exec(
            *command,
            cwd=_REPO_ROOT,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await proc.communicate()
        except asyncio.CancelledError:
            with suppress(ProcessLookupError):
                proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except TimeoutError:
                with suppress(ProcessLookupError):
                    proc.kill()
                await proc.wait()
            raise
        logs = [
            f"command={' '.join(_truncate_command_for_log(command))}",
            *_decode_process_lines(stdout, prefix="stdout"),
            *_decode_process_lines(stderr, prefix="stderr"),
        ]
        if proc.returncode != 0:
            raise _AcquisitionExecutionError(
                f"Acquisition adapter failed with exit code {proc.returncode}",
                logs,
            )

        manifest_ids = self._manifest_ids_from_stdout(preflight.dataset, stdout)
        return (
            manifest_ids or self._produced_manifest_ids(preflight.dataset),
            ["preflight_passed", "adapter_completed", "manifest_validation_pending"],
            logs,
        )

    @staticmethod
    def _manifest_ids_from_stdout(dataset: str, stdout: bytes) -> list[str]:
        for line in stdout.decode(errors="replace").splitlines():
            if not line.startswith(_MANIFEST_OUTPUT_PREFIX):
                continue
            try:
                payload = json.loads(line.removeprefix(_MANIFEST_OUTPUT_PREFIX))
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict) or str(payload.get("dataset", "")) != dataset:
                continue
            manifest_id = str(payload.get("manifest_id") or "").strip()
            if manifest_id:
                return [manifest_id]
            try:
                version = int(payload["manifest_version"])
            except (KeyError, TypeError, ValueError):
                continue
            checksum = str(payload.get("checksum") or "").strip()
            if checksum:
                return [f"{dataset}@v{version}:{checksum}"]
        return []

    def _produced_manifest_ids(self, dataset: str) -> list[str]:
        summary = self._data_manifest_service.get_manifest_summary(dataset)
        if summary is None:
            return []
        return [
            summary.manifest_id
            or f"{summary.dataset}@v{summary.manifest_version}:{summary.manifest_checksum}"
        ]


def _serialize_token_record(record: _SubmitTokenRecord) -> str:
    return json.dumps(
        {
            "user_id": record.user_id,
            "token_hash": record.token_hash,
            "expires_at": record.expires_at.isoformat(),
            "preflight": record.preflight.model_copy(update={"submit_token": ""}).model_dump(
                mode="json"
            ),
            "reason": record.reason,
            "consumed": record.consumed,
        },
        sort_keys=True,
    )


def _deserialize_token_record(payload: str) -> _SubmitTokenRecord:
    data = json.loads(payload)
    return _SubmitTokenRecord(
        user_id=str(data["user_id"]),
        token_hash=str(data["token_hash"]),
        expires_at=datetime.fromisoformat(str(data["expires_at"])),
        preflight=DataAcquisitionPreflightDTO.model_validate(data["preflight"]),
        reason=str(data["reason"]),
        consumed=bool(data.get("consumed", False)),
    )


def _adapter_for_dataset(dataset: str) -> str:
    return (
        "script:scripts/data/alpaca_sip_sync.py"
        if dataset == ALPACA_SIP_DAILY_DATASET
        else "script:scripts/data/alpaca_corp_actions_sync.py"
    )


def _build_acquisition_command(preflight: DataAcquisitionPreflightDTO) -> list[str]:
    if preflight.dataset == ALPACA_SIP_DAILY_DATASET:
        command = [
            sys.executable,
            "scripts/data/alpaca_sip_sync.py",
            "full-sync",
            "--start-year",
            str(preflight.start_date.year),
            "--end-year",
            str(preflight.end_date.year),
            "--feed",
            preflight.source_feed,
            "--adjustment",
            preflight.adjustment_mode or "raw",
        ]
        source_type, source_value = _split_symbol_source(preflight.symbol_source)
        if source_type == "file":
            command.extend(["--symbols", ",".join(_read_symbol_source_file(source_value))])
        elif source_type == "symbols":
            command.extend(["--symbols", source_value])
        else:
            raise ValueError(f"Unsupported daily symbol source: {source_type}")
        return command

    command = [
        sys.executable,
        "scripts/data/alpaca_corp_actions_sync.py",
        "full-sync",
        "--start-date",
        preflight.start_date.isoformat(),
        "--end-date",
        preflight.end_date.isoformat(),
    ]
    source_type, source_value = _split_symbol_source(preflight.symbol_source)
    if source_type == "ids":
        command.extend(["--ids", source_value])
    elif source_type == "file":
        command.extend(["--symbols", ",".join(_read_symbol_source_file(source_value))])
    elif source_type == "symbols":
        command.extend(["--symbols", source_value])
    else:
        raise ValueError(f"Unsupported corporate-actions symbol source: {source_type}")
    return command


def _split_symbol_source(symbol_source: str) -> tuple[str, str]:
    if ":" in symbol_source:
        prefix, value = symbol_source.split(":", 1)
        return prefix.lower(), value
    return "symbols", symbol_source


def _read_symbol_source_file(value: str) -> list[str]:
    path_value, expected_hash = _split_symbol_file_fingerprint(value)
    path = (_REPO_ROOT / path_value).resolve()
    symbol_source_base = (_REPO_ROOT / "data" / "symbols").resolve()
    if not path.is_relative_to(symbol_source_base):
        raise ValueError("File symbol sources must stay under data/symbols/")
    if not path.is_file():
        raise ValueError("File symbol source does not exist")
    symbols = [
        line.strip().upper()
        for line in path.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    if not symbols:
        raise ValueError("File symbol source is empty")
    if any(not _SYMBOL_TOKEN_PATTERN.fullmatch(symbol) for symbol in symbols):
        raise ValueError("File symbol source contains unsupported symbols")
    canonical_symbols = sorted(set(symbols))
    if len(canonical_symbols) > _ACQUISITION_MAX_SYMBOLS_FROM_FILE:
        raise ValueError("File symbol source contains too many symbols")
    actual_hash = _hash_symbol_list(canonical_symbols)
    if expected_hash is not None and not hmac.compare_digest(actual_hash, expected_hash):
        raise ValueError("File symbol source changed after preflight")
    return canonical_symbols


def _fingerprinted_symbol_file_source(value: str) -> str:
    path_value, _expected_hash = _split_symbol_file_fingerprint(value)
    symbols = _read_symbol_source_file(value)
    return f"{path_value}{_SYMBOL_FILE_FINGERPRINT_SEPARATOR}{_hash_symbol_list(symbols)}"


def _split_symbol_file_fingerprint(value: str) -> tuple[str, str | None]:
    if _SYMBOL_FILE_FINGERPRINT_SEPARATOR not in value:
        return value, None
    path_value, fingerprint = value.split(_SYMBOL_FILE_FINGERPRINT_SEPARATOR, 1)
    if not re.fullmatch(r"[0-9a-f]{16}", fingerprint):
        raise ValueError("File symbol source fingerprint is unsupported")
    return path_value, fingerprint


def _hash_symbol_list(symbols: Sequence[str]) -> str:
    # Short fingerprint is for idempotency/change detection, not secret validation.
    payload = "\n".join(symbols).encode()
    return hashlib.sha256(payload).hexdigest()[:16]


def _is_reusable_acquisition_job(job: DataAcquisitionJobDTO, _now: datetime) -> bool:
    if job.status == "completed":
        return True
    if job.status not in _ACQUISITION_REUSABLE_JOB_STATUSES:
        return False
    active_at = job.heartbeat_at or job.started_at
    if active_at is None:
        return False
    return (_now - active_at.astimezone(UTC)) <= timedelta(
        seconds=_ACQUISITION_ACTIVE_JOB_STALE_SECONDS
    )


def _retention_anchor_for_acquisition_job(job: DataAcquisitionJobDTO, now: datetime) -> datetime:
    if job.status in {"queued", "running"}:
        return (job.heartbeat_at or job.started_at or now).astimezone(UTC)
    return (job.completed_at or job.started_at or now).astimezone(UTC)


def _acquisition_store_backend_name(store: _AcquisitionStore) -> str:
    if isinstance(store, _InMemoryAcquisitionStore):
        return "in_memory"
    if isinstance(store, _RedisAcquisitionStore):
        return "redis"
    return type(store).__name__


def _background_acquisition_semaphore() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    semaphore = _BACKGROUND_ACQUISITION_SEMAPHORES.get(loop)
    if semaphore is None:
        semaphore = asyncio.Semaphore(_ACQUISITION_BACKGROUND_CONCURRENCY)
        _BACKGROUND_ACQUISITION_SEMAPHORES[loop] = semaphore
    return semaphore


def _datetime_epoch_us(value: datetime) -> int:
    return int(value.astimezone(UTC).timestamp() * 1_000_000)


def _serialize_acquisition_job(job: DataAcquisitionJobDTO) -> str:
    payload = job.model_dump(mode="json")
    if job.started_at is not None:
        payload["started_at_epoch_us"] = _datetime_epoch_us(job.started_at)
    if job.heartbeat_at is not None:
        payload["heartbeat_at_epoch_us"] = _datetime_epoch_us(job.heartbeat_at)
    if job.completed_at is not None:
        payload["completed_at_epoch_us"] = _datetime_epoch_us(job.completed_at)
    return json.dumps(payload, sort_keys=True)


def _sanitize_log_message(value: str) -> str:
    sanitized = value.strip().replace("\n", " ")
    sanitized = _SENSITIVE_ASSIGNMENT_PATTERN.sub(r"\1\2<redacted>", sanitized)
    return _SENSITIVE_BEARER_PATTERN.sub(r"\1<redacted>", sanitized)


def _truncate_command_for_log(command: Sequence[str]) -> list[str]:
    sanitized_parts = [_sanitize_log_message(part) for part in command]
    return [part if len(part) <= 160 else f"{part[:157]}..." for part in sanitized_parts]


def _sanitize_log_lines(lines: Sequence[str]) -> list[str]:
    return [_sanitize_log_message(line)[:500] for line in lines]


def _format_exception_for_log(exc: BaseException) -> str:
    message = _sanitize_log_message(str(exc))
    detail = f"{type(exc).__name__}:{message}" if message else type(exc).__name__
    return detail if len(detail) <= 320 else f"{detail[:317]}..."


def _decode_process_lines(payload: bytes, *, prefix: str) -> list[str]:
    text = payload.decode(errors="replace").strip()
    if not text:
        return []
    return [f"{prefix}:{_sanitize_log_message(line)[:500]}" for line in text.splitlines()[-20:]]


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

    def __init__(
        self,
        rate_limiter: RateLimiter | None = None,
        data_manifest_service: DataManifestService | None = None,
        acquisition_state: _AcquisitionState | None = None,
        acquisition_store: _AcquisitionStore | None = None,
        acquisition_executor: _AcquisitionExecutor | None = None,
    ) -> None:
        self._rate_limiter = rate_limiter or get_rate_limiter()
        self._data_manifest_service = data_manifest_service or DataManifestService()
        selected_store: _AcquisitionStore
        if acquisition_store is not None:
            selected_store = acquisition_store
            self._acquisition_store = selected_store
            self._acquisition_lock = asyncio.Lock()
            self._preflight_tokens: dict[tuple[str, str], _SubmitTokenRecord] = {}
            self._acquisition_jobs: dict[str, DataAcquisitionJobDTO] = {}
        else:
            if acquisition_state is not None:
                selected_store = _InMemoryAcquisitionStore(acquisition_state)
            else:
                selected_store = _RedisAcquisitionStore.from_env()
            self._acquisition_store = selected_store
            if isinstance(selected_store, _InMemoryAcquisitionStore):
                self._acquisition_lock = selected_store.lock
                self._preflight_tokens = selected_store.preflight_tokens
                self._acquisition_jobs = selected_store.acquisition_jobs
            else:
                self._acquisition_lock = asyncio.Lock()
                self._preflight_tokens = {}
                self._acquisition_jobs = {}
        logger.info(
            "data_acquisition_store_selected",
            extra={"backend": _acquisition_store_backend_name(self._acquisition_store)},
        )
        self._acquisition_executor = acquisition_executor or _ScriptBackedAcquisitionExecutor(
            self._data_manifest_service
        )
        self._background_acquisition_tasks: set[asyncio.Task[None]] = set()

    async def get_sync_status(self, user: Any) -> list[SyncStatusDTO]:
        """Get sync status for datasets user has access to.

        Permission: VIEW_DATA_SYNC + dataset-level access (filtered)
        Returns: List of SyncStatusDTO with dataset, last_sync, row_count, status
        Filtering: Only datasets matching user's DatasetPermission set
        """
        self._require_permission(user, Permission.VIEW_DATA_SYNC)

        now = datetime.now(UTC)
        mock = await asyncio.to_thread(self._build_sync_statuses, now)
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

    async def preflight_acquisition(
        self,
        user: Any,
        request: DataAcquisitionRequestDTO,
    ) -> DataAcquisitionPreflightDTO:
        """Validate a UI acquisition request and mint a one-use submit token."""
        self._require_permission(user, Permission.TRIGGER_DATA_SYNC)
        self._require_dataset_access(user, request.dataset)
        symbol_source = await asyncio.to_thread(self._validate_acquisition_request, request)
        await self._enforce_rate_limit(
            user,
            action="preflight_data_acquisition",
            max_requests=10,
            window=60,
        )

        user_id = get_user_id(user)
        reason = request.reason.strip()
        idempotency_key = self._build_acquisition_idempotency_key(
            request.model_copy(
                update={
                    "reason": reason,
                    "symbol_source": symbol_source,
                }
            )
        )
        now = datetime.now(UTC)
        submit_token = secrets.token_urlsafe(32)
        expires_at = now + timedelta(seconds=_ACQUISITION_TOKEN_TTL_SECONDS)
        preflight = self._build_acquisition_preflight(
            request,
            idempotency_key=idempotency_key,
            submit_token=submit_token,
            expires_at=expires_at,
            reason=reason,
            symbol_source=symbol_source,
        )
        record = _SubmitTokenRecord(
            user_id=user_id,
            token_hash=self._hash_submit_token(submit_token),
            expires_at=expires_at,
            preflight=preflight.model_copy(update={"submit_token": ""}),
            reason=reason,
        )
        await self._acquisition_store.save_preflight_token((user_id, idempotency_key), record, now)
        return preflight

    async def submit_acquisition(
        self,
        user: Any,
        submission: DataAcquisitionSubmitDTO,
    ) -> DataAcquisitionJobDTO:
        """Submit an acquisition job using a current one-use preflight token."""
        self._require_permission(user, Permission.TRIGGER_DATA_SYNC)

        user_id = get_user_id(user)
        now = datetime.now(UTC)
        token_hash = self._hash_submit_token(submission.submit_token)
        token_record = await self._acquisition_store.get_preflight_token(
            user_id=user_id,
            idempotency_key=submission.idempotency_key,
            token_hash=token_hash,
            now=now,
        )
        if token_record is None:
            raise PreflightRequired("Current acquisition preflight required")
        self._require_dataset_access(user, token_record.preflight.dataset)

        existing = await self._acquisition_store.get_reusable_job(
            submission.idempotency_key,
            now,
        )
        if existing is not None:
            record = await self._acquisition_store.consume_preflight_token(
                user_id=user_id,
                idempotency_key=submission.idempotency_key,
                token_hash=token_hash,
                now=now,
            )
            if record is None:
                raise PreflightRequired("Current acquisition preflight required")
            duplicate_job = self._duplicate_job_view(existing)
            self._log_acquisition_submission(
                job=duplicate_job,
                record=record,
                user_id=user_id,
                deduplicated=True,
            )
            return duplicate_job

        try:
            await self._enforce_rate_limit(
                user, action="trigger_data_sync", max_requests=1, window=60
            )
        except RateLimitExceeded:
            preview_job = self._build_acquisition_job(token_record.preflight, now)
            rate_limited_job = preview_job.model_copy(
                update={
                    "status": "failed",
                    "completed_at": datetime.now(UTC),
                    "validation_output": [
                        *preview_job.validation_output,
                        "rate_limited_before_start",
                    ],
                    "logs": [*preview_job.logs, "job_not_started_rate_limited"],
                }
            )
            self._log_acquisition_submission_blocked(
                job=rate_limited_job,
                record=token_record,
                user_id=user_id,
                block_reason="rate_limited",
            )
            raise

        record = await self._acquisition_store.consume_preflight_token(
            user_id=user_id,
            idempotency_key=submission.idempotency_key,
            token_hash=token_hash,
            now=now,
        )
        if record is None:
            raise PreflightRequired("Current acquisition preflight required")

        job = self._build_acquisition_job(record.preflight, now)
        reserved_existing = await self._acquisition_store.reserve_job(job, now)
        if reserved_existing is not None:
            duplicate_job = self._duplicate_job_view(reserved_existing)
            self._log_acquisition_submission(
                job=duplicate_job,
                record=record,
                user_id=user_id,
                deduplicated=True,
            )
            return duplicate_job

        started_job = await self._start_acquisition_execution(job, record.preflight)
        self._log_acquisition_submission(
            job=started_job,
            record=record,
            user_id=user_id,
            deduplicated=False,
        )
        return started_job

    async def close(self, *, cancel_running: bool = False) -> None:
        """Wait for background acquisition tasks, optionally cancelling active work."""
        tasks = tuple(self._background_acquisition_tasks)
        if not tasks:
            return
        if cancel_running:
            for task in tasks:
                task.cancel()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError):
                logger.warning(
                    "background_acquisition_task_failed",
                    extra={"error": str(result), "error_type": type(result).__name__},
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

    def _mock_or_manifest_status(self, dataset: str, now: datetime) -> SyncStatusDTO:
        if dataset != "alpaca_sip":
            return SyncStatusDTO(
                dataset=dataset,
                last_sync=now,
                row_count=1000,
                validation_status="ok",
                schema_version="v1",
            )

        return self._data_manifest_service.get_alpaca_sip_summary().to_sync_status()

    def _build_sync_statuses(self, now: datetime) -> list[SyncStatusDTO]:
        return [self._mock_or_manifest_status(name, now) for name in _SUPPORTED_DATASETS]

    def _validate_acquisition_request(self, request: DataAcquisitionRequestDTO) -> str:
        if request.dataset not in _SUPPORTED_ACQUISITION_DATASETS:
            raise ValueError(f"Unsupported acquisition dataset: {request.dataset}")
        if request.mode != "backfill":
            raise ValueError("Only backfill acquisition mode is currently supported")
        if request.start_date > request.end_date:
            raise ValueError("Start date must be before or equal to end date")
        self._validate_acquisition_date_bounds(request)
        normalized_start, normalized_end = self._normalized_acquisition_dates(request)
        if (normalized_end - normalized_start).days > _ACQUISITION_MAX_DATE_RANGE_DAYS:
            raise ValueError("Date range exceeds the maximum acquisition window")
        if not request.symbol_source.strip():
            raise ValueError("Symbol source is required")
        symbol_source = self._canonical_symbol_source(request)
        if request.dataset == ALPACA_SIP_DAILY_DATASET and request.adjustment_mode != "raw":
            raise ValueError("Daily Alpaca SIP acquisition requires adjustment_mode=raw")
        if (
            request.dataset == ALPACA_SIP_CORP_ACTIONS_DATASET
            and request.adjustment_mode is not None
        ):
            raise ValueError("Corporate actions acquisition does not accept adjustment metadata")
        if not request.reason.strip():
            raise ValueError("Reason is required for audit logging")
        return symbol_source

    def _build_acquisition_idempotency_key(
        self,
        request: DataAcquisitionRequestDTO,
    ) -> str:
        start_date, end_date = self._normalized_acquisition_dates(request)
        payload = {
            "dataset": request.dataset,
            "provider_id": "alpaca_sip",
            "source_feed": "sip",
            "adjustment_mode": request.adjustment_mode,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "symbol_source": request.symbol_source,
            "mode": request.mode,
            "dry_run": request.dry_run,
        }
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        return f"acq_{digest[:32]}"

    def _build_acquisition_preflight(
        self,
        request: DataAcquisitionRequestDTO,
        *,
        idempotency_key: str,
        submit_token: str,
        expires_at: datetime,
        reason: str,
        symbol_source: str,
    ) -> DataAcquisitionPreflightDTO:
        is_daily = request.dataset == ALPACA_SIP_DAILY_DATASET
        effective_start, effective_end = self._normalized_acquisition_dates(request)
        supported_semantics = (
            [
                "alpaca_sip_daily_sync_uses_calendar_year_partitions",
                f"requested_date_range={request.start_date.isoformat()}:{request.end_date.isoformat()}",
                f"effective_date_range={effective_start.isoformat()}:{effective_end.isoformat()}",
                "canonical_storage=raw_ohlc",
                "adj_close_and_ret_remain_unavailable",
            ]
            if is_daily
            else [
                "alpaca_sip_corp_actions_sync_uses_date_range_scope",
                "corporate_actions_feed_has_no_price_adjustment_mode",
            ]
        )
        warnings = (
            [
                "daily_bar_requests_are_expanded_to_supported_year_based_sync_semantics",
                "raw_sip_returns_unavailable",
            ]
            if is_daily
            else []
        )
        logs = [
            "preflight_validated",
            "submit_token_active_until_expiry",
            "submit_token_value_hidden_from_ui_logs",
            f"reason_recorded:{reason}",
        ]
        return DataAcquisitionPreflightDTO(
            dataset=request.dataset,
            start_date=effective_start,
            end_date=effective_end,
            requested_start_date=request.start_date,
            requested_end_date=request.end_date,
            symbol_source=symbol_source,
            mode=request.mode,
            dry_run=request.dry_run,
            provider_id="alpaca_sip",
            source_feed="sip",
            canonical_storage_mode="raw" if is_daily else "not_price_bars",
            read_time_adjustment_mode="unavailable" if is_daily else None,
            adjustment_mode=request.adjustment_mode,
            idempotency_key=idempotency_key,
            submit_token=submit_token,
            submit_token_expires_at=expires_at,
            supported_semantics=supported_semantics,
            warnings=warnings,
            logs=logs,
        )

    def _build_acquisition_job(
        self,
        preflight: DataAcquisitionPreflightDTO,
        now: datetime,
    ) -> DataAcquisitionJobDTO:
        adapter = _adapter_for_dataset(preflight.dataset)
        return DataAcquisitionJobDTO(
            id=str(uuid4()),
            dataset=preflight.dataset,
            status="queued",
            idempotency_key=preflight.idempotency_key,
            mode=preflight.mode,
            dry_run=preflight.dry_run,
            provider_id=preflight.provider_id,
            source_feed=preflight.source_feed,
            canonical_storage_mode=preflight.canonical_storage_mode,
            read_time_adjustment_mode=preflight.read_time_adjustment_mode,
            adjustment_mode=preflight.adjustment_mode,
            started_at=now,
            heartbeat_at=now,
            submit_token_status="consumed",
            adapter=adapter,
            produced_manifest_ids=[],
            validation_output=["preflight_passed", "manifest_validation_pending"],
            logs=[
                "job_queued",
                f"adapter={adapter}",
                "duplicate_submissions_reuse_idempotency_key",
            ],
        )

    @staticmethod
    def _duplicate_job_view(job: DataAcquisitionJobDTO) -> DataAcquisitionJobDTO:
        return job.model_copy(
            update={
                "logs": [*job.logs, "duplicate_submission_reused_existing_job"],
            }
        )

    async def _start_acquisition_execution(
        self,
        job: DataAcquisitionJobDTO,
        preflight: DataAcquisitionPreflightDTO,
    ) -> DataAcquisitionJobDTO:
        now = datetime.now(UTC)
        if preflight.dry_run:
            completed_job = await self._run_acquisition_job(job, preflight)
            await self._acquisition_store.update_job(completed_job, datetime.now(UTC))
            return completed_job

        running_job = job.model_copy(
            update={
                "status": "running",
                "heartbeat_at": now,
                "logs": [*job.logs, "job_started"],
            }
        )
        await self._acquisition_store.update_job(running_job, now)
        task = asyncio.create_task(self._run_and_store_acquisition_job(running_job, preflight))
        self._background_acquisition_tasks.add(task)
        task.add_done_callback(partial(self._observe_background_acquisition_task, job=running_job))
        return running_job

    def _observe_background_acquisition_task(
        self,
        task: asyncio.Task[None],
        *,
        job: DataAcquisitionJobDTO,
    ) -> None:
        self._background_acquisition_tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is None:
            return
        logger.warning(
            "background_acquisition_task_failed",
            extra={
                "job_id": job.id,
                "dataset": job.dataset,
                "error": _format_exception_for_log(exc),
                "error_type": type(exc).__name__,
            },
            exc_info=(type(exc), exc, exc.__traceback__),
        )

    async def _run_and_store_acquisition_job(
        self,
        job: DataAcquisitionJobDTO,
        preflight: DataAcquisitionPreflightDTO,
    ) -> None:
        heartbeat_task: asyncio.Task[None] | None = None
        try:
            heartbeat_task = asyncio.create_task(self._heartbeat_acquisition_job(job))
            async with _background_acquisition_semaphore():
                updated_job = await asyncio.wait_for(
                    self._run_acquisition_job(job, preflight),
                    timeout=_ACQUISITION_EXECUTION_TIMEOUT_SECONDS,
                )
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                with suppress(asyncio.CancelledError):
                    await heartbeat_task
            await self._acquisition_store.update_job(updated_job, datetime.now(UTC))
        except asyncio.CancelledError:
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                with suppress(asyncio.CancelledError):
                    await heartbeat_task
            cancelled_job = job.model_copy(
                update={
                    "status": "failed",
                    "completed_at": datetime.now(UTC),
                    "validation_output": [*job.validation_output, "acquisition_cancelled"],
                    "logs": [*job.logs, "job_cancelled_during_shutdown"],
                }
            )
            try:
                await self._acquisition_store.update_job(cancelled_job, datetime.now(UTC))
            except Exception:
                logger.exception(
                    "background_acquisition_cancel_status_update_failed",
                    extra={"job_id": job.id, "dataset": job.dataset},
                )
            raise
        except Exception as exc:
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                with suppress(asyncio.CancelledError):
                    await heartbeat_task
            failed_job = job.model_copy(
                update={
                    "status": "failed",
                    "completed_at": datetime.now(UTC),
                    "validation_output": [
                        *job.validation_output,
                        "background_acquisition_failed",
                        type(exc).__name__,
                    ],
                    "logs": [*job.logs, f"error={_format_exception_for_log(exc)}"],
                }
            )
            try:
                await self._acquisition_store.update_job(failed_job, datetime.now(UTC))
            except Exception:
                logger.exception(
                    "background_acquisition_failure_status_update_failed",
                    extra={"job_id": job.id, "dataset": job.dataset},
                )
            raise

    async def _heartbeat_acquisition_job(self, job: DataAcquisitionJobDTO) -> None:
        loop = asyncio.get_running_loop()
        next_heartbeat_at = loop.time() + _ACQUISITION_HEARTBEAT_INTERVAL_SECONDS
        while True:
            await asyncio.sleep(max(0.0, next_heartbeat_at - loop.time()))
            heartbeat_started_at = datetime.now(UTC)
            heartbeat_job = job.model_copy(update={"heartbeat_at": heartbeat_started_at})
            try:
                await self._acquisition_store.update_job(heartbeat_job, heartbeat_started_at)
            except Exception:
                logger.exception(
                    "background_acquisition_heartbeat_failed",
                    extra={"job_id": job.id, "dataset": job.dataset},
                )
            next_heartbeat_at += _ACQUISITION_HEARTBEAT_INTERVAL_SECONDS
            if next_heartbeat_at <= loop.time():
                next_heartbeat_at = loop.time() + _ACQUISITION_HEARTBEAT_INTERVAL_SECONDS

    async def _run_acquisition_job(
        self,
        job: DataAcquisitionJobDTO,
        preflight: DataAcquisitionPreflightDTO,
    ) -> DataAcquisitionJobDTO:
        try:
            (
                produced_manifest_ids,
                validation_output,
                execution_logs,
            ) = await self._acquisition_executor.run(preflight)
        except _AcquisitionExecutionError as exc:
            return job.model_copy(
                update={
                    "status": "failed",
                    "completed_at": datetime.now(UTC),
                    "validation_output": [
                        "preflight_passed",
                        "adapter_failed",
                        type(exc).__name__,
                    ],
                    "logs": [
                        *job.logs,
                        *_sanitize_log_lines(exc.logs),
                        f"error={_format_exception_for_log(exc)}",
                    ],
                }
            )
        except Exception as exc:
            return job.model_copy(
                update={
                    "status": "failed",
                    "completed_at": datetime.now(UTC),
                    "validation_output": [
                        "preflight_passed",
                        "adapter_failed",
                        type(exc).__name__,
                    ],
                    "logs": [*job.logs, f"error={_format_exception_for_log(exc)}"],
                }
            )

        return job.model_copy(
            update={
                "status": "completed",
                "completed_at": datetime.now(UTC),
                "produced_manifest_ids": produced_manifest_ids,
                "validation_output": validation_output,
                "logs": [*job.logs, *_sanitize_log_lines(execution_logs), "job_completed"],
            }
        )

    @staticmethod
    def _hash_submit_token(submit_token: str) -> str:
        return hashlib.sha256(submit_token.encode()).hexdigest()

    @staticmethod
    def _normalized_acquisition_dates(request: DataAcquisitionRequestDTO) -> tuple[date, date]:
        if request.dataset == ALPACA_SIP_DAILY_DATASET:
            # The daily sync adapter accepts year partitions, so requested in-year gaps
            # are validated as typed but executed against the containing calendar years.
            return (
                date(request.start_date.year, 1, 1),
                date(request.end_date.year, 12, 31),
            )
        return request.start_date, request.end_date

    @staticmethod
    def _validate_acquisition_date_bounds(request: DataAcquisitionRequestDTO) -> None:
        spec = get_provider_spec(ProviderType.ALPACA_SIP)
        history_start = spec.capabilities.history_start or date(2016, 1, 1)
        if request.start_date < history_start:
            raise ValueError("Alpaca SIP acquisition starts at " f"{history_start.isoformat()}")
        today = datetime.now(UTC).date()
        if request.end_date > today:
            raise ValueError("End date cannot be in the future")

    def _canonical_symbol_source(self, request: DataAcquisitionRequestDTO) -> str:
        symbol_source = request.symbol_source.strip()
        if len(symbol_source) > 256:
            raise ValueError("Symbol source is too long")
        if any(ord(char) < 32 for char in symbol_source):
            raise ValueError("Symbol source contains unsupported control characters")

        normalized = symbol_source.lower()
        if normalized == "all":
            if request.dataset == ALPACA_SIP_CORP_ACTIONS_DATASET:
                raise ValueError("Corporate actions acquisition requires symbols or ids")
            raise ValueError("Alpaca SIP acquisition requires explicit symbols or a source prefix")
        if normalized.startswith(_SYMBOL_SOURCE_PREFIXES):
            prefix, raw_value = symbol_source.split(":", 1)
            value = raw_value.strip()
            prefix = prefix.lower()
            if not value:
                raise ValueError("Symbol source prefix requires a value")
            if prefix == "ids":
                if request.dataset != ALPACA_SIP_CORP_ACTIONS_DATASET:
                    raise ValueError("Daily acquisition does not accept corporate-action ids")
                ids = [part.strip() for part in value.split(",") if part.strip()]
                if not ids or any(
                    not _SOURCE_VALUE_PATTERN.fullmatch(identifier)
                    or ".." in identifier
                    or identifier.startswith("/")
                    for identifier in ids
                ):
                    raise ValueError("Corporate-action id source is unsupported")
                return f"{prefix}:{','.join(sorted(set(ids)))}"
            if prefix == "universe":
                raise ValueError("Universe symbol sources are not executable yet")
            if prefix == "file":
                self._validate_symbol_file_source(value)
                return f"{prefix}:{_fingerprinted_symbol_file_source(value)}"
            if not _SOURCE_VALUE_PATTERN.fullmatch(value) or ".." in value or value.startswith("/"):
                raise ValueError("Symbol source prefix value is unsupported")
            return f"{prefix}:{value}"

        symbols = [part.strip().upper() for part in symbol_source.split(",")]
        if not symbols or any(not _SYMBOL_TOKEN_PATTERN.fullmatch(symbol) for symbol in symbols):
            raise ValueError("Symbol source must be all, a supported source prefix, or symbols")
        return ",".join(sorted(set(symbols)))

    @staticmethod
    def _validate_symbol_file_source(value: str) -> None:
        value, _fingerprint = _split_symbol_file_fingerprint(value)
        path = PurePosixPath(value)
        if (
            not value.startswith(_SYMBOL_FILE_PREFIX)
            or path.is_absolute()
            or any(part in {"", ".", ".."} for part in path.parts)
            or path.suffix.lower() not in _SYMBOL_FILE_EXTENSIONS
        ):
            raise ValueError(
                "File symbol sources must be relative CSV/TXT files under data/symbols/"
            )

    def _log_acquisition_submission(
        self,
        *,
        job: DataAcquisitionJobDTO,
        record: _SubmitTokenRecord,
        user_id: str,
        deduplicated: bool,
    ) -> None:
        logger.info(
            "acquisition_submitted",
            extra={
                "dataset": job.dataset,
                "job_id": job.id,
                "user_id": user_id,
                "reason": record.reason,
                "idempotency_key": job.idempotency_key,
                "deduplicated": deduplicated,
                "mode": job.mode,
                "dry_run": job.dry_run,
                "start_date": record.preflight.start_date.isoformat(),
                "end_date": record.preflight.end_date.isoformat(),
                "requested_start_date": (
                    record.preflight.requested_start_date or record.preflight.start_date
                ).isoformat(),
                "requested_end_date": (
                    record.preflight.requested_end_date or record.preflight.end_date
                ).isoformat(),
                "symbol_source": record.preflight.symbol_source,
                "adjustment_mode": record.preflight.adjustment_mode,
                "provider_id": job.provider_id,
                "source_feed": job.source_feed,
            },
        )

    def _log_acquisition_submission_blocked(
        self,
        *,
        job: DataAcquisitionJobDTO,
        record: _SubmitTokenRecord,
        user_id: str,
        block_reason: str,
    ) -> None:
        logger.info(
            "acquisition_submission_blocked",
            extra={
                "dataset": job.dataset,
                "job_id": job.id,
                "user_id": user_id,
                "reason": record.reason,
                "idempotency_key": job.idempotency_key,
                "block_reason": block_reason,
                "mode": job.mode,
                "dry_run": job.dry_run,
                "start_date": record.preflight.start_date.isoformat(),
                "end_date": record.preflight.end_date.isoformat(),
                "requested_start_date": (
                    record.preflight.requested_start_date or record.preflight.start_date
                ).isoformat(),
                "requested_end_date": (
                    record.preflight.requested_end_date or record.preflight.end_date
                ).isoformat(),
                "symbol_source": record.preflight.symbol_source,
                "adjustment_mode": record.preflight.adjustment_mode,
                "provider_id": job.provider_id,
                "source_feed": job.source_feed,
            },
        )


__all__ = ["DataSyncService", "PreflightRequired", "RateLimitExceeded"]
