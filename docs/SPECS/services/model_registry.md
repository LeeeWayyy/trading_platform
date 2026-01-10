# Model Registry Service

## Identity
- **Type:** Service
- **Port:** 8003
- **Container:** N/A

## Interface
### Public API Endpoints
| Endpoint | Method | Parameters | Returns |
|----------|--------|------------|---------|
| `/` | GET | None | Service metadata JSON |
| `/health` | GET | None | Health status JSON |
| `/api/v1/models/{model_type}/current` | GET | Path `model_type` | `CurrentModelResponse` |
| `/api/v1/models/{model_type}/{version}` | GET | Path `model_type`, `version` | `ModelMetadataResponse` |
| `/api/v1/models/{model_type}/{version}/validate` | POST | Path `model_type`, `version` | `ValidationResultResponse` |
| `/api/v1/models/{model_type}` | GET | Path `model_type`, query `status_filter?` | `ModelListResponse` |

## Behavioral Contracts
### Key Functions
#### get_current_model(model_type) -> CurrentModelResponse
**Purpose:** Return the current production model for a given type.

**Preconditions:**
- Registry initialized during lifespan.
- Request includes valid auth token with `model:read` scope.

**Postconditions:**
- Returns version/checksum/dataset ids for production model.

**Raises:**
- `HTTPException 404` if no production model exists.
- `HTTPException 503` on registry lock or integrity errors.

#### validate_model(model_type, version) -> ValidationResultResponse
**Purpose:** Validate model integrity and loadability.

**Preconditions:**
- Registry initialized.
- Auth token has `model:write` scope.

**Postconditions:**
- Returns validation status; 422 on checksum/load failure.

**Behavior:**
1. Verify checksum integrity.
2. Attempt to load model artifact.
3. Return validation result or raise 422 with error details.

#### list_models(model_type, status_filter?) -> ModelListResponse
**Purpose:** List models and merge registry manifest with DB metadata.

**Preconditions:**
- Registry initialized.
- Auth token has `model:read` scope.

**Postconditions:**
- Returns list of `ModelMetadataResponse` with status and artifact path.

### Invariants
- Auth is always enforced (auth disable flag is rejected at startup).
- Registry manifest integrity is verified on startup if manifest exists.

## Data Flow
```
Request -> Auth -> ModelRegistry (manifest + DB) -> Response
```
- **Input format:** REST requests with bearer token.
- **Output format:** JSON metadata responses.
- **Side effects:** None (read-only API).

## Dependencies
- **Internal:** `libs.models`, `apps/model_registry/auth.py`, `apps/model_registry/schemas.py`, `apps/model_registry/routes.py`.
- **External:** File system registry (`MODEL_REGISTRY_DIR`), Postgres (registry DB), Prometheus (via service logging only).

## Configuration
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `MODEL_REGISTRY_DIR` | No | `data/models` | Registry directory |
| `MODEL_REGISTRY_HOST` | No | `0.0.0.0` | Bind host |
| `MODEL_REGISTRY_PORT` | No | `8003` | Bind port |
| `MODEL_REGISTRY_READ_TOKEN` | Yes | none | Bearer token for read scope |
| `MODEL_REGISTRY_ADMIN_TOKEN` | No | none | Bearer token for admin scope |
| `MODEL_REGISTRY_TOKEN` | No | none | Legacy read-only token |
| `ALLOWED_ORIGINS` | Cond. | none | Required in production |
| `ENVIRONMENT` | No | `production` | CORS defaults |

## Observability (Services only)
### Health Check
- **Endpoint:** `/health`
- **Checks:** None beyond process liveness; reports timestamp.

### Metrics
- N/A (no Prometheus metrics exposed in this service).

## Security
- **Auth Required:** Yes
- **Auth Method:** HTTP Bearer tokens with scope checks (`model:read`, `model:write`, `model:admin`)
- **Data Sensitivity:** Internal
- **RBAC Roles:** Scope-based (read/write/admin)

## Testing
- **Test Files:** `tests/libs/models/` (registry behavior)
- **Run Tests:** `pytest tests/libs/models -v`
- **Coverage:** N/A

## Usage Examples
### Example 1: Health check
```bash
curl -s http://localhost:8003/health
```

### Example 2: Fetch current model
```bash
curl -s http://localhost:8003/api/v1/models/risk_model/current   -H 'Authorization: Bearer $MODEL_REGISTRY_READ_TOKEN'
```

## Edge Cases & Boundaries
| Scenario | Input | Expected Behavior |
|----------|-------|-------------------|
| Missing auth token | No `Authorization` header | 401/403 response. |
| Missing model version | Unknown `model_type` | 404 response. |
| Validation failure | Bad checksum | 422 with validation details. |

## Known Issues & TODO
| Issue | Severity | Description | Tracking |
|-------|----------|-------------|----------|
| None | - | No known issues | - |

## Related Specs
- `signal_service.md`
- `../libs/models.md`

## Metadata
- **Last Updated:** 2026-01-09 (housekeeping refresh)
- **Source Files:** `apps/model_registry/main.py`, `apps/model_registry/routes.py`, `apps/model_registry/auth.py`, `apps/model_registry/schemas.py`, `libs/models`
- **ADRs:** N/A
