# Secrets Migration Runbook

**Purpose:** Migrate from `.env` plain-text credentials to secure secrets backend (Vault or AWS Secrets Manager)

**Owner:** @platform-team

**Last Updated:** 2025-11-06

**Related:** ADR-0017, secret-rotation.md

---

## Overview

This runbook guides operators through migrating credentials from `.env` files to a secure secrets backend. Migration follows a staged approach (local → staging → production) to minimize risk.

**Estimated Time:** 2-3 hours (includes setup, migration, and verification)

**Prerequisites:**
- Vault or AWS Secrets Manager provisioned and accessible
- Credentials for secrets backend (Vault token or AWS IAM role)
- Access to all environments (local, staging, production)
- Backup of current `.env` file

---

## Pre-Migration Checklist

- [ ] **Secrets backend provisioned** (Vault server running or AWS Secrets Manager configured)
- [ ] **Access credentials obtained** (Vault token with write permissions or AWS IAM role)
- [ ] **Backup created** (`cp .env .env.backup.$(date +%Y%m%d)`)
- [ ] **Migration script tested** (`python scripts/migrate_secrets.py --dry-run`)
- [ ] **Rollback plan reviewed** (see "Rollback Procedure" section)
- [ ] **Services health check passing** (`make test && make ci-local`)
- [ ] **Emergency override documented** (`SECRET_ALLOW_ENV_IN_NON_LOCAL=1` enables `.env` fallback outside local)

---

## Migration Workflow

### Phase 1: Local Development Setup

**Goal:** Configure local environment to use `EnvSecretManager` (`.env` fallback)

**Steps:**

1. **Create `.env.template` with placeholders:**
   ```bash
   cat > .env.template <<'EOF'
   # Secrets Management Configuration
   SECRET_BACKEND=env  # Options: vault, aws, env

   # Alpaca API Credentials (SECRETS - populate via SecretManager)
   # ALPACA_API_KEY_ID=<YOUR_ALPACA_KEY_ID>
   # ALPACA_API_SECRET_KEY=<YOUR_ALPACA_SECRET>

   # Database Credentials (SECRETS - populate via SecretManager)
   # DATABASE_PASSWORD=<YOUR_DB_PASSWORD>

   # Redis Credentials (SECRETS - future enhancement)
   # REDIS_PASSWORD=<YOUR_REDIS_PASSWORD>

   # Configuration (ENV VARS - keep in .env)
   ALPACA_BASE_URL=https://paper-api.alpaca.markets/v2
   DATABASE_HOST=localhost
   DATABASE_PORT=5432
   DATABASE_NAME=trader
   DATABASE_USER=trader
   REDIS_URL=redis://localhost:6379/0
   DRY_RUN=true
   LOG_LEVEL=INFO
   STRATEGY_ID=alpha_baseline
   EOF
   ```

2. **Copy real secrets from `.env` (for local dev only):**
   ```bash
   # Uncomment secret lines in .env.template and populate with real values
   cp .env.template .env
   vim .env  # Uncomment and fill in secret values
   ```

3. **Verify services start with `EnvSecretManager`:**
   ```bash
   export SECRET_BACKEND=env
   python -m apps.signal_service.main  # Should start successfully
   # Ctrl+C to stop
   make test  # All tests should pass
   ```

**Success Criteria:**
- ✅ `.env.template` created with placeholders
- ✅ Local `.env` file works with `SECRET_BACKEND=env`
- ✅ All services start successfully
- ✅ Tests pass with `make ci-local`

---

### Phase 2: Vault/AWS Setup (Staging Environment)

**Goal:** Provision secrets backend and populate with staging credentials

#### Option A: HashiCorp Vault Setup

> **⚠️ Production-Like Setup Recommended:**
> Use Vault in server mode (NOT `-dev` mode) even for staging to mirror production.
> `-dev` mode is in-memory, unauthenticated by default, and has no persistence.

1. **Start Vault server (server mode with file storage for staging):**
   ```bash
   # Create Vault config file
   cat > vault-staging.hcl <<EOF
   storage "file" {
     path = "./vault-data"
   }
   listener "tcp" {
     address = "127.0.0.1:8200"
     tls_disable = 1
   }
   EOF

   # Start Vault server
   vault server -config=vault-staging.hcl &

   # In another terminal, initialize and unseal:
   export VAULT_ADDR='http://127.0.0.1:8200'
   vault operator init -key-shares=1 -key-threshold=1
   # Save the unseal key and root token!
   vault operator unseal <UNSEAL_KEY>
   export VAULT_TOKEN=<ROOT_TOKEN>
   ```

   **For quick local testing ONLY** (NOT staging), you can use `-dev` mode:
   ```bash
   vault server -dev -dev-root-token-id="dev-token"
   export VAULT_ADDR='http://127.0.0.1:8200'
   export VAULT_TOKEN="dev-token"
   ```

2. **Enable KV v2 secrets engine:**
   ```bash
   vault secrets enable -path=secret kv-v2
   ```

3. **Create secret namespaces:**
   ```bash
   vault kv put secret/staging/alpaca/api_key_id value="<STAGING_ALPACA_KEY_ID>"
   vault kv put secret/staging/alpaca/api_secret_key value="<STAGING_ALPACA_SECRET>"
   vault kv put secret/staging/database/password value="<STAGING_DB_PASSWORD>"
   ```

4. **Verify secrets readable:**
   ```bash
   vault kv get secret/staging/alpaca/api_key_id
   # Should display: value=<STAGING_ALPACA_KEY_ID>
   ```

#### Option B: AWS Secrets Manager Setup

1. **Create secrets via AWS CLI:**
   ```bash
   aws secretsmanager create-secret \
     --name staging/alpaca/api_key_id \
     --secret-string "<STAGING_ALPACA_KEY_ID>" \
     --region us-east-1

   aws secretsmanager create-secret \
     --name staging/alpaca/api_secret_key \
     --secret-string "<STAGING_ALPACA_SECRET>" \
     --region us-east-1

   aws secretsmanager create-secret \
     --name staging/database/password \
     --secret-string "<STAGING_DB_PASSWORD>" \
     --region us-east-1
   ```

2. **Verify secrets readable:**
   ```bash
   aws secretsmanager get-secret-value \
     --secret-id staging/alpaca/api_key_id \
     --region us-east-1
   ```

**Success Criteria:**
- ✅ Secrets backend accessible (Vault or AWS)
- ✅ All secrets created in `staging` namespace
- ✅ Secrets readable via CLI

---

### Phase 3: Automated Migration (Use Migration Script)

> **⚠️ Note:** The `scripts/migrate_secrets.py` script is planned for a future PR.
> For now, manually migrate secrets using the Vault/AWS CLI commands shown in Phase 2,
> or use the Python code examples in Phase 4 below.

**Goal:** Use `scripts/migrate_secrets.py` to populate backend from `.env`

1. **Dry-run to preview migration:**
   ```bash
   # TODO: This script will be added in a future task
   python scripts/migrate_secrets.py \
     --env-file .env \
     --backend vault \
     --namespace staging \
     --vault-addr http://127.0.0.1:8200 \
     --vault-token staging-root-token \
     --dry-run
   # Will output: Lists secrets that would be created (no actual writes)
   ```

2. **Execute migration:**
   ```bash
   # TODO: This script will be added in a future task
   python scripts/migrate_secrets.py \
     --env-file .env \
     --backend vault \
     --namespace staging \
     --vault-addr http://127.0.0.1:8200 \
     --vault-token staging-root-token
   # Will output: ✓ Migrated 3 secrets to Vault (staging namespace)
   ```

3. **Verify migration:**
   ```bash
   vault kv list secret/staging/
   # Should show: alpaca/, database/
   ```

**Success Criteria:**
- ✅ All secrets migrated to backend
- ✅ No errors in migration script output
- ✅ Secrets listable via CLI

---

### Phase 4: Service Integration (Staging Environment)

**Goal:** Update services to use `VaultSecretManager` or `AWSSecretsManager`

1. **Update environment variables:**
   ```bash
   export SECRET_BACKEND=vault  # or 'aws'
   export VAULT_ADDR=http://127.0.0.1:8200
   export VAULT_TOKEN=staging-root-token
   export SECRET_NAMESPACE=staging  # Used by SecretManager to prefix secret paths
   ```

2. **Start services with Vault backend:**
   ```bash
   python -m apps.signal_service.main
   # Should log: "Initialized VaultSecretManager (namespace=staging)"
   # Should log: "Loaded secret: staging/database/password"
   ```

3. **Verify health checks:**
   ```bash
   curl http://localhost:8000/health
   # Should return: {"status": "healthy", "secrets_backend": "vault"}
   ```

4. **Run integration tests:**
   ```bash
   make test ARGS="tests/integration/test_secrets_config_loading.py"
   # All tests should pass
   ```

**Success Criteria:**
- ✅ Services start successfully with Vault/AWS backend
- ✅ Health checks pass
- ✅ Integration tests pass
- ✅ No errors in service logs

---

### Phase 5: Production Deployment

**Goal:** Migrate production secrets and deploy with zero downtime

1. **Create production secrets in backend:**
   ```bash
   # Vault:
   vault kv put secret/prod/alpaca/api_key_id value="<PROD_ALPACA_KEY_ID>"
   vault kv put secret/prod/alpaca/api_secret_key value="<PROD_ALPACA_SECRET>"
   vault kv put secret/prod/database/password value="<PROD_DB_PASSWORD>"

   # AWS:
   aws secretsmanager create-secret \
     --name prod/alpaca/api_key_id \
     --secret-string "<PROD_ALPACA_KEY_ID>" \
     --region us-east-1
   # ... (repeat for other secrets)
   ```

2. **Update production environment variables:**
   ```bash
   export SECRET_BACKEND=vault  # or 'aws'
   export VAULT_ADDR=https://vault.prod.example.com:8200
   export VAULT_TOKEN=<PROD_VAULT_TOKEN>  # Use production-scoped, long-lived token (IAM auth planned for future)
   export SECRET_NAMESPACE=prod
   ```

3. **Rolling restart services (zero downtime):**
   ```bash
   # Restart one service at a time
   systemctl restart signal-service
   sleep 10  # Wait for health check
   systemctl restart execution-gateway
   sleep 10
   systemctl restart orchestrator
   # ... (repeat for other services)
   ```

4. **Verify production health:**
   ```bash
   curl https://api.prod.example.com/health
   # Should return: {"status": "healthy", "secrets_backend": "vault"}
   ```

5. **Monitor logs for errors:**
   ```bash
   journalctl -u signal-service -f | grep -i "secret\|error"
   # Should see: "Loaded secret: prod/alpaca/api_key_id" (no errors)
   ```

**Success Criteria:**
- ✅ Production secrets created in backend
- ✅ Services restarted successfully (zero downtime)
- ✅ Health checks pass
- ✅ No errors in logs
- ✅ Trading continues without interruption

**CRITICAL: Do NOT delete `.env` file yet!**
- Keep `.env` as emergency rollback for 30 days minimum
- Only delete after 30 days of confirmed stability (see Phase 6)
- Backup `.env` before any changes: `cp .env .env.backup.$(date +%Y%m%d)`

---

### Phase 6: Cleanup (After 30 Days)

**Goal:** Remove `.env` files after confirming stability

1. **Verify stability (30 days post-migration):**
   - No secret-related errors in logs
   - All services healthy
   - Trading operating normally

2. **Remove `.env` files:**
   ```bash
   git rm .env  # Remove from git (if accidentally committed)
   rm .env  # Remove from filesystem
   # Keep .env.backup for emergency rollback (30 days)
   ```

3. **Update `.gitignore`:**
   ```bash
   echo ".env" >> .gitignore
   echo ".env.backup.*" >> .gitignore
   ```

**Success Criteria:**
- ✅ 30 days stability confirmed
- ✅ `.env` files removed
- ✅ `.gitignore` updated

---

## Rollback Procedure

**Scenario:** Migration fails, need to revert to `.env` files

**Steps:**

1. **Stop all services:**
   ```bash
   systemctl stop signal-service execution-gateway orchestrator market-data-service risk-manager
   ```

2. **Restore `.env` from backup:**
   ```bash
   cp .env.backup.$(date +%Y%m%d) .env
   ```

3. **Revert to `EnvSecretManager`:**
   ```bash
   export SECRET_BACKEND=env
   export SECRET_ALLOW_ENV_IN_NON_LOCAL=1  # Emergency override (required outside local envs)
   ```

4. **Restart services:**
   ```bash
   systemctl start signal-service execution-gateway orchestrator market-data-service risk-manager
   ```

5. **Verify health:**
   ```bash
   curl http://localhost:8000/health
   # Should return: {"status": "healthy", "secrets_backend": "env"}
   ```

**Estimated Rollback Time:** 5-10 minutes

---

## Troubleshooting

### Issue 1: Service fails to start with "SecretNotFoundError"

**Symptoms:**
```
ERROR SecretNotFoundError: Secret 'staging/alpaca/api_key_id' not found in Vault
```

**Diagnosis:**
```bash
vault kv get secret/staging/alpaca/api_key_id
# Error: ... not found
```

**Fix:**
```bash
# Verify secret exists with correct path
vault kv list secret/staging/alpaca/
# If missing, create it:
vault kv put secret/staging/alpaca/api_key_id value="<VALUE>"
```

---

### Issue 2: Vault connection timeout

**Symptoms:**
```
ERROR VaultConnectionError: Timeout connecting to Vault at http://127.0.0.1:8200
```

**Diagnosis:**
```bash
curl $VAULT_ADDR/v1/sys/health
# Should return: {"initialized":true,"sealed":false}
```

**Fix:**
```bash
# Check Vault server is running
vault status
# If not running:
vault server -dev -dev-root-token-id="staging-root-token"
```

---

### Issue 3: AWS Secrets Manager permission denied

**Symptoms:**
```
ERROR AWSSecretsManagerError: Access Denied (GetSecretValue on staging/alpaca/api_key_id)
```

**Diagnosis:**
```bash
aws secretsmanager get-secret-value --secret-id staging/alpaca/api_key_id
# Error: AccessDeniedException
```

**Fix:**
```bash
# Verify IAM role/user has correct permissions
aws iam get-user
# Attach SecretsManagerReadWrite policy:
aws iam attach-user-policy \
  --user-name <YOUR_USER> \
  --policy-arn arn:aws:iam::aws:policy/SecretsManagerReadWrite
```

---

### Issue 4: Credential cache not working (401 errors from Alpaca)

**Symptoms:**
```
ERROR AlpacaValidationError: 401 Unauthorized (invalid API key)
```

**Diagnosis:**
```bash
# Check if secret value is correct
vault kv get secret/staging/alpaca/api_key_id
# Verify value matches Alpaca dashboard
```

**Fix:**
```bash
# Update secret with correct value
vault kv put secret/staging/alpaca/api_key_id value="<CORRECT_VALUE>"
# Restart service to invalidate cache
systemctl restart signal-service
```

---

## Validation

**Post-migration validation checklist:**

- [ ] **All secrets migrated:** `vault kv list secret/staging/` shows all expected secrets
- [ ] **Services healthy:** `curl /health` returns `{"status": "healthy", "secrets_backend": "vault"}`
- [ ] **Logs clean:** No "SecretNotFoundError" or "VaultConnectionError" in logs
- [ ] **Trading functional:** Paper run completes successfully (`make paper-run`)
- [ ] **Integration tests pass:** `make test ARGS="tests/integration/test_secrets_*"`
- [ ] **E2E tests pass:** `make test ARGS="tests/e2e/test_service_startup_with_secrets.py"`

---

## References

- ADR-0017: Secrets Management Architecture
- secret-rotation.md: 90-day rotation procedure
- Vault Documentation: https://developer.hashicorp.com/vault
- AWS Secrets Manager Documentation: https://docs.aws.amazon.com/secretsmanager/
