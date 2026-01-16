"""
Admin routes for Execution Gateway.

This module provides administrative endpoints for:
- Configuration management (service config, fat-finger thresholds)
- Strategy status and monitoring
- Kill-switch controls (emergency trading halt)

All endpoints require authentication and appropriate permissions.
"""

import logging
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status

from apps.execution_gateway import __version__
from apps.execution_gateway.database import DatabaseClient
from apps.execution_gateway.fat_finger_validator import FatFingerValidator
from apps.execution_gateway.recovery_manager import RecoveryManager
from apps.execution_gateway.schemas import (
    ConfigResponse,
    FatFingerThresholdsResponse,
    FatFingerThresholdsUpdateRequest,
    KillSwitchDisengageRequest,
    KillSwitchEngageRequest,
    StrategiesListResponse,
    StrategyStatusResponse,
)
from apps.execution_gateway.services.auth_helpers import build_user_context
from libs.core.common.api_auth_dependency import APIAuthConfig, AuthContext, api_auth
from libs.platform.web_console_auth.permissions import (
    Permission,
    get_authorized_strategies,
    require_permission,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Helper Functions
# =============================================================================


def create_fat_finger_thresholds_snapshot(
    fat_finger_validator: FatFingerValidator,
) -> FatFingerThresholdsResponse:
    """Build a response payload with current fat-finger thresholds."""

    return FatFingerThresholdsResponse(
        default_thresholds=fat_finger_validator.get_default_thresholds(),
        symbol_overrides=fat_finger_validator.get_symbol_overrides(),
        updated_at=datetime.now(UTC),
    )


def _determine_strategy_status(
    db_status: dict[str, Any], now: datetime, strategy_activity_threshold_seconds: int
) -> Literal["active", "paused", "error", "inactive"]:
    """Determine strategy status based on activity.

    A strategy is considered active if it has:
    - Open positions (positions_count > 0)
    - Open orders (open_orders_count > 0)
    - Recent signal activity (within threshold)

    Args:
        db_status: Dict with positions_count, open_orders_count, last_signal_at
        now: Current timestamp for age calculation
        strategy_activity_threshold_seconds: Threshold for considering strategy active

    Returns:
        Strategy status: "active", "paused", "error", or "inactive"
    """
    if db_status["positions_count"] > 0 or db_status["open_orders_count"] > 0:
        return "active"
    if db_status["last_signal_at"]:
        age = (now - db_status["last_signal_at"]).total_seconds()
        if age < strategy_activity_threshold_seconds:
            return "active"
    return "inactive"


# =============================================================================
# Router Factory
# =============================================================================


def create_admin_router(
    fat_finger_validator: FatFingerValidator,
    recovery_manager: RecoveryManager,
    db_client: DatabaseClient,
    environment: str,
    dry_run: bool,
    alpaca_paper: bool,
    circuit_breaker_enabled: bool,
    liquidity_check_enabled: bool,
    max_slice_pct_of_adv: float,
    strategy_activity_threshold_seconds: int,
    authenticator_getter: Any,  # Callable that returns authenticator
) -> APIRouter:
    """
    Create admin router with dependencies.

    Args:
        fat_finger_validator: Fat-finger validator instance
        recovery_manager: Recovery manager for kill-switch operations
        db_client: Database client for strategy status queries
        environment: Environment name (dev, staging, prod)
        dry_run: Dry-run mode flag
        alpaca_paper: Paper trading mode flag
        circuit_breaker_enabled: Circuit breaker enabled flag
        liquidity_check_enabled: Liquidity check enabled flag
        max_slice_pct_of_adv: Maximum slice percentage of ADV
        strategy_activity_threshold_seconds: Threshold for strategy activity
        authenticator_getter: Function to get authenticator instance

    Returns:
        Configured APIRouter with admin endpoints
    """
    router = APIRouter()

    # Create kill-switch auth dependency
    kill_switch_auth = api_auth(
        APIAuthConfig(
            action="kill_switch",
            require_role=None,
            require_permission=Permission.CANCEL_ORDER,
        ),
        authenticator_getter=authenticator_getter,
    )

    # =============================================================================
    # Configuration Endpoints
    # =============================================================================

    @router.get("/api/v1/config", response_model=ConfigResponse, tags=["Configuration"])
    async def get_config() -> ConfigResponse:
        """
        Get service configuration for verification.

        Returns safety flags and environment settings for automated verification
        in smoke tests and monitoring. Critical for ensuring paper trading mode
        in staging and detecting configuration drift.

        This endpoint is used by:
        - CI/CD smoke tests to verify paper trading mode active
        - Monitoring systems to detect configuration drift
        - Debugging to verify environment settings

        Returns:
            ConfigResponse with service configuration details

        Examples:
            >>> import requests
            >>> response = requests.get("http://localhost:8002/api/v1/config")
            >>> config = response.json()
            >>> assert config["dry_run"] is True  # Staging safety check
            >>> assert config["alpaca_paper"] is True
            >>> assert config["environment"] == "staging"
        """
        return ConfigResponse(
            service="execution_gateway",
            version=__version__,
            environment=environment,
            dry_run=dry_run,
            alpaca_paper=alpaca_paper,
            circuit_breaker_enabled=circuit_breaker_enabled,
            liquidity_check_enabled=liquidity_check_enabled,
            max_slice_pct_of_adv=max_slice_pct_of_adv,
            timestamp=datetime.now(UTC),
        )

    @router.get(
        "/api/v1/fat-finger/thresholds",
        response_model=FatFingerThresholdsResponse,
        tags=["Configuration"],
    )
    async def get_fat_finger_thresholds() -> FatFingerThresholdsResponse:
        """Get current fat-finger threshold configuration."""

        return create_fat_finger_thresholds_snapshot(fat_finger_validator)

    @router.put(
        "/api/v1/fat-finger/thresholds",
        response_model=FatFingerThresholdsResponse,
        tags=["Configuration"],
    )
    @require_permission(Permission.MANAGE_STRATEGIES)
    async def update_fat_finger_thresholds(
        payload: FatFingerThresholdsUpdateRequest,
        user: dict[str, Any] = Depends(build_user_context),
    ) -> FatFingerThresholdsResponse:
        """Update fat-finger thresholds (defaults and per-symbol overrides)."""

        if payload.default_thresholds is not None:
            fat_finger_validator.update_defaults(payload.default_thresholds)

        if payload.symbol_overrides is not None:
            fat_finger_validator.update_symbol_overrides(payload.symbol_overrides)

        logger.info(
            "Fat-finger thresholds updated",
            extra={
                "user_id": user.get("user_id"),
                "default_thresholds": (
                    payload.default_thresholds.model_dump(mode="json")
                    if payload.default_thresholds
                    else None
                ),
                "symbol_overrides": (
                    list(payload.symbol_overrides.keys()) if payload.symbol_overrides else []
                ),
            },
        )

        return create_fat_finger_thresholds_snapshot(fat_finger_validator)

    # =============================================================================
    # Strategy Status Endpoints
    # =============================================================================

    @router.get(
        "/api/v1/strategies",
        response_model=StrategiesListResponse,
        tags=["Strategies"],
    )
    async def list_strategies(
        user: dict[str, Any] = Depends(build_user_context),
    ) -> StrategiesListResponse:
        """
        List all strategies with their current status.

        Returns consolidated view of each strategy including:
        - Basic info (id, name, status)
        - Position and open order counts
        - Today's realized P&L
        - Last signal time

        Only returns strategies the user is authorized to view.

        Example response:
            {
                "strategies": [
                    {
                        "strategy_id": "alpha_baseline",
                        "name": "Alpha Baseline",
                        "status": "active",
                        ...
                    }
                ],
                "total_count": 1,
                "timestamp": "2024-10-17T16:35:00Z"
            }
        """
        now = datetime.now(UTC)

        # Get authorized strategies for this user
        authorized_strategies = get_authorized_strategies(user.get("user"))
        if not authorized_strategies:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No strategy access",
            )

        # Get strategy IDs filtered at database level for better performance
        # Uses ANY(...) to avoid transferring all strategy IDs then filtering in Python
        strategy_ids = db_client.get_all_strategy_ids(filter_ids=authorized_strategies)

        # Fetch all strategy statuses in a single bulk query (avoids N+1 problem)
        bulk_status = db_client.get_bulk_strategy_status(strategy_ids)

        strategies = []
        for strategy_id in strategy_ids:
            db_status = bulk_status.get(strategy_id)
            if db_status is None:
                continue

            strategy_status = _determine_strategy_status(
                db_status, now, strategy_activity_threshold_seconds
            )

            strategies.append(
                StrategyStatusResponse(
                    strategy_id=strategy_id,
                    name=strategy_id.replace("_", " ").title(),  # Simple name formatting
                    status=strategy_status,
                    model_version=None,  # Could be fetched from model registry
                    model_status=None,
                    last_signal_at=db_status["last_signal_at"],
                    last_error=None,
                    positions_count=db_status["positions_count"],
                    open_orders_count=db_status["open_orders_count"],
                    today_pnl=db_status["today_pnl"],
                    timestamp=now,
                )
            )

        return StrategiesListResponse(
            strategies=strategies,
            total_count=len(strategies),
            timestamp=now,
        )

    @router.get(
        "/api/v1/strategies/{strategy_id}",
        response_model=StrategyStatusResponse,
        tags=["Strategies"],
    )
    async def get_strategy_status(
        strategy_id: str,
        user: dict[str, Any] = Depends(build_user_context),
    ) -> StrategyStatusResponse:
        """
        Get status for a specific strategy.

        Args:
            strategy_id: The strategy identifier

        Returns:
            StrategyStatusResponse with consolidated strategy state

        Raises:
            HTTPException 403 if user not authorized for this strategy
            HTTPException 404 if strategy not found
        """
        now = datetime.now(UTC)

        # Check if user is authorized for this strategy
        authorized_strategies = get_authorized_strategies(user.get("user"))
        if not authorized_strategies:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No strategy access",
            )
        if strategy_id not in authorized_strategies:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Not authorized to access strategy '{strategy_id}'",
            )

        db_status = db_client.get_strategy_status(strategy_id)
        if db_status is None:
            raise HTTPException(
                status_code=404,
                detail=f"Strategy '{strategy_id}' not found",
            )

        strategy_status = _determine_strategy_status(
            db_status, now, strategy_activity_threshold_seconds
        )

        return StrategyStatusResponse(
            strategy_id=strategy_id,
            name=strategy_id.replace("_", " ").title(),
            status=strategy_status,
            model_version=None,
            model_status=None,
            last_signal_at=db_status["last_signal_at"],
            last_error=None,
            positions_count=db_status["positions_count"],
            open_orders_count=db_status["open_orders_count"],
            today_pnl=db_status["today_pnl"],
            timestamp=now,
        )

    # =============================================================================
    # Kill-Switch Endpoints
    # =============================================================================

    @router.post("/api/v1/kill-switch/engage", tags=["Kill-Switch"])
    async def engage_kill_switch(
        request: KillSwitchEngageRequest,
        _auth_context: AuthContext = Depends(kill_switch_auth),
    ) -> dict[str, Any]:
        """
        Engage kill-switch (emergency trading halt).

        CRITICAL: This operator-controlled action immediately blocks ALL trading
        activities across all services until manually disengaged.

        Args:
            request: KillSwitchEngageRequest with reason, operator, and optional details

        Returns:
            Kill-switch status after engagement

        Raises:
            HTTPException 503: Redis unavailable
            HTTPException 400: Kill-switch already engaged

        Examples:
            >>> import requests
            >>> response = requests.post(
            ...     "http://localhost:8002/api/v1/kill-switch/engage",
            ...     json={
            ...         "reason": "Market anomaly detected",
            ...         "operator": "ops_team",
            ...         "details": {"anomaly_type": "flash_crash"}
            ...     }
            ... )
            >>> response.json()
            {"state": "ENGAGED", "engaged_by": "ops_team", ...}
        """
        kill_switch = recovery_manager.kill_switch
        if not kill_switch or recovery_manager.is_kill_switch_unavailable():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Kill-switch unavailable (Redis not initialized)",
            )

        try:
            kill_switch.engage(
                reason=request.reason,
                operator=request.operator,
                details=request.details,
            )
            return kill_switch.get_status()
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e),
            ) from e
        except RuntimeError as e:
            # Kill-switch state missing (fail-closed)
            recovery_manager.set_kill_switch_unavailable(True)
            logger.error(
                "Kill-switch engage failed: state missing (fail-closed)",
                extra={"fail_closed": True, "error": str(e)},
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "Kill-switch unavailable",
                    "message": "Kill-switch state missing in Redis (fail-closed for safety)",
                    "fail_closed": True,
                },
            ) from e

    @router.post("/api/v1/kill-switch/disengage", tags=["Kill-Switch"])
    async def disengage_kill_switch(
        request: KillSwitchDisengageRequest,
        _auth_context: AuthContext = Depends(kill_switch_auth),
    ) -> dict[str, Any]:
        """
        Disengage kill-switch (resume trading).

        This operator action re-enables trading after kill-switch was engaged.

        Args:
            request: KillSwitchDisengageRequest with operator and optional notes

        Returns:
            Kill-switch status after disengagement

        Raises:
            HTTPException 503: Redis unavailable
            HTTPException 400: Kill-switch not currently engaged

        Examples:
            >>> import requests
            >>> response = requests.post(
            ...     "http://localhost:8002/api/v1/kill-switch/disengage",
            ...     json={
            ...         "operator": "ops_team",
            ...         "notes": "Market conditions normalized"
            ...     }
            ... )
            >>> response.json()
            {"state": "ACTIVE", "disengaged_by": "ops_team", ...}
        """
        kill_switch = recovery_manager.kill_switch
        if not kill_switch or recovery_manager.is_kill_switch_unavailable():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Kill-switch unavailable (Redis not initialized)",
            )

        try:
            kill_switch.disengage(operator=request.operator, notes=request.notes)
            return kill_switch.get_status()
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e),
            ) from e
        except RuntimeError as e:
            # Kill-switch state missing (fail-closed)
            recovery_manager.set_kill_switch_unavailable(True)
            logger.error(
                "Kill-switch disengage failed: state missing (fail-closed)",
                extra={"fail_closed": True, "error": str(e)},
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "Kill-switch unavailable",
                    "message": "Kill-switch state missing in Redis (fail-closed for safety)",
                    "fail_closed": True,
                },
            ) from e

    @router.get("/api/v1/kill-switch/status", tags=["Kill-Switch"])
    async def get_kill_switch_status(
        _auth_context: AuthContext = Depends(kill_switch_auth),
    ) -> dict[str, Any]:
        """
        Get kill-switch status.

        Returns current state, last engagement/disengagement details, and history.

        Returns:
            Kill-switch status with state, timestamps, and operator info

        Raises:
            HTTPException 503: Redis unavailable

        Examples:
            >>> import requests
            >>> response = requests.get("http://localhost:8002/api/v1/kill-switch/status")
            >>> status = response.json()
            >>> print(status["state"])
            'ACTIVE'
        """
        kill_switch = recovery_manager.kill_switch
        if not kill_switch or recovery_manager.is_kill_switch_unavailable():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Kill-switch unavailable (Redis not initialized)",
            )

        try:
            return kill_switch.get_status()
        except RuntimeError as e:
            # Kill-switch state missing (fail-closed)
            recovery_manager.set_kill_switch_unavailable(True)
            logger.error(
                "Kill-switch status unavailable: state missing (fail-closed)",
                extra={"fail_closed": True, "error": str(e)},
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "Kill-switch unavailable",
                    "message": "Kill-switch state missing in Redis (fail-closed for safety)",
                    "fail_closed": True,
                },
            ) from e

    return router
