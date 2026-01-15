# admin

## Identity
- **Type:** Library
- **Port:** N/A
- **Container:** N/A

## Interface
### Public Interface (Exported Classes & Functions)
| Class/Function | Parameters | Returns | Description |
|----------------|------------|---------|-------------|
| `ApiKeyScopes` | read/write flags | model | Scope flags for API key permissions. |
| `generate_api_key` | - | tuple[str, str, str] | Generate full key, prefix, and salt. |
| `hash_api_key` | key, salt | str | SHA-256 hash of salted key. |
| `validate_api_key` | key, key_hash, key_salt | bool | Timing-safe key validation. |
| `parse_key_prefix` | key | str \/ None | Extract `tp_live_` prefix from full key. |
| `is_key_revoked` | prefix, redis_client, db_pool | bool | Check revocation (cache-first, DB fallback). |
| `update_last_used` | prefix, db_pool, redis_client | None | Update last_used with Redis-backed debounce. |
| `KEY_PREFIX_PATTERN` | - | regex | Prefix validation pattern. |
| `REVOKED_KEY_CACHE_TTL` | - | int | Redis TTL for revoked cache (seconds). |

## Behavioral Contracts
### generate_api_key() -> tuple[str, str, str]
**Purpose:** Create a new API key with a stable prefix and salt for secure storage.

**Preconditions:**
- None.

**Postconditions:**
- Returns `(full_key, prefix, salt)` where `prefix` matches `KEY_PREFIX_PATTERN`.

**Behavior:**
1. Generates 32 random bytes and base64url-encodes to 43 chars.
2. Prefix uses first 8 chars: `tp_live_<prefix8>`.
3. Generates 16-byte salt (hex encoded).

**Raises:**
- N/A

### is_key_revoked(prefix, redis_client, db_pool) -> bool
**Purpose:** Determine if an API key prefix is revoked.

**Preconditions:**
- `db_pool` is a valid async DB pool.
- `redis_client` may be `None`.

**Postconditions:**
- Returns True if revoked in DB, cached in Redis for `REVOKED_KEY_CACHE_TTL`.

**Behavior:**
1. Check Redis `api_key_revoked:<prefix>` if available.
2. If not found, query DB `api_keys.revoked_at`.
3. Cache positive revocations in Redis.

**Raises:**
- DB errors propagate; Redis errors are logged and ignored.

### update_last_used(prefix, db_pool, redis_client) -> None
**Purpose:** Update last_used_at in DB with a 60s Redis debounce.

**Preconditions:**
- `db_pool` is a valid async DB pool.

**Postconditions:**
- `last_used_at` updated at most once per minute per prefix.

**Behavior:**
1. Uses `SET NX EX` on `api_key_last_used:<prefix>` for debounce.
2. If Redis unavailable, still updates DB (no debounce).

**Raises:**
- DB errors propagate; Redis errors are logged and ignored.

### Invariants
- API key prefixes must match `KEY_PREFIX_PATTERN`.
- Stored key hashes are never compared with non-constant-time equality.

### State Machine (if stateful)
```
[Generated] --> [Active] --> [Revoked]
        ^             |
        +-------------+  (last_used_at updates)
```
- **States:** Generated, Active, Revoked.
- **Transitions:** Revocation is DB-driven; last_used updates do not change state.

## Data Flow
```
full_key -> hash_api_key -> DB stored hash
prefix -> Redis cache -> DB lookup
```
- **Input format:** Strings for key/prefix/salt.
- **Output format:** Booleans or DB updates.
- **Side effects:** Redis cache keys; DB updates to `api_keys`.

## Usage Examples
### Example 1: Generate and store API key
```python
from libs.platform.admin import generate_api_key, hash_api_key

full_key, prefix, salt = generate_api_key()
key_hash = hash_api_key(full_key, salt)
# Store (prefix, key_hash, salt) in DB
```

### Example 2: Validate API key
```python
from libs.platform.admin import validate_api_key

is_valid = validate_api_key(provided_key, stored_hash, stored_salt)
```

## Edge Cases & Boundaries
| Scenario | Input | Expected Behavior |
|----------|-------|-------------------|
| Invalid key length | short key | `parse_key_prefix` returns None |
| Redis down | redis errors | Logs warning; DB path used |
| Frequent use | repeated requests | `update_last_used` debounces via Redis |

## Dependencies
- **Internal:** `libs.web_console_auth.db`
- **External:** Redis, Postgres (api_keys table)

## Configuration
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| N/A | - | - | Uses constants in code for TTLs and patterns. |

## Error Handling
- Redis errors logged and ignored.
- DB exceptions propagate to caller.

## Observability (Services only)
### Health Check
- **Endpoint:** N/A
- **Checks:** N/A

### Metrics
| Metric Name | Type | Labels | Description |
|-------------|------|--------|-------------|
| N/A | - | - | Library has no metrics. |

## Security
- Uses HMAC compare for key validation.
- Key salts are required for hashing; raw keys are not stored.

## Testing
- **Test Files:** `tests/libs/platform/admin/`
- **Run Tests:** `pytest tests/libs/admin -v`
- **Coverage:** N/A

## Related Specs
- `web_console_auth.md`

## Known Issues & TODO
| Issue | Severity | Description | Tracking |
|-------|----------|-------------|----------|
| None | - | No known issues | - |

## Metadata
- **Last Updated:** 2026-01-03
- **Source Files:** `libs/platform/admin/__init__.py`, `libs/platform/admin/api_keys.py`
- **ADRs:** N/A
