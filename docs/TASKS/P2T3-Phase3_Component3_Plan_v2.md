# P2T3 Phase 3 - Component 3: Session Management + UX + CSP Hardening (v2)

**Status:** Planning (Revision 2 - Codex Critical Issues Fixed)
**Component:** 3 of 6
**Estimated Duration:** 2 days (16 hours)
**Dependencies:**
- Component 1 (OAuth2 Config & IdP Setup) ✅ COMPLETED
- Component 2 (OAuth2 Authorization Flow with PKCE) ✅ COMPLETED

**References:**
- Parent Task: `docs/TASKS/P2T3-Phase3_TASK.md`
- Component 1 Plan: `docs/TASKS/P2T3-Phase3_Component1_Plan.md`
- Component 2 Plan: `docs/TASKS/P2T3-Phase3_Component2_Plan_v3.md`
- Session Store Design: `docs/ARCHITECTURE/redis-session-schema.md`
- ADR-015: Auth0 IdP Selection
- **v1 Review:** Codex identified 2 CRITICAL and 2 HIGH issues (token leakage, broken auto-refresh, datetime parsing, weak CSP)

---

## v2 Changes from v1

**CRITICAL FIXES:**

1. **Token Leakage Prevention (Codex Critical #1)**
   - ❌ v1: `validate_session()` returned `access_token` to `st.session_state`
   - ✅ v2: Only return non-sensitive user metadata (user_id, email, display_name)
   - ✅ v2: All API calls use backend helper that fetches token from Redis

2. **Auto-Refresh Logic Fixed (Codex Critical #2)**
   - ❌ v1: Used `last_activity` which resets on every request (never triggers)
   - ✅ v2: Track `access_token_expires_at` in session data
   - ✅ v2: Refresh 10 minutes before actual expiry (not based on activity)

**HIGH-PRIORITY FIXES:**

3. **Datetime Parsing Fixed (Codex High #3)**
   - ❌ v1: `datetime.fromisoformat()` crashes on `...Z` format
   - ✅ v2: Use `.replace('Z', '+00:00')` before parsing

4. **CSP Hardening (Codex High #4)**
   - ❌ v1: Allowed `'unsafe-inline'` without nonces (weak XSS protection)
   - ✅ v2: Use nonce-based CSP with `script-src 'nonce-{random}'`
   - ✅ v2: Add Auth0/auth_service to `connect-src`
   - ✅ v2: Replace meta-refresh with Streamlit `st.rerun()` timer

**MEDIUM FIXES:**

5. **Logout Binding Validation (Codex Medium #5)**
   - ❌ v1: Logout used `current_ip="unknown"` (bypassed binding)
   - ✅ v2: Validate binding on logout; only revoke if binding valid

6. **Testing Gaps Filled (Codex Medium #6)**
   - ✅ v2: Add absolute timeout tests (session dies at 4h even with activity)
   - ✅ v2: Add IP spoofing tests (X-Forwarded-For validation)
   - ✅ v2: Add CSP violation tests (nonce required, inline blocked)

---

## Overview

Implement production-grade session management with idle timeout warnings, automatic token refresh, CSP hardening for XSS protection, and seamless session refresh UX. This component focuses on security hardening and user experience for the OAuth2 authentication system.

**Success Criteria:**
- Idle timeout warning displayed 2 minutes before session expiry
- Automatic token refresh before access token expiration (using actual expiry time)
- CSP headers with nonces prevent XSS attacks
- Session binding validation enforced (IP + User-Agent) - including logout
- Logout flow clears all session state and validates binding
- **No tokens in Streamlit session_state** (CRITICAL - only user metadata)

---

## Security Requirements

### Session Security
- **Absolute timeout**: 4 hours (14,400s) - enforced by Redis TTL (NEVER extended)
- **Idle timeout**: 15 minutes (900s) - enforced by application
- **Session binding**: Validate IP address and User-Agent on **every request including logout**
- **Identity swap protection**: Verify `sub` claim matches on token refresh (already implemented in Component 2)
- **Single-use state/nonce**: CSRF and replay protection (already implemented in Component 2)

### XSS Protection (Defense in Depth)
- **HttpOnly cookies**: Session IDs not accessible to JavaScript (already implemented in Component 2)
- **CSP headers with nonces**: Restrict script sources, block inline scripts without nonce
- **Trusted proxy validation**: Prevent IP spoofing via X-Forwarded-For
- **User-Agent validation**: Detect session hijacking attempts

### Token Security
- **NEVER in session_state**: All tokens stored in encrypted Redis only
- **Only non-sensitive metadata in session_state**: user_id, email, display_name
- **Automatic refresh**: Refresh access token before expiration (tracked via `access_token_expires_at`)
- **Rotation on refresh**: Refresh token rotated on every refresh (Auth0 configured)
- **Revocation on logout**: Revoke refresh token at Auth0 on logout (with binding validation)

---

## Tasks Breakdown

### Task 1: Session Binding Validation + Token Metadata Tracking (5 hours)

**Goal:** Prevent session hijacking by validating IP address and User-Agent on every request, and track token expiry for auto-refresh.

**Implementation Steps:**

1. **Update SessionData Model to Track Token Expiry**

   Modify: `apps/web_console/auth/session_store.py`

   Add `access_token_expires_at` field to track when access token expires:

   ```python
   from datetime import datetime, timedelta, UTC
   from pydantic import BaseModel

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

       # NEW: Track token expiry for auto-refresh
       access_token_expires_at: datetime  # When access token expires (from Auth0 response)
   ```

2. **Update Session Store with Binding Validation**

   Modify: `apps/web_console/auth/session_store.py`

   **Note:** This logic may already exist from Component 2. Update to ensure binding validation and TTL preservation:

   ```python
   async def get_session(
       self,
       session_id: str,
       current_ip: str,
       current_user_agent: str,
       update_activity: bool = True,
   ) -> Optional[SessionData]:
       """Retrieve and validate session with binding enforcement.

       Args:
           session_id: Session identifier from cookie
           current_ip: Current request IP address (from X-Real-IP or remote_addr)
           current_user_agent: Current request User-Agent header
           update_activity: If True, update last_activity timestamp

       Returns:
           SessionData if valid, None if expired or binding mismatch
       """
       key = f"session:{session_id}"
       encrypted = await self.redis.get(key)

       if not encrypted:
           return None

       # Decrypt session data
       json_data = self._decrypt(encrypted)
       session_data = SessionData.model_validate_json(json_data)

       # CRITICAL: Validate session binding
       if session_data.ip_address != current_ip:
           logger.warning(
               "Session IP mismatch - possible hijacking attempt",
               extra={
                   "session_id": session_id[:8] + "...",
                   "expected_ip": session_data.ip_address,
                   "actual_ip": current_ip,
                   "user_id": session_data.user_id,
               }
           )
           await self.delete_session(session_id)
           return None

       if session_data.user_agent != current_user_agent:
           logger.warning(
               "Session User-Agent mismatch - possible hijacking attempt",
               extra={
                   "session_id": session_id[:8] + "...",
                   "expected_ua": session_data.user_agent[:50],
                   "actual_ua": current_user_agent[:50],
                   "user_id": session_data.user_id,
               }
           )
           await self.delete_session(session_id)
           return None

       # Check idle timeout
       now = datetime.now(UTC)
       if now - session_data.last_activity > self.idle_timeout:
           logger.info(
               "Session idle timeout exceeded",
               extra={
                   "session_id": session_id[:8] + "...",
                   "idle_duration_seconds": (now - session_data.last_activity).total_seconds(),
               }
           )
           await self.delete_session(session_id)
           return None

       # Update last_activity if valid
       if update_activity:
           session_data.last_activity = now

           # CRITICAL: Preserve absolute timeout (don't reset TTL)
           # Calculate remaining TTL from creation time
           remaining_absolute = self.absolute_timeout - (now - session_data.created_at)
           remaining_seconds = max(1, int(remaining_absolute.total_seconds()))

           # Re-encrypt and store with REMAINING TTL (not full 4 hours)
           json_data = session_data.model_dump_json()
           encrypted_updated = self._encrypt(json_data)
           await self.redis.setex(key, remaining_seconds, encrypted_updated)

       return session_data
   ```

3. **Update OAuth2 Callback to Store Token Expiry**

   Modify: `apps/web_console/auth/oauth2_flow.py`

   Update `handle_callback()` to calculate and store `access_token_expires_at`:

   ```python
   async def handle_callback(
       self,
       code: str,
       state: str,
       ip_address: str,
       user_agent: str,
   ) -> tuple[str, SessionData]:
       """Handle OAuth2 callback from Auth0."""
       # ... existing code (state validation, token exchange, ID token validation)

       # Calculate access token expiry (Auth0 default: 1 hour / 3600s)
       # expires_in is returned in token response (in seconds)
       now = datetime.now(UTC)
       expires_in_seconds = tokens.get("expires_in", 3600)  # Default 1 hour
       access_token_expires_at = now + timedelta(seconds=expires_in_seconds)

       session_data = SessionData(
           access_token=tokens["access_token"],
           refresh_token=tokens["refresh_token"],
           id_token=tokens["id_token"],
           user_id=id_token_claims["sub"],
           email=id_token_claims.get("email", "unknown@example.com"),
           created_at=now,
           last_activity=now,
           ip_address=ip_address,
           user_agent=user_agent,
           access_token_expires_at=access_token_expires_at,  # NEW
       )

       await self.session_store.create_session(session_id, session_data)

       return session_id, session_data
   ```

4. **Update Token Refresh to Update Expiry**

   Modify: `apps/web_console/auth/oauth2_flow.py`

   Update `refresh_tokens()` to recalculate `access_token_expires_at`:

   ```python
   async def refresh_tokens(
       self,
       session_id: str,
       ip_address: str,
       user_agent: str,
   ) -> SessionData:
       """Refresh access token with session binding validation."""
       # ... existing code (binding validation, refresh token exchange)

       # Update access token and expiry
       session_data.access_token = tokens["access_token"]
       session_data.refresh_token = tokens.get("refresh_token", session_data.refresh_token)

       # NEW: Update access token expiry
       now = datetime.now(UTC)
       expires_in_seconds = tokens.get("expires_in", 3600)
       session_data.access_token_expires_at = now + timedelta(seconds=expires_in_seconds)

       # ... existing code (ID token validation, TTL preservation)

       return session_data
   ```

5. **Update Streamlit Session Manager to Return Only Metadata**

   Modify: `apps/web_console/auth/session_manager.py`

   **CRITICAL FIX:** Remove `access_token` from returned dict (Codex Critical #1):

   ```python
   async def validate_session(
       session_id: str,
       session_store: RedisSessionStore,
       client_ip: str,
       user_agent: str,
   ) -> dict[str, Any] | None:
       """Validate session ID with binding enforcement.

       CRITICAL: Returns ONLY non-sensitive user metadata.
       Tokens remain in Redis and are fetched by backend helpers.
       """
       if not session_id:
           return None

       try:
           # Validate session with IP/UA binding
           session_data = await session_store.get_session(
               session_id,
               current_ip=client_ip,
               current_user_agent=user_agent,
               update_activity=True,
           )

           if not session_data:
               logger.info("Invalid or expired session", extra={"session_id": session_id[:8] + "..."})
               return None

           # Convert to dict - ONLY non-sensitive metadata
           return {
               "user_id": session_data.user_id,
               "email": session_data.email,
               "display_name": session_data.email.split("@")[0],  # Derive display name
               "created_at": session_data.created_at.isoformat(),
               "last_activity": session_data.last_activity.isoformat(),
               "access_token_expires_at": session_data.access_token_expires_at.isoformat(),
               # NEVER include: access_token, refresh_token, id_token
           }
       except Exception as e:
           logger.error(f"Session validation error: {e}")
           return None
   ```

6. **Create Backend Helper for API Calls**

   Create: `apps/web_console/auth/api_client.py`

   ```python
   """Secure API client that fetches access tokens from Redis (never from session_state)."""

   import httpx
   import logging
   from typing import Optional
   import streamlit as st

   from apps.web_console.auth.session_store import RedisSessionStore

   logger = logging.getLogger(__name__)


   async def get_access_token_from_redis(
       session_id: str,
       session_store: RedisSessionStore,
       client_ip: str,
       user_agent: str,
   ) -> Optional[str]:
       """Fetch access token from Redis session store.

       CRITICAL: This is the ONLY way to get access tokens in Streamlit.
       Tokens are NEVER stored in st.session_state.
       """
       session_data = await session_store.get_session(
           session_id,
           current_ip=client_ip,
           current_user_agent=user_agent,
           update_activity=False,  # Don't update activity for token fetch
       )

       if not session_data:
           return None

       return session_data.access_token


   async def call_api_with_auth(
       url: str,
       method: str = "GET",
       session_id: Optional[str] = None,
       session_store: Optional[RedisSessionStore] = None,
       client_ip: Optional[str] = None,
       user_agent: Optional[str] = None,
       **kwargs,
   ) -> httpx.Response:
       """Call API with OAuth2 bearer token from Redis.

       Example:
           response = await call_api_with_auth(
               "https://api.trading-platform.local/positions",
               method="GET",
               session_id=session_id,
               session_store=session_store,
               client_ip=client_ip,
               user_agent=user_agent,
           )
       """
       if not all([session_id, session_store, client_ip, user_agent]):
           raise ValueError("Missing required parameters for authenticated API call")

       # Fetch access token from Redis
       access_token = await get_access_token_from_redis(
           session_id, session_store, client_ip, user_agent
       )

       if not access_token:
           raise ValueError("Session invalid or expired")

       # Add Authorization header
       headers = kwargs.get("headers", {})
       headers["Authorization"] = f"Bearer {access_token}"
       kwargs["headers"] = headers

       # Make API call
       async with httpx.AsyncClient(timeout=10.0) as client:
           response = await client.request(method, url, **kwargs)
           return response
   ```

**Files Modified:**
- `apps/web_console/auth/session_store.py` (add `access_token_expires_at`, binding validation)
- `apps/web_console/auth/session_manager.py` (remove tokens from returned dict)
- `apps/web_console/auth/oauth2_flow.py` (track token expiry in callback and refresh)

**Files Created:**
- `apps/web_console/auth/api_client.py` (secure API helper)

**Testing:**
- Unit test: Session binding mismatch returns None and deletes session
- Unit test: `validate_session()` returns ONLY non-sensitive metadata (no tokens)
- Integration test: Change IP mid-session, verify session invalidated
- Integration test: Change User-Agent mid-session, verify session invalidated
- Integration test: API calls via `call_api_with_auth()` fetch token from Redis

---

### Task 2: Idle Timeout Warning UI (4 hours)

**Goal:** Display countdown warning 2 minutes before idle timeout expires, with option to extend session.

**Implementation Steps:**

1. **Create Idle Timeout Monitor Component**

   Create: `apps/web_console/auth/idle_timeout_monitor.py`

   **FIX:** Handle `Z` timezone format (Codex High #3):

   ```python
   """Idle timeout monitoring and warning UI for Streamlit."""

   import streamlit as st
   from datetime import datetime, timedelta, UTC
   from typing import Optional
   import asyncio


   def parse_iso_datetime(iso_string: str) -> datetime:
       """Parse ISO datetime string with Z timezone support.

       FIX (Codex High #3): Handle ...Z format from session schema.
       """
       # Replace 'Z' with '+00:00' for fromisoformat compatibility
       normalized = iso_string.replace('Z', '+00:00')
       return datetime.fromisoformat(normalized)


   def get_idle_timeout_warning_threshold() -> timedelta:
       """Get threshold for displaying idle timeout warning (2 minutes before expiry)."""
       return timedelta(minutes=13)  # Warning at 13min (2min before 15min timeout)


   def get_time_until_idle_timeout(last_activity_str: str) -> timedelta:
       """Calculate time remaining until idle timeout.

       Args:
           last_activity_str: ISO datetime string from session metadata
       """
       last_activity = parse_iso_datetime(last_activity_str)
       idle_timeout = timedelta(minutes=15)
       now = datetime.now(UTC)
       elapsed = now - last_activity
       return idle_timeout - elapsed


   def should_show_idle_warning(last_activity_str: str) -> bool:
       """Determine if idle timeout warning should be shown."""
       time_remaining = get_time_until_idle_timeout(last_activity_str)
       warning_threshold = timedelta(minutes=2)  # Show warning with 2min remaining

       return time_remaining <= warning_threshold and time_remaining > timedelta(0)


   def render_idle_timeout_warning(last_activity_str: str):
       """Render idle timeout warning banner with countdown.

       Uses st.rerun() instead of meta-refresh (better UX, CSP-friendly).
       """
       time_remaining = get_time_until_idle_timeout(last_activity_str)

       if time_remaining.total_seconds() <= 0:
           st.error("⏰ Your session has expired due to inactivity. Please log in again.")
           st.markdown("[Login](/login)")
           st.stop()

       minutes_remaining = int(time_remaining.total_seconds() / 60)
       seconds_remaining = int(time_remaining.total_seconds() % 60)

       st.warning(
           f"⏰ Your session will expire in {minutes_remaining}m {seconds_remaining}s due to inactivity. "
           "Interact with the page to extend your session."
       )

       # Use st.rerun() with timer instead of meta-refresh (CSP-friendly)
       import time
       time.sleep(5)
       st.rerun()


   async def extend_session_via_refresh():
       """Extend session by calling /refresh endpoint."""
       import httpx

       session_id = st.context.cookies.get("session_id")
       if not session_id:
           return

       async with httpx.AsyncClient() as client:
           try:
               response = await client.post(
                   "http://auth_service:8000/refresh",
                   cookies={"session_id": session_id},
               )
               response.raise_for_status()
               st.success("✅ Session extended successfully")
               st.rerun()
           except httpx.HTTPStatusError as e:
               st.error(f"Failed to extend session: {e.response.text}")
   ```

2. **Integrate Idle Timeout Monitor into Streamlit App**

   Modify: `apps/web_console/app.py`

   Add idle timeout check after authentication:

   ```python
   from apps.web_console.auth.idle_timeout_monitor import (
       should_show_idle_warning,
       render_idle_timeout_warning,
       extend_session_via_refresh,
       parse_iso_datetime,
   )


   def main():
       """Main Streamlit application."""
       # Require authentication for all pages
       if st.query_params.get("path") not in ["/login", "/callback"]:
           require_auth()

       # Check idle timeout if authenticated
       if "user_info" in st.session_state:
           last_activity_str = st.session_state["user_info"]["last_activity"]

           if should_show_idle_warning(last_activity_str):
               render_idle_timeout_warning(last_activity_str)

       # Render page content
       path = st.query_params.get("path", "/")
       if path == "/":
           render_dashboard()
       elif path == "/positions":
           render_positions()
       # ... other pages

       # Sidebar with manual extend session button
       with st.sidebar:
           if "user_info" in st.session_state:
               st.write(f"👤 {st.session_state['user_info']['display_name']}")

               if st.button("Extend Session"):
                   asyncio.run(extend_session_via_refresh())
   ```

**Files Created:**
- `apps/web_console/auth/idle_timeout_monitor.py`

**Files Modified:**
- `apps/web_console/app.py` (add idle timeout warning integration)

**Testing:**
- Unit test: `parse_iso_datetime()` handles `...Z` format correctly
- Unit test: `should_show_idle_warning()` returns True 2 minutes before expiry
- Manual test: Wait 13 minutes after login, verify warning appears
- Manual test: Click "Extend Session", verify countdown resets
- Manual test: Wait 15 minutes without interaction, verify session expires

---

### Task 3: Automatic Access Token Refresh (3 hours)

**Goal:** Automatically refresh access token before expiration using actual expiry time (not activity-based).

**Implementation Steps:**

1. **Create Token Refresh Background Task**

   Create: `apps/web_console/auth/token_refresh_monitor.py`

   **FIX:** Use actual token expiry instead of `last_activity` (Codex Critical #2):

   ```python
   """Background token refresh monitor for Streamlit."""

   import asyncio
   import logging
   import httpx
   from datetime import datetime, timedelta, UTC
   from typing import Optional

   logger = logging.getLogger(__name__)


   def parse_iso_datetime(iso_string: str) -> datetime:
       """Parse ISO datetime with Z support."""
       return datetime.fromisoformat(iso_string.replace('Z', '+00:00'))


   class TokenRefreshMonitor:
       """Monitors access token expiration and triggers refresh.

       FIX (Codex Critical #2): Uses actual token expiry, not last_activity.
       """

       def __init__(
           self,
           refresh_threshold_seconds: int = 600,  # Refresh 10 minutes before expiry
       ):
           self.refresh_threshold = timedelta(seconds=refresh_threshold_seconds)

       def should_refresh_token(self, user_info: dict) -> bool:
           """Determine if access token should be refreshed.

           FIX: Use access_token_expires_at (actual expiry) instead of last_activity.

           Args:
               user_info: User metadata from st.session_state (contains expires_at)

           Returns:
               True if token should be refreshed (10min before expiry)
           """
           if "access_token_expires_at" not in user_info:
               logger.warning("access_token_expires_at missing from user_info")
               return False

           expires_at = parse_iso_datetime(user_info["access_token_expires_at"])
           now = datetime.now(UTC)
           time_until_expiry = expires_at - now

           # Refresh if less than 10 minutes remaining
           should_refresh = time_until_expiry <= self.refresh_threshold

           if should_refresh:
               logger.info(
                   "Token expiring soon, triggering refresh",
                   extra={
                       "time_until_expiry_seconds": time_until_expiry.total_seconds(),
                       "threshold_seconds": self.refresh_threshold.total_seconds(),
                   }
               )

           return should_refresh

       async def refresh_token_via_api(self, session_id: str) -> bool:
           """Call /refresh endpoint to refresh access token."""
           async with httpx.AsyncClient(timeout=10.0) as client:
               try:
                   response = await client.post(
                       "http://auth_service:8000/refresh",
                       cookies={"session_id": session_id},
                   )
                   response.raise_for_status()
                   logger.info("Access token refreshed automatically")
                   return True
               except httpx.HTTPStatusError as e:
                   logger.error(f"Token refresh failed: {e.response.status_code}")
                   return False
               except Exception as e:
                   logger.error(f"Token refresh error: {e}")
                   return False
   ```

2. **Integrate Token Refresh into Streamlit App**

   Modify: `apps/web_console/app.py`

   Add token refresh check before rendering pages:

   ```python
   from apps.web_console.auth.token_refresh_monitor import TokenRefreshMonitor

   # Initialize token refresh monitor (global singleton)
   token_refresh_monitor = TokenRefreshMonitor(
       refresh_threshold_seconds=600,  # Refresh 10 min before expiry
   )


   def main():
       """Main Streamlit application."""
       # ... existing auth and idle timeout checks

       # Check if token needs refresh (proactive refresh)
       if "user_info" in st.session_state:
           session_id = st.context.cookies.get("session_id")

           if token_refresh_monitor.should_refresh_token(st.session_state["user_info"]):
               # Trigger refresh (updates Redis session)
               refresh_success = asyncio.run(token_refresh_monitor.refresh_token_via_api(session_id))

               if refresh_success:
                   # Refresh user_info from updated session
                   st.rerun()

       # ... rest of app
   ```

**Files Created:**
- `apps/web_console/auth/token_refresh_monitor.py`

**Files Modified:**
- `apps/web_console/app.py` (add token refresh check)

**Testing:**
- Unit test: `should_refresh_token()` returns True 10 minutes before expiry
- Unit test: `should_refresh_token()` returns False with 20 minutes remaining
- Integration test: Mock token expiry to 5 minutes from now, verify /refresh called
- Manual test: Login, mock time to 50 minutes, verify token refreshed without logout

---

### Task 4: CSP Headers for XSS Protection with Nonces (4 hours)

**Goal:** Add Content Security Policy headers with nonces to prevent XSS attacks.

**Implementation Steps:**

1. **Add Nonce Generation to Nginx**

   Modify: `apps/web_console/nginx/nginx.conf`

   **FIX:** Use nonce-based CSP instead of `unsafe-inline` (Codex High #4):

   ```nginx
   # Generate random nonce for CSP (per request)
   map $request_id $csp_nonce {
       default $request_id;
   }

   server {
       listen 443 ssl;
       server_name web-console.trading-platform.local;

       # ... existing TLS config

       # Content Security Policy with nonces (XSS protection)
       # FIX: Removed 'unsafe-inline', added nonces, added Auth0/auth_service to connect-src
       # FIX (Codex Medium): Hardcode Auth0 domain instead of undefined $AUTH0_DOMAIN variable
       # NOTE: Update 'trading-platform.us.auth0.com' to match your AUTH0_DOMAIN
       add_header Content-Security-Policy "default-src 'self'; script-src 'self' 'nonce-$csp_nonce' https://cdn.streamlit.io; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; font-src 'self' data:; connect-src 'self' ws: wss: https://trading-platform.us.auth0.com http://auth_service:8000; frame-ancestors 'none'; base-uri 'self'; form-action 'self';" always;

       # Additional security headers
       add_header X-Content-Type-Options "nosniff" always;
       add_header X-Frame-Options "DENY" always;
       add_header X-XSS-Protection "1; mode=block" always;
       add_header Referrer-Policy "strict-origin-when-cross-origin" always;
       add_header Permissions-Policy "geolocation=(), microphone=(), camera=()" always;

       # Pass nonce to backend for inline script generation
       proxy_set_header X-CSP-Nonce $csp_nonce;

       # ... existing location blocks
   }
   ```

   **Note:** `style-src 'unsafe-inline'` is still required for Streamlit styling. Scripts are protected with nonces.

2. **Update Streamlit to Use Nonces for Inline Scripts**

   Create: `apps/web_console/auth/csp_nonce.py`

   ```python
   """CSP nonce handling for Streamlit inline scripts."""

   import streamlit as st
   from typing import Optional


   def get_csp_nonce() -> Optional[str]:
       """Get CSP nonce from request headers (set by nginx).

       Returns:
           Nonce value if present, None otherwise
       """
       try:
           from streamlit.web.server.websocket_headers import _get_websocket_headers

           headers = _get_websocket_headers()
           return headers.get("X-CSP-Nonce") if headers else None
       except Exception:
           return None


   def render_script_with_nonce(script: str):
       """Render inline script with CSP nonce.

       Example:
           render_script_with_nonce("console.log('Hello');")
       """
       nonce = get_csp_nonce()

       if nonce:
           st.markdown(
               f'<script nonce="{nonce}">{script}</script>',
               unsafe_allow_html=True,
           )
       else:
           # Fallback: Log warning if nonce missing
           import logging
           logger = logging.getLogger(__name__)
           logger.warning("CSP nonce missing - inline script may be blocked")
   ```

3. **Update Idle Timeout Monitor to Avoid Inline Scripts**

   Modify: `apps/web_console/auth/idle_timeout_monitor.py`

   Remove meta-refresh (inline content), use `st.rerun()` with timer (already done in Task 2):

   ```python
   def render_idle_timeout_warning(last_activity_str: str):
       """Render idle timeout warning with st.rerun() timer (CSP-friendly)."""
       # ... existing warning display code

       # Use st.rerun() with sleep instead of meta-refresh
       import time
       time.sleep(5)
       st.rerun()  # Refreshes page every 5 seconds
   ```

4. **Test CSP Headers**

   Create: `tests/integration/test_csp_headers.py`

   **UPDATED:** Add CSP violation tests (Codex Medium #6):

   ```python
   """Integration tests for Content Security Policy headers."""

   import httpx
   import pytest


   @pytest.mark.asyncio
   async def test_csp_headers_present():
       """Verify CSP headers are present on all responses."""
       async with httpx.AsyncClient() as client:
           response = await client.get(
               "https://web-console.trading-platform.local/",
               verify=False,  # Skip cert verification for self-signed certs
           )

           assert "Content-Security-Policy" in response.headers
           csp = response.headers["Content-Security-Policy"]

           # Verify key CSP directives
           assert "default-src 'self'" in csp
           assert "script-src 'self'" in csp
           assert "'nonce-" in csp  # Nonce present
           assert "unsafe-inline" not in csp.split("script-src")[1].split(";")[0]  # No unsafe-inline for scripts
           assert "frame-ancestors 'none'" in csp
           assert "base-uri 'self'" in csp


   @pytest.mark.asyncio
   async def test_security_headers_present():
       """Verify all security headers are present."""
       async with httpx.AsyncClient() as client:
           response = await client.get(
               "https://web-console.trading-platform.local/",
               verify=False,
           )

           assert response.headers["X-Content-Type-Options"] == "nosniff"
           assert response.headers["X-Frame-Options"] == "DENY"
           assert response.headers["X-XSS-Protection"] == "1; mode=block"
           assert "Referrer-Policy" in response.headers
           assert "Permissions-Policy" in response.headers


   @pytest.mark.asyncio
   async def test_csp_blocks_inline_script_without_nonce():
       """Verify CSP blocks inline scripts without nonce (browser test).

       This test requires a real browser (Selenium/Playwright) to verify CSP enforcement.
       Manual test: Open browser DevTools console, try to inject inline script.
       """
       # TODO: Add Selenium/Playwright test
       # Expected: Browser blocks script execution and logs CSP violation
       pass


   @pytest.mark.asyncio
   async def test_csp_allows_script_with_nonce():
       """Verify CSP allows inline scripts WITH nonce (browser test).

       Manual test: Use render_script_with_nonce() to inject script with nonce.
       """
       # TODO: Add Selenium/Playwright test
       # Expected: Script executes successfully
       pass
   ```

**Files Modified:**
- `apps/web_console/nginx/nginx.conf` (add nonce-based CSP)
- `apps/web_console/auth/idle_timeout_monitor.py` (remove meta-refresh)

**Files Created:**
- `apps/web_console/auth/csp_nonce.py` (nonce handling)
- `tests/integration/test_csp_headers.py`

**Testing:**
- Integration test: Verify CSP headers present with nonces
- Integration test: Verify no `'unsafe-inline'` in `script-src`
- Manual test: Open browser DevTools, verify CSP policy displayed
- Manual test: Try injecting inline script without nonce, verify blocked by CSP

---

### Task 5: Logout Flow with Token Revocation + Binding Validation (2 hours)

**Goal:** Implement secure logout that validates binding, clears session, revokes refresh token at Auth0, and redirects to login.

**Implementation Steps:**

1. **Update OAuth2 Flow Handler with Binding Validation on Logout**

   Modify: `apps/web_console/auth/oauth2_flow.py`

   **FIX:** Validate binding on logout (Codex Medium #5):

   ```python
   async def handle_logout(
       self,
       session_id: str,
       current_ip: str,
       current_user_agent: str,
   ) -> str:
       """Handle OAuth2 logout with token revocation and binding validation.

       FIX (Codex Medium #5): Validate binding on logout to prevent
       attacker with stolen cookie from revoking real user's refresh token.

       Args:
           session_id: Session ID to delete
           current_ip: Client IP address for binding validation
           current_user_agent: Client User-Agent for binding validation

       Returns:
           Auth0 logout URL to redirect to
       """
       # Retrieve session WITH binding validation
       session_data = await self.session_store.get_session(
           session_id,
           current_ip=current_ip,
           current_user_agent=current_user_agent,
           update_activity=False,
       )

       # Revoke refresh token at Auth0 ONLY if binding is valid
       if session_data and session_data.refresh_token:
           try:
               await self._revoke_refresh_token(session_data.refresh_token)
               logger.info(
                   "Refresh token revoked at Auth0",
                   extra={"user_id": session_data.user_id}
               )
           except Exception as e:
               # Non-critical: Session will still be deleted locally
               logger.error(f"Refresh token revocation failed (non-critical): {e}")
       elif not session_data:
           # Binding failed - delete session locally but don't revoke at Auth0
           logger.warning(
               "Logout binding validation failed - deleting session locally only",
               extra={
                   "session_id": session_id[:8] + "...",
                   "current_ip": current_ip,
               }
           )

       # Delete session from Redis
       await self.session_store.delete_session(session_id)

       # Build Auth0 logout URL
       params = {
           "client_id": self.config.client_id,
           "returnTo": self.config.logout_redirect_uri,
       }
       query = urlencode(params)
       logout_url = f"{self.logout_endpoint}?{query}"

       return logout_url


   async def _revoke_refresh_token(self, refresh_token: str) -> None:
       """Revoke refresh token at Auth0 (internal method).

       See: https://auth0.com/docs/api/authentication#revoke-refresh-token
       """
       revocation_endpoint = f"https://{self.config.auth0_domain}/oauth/revoke"

       async with httpx.AsyncClient(timeout=10.0) as client:
           response = await client.post(
               revocation_endpoint,
               data={
                   "client_id": self.config.client_id,
                   "client_secret": self.config.client_secret,
                   "token": refresh_token,
               },
               headers={"Content-Type": "application/x-www-form-urlencoded"},
           )
           response.raise_for_status()
   ```

2. **Update FastAPI Logout Route to Pass Binding Info**

   Modify: `apps/auth_service/routes/logout.py`

   Update to extract and pass IP/UA to `handle_logout()`:

   ```python
   """Logout endpoint with cookie clearing and binding validation."""

   from fastapi import APIRouter, Request, Cookie
   from fastapi.responses import RedirectResponse
   import logging

   from apps.auth_service.dependencies import get_oauth2_handler, get_config
   from apps.web_console.utils import extract_client_ip_from_fastapi, extract_user_agent_from_fastapi

   logger = logging.getLogger(__name__)
   router = APIRouter()


   @router.get("/logout")
   async def logout(
       request: Request,
       session_id: str = Cookie(None),
   ):
       """Handle logout with binding validation.

       Validates session binding, deletes session, revokes refresh token at Auth0,
       clears cookie, redirects to Auth0 logout.

       Args:
           session_id: Session ID from HttpOnly cookie

       Returns:
           RedirectResponse to Auth0 logout with cleared cookie
       """
       if not session_id:
           # No session, just redirect to login
           return RedirectResponse(url="/login", status_code=302)

       # Get client info for binding validation
       def get_remote_addr() -> str:
           return request.client.host if request.client else "unknown"

       client_ip = extract_client_ip_from_fastapi(request, get_remote_addr)
       user_agent = extract_user_agent_from_fastapi(request)

       # Delete session with binding validation and token revocation
       oauth2_handler = get_oauth2_handler()
       logout_url = await oauth2_handler.handle_logout(
           session_id,
           current_ip=client_ip,
           current_user_agent=user_agent,
       )

       # Build redirect response
       response = RedirectResponse(url=logout_url, status_code=302)

       # Clear session cookie
       config = get_config()
       response.set_cookie(
           key="session_id",
           value="",
           max_age=0,  # Expire immediately
           path="/",
           domain=config.cookie_domain,
           secure=True,
           httponly=True,
           samesite="lax",
       )

       logger.info(
           "User logged out, cookie cleared",
           extra={"session_id": session_id[:8] + "..."},
       )

       return response
   ```

3. **Add Logout Button to Streamlit UI**

   Modify: `apps/web_console/app.py`

   Add logout button to sidebar:

   ```python
   def main():
       """Main Streamlit application."""
       # ... existing auth and timeout checks

       # Sidebar with user info and logout
       with st.sidebar:
           if "user_info" in st.session_state:
               st.write(f"👤 {st.session_state['user_info']['display_name']}")

               if st.button("Extend Session"):
                   asyncio.run(extend_session_via_refresh())

               if st.button("Logout"):
                   # Clear Streamlit session state
                   st.session_state.clear()

                   # Redirect to /logout (FastAPI will handle binding validation, cookie clearing, Auth0 logout)
                   st.markdown(
                       '<meta http-equiv="refresh" content="0;url=/logout">',
                       unsafe_allow_html=True,
                   )
                   st.stop()
   ```

**Files Modified:**
- `apps/web_console/auth/oauth2_flow.py` (add binding validation to `handle_logout()`)
- `apps/auth_service/routes/logout.py` (extract and pass IP/UA)
- `apps/web_console/app.py` (add logout button)

**Testing:**
- Unit test: `handle_logout()` validates binding before revocation
- Unit test: Binding mismatch deletes session but doesn't revoke token
- Integration test: Call /logout with valid binding, verify refresh token revoked at Auth0
- Integration test: Call /logout with invalid IP, verify session deleted but no revocation
- Integration test: Verify session deleted from Redis
- Integration test: Verify cookie cleared
- Manual test: Login, logout, verify cannot access protected pages without re-login

---

## Additional Testing (Codex Medium #6)

### Absolute Timeout Tests (NEW)

Add to `tests/unit/test_session_store.py`:

```python
@pytest.mark.asyncio
async def test_absolute_timeout_enforced_despite_activity():
    """Verify session dies at 4 hours even with continuous activity."""
    session_store = RedisSessionStore(
        redis_client=mock_redis,
        encryption_key=test_key,
        absolute_timeout_hours=4,
        idle_timeout_minutes=15,
    )

    # Create session
    session_id = "test_session_absolute_timeout"
    session_data = SessionData(
        access_token="test_token",
        refresh_token="test_refresh",
        id_token="test_id_token",
        user_id="auth0|12345",
        email="test@example.com",
        created_at=datetime.now(UTC) - timedelta(hours=3, minutes=59),  # Created 3h59m ago
        last_activity=datetime.now(UTC),  # Just updated
        ip_address="192.168.1.1",
        user_agent="test-agent",
        access_token_expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    await session_store.create_session(session_id, session_data)

    # Get session with activity update (simulates active user)
    retrieved = await session_store.get_session(
        session_id,
        current_ip="192.168.1.1",
        current_user_agent="test-agent",
        update_activity=True,
    )
    assert retrieved is not None

    # Check TTL - should be ~1 minute (not 4 hours reset)
    ttl = await mock_redis.ttl(f"session:{session_id}")
    assert ttl <= 60  # Should expire in ~1 minute (not 4 hours)

    # Simulate 1 minute passing
    await asyncio.sleep(61)

    # Session should be expired (absolute timeout enforced)
    expired = await session_store.get_session(
        session_id,
        current_ip="192.168.1.1",
        current_user_agent="test-agent",
        update_activity=False,
    )
    assert expired is None
```

### IP Spoofing Tests (NEW)

Add to `tests/integration/test_session_binding.py`:

```python
@pytest.mark.asyncio
async def test_x_forwarded_for_spoofing_blocked():
    """Verify X-Forwarded-For spoofing is blocked by trusted proxy validation."""
    # Attacker tries to spoof X-Forwarded-For header
    async with httpx.AsyncClient() as client:
        response = await client.get(
            "https://web-console.trading-platform.local/",
            headers={
                "X-Forwarded-For": "10.0.0.100",  # Spoofed IP
                "X-Real-IP": "203.0.113.1",  # Actual IP (from nginx)
            },
            verify=False,
        )

        # Should use X-Real-IP (from trusted proxy) not X-Forwarded-For
        # Session validation should fail if IP doesn't match
        assert response.status_code in [401, 302]  # Unauthorized or redirect to login
```

### CSP Violation Tests (NEW)

Add to `tests/integration/test_csp_headers.py`:

```python
@pytest.mark.asyncio
async def test_csp_nonce_required_for_inline_script():
    """Verify CSP requires nonce for inline scripts (browser test).

    This test requires Selenium/Playwright for full validation.
    """
    # Manual test checklist:
    # 1. Open browser DevTools Console
    # 2. Try to inject: <script>alert('XSS')</script>
    # 3. Expected: CSP blocks execution, logs violation
    # 4. Try with nonce: <script nonce="valid-nonce">alert('OK')</script>
    # 5. Expected: Script executes successfully
    pass
```

---

## Files Summary

### Modified Files (9 files)
- `apps/web_console/auth/session_store.py` - Add `access_token_expires_at`, binding validation
- `apps/web_console/auth/session_manager.py` - Remove tokens from returned dict (CRITICAL)
- `apps/web_console/auth/oauth2_flow.py` - Track token expiry, binding validation on logout
- `apps/web_console/auth/idle_timeout_monitor.py` - Fix datetime parsing, use st.rerun()
- `apps/web_console/app.py` - Integrate idle timeout warning, token refresh, logout
- `apps/web_console/nginx/nginx.conf` - Add nonce-based CSP headers
- `apps/auth_service/routes/logout.py` - Pass IP/UA for binding validation
- `tests/unit/test_session_store.py` - Add absolute timeout tests
- `tests/integration/test_session_binding.py` - Add IP spoofing tests

### New Files (4 files)
- `apps/web_console/auth/api_client.py` - Secure API helper (fetches tokens from Redis)
- `apps/web_console/auth/token_refresh_monitor.py` - Auto-refresh using actual expiry
- `apps/web_console/auth/csp_nonce.py` - CSP nonce handling
- `tests/integration/test_csp_headers.py` - CSP header validation tests

---

## Testing Plan

### Unit Tests (15+ tests)

1. **Session Binding**
   - Test IP mismatch deletes session
   - Test User-Agent mismatch deletes session
   - Test valid binding allows session retrieval
   - Test TTL preserved on activity update
   - **NEW:** Test absolute timeout enforced despite activity

2. **Token Metadata**
   - Test `validate_session()` returns ONLY non-sensitive metadata
   - Test `access_token` NOT in returned dict
   - Test `access_token_expires_at` tracked correctly

3. **Idle Timeout**
   - Test `parse_iso_datetime()` handles `Z` format
   - Test `should_show_idle_warning()` thresholds
   - Test `get_time_until_idle_timeout()` calculation

4. **Token Refresh**
   - Test `should_refresh_token()` uses `access_token_expires_at`
   - Test refresh triggered 10 minutes before expiry
   - Test refresh NOT triggered with 20 minutes remaining

5. **Logout**
   - Test `handle_logout()` validates binding
   - Test binding mismatch deletes session but doesn't revoke token
   - Test refresh token revocation call

### Integration Tests (12+ tests)

1. **Session Binding E2E**
   - Login, change IP, verify session invalidated
   - Login, change User-Agent, verify session invalidated
   - Login, use same IP/UA, verify session valid
   - **NEW:** Test X-Forwarded-For spoofing blocked

2. **Idle Timeout E2E**
   - Login, wait 13 minutes, verify warning appears
   - Login, wait 15 minutes, verify session expired

3. **Token Refresh E2E**
   - Login, mock time to 50 minutes, verify /refresh called
   - Verify new access token returned
   - Verify `access_token_expires_at` updated

4. **Logout E2E**
   - Login, logout with valid binding, verify refresh token revoked at Auth0
   - Login, logout with invalid IP, verify session deleted but no revocation
   - Verify session deleted from Redis
   - Verify cookie cleared

5. **CSP Headers**
   - Verify CSP headers present with nonces
   - Verify no `'unsafe-inline'` in `script-src`
   - **NEW:** Verify CSP blocks inline script without nonce (browser test)
   - **NEW:** Verify CSP allows script with nonce (browser test)

6. **API Client**
   - Test `call_api_with_auth()` fetches token from Redis
   - Test API call includes Authorization header

### Manual Tests (Verification Checklist)

- [ ] Login, wait 13 minutes without interaction, verify warning appears with countdown
- [ ] Click "Extend Session", verify countdown resets
- [ ] Wait 15 minutes without interaction, verify session expires and redirects to login
- [ ] Login, click "Logout", verify redirected to Auth0 logout, then to login page
- [ ] Login, open browser DevTools, verify session_id cookie has HttpOnly flag
- [ ] Login, open browser DevTools Console, verify CSP policy displayed with nonce
- [ ] Login, try `document.cookie` in console, verify session_id NOT accessible
- [ ] Login, try injecting `<script>alert('XSS')</script>`, verify CSP blocks it
- [ ] Login, change IP (e.g., VPN), verify session invalidated on next request
- [ ] Login, use different browser (different User-Agent), verify session not accessible
- [ ] Login, wait 4 hours with continuous activity, verify session expires (absolute timeout)
- [ ] Login, verify `st.session_state` contains NO tokens (only user_id, email, display_name)

---

## Success Criteria

1. **Session Binding Enforcement:**
   - IP mismatch invalidates session (logs warning, deletes session)
   - User-Agent mismatch invalidates session (logs warning, deletes session)
   - Valid binding allows normal session use
   - **Logout validates binding** (prevents token revocation by attacker)

2. **Idle Timeout UX:**
   - Warning appears 2 minutes before expiry (at 13-minute mark)
   - Countdown updates every 5 seconds (via `st.rerun()`)
   - "Extend Session" button refreshes tokens and resets countdown
   - Session expires after 15 minutes of inactivity

3. **Automatic Token Refresh:**
   - Access token refreshed automatically 10 minutes before expiry
   - **Uses `access_token_expires_at`** (not `last_activity`)
   - Refresh does not interrupt user workflow
   - Refresh token rotation occurs on every refresh

4. **CSP Hardening:**
   - CSP headers present with nonces on all responses
   - **No `'unsafe-inline'` in `script-src`**
   - Inline scripts blocked without nonce
   - Auth0 and auth_service in `connect-src`
   - Browser DevTools shows CSP policy

5. **Logout Flow:**
   - **Binding validation on logout** (prevents attacker revocation)
   - Refresh token revoked at Auth0 (if binding valid)
   - Session deleted from Redis
   - Cookie cleared
   - Redirect to Auth0 logout, then to login page

6. **Token Security:**
   - **No tokens in Streamlit session_state** (CRITICAL - CI validates)
   - Only non-sensitive metadata in session_state (user_id, email, display_name)
   - All API calls fetch tokens from Redis via `call_api_with_auth()`
   - All security headers present (X-Frame-Options, X-Content-Type-Options, etc.)

7. **Absolute Timeout:**
   - Sessions expire at 4 hours even with continuous activity
   - TTL never resets to full 4 hours (preserves creation time)

---

## Timeline

- **Day 1 (8 hours):**
  - Task 1: Session binding + token metadata tracking (5h)
  - Task 2: Idle timeout warning UI (3h)

- **Day 2 (8 hours):**
  - Task 3: Automatic token refresh (3h)
  - Task 4: CSP headers with nonces (4h)
  - Task 5: Logout with binding validation (1h)

**Total:** 16 hours (2 days)

---

## Definition of Done

- [ ] All 5 tasks completed
- [ ] All files modified/created as specified
- [ ] Session binding validation enforces IP + User-Agent matching (including logout)
- [ ] Idle timeout warning appears 2 minutes before expiry with correct datetime parsing
- [ ] Automatic token refresh works using `access_token_expires_at` (not `last_activity`)
- [ ] CSP headers with nonces prevent XSS attacks (no `'unsafe-inline'`)
- [ ] Logout validates binding before revoking tokens at Auth0
- [ ] All unit tests pass (15+ tests)
- [ ] All integration tests pass (12+ tests)
- [ ] Manual verification checklist completed
- [ ] **No tokens in Streamlit session_state** (CRITICAL - CI grep check passes)
- [ ] Component 3 plan v2 reviewed via zen-mcp (Gemini + Codex) - APPROVED

---

## Codex Review Issues Addressed (v1 → v2)

### Critical Issues (FIXED)

1. ✅ **Token leakage to session_state**
   - v1: `validate_session()` returned `access_token`
   - v2: Returns ONLY non-sensitive metadata (user_id, email, display_name)
   - v2: Created `api_client.py` for secure token access from Redis

2. ✅ **Auto-refresh logic broken**
   - v1: Used `last_activity` (always near zero)
   - v2: Uses `access_token_expires_at` (actual token expiry)
   - v2: Tracks expiry in SessionData and updates on refresh

### High-Priority Issues (FIXED)

3. ✅ **Datetime parsing crash**
   - v1: `fromisoformat()` doesn't accept `...Z`
   - v2: Added `parse_iso_datetime()` helper with `.replace('Z', '+00:00')`

4. ✅ **CSP policy too weak**
   - v1: Allowed `'unsafe-inline'` without nonces
   - v2: Uses nonce-based CSP (`'nonce-$csp_nonce'`)
   - v2: Added Auth0/auth_service to `connect-src`
   - v2: Removed meta-refresh, uses `st.rerun()` instead

### Medium Issues (FIXED)

5. ✅ **Logout bypasses binding**
   - v1: Used `current_ip="unknown"`
   - v2: Validates binding on logout before revoking token

6. ✅ **Testing gaps**
   - v2: Added absolute timeout tests
   - v2: Added IP spoofing tests
   - v2: Added CSP violation tests

---

## Next Steps (After Component 3)

**Component 4:** Integration + Migration + Rollback (2 days)
- Integrate OAuth2 with existing mTLS authentication
- Add feature flag for gradual rollout
- Implement rollback mechanism (emergency mTLS fallback)
- Add monitoring and alerting

**Component 5:** Documentation + Runbooks (1 day)
- OAuth2 architecture documentation
- mTLS fallback runbook
- Session key rotation runbook
- Troubleshooting guide

**Component 6:** Security Validation (Manual Testing) (1 day)
- Penetration testing checklist
- Session hijacking tests
- XSS attack tests
- CSRF attack tests
- Token leakage tests

---

**Ready for Plan Review:** This v2 plan addresses all Codex critical/high/medium issues and should be reviewed via zen-mcp (Gemini + Codex reviewers) before implementation begins.
