# P2T3 Phase 3: Component 4 - Streamlit UI Integration

**Component:** 4 of 7
**Status:** IN PROGRESS
**Duration:** 1.5 days (12 hours estimated)
**Dependencies:** Components 1, 2, 3 ✅

---

## Overview

Integrate OAuth2/OIDC authentication with Streamlit web console UI, providing seamless login/logout flows, session status indicators, and protected page access control.

**Key Integration Points:**
- Existing `_oauth2_auth()` in `apps/web_console/auth/__init__.py` (lines 401-547)
- Current app structure in `apps/web_console/app.py` (streamlit multipage app)
- Session validation helpers from Component 3 (`session_manager.py`, `idle_timeout_monitor.py`)

---

## Architecture

### Current OAuth2 Flow (Partial Implementation)

**Existing (`_oauth2_auth()`):**
```python
# apps/web_console/auth/__init__.py:401-547
def _oauth2_auth() -> bool:
    # 1. Check if authenticated (cached in session_state)
    # 2. Get session_id cookie via get_session_cookie()
    # 3. If no cookie → redirect to /login (FastAPI endpoint)
    # 4. If cookie exists → validate via RedisSessionStore
    # 5. If valid → cache user_info in session_state
    # 6. If invalid/expired → redirect to /login
```

**Missing Pieces:**
- ❌ Login page UI (currently uses meta-refresh redirect)
- ❌ Logout handler (line 1206: calls /logout via meta-refresh)
- ❌ Session status UI (idle timeout warnings)
- ❌ Protected page decorators
- ❌ User profile display
- ❌ Token refresh UI integration

### Proposed UI Flow

```
User visits /          →  Streamlit app.py
                           ↓
                      check_password() (auth/__init__.py)
                           ↓
                      _oauth2_auth()
                           ↓
                      ┌─ No session_id cookie?
                      │  → Redirect to /pages/login.py
                      │     └─ Displays "Login with Auth0" button
                      │        └─ Links to FastAPI /login endpoint
                      │           └─ FastAPI redirects to Auth0
                      │              └─ User authenticates
                      │                 └─ Auth0 redirects to /callback
                      │                    └─ FastAPI sets session_id cookie
                      │                       └─ FastAPI redirects to /
                      │
                      └─ Has session_id cookie?
                         → Validate session (RedisSessionStore)
                            ├─ Valid → Allow access
                            │          └─ Show session status UI
                            │             └─ Auto-refresh monitoring
                            └─ Invalid/Expired → Redirect to login
```

---

## Implementation Plan

### Deliverable 1: Login Page (2 hours)

**File:** `apps/web_console/pages/login.py`

```python
"""OAuth2 login page for Streamlit web console.

Displays Auth0 login button and handles OAuth2 redirect flow.
"""

import os
import streamlit as st

def main():
    st.set_page_config(page_title="Login - Trading Platform", page_icon="🔐")

    st.title("Trading Platform - Login")
    st.markdown("### Secure Access via Auth0")

    # Get login URL from environment
    login_url = os.getenv("OAUTH2_LOGIN_URL", "/login")

    st.info(
        "🔒 This application uses **OAuth2/OIDC** authentication via Auth0.\n\n"
        "Click the button below to log in securely."
    )

    # Login button (links to FastAPI /login endpoint)
    st.markdown(
        f'<a href="{login_url}"><button style="background-color:#4CAF50;color:white;'
        'padding:15px 32px;text-align:center;font-size:16px;border:none;'
        'border-radius:4px;cursor:pointer;">Login with Auth0</button></a>',
        unsafe_allow_html=True
    )

    # Development info
    if os.getenv("ENVIRONMENT", "production") == "development":
        st.divider()
        st.markdown("**Development Info:**")
        st.code(f"Login URL: {login_url}")
        st.caption("Auth0 will redirect back to /callback after authentication")

if __name__ == "__main__":
    main()
```

**Changes to `auth/__init__.py`:**
```python
# Replace meta-refresh redirect (line 453-456) with proper page navigation
if not session_id:
    st.title("Trading Platform - Login Required")
    st.info("You are not authenticated. Please log in to continue.")

    # Use st.switch_page for proper Streamlit navigation
    # This is better than meta-refresh (CSP-friendly, no HTML injection)
    st.switch_page("pages/login.py")  # Streamlit 1.30+ feature
    st.stop()
```

**Testing:**
- [ ] Access / without session_id cookie → redirects to login page
- [ ] Login page displays Auth0 button
- [ ] Click button → redirects to FastAPI /login
- [ ] After Auth0 authentication → callback sets cookie → redirects to /
- [ ] Session validation succeeds → dashboard displayed

---

### Deliverable 2: Protected Page Decorator (2 hours)

**File:** `apps/web_console/auth/streamlit_helpers.py`

```python
"""Streamlit authentication helpers for protected pages.

Provides decorators and utilities for OAuth2 session validation.
"""

import functools
import logging
from typing import Any, Callable

import streamlit as st

from apps.web_console.auth import check_password

logger = logging.getLogger(__name__)


def requires_auth(func: Callable[..., Any]) -> Callable[..., Any]:
    """
    Decorator for pages requiring OAuth2 authentication.

    Usage:
        @requires_auth
        def main():
            st.title("Protected Dashboard")
            ...

    If user is not authenticated, redirects to login page via check_password().
    """
    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        # Check authentication (handles redirect to login if needed)
        if not check_password():
            st.stop()
            return None

        # User authenticated - proceed with page rendering
        return func(*args, **kwargs)

    return wrapper


def get_user_info() -> dict[str, str]:
    """
    Get current authenticated user information.

    Returns:
        dict with keys: username, email, user_id, auth_method, session_id

    Raises:
        RuntimeError: If called before authentication
    """
    if not st.session_state.get("authenticated", False):
        raise RuntimeError("get_user_info() called before authentication")

    return {
        "username": st.session_state.get("username", "unknown"),
        "email": st.session_state.get("username", "unknown"),  # Username is email for OAuth2
        "user_id": st.session_state.get("user_id", "unknown"),
        "auth_method": st.session_state.get("auth_method", "unknown"),
        "session_id": st.session_state.get("session_id", "unknown"),
    }
```

**Changes to `app.py`:**
```python
# Add decorator to main() function (line 690)
from apps.web_console.auth.streamlit_helpers import requires_auth

@requires_auth  # NEW: Protect all pages with OAuth2
def main() -> None:
    """Main application entry point."""
    # Remove old check_password() call (line 693) - decorator handles it
    # OLD: if not auth.check_password(): st.stop()

    # Sidebar (line 697+)
    with st.sidebar:
        st.title("Navigation")

        # User info (now using OAuth2 session data)
        user_info = auth.get_current_user()
        st.markdown(f"**User:** {user_info['username']}")
        st.markdown(f"**Auth:** {user_info['auth_method']}")

        # Session status indicator (NEW - see Deliverable 3)
        render_session_status()

        if st.button("Logout", use_container_width=True):
            auth.logout()

        # ... rest of sidebar
```

**Testing:**
- [ ] Access protected page without auth → redirects to login
- [ ] Access protected page with valid session → allowed
- [ ] Access protected page with expired session → redirects to login
- [ ] `get_user_info()` returns correct OAuth2 user data

---

### Deliverable 3: Session Status UI Widget (3 hours)

**File:** `apps/web_console/components/session_status.py`

```python
"""Session status UI component for OAuth2 timeout warnings.

Displays idle timeout countdown and provides extend session button.
"""

import logging
import streamlit as st

from apps.web_console.auth.idle_timeout_monitor import (
    get_time_until_idle_timeout,
    should_show_idle_warning,
    extend_session_sync,
)

logger = logging.getLogger(__name__)


def render_session_status() -> None:
    """
    Render session status indicator in sidebar.

    Shows:
    - Idle timeout countdown (when <2 minutes remaining)
    - Extend session button
    - Session expiry time

    Uses idle_timeout_monitor from Component 3 for timeout logic.
    """
    # Get session metadata
    user_info = st.session_state.get("user_info", {})
    last_activity_str = user_info.get("last_activity")

    if not last_activity_str:
        # OAuth2 session not fully initialized yet
        return

    # Check if idle warning should be shown
    if should_show_idle_warning(last_activity_str):
        time_remaining = get_time_until_idle_timeout(last_activity_str)
        minutes_remaining = int(time_remaining.total_seconds() / 60)
        seconds_remaining = int(time_remaining.total_seconds() % 60)

        st.warning(
            f"⏰ **Session expiring soon**\n\n"
            f"{minutes_remaining}m {seconds_remaining}s remaining\n\n"
            "Interact with the page or click below to extend."
        )

        if st.button("Extend Session", use_container_width=True, type="primary"):
            with st.spinner("Extending session..."):
                extend_session_sync()
    else:
        # Session healthy - show subtle status
        time_remaining = get_time_until_idle_timeout(last_activity_str)
        minutes_remaining = int(time_remaining.total_seconds() / 60)

        st.caption(f"✅ Session active ({minutes_remaining}m remaining)")
```

**Changes to `auth/__init__.py` (`_oauth2_auth()`):**

**CRITICAL SECURITY FIX (Codex sec-01):** Remove line 539 that stores access_token in session_state

```python
# Store user_info with metadata for timeout monitoring (line 530-545)
# Cache user info in Streamlit session_state
now = datetime.now()
st.session_state["authenticated"] = True
st.session_state["username"] = user_info["email"]  # Use email as display name
st.session_state["auth_method"] = "oauth2"
st.session_state["login_time"] = now  # Use now as login time for timeout tracking
st.session_state["last_activity"] = now
st.session_state["session_id"] = session_id
st.session_state["user_id"] = user_info["user_id"]
# REMOVED (LINE 539): st.session_state["access_token"] = user_info["access_token"]
# ^^^ CRITICAL: This line MUST be deleted to comply with Component 3 security

# NEW: Store ONLY non-sensitive metadata for session status UI
# CRITICAL SECURITY (Component 3 - Codex Critical #1):
# NEVER store access_token, refresh_token, or id_token in session_state!
# Tokens remain in encrypted Redis and are fetched via api_client.py when needed.
st.session_state["user_info"] = {
    "email": user_info["email"],
    "user_id": user_info["user_id"],
    "display_name": user_info.get("display_name", user_info["email"].split("@")[0]),
    "created_at": user_info.get("created_at", now.isoformat()),
    "last_activity": user_info.get("last_activity", now.isoformat()),
    "access_token_expires_at": user_info.get("access_token_expires_at"),
    # NEVER include: access_token, refresh_token, id_token
}
```

**Testing:**
- [ ] Session status shows green when >2 minutes remaining
- [ ] Session status shows warning when <2 minutes remaining
- [ ] Countdown updates every 5 seconds (via st.rerun() in idle_timeout_monitor)
- [ ] Extend button triggers /refresh endpoint successfully
- [ ] Session extended → countdown resets to 15 minutes

---

### Deliverable 4: Logout Handler with Confirmation (2 hours)

**Changes to `auth/__init__.py` (`logout()`):**
```python
def logout() -> None:
    """Logout current user and clear session.

    For OAuth2 mode:
    - Shows confirmation dialog (via session_state flag)
    - Calls FastAPI /logout endpoint (clears HttpOnly cookie)
    - Revokes refresh token at Auth0
    - Redirects to Auth0 logout URL
    - Clears Streamlit session_state
    """
    username = st.session_state.get("username", "unknown")
    auth_method = st.session_state.get("auth_method", "unknown")
    session_id = st.session_state.get("session_id")

    # ... existing mTLS handling (lines 1169-1188) ...

    # For OAuth2, call FastAPI /logout endpoint
    if auth_method == "oauth2":
        import os
        import requests

        logout_url = os.getenv("OAUTH2_LOGOUT_URL", "/logout")
        auth_service_url = os.getenv("AUTH_SERVICE_URL", "http://auth_service:8000")

        try:
            # Call FastAPI /logout (revokes token, clears cookie)
            # Pass session_id cookie for server-side cleanup
            response = requests.post(
                f"{auth_service_url}/logout",
                cookies={"session_id": session_id},
                timeout=5.0
            )
            response.raise_for_status()
            logger.info(f"OAuth2 logout successful for {username}")
        except Exception as e:
            # Log error but continue with client-side cleanup
            logger.error(f"OAuth2 logout API call failed: {e}")

        # Audit logout
        details = {
            "timestamp": datetime.now().isoformat(),
            "auth_method": auth_method,
        }
        audit_to_database(
            user_id=username,
            action="logout",
            details=details,
            session_id=session_id,
        )

        # Clear session state
        st.session_state.clear()

        # Redirect to Auth0 logout (via FastAPI /logout redirect)
        st.markdown(
            f'<meta http-equiv="refresh" content="0; url={logout_url}">',
            unsafe_allow_html=True,
        )
        st.markdown(f"Click here if not redirected automatically: {logout_url}")
        st.stop()
    else:
        # Other auth methods (mTLS, dev) - existing logic
        st.session_state.clear()
        st.rerun()
```

**Changes to `app.py` (sidebar logout button):**
```python
# Add confirmation dialog (line 705)
if "logout_confirmation_pending" not in st.session_state:
    st.session_state["logout_confirmation_pending"] = False

if st.session_state.get("logout_confirmation_pending", False):
    st.warning("⚠️ **Confirm Logout**")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Yes, Logout", type="primary", use_container_width=True):
            st.session_state["logout_confirmation_pending"] = False
            auth.logout()
    with col2:
        if st.button("Cancel", use_container_width=True):
            st.session_state["logout_confirmation_pending"] = False
            st.rerun()
else:
    if st.button("Logout", use_container_width=True):
        st.session_state["logout_confirmation_pending"] = True
        st.rerun()
```

**Testing:**
- [ ] Click logout → shows confirmation dialog
- [ ] Click "Yes, Logout" → calls /logout → redirects to Auth0 logout
- [ ] Click "Cancel" → closes dialog, stays logged in
- [ ] After logout → session_id cookie cleared
- [ ] After logout → cannot access protected pages without re-login

---

### Deliverable 5: User Profile Display (1 hour)

**Changes to `app.py` (sidebar user info):**
```python
# Enhanced user info section (line 701-703)
with st.sidebar:
    st.title("Navigation")

    # User profile card (NEW)
    user_info = auth.get_current_user()
    with st.expander("👤 User Profile", expanded=False):
        st.markdown(f"**Email:** {user_info['username']}")
        st.markdown(f"**User ID:** {user_info.get('user_id', 'N/A')[:12]}...")
        st.markdown(f"**Auth Method:** {user_info['auth_method']}")

        # Session metadata
        login_time = st.session_state.get("login_time")
        if login_time:
            st.markdown(f"**Login Time:** {login_time.strftime('%Y-%m-%d %H:%M:%S')}")

        # Session creation time (from non-sensitive user_info)
        user_metadata = st.session_state.get("user_info", {})
        created_at = user_metadata.get("created_at")
        if created_at:
            from apps.web_console.auth.idle_timeout_monitor import parse_iso_datetime

            created_dt = parse_iso_datetime(created_at)
            st.markdown(f"**Session Created:** {created_dt.strftime('%Y-%m-%d %H:%M:%S')}")

    # Session status (from Deliverable 3)
    # NOTE: Token expiry countdown is shown in render_session_status() below
    render_session_status()

    # Logout button (from Deliverable 4)
    # ... logout confirmation logic ...
```

**Testing:**
- [ ] User profile expander shows email, user ID, auth method
- [ ] Login time displays correctly
- [ ] Session creation time displays correctly
- [ ] Profile data matches non-sensitive session metadata (NO TOKENS)

---

### Deliverable 6: Token Refresh UI Integration (2 hours)

**Background Token Refresh:**

Component 3 already implements `token_refresh_monitor.py` which handles automatic token refresh 10 minutes before expiry. We need to integrate this with Streamlit UI.

**Changes to `app.py`:**
```python
# Add background token refresh monitoring (after auth check)
from apps.web_console.auth.token_refresh_monitor import start_token_refresh_monitor

@requires_auth
def main() -> None:
    """Main application entry point."""

    # Start background token refresh (only if OAuth2)
    if st.session_state.get("auth_method") == "oauth2":
        # Initialize refresh monitor in session_state (runs once per session)
        if "token_refresh_monitor_started" not in st.session_state:
            start_token_refresh_monitor()
            st.session_state["token_refresh_monitor_started"] = True

    # Sidebar
    # ... rest of application ...
```

**Add refresh status indicator to session status UI:**
```python
# In components/session_status.py
def render_session_status() -> None:
    # ... existing idle timeout logic ...

    # Show token refresh status (if OAuth2)
    user_metadata = st.session_state.get("user_info", {})
    token_expiry = user_metadata.get("access_token_expires_at")

    if token_expiry:
        from datetime import datetime, UTC
        from apps.web_console.auth.idle_timeout_monitor import parse_iso_datetime

        expiry_dt = parse_iso_datetime(token_expiry)
        now = datetime.now(UTC)
        time_until_expiry = expiry_dt - now
        minutes_until_expiry = int(time_until_expiry.total_seconds() / 60)

        # Show refresh warning at 10 minutes (when auto-refresh triggers)
        if minutes_until_expiry <= 10:
            st.info(f"🔄 Token refresh in progress... ({minutes_until_expiry}m until expiry)")
        else:
            st.caption(f"🔑 Token valid ({minutes_until_expiry}m)")
```

**Testing:**
- [ ] Token refresh monitor starts when OAuth2 session begins
- [ ] Token refresh triggers automatically 10 minutes before expiry
- [ ] UI shows refresh indicator when refresh is in progress
- [ ] After refresh → access_token_expires_at updated in session_state
- [ ] Session remains active across token refresh

---

## Files to Create

1. `apps/web_console/pages/login.py` - OAuth2 login page with Auth0 button
2. `apps/web_console/auth/streamlit_helpers.py` - Protected page decorators
3. `apps/web_console/components/session_status.py` - Session status UI widget
4. `apps/web_console/components/__init__.py` - Package marker

## Files to Modify

1. `apps/web_console/auth/__init__.py`
   - `_oauth2_auth()`: Replace meta-refresh with `st.switch_page()` (line 453-456)
   - `_oauth2_auth()`: Store user_info dict for timeout monitoring (line 530-545)
   - `logout()`: Add OAuth2 logout API call + confirmation (line 1203-1214)

2. `apps/web_console/app.py`
   - Import `requires_auth` decorator
   - Add `@requires_auth` to `main()` function (line 690)
   - Remove duplicate `check_password()` call (line 693)
   - Add session status UI to sidebar (after user info)
   - Add logout confirmation dialog (line 705)
   - Add enhanced user profile display (line 701-703)
   - Add token refresh monitor initialization

---

## Testing Strategy

### Unit Tests

**File:** `tests/apps/web_console/auth/test_streamlit_helpers.py`
```python
def test_requires_auth_decorator_allows_authenticated_user():
    """Test @requires_auth allows authenticated users."""

def test_requires_auth_decorator_blocks_unauthenticated_user():
    """Test @requires_auth blocks unauthenticated users."""

def test_get_user_info_returns_oauth2_metadata():
    """Test get_user_info() returns OAuth2 user data."""

def test_get_user_info_raises_before_authentication():
    """Test get_user_info() raises RuntimeError before auth."""

def test_tokens_never_stored_in_session_state(monkeypatch, mock_redis_session_store):
    """CRITICAL REGRESSION TEST (Codex test-01): Ensure tokens never enter session_state.

    This test ensures Component 3's security requirement is not violated:
    access_token, refresh_token, and id_token must NEVER be stored in
    st.session_state. They must remain in encrypted Redis only.

    Tests both successful auth and session validation flows.
    """
    # Mock validate_session to return user_info with tokens (simulating Component 3)
    # Component 3's validate_session strips tokens before returning, but we want
    # to verify that even if tokens were present, they don't enter session_state
    mock_user_info = {
        "user_id": "auth0|12345",
        "email": "test@example.com",
        "display_name": "test",
        "created_at": "2025-11-25T10:00:00Z",
        "last_activity": "2025-11-25T10:00:00Z",
        "access_token_expires_at": "2025-11-25T11:00:00Z",
        # NOTE: Component 3's validate_session NEVER returns these,
        # but we test defensive behavior in case it changes
    }

    async def mock_validate(session_id, session_store, client_ip, user_agent):
        return mock_user_info

    # Mock the validate_session function
    monkeypatch.setattr(
        "apps.web_console.auth.session_manager.validate_session",
        mock_validate
    )

    # Mock get_session_cookie to return a session ID
    monkeypatch.setattr(
        "apps.web_console.auth.session_manager.get_session_cookie",
        lambda: "test-session-id"
    )

    # Simulate OAuth2 authentication by calling _oauth2_auth()
    # This should populate st.session_state but NEVER store tokens
    from apps.web_console.auth import _oauth2_auth

    # Execute auth flow (mocked backend calls)
    authenticated = _oauth2_auth()
    assert authenticated, "Auth should succeed with mocked backend"

    # CRITICAL ASSERTIONS: Verify NO tokens in session_state
    forbidden_keys = ["access_token", "refresh_token", "id_token"]

    for key in forbidden_keys:
        assert key not in st.session_state, (
            f"CRITICAL SECURITY VIOLATION: {key} found in st.session_state! "
            f"Tokens must remain in encrypted Redis. See Component 3 security requirements."
        )

    # Also verify user_info dict doesn't contain tokens
    user_info = st.session_state.get("user_info", {})
    for key in forbidden_keys:
        assert key not in user_info, (
            f"CRITICAL SECURITY VIOLATION: {key} found in user_info dict! "
            f"Only non-sensitive metadata allowed."
        )

    # Verify non-sensitive metadata IS present (positive test)
    assert "email" in user_info
    assert "user_id" in user_info
    assert "display_name" in user_info
```

**File:** `tests/apps/web_console/components/test_session_status.py`
```python
def test_render_session_status_shows_warning_when_expiring():
    """Test session status shows warning <2 minutes."""

def test_render_session_status_shows_healthy_when_active():
    """Test session status shows healthy >2 minutes."""

def test_extend_session_button_calls_refresh_endpoint():
    """Test extend button triggers /refresh API call."""
```

### E2E Tests

**File:** `tests/integration/test_oauth2_ui_flow.py`
```python
def test_login_flow_redirects_to_auth0():
    """Test login page redirects to Auth0."""

def test_callback_sets_cookie_and_redirects_to_dashboard():
    """Test /callback sets session cookie and redirects."""

def test_protected_page_requires_valid_session():
    """Test protected pages block unauthenticated access."""

def test_logout_clears_cookie_and_redirects_to_auth0():
    """Test logout clears session and redirects."""

def test_session_timeout_warning_displays_correctly():
    """Test idle timeout warning shows at <2 minutes."""

def test_token_refresh_extends_session_automatically():
    """Test background token refresh works."""
```

---

## Security Considerations

### Session Validation
- ✅ Session binding (IP + User-Agent) validated on every request (Component 3)
- ✅ HttpOnly cookies prevent XSS token theft (Component 2)
- ✅ Secure + SameSite=Lax flags prevent CSRF (Component 2)

### Timeout Enforcement
- ✅ 15-minute idle timeout (Component 3: idle_timeout_monitor)
- ✅ 4-hour absolute timeout (Component 3: session_store)
- ✅ Timeout warnings 2 minutes before expiry (Component 3)

### Token Security
- ✅ Tokens never stored in session_state (Component 3: api_client)
- ✅ Access via server-side API only (Component 3)
- ✅ Automatic refresh 10 minutes before expiry (Component 3: token_refresh_monitor)

### Logout Security
- ✅ Confirmation dialog prevents accidental logout
- ✅ Server-side token revocation at Auth0 (Component 2: oauth2_flow)
- ✅ HttpOnly cookie cleared by FastAPI /logout
- ✅ Streamlit session_state cleared completely

---

## Dependencies

### Python Packages (Already Installed)
- `streamlit` >= 1.30 (for `st.switch_page()`)
- `requests` (for logout API call)
- `redis.asyncio` (for session validation)

### Environment Variables (From Component 2)
- `OAUTH2_LOGIN_URL` - FastAPI /login endpoint (default: `/login`)
- `OAUTH2_LOGOUT_URL` - FastAPI /logout endpoint (default: `/logout`)
- `AUTH_SERVICE_URL` - FastAPI auth service base URL (default: `http://auth_service:8000`)
- `SESSION_ENCRYPTION_KEY` - AES-256 key for session encryption (base64)
- `REDIS_HOST`, `REDIS_PORT` - Redis connection

---

## Success Criteria

- [ ] Login page displays Auth0 button and redirects correctly
- [ ] Protected pages block unauthenticated access
- [ ] Session status UI shows idle timeout warnings
- [ ] Logout confirmation prevents accidental logout
- [ ] User profile displays OAuth2 metadata correctly
- [ ] Token refresh monitor runs in background
- [ ] All E2E tests pass
- [ ] No regression in existing dashboard functionality

---

## References

- **Component 2 Plan:** `docs/TASKS/P2T3-Phase3_Component2_Plan_v3.md` (OAuth2 flow implementation)
- **Component 3 Plan:** `docs/TASKS/P2T3-Phase3_Component3_Plan_v2.md` (Session management + auto-refresh)
- **Session Schema:** `docs/ARCHITECTURE/redis-session-schema.md` (Redis session structure)
- **ADR-015:** `docs/ADRs/ADR-015-auth0-idp-selection.md` (Auth0 IdP rationale)

---

**Last Updated:** 2025-11-25
**Author:** Development Team (Component 4 Implementation)
