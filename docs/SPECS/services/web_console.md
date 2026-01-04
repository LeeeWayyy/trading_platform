# web_console

## Identity
- **Type:** Service (Streamlit UI + optional FastAPI metrics sidecar)
- **Port:** 8501 (Streamlit UI), metrics sidecar exposes `/metrics` and `/health` (port configured by deployment)
- **Container:** `apps/web_console/Dockerfile`

## Interface
### UI Surface (Streamlit)
- Streamlit UI served by `apps/web_console/app.py` with dashboard and operational controls.
- Routes are Streamlit page-based (see `apps/web_console/pages/`).

### Metrics Sidecar (FastAPI)
| Endpoint | Method | Parameters | Returns |
|----------|--------|------------|---------|
| `/metrics` | GET | None | Prometheus metrics (multiprocess-aware). |
| `/health` | GET | None | JSON `{status}`. |

## Behavioral Contracts
### UI behavior (`app.py`)
**Purpose:** Provide operational dashboard and manual controls for trading.

**Behavior:**
1. Authenticates user via `apps/web_console/auth` based on `WEB_CONSOLE_AUTH_TYPE`.
2. Calls execution gateway endpoints with retrying HTTP client.
3. Renders dashboard panels (positions, PnL, kill switch, config).
4. Uses `st.cache_data` for auto-refresh, plus `st.rerun()` loop.
5. Records audit logs for manual actions via DB-backed audit logger.

### Metrics sidecar (`metrics_server.py`)
**Purpose:** Expose Prometheus metrics with Redis-backed circuit breaker staleness tracking.

**Behavior:**
1. On `/metrics`, updates circuit-breaker staleness gauge via Redis.
2. Serves multiprocess metrics if `PROMETHEUS_MULTIPROC_DIR` is set.
3. Returns 503 on metrics collection failures to surface alerts.

## Data Flow
```
Browser -> Streamlit UI
  -> Execution Gateway REST APIs (positions, orders, kill switch, config)
  -> Prometheus (optional) via /metrics sidecar
  -> Postgres (audit log writes)
```

### Data Validators (`utils/validators.py`)
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
- **Internal:** `apps/web_console/auth/*`, `apps/web_console/services/*`, `apps/web_console/utils/*`, `libs.common.network_utils`, `libs.redis_client`
- **External:** Execution Gateway API, Postgres, Redis, Prometheus (metrics), Streamlit, Requests

## Configuration
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `EXECUTION_GATEWAY_URL` | Yes | `http://localhost:8002` | Base URL for execution gateway API. |
| `WEB_CONSOLE_AUTH_TYPE` | Yes | `dev` | Auth type (`basic`, `oauth2`, `dev`, `mtls`). |
| `WEB_CONSOLE_USER` | No | `admin` | Dev/basic username. |
| `WEB_CONSOLE_PASSWORD` | No | `admin` | Dev/basic password. |
| `DATABASE_URL` | No | `postgresql://...` | Audit log DB connection. |
| `SESSION_TIMEOUT_MINUTES` | No | `15` | Session idle timeout. |
| `SESSION_ABSOLUTE_TIMEOUT_HOURS` | No | `4` | Absolute session timeout. |
| `TRUSTED_PROXY_IPS` | No | N/A | Trusted proxies for IP extraction. |
| `PROMETHEUS_URL` | No | `http://localhost:9090` | Prometheus URL for dashboard panels. |
| `PROMETHEUS_MULTIPROC_DIR` | No | N/A | Enables multiprocess metrics in sidecar. |
| `AUTO_REFRESH_INTERVAL_SECONDS` | No | `10` | Dashboard refresh interval. |
| `FEATURE_*` | No | Varies | Feature flags (risk dashboard, manual controls, circuit breaker, alerts, etc.). |

## Observability
- **Metrics:** `apps/web_console/metrics_server.py` exposes `/metrics`.
- **Health:** `GET /health` on metrics sidecar.
- **Logs:** Streamlit app logs for API errors and audit logging.

## Security
- Authenticated UI with role-based permissions.
- Manual actions require reason strings and are written to audit log.
- API calls include auth headers derived from active session.

## Testing
- **Test Files:** `tests/apps/web_console/`
- **Run Tests:** `pytest tests/apps/web_console -v`

## Usage Examples
### Example 1: Launch Streamlit UI
```bash
streamlit run apps/web_console/app.py
```

### Example 2: Run metrics sidecar
```bash
uvicorn apps.web_console.metrics_server:app --host 0.0.0.0 --port 8502
```

## Edge Cases & Boundaries
| Scenario | Input | Expected Behavior |
|----------|-------|-------------------|
| Missing auth config | `WEB_CONSOLE_AUTH_TYPE` unset | Defaults to `dev` mode. |
| Execution Gateway down | API calls fail | UI shows error banners and retries. |
| Redis unavailable | Metrics sidecar staleness update fails | 503 on `/metrics` with error log. |

## Known Issues & TODO
| Issue | Severity | Description | Tracking |
|-------|----------|-------------|----------|
| None | - | No known issues | - |

## Related Specs
- `docs/SPECS/services/execution_gateway.md`
- `docs/SPECS/services/auth_service.md`
- `docs/SPECS/libs/web_console_auth.md`

## Metadata
- **Last Updated:** 2026-01-03
- **Source Files:** `apps/web_console/app.py`, `apps/web_console/metrics_server.py`, `apps/web_console/config.py`, `apps/web_console/utils/validators.py`
- **ADRs:** N/A
