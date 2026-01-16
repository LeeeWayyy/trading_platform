"""Tests for execution gateway lifespan helpers."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI

from apps.execution_gateway.fat_finger_validator import FatFingerValidator
from apps.execution_gateway.lifespan import (
    LifespanResources,
    LifespanSettings,
    _is_reconciliation_ready,
    _recover_zombie_slices_after_reconciliation,
    shutdown_execution_gateway,
    startup_execution_gateway,
)
from apps.execution_gateway.schemas import FatFingerThresholds
from libs.trading.risk_management import RiskConfig
from libs.core.redis_client import RedisConnectionError
from apps.execution_gateway.alpaca_client import AlpacaConnectionError, AlpacaValidationError


class DummyDBClient:
    def __init__(self, url: str) -> None:
        self.url = url
        self.closed = False

    def check_connection(self) -> bool:
        return True

    def close(self) -> None:
        self.closed = True


class DummyRedisClient:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


class DummyScheduler:
    def __init__(self) -> None:
        self.running = False


class DummySliceScheduler:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.scheduler = DummyScheduler()
        self.started = False
        self.shutdown_called = False

    def start(self) -> None:
        self.started = True
        self.scheduler.running = True

    def shutdown(self, wait: bool = True) -> None:
        self.shutdown_called = True
        self.scheduler.running = False


class DummyRecoveryManager:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.kill_switch = None
        self.circuit_breaker = None
        self.position_reservation = None
        self.slice_scheduler = None

    def initialize_kill_switch(self, factory: object) -> None:
        self.kill_switch = factory()

    def initialize_circuit_breaker(self, factory: object) -> None:
        self.circuit_breaker = factory()

    def initialize_position_reservation(self, factory: object) -> None:
        self.position_reservation = factory()


class DummyPool:
    def __init__(self) -> None:
        self.open_called = False
        self.close_called = False

    async def open(self) -> None:
        self.open_called = True

    async def close(self) -> None:
        self.close_called = True


def _make_settings(dry_run: bool = True) -> LifespanSettings:
    return LifespanSettings(
        dry_run=dry_run,
        strategy_id="alpha_baseline",
        environment="test",
        alpaca_base_url="https://paper-api.alpaca.markets",
        alpaca_paper=True,
        alpaca_data_feed=None,
        liquidity_check_enabled=False,
        redis_host="localhost",
        redis_port=6379,
        redis_db=0,
        version="0.1.0",
        risk_config=RiskConfig(),
        fat_finger_validator=FatFingerValidator(FatFingerThresholds()),
        twap_slicer=MagicMock(),
    )


def test_is_reconciliation_ready():
    settings = _make_settings(dry_run=True)
    resources = LifespanResources(
        db_client=MagicMock(),
        redis_client=None,
        alpaca_client=None,
        webhook_secret="",
        liquidity_service=None,
        recovery_manager=MagicMock(),
        reconciliation_service=None,
        reconciliation_task=None,
    )

    assert _is_reconciliation_ready(settings, resources) is True

    settings.dry_run = False
    assert _is_reconciliation_ready(settings, resources) is False

    resources.reconciliation_service = MagicMock()
    resources.reconciliation_service.is_startup_complete.return_value = True
    assert _is_reconciliation_ready(settings, resources) is True


@pytest.mark.asyncio
async def test_recover_zombie_slices_skips_without_recovery_manager(caplog):
    settings = _make_settings()
    resources = LifespanResources(
        db_client=MagicMock(),
        redis_client=None,
        alpaca_client=None,
        webhook_secret="",
        liquidity_service=None,
        recovery_manager=None,  # type: ignore[arg-type]
        reconciliation_service=None,
        reconciliation_task=None,
    )

    await _recover_zombie_slices_after_reconciliation(settings, resources)
    assert "Recovery manager unavailable" in caplog.text


@pytest.mark.asyncio
async def test_recover_zombie_slices_timed_out(monkeypatch):
    settings = _make_settings(dry_run=False)
    reconciliation_service = MagicMock()
    reconciliation_service.is_startup_complete.return_value = False
    reconciliation_service.startup_timed_out.return_value = True

    slice_scheduler = MagicMock()
    recovery_manager = SimpleNamespace(slice_scheduler=slice_scheduler)
    resources = LifespanResources(
        db_client=MagicMock(),
        redis_client=None,
        alpaca_client=None,
        webhook_secret="",
        liquidity_service=None,
        recovery_manager=recovery_manager,  # type: ignore[arg-type]
        reconciliation_service=reconciliation_service,
        reconciliation_task=None,
    )

    async def _sleep(_: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", _sleep)
    await _recover_zombie_slices_after_reconciliation(settings, resources)
    slice_scheduler.recover_zombie_slices.assert_not_called()


@pytest.mark.asyncio
async def test_recover_zombie_slices_runs(monkeypatch):
    settings = _make_settings(dry_run=False)
    reconciliation_service = MagicMock()
    reconciliation_service.is_startup_complete.return_value = True

    slice_scheduler = MagicMock()
    recovery_manager = SimpleNamespace(slice_scheduler=slice_scheduler)
    resources = LifespanResources(
        db_client=MagicMock(),
        redis_client=None,
        alpaca_client=None,
        webhook_secret="",
        liquidity_service=None,
        recovery_manager=recovery_manager,  # type: ignore[arg-type]
        reconciliation_service=reconciliation_service,
        reconciliation_task=None,
    )

    async def _to_thread(func: object) -> None:
        func()

    monkeypatch.setattr(asyncio, "to_thread", _to_thread)
    await _recover_zombie_slices_after_reconciliation(settings, resources)
    slice_scheduler.recover_zombie_slices.assert_called_once()


@pytest.mark.asyncio
async def test_recover_zombie_slices_waits_for_reconciliation(monkeypatch):
    settings = _make_settings(dry_run=False)

    class Recon:
        def __init__(self) -> None:
            self.calls = 0

        def is_startup_complete(self) -> bool:
            self.calls += 1
            return self.calls > 1

        def startup_timed_out(self) -> bool:
            return False

    reconciliation_service = Recon()
    slice_scheduler = MagicMock()
    recovery_manager = SimpleNamespace(slice_scheduler=slice_scheduler)
    resources = LifespanResources(
        db_client=MagicMock(),
        redis_client=None,
        alpaca_client=None,
        webhook_secret="",
        liquidity_service=None,
        recovery_manager=recovery_manager,  # type: ignore[arg-type]
        reconciliation_service=reconciliation_service,
        reconciliation_task=None,
    )

    async def _sleep(_: float) -> None:
        return None

    async def _to_thread(func: object) -> None:
        func()

    monkeypatch.setattr(asyncio, "sleep", _sleep)
    monkeypatch.setattr(asyncio, "to_thread", _to_thread)

    await _recover_zombie_slices_after_reconciliation(settings, resources)
    slice_scheduler.recover_zombie_slices.assert_called_once()


@pytest.mark.asyncio
async def test_recover_zombie_slices_skips_without_scheduler(caplog):
    settings = _make_settings(dry_run=True)
    recovery_manager = SimpleNamespace(slice_scheduler=None)
    resources = LifespanResources(
        db_client=MagicMock(),
        redis_client=None,
        alpaca_client=None,
        webhook_secret="",
        liquidity_service=None,
        recovery_manager=recovery_manager,  # type: ignore[arg-type]
        reconciliation_service=None,
        reconciliation_task=None,
    )

    await _recover_zombie_slices_after_reconciliation(settings, resources)
    assert "Slice scheduler unavailable" in caplog.text


@pytest.mark.asyncio
async def test_recover_zombie_slices_requires_reconciliation(caplog):
    settings = _make_settings(dry_run=False)
    slice_scheduler = MagicMock()
    recovery_manager = SimpleNamespace(slice_scheduler=slice_scheduler)
    resources = LifespanResources(
        db_client=MagicMock(),
        redis_client=None,
        alpaca_client=None,
        webhook_secret="",
        liquidity_service=None,
        recovery_manager=recovery_manager,  # type: ignore[arg-type]
        reconciliation_service=None,
        reconciliation_task=None,
    )

    await _recover_zombie_slices_after_reconciliation(settings, resources)
    assert "Reconciliation service unavailable" in caplog.text


@pytest.mark.asyncio
async def test_startup_execution_gateway_dry_run(monkeypatch):
    settings = _make_settings(dry_run=True)
    app = FastAPI()
    metrics = {"dummy": MagicMock()}
    pool = DummyPool()

    monkeypatch.setenv("REDIS_AUTH_REQUIRED", "false")

    monkeypatch.setattr("apps.execution_gateway.lifespan.DatabaseClient", DummyDBClient)
    monkeypatch.setattr("apps.execution_gateway.lifespan.RedisClient", DummyRedisClient)
    monkeypatch.setattr("apps.execution_gateway.lifespan.RecoveryManager", DummyRecoveryManager)
    monkeypatch.setattr("apps.execution_gateway.lifespan.SliceScheduler", DummySliceScheduler)
    monkeypatch.setattr("apps.execution_gateway.lifespan.KillSwitch", lambda redis_client=None: object())
    monkeypatch.setattr(
        "apps.execution_gateway.lifespan.CircuitBreaker", lambda redis_client=None: object()
    )
    monkeypatch.setattr(
        "apps.execution_gateway.lifespan.PositionReservation", lambda redis=None: object()
    )
    monkeypatch.setattr("apps.execution_gateway.lifespan.get_settings", lambda: SimpleNamespace(
        internal_token_required=True,
        internal_token_secret=SimpleNamespace(get_secret_value=lambda: ""),
    ))
    monkeypatch.setattr("apps.execution_gateway.lifespan.validate_required_secrets", lambda _: None)
    monkeypatch.setattr("apps.execution_gateway.lifespan.get_required_secret", lambda _: "secret")
    monkeypatch.setattr("apps.execution_gateway.lifespan.get_optional_secret", lambda *_: "opt")
    monkeypatch.setattr("apps.execution_gateway.lifespan.get_optional_secret_or_none", lambda *_: None)
    monkeypatch.setattr("apps.execution_gateway.lifespan.close_secret_manager", lambda: None)

    async def _noop_recover(_: LifespanSettings, __: LifespanResources) -> None:
        return None

    monkeypatch.setattr(
        "apps.execution_gateway.lifespan._recover_zombie_slices_after_reconciliation",
        _noop_recover,
    )

    def _get_db_pool() -> DummyPool:
        return pool

    _get_db_pool.cache_info = lambda: SimpleNamespace(currsize=1)  # type: ignore[attr-defined]
    monkeypatch.setattr("apps.execution_gateway.api.dependencies.get_db_pool", _get_db_pool)

    resources = await startup_execution_gateway(app, settings, metrics)

    assert resources.db_client is not None
    assert app.state.version == settings.version
    assert app.state.metrics is metrics
    assert app.state.context.db is resources.db_client
    assert pool.open_called is True

    if resources.zombie_recovery_task:
        await resources.zombie_recovery_task


@pytest.mark.asyncio
async def test_startup_execution_gateway_requires_redis_password(monkeypatch):
    settings = _make_settings(dry_run=True)
    app = FastAPI()

    monkeypatch.setenv("REDIS_AUTH_REQUIRED", "true")
    monkeypatch.setattr("apps.execution_gateway.lifespan.DatabaseClient", DummyDBClient)
    monkeypatch.setattr("apps.execution_gateway.lifespan.validate_required_secrets", lambda _: None)
    monkeypatch.setattr("apps.execution_gateway.lifespan.get_required_secret", lambda _: "secret")
    monkeypatch.setattr("apps.execution_gateway.lifespan.get_optional_secret_or_none", lambda *_: None)

    with pytest.raises(RuntimeError, match="REDIS_AUTH_REQUIRED=true"):
        await startup_execution_gateway(app, settings, {})


@pytest.mark.asyncio
async def test_startup_execution_gateway_redis_error_fallback(monkeypatch):
    settings = _make_settings(dry_run=True)
    app = FastAPI()
    pool = DummyPool()

    monkeypatch.setattr("apps.execution_gateway.lifespan.DatabaseClient", DummyDBClient)

    def _redis_fail(**kwargs: object) -> None:  # type: ignore[return-value]
        raise RedisConnectionError("redis down")

    monkeypatch.setattr("apps.execution_gateway.lifespan.RedisClient", _redis_fail)
    monkeypatch.setattr("apps.execution_gateway.lifespan.RecoveryManager", DummyRecoveryManager)
    monkeypatch.setattr("apps.execution_gateway.lifespan.KillSwitch", lambda redis_client=None: object())
    monkeypatch.setattr(
        "apps.execution_gateway.lifespan.CircuitBreaker", lambda redis_client=None: object()
    )
    monkeypatch.setattr(
        "apps.execution_gateway.lifespan.PositionReservation", lambda redis=None: object()
    )
    monkeypatch.setattr("apps.execution_gateway.lifespan.validate_required_secrets", lambda _: None)
    monkeypatch.setattr("apps.execution_gateway.lifespan.get_required_secret", lambda _: "secret")
    monkeypatch.setattr("apps.execution_gateway.lifespan.get_optional_secret_or_none", lambda *_: None)
    monkeypatch.setattr("apps.execution_gateway.lifespan.get_optional_secret", lambda *_: "")

    class DummyReconciliation:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

        async def run_startup_reconciliation(self) -> None:
            return None

        async def run_periodic_loop(self) -> None:
            await asyncio.sleep(0)

        def stop(self) -> None:
            return None

        def is_startup_complete(self) -> bool:
            return True

        def startup_timed_out(self) -> bool:
            return False

    monkeypatch.setattr("apps.execution_gateway.lifespan.ReconciliationService", DummyReconciliation)

    class DummyReconciliation:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

        async def run_startup_reconciliation(self) -> None:
            return None

        async def run_periodic_loop(self) -> None:
            await asyncio.sleep(0)

        def stop(self) -> None:
            return None

        def is_startup_complete(self) -> bool:
            return True

        def startup_timed_out(self) -> bool:
            return False

    monkeypatch.setattr("apps.execution_gateway.lifespan.ReconciliationService", DummyReconciliation)

    class DummyReconciliation:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

        async def run_startup_reconciliation(self) -> None:
            return None

        async def run_periodic_loop(self) -> None:
            await asyncio.sleep(0)

        def stop(self) -> None:
            return None

        def is_startup_complete(self) -> bool:
            return True

        def startup_timed_out(self) -> bool:
            return False

    monkeypatch.setattr("apps.execution_gateway.lifespan.ReconciliationService", DummyReconciliation)

    class DummyReconciliation:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

        async def run_startup_reconciliation(self) -> None:
            return None

        async def run_periodic_loop(self) -> None:
            await asyncio.sleep(0)

        def stop(self) -> None:
            return None

        def is_startup_complete(self) -> bool:
            return True

        def startup_timed_out(self) -> bool:
            return False

    monkeypatch.setattr("apps.execution_gateway.lifespan.ReconciliationService", DummyReconciliation)
    monkeypatch.setattr("apps.execution_gateway.lifespan.get_settings", lambda: SimpleNamespace(
        internal_token_required=False,
        internal_token_secret=SimpleNamespace(get_secret_value=lambda: ""),
    ))

    async def _noop_recover(_: LifespanSettings, __: LifespanResources) -> None:
        return None

    monkeypatch.setattr(
        "apps.execution_gateway.lifespan._recover_zombie_slices_after_reconciliation",
        _noop_recover,
    )

    def _get_db_pool() -> DummyPool:
        return pool

    _get_db_pool.cache_info = lambda: SimpleNamespace(currsize=1)  # type: ignore[attr-defined]
    monkeypatch.setattr("apps.execution_gateway.api.dependencies.get_db_pool", _get_db_pool)

    resources = await startup_execution_gateway(app, settings, {})
    assert resources.redis_client is None


@pytest.mark.asyncio
async def test_startup_execution_gateway_requires_webhook_secret(monkeypatch):
    settings = _make_settings(dry_run=True)
    settings.environment = "prod"
    app = FastAPI()

    monkeypatch.setattr("apps.execution_gateway.lifespan.DatabaseClient", DummyDBClient)
    monkeypatch.setattr("apps.execution_gateway.lifespan.validate_required_secrets", lambda _: None)

    def _get_required(name: str) -> str:
        if name == "webhook/secret":
            return ""
        return "secret"

    monkeypatch.setattr("apps.execution_gateway.lifespan.get_required_secret", _get_required)
    monkeypatch.setattr("apps.execution_gateway.lifespan.get_optional_secret_or_none", lambda *_: None)

    with pytest.raises(RuntimeError, match="WEBHOOK_SECRET is required"):
        await startup_execution_gateway(app, settings, {})


@pytest.mark.asyncio
async def test_startup_execution_gateway_alpaca_errors(monkeypatch):
    settings = _make_settings(dry_run=False)
    app = FastAPI()
    pool = DummyPool()

    monkeypatch.setattr("apps.execution_gateway.lifespan.DatabaseClient", DummyDBClient)
    monkeypatch.setattr("apps.execution_gateway.lifespan.RedisClient", DummyRedisClient)
    monkeypatch.setattr("apps.execution_gateway.lifespan.RecoveryManager", DummyRecoveryManager)
    monkeypatch.setattr("apps.execution_gateway.lifespan.KillSwitch", lambda redis_client=None: object())
    monkeypatch.setattr(
        "apps.execution_gateway.lifespan.CircuitBreaker", lambda redis_client=None: object()
    )
    monkeypatch.setattr(
        "apps.execution_gateway.lifespan.PositionReservation", lambda redis=None: object()
    )
    monkeypatch.setattr("apps.execution_gateway.lifespan.validate_required_secrets", lambda _: None)
    monkeypatch.setattr("apps.execution_gateway.lifespan.get_required_secret", lambda _: "secret")
    monkeypatch.setattr("apps.execution_gateway.lifespan.get_optional_secret_or_none", lambda *_: None)
    monkeypatch.setattr("apps.execution_gateway.lifespan.get_optional_secret", lambda *_: "")

    class DummyReconciliation:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

        async def run_startup_reconciliation(self) -> None:
            return None

        async def run_periodic_loop(self) -> None:
            await asyncio.sleep(0)

        def stop(self) -> None:
            return None

        def is_startup_complete(self) -> bool:
            return True

        def startup_timed_out(self) -> bool:
            return False

    monkeypatch.setattr("apps.execution_gateway.lifespan.ReconciliationService", DummyReconciliation)

    def _alpaca_fail(**kwargs: object) -> None:  # type: ignore[return-value]
        raise AlpacaConnectionError("alpaca down")

    monkeypatch.setattr("apps.execution_gateway.lifespan.AlpacaExecutor", _alpaca_fail)

    async def _noop_recover(_: LifespanSettings, __: LifespanResources) -> None:
        return None

    monkeypatch.setattr(
        "apps.execution_gateway.lifespan._recover_zombie_slices_after_reconciliation",
        _noop_recover,
    )

    def _get_db_pool() -> DummyPool:
        return pool

    _get_db_pool.cache_info = lambda: SimpleNamespace(currsize=1)  # type: ignore[attr-defined]
    monkeypatch.setattr("apps.execution_gateway.api.dependencies.get_db_pool", _get_db_pool)

    resources = await startup_execution_gateway(app, settings, {})
    assert resources.alpaca_client is None


@pytest.mark.asyncio
async def test_startup_execution_gateway_alpaca_validation_error(monkeypatch):
    settings = _make_settings(dry_run=False)
    app = FastAPI()

    monkeypatch.setattr("apps.execution_gateway.lifespan.DatabaseClient", DummyDBClient)
    monkeypatch.setattr("apps.execution_gateway.lifespan.RedisClient", DummyRedisClient)
    monkeypatch.setattr("apps.execution_gateway.lifespan.RecoveryManager", DummyRecoveryManager)
    monkeypatch.setattr("apps.execution_gateway.lifespan.KillSwitch", lambda redis_client=None: object())
    monkeypatch.setattr(
        "apps.execution_gateway.lifespan.CircuitBreaker", lambda redis_client=None: object()
    )
    monkeypatch.setattr(
        "apps.execution_gateway.lifespan.PositionReservation", lambda redis=None: object()
    )
    monkeypatch.setattr("apps.execution_gateway.lifespan.validate_required_secrets", lambda _: None)
    monkeypatch.setattr("apps.execution_gateway.lifespan.get_required_secret", lambda _: "secret")
    monkeypatch.setattr("apps.execution_gateway.lifespan.get_optional_secret_or_none", lambda *_: None)
    monkeypatch.setattr("apps.execution_gateway.lifespan.get_optional_secret", lambda *_: "")

    def _alpaca_fail(**kwargs: object) -> None:  # type: ignore[return-value]
        raise AlpacaValidationError("bad creds")

    monkeypatch.setattr("apps.execution_gateway.lifespan.AlpacaExecutor", _alpaca_fail)

    def _get_db_pool() -> DummyPool:
        return DummyPool()

    _get_db_pool.cache_info = lambda: SimpleNamespace(currsize=1)  # type: ignore[attr-defined]
    monkeypatch.setattr("apps.execution_gateway.api.dependencies.get_db_pool", _get_db_pool)
    monkeypatch.setattr(
        "apps.execution_gateway.lifespan._recover_zombie_slices_after_reconciliation",
        lambda *_: asyncio.sleep(0),
    )

    resources = await startup_execution_gateway(app, settings, {})
    assert resources.alpaca_client is None


@pytest.mark.asyncio
async def test_startup_execution_gateway_alpaca_type_error(monkeypatch):
    settings = _make_settings(dry_run=False)
    app = FastAPI()

    monkeypatch.setattr("apps.execution_gateway.lifespan.DatabaseClient", DummyDBClient)
    monkeypatch.setattr("apps.execution_gateway.lifespan.RedisClient", DummyRedisClient)
    monkeypatch.setattr("apps.execution_gateway.lifespan.RecoveryManager", DummyRecoveryManager)
    monkeypatch.setattr("apps.execution_gateway.lifespan.KillSwitch", lambda redis_client=None: object())
    monkeypatch.setattr(
        "apps.execution_gateway.lifespan.CircuitBreaker", lambda redis_client=None: object()
    )
    monkeypatch.setattr(
        "apps.execution_gateway.lifespan.PositionReservation", lambda redis=None: object()
    )
    monkeypatch.setattr("apps.execution_gateway.lifespan.validate_required_secrets", lambda _: None)
    monkeypatch.setattr("apps.execution_gateway.lifespan.get_required_secret", lambda _: "secret")
    monkeypatch.setattr("apps.execution_gateway.lifespan.get_optional_secret_or_none", lambda *_: None)
    monkeypatch.setattr("apps.execution_gateway.lifespan.get_optional_secret", lambda *_: "")

    def _alpaca_fail(**kwargs: object) -> None:  # type: ignore[return-value]
        raise TypeError("bad config")

    monkeypatch.setattr("apps.execution_gateway.lifespan.AlpacaExecutor", _alpaca_fail)

    def _get_db_pool() -> DummyPool:
        return DummyPool()

    _get_db_pool.cache_info = lambda: SimpleNamespace(currsize=1)  # type: ignore[attr-defined]
    monkeypatch.setattr("apps.execution_gateway.api.dependencies.get_db_pool", _get_db_pool)
    monkeypatch.setattr(
        "apps.execution_gateway.lifespan._recover_zombie_slices_after_reconciliation",
        lambda *_: asyncio.sleep(0),
    )

    resources = await startup_execution_gateway(app, settings, {})
    assert resources.alpaca_client is None


@pytest.mark.asyncio
async def test_startup_execution_gateway_alpaca_and_reconciliation(monkeypatch):
    settings = _make_settings(dry_run=False)
    settings.liquidity_check_enabled = True
    app = FastAPI()
    pool = DummyPool()

    monkeypatch.setattr("apps.execution_gateway.lifespan.DatabaseClient", DummyDBClient)
    monkeypatch.setattr("apps.execution_gateway.lifespan.RedisClient", DummyRedisClient)
    monkeypatch.setattr("apps.execution_gateway.lifespan.RecoveryManager", DummyRecoveryManager)
    monkeypatch.setattr("apps.execution_gateway.lifespan.SliceScheduler", DummySliceScheduler)
    monkeypatch.setattr("apps.execution_gateway.lifespan.KillSwitch", lambda redis_client=None: object())
    monkeypatch.setattr(
        "apps.execution_gateway.lifespan.CircuitBreaker", lambda redis_client=None: object()
    )
    monkeypatch.setattr(
        "apps.execution_gateway.lifespan.PositionReservation", lambda redis=None: object()
    )
    monkeypatch.setattr("apps.execution_gateway.lifespan.validate_required_secrets", lambda _: None)
    monkeypatch.setattr("apps.execution_gateway.lifespan.get_required_secret", lambda _: "secret")
    monkeypatch.setattr("apps.execution_gateway.lifespan.get_optional_secret_or_none", lambda *_: None)
    monkeypatch.setattr("apps.execution_gateway.lifespan.get_optional_secret", lambda *_: "")

    dummy_alpaca = SimpleNamespace(check_connection=lambda: True)
    monkeypatch.setattr("apps.execution_gateway.lifespan.AlpacaExecutor", lambda **_: dummy_alpaca)
    monkeypatch.setattr("apps.execution_gateway.lifespan.LiquidityService", lambda **_: "liq")

    class DummyReconciliation:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

        async def run_startup_reconciliation(self) -> None:
            return None

        async def run_periodic_loop(self) -> None:
            await asyncio.sleep(0)

        def stop(self) -> None:
            return None

        def is_startup_complete(self) -> bool:
            return True

        def startup_timed_out(self) -> bool:
            return False

    monkeypatch.setattr("apps.execution_gateway.lifespan.ReconciliationService", DummyReconciliation)

    async def _noop_recover(_: LifespanSettings, __: LifespanResources) -> None:
        return None

    monkeypatch.setattr(
        "apps.execution_gateway.lifespan._recover_zombie_slices_after_reconciliation",
        _noop_recover,
    )

    def _get_db_pool() -> DummyPool:
        return pool

    _get_db_pool.cache_info = lambda: SimpleNamespace(currsize=1)  # type: ignore[attr-defined]
    monkeypatch.setattr("apps.execution_gateway.api.dependencies.get_db_pool", _get_db_pool)

    resources = await startup_execution_gateway(app, settings, {})
    assert resources.alpaca_client is dummy_alpaca
    assert resources.liquidity_service == "liq"
    assert resources.reconciliation_service is not None


@pytest.mark.asyncio
async def test_startup_execution_gateway_missing_safety_components(monkeypatch):
    settings = _make_settings(dry_run=True)
    app = FastAPI()
    pool = DummyPool()

    class EmptyRecoveryManager(DummyRecoveryManager):
        def initialize_kill_switch(self, factory: object) -> None:
            self.kill_switch = None

        def initialize_circuit_breaker(self, factory: object) -> None:
            self.circuit_breaker = None

    monkeypatch.setattr("apps.execution_gateway.lifespan.DatabaseClient", DummyDBClient)
    monkeypatch.setattr("apps.execution_gateway.lifespan.RedisClient", DummyRedisClient)
    monkeypatch.setattr("apps.execution_gateway.lifespan.RecoveryManager", EmptyRecoveryManager)
    monkeypatch.setattr("apps.execution_gateway.lifespan.validate_required_secrets", lambda _: None)
    monkeypatch.setattr("apps.execution_gateway.lifespan.get_required_secret", lambda _: "secret")
    monkeypatch.setattr("apps.execution_gateway.lifespan.get_optional_secret_or_none", lambda *_: None)
    monkeypatch.setattr("apps.execution_gateway.lifespan.get_optional_secret", lambda *_: "")

    class DummyReconciliation:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

        async def run_startup_reconciliation(self) -> None:
            return None

        async def run_periodic_loop(self) -> None:
            await asyncio.sleep(0)

        def stop(self) -> None:
            return None

        def is_startup_complete(self) -> bool:
            return True

        def startup_timed_out(self) -> bool:
            return False

    monkeypatch.setattr("apps.execution_gateway.lifespan.ReconciliationService", DummyReconciliation)

    async def _noop_recover(_: LifespanSettings, __: LifespanResources) -> None:
        return None

    monkeypatch.setattr(
        "apps.execution_gateway.lifespan._recover_zombie_slices_after_reconciliation",
        _noop_recover,
    )

    def _get_db_pool() -> DummyPool:
        return pool

    _get_db_pool.cache_info = lambda: SimpleNamespace(currsize=1)  # type: ignore[attr-defined]
    monkeypatch.setattr("apps.execution_gateway.api.dependencies.get_db_pool", _get_db_pool)

    resources = await startup_execution_gateway(app, settings, {})
    assert resources.recovery_manager.slice_scheduler is None


@pytest.mark.asyncio
async def test_startup_execution_gateway_slice_scheduler_error(monkeypatch):
    settings = _make_settings(dry_run=True)
    app = FastAPI()
    pool = DummyPool()

    def _bad_scheduler(**kwargs: object) -> None:  # type: ignore[return-value]
        raise TypeError("bad scheduler")

    monkeypatch.setattr("apps.execution_gateway.lifespan.DatabaseClient", DummyDBClient)
    monkeypatch.setattr("apps.execution_gateway.lifespan.RedisClient", DummyRedisClient)
    monkeypatch.setattr("apps.execution_gateway.lifespan.RecoveryManager", DummyRecoveryManager)
    monkeypatch.setattr("apps.execution_gateway.lifespan.SliceScheduler", _bad_scheduler)
    monkeypatch.setattr("apps.execution_gateway.lifespan.KillSwitch", lambda redis_client=None: object())
    monkeypatch.setattr(
        "apps.execution_gateway.lifespan.CircuitBreaker", lambda redis_client=None: object()
    )
    monkeypatch.setattr(
        "apps.execution_gateway.lifespan.PositionReservation", lambda redis=None: object()
    )
    monkeypatch.setattr("apps.execution_gateway.lifespan.validate_required_secrets", lambda _: None)
    monkeypatch.setattr("apps.execution_gateway.lifespan.get_required_secret", lambda _: "secret")
    monkeypatch.setattr("apps.execution_gateway.lifespan.get_optional_secret_or_none", lambda *_: None)
    monkeypatch.setattr("apps.execution_gateway.lifespan.get_optional_secret", lambda *_: "")

    async def _noop_recover(_: LifespanSettings, __: LifespanResources) -> None:
        return None

    monkeypatch.setattr(
        "apps.execution_gateway.lifespan._recover_zombie_slices_after_reconciliation",
        _noop_recover,
    )

    def _get_db_pool() -> DummyPool:
        return pool

    _get_db_pool.cache_info = lambda: SimpleNamespace(currsize=1)  # type: ignore[attr-defined]
    monkeypatch.setattr("apps.execution_gateway.api.dependencies.get_db_pool", _get_db_pool)

    resources = await startup_execution_gateway(app, settings, {})
    assert resources.recovery_manager.slice_scheduler is None


@pytest.mark.asyncio
async def test_startup_execution_gateway_slice_scheduler_value_error(monkeypatch):
    settings = _make_settings(dry_run=True)
    app = FastAPI()

    def _bad_scheduler(**kwargs: object) -> None:  # type: ignore[return-value]
        raise ValueError("bad scheduler")

    monkeypatch.setattr("apps.execution_gateway.lifespan.DatabaseClient", DummyDBClient)
    monkeypatch.setattr("apps.execution_gateway.lifespan.RedisClient", DummyRedisClient)
    monkeypatch.setattr("apps.execution_gateway.lifespan.RecoveryManager", DummyRecoveryManager)
    monkeypatch.setattr("apps.execution_gateway.lifespan.SliceScheduler", _bad_scheduler)
    monkeypatch.setattr("apps.execution_gateway.lifespan.KillSwitch", lambda redis_client=None: object())
    monkeypatch.setattr(
        "apps.execution_gateway.lifespan.CircuitBreaker", lambda redis_client=None: object()
    )
    monkeypatch.setattr(
        "apps.execution_gateway.lifespan.PositionReservation", lambda redis=None: object()
    )
    monkeypatch.setattr("apps.execution_gateway.lifespan.validate_required_secrets", lambda _: None)
    monkeypatch.setattr("apps.execution_gateway.lifespan.get_required_secret", lambda _: "secret")
    monkeypatch.setattr("apps.execution_gateway.lifespan.get_optional_secret_or_none", lambda *_: None)
    monkeypatch.setattr("apps.execution_gateway.lifespan.get_optional_secret", lambda *_: "")

    def _get_db_pool() -> DummyPool:
        return DummyPool()

    _get_db_pool.cache_info = lambda: SimpleNamespace(currsize=1)  # type: ignore[attr-defined]
    monkeypatch.setattr("apps.execution_gateway.api.dependencies.get_db_pool", _get_db_pool)
    monkeypatch.setattr(
        "apps.execution_gateway.lifespan._recover_zombie_slices_after_reconciliation",
        lambda *_: asyncio.sleep(0),
    )

    resources = await startup_execution_gateway(app, settings, {})
    assert resources.recovery_manager.slice_scheduler is None


@pytest.mark.asyncio
async def test_startup_execution_gateway_slice_scheduler_attribute_error(monkeypatch):
    settings = _make_settings(dry_run=True)
    app = FastAPI()

    def _bad_scheduler(**kwargs: object) -> None:  # type: ignore[return-value]
        raise AttributeError("missing scheduler")

    monkeypatch.setattr("apps.execution_gateway.lifespan.DatabaseClient", DummyDBClient)
    monkeypatch.setattr("apps.execution_gateway.lifespan.RedisClient", DummyRedisClient)
    monkeypatch.setattr("apps.execution_gateway.lifespan.RecoveryManager", DummyRecoveryManager)
    monkeypatch.setattr("apps.execution_gateway.lifespan.SliceScheduler", _bad_scheduler)
    monkeypatch.setattr("apps.execution_gateway.lifespan.KillSwitch", lambda redis_client=None: object())
    monkeypatch.setattr(
        "apps.execution_gateway.lifespan.CircuitBreaker", lambda redis_client=None: object()
    )
    monkeypatch.setattr(
        "apps.execution_gateway.lifespan.PositionReservation", lambda redis=None: object()
    )
    monkeypatch.setattr("apps.execution_gateway.lifespan.validate_required_secrets", lambda _: None)
    monkeypatch.setattr("apps.execution_gateway.lifespan.get_required_secret", lambda _: "secret")
    monkeypatch.setattr("apps.execution_gateway.lifespan.get_optional_secret_or_none", lambda *_: None)
    monkeypatch.setattr("apps.execution_gateway.lifespan.get_optional_secret", lambda *_: "")

    def _get_db_pool() -> DummyPool:
        return DummyPool()

    _get_db_pool.cache_info = lambda: SimpleNamespace(currsize=1)  # type: ignore[attr-defined]
    monkeypatch.setattr("apps.execution_gateway.api.dependencies.get_db_pool", _get_db_pool)
    monkeypatch.setattr(
        "apps.execution_gateway.lifespan._recover_zombie_slices_after_reconciliation",
        lambda *_: asyncio.sleep(0),
    )

    resources = await startup_execution_gateway(app, settings, {})
    assert resources.recovery_manager.slice_scheduler is None


@pytest.mark.asyncio
async def test_startup_execution_gateway_db_and_alpaca_checks(monkeypatch):
    settings = _make_settings(dry_run=False)
    app = FastAPI()
    pool = DummyPool()

    class FailingDB(DummyDBClient):
        def check_connection(self) -> bool:  # type: ignore[override]
            return False

    dummy_alpaca = SimpleNamespace(check_connection=lambda: False)

    monkeypatch.setattr("apps.execution_gateway.lifespan.DatabaseClient", FailingDB)
    monkeypatch.setattr("apps.execution_gateway.lifespan.RedisClient", DummyRedisClient)
    monkeypatch.setattr("apps.execution_gateway.lifespan.RecoveryManager", DummyRecoveryManager)
    monkeypatch.setattr("apps.execution_gateway.lifespan.SliceScheduler", DummySliceScheduler)
    monkeypatch.setattr("apps.execution_gateway.lifespan.KillSwitch", lambda redis_client=None: object())
    monkeypatch.setattr(
        "apps.execution_gateway.lifespan.CircuitBreaker", lambda redis_client=None: object()
    )
    monkeypatch.setattr(
        "apps.execution_gateway.lifespan.PositionReservation", lambda redis=None: object()
    )
    monkeypatch.setattr("apps.execution_gateway.lifespan.AlpacaExecutor", lambda **_: dummy_alpaca)
    monkeypatch.setattr("apps.execution_gateway.lifespan.validate_required_secrets", lambda _: None)
    monkeypatch.setattr("apps.execution_gateway.lifespan.get_required_secret", lambda _: "secret")
    monkeypatch.setattr("apps.execution_gateway.lifespan.get_optional_secret_or_none", lambda *_: None)
    monkeypatch.setattr("apps.execution_gateway.lifespan.get_optional_secret", lambda *_: "")

    class DummyReconciliation:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

        async def run_startup_reconciliation(self) -> None:
            return None

        async def run_periodic_loop(self) -> None:
            await asyncio.sleep(0)

        def stop(self) -> None:
            return None

        def is_startup_complete(self) -> bool:
            return True

        def startup_timed_out(self) -> bool:
            return False

    monkeypatch.setattr("apps.execution_gateway.lifespan.ReconciliationService", DummyReconciliation)

    def _get_db_pool() -> DummyPool:
        return pool

    _get_db_pool.cache_info = lambda: SimpleNamespace(currsize=1)  # type: ignore[attr-defined]
    monkeypatch.setattr("apps.execution_gateway.api.dependencies.get_db_pool", _get_db_pool)
    monkeypatch.setattr(
        "apps.execution_gateway.lifespan._recover_zombie_slices_after_reconciliation",
        lambda *_: asyncio.sleep(0),
    )

    resources = await startup_execution_gateway(app, settings, {})
    assert resources.db_client is not None
    assert resources.alpaca_client is dummy_alpaca


@pytest.mark.asyncio
async def test_startup_execution_gateway_slice_scheduler_running(monkeypatch):
    settings = _make_settings(dry_run=True)
    app = FastAPI()
    pool = DummyPool()

    class RunningScheduler(DummySliceScheduler):
        def __init__(self, **kwargs: object) -> None:
            super().__init__(**kwargs)
            self.scheduler.running = True

    monkeypatch.setattr("apps.execution_gateway.lifespan.DatabaseClient", DummyDBClient)
    monkeypatch.setattr("apps.execution_gateway.lifespan.RedisClient", DummyRedisClient)
    monkeypatch.setattr("apps.execution_gateway.lifespan.RecoveryManager", DummyRecoveryManager)
    monkeypatch.setattr("apps.execution_gateway.lifespan.SliceScheduler", RunningScheduler)
    monkeypatch.setattr("apps.execution_gateway.lifespan.KillSwitch", lambda redis_client=None: object())
    monkeypatch.setattr(
        "apps.execution_gateway.lifespan.CircuitBreaker", lambda redis_client=None: object()
    )
    monkeypatch.setattr(
        "apps.execution_gateway.lifespan.PositionReservation", lambda redis=None: object()
    )
    monkeypatch.setattr("apps.execution_gateway.lifespan.validate_required_secrets", lambda _: None)
    monkeypatch.setattr("apps.execution_gateway.lifespan.get_required_secret", lambda _: "secret")
    monkeypatch.setattr("apps.execution_gateway.lifespan.get_optional_secret_or_none", lambda *_: None)
    monkeypatch.setattr("apps.execution_gateway.lifespan.get_optional_secret", lambda *_: "")

    def _get_db_pool() -> DummyPool:
        return pool

    _get_db_pool.cache_info = lambda: SimpleNamespace(currsize=1)  # type: ignore[attr-defined]
    monkeypatch.setattr("apps.execution_gateway.api.dependencies.get_db_pool", _get_db_pool)
    monkeypatch.setattr(
        "apps.execution_gateway.lifespan._recover_zombie_slices_after_reconciliation",
        lambda *_: asyncio.sleep(0),
    )

    resources = await startup_execution_gateway(app, settings, {})
    assert resources.recovery_manager.slice_scheduler is not None


@pytest.mark.asyncio
async def test_startup_execution_gateway_shutdown_on_exception(monkeypatch):
    settings = _make_settings(dry_run=True)
    app = FastAPI()
    pool = DummyPool()
    shutdown_called = {"value": False}

    async def _shutdown(_: LifespanResources) -> None:
        shutdown_called["value"] = True

    monkeypatch.setattr("apps.execution_gateway.lifespan.DatabaseClient", DummyDBClient)
    monkeypatch.setattr("apps.execution_gateway.lifespan.RedisClient", DummyRedisClient)
    monkeypatch.setattr("apps.execution_gateway.lifespan.RecoveryManager", DummyRecoveryManager)
    monkeypatch.setattr("apps.execution_gateway.lifespan.SliceScheduler", DummySliceScheduler)
    monkeypatch.setattr("apps.execution_gateway.lifespan.KillSwitch", lambda redis_client=None: object())
    monkeypatch.setattr(
        "apps.execution_gateway.lifespan.CircuitBreaker", lambda redis_client=None: object()
    )
    monkeypatch.setattr(
        "apps.execution_gateway.lifespan.PositionReservation", lambda redis=None: object()
    )
    monkeypatch.setattr("apps.execution_gateway.lifespan.validate_required_secrets", lambda _: None)
    monkeypatch.setattr("apps.execution_gateway.lifespan.get_required_secret", lambda _: "secret")
    monkeypatch.setattr("apps.execution_gateway.lifespan.get_optional_secret_or_none", lambda *_: None)
    monkeypatch.setattr("apps.execution_gateway.lifespan.get_optional_secret", lambda *_: "")
    monkeypatch.setattr("apps.execution_gateway.lifespan.shutdown_execution_gateway", _shutdown)
    monkeypatch.setattr("apps.execution_gateway.lifespan.load_config", lambda: (_ for _ in ()).throw(RuntimeError("boom")))

    def _get_db_pool() -> DummyPool:
        return pool

    _get_db_pool.cache_info = lambda: SimpleNamespace(currsize=1)  # type: ignore[attr-defined]
    monkeypatch.setattr("apps.execution_gateway.api.dependencies.get_db_pool", _get_db_pool)

    with pytest.raises(RuntimeError, match="boom"):
        await startup_execution_gateway(app, settings, {})

    assert shutdown_called["value"] is True


@pytest.mark.asyncio
async def test_shutdown_execution_gateway_pool_close_errors(monkeypatch):
    from psycopg import OperationalError

    class ErrorPool(DummyPool):
        async def close(self) -> None:  # type: ignore[override]
            raise OperationalError("close failed")

    pool = ErrorPool()

    def _get_db_pool() -> ErrorPool:
        return pool

    _get_db_pool.cache_info = lambda: SimpleNamespace(currsize=1)  # type: ignore[attr-defined]
    monkeypatch.setattr("apps.execution_gateway.api.dependencies.get_db_pool", _get_db_pool)
    monkeypatch.setattr("apps.execution_gateway.lifespan.close_secret_manager", lambda: None)

    resources = LifespanResources(
        db_client=DummyDBClient("postgres://test"),
        redis_client=None,
        alpaca_client=None,
        webhook_secret="",
        liquidity_service=None,
        recovery_manager=SimpleNamespace(slice_scheduler=None),  # type: ignore[arg-type]
        reconciliation_service=None,
        reconciliation_task=None,
        zombie_recovery_task=None,
    )

    await shutdown_execution_gateway(resources)


@pytest.mark.asyncio
async def test_shutdown_execution_gateway_pool_state_error(monkeypatch):
    class BadPool:
        pass

    def _get_db_pool() -> BadPool:
        return BadPool()

    _get_db_pool.cache_info = lambda: SimpleNamespace(currsize=1)  # type: ignore[attr-defined]
    monkeypatch.setattr("apps.execution_gateway.api.dependencies.get_db_pool", _get_db_pool)
    monkeypatch.setattr("apps.execution_gateway.lifespan.close_secret_manager", lambda: None)

    resources = LifespanResources(
        db_client=DummyDBClient("postgres://test"),
        redis_client=None,
        alpaca_client=None,
        webhook_secret="",
        liquidity_service=None,
        recovery_manager=SimpleNamespace(slice_scheduler=None),  # type: ignore[arg-type]
        reconciliation_service=None,
        reconciliation_task=None,
        zombie_recovery_task=None,
    )

    await shutdown_execution_gateway(resources)


@pytest.mark.asyncio
async def test_shutdown_execution_gateway(monkeypatch):
    pool = DummyPool()

    def _get_db_pool() -> DummyPool:
        return pool

    _get_db_pool.cache_info = lambda: SimpleNamespace(currsize=1)  # type: ignore[attr-defined]
    monkeypatch.setattr("apps.execution_gateway.api.dependencies.get_db_pool", _get_db_pool)
    monkeypatch.setattr("apps.execution_gateway.lifespan.close_secret_manager", lambda: None)

    scheduler = DummySliceScheduler()
    recovery_manager = SimpleNamespace(slice_scheduler=scheduler)

    async def _long_task() -> None:
        await asyncio.sleep(10)

    reconciliation_task = asyncio.create_task(_long_task())
    zombie_task = asyncio.create_task(_long_task())

    resources = LifespanResources(
        db_client=DummyDBClient("postgres://test"),
        redis_client=None,
        alpaca_client=None,
        webhook_secret="",
        liquidity_service=None,
        recovery_manager=recovery_manager,  # type: ignore[arg-type]
        reconciliation_service=MagicMock(),
        reconciliation_task=reconciliation_task,
        zombie_recovery_task=zombie_task,
    )

    await shutdown_execution_gateway(resources)

    assert scheduler.shutdown_called is True
    assert pool.close_called is True
    assert resources.db_client.closed is True
