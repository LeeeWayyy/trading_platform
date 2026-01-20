"""Tests for reconciliation state management.

These tests cover the ReconciliationState class which handles startup
gate and override tracking with thread-safety.
"""

from __future__ import annotations

import threading
import time

import pytest

from apps.execution_gateway.reconciliation.state import ReconciliationState


class TestReconciliationStateBasic:
    """Basic functionality tests for ReconciliationState."""

    def test_initial_state_not_complete(self) -> None:
        """Initial state should not be complete."""
        state = ReconciliationState(dry_run=False)
        assert state.is_startup_complete() is False

    def test_dry_run_starts_complete(self) -> None:
        """Dry-run mode starts with startup complete."""
        state = ReconciliationState(dry_run=True)
        assert state.is_startup_complete() is True

    def test_mark_startup_complete(self) -> None:
        """mark_startup_complete sets startup to complete."""
        state = ReconciliationState(dry_run=False)
        state.mark_startup_complete()
        assert state.is_startup_complete() is True

    def test_startup_elapsed_seconds(self) -> None:
        """startup_elapsed_seconds returns time since start."""
        state = ReconciliationState(dry_run=False)
        time.sleep(0.1)
        elapsed = state.startup_elapsed_seconds()
        assert elapsed >= 0.1
        assert elapsed < 1.0

    def test_startup_timed_out_false(self) -> None:
        """startup_timed_out returns False when within timeout."""
        state = ReconciliationState(dry_run=False, timeout_seconds=300)
        assert state.startup_timed_out() is False

    def test_startup_timed_out_true(self) -> None:
        """startup_timed_out returns True after timeout."""
        state = ReconciliationState(dry_run=False, timeout_seconds=0)
        time.sleep(0.01)
        assert state.startup_timed_out() is True


class TestReconciliationStateForcedOverride:
    """Tests for forced startup bypass functionality."""

    def test_forced_without_reconciliation_raises(self) -> None:
        """Forced bypass without prior reconciliation raises ValueError."""
        state = ReconciliationState(dry_run=False)
        with pytest.raises(ValueError, match="without running reconciliation first"):
            state.mark_startup_complete(
                forced=True,
                user_id="admin",
                reason="Emergency",
            )

    def test_forced_without_user_id_raises(self) -> None:
        """Forced bypass without user_id raises ValueError."""
        state = ReconciliationState(dry_run=False)
        state.record_reconciliation_result({"status": "failed"})
        with pytest.raises(ValueError, match="user_id and reason are required"):
            state.mark_startup_complete(forced=True, reason="Emergency")

    def test_forced_without_reason_raises(self) -> None:
        """Forced bypass without reason raises ValueError."""
        state = ReconciliationState(dry_run=False)
        state.record_reconciliation_result({"status": "failed"})
        with pytest.raises(ValueError, match="user_id and reason are required"):
            state.mark_startup_complete(forced=True, user_id="admin")

    def test_forced_bypass_succeeds_after_reconciliation(self) -> None:
        """Forced bypass succeeds after recording a reconciliation result."""
        state = ReconciliationState(dry_run=False)
        state.record_reconciliation_result({"status": "failed", "error": "Connection timeout"})
        state.mark_startup_complete(
            forced=True,
            user_id="admin@example.com",
            reason="Emergency market conditions",
        )
        assert state.is_startup_complete() is True
        assert state.override_active() is True

    def test_override_context_contains_details(self) -> None:
        """Override context contains user, reason, and timestamp."""
        state = ReconciliationState(dry_run=False)
        state.record_reconciliation_result({"status": "failed"})
        state.mark_startup_complete(
            forced=True,
            user_id="admin@example.com",
            reason="Emergency bypass",
        )
        context = state.override_context()
        assert context["user_id"] == "admin@example.com"
        assert context["reason"] == "Emergency bypass"
        assert "timestamp" in context
        assert "last_reconciliation_result" in context

    def test_override_context_returns_copy(self) -> None:
        """override_context returns a copy to prevent mutation."""
        state = ReconciliationState(dry_run=False)
        state.record_reconciliation_result({"status": "failed"})
        state.mark_startup_complete(forced=True, user_id="admin", reason="Test")
        context1 = state.override_context()
        context1["mutated"] = True
        context2 = state.override_context()
        assert "mutated" not in context2


class TestReconciliationStateRecording:
    """Tests for reconciliation result recording."""

    def test_record_reconciliation_result(self) -> None:
        """record_reconciliation_result stores the result."""
        state = ReconciliationState(dry_run=False)
        result = {"status": "success", "mode": "startup"}
        state.record_reconciliation_result(result)
        assert state.get_last_reconciliation_result() == result

    def test_record_overwrites_previous(self) -> None:
        """Recording overwrites previous result."""
        state = ReconciliationState(dry_run=False)
        state.record_reconciliation_result({"status": "failed"})
        state.record_reconciliation_result({"status": "success"})
        assert state.get_last_reconciliation_result()["status"] == "success"

    def test_initial_result_is_none(self) -> None:
        """Initial reconciliation result is None."""
        state = ReconciliationState(dry_run=False)
        assert state.get_last_reconciliation_result() is None


class TestReconciliationStateGateOpening:
    """Tests for startup gate opening logic."""

    def test_open_gate_after_successful_run_opens_gate(self) -> None:
        """open_gate_after_successful_run opens the gate."""
        state = ReconciliationState(dry_run=False)
        opened = state.open_gate_after_successful_run("startup")
        assert opened is True
        assert state.is_startup_complete() is True

    def test_open_gate_returns_false_if_already_open(self) -> None:
        """open_gate_after_successful_run returns False if already open."""
        state = ReconciliationState(dry_run=False)
        state.open_gate_after_successful_run("startup")
        opened = state.open_gate_after_successful_run("periodic")
        assert opened is False

    def test_open_gate_in_dry_run_returns_false(self) -> None:
        """In dry-run, gate is already open so returns False."""
        state = ReconciliationState(dry_run=True)
        opened = state.open_gate_after_successful_run("startup")
        # Gate was already open due to dry_run=True
        assert opened is False


class TestReconciliationStateThreadSafety:
    """Thread-safety tests for ReconciliationState."""

    def test_concurrent_startup_complete_checks(self) -> None:
        """Concurrent reads of is_startup_complete are safe."""
        state = ReconciliationState(dry_run=False)
        results = []

        def check_startup() -> None:
            for _ in range(100):
                results.append(state.is_startup_complete())

        threads = [threading.Thread(target=check_startup) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All results should be False (initial state)
        assert all(r is False for r in results)

    def test_concurrent_mark_complete(self) -> None:
        """Concurrent calls to mark_startup_complete are safe."""
        state = ReconciliationState(dry_run=False)
        completed = []

        def mark_complete() -> None:
            state.mark_startup_complete()
            completed.append(True)

        threads = [threading.Thread(target=mark_complete) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(completed) == 10
        assert state.is_startup_complete() is True

    def test_concurrent_record_and_read(self) -> None:
        """Concurrent recording and reading results are safe."""
        state = ReconciliationState(dry_run=False)
        results_read = []

        def record_results() -> None:
            for i in range(100):
                state.record_reconciliation_result({"iteration": i})

        def read_results() -> None:
            for _ in range(100):
                result = state.get_last_reconciliation_result()
                if result:
                    results_read.append(result)

        record_thread = threading.Thread(target=record_results)
        read_thread = threading.Thread(target=read_results)

        record_thread.start()
        read_thread.start()
        record_thread.join()
        read_thread.join()

        # Should have read some results without errors
        # The exact count depends on timing
        assert len(results_read) > 0


class TestReconciliationStateDryRun:
    """Tests for dry-run mode behavior."""

    def test_dry_run_property(self) -> None:
        """dry_run property returns the configured value."""
        state_dry = ReconciliationState(dry_run=True)
        state_live = ReconciliationState(dry_run=False)
        assert state_dry.dry_run is True
        assert state_live.dry_run is False

    def test_dry_run_always_complete(self) -> None:
        """Dry-run mode always reports startup complete."""
        state = ReconciliationState(dry_run=True)
        # Even without calling mark_startup_complete
        assert state.is_startup_complete() is True
