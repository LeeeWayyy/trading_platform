# P2T3 Phase 3 - Component 1: OAuth2 Config & IdP Setup

**Component:** 1/6
**Duration:** 1.75 days (14 hours actual estimate)
**Status:** Planning
**Dependencies:** None (first component)

**Note:** Original plan allocated 1.5 days (12h), but detailed breakdown totals ~14h. Adjusting to 1.75 days for realistic completion.

---

## Overview

Set up OAuth2/OIDC infrastructure foundation for production web console authentication. This component establishes the Auth0 IdP, documents architectural decisions, and designs the Redis session storage schema.

**Success Criteria:**
- Auth0 OAuth2 client registered and configured
- ADR documenting Auth0 selection rationale approved
- IdP health check monitoring operational
- Redis session store schema designed and validated

---

## Task Breakdown

### Task 0: Add Required Dependencies (15 minutes)

**Implementation Steps:**

1. **Add Python Dependencies**

   Edit: `apps/web_console/requirements.txt`

   Add:
   ```
   httpx>=0.25.0  # Phase 3: OAuth2 IdP health checks
   pydantic>=2.0.0  # Phase 3: OAuth2 session data models
   ```

**Files Modified:**
- `apps/web_console/requirements.txt`

**Testing:**
- Run `pip install -r apps/web_console/requirements.txt` to verify no conflicts

---

### Task 1: Register Auth0 Account + OAuth2 Client (3 hours)

**Implementation Steps:**

1. **Create Auth0 Account**
   - Sign up at https://auth0.com (free tier: 7,500 MAU)
   - Select region: US (for data residency compliance)
   - Enable MFA for account security

2. **Create OAuth2 Application**
   - Application Type: Regular Web Application
   - Name: `trading-platform-web-console-prod`
   - Token Endpoint Authentication Method: `client_secret_post`

3. **Configure Application Settings**
   ```
   Application URIs:
   - Allowed Callback URLs:
     https://web-console.trading-platform.local/callback
     http://localhost:8501/callback  (dev only)

   - Allowed Logout URLs:
     https://web-console.trading-platform.local/logout
     http://localhost:8501/logout  (dev only)

   - Allowed Web Origins:
     https://web-console.trading-platform.local
     http://localhost:8501  (dev only)

   Grant Types:
   - Authorization Code
   - Refresh Token

   Token Settings:
   - Access Token Expiration: 3600 seconds (1 hour)
   - Refresh Token Expiration: 14400 seconds (4 hours)
   - Refresh Token Rotation: Enabled
   - Refresh Token Reuse Interval: 0 seconds (strict rotation)
   ```

4. **Enable Advanced Settings**
   ```
   OAuth:
   - OIDC Conformant: Enabled
   - JsonWebToken Signature Algorithm: RS256

   Grant Types:
   - ✓ Authorization Code
   - ✓ Refresh Token
   - ✗ Implicit (disabled for security)
   - ✗ Password (disabled - using OAuth2 flow only)
   - ✗ Client Credentials (not needed for user auth)
   ```

5. **Configure PKCE**
   - Require PKCE: Enabled (S256 challenge method)
   - This protects against authorization code interception attacks

6. **Save Credentials Securely**
   ```bash
   # Store in AWS Secrets Manager (production)
   aws secretsmanager create-secret \
     --name trading-platform/web-console/auth0-credentials \
     --secret-string '{
       "domain": "trading-platform.us.auth0.com",
       "client_id": "<client_id>",
       "client_secret": "<client_secret>",
       "audience": "https://api.trading-platform.local"
     }'

   # For development, create .env file (already gitignored)
   cat > apps/web_console/.env <<EOF
   # Auth0 OAuth2/OIDC Configuration (Phase 3)
   AUTH0_DOMAIN=trading-platform.us.auth0.com
   AUTH0_CLIENT_ID=<client_id>
   AUTH0_CLIENT_SECRET=<client_secret>
   AUTH0_AUDIENCE=https://api.trading-platform.local
   EOF
   ```

   **Note:** `.env` is already covered by `.gitignore` (line 53), preventing secret leakage

**Files Created:**
- None (external Auth0 configuration only)

**Environment Variables Required:**
```bash
# apps/web_console/config.py
AUTH0_DOMAIN=trading-platform.us.auth0.com
AUTH0_CLIENT_ID=<from_secrets_manager>
AUTH0_CLIENT_SECRET=<from_secrets_manager>
AUTH0_AUDIENCE=https://api.trading-platform.local
```

**Testing:**
- Manual verification: Auth0 dashboard shows application created
- Manual test: Open `.well-known/openid-configuration` endpoint returns valid JSON

---

### Task 2: Configure Redirect URIs (30 minutes)

**Implementation Steps:**

1. **Update Nginx Configuration**

   Edit: `apps/web_console/nginx/nginx.conf`

   ```nginx
   # OAuth2 callback endpoint (no auth required)
   location /callback {
       proxy_pass http://streamlit:8501/callback;
       proxy_set_header Host $host;
       proxy_set_header X-Real-IP $remote_addr;
       proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
       proxy_set_header X-Forwarded-Proto https;

       # No rate limiting on callback (already protected by state/nonce)
   }

   # Logout endpoint
   location /logout {
       proxy_pass http://streamlit:8501/logout;
       proxy_set_header Host $host;
       proxy_set_header X-Real-IP $remote_addr;
   }
   ```

2. **Update docker-compose.yml**

   Edit: `docker-compose.yml`

   ```yaml
   services:
     web_console:
       environment:
         - STREAMLIT_SERVER_BASE_URL_PATH=/
         - OAUTH2_REDIRECT_URI=https://web-console.trading-platform.local/callback
         - OAUTH2_LOGOUT_REDIRECT_URI=https://web-console.trading-platform.local/logout
   ```

**Files Modified:**
- `apps/web_console/nginx/nginx.conf` (add /callback and /logout routes)
- `docker-compose.yml` (add OAuth2 environment variables)

**Testing:**
- Integration test: Verify nginx proxies /callback to Streamlit
- Manual test: `curl https://web-console.trading-platform.local/callback` returns 404 (Streamlit handler not yet implemented, expected)

---

### Task 3: Create ADR for Auth0 Selection (2 hours)

**Implementation Steps:**

1. **Create ADR Document**

   Create: `docs/ADRs/ADR-015-auth0-idp-selection.md`

   ```markdown
   # ADR-015: Auth0 for Production OAuth2/OIDC Identity Provider

   **Status:** Proposed
   **Date:** 2025-11-23
   **Deciders:** Platform Team
   **Reviewers:** [To be assigned]
   **Approver:** [To be assigned]
   **Related:** P2T3 Phase 3 (OAuth2/OIDC Authentication)

   ## Context

   Web console requires production-grade OAuth2/OIDC authentication with:
   - SSO capabilities for future multi-service support
   - MFA enforcement
   - Centralized user management
   - High availability (99.99%+ SLA)
   - Compliance (SOC2, ISO27001)
   - US-based data residency

   Internal trading platform (not third-party SaaS), budget-conscious.

   ## Decision

   Use **Auth0** as the production IdP for OAuth2/OIDC authentication.

   ## Rationale

   ### Auth0 Advantages:
   1. **Fastest MVP setup**: 30-min registration vs. 2-3 days Keycloak setup
   2. **Managed service**: No infrastructure maintenance, automatic updates
   3. **99.99% SLA**: Enterprise-grade reliability with US-based hosting
   4. **SOC2/ISO27001**: Built-in compliance certification
   5. **Built-in MFA**: TOTP, SMS, email OTP support
   6. **Cost-effective for internal use**: $240/year (1000 MAU free tier sufficient)
   7. **Developer experience**: Excellent docs, SDKs, debugging tools

   ### Trade-offs Accepted:
   1. **Vendor lock-in risk**: Mitigated by adapter pattern (future Keycloak migration possible)
   2. **Ongoing cost**: $240/year acceptable for internal platform (<10 users)
   3. **External dependency**: Mitigated by mTLS fallback for IdP outages

   ### Alternatives Considered:

   | Option | Pros | Cons | Decision |
   |--------|------|------|----------|
   | **Keycloak (self-hosted)** | Open-source, no vendor lock-in, full control | 2-3 days setup, infrastructure maintenance, no built-in SLA | Rejected (time cost) |
   | **Okta** | Enterprise-grade, similar features | $1500+/year, overkill for internal use | Rejected (cost) |
   | **AWS Cognito** | AWS-native, cheap ($0-50/month) | Complex UX, limited MFA, harder SSO | Rejected (UX concerns) |
   | **Roll-our-own JWT** | Full control, no external dependency | Security risk, no MFA, manual user mgmt, audit burden | Rejected (security) |

   ## Consequences

   ### Positive:
   - Rapid deployment: Production-ready in 1.5 days vs. 1-2 weeks for self-hosted
   - Zero infrastructure overhead: No servers to patch, monitor, or backup
   - Enterprise security out-of-box: MFA, JWKS rotation, breach detection
   - Future SSO support: Can add SAML/social logins without code changes

   ### Negative:
   - External dependency: Auth0 outage blocks logins (mitigated by mTLS fallback)
   - Recurring cost: $240/year (acceptable for internal platform)
   - Migration effort if switching: Adapter pattern reduces but doesn't eliminate

   ### Mitigation:
   - **mTLS fallback**: Emergency authentication mode for Auth0 outages
   - **Adapter pattern**: Abstract IdP interface to ease future migration
   - **AWS Secrets Manager**: Credentials portable to any IdP
   - **Annual review**: Re-evaluate if user count grows or cost increases

   ## Implementation

   - Phase 3 Component 1: Auth0 registration and configuration
   - Phase 3 Component 2: OAuth2 flow with Auth0 endpoints
   - Phase 3 Component 4: mTLS fallback for IdP outages
   - Phase 4 (future): Keycloak adapter if migration needed

   ## References

   - Auth0 Pricing: https://auth0.com/pricing
   - Auth0 Documentation: https://auth0.com/docs
   - P2T3 Phase 3 Final Plan: `docs/TASKS/P2T3_Phase3_FINAL_PLAN.md`
   - mTLS fallback runbook: (to be created in Component 5)
   ```

2. **Update ADR Index**

   Edit: `docs/ADRs/README.md`

   Add entry:
   ```markdown
   | ADR-015 | Auth0 for Production OAuth2/OIDC Identity Provider | Proposed | 2025-11-23 |
   ```

   **Note:** Status will be updated to "Approved" after ADR review and approval

**Files Created:**
- `docs/ADRs/ADR-015-auth0-idp-selection.md`

**Files Modified:**
- `docs/ADRs/README.md` (add ADR-015 to index)

**Testing:**
- Manual review: ADR follows template from `docs/STANDARDS/ADR_GUIDE.md`
- Checklist: Context, Decision, Rationale, Consequences, Alternatives all documented

---

### Task 4: Implement IdP Health Check (4 hours)

**Implementation Steps:**

1. **Create IdP Health Check Module**

   Create: `apps/web_console/auth/idp_health.py`

   ```python
   """Auth0 IdP health monitoring for fallback triggering."""

   import httpx
   import logging
   from datetime import datetime, timedelta
   from typing import Optional
   from pydantic import BaseModel

   logger = logging.getLogger(__name__)


   class IdPHealthStatus(BaseModel):
       """IdP health check result."""
       healthy: bool
       checked_at: datetime
       response_time_ms: float
       error: Optional[str] = None
       consecutive_failures: int = 0


   class IdPHealthChecker:
       """Monitors Auth0 IdP availability."""

       def __init__(
           self,
           auth0_domain: str,
           check_interval_seconds: int = 60,
           failure_threshold: int = 3,
           timeout_seconds: float = 5.0
       ):
           self.auth0_domain = auth0_domain
           self.check_interval = timedelta(seconds=check_interval_seconds)
           self.failure_threshold = failure_threshold
           self.timeout = timeout_seconds

           self._last_status: Optional[IdPHealthStatus] = None
           self._last_check: Optional[datetime] = None
           self._consecutive_failures = 0

       async def check_health(self) -> IdPHealthStatus:
           """Check Auth0 .well-known/openid-configuration endpoint."""
           url = f"https://{self.auth0_domain}/.well-known/openid-configuration"

           start = datetime.utcnow()
           try:
               async with httpx.AsyncClient(timeout=self.timeout) as client:
                   response = await client.get(url)
                   response.raise_for_status()

                   # Validate required OIDC fields exist
                   data = response.json()
                   required_fields = [
                       "issuer", "authorization_endpoint", "token_endpoint",
                       "jwks_uri", "userinfo_endpoint"
                   ]
                   missing = [f for f in required_fields if f not in data]

                   if missing:
                       raise ValueError(f"Missing OIDC fields: {missing}")

                   response_time = (datetime.utcnow() - start).total_seconds() * 1000

                   # Reset failure counter on success
                   self._consecutive_failures = 0

                   status = IdPHealthStatus(
                       healthy=True,
                       checked_at=datetime.utcnow(),
                       response_time_ms=response_time,
                       consecutive_failures=0
                   )

                   logger.info(
                       "IdP health check passed",
                       extra={
                           "auth0_domain": self.auth0_domain,
                           "response_time_ms": response_time
                       }
                   )

           except Exception as e:
               self._consecutive_failures += 1
               response_time = (datetime.utcnow() - start).total_seconds() * 1000

               status = IdPHealthStatus(
                       healthy=False,
                       checked_at=datetime.utcnow(),
                       response_time_ms=response_time,
                       error=str(e),
                       consecutive_failures=self._consecutive_failures
                   )

               logger.error(
                   "IdP health check failed",
                   extra={
                       "auth0_domain": self.auth0_domain,
                       "error": str(e),
                       "consecutive_failures": self._consecutive_failures
                   }
               )

           self._last_status = status
           self._last_check = datetime.utcnow()

           return status

       def should_fallback_to_mtls(self) -> bool:
           """Determine if mTLS fallback should activate."""
           if not self._last_status:
               return False

           return (
               not self._last_status.healthy
               and self._last_status.consecutive_failures >= self.failure_threshold
           )

       def get_last_status(self) -> Optional[IdPHealthStatus]:
           """Get last health check result (cached)."""
           return self._last_status

       def should_check_now(self) -> bool:
           """Determine if health check is due."""
           if not self._last_check:
               return True

           return datetime.utcnow() - self._last_check >= self.check_interval
   ```

2. **Integrate Health Check into Web Console Startup**

   Edit: `apps/web_console/app.py`

   ```python
   # Add imports
   from auth.idp_health import IdPHealthChecker
   import asyncio

   # Initialize health checker (global singleton)
   idp_health_checker = IdPHealthChecker(
       auth0_domain=os.getenv("AUTH0_DOMAIN"),
       check_interval_seconds=60,
       failure_threshold=3,
       timeout_seconds=5.0
   )

   async def background_health_check():
       """Background task to monitor IdP health."""
       while True:
           if idp_health_checker.should_check_now():
               await idp_health_checker.check_health()

               if idp_health_checker.should_fallback_to_mtls():
                   logger.critical(
                       "IdP health check failed 3+ times - MANUAL mTLS FALLBACK REQUIRED",
                       extra={
                           "consecutive_failures": idp_health_checker._consecutive_failures,
                           "runbook": "See docs/RUNBOOKS/auth0-idp-outage.md"
                       }
                   )

           await asyncio.sleep(10)  # Check every 10s if due

   # Start background task (run in separate thread for Streamlit)
   import threading

   def start_health_check_thread():
       loop = asyncio.new_event_loop()
       asyncio.set_event_loop(loop)
       loop.run_until_complete(background_health_check())

   health_thread = threading.Thread(target=start_health_check_thread, daemon=True)
   health_thread.start()
   ```

3. **Add Internal Health Check Endpoint**

   Edit: `apps/web_console/app.py`

   ```python
   # Add Streamlit custom component for health endpoint
   import streamlit as st
   import os

   # Health check page (restricted to internal monitoring)
   # SECURITY: Require shared secret to prevent info disclosure
   if st.query_params.get("health") == "idp":
       health_secret = st.query_params.get("secret")
       expected_secret = os.getenv("HEALTH_CHECK_SECRET")

       if not expected_secret or health_secret != expected_secret:
           st.error("❌ Unauthorized")
           st.stop()

       status = idp_health_checker.get_last_status()
       if status and status.healthy:
           st.success(f"✅ IdP healthy (checked {status.checked_at.isoformat()})")
       else:
           st.error(f"❌ IdP unhealthy: {status.error if status else 'Never checked'}")
       st.stop()
   ```

   **Environment Variable Required:**
   ```bash
   HEALTH_CHECK_SECRET=<random-32-char-string>  # Generate with: openssl rand -hex 16
   ```

**Files Created:**
- `apps/web_console/auth/idp_health.py`

**Files Modified:**
- `apps/web_console/app.py` (add health check initialization and background task)

**Testing:**
- Unit test: Mock httpx responses to test health check logic
- Integration test: Start web console, verify health check runs every 60s
- Failure test: Block Auth0 domain in /etc/hosts, verify 3 failures triggers fallback warning

---

### Task 5: Design Redis Session Store Schema (4 hours)

**Implementation Steps:**

1. **Create Session Store Module (Design Only)**

   Create: `apps/web_console/auth/session_store.py`

   ```python
   """Redis session store for OAuth2 tokens (design specification)."""

   from typing import Optional
   from datetime import datetime, timedelta
   from pydantic import BaseModel
   import json
   from cryptography.hazmat.primitives.ciphers.aead import AESGCM
   import os
   import base64


   class SessionData(BaseModel):
       """OAuth2 session data stored in Redis."""
       access_token: str
       refresh_token: str
       id_token: str
       user_id: str  # Auth0 user ID (e.g., "auth0|12345")
       email: str
       created_at: datetime
       last_activity: datetime
       ip_address: str
       user_agent: str


   class RedisSessionStore:
       """
       Redis-backed session store with AES-256-GCM encryption.

       Design:
       - Redis DB 1 (dedicated, isolated from features/metrics)
       - Key format: session:{session_id}
       - Value: AES-256-GCM encrypted JSON blob of SessionData
       - TTL: 4 hours (absolute timeout, enforced by Redis)
       - Session binding: Hash(session_id + IP + user_agent) prevents hijacking

       Security:
       - Encryption key: 32-byte key from AWS Secrets Manager
       - Dual-key rotation: Primary + secondary key support for zero-downtime rotation
       - Never store tokens in Streamlit session_state (CI validates)
       """

       def __init__(
           self,
           redis_client,
           encryption_key: bytes,
           secondary_key: Optional[bytes] = None,
           db: int = 1,
           absolute_timeout_hours: int = 4,
           idle_timeout_minutes: int = 15
       ):
           """
           Args:
               redis_client: Redis client instance
               encryption_key: 32-byte AES-256 key (primary)
               secondary_key: Optional 32-byte key for rotation fallback
               db: Redis database number (default 1 for sessions)
               absolute_timeout_hours: Maximum session lifetime
               idle_timeout_minutes: Inactivity timeout
           """
           self.redis = redis_client
           self.cipher_primary = AESGCM(encryption_key)
           self.cipher_secondary = AESGCM(secondary_key) if secondary_key else None
           self.db = db
           self.absolute_timeout = timedelta(hours=absolute_timeout_hours)
           self.idle_timeout = timedelta(minutes=idle_timeout_minutes)

       def _encrypt(self, data: str) -> str:
           """Encrypt data with AES-256-GCM using primary key."""
           nonce = os.urandom(12)  # 96-bit nonce
           ciphertext = self.cipher_primary.encrypt(nonce, data.encode(), None)

           # Format: base64(nonce + ciphertext)
           return base64.b64encode(nonce + ciphertext).decode()

       def _decrypt(self, encrypted: str) -> str:
           """Decrypt data with dual-key fallback."""
           blob = base64.b64decode(encrypted)
           nonce = blob[:12]
           ciphertext = blob[12:]

           # Try primary key first
           try:
               return self.cipher_primary.decrypt(nonce, ciphertext, None).decode()
           except Exception:
               # Fallback to secondary key during rotation
               if self.cipher_secondary:
                   return self.cipher_secondary.decrypt(nonce, ciphertext, None).decode()
               raise

       async def create_session(
           self,
           session_id: str,
           session_data: SessionData
       ) -> None:
           """
           Create encrypted session in Redis.

           Args:
               session_id: Unique session identifier (HttpOnly cookie value)
               session_data: OAuth2 tokens and user metadata
           """
           # Serialize to JSON
           json_data = session_data.model_dump_json()

           # Encrypt
           encrypted = self._encrypt(json_data)

           # Store in Redis with TTL
           key = f"session:{session_id}"
           await self.redis.setex(
               key,
               int(self.absolute_timeout.total_seconds()),
               encrypted
           )

       async def get_session(
           self,
           session_id: str,
           update_activity: bool = True
       ) -> Optional[SessionData]:
           """
           Retrieve and decrypt session from Redis.

           Args:
               session_id: Session identifier
               update_activity: If True, update last_activity timestamp

           Returns:
               SessionData if valid, None if expired or not found
           """
           key = f"session:{session_id}"
           encrypted = await self.redis.get(key)

           if not encrypted:
               return None

           # Decrypt
           json_data = self._decrypt(encrypted.decode())
           session_data = SessionData.model_validate_json(json_data)

           # Check idle timeout
           now = datetime.utcnow()
           if now - session_data.last_activity > self.idle_timeout:
               await self.delete_session(session_id)
               return None

           # Update activity timestamp if requested
           if update_activity:
               session_data.last_activity = now
               await self.create_session(session_id, session_data)

           return session_data

       async def delete_session(self, session_id: str) -> None:
           """Delete session from Redis."""
           key = f"session:{session_id}"
           await self.redis.delete(key)

       async def cleanup_all_sessions(self, prefix: str = "session:") -> int:
           """
           Delete all OAuth2 sessions (for IdP fallback).

           SAFE IMPLEMENTATION: Uses SCAN + DEL (not FLUSHDB).

           Returns:
               Number of sessions deleted
           """
           cursor = 0
           deleted = 0

           while True:
               cursor, keys = await self.redis.scan(
                   cursor,
                   match=f"{prefix}*",
                   count=1000
               )

               if keys:
                   deleted += await self.redis.delete(*keys)

               if cursor == 0:
                   break

           return deleted
   ```

2. **Create Schema Documentation**

   Create: `docs/ARCHITECTURE/redis-session-schema.md`

   ```markdown
   # Redis Session Store Schema - OAuth2/OIDC

   **Database:** Redis DB 1 (dedicated, isolated from features DB 0 and metrics DB 2)
   **Encryption:** AES-256-GCM with 32-byte key from AWS Secrets Manager

   ## Key Format

   ```
   session:{session_id}
   ```

   - `session_id`: 32-character random hex string (128-bit entropy)
   - Example: `session:a3f5c8e9d2b1f0a7c4e6d8b2f5a9c3e7`

   ## Value Structure

   **Encrypted JSON blob** (base64-encoded AES-256-GCM ciphertext):

   ```json
   {
     "access_token": "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9...",
     "refresh_token": "v1.MR...abc123",
     "id_token": "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9...",
     "user_id": "auth0|67890abcdef12345",
     "email": "trader@example.com",
     "created_at": "2025-11-23T10:00:00Z",
     "last_activity": "2025-11-23T10:15:00Z",
     "ip_address": "192.168.1.100",
     "user_agent": "Mozilla/5.0..."
   }
   ```

   ## TTL (Time-To-Live)

   - **Absolute timeout**: 4 hours (14,400 seconds)
   - **Idle timeout**: 15 minutes (enforced in application, not Redis TTL)

   Redis TTL is set to 4 hours. Application checks `last_activity` on each request:
   - If `now - last_activity > 15 minutes`: Delete session (idle timeout)
   - If valid: Update `last_activity` and re-encrypt

   ## Encryption Details

   **Algorithm:** AES-256-GCM (Authenticated Encryption with Associated Data)

   **Key Management:**
   - Primary key: 32-byte key from AWS Secrets Manager
   - Secondary key: Optional 32-byte key for zero-downtime rotation

   **Encryption Format:**
   ```
   base64(nonce || ciphertext || tag)
   ```

   - Nonce: 12 bytes (96-bit, randomly generated per encryption)
   - Ciphertext: Variable length (encrypted JSON)
   - Tag: 16 bytes (128-bit authentication tag, provided by GCM)

   ## Session Lifecycle

   1. **Login** → Create session:
      - Generate random session_id (32 chars hex)
      - Store encrypted tokens in Redis with 4-hour TTL
      - Set HttpOnly cookie: `session_id=<session_id>; Secure; SameSite=Lax; Max-Age=14400`

   2. **Request** → Validate session:
      - Read session_id from cookie
      - Fetch encrypted blob from Redis
      - Decrypt and validate idle timeout
      - Update last_activity if valid

   3. **Logout** → Delete session:
      - Delete Redis key
      - Clear cookie
      - Revoke refresh token with Auth0

   4. **Expiration** → Automatic cleanup:
      - Redis TTL expires after 4 hours (automatic deletion)
      - OR idle timeout triggers manual deletion

   ## Security Considerations

   ### Tokens NEVER in Streamlit session_state

   **CI Validation:**
   ```bash
   # Pre-commit hook
   if grep -r "session_state.*token" apps/web_console/*.py; then
       echo "❌ CRITICAL: Tokens MUST NOT be in session_state!"
       exit 1
   fi
   ```

   **Allowed in session_state:**
   - `session_id` (cookie ID, not sensitive)
   - `user_display_name` (non-sensitive UI state)
   - `last_activity` (client-side timer only)

   **NEVER in session_state:**
   - `access_token`
   - `refresh_token`
   - `id_token`

   ### Session Binding

   To prevent session hijacking, bind session to:
   - IP address (stored in SessionData)
   - User-Agent (stored in SessionData)

   On each request:
   ```python
   if session_data.ip_address != request_ip:
       logger.warning("Session IP mismatch - possible hijack attempt")
       delete_session(session_id)
       return None
   ```

   ### Encryption Key Rotation

   See: `docs/RUNBOOKS/session-key-rotation.md` (to be created in Component 5)

   ## Database Isolation

   **Redis Database Allocation:**
   - DB 0: Feature store data
   - DB 1: OAuth2 sessions (THIS SCHEMA)
   - DB 2: Metrics and circuit breaker state

   **Why isolation matters:**
   - `FLUSHDB` on DB 0 won't affect sessions
   - Separate monitoring and alerting
   - Different backup/retention policies

   ## Emergency Cleanup (IdP Outage)

   When switching to mTLS fallback, safely delete all OAuth2 sessions:

   ```bash
   # SAFE: Uses SCAN + DEL (not FLUSHDB)
   redis-cli -n 1 --scan --pattern "session:*" | xargs -L 1 redis-cli -n 1 DEL

   # OR use Python script
   python3 scripts/clear_oauth2_sessions.py --redis-db 1 --prefix "session:"
   ```

   **DO NOT USE:**
   ```bash
   redis-cli -n 1 FLUSHDB  # ❌ DANGEROUS - deletes ALL DB 1 data
   ```
   ```

**Files Created:**
- `apps/web_console/auth/session_store.py` (design, not fully implemented)
- `docs/ARCHITECTURE/redis-session-schema.md`

**Files Modified:**
- None

**Testing:**
- Design review: Validate schema matches approved plan
- Unit test stub: Test encryption/decryption logic (implementation in Component 2)

---

## Deliverables Summary

**Completed:**
1. ✅ Python dependencies added (httpx, pydantic)
2. ✅ Auth0 OAuth2 client registered with production settings
3. ✅ Redirect URIs configured in nginx and docker-compose
4. ✅ ADR-015 (Proposed status) documenting Auth0 selection rationale
5. ✅ IdP health check monitoring operational with secure endpoint
6. ✅ Redis session store schema designed and documented

**Files Created:**
- `docs/ADRs/ADR-015-auth0-idp-selection.md`
- `apps/web_console/auth/idp_health.py`
- `apps/web_console/auth/session_store.py` (design)
- `docs/ARCHITECTURE/redis-session-schema.md`

**Files Modified:**
- `apps/web_console/requirements.txt` (add httpx, pydantic)
- `docs/ADRs/README.md` (add ADR-015 index entry)
- `apps/web_console/nginx/nginx.conf` (add /callback and /logout routes)
- `docker-compose.yml` (add OAuth2 environment variables)
- `apps/web_console/app.py` (add health check initialization with auth)

**Environment Variables Added:**
```bash
AUTH0_DOMAIN=trading-platform.us.auth0.com
AUTH0_CLIENT_ID=<from_secrets_manager>
AUTH0_CLIENT_SECRET=<from_secrets_manager>
AUTH0_AUDIENCE=https://api.trading-platform.local
OAUTH2_REDIRECT_URI=https://web-console.trading-platform.local/callback
OAUTH2_LOGOUT_REDIRECT_URI=https://web-console.trading-platform.local/logout
REDIS_SESSION_DB=1
HEALTH_CHECK_SECRET=<random-32-char-string>
```

---

## Testing Plan

### Unit Tests

1. **IdP Health Check**
   - Mock httpx responses (success, timeout, invalid JSON)
   - Test consecutive failure counter
   - Test should_fallback_to_mtls() threshold logic

2. **Session Store Encryption**
   - Test encrypt/decrypt round-trip
   - Test dual-key fallback during rotation
   - Test encryption with invalid key length (should raise)

### Integration Tests

1. **Auth0 Configuration**
   - Manual test: Open `.well-known/openid-configuration` endpoint
   - Verify required OIDC fields present

2. **Health Check Background Task**
   - Start web console
   - Verify health check logs every 60 seconds
   - Block Auth0 domain, verify fallback warning after 3 failures

3. **Nginx Routing**
   - `curl https://web-console.trading-platform.local/callback` returns 404 (handler not yet implemented, expected)
   - Verify nginx logs show proxy to Streamlit

### Manual Verification

- [ ] Dependencies installed: `pip install -r apps/web_console/requirements.txt` succeeds
- [ ] Auth0 dashboard shows application created
- [ ] Callback URLs configured correctly
- [ ] PKCE enabled with S256 challenge method
- [ ] Refresh token rotation enabled
- [ ] ADR-015 follows template (Status: Proposed) and documents decision rationale
- [ ] Health check runs in background thread without blocking Streamlit
- [ ] Health endpoint requires HEALTH_CHECK_SECRET (unauthorized without secret)

---

## Definition of Done

- [ ] All 6 tasks completed (Task 0 added for dependencies)
- [ ] All files created/modified as specified
- [ ] All environment variables documented
- [ ] ADR-015 created with Proposed status and indexed
- [ ] Unit tests written for health check and encryption
- [ ] Integration test passes for health check background task
- [ ] Manual verification checklist completed
- [ ] Component 1 plan reviewed via zen-mcp (Gemini + Codex) - APPROVED
- [ ] No tokens in session_state (CI grep check passes)

---

## Next Steps (After Component 1)

**Component 2:** OAuth2 Flow + PKCE + Redis Session Store (3 days)
- Implement authorization redirect with PKCE
- Implement /callback handler with token exchange
- Implement ID token validation (JWKS)
- Implement token refresh logic
- Fully implement session_store.py (currently design-only)

---

**Ready for Plan Review:** This plan should be reviewed via zen-mcp (Gemini + Codex planners) before implementation begins.
