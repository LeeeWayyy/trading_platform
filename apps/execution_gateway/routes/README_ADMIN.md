# Admin Routes Module

## Overview

The admin routes module (`admin.py`) provides administrative endpoints for the Execution Gateway, including configuration management, strategy status monitoring, and kill-switch controls.

## File Location

```
apps/execution_gateway/routes/admin.py
```

## Endpoints Provided

### Configuration Endpoints
- `GET /api/v1/config` - Get service configuration
- `GET /api/v1/fat-finger/thresholds` - Get fat-finger thresholds
- `PUT /api/v1/fat-finger/thresholds` - Update fat-finger thresholds (requires Permission.MANAGE_STRATEGIES)

### Strategy Status Endpoints
- `GET /api/v1/strategies` - List all strategies with status
- `GET /api/v1/strategies/{strategy_id}` - Get specific strategy status

### Kill-Switch Endpoints
- `POST /api/v1/kill-switch/engage` - Engage kill-switch (requires Permission.CANCEL_ORDER)
- `POST /api/v1/kill-switch/disengage` - Disengage kill-switch (requires Permission.CANCEL_ORDER)
- `GET /api/v1/kill-switch/status` - Get kill-switch status (requires Permission.CANCEL_ORDER)

## Usage

The module uses a factory pattern to create the router with dependencies:

```python
from apps.execution_gateway.routes.admin import create_admin_router

# Create router with dependencies
admin_router = create_admin_router(
    fat_finger_validator=fat_finger_validator,
    recovery_manager=recovery_manager,
    db_client=db_client,
    environment=ENVIRONMENT,
    dry_run=DRY_RUN,
    alpaca_paper=ALPACA_PAPER,
    circuit_breaker_enabled=CIRCUIT_BREAKER_ENABLED,
    liquidity_check_enabled=LIQUIDITY_CHECK_ENABLED,
    max_slice_pct_of_adv=MAX_SLICE_PCT_OF_ADV,
    strategy_activity_threshold_seconds=STRATEGY_ACTIVITY_THRESHOLD_SECONDS,
    authenticator_getter=build_gateway_authenticator,
)

# Include in FastAPI app
app.include_router(admin_router)
```

## Dependencies Required

### Service Dependencies
- `FatFingerValidator` - For fat-finger threshold management
- `RecoveryManager` - For kill-switch operations
- `DatabaseClient` - For strategy status queries

### Configuration Parameters
- `environment` - Environment name (dev, staging, prod)
- `dry_run` - Dry-run mode flag
- `alpaca_paper` - Paper trading mode flag
- `circuit_breaker_enabled` - Circuit breaker enabled flag
- `liquidity_check_enabled` - Liquidity check enabled flag
- `max_slice_pct_of_adv` - Maximum slice percentage of ADV
- `strategy_activity_threshold_seconds` - Threshold for strategy activity

### Authentication
- `authenticator_getter` - Function to get authenticator instance

## Helper Functions

### `create_fat_finger_thresholds_snapshot(fat_finger_validator)`
Builds a response payload with current fat-finger thresholds.

### `_determine_strategy_status(db_status, now, strategy_activity_threshold_seconds)`
Determines strategy status based on activity:
- `"active"` - Has positions, orders, or recent signals
- `"inactive"` - No activity
- `"paused"` - (reserved for future use)
- `"error"` - (reserved for future use)

## Authentication & Authorization

- **Config endpoint**: No authentication required
- **Fat-finger GET**: No special permission required
- **Fat-finger PUT**: Requires `Permission.MANAGE_STRATEGIES`
- **Strategy endpoints**: Require user context, filtered by authorized strategies
- **Kill-switch endpoints**: Require `Permission.CANCEL_ORDER`

## Testing

The module has been verified with:
- Syntax checking (py_compile)
- Type checking (mypy --strict)
- Linting (ruff)
- Integration testing (FastAPI TestClient)
- Helper function unit tests

## Migration Notes

When integrating this router into main.py:
1. Import the factory function
2. Create the router after initializing all dependencies
3. Include the router in the app
4. Remove the original endpoint definitions from main.py (lines 1971-2407)
5. Remove the helper functions from main.py (lines 1076-1084, 2061-2085)

## Original Location

These endpoints were extracted from:
- `apps/execution_gateway/main.py` lines 1971-2407 (endpoints)
- `apps/execution_gateway/main.py` lines 1076-1084 (create_fat_finger_thresholds_snapshot)
- `apps/execution_gateway/main.py` lines 2061-2085 (_determine_strategy_status)
