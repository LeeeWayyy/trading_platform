# web_console (Legacy Backend Services)

## Identity
- **Type:** Shared Backend Library (used by web_console_ng)
- **Status:** ⚠️ LEGACY - UI migrated to NiceGUI (web_console_ng)
- **Migration:** See [ADR-0031-nicegui-migration.md](../../ADRs/ADR-0031-nicegui-migration.md)

## Overview

The `apps/web_console/` directory now contains only **backend services and utilities** that are shared with the NiceGUI-based `web_console_ng`. The Streamlit UI components (pages/, components/, app.py) were removed in P5T9.

## Remaining Modules

| Directory | Purpose | Status |
|-----------|---------|--------|
| `services/` | Backend service integrations (market data, orders, risk, alerts) | Active - used by NiceGUI |
| `utils/` | Database pools, validators, helpers | Active - used by NiceGUI |
| `data/` | Data models and repositories | Active - used by NiceGUI |
| `auth/` | Authentication handlers for API integration | Active - used by NiceGUI |
| `config.py` | Configuration management | Active - used by NiceGUI |

## Interface

These modules are imported by `apps/web_console_ng/` for backend functionality:

```python
# NiceGUI pages import services from web_console
from apps.web_console.services.market_data_service import MarketDataService
from apps.web_console.services.order_service import OrderService
from apps.web_console.utils.validators import validate_risk_metrics
```

## Data Validators (`utils/validators.py`)
**Purpose:** Validate API response data before rendering in UI components.

**Functions:**
- `validate_risk_metrics(data)` - Validates complete risk metrics (overview + VaR).
- `validate_overview_metrics(data)` - Validates risk overview metrics (total_risk required).
- `validate_var_metrics(data)` - Validates VaR-specific metrics (var_95, var_99, cvar_95 required).
- `validate_var_history(data)` - Validates VaR history entries.
- `validate_stress_tests(data)` - Validates stress test results.
- `validate_factor_exposures(data)` - Validates factor exposure data.

**Behavior:**
- Returns `True` if all required fields are present and non-None.
- Section-specific validators allow partial data display (e.g., show overview even if VaR is missing).

## Dependencies
- **Internal:** `libs.common.network_utils`, `libs.redis_client`
- **External:** PostgreSQL, Redis, Requests, httpx

## Configuration
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `EXECUTION_GATEWAY_URL` | Yes | `http://localhost:8002` | Base URL for execution gateway API. |
| `DATABASE_URL` | No | `postgresql://...` | Audit log DB connection. |
| `TRUSTED_PROXY_IPS` | No | N/A | Trusted proxies for IP extraction. |

## Migration Roadmap

These modules will eventually be migrated to `libs/` for cleaner separation:
- `services/` → `libs/web_console_services/`
- `utils/` → `libs/web_console_utils/`
- `data/` → `libs/web_console_data/`
- `auth/` → `libs/web_console_auth/`

Until then, they remain in `apps/web_console/` for backward compatibility with existing imports.

## Testing
- **Test Files:** `tests/apps/web_console/services/` (service unit tests remain)
- **Run Tests:** `pytest tests/apps/web_console/services -v`

## Related Specs
- `docs/SPECS/services/web_console_ng.md` - NiceGUI frontend
- `docs/ADRs/ADR-0031-nicegui-migration.md` - Migration rationale

## Metadata
- **Last Updated:** 2026-01-05
- **Source Files:** `apps/web_console/__init__.py`, `apps/web_console/services/`, `apps/web_console/utils/`, `apps/web_console/data/`, `apps/web_console/auth/`, `apps/web_console/config.py`
- **ADRs:** ADR-0031-nicegui-migration
