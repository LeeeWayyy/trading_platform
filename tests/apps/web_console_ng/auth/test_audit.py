import asyncio
import json
import logging
import uuid
from unittest.mock import AsyncMock

import pytest

from apps.web_console_ng import config
from apps.web_console_ng.auth import audit as audit_module
from apps.web_console_ng.auth.audit import _AUDIT_REQUEST_ID_NAMESPACE, AuthAuditLogger


class _AcquireContext:
    def __init__(self, conn, enter_exc: Exception | None = None) -> None:
        self._conn = conn
        self._enter_exc = enter_exc

    async def __aenter__(self):
        if self._enter_exc is not None:
            raise self._enter_exc
        return self._conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.fixture(autouse=True)
def _reset_singleton():
    AuthAuditLogger._instance = None


@pytest.fixture()
def _audit_sink_db(monkeypatch):
    monkeypatch.setattr(config, "AUDIT_LOG_SINK", "db")


def _build_logger(db_pool: object | None = object()) -> AuthAuditLogger:
    logger = AuthAuditLogger(db_enabled=True, db_pool=db_pool)
    return logger


@pytest.mark.usefixtures("_audit_sink_db")
def test_log_event_invalid_request_id_includes_raw():
    logger = _build_logger()

    request_id = "not-a-uuid"
    logger.log_event(
        event_type="login",
        user_id=None,
        session_id="abcdefgh1234",
        client_ip="10.0.0.1",
        user_agent="agent",
        auth_type="password",
        outcome="success",
        request_id=request_id,
    )

    assert len(logger._queue) == 1
    payload, attempts = logger._queue[0]
    assert attempts == 0
    assert payload[1] == "login"
    assert payload[2] == "anonymous"
    assert payload[3] == "abcdefgh"

    expected_uuid = uuid.uuid5(_AUDIT_REQUEST_ID_NAMESPACE, request_id)
    assert payload[9] == expected_uuid

    extra_data = json.loads(payload[10])
    assert extra_data["request_id_raw"] == request_id


@pytest.mark.asyncio()
@pytest.mark.usefixtures("_audit_sink_db")
async def test_flush_to_db_writes_payloads(monkeypatch):
    conn = AsyncMock()
    conn.executemany = AsyncMock()

    def fake_acquire(_pool):
        return _AcquireContext(conn)

    monkeypatch.setattr(audit_module, "acquire_connection", fake_acquire)

    logger = _build_logger(db_pool=object())
    logger.log_event(
        event_type="logout",
        user_id="user-1",
        session_id="session-123",
        client_ip="127.0.0.1",
        user_agent="agent",
        auth_type="sso",
        outcome="success",
    )

    await logger._flush_to_db()

    conn.executemany.assert_awaited_once()
    assert len(logger._queue) == 0


@pytest.mark.asyncio()
@pytest.mark.usefixtures("_audit_sink_db")
async def test_flush_to_db_failure_dead_letters(monkeypatch):
    def fake_acquire(_pool):
        return _AcquireContext(None, enter_exc=RuntimeError("boom"))

    monkeypatch.setattr(audit_module, "acquire_connection", fake_acquire)

    logger = _build_logger(db_pool=object())
    logger._max_retries = 1

    logger.log_event(
        event_type="login",
        user_id="user-1",
        session_id="session-123",
        client_ip="127.0.0.1",
        user_agent="agent",
        auth_type="password",
        outcome="failure",
    )

    await logger._flush_to_db()

    assert len(logger._queue) == 0
    assert logger._dead_letter_count == 1
    assert len(logger._dead_letter) == 1


@pytest.mark.asyncio()
@pytest.mark.usefixtures("_audit_sink_db")
async def test_start_disables_db_without_pool(caplog):
    logger = _build_logger(db_pool=None)

    with caplog.at_level(logging.WARNING, logger="audit.auth"):
        await logger.start()

    assert logger._db_enabled is False
    assert logger._flush_task is None
    assert any("audit_db_pool_unavailable_startup" in record.message for record in caplog.records)


# --------------------------------------------------
# Additional tests for improved coverage
# --------------------------------------------------


@pytest.mark.usefixtures("_audit_sink_db")
def test_get_singleton_returns_same_instance():
    """Test get() class method returns singleton and updates db_pool."""
    # First call creates instance
    logger1 = AuthAuditLogger.get(db_enabled=True, db_pool=None)
    assert logger1 is AuthAuditLogger._instance

    # Second call with new db_pool updates the existing instance
    mock_pool = object()
    logger2 = AuthAuditLogger.get(db_enabled=True, db_pool=mock_pool)
    assert logger2 is logger1
    assert logger2._db_pool is mock_pool


@pytest.mark.usefixtures("_audit_sink_db")
def test_get_singleton_enables_db_when_disabled():
    """Test get() enables _db_enabled when called with db_enabled=True on disabled instance."""
    # First call with db_enabled=False
    logger1 = AuthAuditLogger.get(db_enabled=False, db_pool=None)
    assert logger1._db_enabled is False

    # Reset singleton to test the enable path
    AuthAuditLogger._instance = None

    # Create instance with db_enabled=False
    logger2 = AuthAuditLogger.get(db_enabled=False, db_pool=None)
    assert logger2._db_enabled is False

    # Call get with db_enabled=True - should enable it
    logger3 = AuthAuditLogger.get(db_enabled=True, db_pool=None)
    assert logger3 is logger2
    assert logger3._db_enabled is True


@pytest.mark.usefixtures("_audit_sink_db")
def test_set_db_pool_enables_db():
    """Test set_db_pool() enables _db_enabled when pool is provided."""
    logger = _build_logger(db_pool=None)
    logger._db_enabled = False

    mock_pool = object()
    logger.set_db_pool(mock_pool)

    assert logger._db_pool is mock_pool
    assert logger._db_enabled is True


@pytest.mark.asyncio()
@pytest.mark.usefixtures("_audit_sink_db")
async def test_start_creates_flush_task():
    """Test start() creates a flush task when db is enabled with a pool."""
    logger = _build_logger(db_pool=object())

    await logger.start()

    assert logger._flush_task is not None
    assert not logger._flush_task.done()

    # Cleanup
    await logger.stop()


@pytest.mark.asyncio()
@pytest.mark.usefixtures("_audit_sink_db")
async def test_stop_cancels_flush_task_and_flushes(monkeypatch):
    """Test stop() cancels flush task and flushes remaining items."""
    conn = AsyncMock()
    conn.executemany = AsyncMock()

    def fake_acquire(_pool):
        return _AcquireContext(conn)

    monkeypatch.setattr(audit_module, "acquire_connection", fake_acquire)

    logger = _build_logger(db_pool=object())
    await logger.start()
    assert logger._flush_task is not None

    # Add an event to be flushed during stop
    logger.log_event(
        event_type="logout",
        user_id="user-1",
        session_id="session-123",
        client_ip="127.0.0.1",
        user_agent="agent",
        auth_type="sso",
        outcome="success",
    )

    await logger.stop()

    assert logger._flush_task is None
    conn.executemany.assert_awaited()


@pytest.mark.asyncio()
@pytest.mark.usefixtures("_audit_sink_db")
async def test_stop_logs_dead_letter_contents(caplog):
    """Test stop() logs dead letter contents on shutdown."""
    logger = _build_logger(db_pool=object())

    # Manually add an item to dead letter queue
    logger._dead_letter.append(("test_payload",))

    with caplog.at_level(logging.ERROR, logger="audit.auth"):
        await logger.stop()

    assert any("dead-letter queue on shutdown" in record.message for record in caplog.records)


@pytest.mark.usefixtures("_audit_sink_db")
def test_log_event_with_existing_extra_data():
    """Test log_event copies extra_data when request_id is invalid."""
    logger = _build_logger()

    original_extra = {"key": "value"}
    logger.log_event(
        event_type="login",
        user_id=None,
        session_id="abcdefgh1234",
        client_ip="10.0.0.1",
        user_agent="agent",
        auth_type="password",
        outcome="success",
        request_id="not-a-uuid",
        extra_data=original_extra,
    )

    assert len(logger._queue) == 1
    payload, _ = logger._queue[0]
    extra_data = json.loads(payload[10])
    # Should have the raw request_id added
    assert extra_data["request_id_raw"] == "not-a-uuid"
    # Should also have original data
    assert extra_data["key"] == "value"
    # Original dict should not be modified
    assert "request_id_raw" not in original_extra


@pytest.fixture()
def _audit_sink_log(monkeypatch):
    monkeypatch.setattr(config, "AUDIT_LOG_SINK", "log")


@pytest.mark.usefixtures("_audit_sink_log")
def test_log_event_logs_to_json_success(caplog):
    """Test log_event logs JSON to log sink on success."""
    logger = AuthAuditLogger(db_enabled=False)

    with caplog.at_level(logging.INFO, logger="audit.auth"):
        logger.log_event(
            event_type="login",
            user_id="user-1",
            session_id="session-123",
            client_ip="10.0.0.1",
            user_agent="agent",
            auth_type="password",
            outcome="success",
        )

    assert len(caplog.records) == 1
    assert caplog.records[0].levelno == logging.INFO
    event = json.loads(caplog.records[0].message)
    assert event["event_type"] == "login"
    assert event["outcome"] == "success"


@pytest.mark.usefixtures("_audit_sink_log")
def test_log_event_logs_to_json_failure(caplog):
    """Test log_event logs JSON to log sink with WARNING level on failure."""
    logger = AuthAuditLogger(db_enabled=False)

    with caplog.at_level(logging.WARNING, logger="audit.auth"):
        logger.log_event(
            event_type="login",
            user_id="user-1",
            session_id="session-123",
            client_ip="10.0.0.1",
            user_agent="agent",
            auth_type="password",
            outcome="failure",
            failure_reason="bad credentials",
        )

    assert len(caplog.records) == 1
    assert caplog.records[0].levelno == logging.WARNING
    event = json.loads(caplog.records[0].message)
    assert event["outcome"] == "failure"


@pytest.mark.usefixtures("_audit_sink_db")
def test_log_event_queue_overflow(caplog):
    """Test log_event warns on queue overflow."""
    logger = _build_logger()
    # Fill the queue to max
    logger._queue = type(logger._queue)(maxlen=1)
    logger._queue.append(("dummy", 0))

    with caplog.at_level(logging.WARNING, logger="audit.auth"):
        logger.log_event(
            event_type="login",
            user_id="user-1",
            session_id="session-123",
            client_ip="10.0.0.1",
            user_agent="agent",
            auth_type="password",
            outcome="success",
        )

    assert logger._dropped_count == 1
    assert any("audit_queue_overflow" in record.message for record in caplog.records)


@pytest.mark.asyncio()
@pytest.mark.usefixtures("_audit_sink_db")
async def test_flush_loop_flushes_periodically(monkeypatch):
    """Test _flush_loop flushes queue items periodically."""
    conn = AsyncMock()
    conn.executemany = AsyncMock()

    def fake_acquire(_pool):
        return _AcquireContext(conn)

    monkeypatch.setattr(audit_module, "acquire_connection", fake_acquire)

    logger = _build_logger(db_pool=object())
    logger._flush_interval = 0.01  # Very short interval for testing

    logger.log_event(
        event_type="login",
        user_id="user-1",
        session_id="session-123",
        client_ip="10.0.0.1",
        user_agent="agent",
        auth_type="password",
        outcome="success",
    )

    # Start the flush loop
    await logger.start()

    # Wait for at least one flush cycle
    await asyncio.sleep(0.05)

    # Queue should be empty after flush
    assert len(logger._queue) == 0
    conn.executemany.assert_awaited()

    await logger.stop()


@pytest.mark.asyncio()
@pytest.mark.usefixtures("_audit_sink_db")
async def test_flush_to_db_returns_early_on_empty_queue():
    """Test _flush_to_db returns early when queue is empty."""
    logger = _build_logger(db_pool=object())
    assert len(logger._queue) == 0

    # Should return early without error
    await logger._flush_to_db()


@pytest.mark.asyncio()
@pytest.mark.usefixtures("_audit_sink_db")
async def test_flush_to_db_returns_early_when_db_disabled():
    """Test _flush_to_db returns early when db is disabled."""
    logger = _build_logger(db_pool=object())
    logger.log_event(
        event_type="login",
        user_id="user-1",
        session_id="session-123",
        client_ip="10.0.0.1",
        user_agent="agent",
        auth_type="password",
        outcome="success",
    )
    logger._db_enabled = False

    # Should return early without processing
    await logger._flush_to_db()
    assert len(logger._queue) == 1  # Queue unchanged


@pytest.mark.asyncio()
@pytest.mark.usefixtures("_audit_sink_db")
async def test_flush_to_db_returns_early_when_pool_none(caplog):
    """Test _flush_to_db returns early when db_pool is None."""
    logger = _build_logger(db_pool=object())
    logger.log_event(
        event_type="login",
        user_id="user-1",
        session_id="session-123",
        client_ip="10.0.0.1",
        user_agent="agent",
        auth_type="password",
        outcome="success",
    )
    logger._db_pool = None

    with caplog.at_level(logging.DEBUG, logger="audit.auth"):
        await logger._flush_to_db()

    assert len(logger._queue) == 1  # Queue unchanged
    assert any("audit_db_pool_unavailable" in record.message for record in caplog.records)


@pytest.mark.asyncio()
@pytest.mark.usefixtures("_audit_sink_db")
async def test_flush_to_db_handles_cancelled_error(monkeypatch, caplog):
    """Test _flush_to_db re-queues items on CancelledError."""
    conn = AsyncMock()
    conn.executemany = AsyncMock(side_effect=asyncio.CancelledError())

    def fake_acquire(_pool):
        return _AcquireContext(conn)

    monkeypatch.setattr(audit_module, "acquire_connection", fake_acquire)

    logger = _build_logger(db_pool=object())
    logger.log_event(
        event_type="login",
        user_id="user-1",
        session_id="session-123",
        client_ip="10.0.0.1",
        user_agent="agent",
        auth_type="password",
        outcome="success",
    )

    with caplog.at_level(logging.WARNING, logger="audit.auth"):
        with pytest.raises(asyncio.CancelledError):
            await logger._flush_to_db()

    # Items should be re-queued
    assert len(logger._queue) == 1
    assert any("Audit DB flush cancelled" in record.message for record in caplog.records)


@pytest.mark.asyncio()
@pytest.mark.usefixtures("_audit_sink_db")
async def test_flush_to_db_handles_metrics_import_error(monkeypatch, caplog):
    """Test _flush_to_db handles ImportError for metrics module."""
    import sys

    def fake_acquire(_pool):
        return _AcquireContext(None, enter_exc=RuntimeError("boom"))

    monkeypatch.setattr(audit_module, "acquire_connection", fake_acquire)

    logger = _build_logger(db_pool=object())
    logger._max_retries = 1

    logger.log_event(
        event_type="login",
        user_id="user-1",
        session_id="session-123",
        client_ip="10.0.0.1",
        user_agent="agent",
        auth_type="password",
        outcome="failure",
    )

    # Remove the metrics module from sys.modules if it exists
    # and mock the import to fail
    metrics_module = "apps.web_console_ng.core.metrics"
    original_module = sys.modules.get(metrics_module)

    # Create a mock that raises ImportError when accessed
    class RaisingFinder:
        @staticmethod
        def find_module(name, path=None):
            if name == metrics_module:
                return RaisingFinder
            return None

        @staticmethod
        def load_module(name):
            raise ImportError("Mocked import error")

    # Temporarily remove the module and add a failing finder
    if metrics_module in sys.modules:
        del sys.modules[metrics_module]

    sys.meta_path.insert(0, RaisingFinder)
    try:
        with caplog.at_level(logging.WARNING, logger="audit.auth"):
            await logger._flush_to_db()

        # Should have logged warning about metrics
        assert any("Metrics module not available" in record.message for record in caplog.records)
    finally:
        sys.meta_path.remove(RaisingFinder)
        if original_module is not None:
            sys.modules[metrics_module] = original_module


@pytest.mark.asyncio()
@pytest.mark.usefixtures("_audit_sink_db")
async def test_flush_to_db_requeues_on_failure_before_max_retries(monkeypatch, caplog):
    """Test _flush_to_db re-queues items on failure if retries not exhausted."""
    def fake_acquire(_pool):
        return _AcquireContext(None, enter_exc=RuntimeError("boom"))

    monkeypatch.setattr(audit_module, "acquire_connection", fake_acquire)

    logger = _build_logger(db_pool=object())
    logger._max_retries = 3  # Allow retries

    logger.log_event(
        event_type="login",
        user_id="user-1",
        session_id="session-123",
        client_ip="10.0.0.1",
        user_agent="agent",
        auth_type="password",
        outcome="failure",
    )

    # First flush attempt
    await logger._flush_to_db()

    # Should be re-queued, not dead-lettered
    assert len(logger._queue) == 1
    assert len(logger._dead_letter) == 0

    # Check retry count increased
    _, attempts = logger._queue[0]
    assert attempts == 1


@pytest.fixture()
def _audit_sink_both(monkeypatch):
    monkeypatch.setattr(config, "AUDIT_LOG_SINK", "both")


@pytest.mark.usefixtures("_audit_sink_both")
def test_log_event_dual_sink(caplog):
    """Test log_event writes to both log and db sinks."""
    logger = AuthAuditLogger(db_enabled=True, db_pool=object())

    with caplog.at_level(logging.INFO, logger="audit.auth"):
        logger.log_event(
            event_type="login",
            user_id="user-1",
            session_id="session-123",
            client_ip="10.0.0.1",
            user_agent="agent",
            auth_type="password",
            outcome="success",
        )

    # Should log to JSON
    assert len(caplog.records) == 1
    # Should also queue for DB
    assert len(logger._queue) == 1
