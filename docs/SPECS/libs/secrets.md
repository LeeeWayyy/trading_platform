# secrets

## Identity
- **Type:** Library
- **Port:** N/A
- **Container:** N/A

## Interface
### Public Interface (Exported Classes & Functions)
| Class/Function | Parameters | Returns | Description |
|----------------|------------|---------|-------------|
| `SecretManager` | backend | instance | Abstract interface for secrets.
| `create_secret_manager` | None | `SecretManager` | Factory based on `SECRET_BACKEND`. |
| `EnvSecretManager` | config | instance | Env-based backend (dev only). |
| `VaultSecretManager` | config | instance | Vault backend. |
| `AWSSecretsManager` | config | instance | AWS Secrets Manager backend. |
| `SecretCache` | ttl | instance | In-memory TTL cache. |

## Behavioral Contracts
### create_secret_manager()
**Purpose:** Select backend based on `SECRET_BACKEND` and cache instance.

### Invariants
- Env backend is blocked in production environments.
- Secret values are never logged.

## Data Flow
```
secret key -> backend -> cache -> caller
```
- **Input format:** secret paths.
- **Output format:** secret values.
- **Side effects:** cache population.

## Usage Examples
### Example 1: Create manager
```python
from libs.secrets import create_secret_manager

manager = create_secret_manager()
value = manager.get_secret("alpaca/api_key_id")
```

### Example 2: Use cache directly
```python
from libs.secrets import SecretCache

cache = SecretCache(ttl_seconds=3600)
```

## Edge Cases & Boundaries
| Scenario | Input | Expected Behavior |
|----------|-------|-------------------|
| Missing secret | key not found | `SecretNotFoundError`. |
| Backend unavailable | Vault/AWS down | cached value used if available. |
| Prod env with env backend | `SECRET_BACKEND=env` | raises configuration error. |

## Dependencies
- **Internal:** N/A
- **External:** hvac (Vault), boto3 (AWS) optional

## Configuration
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `SECRET_BACKEND` | Yes | `env` | Backend selector (`env`, `vault`, `aws`). |

## Error Handling
- Raises `SecretManagerError` and subclasses for access failures.

## Security
- Centralized secret access with TTL caching and no-logging policy.

## Testing
- **Test Files:** `tests/libs/secrets/`
- **Run Tests:** `pytest tests/libs/secrets -v`
- **Coverage:** N/A

## Related Specs
- `common.md`

## Known Issues & TODO
| Issue | Severity | Description | Tracking |
|-------|----------|-------------|----------|
| None | - | No known issues | - |

## Metadata
- **Last Updated:** 2026-01-09
- **Source Files:** `libs/secrets/__init__.py`, `libs/secrets/factory.py`, `libs/secrets/manager.py`
- **ADRs:** `docs/ADRs/0017-secrets-management.md`
