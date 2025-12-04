# WRDS Lock Recovery Runbook

## Overview

This runbook covers recovery procedures for WRDS data sync locks.

## Lock Architecture

- **Location:** `data/locks/{dataset}.lock`
- **Format:** JSON with pid, hostname, writer_id, acquired_at, expires_at
- **Timeout:** 4 hours maximum, auto-recoverable after expiry
- **PID Check:** Local process liveness verified via `os.kill(pid, 0)`

## Common Scenarios

### 1. Lock Held by Running Process

**Symptoms:**
- `wrds_sync.py` fails with `LockAcquisitionError`
- Lock file exists with recent mtime

**Diagnosis:**
```bash
python scripts/wrds_sync.py lock-status
```

**Resolution:**
Wait for running sync to complete. If genuinely stuck:
```bash
# Check process
ps aux | grep wrds_sync

# If process is hung, kill it first
kill <pid>

# Lock will auto-release on process exit
```

### 2. Stale Lock from Crashed Process

**Symptoms:**
- Lock file exists but holder PID is dead
- Lock mtime > 5 minutes old

**Diagnosis:**
```bash
cat data/locks/crsp.lock
# Check if PID exists
ps -p <pid>
```

**Resolution:**
System auto-recovers stale locks. If urgent:
```bash
python scripts/wrds_sync.py force-unlock --dataset crsp --yes
```

### 3. Lock from Remote Host (Container/VM)

**Symptoms:**
- Lock hostname doesn't match current host
- Cannot verify PID liveness

**Resolution:**
Wait for 30-minute hard timeout, or verify remote process is dead:
```bash
# On remote host
ps -p <pid_from_lock>

# If confirmed dead, force unlock
python scripts/wrds_sync.py force-unlock --dataset crsp --yes
```

### 4. Malformed Lock File

**Symptoms:**
- `MalformedLockFileError` during acquisition
- Lock file contains invalid JSON

**Resolution:**
```bash
# Remove corrupt lock
rm data/locks/crsp.lock

# Retry sync
python scripts/wrds_sync.py full-sync --dataset crsp
```

## Split-Brain Recovery

If two processes both attempt stale lock recovery:

1. Atomic rename determines winner (OS guarantees exactly one succeeds)
2. Winner creates new lock
3. Loser retries after backoff
4. No data corruption possible

## Monitoring

- **Alert:** `sync.lock.stale` - Lock age > 4 hours
- **Action:** Check if sync is actually running; force unlock if dead

## Prevention

1. Always use `atomic_lock()` context manager
2. Never manually create lock files
3. Ensure sync processes have proper signal handlers
