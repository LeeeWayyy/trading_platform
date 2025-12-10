# Model Registry Disaster Recovery Runbook

## Overview

This runbook covers disaster recovery (DR) procedures for the Model Registry, including backup/restore operations, corruption recovery, and version rollback procedures.

## Quick Reference

| Scenario | Command | Expected Time |
|----------|---------|---------------|
| List backups | `python scripts/model_cli.py backup list` | Immediate |
| Restore from backup | `python scripts/model_cli.py restore --from-backup 2024-01-15` | 1-5 min |
| Rollback production model | `python scripts/model_cli.py rollback risk_model` | < 1 min |
| Validate model integrity | `python scripts/model_cli.py validate risk_model v1.0.0` | < 30s |
| Run GC (dry-run) | `python scripts/model_cli.py gc --dry-run` | < 1 min |

---

## Scenario 1: Corrupt Registry Database

### Symptoms
- `DuckDBError: database disk image is malformed`
- Signal service fails to load models
- CLI commands return database errors

### Recovery Steps

1. **Stop affected services**
   ```bash
   # Stop signal_service if running
   pkill -f "signal_service"
   ```

2. **Verify corruption**
   ```bash
   # Check registry integrity
   python -c "
   from libs.models import ModelRegistry
   from pathlib import Path
   registry = ModelRegistry(Path('data/models'))
   print(registry.list_models('risk_model'))
   "
   ```

3. **Locate latest backup**
   ```bash
   # List available backups
   ls -la data/models/backups/

   # Or via CLI
   python scripts/model_cli.py backup list
   ```

4. **Restore from backup**
   ```bash
   # Restore most recent backup
   python scripts/model_cli.py restore --from-backup latest

   # Or specific date
   python scripts/model_cli.py restore --from-backup 2024-01-15
   ```

5. **Verify restoration**
   ```bash
   # Verify manifest
   python -c "
   from libs.models import RegistryManifestManager
   from pathlib import Path
   mgr = RegistryManifestManager(Path('data/models'))
   manifest = mgr.load_manifest()
   print(f'Production models: {manifest.production_models}')
   print(f'Artifact count: {manifest.artifact_count}')
   "
   ```

6. **Restart services**
   ```bash
   make up
   ```

---

## Scenario 2: Corrupt Model Artifact

### Symptoms
- `ChecksumMismatchError` when loading model
- Signal service logs checksum validation failures
- Model loads but produces NaN/invalid values

### Recovery Steps

1. **Identify corrupt artifact**
   ```bash
   # Validate specific model
   python scripts/model_cli.py validate risk_model v1.0.0

   # Output shows checksum status
   ```

2. **Check artifact files**
   ```bash
   # List artifact directory
   ls -la data/models/artifacts/risk_model/v1.0.0/

   # Verify checksum manually
   sha256sum data/models/artifacts/risk_model/v1.0.0/model.pkl
   cat data/models/artifacts/risk_model/v1.0.0/checksum.sha256
   ```

3. **Rollback to previous version**
   ```bash
   # If corrupt version is in production, rollback immediately
   python scripts/model_cli.py rollback risk_model

   # Verify rollback
   python scripts/model_cli.py list risk_model --status production
   ```

4. **Mark corrupt version as failed**
   ```bash
   # Update status via Python
   python -c "
   from libs.models import ModelRegistry
   from pathlib import Path
   registry = ModelRegistry(Path('data/models'))
   # Mark as failed (prevents future promotion)
   registry._update_status('risk_model', 'v1.0.0', 'failed', changed_by='dr:corruption')
   "
   ```

5. **Restore artifact from backup (if available)**
   ```bash
   # Find backup containing the version
   ls data/models/backups/*/artifacts/risk_model/v1.0.0/

   # Manually copy if needed
   cp -r data/models/backups/2024-01-14/artifacts/risk_model/v1.0.0 \
         data/models/artifacts/risk_model/v1.0.0

   # Re-validate
   python scripts/model_cli.py validate risk_model v1.0.0
   ```

---

## Scenario 3: Production Model Performance Degradation

### Symptoms
- Live trading P&L significantly worse than backtest
- Signal service metrics show IC < 0.02
- Alerts from monitoring dashboards

### Recovery Steps

1. **Verify current production model**
   ```bash
   python scripts/model_cli.py list risk_model --status production
   ```

2. **Check model metrics**
   ```bash
   python -c "
   from libs.models import ModelRegistry
   from pathlib import Path
   registry = ModelRegistry(Path('data/models'))
   metadata = registry.get_current_production('risk_model')
   print(f'Version: {metadata.version}')
   print(f'Metrics: {metadata.metrics}')
   print(f'Promoted at: {metadata.created_at}')
   "
   ```

3. **Rollback to previous production version**
   ```bash
   # Rollback immediately
   python scripts/model_cli.py rollback risk_model

   # Confirm rollback
   python scripts/model_cli.py list risk_model --status production
   ```

4. **Verify signal service reloaded**
   ```bash
   # Check signal service logs for reload confirmation
   grep "Model reloaded" logs/signal_service.log | tail -5

   # Or trigger manual reload
   curl -X POST http://localhost:8001/model/reload
   ```

5. **Document incident**
   - Record the failing version, symptoms, and recovery steps
   - Consider marking the version as `failed` status

---

## Scenario 4: Version Drift Alert

### Symptoms
- `VersionDriftError` in logs
- Signal service refuses to load model (STRICT_VERSION_MODE=true)
- Warning logs about dataset version mismatch

### Recovery Steps

1. **Identify version drift**
   ```bash
   python -c "
   from libs.models import ModelRegistry, VersionCompatibilityChecker
   from libs.data_quality.versioning import DatasetVersionManager
   from pathlib import Path

   registry = ModelRegistry(Path('data/models'))
   metadata = registry.get_current_production('risk_model')

   # Get current dataset versions
   version_mgr = DatasetVersionManager(Path('data'))
   current = version_mgr.get_current_versions()

   # Check compatibility
   checker = VersionCompatibilityChecker()
   result = checker.check_compatibility(metadata.dataset_version_ids, current)
   print(f'Compatible: {result.compatible}')
   print(f'Level: {result.level}')
   print(f'Warnings: {result.warnings}')
   "
   ```

2. **Decision options**

   **Option A: Allow drift temporarily (development only)**
   ```bash
   # Set environment variable
   export STRICT_VERSION_MODE=false

   # Restart signal service
   # This allows model to load with warnings
   ```

   **Option B: Retrain model with current dataset versions**
   ```bash
   # Retrain with current data
   python scripts/train_risk_model.py --output v1.1.0

   # Register new version
   python scripts/model_cli.py register risk_model artifacts/risk_model_v1.1.0.pkl --version v1.1.0

   # After paper trading period, promote
   python scripts/model_cli.py promote risk_model v1.1.0
   ```

   **Option C: Rollback dataset version**
   ```bash
   # If dataset update was incorrect, rollback dataset
   # This is a P4T1 operation - see data-backup-restore.md
   ```

---

## Scenario 5: Manifest Integrity Failure

### Symptoms
- `ManifestIntegrityError` on registry load
- Manifest checksum doesn't match registry.db
- `production_models` in manifest doesn't match database

### Recovery Steps

1. **Check manifest status**
   ```bash
   cat data/models/manifest.json | jq .
   ```

2. **Regenerate manifest from registry**
   ```bash
   python -c "
   from libs.models import ModelRegistry, RegistryManifestManager
   from pathlib import Path

   registry = ModelRegistry(Path('data/models'))
   manifest_mgr = RegistryManifestManager(Path('data/models'))

   # Regenerate manifest from current registry state
   manifest_mgr.update_manifest(registry)
   print('Manifest regenerated')
   "
   ```

3. **Verify regenerated manifest**
   ```bash
   python -c "
   from libs.models import RegistryManifestManager
   from pathlib import Path

   mgr = RegistryManifestManager(Path('data/models'))
   if mgr.verify_integrity():
       print('Manifest integrity verified')
   else:
       print('ERROR: Manifest still invalid')
   "
   ```

---

## Backup Procedures

### Manual Backup

```bash
# Create manual backup
python scripts/model_cli.py backup create

# Backup with custom directory
python scripts/model_cli.py backup create --output /path/to/backup
```

### Verify Backup Integrity

```bash
# Verify latest backup
python -c "
from libs.models import RegistryBackupManager
from pathlib import Path
import json

backup_dir = sorted(Path('data/models/backups').iterdir())[-1]
manifest_path = backup_dir / 'backup_manifest.json'

with open(manifest_path) as f:
    manifest = json.load(f)

print(f'Backup date: {manifest[\"created_at\"]}')
print(f'Artifact count: {manifest[\"artifact_count\"]}')
print(f'Registry checksum: {manifest[\"registry_checksum\"]}')
"
```

### Remote Backup Sync

```bash
# Sync to S3 (requires rclone configuration)
rclone sync data/models/backups s3:bucket-name/model-registry-backups

# Verify sync
rclone check data/models/backups s3:bucket-name/model-registry-backups
```

---

## Garbage Collection

### Run GC (Dry Run)

```bash
# Preview what would be deleted
python scripts/model_cli.py gc --dry-run
```

### Run GC (Actual)

```bash
# Execute GC (requires confirmation)
python scripts/model_cli.py gc

# Force without confirmation (use with caution)
python scripts/model_cli.py gc --force
```

### GC Report Example

```
GC Report
---------
Expired staged models: 3
Expired archived models: 5
Total space to reclaim: 1.2 GB

Models to delete:
- risk_model/v0.9.0 (staged, 45 days old)
- risk_model/v0.8.0 (archived, 120 days old)
- alpha_weights/v1.0.0 (staged, 35 days old)
...
```

---

## Monitoring Checklist

### Daily Checks

- [ ] Verify backup completed (check `data/models/backups/` for today's date)
- [ ] Check manifest.json `last_backup_at` timestamp
- [ ] Review signal service logs for model load errors
- [ ] Verify production model versions match expected

### Weekly Checks

- [ ] Run GC dry-run to identify expired models
- [ ] Verify remote backup sync (if configured)
- [ ] Review promotion_history for unexpected changes
- [ ] Validate checksums of production models

### Monthly Checks

- [ ] Test restore procedure from backup
- [ ] Review retention policy compliance
- [ ] Audit promotion_history for access patterns
- [ ] Clean up failed model versions

---

## Emergency Contacts

| Role | Contact | Escalation |
|------|---------|------------|
| On-call engineer | #trading-alerts | 5 min response |
| Platform team | #platform-support | 15 min response |
| Data team | #data-engineering | For dataset version issues |

---

## Related Documentation

- [ADR-0023-model-deployment.md](../ADRs/ADR-0023-model-deployment.md) - Architecture decisions
- [model-registry.md](../CONCEPTS/model-registry.md) - Concepts and usage
- [data-backup-restore.md](./data-backup-restore.md) - Dataset backup procedures
