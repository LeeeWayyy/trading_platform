# Signal Service

## Identity
- **Type:** Service
- **Port:** 8001
- **Container:** N/A

## Interface
### Public API Endpoints
| Endpoint | Method | Parameters | Returns |
|----------|--------|------------|---------|
| `/` | GET | None | Service metadata JSON |
| `/health` | GET | None | `HealthResponse` (model/redis/cache status) |
| `/ready` | GET | None | `HealthResponse` (503 if not fully healthy) |
| `/api/v1/signals/generate` | POST | `SignalRequest` (symbols, as_of_date?, top_n?, bottom_n?) | `SignalResponse` |
| `/api/v1/features/precompute` | POST | `PrecomputeRequest` (symbols, as_of_date?) | `PrecomputeResponse` |
| `/api/v1/model/info` | GET | None | Model metadata JSON |
| `/api/v1/model/reload` | POST | None | Reload status JSON |
| `/metrics` | GET | None | Prometheus metrics |

## Behavioral Contracts
### Key Functions
#### generate_signals(request: SignalRequest) -> SignalResponse
**Purpose:** Generate ranked signals and target weights using the currently loaded model.

**Preconditions:**
- `model_registry` initialized and `is_loaded` is True.
- `signal_generator` initialized in lifespan.
- If `as_of_date` provided, it must be ISO `YYYY-MM-DD`.
- `top_n + bottom_n <= len(symbols)`.

**Postconditions:**
- Returns signals sorted/ranked with weights that sum to +1.0 (longs) and -1.0 (shorts).
- Emits Prometheus metrics and (if Redis enabled) publishes a `SignalEvent`.

**Behavior:**
1. Validate model availability and request parameters.
2. Resolve `as_of_date` (default UTC now).
3. Use cached `SignalGenerator` for overridden `top_n/bottom_n` (LRU size 10).
4. Generate features (Alpha158 parity), predict returns, rank, and weight.
5. Publish signal event to Redis with fallback buffer on failures.

**Raises:**
- `HTTPException 400` on invalid date or parameters.
- `HTTPException 404` on missing data.
- `HTTPException 503` when model not loaded or registry missing.
- `HTTPException 500` for internal errors.

#### precompute_features(request: PrecomputeRequest) -> PrecomputeResponse
**Purpose:** Warm the feature cache without model inference.

**Preconditions:**
- `signal_generator` initialized.
- If `as_of_date` provided, it must be ISO `YYYY-MM-DD`.

**Postconditions:**
- Returns counts for cached/skipped symbols.
- No model inference is performed.

**Behavior:**
1. Validate request and normalize symbols to uppercase.
2. Compute features and populate Redis cache when enabled.
3. Continue on per-symbol errors; return partial results.

**Raises:**
- `HTTPException 400` on invalid date format.
- `HTTPException 503` if generator not initialized.

#### ModelRegistry.reload_if_changed(...)
**Purpose:** Hot-reload model when registry version changes.

**Preconditions:**
- Database reachable and registry initialized.

**Postconditions:**
- If a newer version exists and loads successfully, it becomes active.
- If reload fails and a model is already loaded, the previous model remains active.

**Behavior:**
1. Query active model version.
2. Compare with current version.
3. Load/validate new model, optionally shadow-validate.
4. Activate model and update metrics.

### Invariants
- Model must be loaded before signal generation (unless `testing=true`).
- Signal generation uses the same feature pipeline as research (feature parity).
- Redis publish failures buffer events and replay when Redis recovers.
- Shadow validation gates activation when enabled.

## Data Flow
```
Request -> Validate -> Feature Generation -> Model Predict -> Rank/Weight -> Response
                         |                                   |
                         v                                   v
                   Redis Feature Cache                Redis Signal Event
```
- **Input format:** JSON request with symbols and optional date/portfolio params.
- **Output format:** JSON response with per-symbol predicted_return, rank, target_weight.
- **Side effects:** Redis cache writes, Redis event publishing, Prometheus metrics.

## Dependencies
- **Internal:** `apps/signal_service/model_registry.py`, `apps/signal_service/signal_generator.py`, `strategies/alpha_baseline/*`, `libs.redis_client`, `libs.common.api_auth_dependency`, `libs.common.rate_limit_dependency`, `libs.common.secrets`, `libs.web_console_auth.permissions`.
- **External:** Postgres (model registry table), Redis (optional), Prometheus, Parquet data in `data/adjusted`.

## Configuration
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `HOST` | No | `0.0.0.0` | Bind address (main entrypoint) |
| `PORT` | No | `8001` | Service port |
| `DEBUG` | No | `false` | Enable debug mode with auto-reload (never for prod) |
| `TESTING` | No | `false` | Enable CI/E2E test mode (allows startup without active model) |
| `DATABASE_URL` | Yes | `postgresql://trader:trader@localhost:5433/trader` | Model registry DB |
| `DATA_DIR` | No | `data/adjusted` | T1 data directory |
| `DEFAULT_STRATEGY` | No | `alpha_baseline` | Strategy to load |
| `TRADABLE_SYMBOLS` | No | `["AAPL","MSFT","GOOGL","AMZN","TSLA"]` | Tradable universe for signal generation |
| `TOP_N` | No | `3` | Long positions |
| `BOTTOM_N` | No | `3` | Short positions |
| `MODEL_RELOAD_INTERVAL_SECONDS` | No | `300` | Poll interval |
| `LOG_LEVEL` | No | `INFO` | Logging verbosity (DEBUG/INFO/WARNING/ERROR) |
| `REDIS_ENABLED` | No | `false` | Enable Redis cache/events |
| `REDIS_HOST` | No | `localhost` | Redis host |
| `REDIS_PORT` | No | `6379` | Redis port |
| `REDIS_DB` | No | `0` | Redis DB |
| `REDIS_TTL` | No | `3600` | Feature cache TTL |
| `REDIS_FALLBACK_BUFFER_MAX_SIZE` | No | `1000` | Buffered signal events |
| `REDIS_FALLBACK_BUFFER_PATH` | No | `None` | Persist fallback buffer |
| `REDIS_FALLBACK_REPLAY_INTERVAL_SECONDS` | No | `5` | Replay interval |
| `FEATURE_HYDRATION_ENABLED` | No | `true` | Startup cache warmup |
| `FEATURE_HYDRATION_TIMEOUT_SECONDS` | No | `300` | Hydration timeout |
| `SHADOW_VALIDATION_ENABLED` | No | `true` | Gate model activation |
| `SHADOW_SAMPLE_COUNT` | No | `100` | Samples for validation |
| `SKIP_SHADOW_VALIDATION` | No | `false` | Emergency bypass |
| `ENVIRONMENT` | No | `dev` | CORS defaults |
| `ALLOWED_ORIGINS` | Cond. | none | Required in prod |
| `TRUSTED_PROXY_HOSTS` | No | `127.0.0.1` | Proxy IP allowlist |
| `SIGNAL_GENERATE_RATE_LIMIT` | No | `30` | Rate limit per minute |

## Observability (Services only)
### Health Check
- **Endpoint:** `/health`, `/ready`
- **Checks:** model loaded, metadata available, Redis connectivity, hydration status.

### Metrics
- `signal_service_requests_total{status}`
- `signal_service_signal_generation_duration_seconds`
- `signal_service_signals_generated_total{symbol}`
- `signal_service_model_predictions_total`
- `signal_service_model_reload_total{status}`
- `signal_service_shadow_validation_*` (correlation, diff ratio, sign change rate)
- `signal_service_database_connection_status`
- `signal_service_redis_connection_status`
- `signal_service_redis_fallback_buffer_size`

## Security
- **Auth Required:** Yes (signals/generate)
- **Auth Method:** `libs.common.api_auth_dependency` + RBAC permission `GENERATE_SIGNALS`
- **Data Sensitivity:** Internal
- **RBAC Roles:** Permission-gated via `libs.web_console_auth.permissions`

## Testing
- **Test Files:** `tests/apps/signal_service/`
- **Run Tests:** `pytest tests/apps/signal_service -v`
- **Coverage:** N/A

## Usage Examples
### Example 1: Health check
```bash
curl -s http://localhost:8001/health
```

### Example 2: Generate signals
```bash
curl -s -X POST http://localhost:8001/api/v1/signals/generate   -H 'Content-Type: application/json'   -d '{"symbols":["AAPL","MSFT"],"top_n":1,"bottom_n":1}'
```

## Edge Cases & Boundaries
| Scenario | Input | Expected Behavior |
|----------|-------|-------------------|
| Empty symbols list | `{"symbols":[]}` | 400 validation error. |
| Model not loaded | Startup incomplete | 503 response. |
| Invalid date | `as_of_date="bad"` | 400 validation error. |

## Known Issues & TODO
| Issue | Severity | Description | Tracking |
|-------|----------|-------------|----------|
| None | - | No known issues | - |

## Related Specs
- `execution_gateway.md`
- `model_registry.md`
- `../libs/redis_client.md`
- `../libs/web_console_auth.md`

## Metadata
- **Last Updated:** 2026-01-10
- **Source Files:** `apps/signal_service/main.py`, `apps/signal_service/config.py`, `apps/signal_service/signal_generator.py`, `apps/signal_service/model_registry.py`
- **ADRs:** `docs/ADRs/0004-signal-service-architecture.md`
