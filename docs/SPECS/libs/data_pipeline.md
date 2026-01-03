# data_pipeline

## Identity
- **Type:** Library
- **Port:** N/A
- **Container:** N/A

## Interface
### Public Interface (Exported Classes & Functions)
| Class/Function | Parameters | Returns | Description |
|----------------|------------|---------|-------------|
| `HistoricalETL` | config | instance | Historical ETL pipeline with atomic writes. |
| `ETLProgressManifest` | path | model | Tracks ETL progress for resume. |
| `ETLResult` | fields | model | ETL outcome summary. |
| `ETLError` | message | exception | Base ETL exception. |

## Behavioral Contracts
### HistoricalETL.run(...)
**Purpose:** Download, validate, and persist historical datasets.

### Invariants
- Writes are atomic; partial outputs are cleaned on failure.

## Data Flow
```
source data -> validation -> atomic write -> manifest update
```
- **Input format:** provider data frames/files.
- **Output format:** parquet/datasets + manifest.
- **Side effects:** file system writes, manifest updates.

## Usage Examples
### Example 1: Run historical ETL
```python
from libs.data_pipeline import HistoricalETL

etl = HistoricalETL(...)
result = etl.run()
```

### Example 2: Read progress
```python
from libs.data_pipeline import ETLProgressManifest

manifest = ETLProgressManifest.load("path/to/manifest.json")
```

## Edge Cases & Boundaries
| Scenario | Input | Expected Behavior |
|----------|-------|-------------------|
| Disk full | insufficient space | `DiskSpaceError` raised. |
| Checksum mismatch | corrupted file | `ChecksumMismatchError` raised. |
| Missing data | provider gaps | `DataQualityError` raised. |

## Dependencies
- **Internal:** `libs.data_quality`
- **External:** filesystem, pandas/polars (data processing)

## Configuration
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| N/A | - | - | Configuration via constructor args. |

## Error Handling
- Raises `ETLError` subclasses for disk, checksum, and data quality issues.

## Security
- N/A (data processing library).

## Testing
- **Test Files:** `tests/libs/data_pipeline/`
- **Run Tests:** `pytest tests/libs/data_pipeline -v`
- **Coverage:** N/A

## Related Specs
- `data_quality.md`

## Known Issues & TODO
| Issue | Severity | Description | Tracking |
|-------|----------|-------------|----------|
| None | - | No known issues | - |

## Metadata
- **Last Updated:** 2026-01-03
- **Source Files:** `libs/data_pipeline/historical_etl.py`
- **ADRs:** N/A
