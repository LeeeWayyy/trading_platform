# common

## Identity
- **Type:** Library
- **Port:** N/A
- **Container:** N/A

## Interface
### Public Interface (Exported Classes & Functions)
| Class/Function | Parameters | Returns | Description |
|----------------|------------|---------|-------------|
| `TradingPlatformError` | message | exception | Base exception type. |
| `RateLimitConfig` | limits | model | Rate limit configuration. |
| `rate_limit` | request, config | response | Dependency for FastAPI rate limiting. |
| `TimestampSerializerMixin` | model | mixin | JSON serialization for datetimes. |
| `get_required_secret` | key | str | Fetch secret (raises if missing). |
| `get_optional_secret` | key | str | Fetch secret (optional). |
| `hash_file_sha256` | path | str | Hash file contents. |

## Behavioral Contracts
### rate_limit(...)
**Purpose:** Apply Redis-backed rate limiting in API routes.

### Secrets helpers
**Purpose:** Provide a unified wrapper around `libs.secrets` and caching.

### Invariants
- Secrets must be fetched via shared manager to avoid backend duplication.

## Data Flow
```
request -> rate limiter -> allow/deny
secret key -> secret manager -> value
```
- **Input format:** FastAPI requests, secret identifiers.
- **Output format:** rate-limit responses or secret values.
- **Side effects:** Redis counters, secret cache updates.

## Usage Examples
### Example 1: Rate limit dependency
```python
from libs.common import rate_limit, RateLimitConfig

config = RateLimitConfig(per_minute=30)
app.get("/path", dependencies=[Depends(rate_limit(config))])
```

### Example 2: Fetch secret
```python
from libs.common import get_required_secret

api_key = get_required_secret("alpaca/api_key_id")
```

## Edge Cases & Boundaries
| Scenario | Input | Expected Behavior |
|----------|-------|-------------------|
| Redis timeout | rate limit store down | Default to allow with metric increment. |
| Missing secret | key not found | `SecretNotFoundError` raised. |
| Invalid file path | hash missing | `FileNotFoundError`. |

## Dependencies
- **Internal:** `libs.secrets`
- **External:** Redis (rate limiting)

## Configuration
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| N/A | - | - | Secrets backend configured in `libs.secrets`. |

## Error Handling
- Custom exceptions in `libs.common.exceptions`.

## Security
- Secrets are fetched via centralized manager; values not logged.

## Testing
- **Test Files:** `tests/libs/common/`
- **Run Tests:** `pytest tests/libs/common -v`
- **Coverage:** N/A

## Related Specs
- `secrets.md`
- `redis_client.md`

## Known Issues & TODO
| Issue | Severity | Description | Tracking |
|-------|----------|-------------|----------|
| None | - | No known issues | - |

## Metadata
- **Last Updated:** 2026-01-09
- **Source Files:** `libs/common/__init__.py`, `libs/common/rate_limit_dependency.py`, `libs/common/secrets.py`
- **ADRs:** N/A
