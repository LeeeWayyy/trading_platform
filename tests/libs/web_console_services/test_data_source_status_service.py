"""Unit tests for DataSourceStatusService (P6T14/T14.2)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from unittest.mock import AsyncMock

import pytest
import redis.exceptions

from libs.platform.web_console_auth.permissions import Role
from libs.web_console_services import data_source_status_service as module
from libs.web_console_services.data_source_status_service import (
    _REFRESH_TIMEOUT_SECONDS,
    DataSourceStatusService,
)


@dataclass(frozen=True)
class DummyUser:
    user_id: str
    role: Role


class DummyRedis:
    def __init__(
        self,
        *,
        set_side_effect: Exception | None = None,
        eval_side_effect: Exception | None = None,
        set_results: list[bool] | None = None,
    ) -> None:
        self._set_side_effect = set_side_effect
        self._eval_side_effect = eval_side_effect
        self._set_results = list(set_results or [True])
        self.set_calls: list[tuple[str, str, int, bool]] = []
        self.eval_calls: list[tuple[str, int, str, str]] = []

    async def set(self, key: str, value: str, *, ex: int, nx: bool) -> bool:
        self.set_calls.append((key, value, ex, nx))
        if self._set_side_effect is not None:
            raise self._set_side_effect
        if self._set_results:
            return self._set_results.pop(0)
        return False

    async def eval(self, script: str, numkeys: int, key: str, value: str) -> int:
        self.eval_calls.append((script, numkeys, key, value))
        if self._eval_side_effect is not None:
            raise self._eval_side_effect
        return 1


@pytest.fixture()
def admin_user() -> DummyUser:
    return DummyUser(user_id="admin-1", role=Role.ADMIN)


@pytest.fixture()
def operator_user() -> DummyUser:
    return DummyUser(user_id="operator-1", role=Role.OPERATOR)


@pytest.fixture()
def viewer_user() -> DummyUser:
    return DummyUser(user_id="viewer-1", role=Role.VIEWER)


@pytest.fixture()
def researcher_user() -> DummyUser:
    return DummyUser(user_id="researcher-1", role=Role.RESEARCHER)


@pytest.mark.asyncio()
async def test_get_all_sources_returns_all_for_admin(admin_user: DummyUser) -> None:
    service = DataSourceStatusService()

    sources = await service.get_all_sources(admin_user)

    assert len(sources) == 5
    assert {s.name for s in sources} == {
        "crsp",
        "yfinance",
        "compustat",
        "fama_french",
        "taq",
    }


@pytest.mark.asyncio()
async def test_get_all_sources_permission_denied(researcher_user: DummyUser) -> None:
    service = DataSourceStatusService()

    with pytest.raises(PermissionError, match="view_data_sync"):
        await service.get_all_sources(researcher_user)


@pytest.mark.asyncio()
async def test_get_all_sources_filters_by_entitlement(viewer_user: DummyUser) -> None:
    service = DataSourceStatusService()

    sources = await service.get_all_sources(viewer_user)

    assert {s.name for s in sources} == {"fama_french", "yfinance"}


@pytest.mark.asyncio()
async def test_public_source_visible_with_view_permission(viewer_user: DummyUser) -> None:
    service = DataSourceStatusService()

    sources = await service.get_all_sources(viewer_user)

    yfinance = next(s for s in sources if s.name == "yfinance")
    assert yfinance.dataset_key is None


@pytest.mark.asyncio()
async def test_refresh_source_success(operator_user: DummyUser) -> None:
    service = DataSourceStatusService(redis_client_factory=None)

    refreshed = await service.refresh_source(operator_user, "crsp")

    assert refreshed.name == "crsp"
    assert refreshed.age_seconds == 0.0


@pytest.mark.asyncio()
async def test_refresh_source_requires_trigger_permission(viewer_user: DummyUser) -> None:
    service = DataSourceStatusService()

    with pytest.raises(PermissionError, match="trigger_data_sync"):
        await service.refresh_source(viewer_user, "crsp")


@pytest.mark.asyncio()
async def test_refresh_source_unknown_name_returns_uniform_error(
    operator_user: DummyUser,
) -> None:
    service = DataSourceStatusService()

    with pytest.raises(PermissionError, match="Source not available"):
        await service.refresh_source(operator_user, "does_not_exist")


@pytest.mark.asyncio()
async def test_refresh_source_empty_name_rejected(operator_user: DummyUser) -> None:
    service = DataSourceStatusService()

    with pytest.raises(PermissionError, match="Source not available"):
        await service.refresh_source(operator_user, "")


@pytest.mark.asyncio()
async def test_refresh_source_unauthorized_and_unknown_identical_error(
    operator_user: DummyUser,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = DataSourceStatusService()
    original_has_dataset_permission = module.has_dataset_permission

    def deny_taq(user: object, dataset: str) -> bool:
        if dataset == "taq":
            return False
        return original_has_dataset_permission(user, dataset)

    monkeypatch.setattr(module, "has_dataset_permission", deny_taq)

    with pytest.raises(PermissionError) as unauthorized_error:
        await service.refresh_source(operator_user, "taq")

    with pytest.raises(PermissionError) as unknown_error:
        await service.refresh_source(operator_user, "unknown_source")

    assert str(unauthorized_error.value) == "Source not available"
    assert str(unknown_error.value) == "Source not available"


@pytest.mark.asyncio()
async def test_mock_data_fields_types(admin_user: DummyUser) -> None:
    service = DataSourceStatusService()

    sources = await service.get_all_sources(admin_user)

    sample = sources[0]
    assert isinstance(sample.name, str)
    assert isinstance(sample.provider_type, str)
    assert sample.status in {"ok", "stale", "error", "unknown"}
    assert isinstance(sample.tables, list)
    assert sample.last_update is None or sample.last_update.tzinfo is not None


@pytest.mark.asyncio()
async def test_constructor_injection_kept() -> None:
    async def factory() -> object:
        return object()

    service = DataSourceStatusService(redis_client_factory=factory)

    assert service._redis_client_factory is factory


@pytest.mark.asyncio()
async def test_no_redis_fallback_returns_data_and_logs_warning(
    operator_user: DummyUser,
    caplog: pytest.LogCaptureFixture,
) -> None:
    service = DataSourceStatusService(redis_client_factory=None)

    with caplog.at_level("WARNING"):
        status = await service.refresh_source(operator_user, "crsp")

    assert status.name == "crsp"
    assert "redis_lock_disabled_no_factory" in caplog.text


@pytest.mark.asyncio()
async def test_source_lookup_normalizes_keyerror_to_permission_error(
    operator_user: DummyUser,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = DataSourceStatusService(redis_client_factory=None)

    def raise_key_error(_name: str):
        raise KeyError("broken lookup")

    monkeypatch.setattr(service, "_get_source_by_name", raise_key_error)

    with pytest.raises(PermissionError, match="Source not available"):
        await service.refresh_source(operator_user, "crsp")


@pytest.mark.asyncio()
async def test_source_lookup_normalizes_lookuperror_to_permission_error(
    operator_user: DummyUser,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = DataSourceStatusService(redis_client_factory=None)

    def raise_lookup_error(_name: str):
        raise LookupError("broken lookup")

    monkeypatch.setattr(service, "_get_source_by_name", raise_lookup_error)

    with pytest.raises(PermissionError, match="Source not available"):
        await service.refresh_source(operator_user, "crsp")


@pytest.mark.asyncio()
async def test_refresh_lock_concurrent_returns_cached(operator_user: DummyUser) -> None:
    redis_client = DummyRedis(set_results=[True, False])

    async def factory() -> DummyRedis:
        return redis_client

    service = DataSourceStatusService(redis_client_factory=factory)

    first_call_started = asyncio.Event()
    allow_finish = asyncio.Event()

    original = service._perform_refresh

    async def slow_refresh(source):
        first_call_started.set()
        await allow_finish.wait()
        return await original(source)

    service._perform_refresh = slow_refresh  # type: ignore[assignment]

    task1 = asyncio.create_task(service.refresh_source(operator_user, "crsp"))
    await first_call_started.wait()
    task2 = asyncio.create_task(service.refresh_source(operator_user, "crsp"))
    allow_finish.set()

    result1, result2 = await asyncio.gather(task1, task2)

    assert result1.name == "crsp"
    assert result2.name == "crsp"
    assert len(redis_client.set_calls) == 2


@pytest.mark.asyncio()
async def test_refresh_lock_released_after_success(operator_user: DummyUser) -> None:
    redis_client = DummyRedis()

    async def factory() -> DummyRedis:
        return redis_client

    service = DataSourceStatusService(redis_client_factory=factory)

    await service.refresh_source(operator_user, "crsp")

    assert len(redis_client.eval_calls) == 1


@pytest.mark.asyncio()
async def test_refresh_lock_released_after_failure(operator_user: DummyUser) -> None:
    redis_client = DummyRedis()

    async def factory() -> DummyRedis:
        return redis_client

    service = DataSourceStatusService(redis_client_factory=factory)

    async def fail_refresh(_source):
        raise RuntimeError("refresh failed")

    service._perform_refresh = fail_refresh  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="refresh failed"):
        await service.refresh_source(operator_user, "crsp")

    assert len(redis_client.eval_calls) == 1


@pytest.mark.asyncio()
async def test_refresh_lock_release_failure_does_not_override_result(
    operator_user: DummyUser,
    caplog: pytest.LogCaptureFixture,
) -> None:
    redis_client = DummyRedis(eval_side_effect=redis.exceptions.ConnectionError("blip"))

    async def factory() -> DummyRedis:
        return redis_client

    service = DataSourceStatusService(redis_client_factory=factory)

    with caplog.at_level("WARNING"):
        result = await service.refresh_source(operator_user, "crsp")

    assert result.name == "crsp"
    assert "redis_lock_release_failed" in caplog.text


@pytest.mark.asyncio()
async def test_refresh_lock_acquire_failure_mock_mode_fallback(
    operator_user: DummyUser,
    caplog: pytest.LogCaptureFixture,
) -> None:
    redis_client = DummyRedis(set_side_effect=redis.exceptions.ConnectionError("down"))

    async def factory() -> DummyRedis:
        return redis_client

    service = DataSourceStatusService(redis_client_factory=factory, data_mode="mock")

    with caplog.at_level("WARNING"):
        result = await service.refresh_source(operator_user, "crsp")

    assert result.name == "crsp"
    assert "redis_lock_acquire_failed_fallback" in caplog.text


@pytest.mark.asyncio()
async def test_refresh_lock_acquire_failure_real_mode_raises(operator_user: DummyUser) -> None:
    redis_client = DummyRedis(set_side_effect=redis.exceptions.ConnectionError("down"))

    async def factory() -> DummyRedis:
        return redis_client

    service = DataSourceStatusService(redis_client_factory=factory, data_mode="real")

    with pytest.raises(RuntimeError, match="Redis required for real-data refresh"):
        await service.refresh_source(operator_user, "crsp")


@pytest.mark.asyncio()
async def test_runtime_redis_factory_failure_paths(
    operator_user: DummyUser,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def failing_factory() -> DummyRedis:
        raise redis.exceptions.TimeoutError("timeout")

    service_mock = DataSourceStatusService(redis_client_factory=failing_factory, data_mode="mock")
    with caplog.at_level("WARNING"):
        result = await service_mock.refresh_source(operator_user, "crsp")
    assert result.name == "crsp"
    assert "redis_lock_fallback_runtime_error" in caplog.text

    service_real = DataSourceStatusService(redis_client_factory=failing_factory, data_mode="real")
    with pytest.raises(RuntimeError, match="Redis required for real-data refresh"):
        await service_real.refresh_source(operator_user, "crsp")


@pytest.mark.asyncio()
async def test_refresh_timeout_releases_lock(operator_user: DummyUser) -> None:
    redis_client = DummyRedis()

    async def factory() -> DummyRedis:
        return redis_client

    service = DataSourceStatusService(redis_client_factory=factory)

    async def never_finishes(_source):
        await asyncio.sleep(_REFRESH_TIMEOUT_SECONDS + 0.2)
        return AsyncMock()

    service._perform_refresh = never_finishes  # type: ignore[assignment]

    with pytest.raises(asyncio.TimeoutError):
        await service.refresh_source(operator_user, "crsp")

    assert len(redis_client.eval_calls) == 1


@pytest.mark.asyncio()
async def test_refresh_then_list_returns_refreshed_state(operator_user: DummyUser) -> None:
    """After manual refresh, get_all_sources must return refreshed fields (merge path)."""
    service = DataSourceStatusService(redis_client_factory=None)

    pre_list = await service.get_all_sources(operator_user)
    crsp_before = next(s for s in pre_list if s.name == "crsp")
    assert crsp_before.age_seconds is not None
    assert crsp_before.age_seconds > 0

    refreshed = await service.refresh_source(operator_user, "crsp")
    assert refreshed.age_seconds == 0.0

    post_list = await service.get_all_sources(operator_user)
    crsp_after = next(s for s in post_list if s.name == "crsp")
    assert crsp_after.age_seconds == 0.0
    assert crsp_after.last_update == refreshed.last_update


def test_invalid_data_mode_raises() -> None:
    with pytest.raises(ValueError, match="Invalid data_mode"):
        DataSourceStatusService(data_mode="invalid")  # type: ignore[arg-type]
