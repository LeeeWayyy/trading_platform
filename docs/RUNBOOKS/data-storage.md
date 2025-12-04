# Data Storage Runbook

## Overview

This runbook covers disk monitoring, storage management, and cleanup procedures for the local data warehouse.

## Storage Budget

| Component | Size | Location |
|-----------|------|----------|
| CRSP Daily | ~5-10 GB | `data/wrds/crsp/` |
| Compustat | ~2-5 GB | `data/wrds/compustat/` |
| Fama-French | ~100 MB | `data/wrds/fama_french/` |
| Manifests | ~10 MB | `data/manifests/` |
| Snapshots | ~50% of data | `data/manifests/snapshots/` |
| Quarantine | Variable | `data/quarantine/` |

**Total Budget:** ~200 GB initial, ~500 GB with versioning

## Disk Monitoring

### Watermarks

| Level | Threshold | Action |
|-------|-----------|--------|
| OK | < 80% | Normal operation |
| Warning | 80% | Alert, review cleanup |
| Critical | 90% | Alert, immediate cleanup |
| Blocked | 95% | Sync blocked |

### Check Disk Usage

```bash
# Overall disk usage
df -h data/

# Per-dataset usage
du -sh data/wrds/*/

# Quarantine size
du -sh data/quarantine/
```

### Monitoring Events

- `sync.disk.warning` - 80% threshold
- `sync.disk.critical` - 90% threshold
- `sync.disk.blocked` - 95% threshold

## Cleanup Procedures

### 1. Quarantine Cleanup

Quarantined data auto-expires after 7 days. Manual cleanup:

```bash
# List quarantine contents
ls -la data/quarantine/

# Remove old quarantine (> 7 days)
find data/quarantine/ -type d -mtime +7 -exec rm -rf {} +

# Or remove specific quarantine
rm -rf data/quarantine/20240115_103000_crsp_sync/
```

### 2. Snapshot Cleanup

90-day retention for snapshots:

```bash
# List snapshots
ls -la data/manifests/snapshots/

# Remove old snapshots (> 90 days)
find data/manifests/snapshots/ -type d -mtime +90 -exec rm -rf {} +
```

**Important:** Never delete snapshots referenced by backtests.

### 3. Temp File Cleanup

Temp files should auto-cleanup, but if orphaned:

```bash
# Find orphaned temp files
find data/ -name "*.tmp" -mtime +1

# Remove if confirmed orphaned
find data/ -name "*.tmp" -mtime +1 -delete
```

### 4. DuckDB Temp Cleanup

```bash
# Clear DuckDB temp files
rm -rf data/tmp/duckdb/*
```

## Storage Expansion

If approaching limits:

1. **Add storage:**
   - Expand disk/volume
   - Move to larger storage

2. **Reduce retention:**
   - Shorten snapshot retention (default 90 days)
   - Archive old datasets to cold storage

3. **Optimize partitioning:**
   - Use yearly partitions (default)
   - Consider monthly for high-volume datasets

## Alerts Configuration

```yaml
# prometheus/alerts/storage.yml
groups:
  - name: storage
    rules:
      - alert: DiskUsageWarning
        expr: disk_used_percent{path="/data"} > 80
        for: 5m
        labels:
          severity: warning

      - alert: DiskUsageCritical
        expr: disk_used_percent{path="/data"} > 90
        for: 5m
        labels:
          severity: critical
```

## Emergency Procedures

### Disk Full During Sync

1. Sync auto-aborts on ENOSPC
2. Temp files quarantined
3. Manifest NOT updated (data remains consistent)

**Recovery:**
```bash
# Free space
rm -rf data/quarantine/*
rm -rf data/tmp/*

# Retry sync
python scripts/wrds_sync.py incremental --dataset crsp
```
