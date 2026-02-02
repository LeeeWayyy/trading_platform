"""Tests for SafetyGate component.

Tests cover:
- SafetyPolicy enum values
- SafetyCheckResult dataclass
- SafetyGate.check() with FAIL_OPEN and FAIL_CLOSED policies
- SafetyGate.check_with_api_verification() with both policies
- Connection state handling
- Kill switch state handling
- Circuit breaker state handling
- Error handling for API calls
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from apps.web_console_ng.components.safety_gate import (
    KNOWN_GOOD_STATES,
    READ_ONLY_STATES,
    SafetyCheckResult,
    SafetyGate,
    SafetyPolicy,
)


class TestSafetyPolicy:
    """Tests for SafetyPolicy enum."""

    def test_fail_open_value(self) -> None:
        assert SafetyPolicy.FAIL_OPEN.value == "fail_open"

    def test_fail_closed_value(self) -> None:
        assert SafetyPolicy.FAIL_CLOSED.value == "fail_closed"


class TestSafetyCheckResult:
    """Tests for SafetyCheckResult dataclass."""

    def test_allowed_result(self) -> None:
        result = SafetyCheckResult(allowed=True, reason=None, warnings=[])
        assert result.allowed is True
        assert result.reason is None
        assert result.warnings == []

    def test_blocked_result_with_reason(self) -> None:
        result = SafetyCheckResult(allowed=False, reason="Kill switch engaged", warnings=[])
        assert result.allowed is False
        assert result.reason == "Kill switch engaged"

    def test_allowed_with_warnings(self) -> None:
        warnings = ["Connection state unknown", "Circuit breaker tripped"]
        result = SafetyCheckResult(allowed=True, reason=None, warnings=warnings)
        assert result.allowed is True
        assert len(result.warnings) == 2


class TestConnectionStateConstants:
    """Tests for connection state constants."""

    def test_read_only_states(self) -> None:
        assert "DISCONNECTED" in READ_ONLY_STATES
        assert "RECONNECTING" in READ_ONLY_STATES
        assert "DEGRADED" in READ_ONLY_STATES
        assert "CONNECTED" not in READ_ONLY_STATES

    def test_known_good_states(self) -> None:
        assert "CONNECTED" in KNOWN_GOOD_STATES
        assert len(KNOWN_GOOD_STATES) == 1


class TestSafetyGateCheck:
    """Tests for SafetyGate.check() method."""

    @pytest.fixture()
    def mock_client(self) -> AsyncMock:
        return AsyncMock()

    @pytest.fixture()
    def gate(self, mock_client: AsyncMock) -> SafetyGate:
        return SafetyGate(
            client=mock_client,
            user_id="test_user",
            user_role="trader",
            strategies=["alpha_baseline"],
        )

    @pytest.mark.asyncio()
    async def test_fail_open_all_safe(self, gate: SafetyGate) -> None:
        """FAIL_OPEN with all safe states should allow."""
        result = await gate.check(
            policy=SafetyPolicy.FAIL_OPEN,
            cached_kill_switch=False,
            cached_connection_state="CONNECTED",
            cached_circuit_breaker=False,
        )
        assert result.allowed is True
        assert result.reason is None
        assert result.warnings == []

    @pytest.mark.asyncio()
    async def test_fail_open_unknown_connection_warns(self, gate: SafetyGate) -> None:
        """FAIL_OPEN with unknown connection should warn but allow."""
        result = await gate.check(
            policy=SafetyPolicy.FAIL_OPEN,
            cached_kill_switch=False,
            cached_connection_state=None,
            cached_circuit_breaker=False,
        )
        assert result.allowed is True
        assert "Connection state unknown" in result.warnings[0]

    @pytest.mark.asyncio()
    async def test_fail_open_disconnected_warns(self, gate: SafetyGate) -> None:
        """FAIL_OPEN with disconnected state should warn but allow."""
        result = await gate.check(
            policy=SafetyPolicy.FAIL_OPEN,
            cached_kill_switch=False,
            cached_connection_state="DISCONNECTED",
            cached_circuit_breaker=False,
        )
        assert result.allowed is True
        assert "DISCONNECTED" in result.warnings[0]

    @pytest.mark.asyncio()
    async def test_fail_open_kill_switch_engaged_warns(self, gate: SafetyGate) -> None:
        """FAIL_OPEN with kill switch engaged should warn but allow (risk-reducing)."""
        result = await gate.check(
            policy=SafetyPolicy.FAIL_OPEN,
            cached_kill_switch=True,
            cached_connection_state="CONNECTED",
            cached_circuit_breaker=False,
        )
        assert result.allowed is True
        assert any("ENGAGED" in w for w in result.warnings)

    @pytest.mark.asyncio()
    async def test_fail_open_circuit_breaker_tripped_warns(self, gate: SafetyGate) -> None:
        """FAIL_OPEN with circuit breaker tripped should warn but allow."""
        result = await gate.check(
            policy=SafetyPolicy.FAIL_OPEN,
            cached_kill_switch=False,
            cached_connection_state="CONNECTED",
            cached_circuit_breaker=True,
        )
        assert result.allowed is True
        assert "Circuit breaker TRIPPED" in result.warnings[0]

    @pytest.mark.asyncio()
    async def test_fail_closed_all_safe(self, gate: SafetyGate) -> None:
        """FAIL_CLOSED with all safe states should allow."""
        result = await gate.check(
            policy=SafetyPolicy.FAIL_CLOSED,
            cached_kill_switch=False,
            cached_connection_state="CONNECTED",
            cached_circuit_breaker=False,
        )
        assert result.allowed is True
        assert result.reason is None
        assert result.warnings == []

    @pytest.mark.asyncio()
    async def test_fail_closed_unknown_connection_blocks(self, gate: SafetyGate) -> None:
        """FAIL_CLOSED with unknown connection should block."""
        result = await gate.check(
            policy=SafetyPolicy.FAIL_CLOSED,
            cached_kill_switch=False,
            cached_connection_state=None,
            cached_circuit_breaker=False,
        )
        assert result.allowed is False
        assert "Connection state unknown" in result.reason

    @pytest.mark.asyncio()
    async def test_fail_closed_disconnected_blocks(self, gate: SafetyGate) -> None:
        """FAIL_CLOSED with disconnected state should block."""
        result = await gate.check(
            policy=SafetyPolicy.FAIL_CLOSED,
            cached_kill_switch=False,
            cached_connection_state="DISCONNECTED",
            cached_circuit_breaker=False,
        )
        assert result.allowed is False
        assert "DISCONNECTED" in result.reason

    @pytest.mark.asyncio()
    async def test_fail_closed_unknown_kill_switch_blocks(self, gate: SafetyGate) -> None:
        """FAIL_CLOSED with unknown kill switch should block."""
        result = await gate.check(
            policy=SafetyPolicy.FAIL_CLOSED,
            cached_kill_switch=None,
            cached_connection_state="CONNECTED",
            cached_circuit_breaker=False,
        )
        assert result.allowed is False
        assert "Kill switch state unknown" in result.reason

    @pytest.mark.asyncio()
    async def test_fail_closed_unknown_circuit_breaker_blocks(self, gate: SafetyGate) -> None:
        """FAIL_CLOSED with unknown circuit breaker should block."""
        result = await gate.check(
            policy=SafetyPolicy.FAIL_CLOSED,
            cached_kill_switch=False,
            cached_connection_state="CONNECTED",
            cached_circuit_breaker=None,
        )
        assert result.allowed is False
        assert "Circuit breaker state unknown" in result.reason

    @pytest.mark.asyncio()
    async def test_fail_closed_circuit_breaker_tripped_blocks(self, gate: SafetyGate) -> None:
        """FAIL_CLOSED with circuit breaker tripped should block."""
        result = await gate.check(
            policy=SafetyPolicy.FAIL_CLOSED,
            cached_kill_switch=False,
            cached_connection_state="CONNECTED",
            cached_circuit_breaker=True,
        )
        assert result.allowed is False
        assert "Circuit breaker is TRIPPED" in result.reason

    @pytest.mark.asyncio()
    async def test_fail_closed_lowercase_unknown_connection_blocks(self, gate: SafetyGate) -> None:
        """FAIL_CLOSED with lowercase 'unknown' connection should block (case-insensitive)."""
        result = await gate.check(
            policy=SafetyPolicy.FAIL_CLOSED,
            cached_kill_switch=False,
            cached_connection_state="unknown",  # lowercase
            cached_circuit_breaker=False,
        )
        assert result.allowed is False
        assert "Connection state unknown" in result.reason

    @pytest.mark.asyncio()
    async def test_fail_closed_uppercase_unknown_connection_blocks(self, gate: SafetyGate) -> None:
        """FAIL_CLOSED with uppercase 'UNKNOWN' connection should block."""
        result = await gate.check(
            policy=SafetyPolicy.FAIL_CLOSED,
            cached_kill_switch=False,
            cached_connection_state="UNKNOWN",
            cached_circuit_breaker=False,
        )
        assert result.allowed is False
        assert "Connection state unknown" in result.reason

    @pytest.mark.asyncio()
    async def test_fail_closed_unrecognized_connection_state_blocks(self, gate: SafetyGate) -> None:
        """FAIL_CLOSED with unrecognized connection state should block."""
        result = await gate.check(
            policy=SafetyPolicy.FAIL_CLOSED,
            cached_kill_switch=False,
            cached_connection_state="DELAYED",  # Not in KNOWN_GOOD_STATES or READ_ONLY_STATES
            cached_circuit_breaker=False,
        )
        assert result.allowed is False
        assert "unrecognized" in result.reason.lower()

    @pytest.mark.asyncio()
    async def test_fail_open_unrecognized_connection_state_warns(self, gate: SafetyGate) -> None:
        """FAIL_OPEN with unrecognized connection state should warn but allow (risk-reducing)."""
        result = await gate.check(
            policy=SafetyPolicy.FAIL_OPEN,
            cached_kill_switch=False,
            cached_connection_state="DELAYED",  # Unrecognized state
            cached_circuit_breaker=False,
        )
        # FAIL_OPEN allows unrecognized states for risk-reducing actions with warning
        assert result.allowed is True
        assert any("unrecognized" in w.lower() for w in result.warnings)


class TestSafetyGateCheckWithApiVerification:
    """Tests for SafetyGate.check_with_api_verification() method."""

    @pytest.fixture()
    def mock_client(self) -> AsyncMock:
        client = AsyncMock()
        # Default to safe states (DISENGAGED for kill switch, OPEN for circuit breaker)
        client.fetch_kill_switch_status.return_value = {"state": "DISENGAGED"}
        client.fetch_circuit_breaker_status.return_value = {"state": "OPEN"}
        return client

    @pytest.fixture()
    def gate(self, mock_client: AsyncMock) -> SafetyGate:
        return SafetyGate(
            client=mock_client,
            user_id="test_user",
            user_role="trader",
            strategies=["alpha_baseline"],
        )

    @pytest.mark.asyncio()
    async def test_fail_open_api_safe(self, gate: SafetyGate) -> None:
        """FAIL_OPEN with safe API responses should allow."""
        result = await gate.check_with_api_verification(
            policy=SafetyPolicy.FAIL_OPEN,
            cached_connection_state="CONNECTED",
        )
        assert result.allowed is True
        assert result.warnings == []

    @pytest.mark.asyncio()
    async def test_fail_open_kill_switch_engaged_warns(
        self, gate: SafetyGate, mock_client: AsyncMock
    ) -> None:
        """FAIL_OPEN with kill switch engaged should warn but allow (risk-reducing)."""
        mock_client.fetch_kill_switch_status.return_value = {"state": "ENGAGED"}
        result = await gate.check_with_api_verification(
            policy=SafetyPolicy.FAIL_OPEN,
            cached_connection_state="CONNECTED",
        )
        assert result.allowed is True
        assert any("ENGAGED" in w for w in result.warnings)

    @pytest.mark.asyncio()
    async def test_fail_open_circuit_breaker_tripped_warns(
        self, gate: SafetyGate, mock_client: AsyncMock
    ) -> None:
        """FAIL_OPEN with circuit breaker tripped should warn but allow."""
        mock_client.fetch_circuit_breaker_status.return_value = {"state": "TRIPPED"}
        result = await gate.check_with_api_verification(
            policy=SafetyPolicy.FAIL_OPEN,
            cached_connection_state="CONNECTED",
        )
        assert result.allowed is True
        assert any("TRIPPED" in w for w in result.warnings)

    @pytest.mark.asyncio()
    async def test_fail_open_api_5xx_warns_and_allows(
        self, gate: SafetyGate, mock_client: AsyncMock
    ) -> None:
        """FAIL_OPEN should warn but allow on 5xx errors (transient)."""
        response = MagicMock()
        response.status_code = 503
        mock_client.fetch_kill_switch_status.side_effect = httpx.HTTPStatusError(
            "Service unavailable", request=MagicMock(), response=response
        )
        result = await gate.check_with_api_verification(
            policy=SafetyPolicy.FAIL_OPEN,
            cached_connection_state="CONNECTED",
        )
        assert result.allowed is True
        assert any("503" in w for w in result.warnings)

    @pytest.mark.asyncio()
    async def test_fail_open_api_4xx_warns_and_allows(
        self, gate: SafetyGate, mock_client: AsyncMock
    ) -> None:
        """FAIL_OPEN should warn but allow on 4xx errors (risk-reducing priority).

        Rationale: Safety service misconfiguration (403/404) shouldn't prevent
        the panic button (flatten) from working. Risk-reducing actions take priority.
        """
        response = MagicMock()
        response.status_code = 403
        mock_client.fetch_kill_switch_status.side_effect = httpx.HTTPStatusError(
            "Forbidden", request=MagicMock(), response=response
        )
        result = await gate.check_with_api_verification(
            policy=SafetyPolicy.FAIL_OPEN,
            cached_connection_state="CONNECTED",
        )
        assert result.allowed is True
        assert any("403" in w for w in result.warnings)

    @pytest.mark.asyncio()
    async def test_fail_closed_api_safe(self, gate: SafetyGate) -> None:
        """FAIL_CLOSED with safe API responses should allow."""
        result = await gate.check_with_api_verification(
            policy=SafetyPolicy.FAIL_CLOSED,
            cached_connection_state="CONNECTED",
        )
        assert result.allowed is True

    @pytest.mark.asyncio()
    async def test_fail_closed_connection_none_blocks(self, gate: SafetyGate) -> None:
        """FAIL_CLOSED with None connection state should block."""
        result = await gate.check_with_api_verification(
            policy=SafetyPolicy.FAIL_CLOSED,
            cached_connection_state=None,
        )
        assert result.allowed is False
        assert "Connection state unknown" in result.reason

    @pytest.mark.asyncio()
    async def test_fail_closed_connection_disconnected_blocks(self, gate: SafetyGate) -> None:
        """FAIL_CLOSED with disconnected connection should block."""
        result = await gate.check_with_api_verification(
            policy=SafetyPolicy.FAIL_CLOSED,
            cached_connection_state="DISCONNECTED",
        )
        assert result.allowed is False
        assert "DISCONNECTED" in result.reason

    @pytest.mark.asyncio()
    async def test_fail_closed_api_5xx_blocks(
        self, gate: SafetyGate, mock_client: AsyncMock
    ) -> None:
        """FAIL_CLOSED should block on 5xx errors."""
        response = MagicMock()
        response.status_code = 500
        mock_client.fetch_kill_switch_status.side_effect = httpx.HTTPStatusError(
            "Internal error", request=MagicMock(), response=response
        )
        result = await gate.check_with_api_verification(
            policy=SafetyPolicy.FAIL_CLOSED,
            cached_connection_state="CONNECTED",
        )
        assert result.allowed is False
        assert "500" in result.reason

    @pytest.mark.asyncio()
    async def test_fail_closed_circuit_breaker_tripped_blocks(
        self, gate: SafetyGate, mock_client: AsyncMock
    ) -> None:
        """FAIL_CLOSED should block on circuit breaker tripped."""
        mock_client.fetch_circuit_breaker_status.return_value = {"state": "TRIPPED"}
        result = await gate.check_with_api_verification(
            policy=SafetyPolicy.FAIL_CLOSED,
            cached_connection_state="CONNECTED",
        )
        assert result.allowed is False
        assert "TRIPPED" in result.reason

    @pytest.mark.asyncio()
    async def test_fail_closed_circuit_breaker_quiet_period_blocks(
        self, gate: SafetyGate, mock_client: AsyncMock
    ) -> None:
        """FAIL_CLOSED should block on circuit breaker quiet period."""
        mock_client.fetch_circuit_breaker_status.return_value = {"state": "QUIET_PERIOD"}
        result = await gate.check_with_api_verification(
            policy=SafetyPolicy.FAIL_CLOSED,
            cached_connection_state="CONNECTED",
        )
        assert result.allowed is False
        assert "QUIET_PERIOD" in result.reason

    @pytest.mark.asyncio()
    async def test_fail_open_unknown_connection_warns(self, gate: SafetyGate) -> None:
        """FAIL_OPEN with None connection should warn but proceed."""
        result = await gate.check_with_api_verification(
            policy=SafetyPolicy.FAIL_OPEN,
            cached_connection_state=None,
        )
        assert result.allowed is True
        assert any("Connection state unknown" in w for w in result.warnings)

    @pytest.mark.asyncio()
    async def test_fail_open_disconnected_warns(self, gate: SafetyGate) -> None:
        """FAIL_OPEN with disconnected connection should warn but proceed."""
        result = await gate.check_with_api_verification(
            policy=SafetyPolicy.FAIL_OPEN,
            cached_connection_state="DISCONNECTED",
        )
        assert result.allowed is True
        assert any("DISCONNECTED" in w for w in result.warnings)

    @pytest.mark.asyncio()
    async def test_fail_closed_request_error_blocks(
        self, gate: SafetyGate, mock_client: AsyncMock
    ) -> None:
        """FAIL_CLOSED should block on network errors."""
        mock_client.fetch_kill_switch_status.side_effect = httpx.RequestError("Connection refused")
        result = await gate.check_with_api_verification(
            policy=SafetyPolicy.FAIL_CLOSED,
            cached_connection_state="CONNECTED",
        )
        assert result.allowed is False
        assert "unreachable" in result.reason

    @pytest.mark.asyncio()
    async def test_fail_closed_unknown_kill_switch_api_state_blocks(
        self, gate: SafetyGate, mock_client: AsyncMock
    ) -> None:
        """FAIL_CLOSED should block on unknown kill switch API state."""
        # API returns but with unexpected/empty state
        mock_client.fetch_kill_switch_status.return_value = {"state": ""}
        result = await gate.check_with_api_verification(
            policy=SafetyPolicy.FAIL_CLOSED,
            cached_connection_state="CONNECTED",
        )
        assert result.allowed is False
        assert "Kill switch state unknown" in result.reason

    @pytest.mark.asyncio()
    async def test_fail_closed_missing_kill_switch_api_state_blocks(
        self, gate: SafetyGate, mock_client: AsyncMock
    ) -> None:
        """FAIL_CLOSED should block on missing kill switch API state."""
        # API returns but with no state key
        mock_client.fetch_kill_switch_status.return_value = {}
        result = await gate.check_with_api_verification(
            policy=SafetyPolicy.FAIL_CLOSED,
            cached_connection_state="CONNECTED",
        )
        assert result.allowed is False
        assert "Kill switch state unknown" in result.reason

    @pytest.mark.asyncio()
    async def test_fail_closed_unknown_circuit_breaker_api_state_blocks(
        self, gate: SafetyGate, mock_client: AsyncMock
    ) -> None:
        """FAIL_CLOSED should block on unknown circuit breaker API state."""
        mock_client.fetch_kill_switch_status.return_value = {"state": "DISENGAGED"}
        mock_client.fetch_circuit_breaker_status.return_value = {"state": "UNKNOWN_STATE"}
        result = await gate.check_with_api_verification(
            policy=SafetyPolicy.FAIL_CLOSED,
            cached_connection_state="CONNECTED",
        )
        assert result.allowed is False
        assert "Circuit breaker state unknown" in result.reason

    @pytest.mark.asyncio()
    async def test_fail_closed_empty_circuit_breaker_api_state_blocks(
        self, gate: SafetyGate, mock_client: AsyncMock
    ) -> None:
        """FAIL_CLOSED should block on empty circuit breaker API state."""
        mock_client.fetch_kill_switch_status.return_value = {"state": "DISENGAGED"}
        mock_client.fetch_circuit_breaker_status.return_value = {"state": ""}
        result = await gate.check_with_api_verification(
            policy=SafetyPolicy.FAIL_CLOSED,
            cached_connection_state="CONNECTED",
        )
        assert result.allowed is False
        assert "Circuit breaker state unknown" in result.reason

    @pytest.mark.asyncio()
    async def test_fail_open_unknown_api_states_allows(
        self, gate: SafetyGate, mock_client: AsyncMock
    ) -> None:
        """FAIL_OPEN should allow on unknown API states (risk-reducing)."""
        mock_client.fetch_kill_switch_status.return_value = {"state": "WEIRD_STATE"}
        mock_client.fetch_circuit_breaker_status.return_value = {"state": "WHATEVER"}
        result = await gate.check_with_api_verification(
            policy=SafetyPolicy.FAIL_OPEN,
            cached_connection_state="CONNECTED",
        )
        # FAIL_OPEN allows - unknown states don't block risk-reducing actions
        assert result.allowed is True

    @pytest.mark.asyncio()
    async def test_fail_closed_unknown_connection_state_string_blocks(
        self, gate: SafetyGate
    ) -> None:
        """FAIL_CLOSED should block on unknown connection state string."""
        result = await gate.check_with_api_verification(
            policy=SafetyPolicy.FAIL_CLOSED,
            cached_connection_state="UNKNOWN",
        )
        assert result.allowed is False
        assert "UNKNOWN" in result.reason

    @pytest.mark.asyncio()
    async def test_fail_open_request_error_warns_and_allows(
        self, gate: SafetyGate, mock_client: AsyncMock
    ) -> None:
        """FAIL_OPEN should warn but allow on network errors (risk-reducing)."""
        mock_client.fetch_kill_switch_status.side_effect = httpx.RequestError("Connection refused")
        result = await gate.check_with_api_verification(
            policy=SafetyPolicy.FAIL_OPEN,
            cached_connection_state="CONNECTED",
        )
        assert result.allowed is True
        assert any("unreachable" in w for w in result.warnings)

    @pytest.mark.asyncio()
    async def test_fail_closed_unexpected_exception_blocks(
        self, gate: SafetyGate, mock_client: AsyncMock
    ) -> None:
        """FAIL_CLOSED should block on unexpected exceptions in kill switch check."""
        # Simulate unexpected error (not httpx.HTTPStatusError or RequestError)
        mock_client.fetch_kill_switch_status.side_effect = ValueError("Bad JSON")
        result = await gate.check_with_api_verification(
            policy=SafetyPolicy.FAIL_CLOSED,
            cached_connection_state="CONNECTED",
        )
        assert result.allowed is False
        assert "error" in result.reason.lower()

    @pytest.mark.asyncio()
    async def test_fail_open_unexpected_exception_warns(
        self, gate: SafetyGate, mock_client: AsyncMock
    ) -> None:
        """FAIL_OPEN should warn but allow on unexpected exceptions."""
        mock_client.fetch_kill_switch_status.side_effect = ValueError("Bad JSON")
        result = await gate.check_with_api_verification(
            policy=SafetyPolicy.FAIL_OPEN,
            cached_connection_state="CONNECTED",
        )
        assert result.allowed is True
        assert any("error" in w.lower() for w in result.warnings)

    @pytest.mark.asyncio()
    async def test_fail_closed_circuit_breaker_unexpected_exception_blocks(
        self, gate: SafetyGate, mock_client: AsyncMock
    ) -> None:
        """FAIL_CLOSED should block on unexpected circuit breaker exceptions."""
        mock_client.fetch_kill_switch_status.return_value = {"state": "DISENGAGED"}
        mock_client.fetch_circuit_breaker_status.side_effect = TypeError("Invalid type")
        result = await gate.check_with_api_verification(
            policy=SafetyPolicy.FAIL_CLOSED,
            cached_connection_state="CONNECTED",
        )
        assert result.allowed is False
        assert "error" in result.reason.lower()

    @pytest.mark.asyncio()
    async def test_fail_open_circuit_breaker_unexpected_exception_warns(
        self, gate: SafetyGate, mock_client: AsyncMock
    ) -> None:
        """FAIL_OPEN should warn but allow on unexpected circuit breaker exceptions."""
        mock_client.fetch_kill_switch_status.return_value = {"state": "DISENGAGED"}
        mock_client.fetch_circuit_breaker_status.side_effect = TypeError("Invalid type")
        result = await gate.check_with_api_verification(
            policy=SafetyPolicy.FAIL_OPEN,
            cached_connection_state="CONNECTED",
        )
        assert result.allowed is True
        assert any("error" in w.lower() for w in result.warnings)

    @pytest.mark.asyncio()
    async def test_fail_closed_circuit_breaker_request_error_blocks(
        self, gate: SafetyGate, mock_client: AsyncMock
    ) -> None:
        """FAIL_CLOSED should block on circuit breaker network errors."""
        mock_client.fetch_kill_switch_status.return_value = {"state": "DISENGAGED"}
        mock_client.fetch_circuit_breaker_status.side_effect = httpx.RequestError(
            "Connection refused"
        )
        result = await gate.check_with_api_verification(
            policy=SafetyPolicy.FAIL_CLOSED,
            cached_connection_state="CONNECTED",
        )
        assert result.allowed is False
        assert "unreachable" in result.reason

    @pytest.mark.asyncio()
    async def test_fail_open_circuit_breaker_request_error_warns(
        self, gate: SafetyGate, mock_client: AsyncMock
    ) -> None:
        """FAIL_OPEN should warn but allow on circuit breaker network errors."""
        mock_client.fetch_kill_switch_status.return_value = {"state": "DISENGAGED"}
        mock_client.fetch_circuit_breaker_status.side_effect = httpx.RequestError(
            "Connection refused"
        )
        result = await gate.check_with_api_verification(
            policy=SafetyPolicy.FAIL_OPEN,
            cached_connection_state="CONNECTED",
        )
        assert result.allowed is True
        assert any("unreachable" in w for w in result.warnings)

    @pytest.mark.asyncio()
    async def test_fail_closed_circuit_breaker_http_error_blocks(
        self, gate: SafetyGate, mock_client: AsyncMock
    ) -> None:
        """FAIL_CLOSED should block on circuit breaker HTTP errors."""
        mock_client.fetch_kill_switch_status.return_value = {"state": "DISENGAGED"}
        response = MagicMock()
        response.status_code = 500
        mock_client.fetch_circuit_breaker_status.side_effect = httpx.HTTPStatusError(
            "Internal error", request=MagicMock(), response=response
        )
        result = await gate.check_with_api_verification(
            policy=SafetyPolicy.FAIL_CLOSED,
            cached_connection_state="CONNECTED",
        )
        assert result.allowed is False
        assert "500" in result.reason

    @pytest.mark.asyncio()
    async def test_fail_open_circuit_breaker_http_error_warns(
        self, gate: SafetyGate, mock_client: AsyncMock
    ) -> None:
        """FAIL_OPEN should warn but allow on circuit breaker HTTP errors."""
        mock_client.fetch_kill_switch_status.return_value = {"state": "DISENGAGED"}
        response = MagicMock()
        response.status_code = 503
        mock_client.fetch_circuit_breaker_status.side_effect = httpx.HTTPStatusError(
            "Service unavailable", request=MagicMock(), response=response
        )
        result = await gate.check_with_api_verification(
            policy=SafetyPolicy.FAIL_OPEN,
            cached_connection_state="CONNECTED",
        )
        assert result.allowed is True
        assert any("503" in w for w in result.warnings)

    @pytest.mark.asyncio()
    async def test_fail_closed_kill_switch_engaged_blocks(
        self, gate: SafetyGate, mock_client: AsyncMock
    ) -> None:
        """FAIL_CLOSED should block when kill switch is engaged."""
        mock_client.fetch_kill_switch_status.return_value = {"state": "ENGAGED"}
        result = await gate.check_with_api_verification(
            policy=SafetyPolicy.FAIL_CLOSED,
            cached_connection_state="CONNECTED",
        )
        assert result.allowed is False
        assert "Kill Switch engaged" in result.reason

    @pytest.mark.asyncio()
    async def test_fail_open_unrecognized_connection_state_warns(
        self, gate: SafetyGate, mock_client: AsyncMock
    ) -> None:
        """FAIL_OPEN should warn on unrecognized connection state."""
        result = await gate.check_with_api_verification(
            policy=SafetyPolicy.FAIL_OPEN,
            cached_connection_state="LIMBO",  # Unrecognized
        )
        assert result.allowed is True
        assert any("unknown" in w.lower() for w in result.warnings)

    @pytest.mark.asyncio()
    async def test_fail_closed_4xx_http_error_blocks(
        self, gate: SafetyGate, mock_client: AsyncMock
    ) -> None:
        """FAIL_CLOSED should block on 4xx HTTP errors (invalid request)."""
        response = MagicMock()
        response.status_code = 403
        mock_client.fetch_kill_switch_status.side_effect = httpx.HTTPStatusError(
            "Forbidden", request=MagicMock(), response=response
        )
        result = await gate.check_with_api_verification(
            policy=SafetyPolicy.FAIL_CLOSED,
            cached_connection_state="CONNECTED",
        )
        assert result.allowed is False
        assert "403" in result.reason
