# Platform Administration

## Overview

The Admin Dashboard provides centralized management for platform-wide configuration, API keys, and audit logs. It consolidates administrative functions that require elevated privileges and provides comprehensive audit trails for all administrative actions.

## Architecture

```
+------------------+     +------------------+     +----------+
| Admin Dashboard  | --> | Admin Service    | --> | Redis    |
| (pages/admin.py) |     | (config/keys)    |     | (cache)  |
+------------------+     +------------------+     +----------+
        |                        |
        v                        v
+------------------+     +------------------+
| AuditLogger      |     | PostgreSQL       |
| (audit_log.py)   |     | (config, keys)   |
+------------------+     +------------------+
```

## Tabs Overview

The Admin Dashboard contains three main tabs:

1. **API Key Management** - Create, rotate, and revoke API keys
2. **System Configuration** - Edit runtime configuration values
3. **Audit Log Viewer** - Search and filter audit events

## API Key Management

### Key Lifecycle

```
Created -> Active -> Rotated -> Revoked
                        |
                        v
                      Active (new key)
```

### Operations

| Action | Description | Permission |
|--------|-------------|------------|
| Create Key | Generate new API key | `MANAGE_API_KEYS` |
| Rotate Key | Create new, keep old active briefly | `MANAGE_API_KEYS` |
| Revoke Key | Immediately disable key | `MANAGE_API_KEYS` |
| List Keys | View all keys (masked) | `MANAGE_API_KEYS` |

### Key Display

Keys are partially masked in the UI:
- Full key shown only once at creation
- After: `sk_live_abc...xyz` (first/last 3 chars)

### Security Features

- Keys stored hashed (bcrypt)
- Creation audit logged with user context
- Revocation is immediate and irreversible
- Rotation provides 24h grace period

## System Configuration Editor

### Editable Settings

| Setting | Type | Description |
|---------|------|-------------|
| `DRY_RUN` | Boolean | Enable/disable dry run mode |
| `MAX_POSITION_SIZE` | Decimal | Maximum position value |
| `CIRCUIT_BREAKER_THRESHOLD` | Decimal | Auto-trip threshold |
| `RATE_LIMIT_ORDERS` | Integer | Orders per minute |

### Workflow

1. Select configuration key
2. Edit value in form
3. Review change preview
4. Click "Apply Changes"
5. Changes take effect immediately (via Redis cache invalidation)

### Change Propagation

```
Admin UI -> PostgreSQL -> Redis PUBLISH -> Services SUBSCRIBE -> Reload
```

Configuration changes are:
- Persisted to PostgreSQL
- Published to Redis channel
- Services receive and reload

## Audit Log Viewer

### Searchable Fields

| Field | Type | Example |
|-------|------|---------|
| `timestamp` | DateTime | Last 24 hours |
| `action` | String | CIRCUIT_BREAKER_TRIP |
| `user_id` | String | admin@example.com |
| `resource_type` | String | circuit_breaker |
| `outcome` | String | success, failure |

### Filters

- **Date Range:** Start/end date picker
- **Action Type:** Dropdown with all action types
- **User:** Text search
- **Outcome:** Success/failure toggle

### Export

Audit logs can be exported as CSV for compliance:
- Requires `VIEW_AUDIT` permission
- Export action is itself audit logged
- Maximum 10,000 rows per export

## RBAC Permissions Model

### Permission Hierarchy

| Permission | Description | Typical Role |
|------------|-------------|--------------|
| `VIEW_AUDIT` | View audit logs | Compliance, Admin |
| `MANAGE_API_KEYS` | Create/rotate/revoke keys | Admin |
| `MANAGE_SYSTEM_CONFIG` | Edit configuration | Admin |

### Access Rules

Admin Dashboard visibility requires ANY of:
- `MANAGE_API_KEYS`
- `MANAGE_SYSTEM_CONFIG`
- `VIEW_AUDIT`

Each tab enforces its specific permission.

## PII Handling

The Admin Dashboard handles PII carefully:

### Masking Rules

| Data Type | Masking | Example |
|-----------|---------|---------|
| Email | Partial | `ad***@example.com` |
| API Key | Ends only | `sk_live_...abc123` |
| IP Address | Full | `192.168.1.100` (audit only) |

### Storage

- Audit logs retain IP addresses for security
- API keys stored as bcrypt hashes
- Configuration values may contain sensitive data (encrypted at rest)

## Audit Logging

All admin actions are logged with:

```json
{
    "timestamp": "2025-01-15T10:30:00Z",
    "action": "API_KEY_CREATED",
    "user_id": "admin@example.com",
    "resource_type": "api_key",
    "resource_id": "key_abc123",
    "details": {
        "key_name": "Production Service",
        "scopes": ["read", "write"]
    },
    "ip_address": "192.168.1.100",
    "outcome": "success"
}
```

## Prometheus Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `admin_action_total` | Counter | action | Admin actions by type |
| `audit_write_latency_seconds` | Histogram | - | Audit log write latency |
| `api_key_operations_total` | Counter | operation | Key operations |
| `config_changes_total` | Counter | key | Configuration changes |

## SLA Targets

| Metric | Target | Alert Threshold |
|--------|--------|-----------------|
| Audit write latency P95 | < 1s | > 1s |
| Config propagation | < 5s | > 10s |
| Key rotation | < 2s | > 5s |

## Security Considerations

1. **Session validation:** Admin actions require fresh session
2. **Rate limiting:** Max 10 key operations per minute
3. **IP logging:** All admin actions log source IP
4. **Change confirmation:** Destructive actions require confirmation
5. **Audit trail:** Complete history of all admin actions

## Configuration

Environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `ADMIN_SESSION_TIMEOUT` | `15m` | Admin session idle timeout |
| `API_KEY_ROTATION_GRACE` | `24h` | Old key valid after rotation |
| `AUDIT_RETENTION_DAYS` | `90` | Audit log retention |

## Related Documentation

- [OAuth2/mTLS Authentication](./oauth2-mtls-fallback-architecture.md)
- [Audit Logging](./structured-logging.md)
- [Operations Runbook](../RUNBOOKS/ops.md)
