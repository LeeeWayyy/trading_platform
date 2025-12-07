# Data Backup & Restore Runbook

## Overview

This runbook covers backup and restore procedures for WRDS local data warehouse.

## Backup Strategy

### Storage Budget
| Dataset | Size | Retention |
|---------|------|-----------|
| CRSP Daily | ~5-10 GB | Permanent |
| Compustat | ~2-5 GB | Permanent |
| Fama-French | ~100 MB | Permanent |
| Snapshots | ~50% overhead | 90 days rolling |

### Backup Schedule
- **Daily:** Manifest files → `data/backups/daily/`
- **Weekly:** Full Parquet backup → external storage
- **On-demand:** Before major sync operations

### Tooling

```bash
# Using rclone for backups
rclone sync data/wrds/ remote:backups/wrds/ --config ~/.config/rclone/rclone.conf

# Local backup
rsync -av data/wrds/ /mnt/backup/wrds/
```

## Restore Procedure

### 1. From Daily Manifest Backup

```bash
# List available backups
ls -la data/backups/daily/

# Restore manifest
cp data/backups/daily/crsp_v3.json data/manifests/crsp.json

# Verify
python scripts/wrds_sync.py verify --dataset crsp
```

### 2. From Weekly Full Backup

```bash
# Sync from remote
rclone sync remote:backups/wrds/ data/wrds/ --progress

# Restore manifests
rclone sync remote:backups/manifests/ data/manifests/

# Verify all datasets
python scripts/wrds_sync.py verify --all
```

### 3. Emergency Re-sync

If backups unavailable, re-sync from WRDS:

```bash
python scripts/wrds_sync.py full-sync --dataset crsp --start-year 2000
```

## Monthly Drill Procedure

1. **Verify backup integrity:**
   ```bash
   rclone check data/wrds/ remote:backups/wrds/
   ```

2. **Test restore to staging:**
   ```bash
   mkdir -p /tmp/restore_test
   rclone sync remote:backups/wrds/ /tmp/restore_test/
   ```

3. **Verify checksums (SHA-256 per ADR-0019):**
   ```bash
   cd /tmp/restore_test && sha256sum -c manifest_checksums.txt
   ```

4. **Success criteria:**
   - All files present
   - All checksums match
   - Total time < 4 hours

## Encryption

- **At rest:** AES-256 for off-site backups
- **Key location:** Secrets manager (`backup/encryption_key`)
- **Key rotation:** Quarterly

## Monitoring

- **Alert:** `sync.backup.failed` - Backup exit code != 0
- **Alert:** Disk usage > 80% capacity
