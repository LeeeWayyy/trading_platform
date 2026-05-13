"""Unit tests for libs.web_console_services.alert_acknowledgment_store."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from libs.web_console_services.alert_acknowledgment_store import (
    InMemoryAlertAcknowledgmentStore,
    PostgresAlertAcknowledgmentStore,
)


class _FakeCursor:
    def __init__(self, fake_db: _FakeDB) -> None:
        self._db = fake_db
        self._result: tuple[Any, ...] | None = None

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, *_: Any) -> None:
        return None

    def execute(self, sql: str, params: tuple[Any, ...]) -> None:
        normalized = " ".join(sql.split())
        if normalized.startswith("INSERT INTO data_quality_alert_acknowledgments"):
            (
                alert_id,
                dataset,
                metric,
                severity,
                acknowledged_by,
                reason,
                source,
                scope_json,
                _original_alert_json,
            ) = params
            if alert_id in self._db.rows:
                # ON CONFLICT DO NOTHING — RETURNING yields nothing.
                self._result = None
                return
            row = (
                str(uuid4()),
                alert_id,
                dataset,
                metric,
                severity,
                acknowledged_by,
                datetime.now(UTC),
                reason,
                source,
                scope_json,
            )
            self._db.rows[alert_id] = row
            self._result = row
        elif normalized.startswith("SELECT id, alert_id, dataset"):
            (alert_id,) = params
            self._result = self._db.rows.get(alert_id)
        else:
            raise AssertionError(f"unexpected SQL: {sql}")

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._result


class _FakeConnection:
    def __init__(self, fake_db: _FakeDB) -> None:
        self._db = fake_db
        self.committed = False

    def __enter__(self) -> _FakeConnection:
        return self

    def __exit__(self, *_: Any) -> None:
        return None

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self._db)

    def commit(self) -> None:
        self.committed = True


class _FakeDB:
    def __init__(self) -> None:
        self.rows: dict[str, tuple[Any, ...]] = {}


class _FakePool:
    def __init__(self) -> None:
        self.db = _FakeDB()
        self.last_connection: _FakeConnection | None = None

    def connection(self) -> _FakeConnection:
        self.last_connection = _FakeConnection(self.db)
        return self.last_connection


def test_in_memory_store_is_not_persistent() -> None:
    store = InMemoryAlertAcknowledgmentStore()
    assert store.is_persistent is False


def test_in_memory_store_is_idempotent_under_concurrent_writes() -> None:
    """First-write-wins must hold even when DataQualityService runs
    ``acknowledge`` from multiple ``asyncio.to_thread`` workers concurrently.
    Without the lock both threads can pass the ``_records.get is None``
    check, create distinct DTOs, and the later write would silently win.
    """
    import threading as _threading

    store = InMemoryAlertAcknowledgmentStore()
    start = _threading.Barrier(8)
    results: list[Any] = []
    results_lock = _threading.Lock()

    def writer(actor: str) -> None:
        start.wait()
        ack = store.acknowledge(
            alert_id="alert-race",
            dataset="alpaca_sip",
            metric="row_drop",
            severity="warning",
            acknowledged_by=actor,
            reason="triage",
            source="anomaly_alert",
            issue_scope={"dataset": "alpaca_sip"},
        )
        with results_lock:
            results.append(ack)

    threads = [_threading.Thread(target=writer, args=(f"user-{i}",)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Every thread must observe the same acknowledgment row (first-write-wins).
    assert len({ack.id for ack in results}) == 1
    assert all(ack.acknowledged_by == results[0].acknowledged_by for ack in results)


def test_in_memory_store_is_idempotent_on_alert_id() -> None:
    store = InMemoryAlertAcknowledgmentStore()
    first = store.acknowledge(
        alert_id="alert-1",
        dataset="alpaca_sip",
        metric="row_drop",
        severity="warning",
        acknowledged_by="user-1",
        reason="triage",
        source="anomaly_alert",
        issue_scope={"dataset": "alpaca_sip"},
    )
    second = store.acknowledge(
        alert_id="alert-1",
        dataset="alpaca_sip",
        metric="row_drop",
        severity="warning",
        acknowledged_by="user-2",
        reason="ignore",
        source="anomaly_alert",
        issue_scope={"dataset": "alpaca_sip"},
    )
    assert first.id == second.id
    assert second.acknowledged_by == "user-1"  # first writer wins


def test_postgres_store_persists_scope_and_source() -> None:
    pool = _FakePool()
    store = PostgresAlertAcknowledgmentStore(db_pool=pool)  # type: ignore[arg-type]

    ack = store.acknowledge(
        alert_id="alert-42",
        dataset="alpaca_sip",
        metric="row_drop",
        severity="warning",
        acknowledged_by="user-1",
        reason="triage",
        source="manifest_validation",
        issue_scope={"dataset": "alpaca_sip", "metric": "row_drop"},
        original_alert={"foo": "bar"},
    )

    assert store.is_persistent is True
    assert ack.alert_id == "alert-42"
    assert ack.source == "manifest_validation"
    assert ack.issue_scope == {"dataset": "alpaca_sip", "metric": "row_drop"}
    assert pool.last_connection is not None
    assert pool.last_connection.committed is True


def test_postgres_store_is_idempotent_via_on_conflict() -> None:
    pool = _FakePool()
    store = PostgresAlertAcknowledgmentStore(db_pool=pool)  # type: ignore[arg-type]

    first = store.acknowledge(
        alert_id="alert-99",
        dataset="alpaca_sip",
        metric="row_drop",
        severity="warning",
        acknowledged_by="user-1",
        reason="triage",
        source="anomaly_alert",
        issue_scope={"dataset": "alpaca_sip"},
    )
    second = store.acknowledge(
        alert_id="alert-99",
        dataset="alpaca_sip",
        metric="row_drop",
        severity="warning",
        acknowledged_by="user-2",
        reason="ignore",
        source="anomaly_alert",
        issue_scope={"dataset": "alpaca_sip"},
    )

    assert first.id == second.id
    assert second.acknowledged_by == "user-1"


def test_postgres_store_get_returns_existing_record() -> None:
    pool = _FakePool()
    store = PostgresAlertAcknowledgmentStore(db_pool=pool)  # type: ignore[arg-type]

    store.acknowledge(
        alert_id="alert-7",
        dataset="alpaca_sip",
        metric="row_drop",
        severity="warning",
        acknowledged_by="user-1",
        reason="triage",
        source="anomaly_alert",
        issue_scope={"dataset": "alpaca_sip"},
    )

    fetched = store.get("alert-7")
    assert fetched is not None
    assert fetched.alert_id == "alert-7"
    assert fetched.dataset == "alpaca_sip"


def test_postgres_store_get_returns_none_when_missing() -> None:
    pool = _FakePool()
    store = PostgresAlertAcknowledgmentStore(db_pool=pool)  # type: ignore[arg-type]
    assert store.get("does-not-exist") is None


def test_postgres_store_row_to_dto_coerces_non_dict_scope() -> None:
    """If issue_scope arrives as a JSON list/null/garbage, coerce to {}.

    Pydantic v2 strictly rejects non-dict values for `issue_scope`; the store
    must defend against unexpected JSONB shapes so that legacy rows or
    rows written by a different writer cannot crash readers.
    """
    base_row = (
        "id-1",
        "alert-x",
        "alpaca_sip",
        "row_drop",
        "warning",
        "user-1",
        datetime.now(UTC),
        "triage",
        "anomaly_alert",
    )
    json_list = base_row + ("[]",)
    ack_list = PostgresAlertAcknowledgmentStore._row_to_dto(json_list)
    assert ack_list.issue_scope == {}

    json_garbage = base_row + ("not-json",)
    ack_garbage = PostgresAlertAcknowledgmentStore._row_to_dto(json_garbage)
    assert ack_garbage.issue_scope == {}

    none_scope = base_row + (None,)
    ack_none = PostgresAlertAcknowledgmentStore._row_to_dto(none_scope)
    assert ack_none.issue_scope == {}


def test_postgres_store_serializes_scope_as_json_string() -> None:
    """The store must JSON-encode dict scopes for the JSONB cast."""
    pool = _FakePool()
    store = PostgresAlertAcknowledgmentStore(db_pool=pool)  # type: ignore[arg-type]
    store.acknowledge(
        alert_id="alert-json",
        dataset="alpaca_sip",
        metric="row_drop",
        severity="warning",
        acknowledged_by="user-1",
        reason="triage",
        source="anomaly_alert",
        issue_scope={"nested": {"k": "v"}},
    )
    row = pool.db.rows["alert-json"]
    # scope_json is column 9 in the row tuple
    assert isinstance(row[9], str)
    assert json.loads(row[9]) == {"nested": {"k": "v"}}
