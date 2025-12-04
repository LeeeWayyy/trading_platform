# WRDS Credentials Runbook

## Overview

WRDS (Wharton Research Data Services) credentials are required for database access. This runbook covers credential management, rotation, and expiry monitoring.

## Credential Storage

- **Path:** `wrds/username`, `wrds/password` in secrets manager
- **Backend:** Vault (production), AWS Secrets Manager, or .env (dev)
- **Cache TTL:** 1 hour (prevents trading halt during backend downtime)

## Credential Setup

### Initial Setup

```bash
# Using Vault
vault kv put secret/wrds username=myuser password=mypassword

# Using .env (development only)
echo "WRDS_USERNAME=myuser" >> .env
echo "WRDS_PASSWORD=mypassword" >> .env
```

### Verify Credentials

```bash
# Test connection
python -c "
from libs.data_providers.wrds_client import WRDSClient, WRDSConfig
client = WRDSClient(WRDSConfig())
client.connect()
print('Connection successful')
client.close()
"
```

## Credential Rotation

### When to Rotate

- Every 90 days (policy requirement)
- Immediately if credentials may be compromised
- When staff with access leave

### Rotation Procedure

1. **Generate new credentials in WRDS:**
   - Log into wrds.wharton.upenn.edu
   - Account Settings → Change Password

2. **Update secrets manager:**
   ```bash
   vault kv put secret/wrds username=myuser password=newpassword
   ```

3. **Verify sync still works:**
   ```bash
   python scripts/wrds_sync.py status
   ```

4. **Clear credential cache:**
   ```python
   # Force credential refresh
   client._credential_expires = None
   client._get_credentials()
   ```

## Expiry Monitoring

### Alert Configuration

- **Warning:** 30 days before expiry (`sync.credential.expiring`)
- **Critical:** 7 days before expiry
- **Block:** At expiry

### Check Expiry

```python
from libs.data_providers.wrds_client import WRDSClient, WRDSConfig

client = WRDSClient(WRDSConfig())
is_expiring, days = client.check_credential_expiry()
print(f"Expiring: {is_expiring}, Days: {days}")
```

## Troubleshooting

### Authentication Failed

1. Verify credentials in secrets manager
2. Test credentials manually on WRDS website
3. Check IP allowlist if WRDS has restrictions

### Credential Cache Issues

```python
# Force refresh
client._credential_expires = None
username, password = client._get_credentials()
```

### Rate Limiting

WRDS has per-user query limits:
- Respect `rate_limit_queries_per_minute` config
- Default: 10 QPM
- Increase only with WRDS approval
