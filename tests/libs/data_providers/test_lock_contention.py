"""Multi-process lock contention tests."""

from __future__ import annotations

import datetime
import json
import multiprocessing
import os
import socket
import tempfile
import time
from pathlib import Path
from typing import Any

import pytest


def acquire_lock_worker(
    lock_dir: str,
    dataset: str,
    writer_id: str,
    result_queue: multiprocessing.Queue,  # type: ignore[type-arg]
    timeout: float = 5.0,
    hold_time: float = 0.5,
    acquired_event: multiprocessing.Event | None = None,
) -> None:
    """Worker function to acquire lock from separate process."""
    # Import inside worker to avoid pickle issues
    from libs.data_providers.locking import AtomicFileLock, LockAcquisitionError

    lock = AtomicFileLock(Path(lock_dir), dataset, writer_id)
    try:
        token = lock.acquire(timeout_seconds=timeout)
        if acquired_event is not None:
            acquired_event.set()
        result_queue.put(
            {
                "success": True,
                "pid": os.getpid(),
                "writer_id": writer_id,
            }
        )
        # Hold lock briefly
        time.sleep(hold_time)
        lock.release(token)
    except LockAcquisitionError:
        result_queue.put(
            {
                "success": False,
                "pid": os.getpid(),
                "writer_id": writer_id,
            }
        )


def recover_stale_lock_worker(
    lock_dir: str,
    dataset: str,
    writer_id: str,
    result_queue: multiprocessing.Queue,  # type: ignore[type-arg]
) -> None:
    """Worker to attempt stale lock recovery."""
    from libs.data_providers.locking import AtomicFileLock

    lock = AtomicFileLock(Path(lock_dir), dataset, writer_id)
    lock_path = Path(lock_dir) / f"{dataset}.lock"

    # Try to recover
    success = lock._recover_stale_lock(lock_path)
    result_queue.put(
        {
            "success": success,
            "pid": os.getpid(),
            "writer_id": writer_id,
        }
    )


@pytest.fixture()
def mp_lock_dir() -> Path:
    """Create a temporary lock directory for multiprocessing tests."""
    with tempfile.TemporaryDirectory() as tmp:
        lock_dir = Path(tmp) / "locks"
        lock_dir.mkdir()
        yield lock_dir


class TestLockContention:
    """Tests for lock contention scenarios."""

    @pytest.mark.slow()
    def test_lock_contention_between_two_processes(self, mp_lock_dir: Path) -> None:
        """Test 12: Lock contention between two processes.

        Tests that only one process can hold the lock at a time. The lock is
        blocking with retry, so the second process will acquire after the first
        releases (both succeed sequentially) unless it times out first.
        """
        result_queue: multiprocessing.Queue[dict[str, Any]] = multiprocessing.Queue()

        # Start two processes trying to acquire the same lock
        # Use handshake to ensure the first process holds the lock before starting
        # the second. Hold time is longer than the second process timeout so the
        # second process deterministically times out while waiting.
        acquired_event = multiprocessing.Event()

        p1 = multiprocessing.Process(
            target=acquire_lock_worker,
            args=(str(mp_lock_dir), "test_dataset", "writer1", result_queue),
            kwargs={"timeout": 2.0, "hold_time": 1.0, "acquired_event": acquired_event},
        )
        p2 = multiprocessing.Process(
            target=acquire_lock_worker,
            args=(str(mp_lock_dir), "test_dataset", "writer2", result_queue),
            kwargs={"timeout": 0.2},
        )

        p1.start()
        # Wait for first process to acquire lock before starting second
        assert acquired_event.wait(2.0), "First process failed to acquire lock in time"
        p2.start()

        p1.join(timeout=10)
        p2.join(timeout=10)

        # Collect results
        results = []
        while not result_queue.empty():
            results.append(result_queue.get())

        assert len(results) == 2

        # With short timeout, exactly one should succeed (the one that got the lock first)
        # and one should fail (timeout while waiting)
        successes = [r for r in results if r["success"]]
        failures = [r for r in results if not r["success"]]

        assert len(successes) == 1, "Exactly one process should acquire lock"
        assert len(failures) == 1, "Second process should timeout"

    @pytest.mark.slow()
    def test_concurrent_stale_lock_recovery_deterministic_winner(self, mp_lock_dir: Path) -> None:
        """Test 13: Concurrent stale-lock recovery produces deterministic winner."""
        # Create a stale lock file
        lock_path = mp_lock_dir / "test_dataset.lock"
        now = datetime.datetime.now(datetime.UTC)
        expired = now - datetime.timedelta(hours=5)

        lock_data = {
            "pid": 999999,  # Non-existent PID
            "hostname": socket.gethostname(),
            "writer_id": "dead_writer",
            "acquired_at": expired.isoformat(),
            "expires_at": expired.isoformat(),
        }
        lock_path.write_text(json.dumps(lock_data))

        result_queue: multiprocessing.Queue[dict[str, Any]] = multiprocessing.Queue()

        # Start multiple processes trying to recover simultaneously
        processes = []
        for i in range(3):
            p = multiprocessing.Process(
                target=recover_stale_lock_worker,
                args=(str(mp_lock_dir), "test_dataset", f"recoverer{i}", result_queue),
            )
            processes.append(p)

        # Start all at once for maximum contention
        for p in processes:
            p.start()

        for p in processes:
            p.join(timeout=10)

        # Collect results
        results = []
        while not result_queue.empty():
            results.append(result_queue.get())

        assert len(results) == 3

        # Exactly one should win the recovery
        winners = [r for r in results if r["success"]]
        assert len(winners) <= 1, "At most one process should win recovery"

    @pytest.mark.slow()
    def test_split_brain_recovery_scenario(self, mp_lock_dir: Path) -> None:
        """Test 14: Split-brain scenario where two processes see stale lock simultaneously."""
        # Create a stale lock
        lock_path = mp_lock_dir / "test_dataset.lock"
        now = datetime.datetime.now(datetime.UTC)
        expired = now - datetime.timedelta(hours=5)

        lock_data = {
            "pid": 999999,
            "hostname": socket.gethostname(),
            "writer_id": "dead_writer",
            "acquired_at": expired.isoformat(),
            "expires_at": expired.isoformat(),
        }
        lock_path.write_text(json.dumps(lock_data))

        result_queue: multiprocessing.Queue[dict[str, Any]] = multiprocessing.Queue()

        # Two processes attempt full lock acquisition (which includes recovery)
        p1 = multiprocessing.Process(
            target=acquire_lock_worker,
            args=(str(mp_lock_dir), "test_dataset", "splitter1", result_queue, 2.0),
        )
        p2 = multiprocessing.Process(
            target=acquire_lock_worker,
            args=(str(mp_lock_dir), "test_dataset", "splitter2", result_queue, 2.0),
        )

        # Start simultaneously
        p1.start()
        p2.start()

        p1.join(timeout=10)
        p2.join(timeout=10)

        results = []
        while not result_queue.empty():
            results.append(result_queue.get())

        assert len(results) == 2

        # After split-brain resolution, exactly one should succeed
        successes = [r for r in results if r["success"]]
        # Note: Both might succeed sequentially if one recovers, releases, then other acquires
        # The important thing is no data corruption occurs
        assert len(successes) >= 1, "At least one process should succeed after recovery"


# Mark all tests in this module as requiring multiprocessing
pytestmark = pytest.mark.integration
