# P2T3 Phase 3 - Component 5: CSP Hardening + Nginx Integration

**Status:** NOT STARTED
**Component:** 5 of 7
**Estimated Duration:** 2-2.5 days (14-18 hours)
**Estimate Update (Codex Iteration 2):**
- Original: 12-14 hours
- Added: Template integration (+2h), CSP rationale docs (+1h), monitoring plan (+1h)
- New Total: 14-18 hours
**Dependencies:**
- Component 1 (OAuth2 Config & IdP Setup) ✅ COMPLETED
- Component 2 (OAuth2 Authorization Flow with PKCE) ✅ COMPLETED
- Component 3 (Session Management + UX + Auto-Refresh) ✅ COMPLETED
- Component 4 (Streamlit UI Integration) ✅ COMPLETED

**References:**
- Parent Task: `docs/TASKS/P2T3-Phase3_TASK.md`
- Planning Summary: `docs/TASKS/P2T3_Phase3_PLANNING_SUMMARY.md`
- Component 2 Plan: `docs/TASKS/P2T3-Phase3_Component2_Plan_v3.md`
- Component 3 Plan: `docs/TASKS/P2T3-Phase3_Component3_Plan_v2.md`
- Component 4 Plan: `docs/TASKS/P2T3-Phase3_Component4_Plan.md`
- **OAuth2 Nginx Config Template:** `apps/web_console/nginx/nginx-oauth2.conf.template` ⚠️ (Codex Iteration 5 Issue #1)
- **OAuth2 Nginx Entrypoint:** `apps/web_console/nginx/entrypoint.sh` (Codex Iteration 5 Issue #1)
- **mTLS Nginx Config:** `apps/web_console/nginx/nginx.conf` (DO NOT MODIFY)
- ADR-015: Auth0 IdP Selection

---

## ⚠️ CRITICAL: Nginx Config File Distinction (Codex Iteration 5 Issue #1)

**This component creates/modifies:**
- **CREATE:** `apps/web_console/nginx/nginx-oauth2.conf.template` (source template with envsubst placeholders)
- **CREATE:** `apps/web_console/nginx/entrypoint.sh` (runs envsubst to generate final config)
- **GENERATED:** `nginx-oauth2.conf` (created at container start, not edited manually)

**DO NOT MODIFY:** `apps/web_console/nginx/nginx.conf` (mTLS profile - Component 3 of P2T3 Phase 2)

**Why two authentication profiles?**
- `nginx.conf` → mTLS authentication profile (client certificates)
- `nginx-oauth2.conf.template` → OAuth2/OIDC authentication profile (Auth0), processed by envsubst
- Different security models, different configurations
- Both mounted in docker-compose via profiles

**File Workflow (Codex Iteration 5 Issue #1):**
1. Template file (`nginx-oauth2.conf.template`) contains `${CSP_REPORT_ONLY}` placeholders
2. Docker entrypoint (`entrypoint.sh`) runs `envsubst` at container start
3. `envsubst` generates final `nginx-oauth2.conf` from template
4. Nginx loads the generated config (not the template)

**Docker Compose Profiles:**
```yaml
# mTLS profile (Phase 2 Component 3)
docker-compose --profile mtls up -d  # Uses nginx.conf

# OAuth2 profile (Phase 3 all components)
docker-compose --profile oauth2 up -d  # Uses nginx-oauth2.conf (generated from template)
```

**ALL references in this plan target `nginx-oauth2.conf.template` exclusively (source template, not generated file).**

---

## Overview

Implement production-grade security hardening for the OAuth2/OIDC authentication system by adding Content Security Policy (CSP) headers with nonce-based script execution, Nginx routing for auth endpoints, trusted proxy IP validation to prevent header spoofing, and CSP violation reporting for security monitoring.

**Key Goals:**
1. **CSP Headers**: Implement nonce-based `script-src` to prevent inline script attacks
2. **CSP Coverage**: Extend CSP to both auth_service AND Streamlit responses
3. **Nginx Integration**: Route `/login`, `/callback`, `/refresh`, `/logout` through Nginx to auth_service
4. **Nginx Security**: Configure `real_ip` directives for trusted proxy validation
5. **Proxy Security**: Validate trusted proxy IPs to prevent X-Forwarded-For spoofing
6. **Monitoring**: Log CSP violations for security monitoring with rate limiting
7. **Production Readiness**: Ensure all auth endpoints work correctly behind Nginx reverse proxy

**Success Criteria (Updated - Codex Iteration 2):**
- ✅ CSP headers block inline scripts without valid nonce (auth_service ONLY)
- ⚠️ CSP headers allow nonce-based scripts (auth_service ONLY - Streamlit requires unsafe-inline)
- ✅ CSP headers cover BOTH auth_service AND Streamlit pages
- ✅ CSP allowlists include Auth0 CDN + Streamlit requirements (wss://, blob:, data:)
- ✅ CSP allowlist rationale documented (WHY each directive needed)
- ✅ Streamlit CSP limitation documented (unsafe-inline accepted risk)
- ✅ Report-only mode available for safe Streamlit rollout
- ✅ Monitoring plan defined for CSP violations
- ✅ Nginx routes auth endpoints to auth_service correctly
- ✅ Nginx validates trusted proxy IPs via `real_ip_from` directives
- ✅ Trusted proxy validation prevents IP spoofing attacks (Nginx + app-level)
- ✅ CSP violation reports logged with rate limiting (10KB max payload, Nginx + app-level)
- ✅ All OAuth2 flows work correctly through Nginx (login → callback → dashboard)
- ✅ Automated test verifies CSP nonce propagation to HTML (auth_service templates)
- ✅ Automated test verifies app-level payload size guard (defense-in-depth)
- ✅ Automated test verifies Nginx blocks forged X-Forwarded-For (negative test)

---

## Architecture

### Current State (Component 4)

**Auth Flow:**
```
Browser → Streamlit (port 8501) → Login page
         ↓
Browser → FastAPI auth_service (port 8001) → /login
         ↓
Browser → Auth0 IdP
         ↓
Browser → FastAPI auth_service (port 8001) → /callback
         ↓
Browser → Streamlit (port 8501) → Dashboard
```

**Problem:** Direct access to auth_service bypasses Nginx, exposing internal service.

### Target State (Component 5)

**Auth Flow via Nginx:**
```
Browser → Nginx (port 443) → /
         ↓
Nginx → Streamlit (web_console_oauth2:8501) → Login page
         ↓
Browser → Nginx (port 443) → /login
         ↓
Nginx → FastAPI (auth_service:8001) → /login
         ↓
Browser → Auth0 IdP
         ↓
Browser → Nginx (port 443) → /callback
         ↓
Nginx → FastAPI (auth_service:8001) → /callback
         ↓
Browser → Nginx (port 443) → /
         ↓
Nginx → Streamlit (web_console_oauth2:8501) → Dashboard
```

**Benefits:**
- Single entry point (Nginx) for all traffic
- Centralized security policy enforcement (CSP, rate limiting)
- Nginx-level trusted proxy IP validation (prevents X-Forwarded-For spoofing)
- Internal services not exposed to internet
- CSP protection for both auth_service and Streamlit pages

### CSP Architecture

**CSP Policy (Updated with Auth0 CDN + Streamlit requirements):**
```
Content-Security-Policy:
  default-src 'self';
  script-src 'self' 'nonce-{random}' https://*.auth0.com https://cdn.auth0.com;
  style-src 'self' 'unsafe-inline' https://fonts.googleapis.com;
  font-src 'self' https://fonts.gstatic.com;
  img-src 'self' data: blob: https:;
  connect-src 'self' wss: https://*.auth0.com https://*.trading-platform.local;
  frame-src 'self' https://*.auth0.com;
  base-uri 'self';
  form-action 'self';
  upgrade-insecure-requests;
  block-all-mixed-content;
  report-uri /csp-report;
```

**Key Updates (Codex Review Issues #2 + #3):**
- `script-src`: Added `https://cdn.auth0.com` for Auth0 Universal Login scripts
- `connect-src`: Added `wss:` for Streamlit WebSocket connections
- `img-src`: Added `blob:` for Streamlit blob URLs
- All domains now include both wildcard and CDN-specific entries

**Nonce Generation:**
- Generate random nonce per request (16 bytes, hex-encoded)
- Store nonce in response headers
- Pass nonce to templates for inline scripts
- Reject scripts without valid nonce

**Streamlit CSP Integration (Codex Review Issue #2):**
- CSP middleware applied to auth_service responses (FastAPI)
- Streamlit responses inherit CSP via shared middleware or proxy headers
- Nonce propagation to Streamlit templates (if using custom HTML)
- WebSocket support: `wss:` in `connect-src` for `/_stcore/stream`

---

## Implementation Plan

### Deliverable 1: CSP Middleware for FastAPI + Template Integration (4 hours)

**File:** `apps/auth_service/middleware/csp_middleware.py`

**Purpose:** Generate CSP headers with nonces for auth_service responses AND inject nonces into HTML templates

**Implementation:**

```python
"""Content Security Policy (CSP) middleware for FastAPI auth service.

Adds nonce-based CSP headers to all responses to prevent XSS attacks.
Generates unique nonce per request for inline script execution.

IMPORTANT: This middleware covers auth_service responses.
For Streamlit page CSP coverage, see Deliverable 5 (Streamlit CSP Integration).

Nonce Template Integration:
- Middleware stores nonce in request.state.csp_nonce
- FastAPI templates access nonce via {{ csp_nonce }}
- Example: <script nonce="{{ csp_nonce }}">...</script>
"""

import logging
import secrets
from typing import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)


class CSPMiddleware(BaseHTTPMiddleware):
    """Middleware to add Content Security Policy headers with nonces.

    Implements defense-in-depth XSS protection by:
    - Restricting script sources to 'self' and Auth0 domains (including cdn.auth0.com)
    - Using nonces for inline scripts (Streamlit compatibility)
    - Blocking unsafe-inline and unsafe-eval
    - Logging CSP violations via report-uri
    - Supporting Streamlit requirements: wss:, blob:, data:

    Nonce generation:
    - 16-byte random nonce per request
    - Hex-encoded (32 characters)
    - Stored in request.state.csp_nonce for template access

    CSP Coverage:
    - auth_service endpoints: /login, /callback, /refresh, /logout
    - For Streamlit pages: See Deliverable 5 (Streamlit CSP Integration)
    """

    def __init__(
        self,
        app,
        auth0_domain: str,
        report_uri: str = "/csp-report",
        enable_report_only: bool = False,
    ):
        """Initialize CSP middleware.

        Args:
            app: FastAPI application
            auth0_domain: Auth0 tenant domain (e.g., "dev-xyz.us.auth0.com")
            report_uri: Endpoint for CSP violation reports
            enable_report_only: If True, use Content-Security-Policy-Report-Only
        """
        super().__init__(app)
        self.auth0_domain = auth0_domain
        self.report_uri = report_uri
        self.enable_report_only = enable_report_only

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Add CSP headers to response.

        Generates nonce, processes request, adds CSP header to response.
        """
        # Generate nonce for this request (16 bytes = 32 hex chars)
        nonce = secrets.token_hex(16)

        # Store nonce in request state for template access
        request.state.csp_nonce = nonce

        # Process request
        response = await call_next(request)

        # Build CSP policy
        csp_policy = self._build_csp_policy(nonce)

        # Add CSP header
        header_name = (
            "Content-Security-Policy-Report-Only"
            if self.enable_report_only
            else "Content-Security-Policy"
        )
        response.headers[header_name] = csp_policy

        # Log CSP header for debugging
        logger.debug(
            "CSP header added",
            extra={
                "nonce": nonce[:8] + "...",
                "report_only": self.enable_report_only,
                "path": request.url.path,
            },
        )

        return response

    def _build_csp_policy(self, nonce: str) -> str:
        """Build CSP policy string with nonce.

        Updated to include:
        - Auth0 CDN domains (cdn.auth0.com) for Universal Login
        - Streamlit requirements: wss:, blob:, data:
        - Comprehensive allowlist for all required sources

        Args:
            nonce: Random nonce for this request

        Returns:
            CSP policy string
        """
        # Note: unsafe-inline for style-src required for Streamlit/FastAPI templating
        # TODO: Replace with nonce-based styles when Streamlit supports it
        policy_directives = [
            "default-src 'self'",
            # script-src: Allow Auth0 wildcard + CDN + nonce
            f"script-src 'self' 'nonce-{nonce}' https://{self.auth0_domain} https://cdn.auth0.com",
            # style-src: Streamlit requires unsafe-inline
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
            "font-src 'self' https://fonts.gstatic.com",
            # img-src: Include blob: for Streamlit
            "img-src 'self' data: blob: https:",
            # connect-src: Include wss: for Streamlit WebSockets
            f"connect-src 'self' wss: https://{self.auth0_domain} https://cdn.auth0.com https://*.trading-platform.local",
            f"frame-src 'self' https://{self.auth0_domain}",
            "base-uri 'self'",
            "form-action 'self'",
            "upgrade-insecure-requests",
            "block-all-mixed-content",
            f"report-uri {self.report_uri}",
        ]

        return "; ".join(policy_directives)
```

**Integration with auth_service:**

```python
# File: apps/auth_service/main.py
# Add CSP middleware to FastAPI app

import os
from apps.auth_service.middleware.csp_middleware import CSPMiddleware
from apps.auth_service.config import get_config

# ... existing imports ...

app = FastAPI(
    title="Auth Service",
    description="OAuth2 authentication endpoints with PKCE",
    version="1.0.0",
)

# Add CSP middleware (AFTER app creation, BEFORE routes)
config = get_config()

# Wire CSP_REPORT_ONLY environment variable (Codex Review Issue #6)
enable_report_only = os.getenv("CSP_REPORT_ONLY", "false").lower() == "true"

app.add_middleware(
    CSPMiddleware,
    auth0_domain=config.auth0_domain,
    report_uri="/csp-report",
    enable_report_only=enable_report_only,  # Controlled by env var
)

# Include routers
app.include_router(callback.router, tags=["auth"])
# ... rest of routers ...
```

**Template Integration Example:**

```python
# File: apps/auth_service/routes/some_page.py
# Example showing how to render HTML template with nonce

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory="apps/auth_service/templates")

@router.get("/example-page", response_class=HTMLResponse)
async def example_page(request: Request):
    """Example page demonstrating nonce injection into templates."""
    # Nonce is automatically available via request.state.csp_nonce
    # (set by CSPMiddleware)
    return templates.TemplateResponse(
        "example.html",
        {
            "request": request,
            "csp_nonce": request.state.csp_nonce,  # Pass nonce to template
            "some_data": "example",
        },
    )
```

**Template File Example:**

```html
<!-- File: apps/auth_service/templates/example.html -->
<!DOCTYPE html>
<html>
<head>
    <title>Example Page</title>
</head>
<body>
    <h1>CSP Nonce Example</h1>

    <!-- Inline script with nonce (ALLOWED by CSP) -->
    <script nonce="{{ csp_nonce }}">
        console.log("This script has valid nonce and will execute");
        document.addEventListener("DOMContentLoaded", function() {
            console.log("DOM loaded");
        });
    </script>

    <!-- Inline script WITHOUT nonce (BLOCKED by CSP) -->
    <!-- <script>alert("XSS attack - blocked by CSP")</script> -->

    <!-- External script from allowed domain (ALLOWED by CSP) -->
    <script src="https://cdn.auth0.com/some-library.js"></script>
</body>
</html>
```

**Testing:**

**Unit Tests:** `tests/apps/auth_service/middleware/test_csp_middleware.py`

```python
"""Tests for CSP middleware."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.auth_service.middleware.csp_middleware import CSPMiddleware


@pytest.fixture
def app_with_csp():
    """Create test FastAPI app with CSP middleware."""
    app = FastAPI()

    @app.get("/test")
    async def test_endpoint(request):
        nonce = request.state.csp_nonce
        return {"nonce": nonce}

    app.add_middleware(
        CSPMiddleware,
        auth0_domain="dev-test.us.auth0.com",
        report_uri="/csp-report",
        enable_report_only=False,
    )

    return app


def test_csp_middleware_adds_header(app_with_csp):
    """Test CSP middleware adds Content-Security-Policy header."""
    client = TestClient(app_with_csp)
    response = client.get("/test")

    assert response.status_code == 200
    assert "Content-Security-Policy" in response.headers

    csp_header = response.headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp_header
    assert "script-src 'self' 'nonce-" in csp_header
    assert "https://dev-test.us.auth0.com" in csp_header
    assert "https://cdn.auth0.com" in csp_header  # NEW: Auth0 CDN
    assert "report-uri /csp-report" in csp_header


def test_csp_middleware_includes_streamlit_requirements(app_with_csp):
    """Test CSP middleware includes Streamlit-required directives."""
    client = TestClient(app_with_csp)
    response = client.get("/test")

    csp_header = response.headers["Content-Security-Policy"]

    # Streamlit WebSocket support
    assert "connect-src 'self' wss:" in csp_header

    # Streamlit blob URLs
    assert "blob:" in csp_header

    # Streamlit data URLs
    assert "data:" in csp_header

    # Streamlit inline styles (required)
    assert "style-src 'self' 'unsafe-inline'" in csp_header


def test_csp_middleware_generates_unique_nonces(app_with_csp):
    """Test CSP middleware generates unique nonces per request."""
    client = TestClient(app_with_csp)

    response1 = client.get("/test")
    response2 = client.get("/test")

    csp1 = response1.headers["Content-Security-Policy"]
    csp2 = response2.headers["Content-Security-Policy"]

    # Extract nonces from CSP headers
    import re
    nonce_pattern = r"'nonce-([a-f0-9]{32})'"

    nonce1 = re.search(nonce_pattern, csp1).group(1)
    nonce2 = re.search(nonce_pattern, csp2).group(1)

    assert nonce1 != nonce2, "Nonces should be unique per request"


def test_csp_middleware_report_only_mode(app_with_csp):
    """Test CSP middleware in report-only mode."""
    app = FastAPI()

    @app.get("/test")
    async def test_endpoint():
        return {"status": "ok"}

    app.add_middleware(
        CSPMiddleware,
        auth0_domain="dev-test.us.auth0.com",
        report_uri="/csp-report",
        enable_report_only=True,  # Report-only mode
    )

    client = TestClient(app)
    response = client.get("/test")

    assert "Content-Security-Policy-Report-Only" in response.headers
    assert "Content-Security-Policy" not in response.headers


def test_csp_middleware_nonce_stored_in_request_state(app_with_csp):
    """Test CSP middleware stores nonce in request.state for templates."""
    client = TestClient(app_with_csp)
    response = client.get("/test")

    # Endpoint returns nonce from request.state
    nonce_from_state = response.json()["nonce"]

    # Extract nonce from CSP header
    import re
    csp_header = response.headers["Content-Security-Policy"]
    nonce_pattern = r"'nonce-([a-f0-9]{32})'"
    nonce_from_header = re.search(nonce_pattern, csp_header).group(1)

    assert nonce_from_state == nonce_from_header


def test_csp_middleware_template_rendering_with_nonce(app_with_csp):
    """Test CSP middleware nonce appears in rendered HTML template.

    Addresses Codex Iteration 2 Issue #1: Missing template integration example.
    """
    from fastapi.templating import Jinja2Templates
    from io import StringIO

    # Create app with template endpoint
    app = FastAPI()

    # Mock templates using in-memory template
    templates = Jinja2Templates(directory=".")

    @app.get("/page", response_class=HTMLResponse)
    async def test_page(request: Request):
        # Simulate template rendering with nonce
        nonce = request.state.csp_nonce
        html = f"""<!DOCTYPE html>
<html>
<head><title>Test</title></head>
<body>
    <script nonce="{nonce}">console.log('test');</script>
</body>
</html>"""
        return HTMLResponse(content=html)

    app.add_middleware(
        CSPMiddleware,
        auth0_domain="dev-test.us.auth0.com",
        report_uri="/csp-report",
        enable_report_only=False,
    )

    client = TestClient(app)
    response = client.get("/page")

    # Extract nonce from CSP header
    csp_header = response.headers["Content-Security-Policy"]
    nonce_pattern = r"'nonce-([a-f0-9]{32})'"
    nonce_match = re.search(nonce_pattern, csp_header)
    assert nonce_match, "CSP header should contain nonce"
    header_nonce = nonce_match.group(1)

    # Verify nonce appears in HTML
    assert f'nonce="{header_nonce}"' in response.text, "HTML should contain nonce matching CSP header"
```

**Integration Tests:** `tests/integration/test_csp_enforcement.py`

```python
"""Integration tests for CSP enforcement."""

import pytest
from fastapi.testclient import TestClient

from apps.auth_service.main import app


def test_csp_blocks_inline_script_without_nonce():
    """Test CSP blocks inline scripts without valid nonce.

    Note: This test simulates browser CSP enforcement logic.
    Actual enforcement happens in the browser, not FastAPI.
    """
    client = TestClient(app)
    response = client.get("/login")

    csp_header = response.headers.get("Content-Security-Policy", "")

    # Verify CSP header exists
    assert csp_header, "CSP header should be present"

    # Verify unsafe-inline is NOT allowed for scripts
    assert "'unsafe-inline'" not in csp_header or "script-src" in csp_header

    # If 'unsafe-inline' appears, it should NOT be in script-src
    # (may be in style-src for Streamlit compatibility)
    if "'unsafe-inline'" in csp_header:
        # Extract script-src directive
        script_src = [d for d in csp_header.split(";") if "script-src" in d][0]
        assert "'unsafe-inline'" not in script_src


def test_csp_allows_nonce_based_scripts():
    """Test CSP allows scripts with valid nonce."""
    client = TestClient(app)
    response = client.get("/login")

    csp_header = response.headers.get("Content-Security-Policy", "")

    # Verify nonce-based script-src is present
    assert "'nonce-" in csp_header
    assert "script-src" in csp_header


def test_csp_includes_auth0_domain_and_cdn():
    """Test CSP includes Auth0 domain AND CDN in script-src and connect-src."""
    client = TestClient(app)
    response = client.get("/login")

    csp_header = response.headers.get("Content-Security-Policy", "")

    # Auth0 domain should be in script-src (for Universal Login scripts)
    # and connect-src (for token endpoint)
    assert "https://" in csp_header

    # Must include cdn.auth0.com for Auth0 Universal Login
    assert "cdn.auth0.com" in csp_header


def test_csp_includes_streamlit_websocket_support():
    """Test CSP includes wss: for Streamlit WebSocket connections."""
    client = TestClient(app)
    response = client.get("/login")

    csp_header = response.headers.get("Content-Security-Policy", "")

    # Streamlit requires wss: for /_stcore/stream WebSocket
    assert "wss:" in csp_header
    assert "connect-src" in csp_header
```

---

### Deliverable 2: Nginx Routing + Real IP Validation (3 hours)

**Files to Create/Modify (Codex Iteration 5 Issue #1):**
- **CREATE:** `apps/web_console/nginx/nginx-oauth2.conf.template` (source template with `${CSP_REPORT_ONLY}` placeholders)
- **CREATE:** `apps/web_console/nginx/entrypoint.sh` (bash script that runs envsubst + nginx)
- **MODIFY:** `docker-compose.yml` (add entrypoint, volume mounts, CSP_REPORT_ONLY env var)
- **NOTE:** `nginx-oauth2.conf` is generated at container start, not edited manually

**Changes to nginx-oauth2.conf.template (Codex Iteration 5 Issue #1):**
1. Add routing for `/login`, `/callback`, `/refresh`, `/logout` to auth_service
2. **NEW:** Add `real_ip_from` directive for trusted proxy subnet validation
3. **NEW:** Add rate limiting to `/csp-report` endpoint
4. **NEW:** Add `client_max_body_size` limit for `/csp-report` (10KB)
5. **NEW (Codex Iteration 4 Issue #1):** Add CSP toggle using envsubst template syntax for safe rollout

**Implementation:**

```nginx
# Nginx Reverse Proxy for Web Console with OAuth2
# Component 5 of P2T3 Phase 3
#
# ⚠️ IMPORTANT: This is nginx-oauth2.conf.template (OAuth2 profile template)
# This template file is processed by envsubst at container startup to generate nginx-oauth2.conf
# DO NOT confuse with nginx.conf (mTLS profile - P2T3 Phase 2 Component 3)
#
# Security Features:
# - OAuth2/OIDC authentication with Auth0 IdP
# - Content Security Policy (CSP) with nonces
# - Three-layer rate limiting (connection, IP-based, endpoint-based)
# - TLS 1.3 with Mozilla Intermediate cipher suite
# - OCSP stapling for certificate validation
# - Header spoofing prevention (real_ip_from validation)
# - Trusted proxy IP validation (Nginx-level)
# - WebSocket support for Streamlit (3600s timeout)
#
# Template Processing (Codex Iteration 5 Issue #1):
# - This file contains ${CSP_REPORT_ONLY} placeholders
# - entrypoint.sh runs envsubst to generate final nginx-oauth2.conf
# - Supports CSP report-only mode toggle via environment variable
#
# References:
# - docs/TASKS/P2T3-Phase3_Component5_Plan.md
# - docs/RUNBOOKS/web-console-oauth2-setup.md

user nginx;
worker_processes auto;
error_log /var/log/nginx/error.log warn;
pid /var/run/nginx.pid;

events {
    worker_connections 1024;
}

http {
    include /etc/nginx/mime.types;
    default_type application/octet-stream;

    # Logging format with OAuth2 details
    log_format oauth2 '$remote_addr - $http_x_forwarded_user [$time_local] '
                      '"$request" $status $body_bytes_sent '
                      '"$http_referer" "$http_user_agent"';

    access_log /var/log/nginx/access.log oauth2;

    # Performance settings
    sendfile on;
    tcp_nopush on;
    tcp_nodelay on;
    keepalive_timeout 65;
    types_hash_max_size 2048;

    # Rate limiting zones (MUST be in http context)
    # Pre-auth: Before OAuth2 verification (protect against DoS)
    limit_req_zone $binary_remote_addr zone=preauth_limit:10m rate=20r/s;

    # Auth endpoints: Stricter limits for /login, /callback
    limit_req_zone $binary_remote_addr zone=auth_limit:10m rate=10r/m;

    # CSP report endpoint: Prevent abuse (Codex Review Issue #5)
    limit_req_zone $binary_remote_addr zone=csp_report_limit:10m rate=10r/m;

    # Connection-level rate limiting (per IP)
    limit_conn_zone $binary_remote_addr zone=conn_limit:10m;

    # Rate limiting status codes (return 429 Too Many Requests)
    limit_req_status 429;
    limit_conn_status 429;

    # CSP Toggle Architecture (Codex Iteration 4 Issue #1 - Fixed invalid map syntax)
    # ---
    # CSP headers are set by TWO different components based on route:
    #
    # 1. FastAPI CSPMiddleware (auth_service):
    #    - Handles: /login, /callback, /refresh, /logout, /csp-report
    #    - Reads: CSP_REPORT_ONLY environment variable directly
    #    - Implementation: CSPMiddleware.enable_report_only parameter
    #    - No Nginx involvement (FastAPI sets headers)
    #
    # 2. Nginx (Streamlit routes):
    #    - Handles: /, /_stcore/stream (Streamlit pages)
    #    - Reads: CSP_REPORT_ONLY environment variable via envsubst
    #    - Implementation: add_header directives in location blocks (see below)
    #    - Why: Streamlit framework doesn't support FastAPI middleware
    #
    # Toggle Mechanism:
    # - Set CSP_REPORT_ONLY=true in docker-compose.yml → report-only mode
    # - Set CSP_REPORT_ONLY=false (default) → enforcement mode
    # - Both FastAPI and Nginx read same environment variable for consistency
    #
    # NOTE: Original plan used invalid Nginx map syntax `map $csp_report_only "off"`.
    #       This has been removed because:
    #       - FastAPI reads CSP_REPORT_ONLY directly (no map needed)
    #       - Nginx uses envsubst templating for Streamlit CSP headers (see Deliverable 5)
    #       - No dynamic variable mapping required in Nginx http context

    # Upstream web console (Streamlit)
    upstream web_console {
        server web_console_oauth2:8501;
        keepalive 32;
    }

    # Upstream auth service (FastAPI)
    upstream auth_service {
        server auth_service:8001;
        keepalive 16;
    }

    # HTTPS server with OAuth2
    server {
        listen 443 ssl;
        server_name localhost;

        # TLS certificate configuration
        # Note: Paths assume certificates mounted at /etc/nginx/certs/
        ssl_certificate /etc/nginx/certs/server.crt;
        ssl_certificate_key /etc/nginx/certs/server.key;

        # TLS protocol and cipher configuration (Mozilla Intermediate)
        ssl_protocols TLSv1.2 TLSv1.3;
        ssl_ciphers 'ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305:DHE-RSA-AES128-GCM-SHA256:DHE-RSA-AES256-GCM-SHA384';
        ssl_prefer_server_ciphers off;

        # TLS session optimization
        ssl_session_cache shared:SSL:10m;
        ssl_session_timeout 10m;
        ssl_session_tickets off;

        # ECDH curve configuration (X25519 preferred for TLS 1.3)
        ssl_ecdh_curve X25519:secp384r1;

        # Diffie-Hellman parameters (4096-bit for strong security)
        ssl_dhparam /etc/nginx/certs/dhparam.pem;

        # OCSP stapling (online certificate status checking)
        ssl_stapling on;
        ssl_stapling_verify on;
        ssl_trusted_certificate /etc/nginx/certs/ca.crt;

        # DNS resolver for OCSP
        resolver 127.0.0.11 valid=30s;
        resolver_timeout 5s;

        # Trusted proxy configuration (Codex Review Issue #4)
        # Validates X-Forwarded-For headers from trusted proxy subnet
        # Prevents IP spoofing attacks
        set_real_ip_from 172.28.0.0/24;  # Docker oauth2_network subnet
        real_ip_header X-Forwarded-For;
        real_ip_recursive on;

        # Security headers (non-CSP)
        # NOTE: CSP headers handled by FastAPI middleware (not Nginx)
        # CSP toggle mechanism (enforcement vs report-only) configured via map directive in http context
        add_header Strict-Transport-Security "max-age=63072000; includeSubDomains" always;
        add_header X-Frame-Options "DENY" always;
        add_header X-Content-Type-Options "nosniff" always;
        add_header X-XSS-Protection "1; mode=block" always;

        # Rate limiting (connection-level)
        limit_conn conn_limit 30;

        # OAuth2 /login endpoint (public, no auth required)
        # Routes to FastAPI auth_service
        location /login {
            # Auth endpoint rate limiting (10 requests/min per IP)
            limit_req zone=auth_limit burst=2 nodelay;

            # Proxy to auth_service
            proxy_pass http://auth_service/login;
            proxy_http_version 1.1;

            # Standard proxy headers
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto https;

            # Timeouts
            proxy_connect_timeout 60s;
            proxy_send_timeout 60s;
            proxy_read_timeout 60s;
        }

        # OAuth2 /callback endpoint (public, no auth required)
        # Routes to FastAPI auth_service
        location /callback {
            # Auth endpoint rate limiting (10 requests/min per IP)
            limit_req zone=auth_limit burst=2 nodelay;

            # Proxy to auth_service
            proxy_pass http://auth_service/callback;
            proxy_http_version 1.1;

            # Standard proxy headers
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto https;

            # Timeouts
            proxy_connect_timeout 60s;
            proxy_send_timeout 60s;
            proxy_read_timeout 60s;
        }

        # OAuth2 /refresh endpoint (requires valid session cookie)
        # Routes to FastAPI auth_service
        location /refresh {
            # Auth endpoint rate limiting (10 requests/min per IP)
            limit_req zone=auth_limit burst=2 nodelay;

            # Proxy to auth_service
            proxy_pass http://auth_service/refresh;
            proxy_http_version 1.1;

            # Standard proxy headers
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto https;

            # Timeouts
            proxy_connect_timeout 60s;
            proxy_send_timeout 60s;
            proxy_read_timeout 60s;
        }

        # OAuth2 /logout endpoint (requires valid session cookie)
        # Routes to FastAPI auth_service
        location /logout {
            # Auth endpoint rate limiting (10 requests/min per IP)
            limit_req zone=auth_limit burst=2 nodelay;

            # Proxy to auth_service
            proxy_pass http://auth_service/logout;
            proxy_http_version 1.1;

            # Standard proxy headers
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto https;

            # Timeouts
            proxy_connect_timeout 60s;
            proxy_send_timeout 60s;
            proxy_read_timeout 60s;
        }

        # CSP violation reporting endpoint (Codex Review Issue #5)
        # Routes to auth_service (Component 5: CSP Hardening)
        # Security: Rate limited + payload size restricted
        location /csp-report {
            # Rate limit CSP reports (10/min per IP, burst 2)
            # Prevents abuse of unauthenticated endpoint
            limit_req zone=csp_report_limit burst=2 nodelay;

            # Restrict payload size (10KB max)
            # Prevents large payload DoS attacks
            client_max_body_size 10k;

            # Allow CSP violation reports without auth
            # Browser sends POST with application/csp-report content type

            # Proxy to auth_service
            proxy_pass http://auth_service/csp-report;
            proxy_http_version 1.1;

            # Standard proxy headers
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto https;

            # Timeouts
            proxy_connect_timeout 30s;
            proxy_send_timeout 30s;
            proxy_read_timeout 30s;
        }

        # Default location (Streamlit app)
        location / {
            # Pre-auth rate limiting
            limit_req zone=preauth_limit burst=10 nodelay;

            # Proxy configuration
            proxy_pass http://web_console;
            proxy_http_version 1.1;

            # WebSocket support (required for Streamlit)
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection "upgrade";

            # Standard proxy headers
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
            proxy_set_header X-Forwarded-Host $host;

            # Timeouts (standard HTTP requests)
            proxy_connect_timeout 60s;
            proxy_send_timeout 60s;
            proxy_read_timeout 300s;

            # Buffer settings
            proxy_buffering on;
            proxy_buffer_size 4k;
            proxy_buffers 8 4k;
            proxy_busy_buffers_size 8k;
        }

        # WebSocket-specific location for Streamlit streaming
        location /_stcore/stream {
            # Pre-auth rate limiting
            limit_req zone=preauth_limit burst=10 nodelay;

            # Proxy configuration
            proxy_pass http://web_console;
            proxy_http_version 1.1;

            # WebSocket headers (REQUIRED)
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection "upgrade";

            # Standard proxy headers
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
            proxy_set_header X-Forwarded-Host $host;

            # WebSocket timeouts (CRITICAL: Must be >= 3600s)
            proxy_connect_timeout 60s;
            proxy_send_timeout 3600s;  # 1 hour
            proxy_read_timeout 3600s;  # 1 hour

            # Disable buffering for real-time streaming
            proxy_buffering off;
            proxy_cache off;
        }

        # Health check endpoint (no rate limiting)
        location /health {
            access_log off;
            return 200 "OK\n";
            add_header Content-Type text/plain;
        }
    }

    # HTTP redirect to HTTPS
    server {
        listen 80;
        server_name localhost;
        return 301 https://$host$request_uri;
    }
}
```

**CSP Toggle Mechanism (Codex Iteration 4 Issue #1 - Clarified architecture):**

CSP headers are set by **two different components** based on the route:

1. **FastAPI CSPMiddleware (auth_service routes):**
   - **Handles:** `/login`, `/callback`, `/refresh`, `/logout`, `/csp-report`
   - **Toggle:** Reads `CSP_REPORT_ONLY` environment variable directly
   - **Implementation:**
     ```python
     enable_report_only = os.getenv("CSP_REPORT_ONLY", "false").lower() == "true"
     app.add_middleware(CSPMiddleware, enable_report_only=enable_report_only)
     ```
   - **Header:** Sets `Content-Security-Policy-Report-Only` if enabled, else `Content-Security-Policy`
   - **No Nginx involvement:** FastAPI sets CSP headers directly in middleware

2. **Nginx (Streamlit routes only):**
   - **Handles:** `/` and `/_stcore/stream` (Streamlit framework pages)
   - **Toggle:** Uses `envsubst` templating to substitute `${CSP_REPORT_ONLY}` in config
   - **Implementation:** See Deliverable 5 (Streamlit CSP Integration) for envsubst approach
   - **Why:** Streamlit doesn't support FastAPI middleware (separate service)

3. **Environment Variable Wiring:**
   - Set `CSP_REPORT_ONLY=true` in `docker-compose.yml` for report-only mode
   - Both FastAPI and Nginx read the same environment variable
   - Example in `docker-compose.yml`:
     ```yaml
     auth_service:
       environment:
         - CSP_REPORT_ONLY=true  # FastAPI reads directly
     nginx_oauth2:
       environment:
         - CSP_REPORT_ONLY=true  # Nginx uses envsubst preprocessing
     ```

4. **Rollout Strategy:**
   - **Phase 1 (Week 1-2):** Deploy with `CSP_REPORT_ONLY=true`, monitor `/csp-report` endpoint
   - **Phase 2 (Week 3):** Analyze violations, update CSP policy to fix legitimate issues
   - **Phase 3 (Week 4+):** Switch to enforcement mode (`CSP_REPORT_ONLY=false`), continue monitoring

**IMPORTANT:** Original plan (Iteration 3) used invalid Nginx map syntax `map $csp_report_only "off"`. This has been removed because:
- FastAPI handles CSP for auth endpoints (no Nginx map needed)
- Nginx handles CSP for Streamlit routes via envsubst templating (no dynamic variables)
- No Nginx http-level map directive is required for this architecture

**Testing:**

**Integration Tests:** `tests/integration/test_nginx_oauth2_routing.py`

```python
"""Integration tests for Nginx OAuth2 routing.

NOTE: These tests require docker-compose --profile oauth2 up -d
See Codex Review Issue #6 for test harness usage.
"""

import pytest
import requests


@pytest.fixture
def nginx_base_url():
    """Base URL for Nginx reverse proxy."""
    return "https://localhost:443"


def test_nginx_routes_login_to_auth_service(nginx_base_url):
    """Test /login routes to auth_service."""
    # Note: This test requires Nginx + auth_service running
    # Use docker-compose --profile oauth2 up -d

    response = requests.get(
        f"{nginx_base_url}/login",
        allow_redirects=False,
        verify=False,  # Skip cert verification for self-signed certs
    )

    # Should redirect to Auth0 (302 Found)
    assert response.status_code == 302
    assert "auth0.com" in response.headers.get("Location", "")


def test_nginx_routes_callback_to_auth_service(nginx_base_url):
    """Test /callback routes to auth_service."""
    # Send invalid callback (missing code/state)
    # Should return 400 Bad Request from auth_service

    response = requests.get(
        f"{nginx_base_url}/callback",
        verify=False,
    )

    # FastAPI should return 422 Unprocessable Entity (missing query params)
    assert response.status_code == 422


def test_nginx_routes_logout_to_auth_service(nginx_base_url):
    """Test /logout routes to auth_service."""
    # Send logout without session cookie
    # Should redirect to Auth0 logout (or return error)

    response = requests.post(
        f"{nginx_base_url}/logout",
        allow_redirects=False,
        verify=False,
    )

    # Should return 401 Unauthorized (no session cookie)
    # or 302 redirect to Auth0 logout
    assert response.status_code in [302, 401]


def test_nginx_csp_report_rate_limiting(nginx_base_url):
    """Test /csp-report endpoint has rate limiting (Codex Review Issue #5)."""
    # Send 15 CSP reports rapidly (limit is 10/min)
    responses = []
    for i in range(15):
        response = requests.post(
            f"{nginx_base_url}/csp-report",
            json={
                "csp-report": {
                    "document-uri": "https://localhost/test",
                    "violated-directive": "script-src",
                    "effective-directive": "script-src",
                    "original-policy": "default-src 'self'",
                    "blocked-uri": "https://evil.com/script.js",
                    "status-code": 200,
                }
            },
            verify=False,
        )
        responses.append(response)

    # At least one request should be rate limited (429)
    status_codes = [r.status_code for r in responses]
    assert 429 in status_codes, "CSP report endpoint should enforce rate limiting"


def test_nginx_csp_report_payload_size_limit(nginx_base_url):
    """Test /csp-report rejects large payloads (Codex Review Issue #5)."""
    # Send CSP report with 20KB payload (limit is 10KB)
    large_payload = {
        "csp-report": {
            "document-uri": "https://localhost/test",
            "violated-directive": "script-src",
            "effective-directive": "script-src",
            "original-policy": "default-src 'self'",
            "blocked-uri": "https://evil.com/script.js",
            "status-code": 200,
            "sample": "x" * 20000,  # 20KB sample (exceeds 10KB limit)
        }
    }

    response = requests.post(
        f"{nginx_base_url}/csp-report",
        json=large_payload,
        verify=False,
    )

    # Should return 413 Request Entity Too Large
    assert response.status_code == 413


def test_nginx_blocks_forged_x_forwarded_for(nginx_base_url, auth_service_url):
    """Test Nginx set_real_ip_from blocks forged X-Forwarded-For headers.

    Addresses Codex Iteration 2 Issue #5: Missing negative test for real_ip validation.
    Addresses Codex Iteration 3 Issue #2: Verify actual IP blocking, not just status codes.

    NOTE: This test simulates an attacker sending forged X-Forwarded-For from untrusted IP.
    Nginx should ignore forged header and use actual client IP.

    Strategy: Use echo endpoint to verify Nginx passes correct IP to backend.
    """
    # Step 1: Create test endpoint that echoes client IP (for verification)
    # This endpoint should be added to auth_service for testing purposes
    # Example: GET /test/echo-ip returns {"client_ip": "x.x.x.x"}

    # Step 2: Send request with forged X-Forwarded-For from external IP
    # (simulating attacker trying to bypass IP-based restrictions)
    response = requests.get(
        f"{nginx_base_url}/test/echo-ip",  # New endpoint in auth_service
        headers={
            "X-Forwarded-For": "1.2.3.4",  # Forged IP (should be ignored)
        },
        verify=False,
        allow_redirects=False,
    )

    # Step 3: Verify request succeeded
    assert response.status_code == 200

    # Step 4: Verify Nginx passed REAL client IP to backend (not forged IP)
    # Nginx should extract actual client IP and pass via X-Real-IP header
    data = response.json()
    client_ip = data.get("client_ip", "")

    # The client IP should NOT be the forged "1.2.3.4"
    # It should be the actual test client IP (likely 127.0.0.1 or docker bridge IP)
    assert client_ip != "1.2.3.4", (
        f"Nginx accepted forged X-Forwarded-For header! "
        f"Expected real IP, got forged IP: {client_ip}"
    )

    # Verify it's a valid IP format (not empty or "unknown")
    assert client_ip and client_ip != "unknown", (
        f"Nginx did not pass valid client IP to backend: {client_ip}"
    )

    # Additional verification: Check that client IP matches expected pattern
    # In Docker environment, should be 172.x.x.x (docker bridge) or 127.0.0.1 (localhost)
    assert (
        client_ip.startswith("172.") or
        client_ip.startswith("127.") or
        client_ip.startswith("::1")  # IPv6 localhost
    ), f"Unexpected client IP format: {client_ip}"


# Test helper endpoint for auth_service (add to apps/auth_service/main.py)
# This endpoint should be added as part of Deliverable 3 implementation
"""
Example endpoint to add to auth_service:

@app.get("/test/echo-ip")
async def echo_client_ip(request: Request):
    '''Echo client IP for testing Nginx real_ip_from validation.

    Returns the client IP that Nginx forwarded to the backend.
    Used to verify Nginx correctly ignores forged X-Forwarded-For headers.
    '''
    # Nginx sets X-Real-IP to actual client IP (after real_ip_from processing)
    client_ip = request.headers.get("X-Real-IP", "unknown")

    return {
        "client_ip": client_ip,
        "x_forwarded_for": request.headers.get("X-Forwarded-For", ""),
        "x_real_ip": request.headers.get("X-Real-IP", ""),
    }
"""
```

---

### Deliverable 3: Trusted Proxy IP Validation (2 hours)

**Purpose:** Prevent X-Forwarded-For header spoofing by validating requests come from trusted proxy (Nginx).

**Files:**
1. `apps/web_console/utils.py` (already exists, add validation)
2. `apps/auth_service/main.py` (add `/test/echo-ip` endpoint for testing - Codex Iteration 3 Issue #2)

**Implementation:**

```python
# File: apps/web_console/utils.py
# Add trusted proxy validation

import logging
import os
from typing import Callable

from fastapi import HTTPException, Request
from starlette.datastructures import Headers

logger = logging.getLogger(__name__)


def validate_trusted_proxy(request: Request, get_remote_addr: Callable[[], str]) -> None:
    """Validate request comes from trusted proxy.

    Prevents X-Forwarded-For header spoofing by checking the immediate
    peer IP address against TRUSTED_PROXY_IPS environment variable.

    NOTE: This is application-level validation. Nginx-level validation
    via real_ip_from directive is the PRIMARY defense (see nginx-oauth2.conf).
    This function provides defense-in-depth.

    Args:
        request: FastAPI request object
        get_remote_addr: Callable returning immediate peer IP (request.client.host)

    Raises:
        HTTPException: 403 Forbidden if request not from trusted proxy

    Example:
        def get_remote_addr():
            return request.client.host if request.client else "unknown"

        validate_trusted_proxy(request, get_remote_addr)
    """
    # Get trusted proxy IPs from environment (comma-separated)
    trusted_proxies_str = os.getenv("TRUSTED_PROXY_IPS", "")

    if not trusted_proxies_str:
        # No trusted proxies configured - allow all (development mode)
        logger.warning(
            "TRUSTED_PROXY_IPS not set - accepting all requests (INSECURE)",
            extra={"remote_addr": get_remote_addr()},
        )
        return

    trusted_proxies = [ip.strip() for ip in trusted_proxies_str.split(",")]
    remote_addr = get_remote_addr()

    if remote_addr not in trusted_proxies:
        logger.error(
            "Request from untrusted proxy blocked",
            extra={
                "remote_addr": remote_addr,
                "trusted_proxies": trusted_proxies,
                "x_forwarded_for": request.headers.get("X-Forwarded-For"),
            },
        )
        raise HTTPException(
            status_code=403,
            detail="Forbidden: Request not from trusted proxy",
        )

    logger.debug(
        "Trusted proxy validation passed",
        extra={"remote_addr": remote_addr},
    )


def extract_client_ip_from_fastapi(
    request: Request,
    get_remote_addr: Callable[[], str],
) -> str:
    """Extract client IP from FastAPI request with trusted proxy validation.

    UPDATED for Component 5: Now validates trusted proxy before using X-Forwarded-For.

    NOTE: Nginx real_ip_from directive provides Nginx-level validation.
    This function provides application-level defense-in-depth.

    Order of precedence:
    1. Validate request.client.host against TRUSTED_PROXY_IPS
    2. If trusted, use X-Forwarded-For (first IP = original client)
    3. If not trusted or no X-Forwarded-For, use request.client.host

    Args:
        request: FastAPI request object
        get_remote_addr: Callable returning request.client.host

    Returns:
        Client IP address (original client, not proxy)
    """
    # Get immediate peer IP (the proxy)
    remote_addr = get_remote_addr()

    # Check if request is from trusted proxy
    trusted_proxies_str = os.getenv("TRUSTED_PROXY_IPS", "")

    if not trusted_proxies_str:
        # No trusted proxies - use remote_addr directly (development mode)
        logger.debug(
            "No trusted proxies configured, using remote_addr",
            extra={"remote_addr": remote_addr},
        )
        return remote_addr

    trusted_proxies = [ip.strip() for ip in trusted_proxies_str.split(",")]

    if remote_addr not in trusted_proxies:
        # Request not from trusted proxy - use remote_addr
        # This prevents X-Forwarded-For spoofing
        logger.warning(
            "Ignoring X-Forwarded-For from untrusted proxy",
            extra={
                "remote_addr": remote_addr,
                "x_forwarded_for": request.headers.get("X-Forwarded-For"),
            },
        )
        return remote_addr

    # Request is from trusted proxy - use X-Forwarded-For
    x_forwarded_for = request.headers.get("X-Forwarded-For", "").strip()

    if x_forwarded_for:
        # X-Forwarded-For format: "client, proxy1, proxy2, ..."
        # First IP is the original client
        client_ip = x_forwarded_for.split(",")[0].strip()
        logger.debug(
            "Using X-Forwarded-For from trusted proxy",
            extra={
                "client_ip": client_ip,
                "proxy_ip": remote_addr,
                "x_forwarded_for": x_forwarded_for,
            },
        )
        return client_ip

    # No X-Forwarded-For header - use remote_addr
    logger.debug(
        "No X-Forwarded-For header, using remote_addr",
        extra={"remote_addr": remote_addr},
    )
    return remote_addr
```

**Integration with auth_service:**

No changes needed - `extract_client_ip_from_fastapi()` is already used in callback/refresh/logout routes (Component 2).

**Configuration:**

```yaml
# File: docker-compose.yml
# Add TRUSTED_PROXY_IPS to auth_service and web_console_oauth2

services:
  auth_service:
    # ... existing config ...
    environment:
      # ... existing env vars ...
      # Component 5: Trust nginx reverse proxy for X-Forwarded-For
      # Static IP assigned to nginx_oauth2 service (see networks section)
      - TRUSTED_PROXY_IPS=172.28.0.10

  web_console_oauth2:
    # ... existing config ...
    environment:
      # ... existing env vars ...
      # Component 5: Trust nginx reverse proxy for X-Forwarded-For
      - TRUSTED_PROXY_IPS=172.28.0.10
```

**Testing:**

**Unit Tests:** `tests/apps/web_console/test_utils.py`

```python
"""Tests for trusted proxy validation."""

import os
import pytest
from fastapi import HTTPException, Request
from starlette.datastructures import Headers

from apps.web_console.utils import validate_trusted_proxy, extract_client_ip_from_fastapi


def test_validate_trusted_proxy_allows_trusted_ip(monkeypatch):
    """Test validate_trusted_proxy allows requests from trusted proxy."""
    monkeypatch.setenv("TRUSTED_PROXY_IPS", "172.28.0.10,172.28.0.11")

    # Mock request from trusted proxy
    class MockRequest:
        client = type("Client", (), {"host": "172.28.0.10"})()

    def get_remote_addr():
        return "172.28.0.10"

    request = MockRequest()

    # Should not raise exception
    validate_trusted_proxy(request, get_remote_addr)


def test_validate_trusted_proxy_blocks_untrusted_ip(monkeypatch):
    """Test validate_trusted_proxy blocks requests from untrusted IP."""
    monkeypatch.setenv("TRUSTED_PROXY_IPS", "172.28.0.10")

    # Mock request from untrusted IP
    class MockRequest:
        client = type("Client", (), {"host": "192.168.1.100"})()
        headers = Headers({"X-Forwarded-For": "10.0.0.1"})

    def get_remote_addr():
        return "192.168.1.100"

    request = MockRequest()

    # Should raise 403 Forbidden
    with pytest.raises(HTTPException) as exc_info:
        validate_trusted_proxy(request, get_remote_addr)

    assert exc_info.value.status_code == 403
    assert "not from trusted proxy" in exc_info.value.detail.lower()


def test_extract_client_ip_uses_x_forwarded_for_from_trusted_proxy(monkeypatch):
    """Test extract_client_ip uses X-Forwarded-For from trusted proxy."""
    monkeypatch.setenv("TRUSTED_PROXY_IPS", "172.28.0.10")

    # Mock request from trusted proxy with X-Forwarded-For
    class MockRequest:
        client = type("Client", (), {"host": "172.28.0.10"})()
        headers = Headers({"X-Forwarded-For": "203.0.113.45, 172.28.0.10"})

    def get_remote_addr():
        return "172.28.0.10"

    request = MockRequest()
    client_ip = extract_client_ip_from_fastapi(request, get_remote_addr)

    # Should extract first IP from X-Forwarded-For (original client)
    assert client_ip == "203.0.113.45"


def test_extract_client_ip_ignores_x_forwarded_for_from_untrusted_proxy(monkeypatch):
    """Test extract_client_ip ignores X-Forwarded-For from untrusted proxy."""
    monkeypatch.setenv("TRUSTED_PROXY_IPS", "172.28.0.10")

    # Mock request from UNTRUSTED proxy with X-Forwarded-For
    class MockRequest:
        client = type("Client", (), {"host": "192.168.1.100"})()
        headers = Headers({"X-Forwarded-For": "203.0.113.45"})

    def get_remote_addr():
        return "192.168.1.100"

    request = MockRequest()
    client_ip = extract_client_ip_from_fastapi(request, get_remote_addr)

    # Should ignore X-Forwarded-For and use remote_addr
    # This prevents IP spoofing attacks
    assert client_ip == "192.168.1.100"
```

**Test Echo Endpoint (Codex Iteration 3 Issue #2):**

Add a test helper endpoint to `apps/auth_service/main.py` for verifying Nginx real_ip_from validation:

```python
# Add to apps/auth_service/main.py

@app.get("/test/echo-ip")
async def echo_client_ip(request: Request):
    """Echo client IP for testing Nginx real_ip_from validation.

    Returns the client IP that Nginx forwarded to the backend.
    Used by test_nginx_blocks_forged_x_forwarded_for() to verify
    Nginx correctly ignores forged X-Forwarded-For headers.

    NOTE: This endpoint is for testing only. Should be disabled in production
    or protected behind feature flag.
    """
    # Nginx sets X-Real-IP to actual client IP (after real_ip_from processing)
    client_ip = request.headers.get("X-Real-IP", "unknown")

    return {
        "client_ip": client_ip,
        "x_forwarded_for": request.headers.get("X-Forwarded-For", ""),
        "x_real_ip": request.headers.get("X-Real-IP", ""),
    }
```

This endpoint enables the integration test in Deliverable 2 to verify actual IP blocking instead of just checking HTTP status codes.

---

### Deliverable 4: CSP Violation Reporting Endpoint (1.5 hours)

**File:** `apps/auth_service/routes/csp_report.py`

**Purpose:** Log CSP violations for security monitoring

**Implementation:**

```python
"""CSP violation reporting endpoint.

Receives CSP violation reports from browsers and logs them
for security monitoring.

Security: Multi-layer defense (Nginx + FastAPI):
- Nginx-level: Rate limiting (10/min), payload limit (10KB)
- FastAPI-level: App-level payload check (defense-in-depth)
See nginx-oauth2.conf /csp-report location for details.
"""

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
router = APIRouter()


class CSPViolationReport(BaseModel):
    """CSP violation report from browser.

    Browsers send this JSON when CSP is violated.
    Schema defined by W3C CSP Level 2 spec.
    """

    document_uri: str = Field(..., alias="document-uri")
    violated_directive: str = Field(..., alias="violated-directive")
    effective_directive: str = Field(..., alias="effective-directive")
    original_policy: str = Field(..., alias="original-policy")
    blocked_uri: str = Field(..., alias="blocked-uri")
    status_code: int = Field(..., alias="status-code")
    referrer: str = ""
    source_file: str | None = Field(None, alias="source-file")
    line_number: int | None = Field(None, alias="line-number")
    column_number: int | None = Field(None, alias="column-number")
    sample: str | None = None

    class Config:
        """Pydantic config."""

        populate_by_name = True  # Allow both snake_case and kebab-case


class CSPReportWrapper(BaseModel):
    """Wrapper for CSP violation report.

    Browsers send: {"csp-report": {...}}
    """

    csp_report: CSPViolationReport = Field(..., alias="csp-report")

    class Config:
        """Pydantic config."""

        populate_by_name = True


@router.post("/csp-report")
async def csp_report(request: Request, report: CSPReportWrapper) -> dict[str, str]:
    """Handle CSP violation reports.

    Logs CSP violations for security monitoring.
    This endpoint is public (no auth required) to allow browser CSP reports.

    Security measures (defense-in-depth):
    - App-level payload size check (10KB max) - Codex Review Issue #4
    - Rate limiting: 10 requests/min per IP (enforced by Nginx + FastAPI)
    - Payload limit: 10KB max (enforced by Nginx + FastAPI)

    Args:
        request: FastAPI request object (for IP logging)
        report: CSP violation report from browser

    Returns:
        JSON response acknowledging receipt

    Raises:
        HTTPException: 413 if payload exceeds 10KB limit
    """
    # App-level payload size guard (defense-in-depth)
    # Codex Review Issue #4: Don't rely solely on Nginx
    content_length = request.headers.get("content-length", "0")
    if int(content_length) > 10240:  # 10KB limit
        logger.warning(
            "CSP report rejected: payload too large",
            extra={
                "content_length": content_length,
                "client_ip": request.client.host if request.client else "unknown",
            },
        )
        raise HTTPException(status_code=413, detail="Payload too large")

    violation = report.csp_report

    # Extract client IP for logging
    client_ip = request.client.host if request.client else "unknown"

    # Log CSP violation with structured logging
    logger.warning(
        "CSP violation reported",
        extra={
            "client_ip": client_ip,
            "document_uri": violation.document_uri,
            "violated_directive": violation.violated_directive,
            "effective_directive": violation.effective_directive,
            "blocked_uri": violation.blocked_uri,
            "source_file": violation.source_file,
            "line_number": violation.line_number,
            "sample": violation.sample,
        },
    )

    # Check for common attack patterns
    if "javascript:" in violation.blocked_uri.lower():
        logger.error(
            "POSSIBLE XSS ATTACK: javascript: URI blocked by CSP",
            extra={
                "client_ip": client_ip,
                "blocked_uri": violation.blocked_uri,
                "document_uri": violation.document_uri,
            },
        )

    if "data:" in violation.blocked_uri.lower() and "script-src" in violation.violated_directive:
        logger.error(
            "POSSIBLE XSS ATTACK: data: URI script blocked by CSP",
            extra={
                "client_ip": client_ip,
                "blocked_uri": violation.blocked_uri,
                "document_uri": violation.document_uri,
            },
        )

    return {"status": "received", "message": "CSP violation logged"}
```

**Integration with auth_service:**

```python
# File: apps/auth_service/main.py
# Add CSP report router

from apps.auth_service.routes import callback, logout, refresh, csp_report

# ... existing code ...

# Include routers
app.include_router(callback.router, tags=["auth"])
app.include_router(refresh.router, tags=["auth"])
app.include_router(logout.router, tags=["auth"])
app.include_router(csp_report.router, tags=["security"])  # NEW
```

**Testing:**

**Unit Tests:** `tests/apps/auth_service/routes/test_csp_report.py`

```python
"""Tests for CSP violation reporting endpoint."""

import pytest
from fastapi.testclient import TestClient

from apps.auth_service.main import app


@pytest.fixture
def client():
    """Create test client."""
    return TestClient(app)


def test_csp_report_endpoint_accepts_valid_report(client):
    """Test /csp-report accepts valid CSP violation report."""
    violation_report = {
        "csp-report": {
            "document-uri": "https://localhost/dashboard",
            "violated-directive": "script-src 'self'",
            "effective-directive": "script-src",
            "original-policy": "default-src 'self'; script-src 'self' 'nonce-abc123'",
            "blocked-uri": "https://evil.com/malicious.js",
            "status-code": 200,
            "referrer": "",
            "source-file": "https://localhost/dashboard",
            "line-number": 42,
            "column-number": 15,
            "sample": "<script src='https://evil.com/malicious.js'></script>",
        }
    }

    response = client.post("/csp-report", json=violation_report)

    assert response.status_code == 200
    assert response.json()["status"] == "received"


def test_csp_report_endpoint_logs_xss_attempts(client, caplog):
    """Test /csp-report logs potential XSS attacks."""
    violation_report = {
        "csp-report": {
            "document-uri": "https://localhost/dashboard",
            "violated-directive": "script-src 'self'",
            "effective-directive": "script-src",
            "original-policy": "default-src 'self'; script-src 'self'",
            "blocked-uri": "javascript:alert(1)",  # XSS attempt
            "status-code": 200,
        }
    }

    response = client.post("/csp-report", json=violation_report)

    assert response.status_code == 200

    # Check logs for XSS warning
    assert any("POSSIBLE XSS ATTACK" in record.message for record in caplog.records)


def test_csp_report_endpoint_rejects_invalid_format(client):
    """Test /csp-report rejects invalid report format."""
    invalid_report = {
        "invalid-key": {
            "document-uri": "https://localhost/dashboard",
        }
    }

    response = client.post("/csp-report", json=invalid_report)

    # Should return 422 Unprocessable Entity (validation error)
    assert response.status_code == 422


def test_csp_report_endpoint_app_level_payload_size_check(client):
    """Test /csp-report app-level payload size guard.

    Addresses Codex Iteration 2 Issue #4: App-level defense-in-depth.
    """
    # Create payload exceeding 10KB
    large_payload = {
        "csp-report": {
            "document-uri": "https://localhost/dashboard",
            "violated-directive": "script-src 'self'",
            "effective-directive": "script-src",
            "original-policy": "default-src 'self'",
            "blocked-uri": "https://evil.com/script.js",
            "status-code": 200,
            "sample": "x" * 15000,  # 15KB sample (exceeds 10KB limit)
        }
    }

    # Mock Content-Length header
    response = client.post(
        "/csp-report",
        json=large_payload,
        headers={"Content-Length": "15360"},  # 15KB
    )

    # Should return 413 Payload Too Large (app-level check)
    assert response.status_code == 413
    assert "too large" in response.json()["detail"].lower()
```

---

### Deliverable 5: Streamlit CSP Integration (3 hours)

**Purpose:** Extend CSP coverage to Streamlit pages (Codex Review Issue #2)

**Background:** CSP middleware (Deliverable 1) only covers auth_service responses. Streamlit pages (where most JavaScript runs) currently lack CSP protection.

**Approach:** Streamlit doesn't support custom middleware, so we use Nginx to inject CSP headers for Streamlit responses.

**CRITICAL LIMITATION - Accepted Residual Risk (Codex Iteration 2 Issue #2):**

**Problem:** Streamlit architecture REQUIRES `unsafe-inline` for scripts (cannot use nonce-based CSP).

**Why:**
- Streamlit generates inline scripts dynamically on client side
- No server-side template rendering (React-based SPA architecture)
- No mechanism to inject nonces into Streamlit-generated `<script>` tags
- Streamlit's internal JavaScript framework depends on inline event handlers

**Accepted Risk:**
- XSS attacks via inline scripts remain possible on Streamlit pages
- CSP provides PARTIAL protection (allowlist blocks external scripts)
- Auth_service pages use nonce-based CSP (FULL protection)

**Mitigation Strategy:**
1. **Report-Only Mode (Initial Rollout):**
   - Deploy with `Content-Security-Policy-Report-Only` first
   - Monitor violation reports for 1-2 weeks
   - Verify no legitimate Streamlit functionality blocked
   - Switch to enforcement mode after validation

2. **Monitoring Plan:**
   - Alert on CSP violation rate >10/min (possible attack)
   - Weekly review of CSP violation reports
   - Track violation sources (external domains, inline scripts)
   - Investigate suspicious patterns (javascript: URIs, data: scripts)

3. **Rollback Steps (If CSP Breaks Streamlit):**
   - Switch back to report-only mode: `CSP_REPORT_ONLY=true`
   - Adjust allowlist to include missing CDN domains
   - Re-test Streamlit functionality (dashboard, charts, forms)
   - Document required domains in CSP policy comments

4. **Future Enhancement (Out of Scope for Component 5):**
   - Custom Streamlit component with nonce injection support
   - Replace Streamlit with FastAPI + Jinja2 templates (nonce-based CSP)
   - Evaluate alternative dashboarding frameworks (Dash, Panel)

**Success Criteria Update (Codex Iteration 2 Issue #2):**
- ✅ CSP headers cover Streamlit pages (baseline protection)
- ❌ Nonce-based CSP for Streamlit (NOT POSSIBLE - documented limitation)
- ✅ Report-only mode available for safe rollout
- ✅ Monitoring plan defined for CSP violations
- ✅ Rollback procedure documented

**File:** `apps/web_console/nginx/nginx-oauth2.conf.template` (Codex Iteration 5 Issue #1 - template, not final config)

**Changes:** Add CSP headers to Streamlit proxy locations (`/` and `/_stcore/stream`) using envsubst toggle syntax

**CSP Allowlist Rationale (Codex Iteration 2 Issue #3):**

Each CSP directive is required for specific Streamlit or Auth0 functionality:

| Directive | Value | Why Required |
|-----------|-------|-------------|
| `default-src` | `'self'` | Baseline: only load resources from same origin |
| `script-src` | `'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdn.plot.ly` | **'unsafe-inline'**: Streamlit architecture (no nonce support)<br>**cdn.jsdelivr.net**: Streamlit assets<br>**cdn.plot.ly**: Plotly chart library |
| `style-src` | `'self' 'unsafe-inline' https://fonts.googleapis.com` | **'unsafe-inline'**: Streamlit inline styles<br>**fonts.googleapis.com**: Google Fonts CSS |
| `font-src` | `'self' https://fonts.gstatic.com` | **fonts.gstatic.com**: Google Fonts files (.woff2) |
| `img-src` | `'self' data: blob: https:` | **data:**: Streamlit inline images<br>**blob:**: Streamlit blob URLs (charts)<br>**https:**: External images (charts, logos) |
| `connect-src` | `'self' wss: https://*.trading-platform.local` | **wss:**: Streamlit WebSocket (`/_stcore/stream`)<br>**\*.trading-platform.local**: Backend API calls |
| `frame-src` | `'self'` | Prevent clickjacking (only allow same-origin iframes) |
| `base-uri` | `'self'` | Prevent `<base>` tag injection attacks |
| `form-action` | `'self'` | Only allow form submissions to same origin |
| `report-uri` | `/csp-report` | Send violation reports to monitoring endpoint |

**Report-Only Mode Toggle (Codex Iteration 4 Issue #1 - envsubst approach):**

Nginx supports report-only mode via environment variable using `envsubst` templating:

```bash
# docker-compose.yml environment variable
CSP_REPORT_ONLY=true  # Start with report-only (Header: Content-Security-Policy-Report-Only)
CSP_REPORT_ONLY=false # Switch to enforcement (Header: Content-Security-Policy)
```

**Implementation Approach:**

Since Nginx doesn't support native environment variable substitution in `add_header` directives, we use `envsubst` preprocessing:

1. **Create template file:** `apps/web_console/nginx/nginx-oauth2.conf.template`
2. **Use envsubst syntax:** `${CSP_REPORT_ONLY}` placeholder in template
3. **Preprocess on startup:** Docker entrypoint runs `envsubst` before Nginx starts
4. **Result:** Dynamic CSP header name based on environment variable

**Template File Changes:**

```nginx
# File: apps/web_console/nginx/nginx-oauth2.conf.template
# (Rename from nginx-oauth2.conf, add .template extension)
#
# Codex Iteration 4 Issue #1: Use envsubst for CSP toggle instead of invalid map syntax

        # Default location (Streamlit app)
        location / {
            # ... existing rate limiting ...

            # Proxy configuration
            proxy_pass http://web_console;
            proxy_http_version 1.1;

            # ... existing headers ...

            # CSP header for Streamlit pages (Component 5 - Streamlit CSP Integration)
            # Codex Iteration 4: Use envsubst to substitute CSP_REPORT_ONLY env var
            # - If CSP_REPORT_ONLY=true → Header: "Content-Security-Policy-Report-Only"
            # - If CSP_REPORT_ONLY=false → Header: "Content-Security-Policy" (enforcement)
            # LIMITATION: 'unsafe-inline' required for Streamlit (no nonce support)
            # ROLLBACK: Set CSP_REPORT_ONLY=true if Streamlit breaks
            add_header Content-Security-Policy${CSP_REPORT_ONLY:+-Report-Only} "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdn.plot.ly; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data: blob: https:; connect-src 'self' wss: https://*.trading-platform.local; frame-src 'self'; base-uri 'self'; form-action 'self'; report-uri /csp-report;" always;

            # Timeouts
            # ... existing timeouts ...
        }

        # WebSocket-specific location for Streamlit streaming
        location /_stcore/stream {
            # ... existing rate limiting ...

            # Proxy configuration
            proxy_pass http://web_console;
            proxy_http_version 1.1;

            # ... existing headers ...

            # CSP header for Streamlit WebSocket (same policy as /)
            # Codex Iteration 4: Use envsubst for CSP toggle
            add_header Content-Security-Policy${CSP_REPORT_ONLY:+-Report-Only} "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdn.plot.ly; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data: blob: https:; connect-src 'self' wss: https://*.trading-platform.local; frame-src 'self'; base-uri 'self'; form-action 'self'; report-uri /csp-report;" always;

            # WebSocket timeouts
            # ... existing timeouts ...
        }
```

**Docker Entrypoint for envsubst Preprocessing:**

```bash
# File: apps/web_console/nginx/entrypoint.sh
#!/bin/bash
# Nginx entrypoint with envsubst preprocessing
# Codex Iteration 4 Issue #1: Process .template file to enable CSP toggle

set -e

# Default CSP_REPORT_ONLY to "false" if not set
export CSP_REPORT_ONLY="${CSP_REPORT_ONLY:-false}"

# Log configuration mode
echo "CSP Configuration: CSP_REPORT_ONLY=${CSP_REPORT_ONLY}"
if [ "$CSP_REPORT_ONLY" = "true" ]; then
    echo "CSP Mode: Report-Only (violations logged, not blocked)"
else
    echo "CSP Mode: Enforcement (violations blocked)"
fi

# Process template with envsubst
# Only substitute CSP_REPORT_ONLY (preserve other $ variables like $host, $request_uri)
envsubst '${CSP_REPORT_ONLY}' < /etc/nginx/conf.d/nginx-oauth2.conf.template > /etc/nginx/conf.d/nginx-oauth2.conf

# Validate Nginx configuration
nginx -t

# Start Nginx in foreground
exec nginx -g 'daemon off;'
```

**Docker Compose Wiring:**

```yaml
# File: docker-compose.yml (oauth2 profile)
services:
  nginx_oauth2:
    build:
      context: apps/web_console/nginx
      dockerfile: Dockerfile.oauth2
    container_name: nginx_oauth2
    environment:
      - CSP_REPORT_ONLY=false  # Set to "true" for report-only mode
    volumes:
      - ./apps/web_console/nginx/nginx-oauth2.conf.template:/etc/nginx/conf.d/nginx-oauth2.conf.template:ro
      - ./apps/web_console/nginx/entrypoint.sh:/entrypoint.sh:ro
      # ... other volumes ...
    entrypoint: ["/entrypoint.sh"]
    # ... other config ...
```

**How It Works:**

1. **Startup:** Docker runs `entrypoint.sh` when container starts
2. **Substitution:** `envsubst` replaces `${CSP_REPORT_ONLY}` in template with environment variable value
3. **Toggle Logic:**
   - If `CSP_REPORT_ONLY=true`: `${CSP_REPORT_ONLY:+-Report-Only}` → `-Report-Only`
   - If `CSP_REPORT_ONLY=false` or unset: `${CSP_REPORT_ONLY:+-Report-Only}` → empty string
4. **Result:**
   - Report-only: `add_header Content-Security-Policy-Report-Only "..."`
   - Enforcement: `add_header Content-Security-Policy "..."`
5. **Validation:** `nginx -t` checks config before starting
6. **Execution:** Nginx starts with processed configuration

**Limitations:**
- Streamlit doesn't support nonce-based CSP (requires `unsafe-inline` for scripts)
- Future enhancement: Custom Streamlit component with nonce injection
- Current CSP still provides significant XSS protection via allowlist

**Testing:**

**Integration Tests:** `tests/integration/test_streamlit_csp.py`

```python
"""Integration tests for Streamlit CSP coverage.

Addresses Codex Review Issue #2: CSP must cover Streamlit pages.
"""

import pytest
import requests


@pytest.fixture
def nginx_base_url():
    """Base URL for Nginx reverse proxy."""
    return "https://localhost:443"


def test_streamlit_homepage_has_csp_header(nginx_base_url):
    """Test Streamlit homepage includes CSP header."""
    response = requests.get(
        f"{nginx_base_url}/",
        verify=False,
    )

    assert response.status_code == 200
    assert "Content-Security-Policy" in response.headers

    csp_header = response.headers["Content-Security-Policy"]

    # Verify CSP includes Streamlit requirements
    assert "wss:" in csp_header  # WebSocket support
    assert "blob:" in csp_header  # Blob URLs
    assert "data:" in csp_header  # Data URLs
    assert "report-uri /csp-report" in csp_header


def test_streamlit_websocket_has_csp_header(nginx_base_url):
    """Test Streamlit WebSocket endpoint includes CSP header."""
    # Note: WebSocket upgrade requires special handling
    # This test verifies CSP header is present in initial HTTP response

    response = requests.get(
        f"{nginx_base_url}/_stcore/stream",
        verify=False,
        headers={
            "Upgrade": "websocket",
            "Connection": "Upgrade",
        },
    )

    # WebSocket upgrade may return 101 or 426 (browser requirement)
    # CSP header should be present regardless
    assert "Content-Security-Policy" in response.headers


def test_streamlit_csp_includes_cdn_allowlist(nginx_base_url):
    """Test Streamlit CSP includes required CDN domains."""
    response = requests.get(
        f"{nginx_base_url}/",
        verify=False,
    )

    csp_header = response.headers.get("Content-Security-Policy", "")

    # Streamlit dependencies
    assert "cdn.jsdelivr.net" in csp_header  # Streamlit assets
    assert "cdn.plot.ly" in csp_header  # Plotly charts
```

---

### Deliverable 6: Automated CSP Nonce Test (1.5 hours)

**Purpose:** Verify CSP nonce propagation to HTML (Gemini Review Issue #8)

**File:** `tests/integration/test_csp_nonce_propagation.py`

**Implementation:**

```python
"""Integration tests for CSP nonce propagation to HTML.

Verifies that CSP nonces in headers match nonce attributes in HTML <script> tags.
Addresses Gemini Review Issue #8: Need automated test for nonce propagation.

NOTE: Requires docker-compose --profile oauth2 up -d
"""

import re
import pytest
import requests
from bs4 import BeautifulSoup


@pytest.fixture
def nginx_base_url():
    """Base URL for Nginx reverse proxy."""
    return "https://localhost:443"


def test_login_page_script_tags_have_nonce(nginx_base_url):
    """Test /login page HTML includes nonce attributes in script tags."""
    response = requests.get(
        f"{nginx_base_url}/login",
        verify=False,
        allow_redirects=False,
    )

    # If /login redirects to Auth0, we can't test HTML nonce
    # This test assumes /login serves HTML (auth_service FastAPI template)
    if response.status_code == 302:
        pytest.skip("/login redirects to Auth0 (no HTML to test)")

    # Extract CSP header nonce
    csp_header = response.headers.get("Content-Security-Policy", "")
    nonce_pattern = r"'nonce-([a-f0-9]{32})'"
    nonce_match = re.search(nonce_pattern, csp_header)

    assert nonce_match, "CSP header should contain nonce"
    header_nonce = nonce_match.group(1)

    # Parse HTML
    soup = BeautifulSoup(response.text, "html.parser")
    script_tags = soup.find_all("script")

    # Verify at least one script tag exists with nonce
    script_nonces = [tag.get("nonce") for tag in script_tags if tag.get("nonce")]

    assert len(script_nonces) > 0, "At least one script tag should have nonce attribute"

    # Verify nonce in HTML matches nonce in CSP header
    assert header_nonce in script_nonces, "Script tag nonce should match CSP header nonce"


def test_callback_page_script_tags_have_nonce(nginx_base_url):
    """Test /callback page HTML includes nonce attributes (if applicable)."""
    # /callback may not serve HTML (typically redirects)
    # This test is informational only

    response = requests.get(
        f"{nginx_base_url}/callback",
        verify=False,
        allow_redirects=False,
    )

    if response.status_code != 200:
        pytest.skip("/callback does not serve HTML")

    # Extract CSP header
    csp_header = response.headers.get("Content-Security-Policy", "")

    if not csp_header:
        pytest.skip("/callback does not include CSP header")

    # Extract nonce
    nonce_pattern = r"'nonce-([a-f0-9]{32})'"
    nonce_match = re.search(nonce_pattern, csp_header)

    if not nonce_match:
        pytest.skip("/callback CSP header does not include nonce")

    header_nonce = nonce_match.group(1)

    # Parse HTML
    soup = BeautifulSoup(response.text, "html.parser")
    script_tags = soup.find_all("script")

    # If script tags exist, verify nonce
    script_nonces = [tag.get("nonce") for tag in script_tags if tag.get("nonce")]

    if len(script_nonces) > 0:
        assert header_nonce in script_nonces


def test_streamlit_pages_csp_nonce_absence_expected(nginx_base_url):
    """Test Streamlit pages do not use nonce (unsafe-inline required).

    Streamlit does not support nonce-based CSP (uses inline scripts).
    This test documents expected behavior.

    Future enhancement: Custom Streamlit component with nonce injection.
    """
    response = requests.get(
        f"{nginx_base_url}/",
        verify=False,
    )

    csp_header = response.headers.get("Content-Security-Policy", "")

    # Streamlit CSP should use unsafe-inline (not nonce)
    assert "'unsafe-inline'" in csp_header
    assert "'nonce-" not in csp_header  # No nonce for Streamlit pages
```

**Dependencies:**

```bash
# Add to requirements.txt (if not already present)
beautifulsoup4>=4.12.0
```

---

## Files Summary

### Files to Create

1. **`apps/auth_service/middleware/__init__.py`** - Package marker
2. **`apps/auth_service/middleware/csp_middleware.py`** - CSP header generation with nonces
3. **`apps/auth_service/routes/csp_report.py`** - CSP violation logging endpoint
4. **`apps/web_console/nginx/nginx-oauth2.conf.template`** - Nginx config template with `${CSP_REPORT_ONLY}` placeholders (Codex Iteration 5 Issue #1)
5. **`apps/web_console/nginx/entrypoint.sh`** - Bash script that runs envsubst + nginx (Codex Iteration 5 Issue #1)
6. **`tests/apps/auth_service/middleware/__init__.py`** - Test package marker
7. **`tests/apps/auth_service/middleware/test_csp_middleware.py`** - CSP middleware tests
8. **`tests/apps/auth_service/routes/test_csp_report.py`** - CSP reporting endpoint tests
9. **`tests/integration/test_csp_enforcement.py`** - CSP integration tests
10. **`tests/integration/test_nginx_oauth2_routing.py`** - Nginx routing tests
11. **`tests/integration/test_streamlit_csp.py`** - Streamlit CSP coverage tests (NEW)
12. **`tests/integration/test_csp_nonce_propagation.py`** - CSP nonce HTML validation (NEW)

### Files to Modify

1. **`apps/auth_service/main.py`**
   - Add CSP middleware (after app creation, before routes)
   - Add CSP report router

2. **`docker-compose.yml`** (Codex Iteration 5 Issue #1)
   - Add entrypoint: ["/entrypoint.sh"] to nginx_oauth2 service
   - Add volume mount for nginx-oauth2.conf.template
   - Add volume mount for entrypoint.sh
   - Add `CSP_REPORT_ONLY=false` to nginx_oauth2 environment
   - Add `TRUSTED_PROXY_IPS=172.28.0.10` to auth_service environment
   - Add `TRUSTED_PROXY_IPS=172.28.0.10` to web_console_oauth2 environment
   - Add `CSP_REPORT_ONLY=false` to auth_service environment (Codex Iteration 2 Issue #6)
   - Ensure nginx_oauth2 has static IP 172.28.0.10 (already configured)

**NOTE (Codex Iteration 5 Issue #1):**
- `apps/web_console/nginx/nginx-oauth2.conf` is **generated at container start**, not edited manually
- Template file (`nginx-oauth2.conf.template`) contains `${CSP_REPORT_ONLY}` placeholders
- `entrypoint.sh` runs envsubst to generate final `nginx-oauth2.conf`
- Nginx loads the generated config (not the template)

3. **`apps/web_console/utils.py`**
   - Add `validate_trusted_proxy()` function
   - Update `extract_client_ip_from_fastapi()` to validate trusted proxy

4. **`tests/apps/web_console/test_utils.py`**
   - Add tests for `validate_trusted_proxy()`
   - Add tests for `extract_client_ip_from_fastapi()` with trusted proxy validation

5. **`requirements.txt`** (if needed)
   - Add `beautifulsoup4>=4.12.0` (for nonce propagation tests)

---

## Testing Strategy

### Unit Tests (2.5 hours)

**Coverage Areas:**
1. CSP middleware nonce generation
2. CSP policy string construction with Auth0 CDN + Streamlit requirements
3. Trusted proxy validation logic
4. CSP violation report parsing
5. Client IP extraction with proxy validation

**Test Files:**
- `tests/apps/auth_service/middleware/test_csp_middleware.py`
- `tests/apps/auth_service/routes/test_csp_report.py`
- `tests/apps/web_console/test_utils.py`

**Key Test Cases:**
- CSP middleware adds header with unique nonce per request
- CSP middleware supports report-only mode
- CSP policy includes Auth0 domain AND cdn.auth0.com
- CSP policy includes Streamlit requirements (wss:, blob:, data:)
- CSP policy blocks unsafe-inline for scripts (auth_service)
- Trusted proxy validation allows trusted IPs
- Trusted proxy validation blocks untrusted IPs
- Client IP extraction uses X-Forwarded-For from trusted proxy only
- CSP violation reports parsed correctly
- XSS attempts logged with ERROR level

### Integration Tests (3 hours)

**Coverage Areas:**
1. Nginx routing to auth_service
2. Nginx real_ip validation
3. CSP headers present in responses (auth_service AND Streamlit)
4. CSP report rate limiting
5. CSP report payload size limiting
6. End-to-end OAuth2 flow through Nginx
7. CSP violation reporting flow
8. CSP nonce propagation to HTML

**Test Files:**
- `tests/integration/test_nginx_oauth2_routing.py`
- `tests/integration/test_csp_enforcement.py`
- `tests/integration/test_streamlit_csp.py` (NEW)
- `tests/integration/test_csp_nonce_propagation.py` (NEW)

**Key Test Cases:**
- Nginx routes `/login` to auth_service (302 to Auth0)
- Nginx routes `/callback` to auth_service (422 without params)
- Nginx routes `/refresh` to auth_service
- Nginx routes `/logout` to auth_service
- Nginx routes `/csp-report` to auth_service
- Nginx rate limits `/csp-report` (429 after 10/min)
- Nginx rejects large `/csp-report` payloads (413 for >10KB)
- CSP header present on all auth_service responses
- CSP header present on Streamlit homepage (/)
- CSP header present on Streamlit WebSocket (/_stcore/stream)
- CSP header includes nonce (auth_service)
- CSP header includes Auth0 domain + CDN
- CSP header includes Streamlit requirements (wss:, blob:, data:)
- Full OAuth2 flow works through Nginx
- HTML script tags include nonce matching CSP header (auth_service)

### E2E Tests (Manual - 0.5 hours)

**Test Harness (Codex Review Issue #6):**

```bash
# Start full OAuth2 stack
docker-compose --profile oauth2 up -d

# Verify services
docker ps | grep -E "(nginx_oauth2|auth_service|web_console_oauth2)"

# Check logs
docker logs trading_platform_nginx_oauth2
docker logs trading_platform_auth_service
docker logs trading_platform_web_console_oauth2
```

**Test Scenarios:**

1. **Happy Path:**
   - Access https://localhost/ → redirects to login
   - Click "Login with Auth0" → redirects to Auth0
   - Authenticate at Auth0 → redirects to /callback
   - Callback processes → redirects to dashboard
   - Dashboard displays with session status

2. **CSP Enforcement:**
   - Open browser DevTools → Console tab
   - Attempt to inject inline script via console: `eval("alert(1)")`
   - Verify CSP blocks execution
   - Check /csp-report endpoint for violation log

3. **Trusted Proxy:**
   - Attempt direct access to auth_service (bypass Nginx)
   - Verify 403 Forbidden (not from trusted proxy)

4. **Rate Limiting:**
   - Send 15 /csp-report requests rapidly
   - Verify 429 Too Many Requests after 10 requests

---

## Security Considerations

### CSP Policy

**Threat: XSS via inline scripts**
- **Mitigation:** Nonce-based script-src blocks all inline scripts without nonce (auth_service)
- **Limitation:** Streamlit requires unsafe-inline (lacks nonce support)
- **Future:** Replace Streamlit inline scripts with nonce-based custom component

**Threat: Script injection from untrusted domains**
- **Mitigation:** script-src limited to 'self', Auth0 domains (wildcard + CDN), Streamlit CDNs
- **Monitoring:** CSP violation reports log blocked scripts

**Threat: Clickjacking via iframes**
- **Mitigation:** X-Frame-Options: DENY header
- **Mitigation:** frame-ancestors 'self' in CSP (future enhancement)

### Trusted Proxy Validation

**Threat: X-Forwarded-For header spoofing**
- **Mitigation (Primary):** Nginx `real_ip_from 172.28.0.0/24` directive validates proxy subnet
- **Mitigation (Secondary):** Application-level validation in `extract_client_ip_from_fastapi()`
- **Impact:** Prevents session binding bypass via IP spoofing

**Threat: Direct access to internal services**
- **Mitigation:** Docker network isolation (auth_service no exposed ports)
- **Mitigation:** Nginx as single entry point
- **Impact:** Internal services not accessible from internet

### Nginx Rate Limiting

**Threat: Brute force login attempts**
- **Mitigation:** auth_limit zone (10 requests/min for /login, /callback)
- **Mitigation:** Connection-level limits (30 concurrent per IP)
- **Impact:** Slows down credential stuffing attacks

**Threat: CSP report endpoint abuse**
- **Mitigation:** csp_report_limit zone (10 requests/min)
- **Mitigation:** Payload size limit (10KB max)
- **Impact:** Prevents CSP report flooding DoS

**Threat: DoS via connection exhaustion**
- **Mitigation:** Connection-level rate limiting (conn_limit)
- **Mitigation:** Pre-auth rate limiting (preauth_limit)
- **Impact:** Protects against TLS handshake DoS

---

## Operational Considerations

### Environment Variables

**New Variables (Component 5 - Codex Iteration 2):**
```bash
# TRUSTED_PROXY_IPS: Comma-separated list of trusted proxy IPs
# Required for X-Forwarded-For validation (Nginx + app-level)
# Value: Static IP assigned to nginx_oauth2 service in docker-compose.yml
TRUSTED_PROXY_IPS=172.28.0.10

# CSP_REPORT_ONLY: Enable CSP report-only mode (safe rollout)
# Codex Iteration 2 Issue #6: Wired into FastAPI middleware
# Recommended rollout: Start with true, monitor violations, switch to false
# Values: true (report-only), false (enforcement mode)
# Default: false
CSP_REPORT_ONLY=false

# Rollout Strategy:
# 1. Deploy with CSP_REPORT_ONLY=true (1-2 weeks monitoring)
# 2. Review CSP violation reports
# 3. Adjust allowlist if needed
# 4. Switch to CSP_REPORT_ONLY=false (enforcement)
```

**Existing Variables (Components 1-4):**
```bash
AUTH0_DOMAIN=dev-xyz.us.auth0.com
AUTH0_CLIENT_ID=...
AUTH0_CLIENT_SECRET=...
SESSION_ENCRYPTION_KEY=...
REDIS_HOST=redis
REDIS_PORT=6379
```

### Nginx Configuration

**Static IP Assignment:**
- Nginx service: 172.28.0.10 (already configured in docker-compose.yml)
- Required for TRUSTED_PROXY_IPS validation

**Certificate Management:**
- Self-signed certs for development (apps/web_console/certs/)
- Production: Use Let's Encrypt or commercial CA
- Renewal process documented in docs/RUNBOOKS/web-console-oauth2-setup.md

### Monitoring

**CSP Violation Logs:**
- Location: auth_service structured logs
- Format: JSON with violation details
- Alerts: Set up Prometheus alert for high violation rate (>10/min)

**Nginx Access Logs:**
- Location: /var/log/nginx/access.log
- Format: oauth2 log format (includes X-Forwarded-User)
- Rotation: logrotate configured (weekly, 4 weeks retention)

**Rate Limiting:**
- Monitor 429 responses (rate limit exceeded)
- Alert if 429 rate > 5% of total requests

---

## Success Criteria

**Functional:**
- [ ] CSP headers present on all auth_service responses
- [ ] CSP headers present on all Streamlit responses (/, /_stcore/stream)
- [ ] CSP nonces unique per request (32-character hex)
- [ ] CSP allowlists include Auth0 CDN (cdn.auth0.com)
- [ ] CSP allowlists include Streamlit requirements (wss:, blob:, data:)
- [ ] Nginx routes `/login`, `/callback`, `/refresh`, `/logout` to auth_service
- [ ] Nginx routes `/csp-report` to auth_service with rate limiting
- [ ] Nginx routes `/` and `/_stcore/stream` to web_console with CSP headers
- [ ] Nginx validates trusted proxy via `real_ip_from` directive
- [ ] Trusted proxy validation blocks untrusted IPs (application-level)
- [ ] Trusted proxy validation allows Nginx (172.28.0.10)
- [ ] CSP violation reports logged to structured logging
- [ ] CSP violation reports rate limited (10/min, 10KB max)
- [ ] Automated test verifies CSP nonce in HTML

**Security:**
- [ ] CSP blocks inline scripts without valid nonce (auth_service)
- [ ] CSP allows nonce-based scripts (Streamlit compatibility)
- [ ] CSP allowlist complete (Auth0 CDN + Streamlit CDNs)
- [ ] X-Forwarded-For only accepted from trusted proxy (Nginx + app level)
- [ ] Direct access to auth_service blocked (no exposed ports)
- [ ] Rate limiting enforced on auth endpoints (10/min)
- [ ] Rate limiting enforced on /csp-report (10/min, 10KB max)

**Testing:**
- [ ] All unit tests pass (csp_middleware, csp_report, utils)
- [ ] All integration tests pass (nginx routing, CSP enforcement, Streamlit CSP, nonce propagation)
- [ ] E2E OAuth2 flow works through Nginx
- [ ] Manual CSP testing confirms XSS protection

**Documentation:**
- [ ] Nginx config documented with comments
- [ ] CSP policy documented in code comments
- [ ] Trusted proxy validation documented (Nginx + app level)
- [ ] TRUSTED_PROXY_IPS env var documented in docker-compose.yml
- [ ] nginx.conf vs nginx-oauth2.conf.template distinction documented (Codex Iteration 5 Issue #1)

---

## Dependencies

### Python Packages

**Already Installed:**
- `fastapi` - Web framework
- `pydantic` - Data validation (CSP report parsing)
- `starlette` - ASGI framework (middleware)

**New:**
- `beautifulsoup4>=4.12.0` - HTML parsing (nonce propagation tests)

### Infrastructure

- Nginx 1.25+ - Reverse proxy
- Docker Compose 3.9+ - Container orchestration
- OpenSSL 3.0+ - Certificate generation

### Component Dependencies

- Component 1: OAuth2 config, IdP setup ✅
- Component 2: OAuth2 flow, PKCE, session store ✅
- Component 3: Session management, auto-refresh ✅
- Component 4: Streamlit UI, login page ✅

---

## Rollback Plan

**If CSP breaks Streamlit:**
1. Set `CSP_REPORT_ONLY=true` (report violations, don't block)
2. Monitor CSP violation reports
3. Adjust CSP policy to allow Streamlit scripts
4. Re-enable enforcement mode after testing

**If Nginx routing breaks OAuth2:**
1. Switch to `docker-compose --profile dev up -d` (direct access)
2. Debug Nginx config with `docker logs trading_platform_nginx_oauth2`
3. Fix routing, test locally
4. Switch back to `docker-compose --profile oauth2 up -d`

**If trusted proxy validation breaks production:**
1. Set `TRUSTED_PROXY_IPS=""` (disables validation, TEMPORARY)
2. Investigate proxy IP mismatch
3. Update TRUSTED_PROXY_IPS with correct IP
4. Re-enable validation

**If wrong Nginx config modified (Codex Iteration 5 Issue #1):**
1. Restore `nginx.conf` from git (mTLS profile)
2. Verify changes only in `nginx-oauth2.conf.template` (source template, not generated nginx-oauth2.conf)
3. Test mTLS profile: `docker-compose --profile mtls up -d`
4. Test OAuth2 profile: `docker-compose --profile oauth2 up -d`
5. Verify entrypoint.sh properly generates nginx-oauth2.conf from template

---

## Future Enhancements (Out of Scope)

**Phase 4 (After Component 5):**
- Nonce-based style-src (requires Streamlit support)
- Nonce-based script-src for Streamlit (custom component)
- frame-ancestors directive (additional clickjacking protection)
- strict-dynamic for script-src (allowlist-free CSP)
- CSP Level 3 features (hash-based scripts)

**Monitoring:**
- Grafana dashboard for CSP violations
- Prometheus alerts for high violation rate
- CSP violation aggregation and trending

**Multi-Region:**
- Multiple trusted proxy IPs (load balancer)
- Geo-IP based rate limiting
- Regional CSP policies

---

## References

**Standards:**
- [W3C CSP Level 2 Spec](https://www.w3.org/TR/CSP2/)
- [OWASP CSP Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Content_Security_Policy_Cheat_Sheet.html)
- [Mozilla CSP Guidelines](https://developer.mozilla.org/en-US/docs/Web/HTTP/CSP)

**Internal Docs:**
- ADR-015: Auth0 IdP Selection
- Component 2 Plan: OAuth2 Authorization Flow
- Component 3 Plan: Session Management + UX
- Component 4 Plan: Streamlit UI Integration
- Nginx mTLS Config: apps/web_console/nginx/nginx.conf (Component 3 P2T3 Phase 2)
- Nginx OAuth2 Config Template: apps/web_console/nginx/nginx-oauth2.conf.template (THIS COMPONENT - Codex Iteration 5 Issue #1)
- Nginx OAuth2 Entrypoint: apps/web_console/nginx/entrypoint.sh (THIS COMPONENT - Codex Iteration 5 Issue #1)

**Tools:**
- [CSP Evaluator](https://csp-evaluator.withgoogle.com/) - Validate CSP policies
- [Report URI](https://report-uri.com/) - CSP violation monitoring (external service)

---

## Review History

**v1 → v2 Changes (Codex + Gemini Reviews):**

**CRITICAL Issues Fixed:**
1. ✅ Wrong Nginx file target (both reviewers)
   - Changed all references from `nginx.conf` → `nginx-oauth2.conf.template` (Codex Iteration 5 Issue #1)
   - Added prominent warning section about file distinction
   - Updated all code examples and file paths
   - Added envsubst template workflow documentation (Codex Iteration 5 Issue #1)

**HIGH Issues Fixed:**
2. ✅ CSP coverage gap - Streamlit pages (Codex)
   - Added Deliverable 5: Streamlit CSP Integration
   - Added CSP headers to Nginx `/` and `/_stcore/stream` locations
   - Added integration tests for Streamlit CSP coverage

3. ✅ CSP allowlist incomplete (Codex)
   - Added `https://cdn.auth0.com` to script-src
   - Added `wss:` to connect-src (Streamlit WebSockets)
   - Added `blob:` to img-src (Streamlit blob URLs)
   - Updated all CSP policy examples

4. ✅ Nginx proxy validation incomplete (Codex)
   - Added `set_real_ip_from 172.28.0.0/24` directive
   - Added `real_ip_header X-Forwarded-For` directive
   - Added `real_ip_recursive on` directive
   - Updated Nginx config in Deliverable 2

**MEDIUM Issues Fixed:**
5. ✅ CSP reporting security (Codex)
   - Added rate limiting to /csp-report (10/min)
   - Added `client_max_body_size 10k` limit
   - Added integration tests for rate limiting + payload limits

6. ✅ Testing realism (Codex)
   - Added test harness section with docker-compose commands
   - Added E2E test setup instructions
   - Updated all integration test docstrings with prerequisites

7. ✅ Estimate too low (Codex)
   - Updated estimate from 8h → 12-14h (1.5-2 days)
   - Adjusted deliverable estimates (added Streamlit CSP + nonce tests)

8. ✅ Add automated CSP nonce test (Gemini)
   - Added Deliverable 6: Automated CSP Nonce Test
   - Created `test_csp_nonce_propagation.py` with HTML scraping
   - Added beautifulsoup4 dependency

---

## Iteration 3 Changelog (Codex Review - 2025-11-25)

**CRITICAL Issues Fixed:**

1. ✅ **Missing Nonce Template Integration (Issue #1)**
   - Added complete template integration example in Deliverable 1
   - Created `example.html` template showing nonce usage: `<script nonce="{{ csp_nonce }}">`
   - Added FastAPI route example: `templates.TemplateResponse(..., {"csp_nonce": request.state.csp_nonce})`
   - Added unit test `test_csp_middleware_template_rendering_with_nonce()`
   - Updated Deliverable 1 estimate: 2.5h → 4h (+1.5h for template work)

**HIGH Issues Fixed:**

2. ✅ **Streamlit CSP Contradicts Goals (Issue #2)**
   - Added "CRITICAL LIMITATION - Accepted Residual Risk" section
   - Documented WHY Streamlit requires `unsafe-inline` (React SPA, no template rendering)
   - Added accepted risk statement (XSS via inline scripts remains possible)
   - Added 4-part mitigation strategy (report-only rollout, monitoring, rollback, future enhancement)
   - Updated success criteria to clarify limitation (⚠️ symbol for partial protection)
   - Updated Deliverable 5 estimate: 2.5h → 3h (+0.5h for documentation)

3. ✅ **Streamlit CSP Allowlist Rationale (Issue #3)**
   - Added comprehensive table documenting WHY each CSP directive is needed
   - Examples: `wss:` for Streamlit WebSocket, `cdn.jsdelivr.net` for Streamlit assets
   - Added report-only mode toggle configuration via `CSP_REPORT_ONLY` env var
   - Added rollback steps if CSP breaks Streamlit functionality

**MEDIUM Issues Fixed:**

4. ✅ **CSP Report App-Level Guards (Issue #4)**
   - Added app-level payload size check in FastAPI `/csp-report` handler
   - Defense-in-depth: Check `Content-Length` header (10KB limit) before processing
   - Added `HTTPException` import to `csp_report.py`
   - Added unit test `test_csp_report_endpoint_app_level_payload_size_check()`
   - Updated docstring to document multi-layer defense (Nginx + FastAPI)

5. ✅ **Missing Nginx Real IP Negative Test (Issue #5)**
   - Added integration test `test_nginx_blocks_forged_x_forwarded_for()`
   - Tests that Nginx ignores forged `X-Forwarded-For` from untrusted IPs
   - Simulates attacker sending spoofed header from external IP
   - Verifies `set_real_ip_from` enforcement

6. ✅ **CSP_REPORT_ONLY Toggle Wiring (Issue #6)**
   - Wired `CSP_REPORT_ONLY` env var into FastAPI middleware initialization
   - Added `enable_report_only = os.getenv("CSP_REPORT_ONLY", "false").lower() == "true"`
   - Added to `docker-compose.yml` modifications list
   - Added rollout strategy documentation (1-2 weeks report-only → enforcement)

**Overall Estimate Update:**
- Original (v2): 12-14 hours
- Added: Template integration (+2h), CSP rationale docs (+1h), monitoring plan (+1h)
- **New Total: 14-18 hours (2-2.5 days)**

**Files Updated:**
- Deliverable 1: CSP Middleware + Template Integration (2.5h → 4h)
- Deliverable 4: CSP Report endpoint (added app-level guard)
- Deliverable 5: Streamlit CSP Integration (2.5h → 3h, added limitation docs)
- Deliverable 2: Nginx routing tests (added negative test)
- Success Criteria: Updated to reflect Streamlit limitation
- Environment Variables: Added rollout strategy for `CSP_REPORT_ONLY`
- Overall estimate: 12-14h → 14-18h

---

## Iteration 4 Changelog (Codex Review - 2025-11-25)

**HIGH Issues Fixed:**

1. ✅ **CSP Toggle Map Not Implementable (Issue #1)**
   - **Problem:** Invalid Nginx map syntax `map $csp_report_only "off"` where source variable never set
   - **Root Cause:** CSP headers are set by TWO different components:
     - FastAPI CSPMiddleware → auth endpoints (`/login`, `/callback`, etc.)
     - Nginx add_header → Streamlit routes (`/`, `/_stcore/stream`)
   - **Fix Applied:**
     - Removed invalid map directive from http context (line 751-776)
     - Clarified CSP architecture: FastAPI handles auth, Nginx handles Streamlit
     - FastAPI reads `CSP_REPORT_ONLY` env var directly (no map needed)
     - Nginx uses `envsubst` templating for Streamlit CSP toggle
   - **Implementation (Deliverable 5):**
     - Created `nginx-oauth2.conf.template` with `${CSP_REPORT_ONLY:+-Report-Only}` syntax
     - Added `entrypoint.sh` script to run `envsubst` before Nginx starts
     - Updated docker-compose.yml to mount template + entrypoint
     - Both components now consistently read same environment variable
   - **Documentation:**
     - Added inline comments explaining two-component architecture
     - Added "How It Works" section with 6-step flow
     - Referenced "Codex Iteration 4 Issue #1" in all modified sections
   - **Files Updated:**
     - Line 751-776: Replaced invalid map with CSP Toggle Architecture comments
     - Line 1041-1083: Updated CSP Toggle Mechanism section
     - Line 1942-2073: Added envsubst implementation + Docker entrypoint

**Overall Estimate:**
- No change (14-18 hours) - fix clarifies existing implementation approach

**Files Updated:**
- Deliverable 2: Nginx config (removed invalid map, added architecture comments)
- Deliverable 5: Streamlit CSP Integration (added envsubst templating, Docker entrypoint)
- CSP Toggle Mechanism section: Clarified two-component architecture

---

## Iteration 5 Changelog (Codex Review - 2025-11-25)

**HIGH Issues Fixed:**

1. ✅ **Envsubst Toggle Not Reflected in Files/Tasks List (Issue #1)**
   - **Problem:** Plan uses envsubst with nginx-oauth2.conf.template (Iteration 4), but Files to Create/Modify sections still reference editing nginx-oauth2.conf directly. Missing template and entrypoint.sh from deliverables.
   - **Root Cause:** Iteration 4 added envsubst templating approach but didn't update file inventory sections
   - **Fix Applied:**
     - Updated "Deliverable 2: Nginx Routing" file list (line 675-679):
       - Added CREATE items: nginx-oauth2.conf.template, entrypoint.sh
       - Added MODIFY item: docker-compose.yml (entrypoint, volumes, env var)
       - Added note: nginx-oauth2.conf is generated, not edited manually
     - Updated "Files Summary → Files to Create" (line 2303-2304):
       - Added nginx-oauth2.conf.template as item #4
       - Added entrypoint.sh as item #5
     - Updated "Files Summary → Files to Modify" (line 2319-2327):
       - Moved docker-compose.yml to position #2 (was #3)
       - Added entrypoint, volume mount, CSP_REPORT_ONLY env var details
       - Added NOTE section explaining template workflow
     - Updated "⚠️ CRITICAL: Nginx Config File Distinction" section (line 29-59):
       - Changed from "modifies nginx-oauth2.conf" → "creates template + entrypoint"
       - Added 4-step file workflow documentation
       - Updated all references to target template, not final config
     - Updated section headings (line 681, 1931):
       - Changed "File: nginx-oauth2.conf" → "File: nginx-oauth2.conf.template"
       - Added Codex Iteration 5 Issue #1 reference
     - Updated Nginx config code block header (line 694-695):
       - Changed comment from "nginx-oauth2.conf (OAuth2 profile)" → "nginx-oauth2.conf.template (OAuth2 profile template)"
       - Added template processing comment
     - Updated References section (line 2707-2708):
       - Changed nginx-oauth2.conf → nginx-oauth2.conf.template
       - Added entrypoint.sh reference
     - Updated Review History (line 2722):
       - Added envsubst template workflow documentation note
     - Updated Rollback Plan (line 2656):
       - Changed "Verify changes only in nginx-oauth2.conf" → "nginx-oauth2.conf.template"
       - Added step to verify entrypoint.sh generation
     - Updated Documentation Checklist (line 2603):
       - Changed "nginx.conf vs nginx-oauth2.conf" → "nginx.conf vs nginx-oauth2.conf.template"
   - **Impact:** Files inventory now accurately reflects template-based approach from Iteration 4
   - **Verification:** All "Files to Create/Modify" sections now consistent with envsubst implementation

**Overall Estimate:**
- No change (14-18 hours) - fix updates documentation to match Iteration 4 implementation

**Files Updated:**
- Line 22-23: References section (added template + entrypoint)
- Line 29-59: CRITICAL section (file workflow documentation)
- Line 675-679: Deliverable 2 file list
- Line 681, 1931: Section headings (nginx-oauth2.conf → .template)
- Line 694-711: Nginx config header comments
- Line 2303-2333: Files Summary (create/modify sections)
- Line 2603: Documentation checklist
- Line 2656-2659: Rollback plan
- Line 2707-2708, 2722: References + Review History

---

**Last Updated:** 2025-11-25
**Author:** Development Team (Component 5 Planning v5)
**Status:** Ready for Implementation (Post Codex Iteration 5 Review)
**Reviewers:** Codex Code Reviewer (Iterations 1-5), Gemini Code Reviewer (Iteration 1)
