"""State management for reconciliation service.

This module handles the startup gate state and override tracking for the
reconciliation service. Thread-safety is ensured via locks.
"""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


class ReconciliationState:
    """Thread-safe state management for reconciliation.

    Manages:
    - Startup gate (blocks trading until reconciliation completes)
    - Override tracking (for forced bypasses with audit context)
    - Last reconciliation result (for forced bypass validation)
    """

    def __init__(self, dry_run: bool = False, timeout_seconds: int = 300) -> None:
        """Initialize reconciliation state.

        Args:
            dry_run: If True, startup is immediately marked complete.
            timeout_seconds: Timeout for startup reconciliation.
        """
        self._dry_run = dry_run
        self._timeout_seconds = timeout_seconds

        self._startup_complete = dry_run  # Dry-run starts complete
        self._startup_started_at = datetime.now(UTC)
        self._override_active = False
        self._override_context: dict[str, Any] = {}
        self._last_reconciliation_result: dict[str, Any] | None = None

        self._lock = threading.Lock()

    @property
    def dry_run(self) -> bool:
        """Whether running in dry-run mode."""
        return self._dry_run

    def is_startup_complete(self) -> bool:
        """Check if startup reconciliation is complete.

        Returns True immediately in dry-run mode.
        """
        if self._dry_run:
            return True
        with self._lock:
            return self._startup_complete

    def startup_elapsed_seconds(self) -> float:
        """Get seconds elapsed since startup began."""
        return (datetime.now(UTC) - self._startup_started_at).total_seconds()

    def startup_timed_out(self) -> bool:
        """Check if startup reconciliation has timed out."""
        return self.startup_elapsed_seconds() > self._timeout_seconds

    def mark_startup_complete(
        self,
        forced: bool = False,
        user_id: str | None = None,
        reason: str | None = None,
    ) -> None:
        """Mark startup reconciliation as complete.

        Args:
            forced: If True, allow bypassing reconciliation gate. SECURITY: Requires
                   that at least one reconciliation attempt was made to prevent
                   completely skipping safety checks.
            user_id: User requesting the override (required if forced=True).
            reason: Reason for the override (required if forced=True).

        Raises:
            ValueError: If forced=True but no reconciliation was ever attempted.
                       This prevents operators from completely skipping safety checks.
        """
        if forced:
            # SECURITY: Require at least one reconciliation attempt before allowing forced bypass
            # This prevents completely skipping safety checks while still allowing emergency
            # scenarios where reconciliation fails but operator needs to proceed
            with self._lock:
                last_result = self._last_reconciliation_result
            if last_result is None:
                raise ValueError(
                    "Cannot force startup complete without running reconciliation first. "
                    "Run reconciliation at least once before using forced bypass. "
                    "This ensures broker state was checked even if reconciliation failed."
                )
            if not user_id or not reason:
                raise ValueError("Both user_id and reason are required for forced startup bypass")
            with self._lock:
                self._override_active = True
                self._override_context = {
                    "user_id": user_id,
                    "reason": reason,
                    "timestamp": datetime.now(UTC).isoformat(),
                    "last_reconciliation_result": last_result,
                }
            logger.warning(
                "Startup reconciliation gate FORCED BYPASSED",
                extra={
                    "user_id": user_id,
                    "reason": reason,
                    "last_reconciliation_result": last_result,
                },
            )
        with self._lock:
            self._startup_complete = True

    def override_active(self) -> bool:
        """Check if a forced override is currently active."""
        with self._lock:
            return self._override_active

    def override_context(self) -> dict[str, Any]:
        """Get the context of the active override.

        Returns a copy to prevent external mutation.
        """
        with self._lock:
            return dict(self._override_context)

    def record_reconciliation_result(self, result: dict[str, Any]) -> None:
        """Record the result of a reconciliation run.

        This is used for forced bypass validation - operators can only
        force bypass after at least one reconciliation attempt.

        Args:
            result: Dict containing status, mode, timestamp, and optionally error.
        """
        with self._lock:
            self._last_reconciliation_result = result

    def get_last_reconciliation_result(self) -> dict[str, Any] | None:
        """Get the last reconciliation result.

        Returns a shallow copy to prevent external mutation.
        """
        with self._lock:
            if self._last_reconciliation_result is None:
                return None
            return dict(self._last_reconciliation_result)

    def open_gate_after_successful_run(self, mode: str) -> bool:
        """Open the startup gate after a successful reconciliation run.

        Args:
            mode: The reconciliation mode (startup, periodic, manual).

        Returns:
            True if the gate was opened by this call, False if already open.
        """
        with self._lock:
            if not self._startup_complete:
                self._startup_complete = True
                logger.info(
                    "Startup reconciliation gate opened after successful run",
                    extra={"mode": mode},
                )
                return True
            return False
