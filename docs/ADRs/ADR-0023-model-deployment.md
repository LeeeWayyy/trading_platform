# ADR-0023: Model Registry and Deployment Architecture

## Status
Accepted

## Date
2025-12-08

## Context

The trading platform requires a production-ready model registry for versioned model storage, deployment, and hot-reloading. The system must:

1. **Store and version** risk models, alpha weights, factor definitions, and feature transforms
2. **Track provenance** with linkage to P4T1 dataset versions and snapshots
3. **Enable hot-reloading** without service restarts
4. **Provide DR capabilities** with manifest-based discoverability
5. **Cache computed factors** with Point-in-Time (PIT) safety

We evaluated several approaches:

### Option 1: MLflow Model Registry
- **Pros:** Industry standard, good experiment tracking, built-in UI
- **Cons:** Heavyweight dependency, server deployment required, schema inflexibility

### Option 2: DVC (Data Version Control)
- **Pros:** Git-based versioning, remote storage support
- **Cons:** No native registry features, limited metadata, no hot-reload

### Option 3: Custom DuckDB-Based Registry
- **Pros:** Lightweight, embeddable, full control over schema, local-first
- **Cons:** Custom implementation effort, no built-in experiment tracking

## Decision

We implement a **custom DuckDB-based model registry** with the following architecture:

### 1. Storage Architecture

```
data/models/
├── registry.db           # DuckDB catalog (query index)
├── manifest.json         # Registry manifest (DR/discoverability)
├── artifacts/
│   ├── risk_model/
│   │   ├── v1.0.0/
│   │   │   ├── model.pkl
│   │   │   ├── metadata.json   # AUTHORITATIVE source
│   │   │   └── checksum.sha256
│   │   └── v1.1.0/
│   ├── alpha_weights/
│   └── factor_definitions/
└── backups/              # Daily backups
```

### 2. Authoritative Data Source

- **metadata.json sidecar** is the AUTHORITATIVE source for full metadata
- **DuckDB** stores key fields for efficient querying/filtering
- API responses load from metadata.json to ensure complete provenance

### 3. DuckDB Schema

```sql
CREATE TABLE models (
    model_id VARCHAR PRIMARY KEY,
    model_type VARCHAR NOT NULL,
    version VARCHAR NOT NULL,
    status VARCHAR NOT NULL DEFAULT 'staged',
    artifact_path VARCHAR NOT NULL,
    checksum_sha256 VARCHAR NOT NULL,
    created_at TIMESTAMP NOT NULL,
    promoted_at TIMESTAMP,
    archived_at TIMESTAMP,
    config_hash VARCHAR NOT NULL,
    snapshot_id VARCHAR NOT NULL,
    dataset_version_ids_json VARCHAR NOT NULL,
    metrics_json VARCHAR,
    factor_list_json VARCHAR,
    experiment_id VARCHAR,
    run_id VARCHAR,
    UNIQUE(model_type, version),
    CHECK (status IN ('staged', 'production', 'archived', 'failed'))
);

CREATE TABLE promotion_history (
    id INTEGER PRIMARY KEY,
    model_id VARCHAR NOT NULL,
    from_status VARCHAR NOT NULL,
    to_status VARCHAR NOT NULL,
    changed_at TIMESTAMP NOT NULL,
    changed_by VARCHAR NOT NULL
);
```

### 4. Version Compatibility Policy

**STRICT_VERSION_MODE** controls version drift handling:

| Scenario | STRICT_VERSION_MODE=true | STRICT_VERSION_MODE=false |
|----------|-------------------------|---------------------------|
| Exact match | ALLOW | ALLOW |
| ANY version drift | BLOCK | WARN + ALLOW |
| Missing dataset | BLOCK | BLOCK |

This is stricter than semantic versioning - ANY version mismatch triggers the policy.

### 5. Promotion Gates

Before promotion to production, models must pass:

| Gate | Threshold |
|------|-----------|
| Information Coefficient (IC) | > 0.02 |
| Sharpe Ratio | > 0.5 |
| Paper Trading Period | >= 24 hours |

### 6. DiskExpressionCache

5-component cache key for PIT safety:

```
{factor_name}:{as_of_date}:{dataset_version_id}:{snapshot_id}:{config_hash}
```

Where `dataset_version_id` is deterministic: `crsp-v1.2.3_compustat-v1.0.1` (sorted keys).

### 7. ProductionModelLoader

```python
class ProductionModelLoader:
    """Hot-reload models with fallback and circuit breaker."""

    def get_risk_model(self, version: str | None = None) -> BarraRiskModel
    def get_alpha_weights(self, version: str | None = None) -> dict[str, float]
    def check_compatibility(self, model_id: str, current_versions: dict[str, str]) -> CompatibilityResult
```

Features:
- Version polling (60s default)
- Atomic model swap (load → validate → swap)
- Fallback to last-good version on failure
- Circuit breaker (3 consecutive failures → trip)
- In-memory cache with 24h TTL

### 8. FastAPI Endpoints

```
GET  /api/v1/models/{model_type}/current      # model:read scope
GET  /api/v1/models/{model_type}/{version}    # model:read scope
POST /api/v1/models/{model_type}/{version}/validate  # model:write scope
GET  /api/v1/models/{model_type}              # model:read scope (list)
```

JWT authentication with scopes: `model:read`, `model:write`, `model:admin`.

## Consequences

### Positive

1. **Lightweight** - No external server dependencies, embedded DuckDB
2. **Full provenance** - Linked to P4T1 dataset versions and snapshots
3. **Hot-reloadable** - Signal service can reload models without restart
4. **DR-ready** - Manifest enables discoverability after disaster
5. **Cache safety** - 5-component key prevents stale data

### Negative

1. **Single-writer** - DuckDB single-writer pattern limits concurrent registration
2. **Custom implementation** - No MLflow ecosystem compatibility
3. **Local storage** - Primary storage is local (backup to S3/GCS optional)

### Mitigations

1. **Single-writer** - Production use case is read-heavy, writes are rare
2. **Custom implementation** - Full control over schema and validation
3. **Local storage** - Daily backups with 90-day retention

## Implementation Details

### File Structure

```
libs/models/
├── __init__.py            # Module exports
├── types.py               # ModelMetadata, ModelType, PromotionGates
├── serialization.py       # Pickle/joblib with checksums
├── manifest.py            # RegistryManifestManager
├── registry.py            # ModelRegistry (DuckDB)
├── loader.py              # ProductionModelLoader
├── compatibility.py       # VersionCompatibilityChecker
└── backup.py              # RegistryBackupManager, RegistryGC

libs/factors/
└── cache.py               # DiskExpressionCache

apps/model_registry/
├── main.py                # FastAPI application
├── routes.py              # API endpoints
├── schemas.py             # Request/response models
└── auth.py                # JWT authentication

scripts/
└── model_cli.py           # CLI for model management
```

### Key Classes

| Class | Purpose |
|-------|---------|
| `ModelRegistry` | DuckDB-based catalog with CRUD operations |
| `ProductionModelLoader` | Hot-reload with circuit breaker |
| `VersionCompatibilityChecker` | Version drift detection |
| `DiskExpressionCache` | PIT-safe factor caching |
| `RegistryManifestManager` | Manifest lifecycle |
| `RegistryBackupManager` | Backup and restore |

### Per-Artifact Required Fields

| Model Type | Required Fields |
|------------|-----------------|
| risk_model | factor_list, halflife_days, shrinkage_intensity |
| alpha_weights | alpha_names, combination_method, ic_threshold |
| factor_definitions | factor_names, categories, lookback_days |
| feature_transforms | feature_names, normalization_params |

## Security Considerations

### ⚠️ Pickle Deserialization Risk (RCE)

**WARNING:** Model artifacts use Python `pickle` for serialization. Loading untrusted pickle files can execute arbitrary code (Remote Code Execution vulnerability).

**Current Mitigations:**
- SHA-256 checksum verification (integrity, not authenticity)
- Atomic file writes to prevent partial loads
- Filesystem access controls

**Required Operating Constraints:**
- **ONLY load artifacts produced by trusted CI from the secured registry**
- Registry storage must be non-user-writable
- Do not accept user-uploaded or external model artifacts
- Run deserialization in low-privilege service containers

**Future Work:** Migrate to safe serialization formats (safetensors/ONNX) or implement cryptographic signature verification (Ed25519) before unpickling. See follow-up task tracking.

### ⚠️ Single Token Authentication

**WARNING:** The Model Registry API uses a single shared bearer token (`MODEL_REGISTRY_TOKEN`) with admin-equivalent scopes. No per-service RBAC or granular permissions.

**Current Mitigations:**
- Fail-closed behavior when token is unset (503 response)
- Production environment blocks auth disable
- Token required for all write operations

**Required Operating Constraints:**
- Store token in a secret manager (not environment files)
- Rotate token regularly (support dual-token during rotation)
- Internal network access only - do not expose to external networks
- Log caller source IP for audit trail

**Future Work:** Implement JWT-based authentication with signed scope claims when external access or multi-service RBAC is required.

## Related Documentation

- [T2.8 Task](../TASKS/P4T2_TASK.md): Task specification
- [model-registry.md](../CONCEPTS/model-registry.md): User guide
- [model-registry-dr.md](../RUNBOOKS/model-registry-dr.md): DR procedures
