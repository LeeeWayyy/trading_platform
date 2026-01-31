"""Safety gate for order actions with fail-open/fail-closed policies.

This module provides centralized safety checking for trading operations,
distinguishing between risk-reducing (fail-open) and risk-increasing (fail-closed)
actions.

Example:
    gate = SafetyGate(client, user_id, user_role, strategies)

    # Pre-check with cached state (instant UI response)
    result = await gate.check(
        policy=SafetyPolicy.FAIL_OPEN,
        cached_kill_switch=context.cached_kill_switch_engaged,
        cached_connection_state=context.cached_connection_state,
    )

    # Confirm-time check with API verification
    result = await gate.check_with_api_verification(
        policy=SafetyPolicy.FAIL_CLOSED,
        cached_connection_state=context.cached_connection_state,
    )
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

import httpx

from apps.web_console_ng.core.client import AsyncTradingClient

logger = logging.getLogger(__name__)


class SafetyPolicy(Enum):
    """Safety gate behavior policy."""

    FAIL_OPEN = "fail_open"  # Risk-reducing: allow on uncertainty
    FAIL_CLOSED = "fail_closed"  # Risk-increasing: block on uncertainty


@dataclass(frozen=True)
class SafetyCheckResult:
    """Result of safety gate check."""

    allowed: bool
    reason: str | None
    warnings: list[str]


# Connection state constants (aligned with order_entry_context.py)
READ_ONLY_STATES = {"DISCONNECTED", "RECONNECTING", "DEGRADED"}
KNOWN_GOOD_STATES = {"CONNECTED"}

# Kill switch state constants
KILL_SWITCH_ENGAGED_STATES = {"ENGAGED"}
KILL_SWITCH_SAFE_STATES = {"DISENGAGED", "OFF", "INACTIVE"}

# Circuit breaker state constants
CIRCUIT_BREAKER_TRIPPED_STATES = {"TRIPPED", "ENGAGED", "ON", "QUIET_PERIOD"}
CIRCUIT_BREAKER_SAFE_STATES = {"OPEN", "OFF", "INACTIVE", "DISENGAGED"}


class SafetyGate:
    """Reusable safety gate for order actions.

    Centralizes the complex logic for:
    - Cached state pre-check (instant UI response)
    - API validation with 5xx vs 4xx handling
    - Policy-based fail-open/fail-closed behavior

    IMPLEMENTATION NOTE: Startup Race Condition
    - Fail-closed actions require cached states to be available
    - Ensure OrderEntryContext._fetch_initial_safety_state() completes before
      enabling risk-increasing actions (Reverse, One-Click)
    - Consider adding an `is_ready` property that returns False until initial
      state fetch completes, and block fail-closed actions when not ready
    """

    def __init__(
        self,
        client: AsyncTradingClient,
        user_id: str,
        user_role: str,
        strategies: list[str] | None = None,
    ):
        """Initialize safety gate.

        Args:
            client: Trading client for API calls
            user_id: User ID for authentication
            user_role: User role (trader, admin, viewer)
            strategies: Strategy scope for multi-strategy users
        """
        self._client = client
        self._user_id = user_id
        self._user_role = user_role
        self._strategies = strategies

    async def check(
        self,
        *,
        policy: SafetyPolicy,
        cached_kill_switch: bool | None = None,
        cached_connection_state: str | None = None,
        cached_circuit_breaker: bool | None = None,
        require_connected: bool = True,
    ) -> SafetyCheckResult:
        """Check all safety gates with specified policy.

        CRITICAL: For FAIL_CLOSED policy, unknown/None state = BLOCKED.
        This ensures risk-increasing actions truly fail closed on ANY uncertainty.

        Args:
            policy: FAIL_OPEN for risk-reducing, FAIL_CLOSED for risk-increasing
            cached_kill_switch: Cached kill switch state from OrderEntryContext
            cached_connection_state: Cached connection state from OrderEntryContext
            cached_circuit_breaker: Cached circuit breaker state from OrderEntryContext
            require_connected: Whether to check connection state

        Returns:
            SafetyCheckResult with allowed status and any warnings
        """
        warnings: list[str] = []

        # 1. Connection state check (normalize to uppercase for case-insensitive comparison)
        conn_state_upper = (cached_connection_state or "").upper()
        if require_connected:
            if cached_connection_state is None or conn_state_upper == "UNKNOWN":
                if policy == SafetyPolicy.FAIL_CLOSED:
                    return SafetyCheckResult(
                        False, "Connection state unknown - cannot proceed", []
                    )
                warnings.append("Connection state unknown - proceeding with caution")
            elif conn_state_upper in READ_ONLY_STATES:
                if policy == SafetyPolicy.FAIL_CLOSED:
                    return SafetyCheckResult(
                        False, f"Connection {cached_connection_state}", []
                    )
                warnings.append(
                    f"Connection {cached_connection_state} - proceeding with caution"
                )
            # Handle unrecognized states (not in KNOWN_GOOD_STATES or READ_ONLY_STATES)
            elif conn_state_upper not in KNOWN_GOOD_STATES:
                if policy == SafetyPolicy.FAIL_CLOSED:
                    return SafetyCheckResult(
                        False, f"Connection state '{cached_connection_state}' unrecognized (FAIL-CLOSED)", []
                    )
                # FAIL_OPEN: Warn on unrecognized state for visibility (mirrors API verification)
                warnings.append(
                    f"Connection state '{cached_connection_state}' unrecognized - proceeding (risk-reducing)"
                )

        # 2. Kill switch check (FAIL_CLOSED blocks; FAIL_OPEN allows risk reduction)
        if cached_kill_switch is None:
            if policy == SafetyPolicy.FAIL_CLOSED:
                return SafetyCheckResult(
                    False, "Kill switch state unknown - cannot proceed", []
                )
            warnings.append("Kill switch state unknown - proceeding with caution")
        elif cached_kill_switch is True:
            if policy == SafetyPolicy.FAIL_CLOSED:
                return SafetyCheckResult(False, "Kill Switch is ENGAGED", [])
            # FAIL_OPEN: Allow risk-reducing actions (flatten/cancel) during kill switch
            warnings.append("Kill switch ENGAGED - allowed for risk reduction")

        # 3. Circuit breaker check (FAIL_CLOSED blocks on unknown)
        if cached_circuit_breaker is None:
            if policy == SafetyPolicy.FAIL_CLOSED:
                return SafetyCheckResult(
                    False, "Circuit breaker state unknown - cannot proceed", []
                )
            warnings.append("Circuit breaker state unknown - proceeding with caution")
        elif cached_circuit_breaker is True:
            if policy == SafetyPolicy.FAIL_CLOSED:
                return SafetyCheckResult(False, "Circuit breaker is TRIPPED", [])
            warnings.append("Circuit breaker TRIPPED - allowed for risk reduction")

        return SafetyCheckResult(True, None, warnings)

    async def check_with_api_verification(
        self,
        *,
        policy: SafetyPolicy,
        cached_connection_state: str | None = None,
    ) -> SafetyCheckResult:
        """Check safety gates with fresh API verification at confirm time.

        Used in confirmation dialogs to ensure state hasn't changed.

        For FAIL_CLOSED actions: Verifies kill switch, circuit breaker, AND connection state.
        For FAIL_OPEN actions: Verifies BOTH but only warns on circuit breaker (doesn't block).

        Args:
            policy: FAIL_OPEN for risk-reducing, FAIL_CLOSED for risk-increasing
            cached_connection_state: Current connection state for verification

        Returns:
            SafetyCheckResult with allowed status and any warnings
        """
        warnings: list[str] = []

        # 0. Connection state check
        # CRITICAL: Must re-check connection at confirm time for risk-increasing actions
        # FAIL-CLOSED: Block on unknown/None state (uncertainty = block)
        if policy == SafetyPolicy.FAIL_CLOSED:
            # Block on None/unknown (fail-closed means uncertainty = block)
            if cached_connection_state is None:
                return SafetyCheckResult(
                    False, "Connection state unknown (FAIL-CLOSED)", []
                )
            state_upper = cached_connection_state.upper()
            if state_upper in READ_ONLY_STATES:
                return SafetyCheckResult(
                    False,
                    f"Connection is {cached_connection_state} (FAIL-CLOSED)",
                    [],
                )
            if state_upper not in KNOWN_GOOD_STATES:
                return SafetyCheckResult(
                    False,
                    f"Connection state '{cached_connection_state}' unknown (FAIL-CLOSED)",
                    [],
                )
        else:
            # FAIL_OPEN: Warn but proceed for risk-reducing actions
            # CRITICAL: Also warn on None/UNKNOWN to maintain visibility
            if cached_connection_state is None:
                warnings.append("Connection state unknown - proceeding (risk-reducing)")
            elif cached_connection_state.upper() in READ_ONLY_STATES:
                warnings.append(
                    f"Connection is {cached_connection_state} - proceeding (risk-reducing)"
                )
            elif cached_connection_state.upper() not in KNOWN_GOOD_STATES:
                warnings.append(
                    f"Connection state '{cached_connection_state}' unknown - proceeding (risk-reducing)"
                )

        # 1. Fresh kill switch check via API
        # Error handling mirrors positions_grid.py logic:
        # - FAIL_OPEN (risk-reducing): 5xx = warn & proceed, 4xx = block (invalid request)
        # - FAIL_CLOSED (risk-increasing): any error = block
        try:
            ks = await self._client.fetch_kill_switch_status(
                self._user_id, role=self._user_role, strategies=self._strategies
            )
            ks_state = str(ks.get("state", "")).upper()
            if ks_state in KILL_SWITCH_ENGAGED_STATES:
                if policy == SafetyPolicy.FAIL_CLOSED:
                    return SafetyCheckResult(False, "Kill Switch engaged", [])
                # FAIL_OPEN: Allow risk-reducing actions (flatten/cancel) during kill switch
                warnings.append("Kill switch ENGAGED - allowed for risk reduction")
            elif ks_state not in KILL_SWITCH_SAFE_STATES:
                # Unknown state handling
                if policy == SafetyPolicy.FAIL_CLOSED:
                    return SafetyCheckResult(
                        False, f"Kill switch state unknown: '{ks_state}' (FAIL-CLOSED)", []
                    )
                # FAIL_OPEN: Warn but proceed (LOW issue fix)
                warnings.append(f"Kill switch state unknown: '{ks_state}' - proceeding (risk-reducing)")
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status >= 500:
                # 5xx = Server error (transient)
                if policy == SafetyPolicy.FAIL_CLOSED:
                    return SafetyCheckResult(
                        False, f"Kill switch check failed (HTTP {status})", []
                    )
                # FAIL_OPEN: Allow risk-reducing action when safety service has transient issues
                warnings.append(
                    f"Kill switch service error ({status}) - proceeding (risk-reducing)"
                )
            else:
                # 4xx = Client error (invalid/unauthorized)
                if policy == SafetyPolicy.FAIL_CLOSED:
                    # FAIL_CLOSED: Block on any error (uncertainty = block)
                    return SafetyCheckResult(
                        False,
                        f"Kill switch check failed (HTTP {status} - invalid request)",
                        [],
                    )
                # FAIL_OPEN: Warn but allow risk-reducing action
                # Rationale: Safety service misconfiguration shouldn't prevent panic button
                warnings.append(
                    f"Kill switch check error (HTTP {status}) - proceeding (risk-reducing)"
                )
        except httpx.RequestError:
            if policy == SafetyPolicy.FAIL_CLOSED:
                return SafetyCheckResult(False, "Kill switch service unreachable", [])
            # FAIL_OPEN: Allow risk-reducing action when safety service is unreachable
            warnings.append("Kill switch service unreachable - proceeding (risk-reducing)")
        except Exception as exc:
            # Catch-all for unexpected errors (bad JSON, ValueError, etc.)
            if policy == SafetyPolicy.FAIL_CLOSED:
                return SafetyCheckResult(False, f"Kill switch check error: {exc}", [])
            warnings.append(f"Kill switch check error ({exc}) - proceeding (risk-reducing)")

        # 2. Fresh circuit breaker check via API
        # FAIL_CLOSED: Block on tripped or error
        # FAIL_OPEN: Warn on tripped (don't block risk-reducing actions), ignore errors
        try:
            cb = await self._client.fetch_circuit_breaker_status(
                self._user_id, role=self._user_role, strategies=self._strategies
            )
            cb_state = str(cb.get("state", "")).upper()
            if cb_state in CIRCUIT_BREAKER_TRIPPED_STATES:
                if policy == SafetyPolicy.FAIL_CLOSED:
                    return SafetyCheckResult(False, f"Circuit breaker is {cb_state}", [])
                # FAIL_OPEN: Warn but allow risk-reducing action
                warnings.append(f"Warning: Circuit breaker is {cb_state}")
            elif cb_state not in CIRCUIT_BREAKER_SAFE_STATES:
                # Unknown state handling
                if policy == SafetyPolicy.FAIL_CLOSED:
                    return SafetyCheckResult(
                        False, f"Circuit breaker state unknown: '{cb_state}' (FAIL-CLOSED)", []
                    )
                # FAIL_OPEN: Warn but proceed (LOW issue fix)
                warnings.append(f"Circuit breaker state unknown: '{cb_state}' - proceeding (risk-reducing)")
        except httpx.HTTPStatusError as exc:
            if policy == SafetyPolicy.FAIL_CLOSED:
                # FAIL_CLOSED: Block on any CB verification failure
                return SafetyCheckResult(
                    False,
                    f"Circuit breaker check failed (HTTP {exc.response.status_code})",
                    [],
                )
            # FAIL_OPEN: Allow risk-reducing action on CB service error
            warnings.append(
                f"Circuit breaker service error ({exc.response.status_code}) - proceeding"
            )
        except httpx.RequestError:
            if policy == SafetyPolicy.FAIL_CLOSED:
                return SafetyCheckResult(
                    False, "Circuit breaker service unreachable", []
                )
            # FAIL_OPEN: Allow risk-reducing action
            warnings.append("Circuit breaker service unreachable - proceeding")
        except Exception as exc:
            # Catch-all for unexpected errors (bad JSON, ValueError, etc.)
            if policy == SafetyPolicy.FAIL_CLOSED:
                return SafetyCheckResult(False, f"Circuit breaker check error: {exc}", [])
            warnings.append(f"Circuit breaker check error ({exc}) - proceeding (risk-reducing)")

        return SafetyCheckResult(True, None, warnings)


__all__ = [
    "SafetyGate",
    "SafetyPolicy",
    "SafetyCheckResult",
    "READ_ONLY_STATES",
    "KNOWN_GOOD_STATES",
    "KILL_SWITCH_ENGAGED_STATES",
    "KILL_SWITCH_SAFE_STATES",
    "CIRCUIT_BREAKER_TRIPPED_STATES",
    "CIRCUIT_BREAKER_SAFE_STATES",
]
