# DuckDB Operations Runbook

## Overview

DuckDB is used for querying local Parquet files. This runbook covers cache management and reader configuration during sync operations.

## Architecture

```
SINGLE-WRITER, MULTI-READER
- Writers: Use atomic_lock() before any writes
- Readers: Use read-only connections, may see previous snapshot during sync
```

## Reader Configuration

### Long-Lived Sessions

For notebooks or interactive sessions:

```python
import duckdb

conn = duckdb.connect(read_only=True)
conn.execute("PRAGMA disable_object_cache;")  # Prevents stale reads

# Or reconnect per query
def query(sql):
    with duckdb.connect(read_only=True) as conn:
        return conn.execute(sql).fetchdf()
```

### Sync-Aware Readers

```python
from libs.data_quality.manifest import ManifestManager

mm = ManifestManager()

# Check manifest version before query
manifest = mm.load_manifest("crsp")
version_at_start = manifest.manifest_version

# Execute query
result = conn.execute("SELECT * FROM 'data/wrds/crsp/*.parquet'").fetchdf()

# Verify version unchanged
current = mm.load_manifest("crsp")
if current.manifest_version != version_at_start:
    # Retry with new data
    pass
```

## Cache Invalidation

### When to Invalidate

- After sync completes
- When manifest version changes
- When switching datasets

### How to Invalidate

```python
# Option 1: Disable cache (recommended for long sessions)
conn.execute("PRAGMA disable_object_cache;")

# Option 2: Reconnect
conn.close()
conn = duckdb.connect(read_only=True)

# Option 3: Clear file cache (DuckDB 0.9+)
conn.execute("CALL pg_clear_cache();")
```

## During Sync Windows

**Behavior:**
- Readers see previous snapshot (atomic rename ensures consistency)
- No partial reads possible
- No `.tmp` files visible to readers

**Recommendations:**
- Schedule syncs during off-hours (overnight)
- Long queries may need retry if manifest changes
- Use `PRAGMA disable_object_cache;` for always-fresh reads

## Troubleshooting

### Stale Data After Sync

```python
# Force cache refresh
conn.close()
conn = duckdb.connect(read_only=True)
conn.execute("PRAGMA disable_object_cache;")
```

### Memory Issues

```python
# Limit memory usage
conn.execute("PRAGMA memory_limit='4GB';")
conn.execute("PRAGMA threads=4;")
```

## Monitoring

- Queries should not see `.tmp` files (indicates atomic write failure)
- Monitor manifest version changes during long-running queries
