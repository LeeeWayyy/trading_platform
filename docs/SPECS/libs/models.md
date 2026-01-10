# models

## Identity
- **Type:** Library
- **Port:** N/A
- **Container:** N/A

## Interface
### Public Interface (Exported Classes & Functions)
| Class/Function | Parameters | Returns | Description |
|----------------|------------|---------|-------------|
| `ModelRegistry` | registry_dir, version_manager | registry | Register, promote, and load model artifacts. |
| `ModelMetadata` | fields | model | Full model metadata and provenance. |
| `ModelType` | - | enum | Model type taxonomy. |
| `generate_model_id` | - | str | Create unique model IDs. |
| `PromotionGates` | fields | model | Gate thresholds for promotion. |
| `PromotionResult` | fields | model | Result of promotion attempt. |
| `ProductionModelLoader` | config | loader | Load production models with caching. |
| `serialize_model` | model, metadata | bytes | Serialize artifact with checksum. |
| `deserialize_model` | payload | model | Deserialize artifact and metadata. |
| `RegistryManifestManager` | paths | manager | Registry manifest for DR/consistency. |
| `RegistryBackupManager` | paths | manager | Backups and GC for registry. |
| `VersionCompatibilityChecker` | versions | checker | Validate dataset/model compatibility. |
| `IntegrityError` | message | exception | Registry integrity errors. |
| `VersionExistsError` | message | exception | Duplicate version error. |

## Behavioral Contracts
### ModelRegistry.register_model(...)
**Purpose:** Register a new model artifact with immutable versioning.

**Preconditions:**
- Artifact metadata is valid and includes required fields.
- Version does not already exist.

**Postconditions:**
- Artifact and metadata persisted with checksum.
- Registry manifest updated atomically.

**Behavior:**
1. Validate metadata and required fields.
2. Serialize artifact and compute checksum.
3. Write artifact and metadata to registry.
4. Update registry manifest and index.

**Raises:**
- `VersionExistsError`, `IntegrityError`, `MissingRequiredFieldError`.

### ModelRegistry.promote_model(model_type, version) -> PromotionResult
**Purpose:** Promote a model version to production after gate checks.

**Preconditions:**
- Version exists and passes promotion gates.

**Postconditions:**
- Production pointer updated to target version.

**Behavior:**
1. Validate model metrics vs `PromotionGates`.
2. Update registry manifest/current pointer.
3. Return `PromotionResult` with pass/fail.

**Raises:**
- `PromotionGateError` on gate failures.

### Invariants
- Versions are immutable; no overwrite once registered.
- Checksums must match serialized artifacts.

### State Machine (if stateful)
```
[Registered] --> [Validated] --> [Promoted]
      |               |
      +---------------+ (rollback)
```
- **States:** registered, validated, promoted.
- **Transitions:** promotion gates control moves.

## Data Flow
```
model artifact -> serialization -> registry storage -> manifest/index
```
- **Input format:** Python model artifacts + metadata.
- **Output format:** Serialized artifact + metadata files.
- **Side effects:** File writes in registry directory.

## Usage Examples
### Example 1: Register model
```python
model_id = registry.register_model(model_obj, metadata)
```

### Example 2: Promote model
```python
result = registry.promote_model("risk_model", "v1.0.0")
```

## Edge Cases & Boundaries
| Scenario | Input | Expected Behavior |
|----------|-------|-------------------|
| Duplicate version | existing version | `VersionExistsError` |
| Invalid metadata | missing fields | `MissingRequiredFieldError` |
| Checksum mismatch | corrupted file | `ChecksumMismatchError` |

## Dependencies
- **Internal:** `libs.data_quality`
- **External:** File system, DuckDB (registry metadata)

## Configuration
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| N/A | - | - | Config passed via registry initialization. |

## Error Handling
- Typed errors for integrity, compatibility, and serialization failures.

## Observability (Services only)
### Health Check
- **Endpoint:** N/A
- **Checks:** N/A

### Metrics
| Metric Name | Type | Labels | Description |
|-------------|------|--------|-------------|
| N/A | - | - | Library has no metrics. |

## Security
- Model artifacts are stored with checksums; access control enforced by callers.

## Testing
- **Test Files:** `tests/libs/models/`
- **Run Tests:** `pytest tests/libs/models -v`
- **Coverage:** N/A

## Related Specs
- `data_quality.md`
- `model_registry.md`

## Known Issues & TODO
| Issue | Severity | Description | Tracking |
|-------|----------|-------------|----------|
| None | - | No known issues | - |

## Metadata
- **Last Updated:** 2026-01-09
- **Source Files:** `libs/models/__init__.py`, `libs/models/registry.py`, `libs/models/serialization.py`, `libs/models/loader.py`, `libs/models/manifest.py`, `libs/models/types.py`, `libs/models/backup.py`, `libs/models/compatibility.py`
- **ADRs:** N/A
