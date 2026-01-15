# risk_management

## Identity
- **Type:** Library
- **Port:** N/A
- **Container:** N/A

## Interface
### Public Interface (Exported Classes & Functions)
| Class/Function | Parameters | Returns | Description |
|----------------|------------|---------|-------------|
| `RiskConfig` | limits | model | Risk limits configuration. |
| `CircuitBreaker` | redis_client | instance | Circuit breaker state machine. |
| `KillSwitch` | redis_client | instance | Operator kill switch. |
| `RiskChecker` | config, breaker | instance | Pre-trade risk validation. |
| `PositionReservation` | redis_client | instance | Position reservation helpers. |

## Behavioral Contracts
### RiskChecker.validate_order(...)
**Purpose:** Enforce position and portfolio limits before order submission.

### CircuitBreaker.trip(...)
**Purpose:** Transition breaker state to TRIPPED and persist in Redis.

### Invariants
- Circuit breaker state is checked before order submission.
- Kill switch overrides all non-risk-reducing actions.

## Data Flow
```
order request -> risk checks -> allow/deny
```
- **Input format:** order params and current positions.
- **Output format:** boolean allow/deny with reason.
- **Side effects:** Redis state updates for breaker/kill switch.

## Usage Examples
### Example 1: Pre-trade check
```python
from libs.trading.risk_management import RiskChecker, RiskConfig

checker = RiskChecker(config=RiskConfig(), breaker=...)
valid, reason = checker.validate_order(symbol="AAPL", side="buy", qty=10, current_position=0)
```

### Example 2: Trip breaker
```python
from libs.trading.risk_management import CircuitBreaker

breaker = CircuitBreaker(redis_client=...)
breaker.trip("manual")
```

## Edge Cases & Boundaries
| Scenario | Input | Expected Behavior |
|----------|-------|-------------------|
| Breaker unavailable | Redis down | fail-closed (deny orders). |
| Position limit exceeded | new qty | `RiskViolation`. |
| Kill switch engaged | any order | deny unless risk-reducing. |

## Dependencies
- **Internal:** `libs.redis_client`
- **External:** Redis

## Configuration
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| N/A | - | - | Configuration via `RiskConfig`. |

## Error Handling
- Raises `RiskViolation` and breaker exceptions on failure.

## Security
- Centralized control for trading safety mechanisms.

## Testing
- **Test Files:** `tests/libs/trading/risk_management/`
- **Run Tests:** `pytest tests/libs/risk_management -v`
- **Coverage:** N/A

## Related Specs
- `../services/execution_gateway.md`

## Known Issues & TODO
| Issue | Severity | Description | Tracking |
|-------|----------|-------------|----------|
| None | - | No known issues | - |

## Metadata
- **Last Updated:** 2026-01-03
- **Source Files:** `libs/trading/risk_management/breaker.py`, `libs/trading/risk_management/checker.py`, `libs/trading/risk_management/kill_switch.py`
- **ADRs:** `docs/ADRs/0011-risk-management-system.md`
