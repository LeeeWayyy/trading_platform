# data_quality

## Identity
- **Type:** Library
- **Port:** N/A
- **Container:** N/A

## Interface
### Public Interface (Exported Classes & Functions)
| Class/Function | Parameters | Returns | Description |
|----------------|------------|---------|-------------|
| `SyncManifest` | fields | model | Tracks data sync metadata and validation state. |
| `ManifestManager` | storage paths | manager | Atomic manifest read/write with locking. |
| `DataValidator` | config | validator | Validates row counts, nulls, schema, dates. |
| `SchemaRegistry` | root path | registry | Schema versioning and drift detection. |
| `DatasetVersionManager` | paths | manager | Dataset snapshot/version management (CAS + diffs). |
| `DatasetSchema` | fields | model | Schema definition for datasets. |
| `SchemaDrift` | fields | model | Schema drift report. |
| `AnomalyAlert` | fields | model | Validation anomaly record. |
| `ValidationError` | message | exception | Validation error.
| `LockToken` | host, pid, timestamp | model | File lock token. |
| `DiskSpaceStatus` | usage | model | Disk usage status report. |
| `SyncValidationError` | message | exception | Sync validation failure. |
| `SnapshotNotFoundError` | message | exception | Snapshot not found. |

## Behavioral Contracts
### ManifestManager.write_manifest(...)
**Purpose:** Atomically write sync manifest with locking and disk checks.

**Preconditions:**
- Lock is held for the manifest path.
- Sufficient disk space available (>= required multiplier).

**Postconditions:**
- Manifest file written atomically and fsynced.
- Backup and quarantine paths updated on failure.

**Behavior:**
1. Acquire lock (file-based, stale detection).
2. Validate disk space and file paths.
3. Write temp file, verify checksum, rename atomically.

**Raises:**
- `LockNotHeldError`, `DiskSpaceError`, `QuarantineError`.

### DataValidator.validate(...)
**Purpose:** Validate dataset integrity before use.

**Preconditions:**
- Dataset files exist and are readable.

**Postconditions:**
- Returns list of `AnomalyAlert` or raises `ValidationError`.

**Behavior:**
1. Check row counts and null rates.
2. Validate schema version and column types.
3. Validate date ranges and trading calendar alignment.

**Raises:**
- `ValidationError` on validation failures.

### Invariants
- Sync manifests are written atomically; partial writes are not allowed.
- Schema drift is recorded and must not silently pass.

### State Machine (if stateful)
```
[Draft] --> [Validated] --> [Quarantined]
      |           |
      +-----------+ (re-validate)
```
- **States:** draft, validated, quarantined.
- **Transitions:** validation pass/fail controls state.

## Data Flow
```
raw dataset -> validator -> manifest -> snapshot/versioning
                    |
                    v
              anomaly alerts
```
- **Input format:** Parquet paths, schema definitions, date ranges.
- **Output format:** Validation reports + manifests/snapshots.
- **Side effects:** File writes under `data/manifests` and `data/quarantine`.

## Usage Examples
### Example 1: Write manifest
```python
from libs.data.data_quality import ManifestManager, SyncManifest

manager = ManifestManager()
manager.write_manifest(dataset, manifest)
```

### Example 2: Validate dataset
```python
from libs.data.data_quality import DataValidator

validator = DataValidator()
alerts = validator.validate(dataset_path)
```

## Edge Cases & Boundaries
| Scenario | Input | Expected Behavior |
|----------|-------|-------------------|
| Low disk space | usage > 95% | Writes blocked with `DiskSpaceError` |
| Stale lock | lock older than threshold | Lock can be broken after timeout |
| Schema drift | columns changed | `SchemaDrift` recorded; validation fails |

## Dependencies
- **Internal:** `libs.data_quality.*`
- **External:** File system, Pydantic

## Configuration
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DATA_LOCK_DIR` | No | `data/locks` | Lock directory for manifests. |

## Error Handling
- Typed exceptions for disk, lock, schema, and snapshot errors.

## Observability (Services only)
### Health Check
- **Endpoint:** N/A
- **Checks:** N/A

### Metrics
| Metric Name | Type | Labels | Description |
|-------------|------|--------|-------------|
| N/A | - | - | Library has no metrics. |

## Security
- Uses file locks to prevent concurrent writers.
- Prevents checksum mismatches and corrupted snapshots.

## Testing
- **Test Files:** `tests/libs/data/data_quality/`
- **Run Tests:** `pytest tests/libs/data_quality -v`
- **Coverage:** N/A

## Related Specs
- `data_pipeline.md`
- `models.md`

## Known Issues & TODO
| Issue | Severity | Description | Tracking |
|-------|----------|-------------|----------|
| None | - | No known issues | - |

## Metadata
- **Last Updated:** 2026-01-10
- **Source Files:** `libs/data/data_quality/__init__.py`, `libs/data/data_quality/manifest.py`, `libs/data/data_quality/validation.py`, `libs/data/data_quality/schema.py`, `libs/data/data_quality/versioning.py`, `libs/data/data_quality/exceptions.py`, `libs/data/data_quality/types.py`
- **ADRs:** N/A
