# Mutual TLS (mTLS) and JWT Authentication Concepts

**Educational guide for understanding cryptographic authentication in the trading platform web console.**

This document explains the core concepts behind our Phase 2 authentication system, combining Mutual TLS (mTLS) for client identity verification with JSON Web Tokens (JWT) for stateful session management.

---

## Table of Contents

1. [TLS/SSL Fundamentals](#tlsssl-fundamentals)
2. [Public Key Infrastructure (PKI)](#public-key-infrastructure-pki)
3. [Mutual TLS (mTLS)](#mutual-tls-mtls)
4. [JSON Web Tokens (JWT)](#json-web-tokens-jwt)
5. [RS256 vs HS256 Signing](#rs256-vs-hs256-signing)
6. [Session Management](#session-management)
7. [Token Revocation](#token-revocation)
8. [Session Binding](#session-binding)
9. [Fail-Closed Security](#fail-closed-security)
10. [Real-World Example](#real-world-example)

---

## TLS/SSL Fundamentals

### What is TLS?

**Transport Layer Security (TLS)** is a cryptographic protocol that provides secure communication over a network. It's the successor to SSL (Secure Sockets Layer).

### Key Concepts

**Asymmetric Cryptography:**
- **Public Key**: Shared openly, used for encryption or signature verification
- **Private Key**: Kept secret, used for decryption or signing
- **Property**: Data encrypted with public key can only be decrypted with private key

**TLS Handshake (Simplified):**
```
1. Client → Server: "Hello, I want secure connection"
2. Server → Client: "Here's my certificate (contains public key)"
3. Client verifies:   Is certificate valid? Signed by trusted CA?
4. Client generates:  Session key (symmetric encryption key)
5. Client → Server:   Session key encrypted with server's public key
6. Server decrypts:   Session key using private key
7. Both sides:        Use session key for fast symmetric encryption
```

**Why This Works:**
- Server proves identity by possessing private key that matches certificate's public key
- Session key is securely exchanged without eavesdroppers learning it
- Symmetric encryption (AES) used for actual data (much faster than asymmetric)

### One-Way TLS (Standard HTTPS)

In typical HTTPS:
- **Server** proves identity (sends certificate)
- **Client** remains anonymous (no certificate required)
- **Example**: Browsing https://github.com — GitHub proves it's really GitHub, but you don't prove who you are (until you login)

---

## Public Key Infrastructure (PKI)

### Certificate Authority (CA)

A **Certificate Authority** is a trusted entity that issues digital certificates. Think of it like a passport office that verifies your identity before issuing a passport.

**Certificate Contents:**
```
Subject: CN=john.doe, O=Trading Platform, C=US  # Who owns this cert
Issuer: CN=Trading Platform CA                   # Who signed it
Public Key: [4096-bit RSA public key]            # Encryption/verification key
Signature: [CA's signature of above fields]      # Proof of authenticity
Valid From: 2025-01-01                           # Not before
Valid Until: 2026-01-01                          # Expiration
```

**Certificate Chain:**
```
Root CA (self-signed, 10-year validity)
  └── Server Certificate (signed by CA, 1-year validity)
  └── Client Certificate (signed by CA, 1-year validity)
```

**Verification Process:**
1. Verifier has Root CA public key (pre-distributed, trusted)
2. Certificate presented contains subject + public key + signature
3. Verifier uses CA public key to verify signature
4. If signature valid → Certificate is authentic → Trust the public key

**Why It Works:**
- CA's private key is heavily guarded (offline, HSM-protected)
- Attacker cannot forge signature without CA's private key
- Compromising one certificate doesn't compromise CA

### Our PKI Design

**Root CA:**
- 4096-bit RSA key (extremely difficult to crack)
- Self-signed (we are our own CA for internal use)
- 10-year validity (rarely rotated)
- Stored offline when not issuing certificates

**Server Certificate:**
- 2048-bit RSA key (industry standard)
- Signed by Root CA
- 1-year validity (annual rotation)
- Subject: CN=trading-platform-web-console
- Used by nginx for TLS termination

**Client Certificates:**
- 2048-bit RSA key
- Signed by Root CA
- 1-year validity (annual rotation)
- Subject: CN=john.doe (unique per user)
- Used by browser/curl to authenticate to server

---

## Mutual TLS (mTLS)

### What is mTLS?

**Mutual TLS** extends standard TLS by requiring **both** client and server to present certificates. Both sides prove their identity cryptographically.

**Comparison:**

| Aspect | Standard TLS | Mutual TLS |
|--------|--------------|------------|
| Server Certificate | Required | Required |
| Client Certificate | Not required | **Required** |
| Server proves identity | Yes | Yes |
| Client proves identity | No (username/password later) | **Yes (cryptographically)** |
| Example Use Case | Public websites | API authentication, internal services |

### mTLS Handshake

```
1. Client → Server: "Hello, let's do TLS"
2. Server → Client: "Here's my certificate"
3. Client verifies:  Server certificate is valid and trusted
4. Server → Client: "Now show me YOUR certificate"  ← Key difference
5. Client → Server: "Here's my certificate"
6. Server verifies:  Client certificate is valid and signed by trusted CA
7. Both sides:       Establish encrypted channel
```

**What Server Verifies:**
- Certificate signature is valid (signed by trusted CA)
- Certificate is within validity period (not expired)
- Certificate is not revoked (via CRL or OCSP)
- Subject DN matches expected pattern (e.g., CN=john.doe)

**What This Proves:**
- Client possesses private key matching the certificate
- Certificate was issued by trusted CA
- Client is who they claim to be (identity in certificate Subject)

### mTLS vs Password Authentication

| Aspect | Password Auth | mTLS |
|--------|---------------|------|
| Secret Type | Shared secret | Private key (never shared) |
| Transmission | Sent with every request | Never transmitted |
| Brute Force | Vulnerable (password guessing) | Impossible (cryptographic security) |
| Phishing | Vulnerable (user enters password on fake site) | Resistant (certificate is cryptographic proof) |
| Revocation | Change password | Revoke certificate via CA |
| Audit Trail | Username only | Full DN (CN, OU, O, C) |

**Why mTLS is Stronger:**
- Private key never leaves client machine
- No password to memorize, steal, or guess
- Cryptographic proof of identity (not just knowledge of secret)
- Certificate contains tamper-proof identity claims (DN fields)

### mTLS in Our Architecture

```
┌──────────┐            ┌────────┐            ┌─────────────┐
│  Browser │──── TLS ───│  Nginx │──── HTTP ──│  Streamlit  │
│  (cert)  │            │ (mTLS) │            │ (web_console)│
└──────────┘            └────────┘            └─────────────┘
     │                       │                       │
     │                       │                       │
  [Client                [Verifies             [Trusts nginx
   proves                 client cert,           forwarded
   identity               extracts DN,           headers:
   with cert]             forwards to            X-SSL-Client-S-DN
                          Streamlit]             X-SSL-Client-Verify]
```

**Why Nginx Layer?**
- Streamlit doesn't support native client certificate verification
- Nginx is battle-tested for TLS termination
- Centralized certificate verification logic
- Rate limiting and request filtering before reaching app

**Header Forwarding:**
```
X-SSL-Client-Verify: SUCCESS | FAILED | NONE
X-SSL-Client-S-DN:   CN=john.doe,O=Trading Platform,C=US
X-SSL-Client-I-DN:   CN=Trading Platform CA
X-SSL-Client-Serial: A3:B2:C1:...
X-SSL-Client-Fingerprint: SHA256:ABC123...
```

**Security Critical:**
- Nginx uses `proxy_set_header` which **OVERWRITES** any incoming headers
- This prevents client from spoofing `X-SSL-Client-S-DN` header
- Streamlit trusts headers **only if** request came from trusted proxy IP
- Empty `TRUSTED_PROXY_IPS` → Fail closed (reject all requests)

---

## JSON Web Tokens (JWT)

### What is a JWT?

A **JSON Web Token** is a compact, URL-safe token that contains claims about an entity (usually a user). It's cryptographically signed to prevent tampering.

**Structure:**
```
<header>.<payload>.<signature>

Example:
eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJDTj1qb2huLmRvZSIsImp0aSI6IjEyMzQ1Njc4IiwiZXhwIjoxNzMyMzQ1Njc4fQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c
```

**Decoded Parts:**

**Header:**
```json
{
  "alg": "RS256",      // Signing algorithm
  "typ": "JWT"         // Token type
}
```

**Payload (Claims):**
```json
{
  "sub": "CN=john.doe,O=Trading Platform",  // Subject (who)
  "jti": "12345678",                        // JWT ID (unique)
  "exp": 1732345678,                        // Expiration (Unix timestamp)
  "iat": 1732344778,                        // Issued at
  "session_binding": "a3f2b1c4d5..."        // Anti-hijacking
}
```

**Signature:**
```
RS256_Sign(
  base64UrlEncode(header) + "." + base64UrlEncode(payload),
  privateKey
)
```

### JWT Verification Process

**Verification Steps:**
1. Split token into header, payload, signature
2. Decode header and payload (base64url decode)
3. Verify signature using public key:
   ```
   expected_signature = RS256_Verify(header + "." + payload, publicKey)
   if (expected_signature != actual_signature):
       reject("Invalid signature - token was tampered")
   ```
4. Check `exp` claim: `if (now > exp): reject("Expired")`
5. Check `jti` is not revoked: `if (redis.exists("revoked:" + jti)): reject("Revoked")`
6. Validate custom claims (e.g., `session_binding`)

**Why This Works:**
- Signature proves token was issued by holder of private key (our server)
- Any modification to payload invalidates signature
- Attacker cannot forge signature without private key
- Token is stateless (no database lookup needed for basic validation)

### Access Token vs Refresh Token

**Why Two Tokens?**

**Access Token (Short-lived, 15 minutes):**
- Sent with every API request
- Contains full claims needed for authorization
- Short expiry limits damage if stolen
- Stored in memory (Streamlit session state)

**Refresh Token (Long-lived, 4 hours):**
- Only used to obtain new access token
- Sent only to `/refresh` endpoint
- Longer expiry reduces login frequency
- More sensitive → Additional validation required

**Token Rotation Flow:**
```
1. Client: "Here's my expired access token + valid refresh token"
2. Server validates refresh token:
   - Signature valid?
   - Not expired?
   - JTI not revoked?
   - Session binding matches?
3. Server atomically:
   - Revoke old refresh token JTI (add to Redis blacklist)
   - Issue new access token (15 min expiry)
   - Issue new refresh token (4 hours from now)
4. Client receives both new tokens
5. Old refresh token immediately invalid (cannot reuse)
```

**Atomic Rotation Guarantees:**
- Old refresh token cannot be reused after successful rotation
- If rotation fails mid-flight, old tokens remain valid
- No race condition where both old and new tokens are valid

**Attack Mitigation:**
- Stolen access token: Limited to 15-minute window
- Stolen refresh token: Must also match session binding (IP + User-Agent)
- Token replay: JTI revocation prevents reuse after rotation

---

## RS256 vs HS256 Signing

### HS256 (Symmetric Signing)

**How It Works:**
```
signature = HMAC-SHA256(header + payload, secret_key)
```

**Verification:**
```
expected = HMAC-SHA256(header + payload, secret_key)
valid = (expected == signature)
```

**Properties:**
- ✅ Fast (HMAC is computationally cheap)
- ✅ Simple (single shared secret)
- ❌ **Anyone with secret can sign tokens** (not just server)
- ❌ Secret must be distributed to all validators
- ❌ Secret rotation requires coordinating all systems

**Security Concern:**
```
If monitoring system needs to validate tokens:
  → Must have secret_key
  → Can now FORGE tokens (same key signs and verifies)
  → Compromise of monitoring system = compromise of auth
```

### RS256 (Asymmetric Signing)

**How It Works:**
```
signature = RSA_Sign(header + payload, private_key)
```

**Verification:**
```
valid = RSA_Verify(header + payload, signature, public_key)
```

**Properties:**
- ✅ **Only private key holder can sign** (server only)
- ✅ Public key can be distributed freely (cannot sign, only verify)
- ✅ Monitoring systems can validate without signing capability
- ✅ Private key rotation doesn't require updating validators
- ❌ Slower than HMAC (RSA operations are expensive)
- ❌ More complex key management (key pair vs single secret)

**Security Advantage:**
```
Monitoring system gets public key:
  → Can verify tokens are legitimate
  → CANNOT forge new tokens
  → Compromise of monitoring system ≠ compromise of auth
```

### Why We Chose RS256

**Requirements:**
1. Future integration with OAuth2/OIDC (industry standard uses RS256)
2. Token verification in multiple services (monitoring, audit, future microservices)
3. Key rotation without service disruption
4. Clear separation: "Who can sign?" (server only) vs "Who can verify?" (anyone)

**Trade-offs:**
- **Accepted**: Slightly slower signing (negligible for our QPS)
- **Gained**: Better security posture, easier scaling, OAuth2 compatibility

**Benchmark:**
- HS256: ~10 µs per sign/verify
- RS256: ~100 µs per sign, ~20 µs per verify
- Our load: <10 tokens/second → Performance difference irrelevant

---

## Session Management

### Session Lifecycle

**Timeline:**
```
Login (0:00)
  │
  ├─ Access Token:  Expires at 0:15 (15 min)
  ├─ Refresh Token: Expires at 4:00 (4 hours)
  ├─ Idle Timeout:  15 minutes of inactivity
  └─ Absolute Timeout: 4 hours since login
```

**Timeout Behaviors:**

**Idle Timeout (15 minutes):**
- Reset on every user interaction
- Prevents unattended browser sessions
- Streamlit tracks `last_activity_time` in session state
- Check on every page load:
  ```python
  if (now - last_activity_time) > 15 * 60:
      logout_user()  # Revoke tokens, clear session
  ```

**Absolute Timeout (4 hours):**
- Not reset by activity
- Forces re-authentication even if actively using
- Prevents indefinite sessions
- Check on every page load:
  ```python
  if (now - login_time) > 4 * 60 * 60:
      logout_user()
  ```

**Why Both Timeouts?**
- **Idle**: Security against unattended workstations
- **Absolute**: Compliance requirement (regular re-authentication)
- **Combined**: "Use it regularly, but re-login daily"

### Session State Storage

**What We Store:**
```python
session_state = {
    "username": "CN=john.doe,O=Trading Platform",
    "access_jti": "a1b2c3d4",           # For revocation
    "refresh_jti": "e5f6g7h8",          # For revocation
    "login_time": 1732340000,           # Absolute timeout
    "last_activity_time": 1732345678,   # Idle timeout
    "session_binding": "hmac_of_ip_ua", # Anti-hijacking
}
```

**Storage Location:**
- **Streamlit session state**: In-memory, per-browser-tab
- **Redis**: Revoked JTI list (shared across all sessions)

**Streamlit Limitation:**
- Session state is not persisted across browser restarts
- Closing browser tab → Session lost, must re-login
- Cannot use HttpOnly cookies (Streamlit doesn't expose HTTP layer)

**Trade-off:**
- ✅ Simplicity: No database schema for sessions
- ❌ UX: Users must re-login after browser close
- 💡 Acceptable: High-security console, not a consumer app

---

## Token Revocation

### The Problem: JWT is Stateless

**JWT Design Philosophy:**
- Self-contained (all claims in token)
- Verifiable without database lookup
- **Cannot be "invalidated" by design**

**Concrete Problem:**
```
1. User logs in at 9:00 AM
2. Server issues access token (expires 9:15 AM)
3. User clicks "Logout" at 9:05 AM
4. Server clears session state
5. Attacker has stolen token copy
6. Attacker uses token at 9:10 AM ← Still valid! 😱
```

**Why This Happens:**
- Token validation only checks: signature + expiration
- Server has no state tracking "this token was revoked"
- Logout only clears client-side state, not server-side

### Solution: JTI Blacklist in Redis

**Design:**
```python
# On logout or timeout:
revoke_token(jti, exp):
    ttl = exp - now()  # Time until natural expiration
    redis.setex(f"revoked_jti:{jti}", ttl, "revoked")

# On every request:
validate_token(token):
    jti = token.payload["jti"]
    if redis.exists(f"revoked_jti:{jti}"):
        raise TokenRevokedError("Token was revoked on logout")
```

**Key Properties:**

**Memory Efficiency:**
- Only revoked JTIs are stored (not all valid JTIs)
- TTL matches token expiration (auto-cleanup)
- Example memory usage:
  ```
  10 users × 2 tokens (access + refresh) × 100 bytes = 2 KB
  Max revoked list size: All users logout at once = 20 KB
  ```

**Performance:**
- Redis `EXISTS` command: O(1), sub-millisecond
- Negligible overhead vs database query
- Scales to millions of tokens

**Automatic Cleanup:**
- Token expires at 9:15 AM
- Redis TTL also set to 9:15 AM
- After 9:15 AM: Redis auto-deletes, no manual cleanup
- No memory leak

**Edge Cases:**

**Redis Unavailable:**
```python
try:
    if redis.exists(f"revoked_jti:{jti}"):
        raise TokenRevokedError()
except RedisConnectionError:
    # Fail-open: Allow request (token might be revoked but we can't verify)
    # Mitigated by short token lifetime (15 min access, 4 hour refresh)
    logger.error("Redis unavailable, cannot verify revocation")
```

**Trade-off:**
- ✅ Immediate revocation (logout takes effect instantly)
- ❌ Redis dependency (failure mode must be acceptable)
- 💡 Mitigation: Short-lived tokens limit exposure window

---

## Session Binding

### Attack: Session Hijacking

**Scenario:**
```
1. Victim logs in from laptop (IP: 192.168.1.100, User-Agent: Chrome/Mac)
2. Server issues tokens, stores in Streamlit session state
3. Attacker steals session cookie (XSS, malware, physical access)
4. Attacker opens browser from different location (IP: 10.0.0.50, User-Agent: Firefox/Windows)
5. Attacker imports stolen cookie
6. Attacker makes request with stolen tokens
```

**Without Session Binding:**
- Tokens are valid (correct signature, not expired, not revoked)
- Server accepts request ✅
- Attacker gains full access 😱

**Problem:**
- Tokens don't know "who is allowed to use them"
- Any bearer of token can use it (bearer token model)

### Solution: Bind Session to IP + User-Agent

**Design:**
```python
# On login:
client_ip = request.headers["X-Forwarded-For"]  # From nginx
user_agent = request.headers["User-Agent"]

session_binding = hmac_sha256(
    message=f"{client_ip}|{user_agent}",
    key=secret_key
)

session_state["session_binding"] = session_binding
session_state["original_ip"] = client_ip
session_state["original_ua"] = user_agent

# On every request:
current_ip = request.headers["X-Forwarded-For"]
current_ua = request.headers["User-Agent"]

current_binding = hmac_sha256(
    message=f"{current_ip}|{current_ua}",
    key=secret_key
)

if current_binding != stored_binding:
    logger.error(f"Session binding mismatch: expected {stored_binding}, got {current_binding}")
    logger.error(f"Original IP={original_ip}, current IP={current_ip}")
    logger.error(f"Original UA={original_ua}, current UA={current_ua}")
    logout_user()
    raise AuthenticationError("Session hijacking detected")
```

**What This Prevents:**
- Stolen cookie used from different IP → Rejected
- Stolen cookie used from different browser → Rejected
- Both IP and User-Agent must match original login

**Why HMAC Instead of Plain Comparison?**
```python
# BAD: Store plaintext IP and UA
session_state["ip"] = client_ip
session_state["ua"] = user_agent

# If attacker gets session state (XSS, debug logs, etc.):
# → Knows exact IP and UA to spoof
```

```python
# GOOD: Store HMAC(IP + UA)
session_state["session_binding"] = hmac_sha256(f"{ip}|{ua}", secret)

# If attacker gets session state:
# → Sees hash: "a3f2b1c4d5..."
# → Cannot reverse to find original IP/UA (one-way function)
# → Cannot forge valid hash without secret_key
```

**HMAC Properties:**
- One-way: Cannot reverse hash to find IP/UA
- Deterministic: Same input → Same hash (for comparison)
- Keyed: Requires secret_key to compute (prevents forgery)

### Trade-offs

**Legitimate Scenarios Broken:**

**VPN Reconnect:**
```
User on laptop, connected to VPN
  → Login (IP: 10.8.0.5 via VPN)
  → VPN drops connection
  → VPN reconnects (new IP: 10.8.0.6)
  → Session binding check fails
  → User logged out 😞
```

**Browser Update:**
```
User logged in (User-Agent: Chrome/91.0)
  → Chrome auto-updates to 92.0
  → User-Agent changes
  → Session binding check fails
  → User logged out 😞
```

**Mobile Network Roaming:**
```
User on phone, on 4G (IP: Carrier NAT IP 1)
  → Moves to different cell tower
  → IP changes (new Carrier NAT IP 2)
  → Session logged out 😞
```

**Corporate NAT/Proxy:**
```
User behind corporate proxy
  → Load balancer changes proxy route
  → X-Forwarded-For changes
  → Session logged out 😞
```

**Design Decision:**

For a **high-security trading console**:
- ✅ Prevent session hijacking (critical)
- ❌ Inconvenience on network changes (acceptable)
- 💡 Users understand "re-login after VPN change" is security feature

For a **consumer app** (e.g., social media):
- Session binding would be too disruptive
- Better to use device fingerprinting or other heuristics

**Mitigation Options (Not Implemented):**

1. **Fallback to partial binding** (IP only, ignore UA):
   - Pro: UA updates don't break session
   - Con: Attacker can spoof UA, only IP checked

2. **Grace period** (allow 1 IP change per hour):
   - Pro: Handles VPN reconnects
   - Con: Allows limited session hijacking

3. **Device fingerprinting** (canvas, WebGL, fonts):
   - Pro: More stable than IP/UA
   - Con: Complex, privacy concerns

---

## Fail-Closed Security

### Fail-Open vs Fail-Closed

**Fail-Open (Insecure Default):**
```python
def verify_certificate(cert):
    try:
        # Verify signature, expiration, etc.
        return is_valid(cert)
    except Exception:
        # ERROR OCCURRED: Can't verify, so... allow it? 😱
        return True  # FAIL-OPEN
```

**Risk:**
- Network error → Allow access
- Malformed certificate → Allow access
- Missing CA certificate → Allow access
- **Attacker can trigger errors to bypass verification**

**Fail-Closed (Secure Default):**
```python
def verify_certificate(cert):
    try:
        return is_valid(cert)
    except Exception as e:
        logger.error(f"Certificate verification failed: {e}")
        return False  # FAIL-CLOSED: Deny if we can't verify
```

**Trade-off:**
- ✅ Attacker cannot bypass by causing errors
- ❌ Legitimate users blocked if system malfunction
- 💡 Acceptable for high-security systems (manual intervention OK)

### Fail-Closed in Our Implementation

**Example 1: TRUSTED_PROXY_IPS**
```python
# Requirement: Only accept requests from nginx proxy
# Purpose: Prevent X-SSL-Client-* header spoofing

if not TRUSTED_PROXY_IPS:
    # Configuration error: Admin forgot to set TRUSTED_PROXY_IPS
    if ALLOW_INSECURE_MTLS_DEV != "true":
        # FAIL-CLOSED: Reject all requests
        st.error("Configuration Error: TRUSTED_PROXY_IPS not set")
        return False
    else:
        # Explicit dev override: Admin acknowledges risk
        logger.warning("INSECURE: Allowing mTLS without proxy verification")

remote_addr = get_remote_addr()
if remote_addr not in TRUSTED_PROXY_IPS:
    # Request not from nginx: Possible direct access or attack
    # FAIL-CLOSED: Reject
    logger.error(f"Untrusted source: {remote_addr}")
    return False
```

**Why This Matters:**
```
Without fail-closed:
  → Admin forgets TRUSTED_PROXY_IPS
  → System allows all requests (can't verify source)
  → Attacker can bypass nginx and spoof headers

With fail-closed:
  → Admin forgets TRUSTED_PROXY_IPS
  → System rejects all requests
  → Admin alerted immediately (service down)
  → Fix configuration before attacker can exploit
```

**Example 2: JWT Signature Verification**
```python
def verify_jwt(token):
    try:
        # Decode header and payload
        header, payload, signature = decode_jwt(token)

        # Load public key
        public_key = load_public_key()  # May raise exception

        # Verify signature
        if not rsa_verify(header + payload, signature, public_key):
            # FAIL-CLOSED: Invalid signature
            raise InvalidTokenError("Signature verification failed")

        return payload
    except FileNotFoundError:
        # Public key file missing (deployment error)
        # FAIL-CLOSED: Cannot verify, so reject
        raise ConfigurationError("Public key not found")
    except Exception as e:
        # Unexpected error (malformed token, crypto library bug, etc.)
        # FAIL-CLOSED: Cannot safely verify, so reject
        logger.error(f"JWT verification error: {e}")
        raise InvalidTokenError("Token verification failed")
```

**Example 3: Certificate Expiration**
```python
def check_certificate_validity(cert):
    try:
        not_before = cert.not_valid_before
        not_after = cert.not_valid_after
        now = datetime.utcnow()

        if now < not_before:
            # FAIL-CLOSED: Certificate not yet valid
            return False

        if now > not_after:
            # FAIL-CLOSED: Certificate expired
            return False

        return True
    except AttributeError:
        # Malformed certificate (missing validity fields)
        # FAIL-CLOSED: Cannot verify, reject
        return False
```

### Operational Impact

**Fail-Closed Benefits:**
- **Security**: Vulnerabilities cannot be exploited by causing errors
- **Alerting**: Service downtime immediately alerts admins to configuration issues
- **Compliance**: Demonstrates security-first design in audits

**Fail-Closed Risks:**
- **Availability**: Misconfigurations cause outages (manual fix required)
- **Operational burden**: Requires 24/7 on-call for critical systems

**When to Use Fail-Closed:**
- Authentication and authorization (our case)
- Payment processing
- Access control to sensitive data
- Safety-critical systems (medical, industrial)

**When to Use Fail-Open:**
- Monitoring and logging (don't break app if logger down)
- Non-critical features (e.g., "recommended for you")
- Graceful degradation (disable feature, keep core working)

---

## Real-World Example

Let's trace a complete authentication flow from browser to web console.

### Scenario: User "Alice" Logs Into Trading Console

**Preconditions:**
- Alice has client certificate installed in browser: `CN=alice,O=Trading Platform`
- Nginx running with server certificate and CA certificate
- Streamlit web console running behind nginx
- Redis running for token revocation

---

### Step 1: Browser Opens HTTPS Connection

**Browser → Nginx (Port 443):**
```
Client Hello:
  - Supported ciphers: TLS_AES_256_GCM_SHA384, TLS_CHACHA20_POLY1305_SHA256, ...
  - Supported curves: X25519, secp384r1
  - SNI: localhost
```

**Nginx → Browser:**
```
Server Hello:
  - Selected cipher: TLS_AES_256_GCM_SHA384
  - Server Certificate:
      Subject: CN=trading-platform-web-console
      Issuer: CN=Trading Platform CA
      Public Key: [2048-bit RSA]
      Signature: [Signed by CA]
```

**Browser verifies:**
- ✅ Signature valid (signed by trusted CA in browser's cert store)
- ✅ Subject matches hostname (CN=trading-platform-web-console)
- ✅ Not expired (valid from 2025-01-01 to 2026-01-01)

---

### Step 2: Nginx Requests Client Certificate

**Nginx → Browser:**
```
Certificate Request:
  - Accepted CAs: CN=Trading Platform CA
  - Signature algorithms: RSA-PSS-SHA256, RSA-PKCS1-SHA256, ...
```

**Browser:**
- Searches keychain for certificates signed by "Trading Platform CA"
- Finds: Alice's certificate `CN=alice,O=Trading Platform`
- Prompts user: "Trading Platform requests your certificate. Use 'alice'?"
- User clicks "OK"

**Browser → Nginx:**
```
Client Certificate:
  Subject: CN=alice,O=Trading Platform,C=US
  Issuer: CN=Trading Platform CA
  Public Key: [2048-bit RSA]
  Signature: [Signed by CA]

Certificate Verify (proves possession of private key):
  Signature of handshake messages using client private key
```

**Nginx verifies:**
- ✅ Certificate signed by trusted CA (Trading Platform CA)
- ✅ Certificate not expired
- ✅ Client possesses private key (signature verification)
- ✅ OCSP status: Good (not revoked)

**Result:**
- TLS handshake complete
- Nginx extracts: `X-SSL-Client-S-DN: CN=alice,O=Trading Platform,C=US`
- Nginx sets: `X-SSL-Client-Verify: SUCCESS`

---

### Step 3: Request Forwarded to Streamlit

**Nginx → Streamlit (Internal HTTP):**
```http
GET / HTTP/1.1
Host: localhost
X-Real-IP: 203.0.113.42
X-Forwarded-For: 203.0.113.42
X-Forwarded-Proto: https
X-SSL-Client-Verify: SUCCESS
X-SSL-Client-S-DN: CN=alice,O=Trading Platform,C=US
X-SSL-Client-I-DN: CN=Trading Platform CA
X-SSL-Client-Serial: 01:23:45:67:89:AB
X-SSL-Client-Fingerprint: SHA256:A1B2C3...
User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)...
```

**Streamlit auth.py:**
```python
# Check request came from trusted proxy (fail-closed)
remote_addr = get_remote_addr()  # Returns nginx IP: 172.28.0.10
if remote_addr not in TRUSTED_PROXY_IPS:  # ["172.28.0.10"]
    st.error("Request not from trusted proxy")
    return False

# Verify mTLS success
verify_status = request.headers.get("X-SSL-Client-Verify")
if verify_status != "SUCCESS":
    st.error(f"Client certificate verification failed: {verify_status}")
    return False

# Extract client DN
client_dn = request.headers.get("X-SSL-Client-S-DN")
# Result: "CN=alice,O=Trading Platform,C=US"
```

---

### Step 4: JWT Token Issuance

**Streamlit issues tokens:**
```python
# Generate unique JTI
access_jti = str(uuid.uuid4())   # "a1b2c3d4-..."
refresh_jti = str(uuid.uuid4())  # "e5f6g7h8-..."

# Compute session binding
client_ip = request.headers["X-Forwarded-For"]  # "203.0.113.42"
user_agent = request.headers["User-Agent"]      # "Mozilla/5.0..."
session_binding = hmac_sha256(f"{client_ip}|{user_agent}", secret_key)
# Result: "7f3a1b9c2e8d..."

# Create access token (15 min)
access_token = jwt_manager.create_access_token(
    subject=client_dn,                 # "CN=alice,O=Trading Platform,C=US"
    jti=access_jti,
    exp=datetime.utcnow() + timedelta(minutes=15),
    session_binding=session_binding
)

# Create refresh token (4 hours)
refresh_token = jwt_manager.create_refresh_token(
    subject=client_dn,
    jti=refresh_jti,
    exp=datetime.utcnow() + timedelta(hours=4),
    session_binding=session_binding
)

# Store in session state
st.session_state.username = client_dn
st.session_state.access_jti = access_jti
st.session_state.refresh_jti = refresh_jti
st.session_state.login_time = time.time()
st.session_state.last_activity_time = time.time()
st.session_state.session_binding = session_binding
st.session_state.access_token = access_token
st.session_state.refresh_token = refresh_token
```

**Access Token (Decoded):**
```json
{
  "header": {
    "alg": "RS256",
    "typ": "JWT"
  },
  "payload": {
    "sub": "CN=alice,O=Trading Platform,C=US",
    "jti": "a1b2c3d4-e5f6-4a5b-8c7d-9e0f1a2b3c4d",
    "exp": 1732346778,
    "iat": 1732345878,
    "session_binding": "7f3a1b9c2e8d..."
  },
  "signature": "SflKxw... [RS256 signature]"
}
```

---

### Step 5: User Makes Authenticated Request

**10 minutes later, Alice clicks "Submit Order" button.**

**Streamlit before processing order:**
```python
# 1. Check idle timeout
if (time.time() - st.session_state.last_activity_time) > 15 * 60:
    logout_user()
    st.error("Session timed out due to inactivity")
    return

# 2. Check absolute timeout
if (time.time() - st.session_state.login_time) > 4 * 60 * 60:
    logout_user()
    st.error("Session expired (maximum 4 hours)")
    return

# 3. Validate access token
try:
    jwt_manager.verify_access_token(st.session_state.access_token)
except TokenExpiredError:
    # Access token expired, try to refresh
    refresh_tokens()
except TokenRevokedError:
    # Token was revoked (logged out elsewhere)
    logout_user()
    st.error("Token revoked")
    return

# 4. Verify session binding
client_ip = request.headers["X-Forwarded-For"]
user_agent = request.headers["User-Agent"]
current_binding = hmac_sha256(f"{client_ip}|{user_agent}", secret_key)

if current_binding != st.session_state.session_binding:
    logout_user()
    st.error("Session hijacking detected (IP or browser changed)")
    return

# 5. Update activity timestamp
st.session_state.last_activity_time = time.time()

# 6. Process order
submit_order(symbol="AAPL", side="buy", qty=100)
```

---

### Step 6: Token Refresh (Access Token Expired)

**15 minutes after login, access token expires.**

**Streamlit refresh flow:**
```python
def refresh_tokens():
    # 1. Verify refresh token
    try:
        claims = jwt_manager.verify_refresh_token(st.session_state.refresh_token)
    except TokenExpiredError:
        # Refresh token also expired (>4 hours), must re-login
        logout_user()
        st.error("Please log in again")
        return
    except TokenRevokedError:
        # Refresh token was revoked
        logout_user()
        st.error("Session invalidated")
        return

    # 2. Verify session binding
    client_ip = request.headers["X-Forwarded-For"]
    user_agent = request.headers["User-Agent"]
    current_binding = hmac_sha256(f"{client_ip}|{user_agent}", secret_key)

    if claims["session_binding"] != current_binding:
        logout_user()
        st.error("Session binding changed during refresh")
        return

    # 3. Revoke old refresh token (atomic rotation)
    old_refresh_jti = claims["jti"]
    old_refresh_exp = claims["exp"]
    jwt_manager.revoke_token(old_refresh_jti, old_refresh_exp)
    # Redis: SETEX revoked_jti:e5f6g7h8 <ttl> "revoked"

    # 4. Issue new tokens
    new_access_jti = str(uuid.uuid4())
    new_refresh_jti = str(uuid.uuid4())

    new_access_token = jwt_manager.create_access_token(
        subject=claims["sub"],
        jti=new_access_jti,
        exp=datetime.utcnow() + timedelta(minutes=15),
        session_binding=current_binding
    )

    new_refresh_token = jwt_manager.create_refresh_token(
        subject=claims["sub"],
        jti=new_refresh_jti,
        exp=datetime.utcnow() + timedelta(hours=4),
        session_binding=current_binding
    )

    # 5. Update session state
    st.session_state.access_jti = new_access_jti
    st.session_state.refresh_jti = new_refresh_jti
    st.session_state.access_token = new_access_token
    st.session_state.refresh_token = new_refresh_token

    logger.info(f"Tokens refreshed for {claims['sub']}")
```

**Redis State After Rotation:**
```
Key: revoked_jti:e5f6g7h8-... (old refresh token)
Value: "revoked"
TTL: 14385 seconds (time until old token's natural expiry)
```

**Result:**
- Old refresh token immediately invalid (cannot reuse for another refresh)
- New access token valid for next 15 minutes
- New refresh token valid for next 4 hours
- Alice's session continues seamlessly

---

### Step 7: User Logs Out

**Alice clicks "Logout" button.**

**Streamlit logout flow:**
```python
def logout_user():
    username = st.session_state.get("username", "unknown")
    access_jti = st.session_state.get("access_jti")
    refresh_jti = st.session_state.get("refresh_jti")

    # 1. Revoke access token
    if access_jti:
        # Get expiration from stored token
        access_claims = jwt.decode(
            st.session_state.access_token,
            options={"verify_signature": False}  # Just reading exp
        )
        jwt_manager.revoke_token(access_jti, access_claims["exp"])

    # 2. Revoke refresh token
    if refresh_jti:
        refresh_claims = jwt.decode(
            st.session_state.refresh_token,
            options={"verify_signature": False}
        )
        jwt_manager.revoke_token(refresh_jti, refresh_claims["exp"])

    # 3. Clear session state
    st.session_state.clear()

    # 4. Audit log
    logger.info(f"User logged out: {username}")
    audit_log(
        event="logout",
        user=username,
        ip=request.headers["X-Forwarded-For"],
        timestamp=datetime.utcnow()
    )

    st.success("Logged out successfully")
```

**Redis State After Logout:**
```
Key: revoked_jti:a1b2c3d4-... (access token)
Value: "revoked"
TTL: 300 seconds (5 minutes until natural expiry)

Key: revoked_jti:f9g0h1i2-... (refresh token)
Value: "revoked"
TTL: 10800 seconds (3 hours until natural expiry)
```

**Result:**
- Both tokens immediately invalid
- If attacker has stolen token copies, they are rejected
- Alice must provide client certificate again to re-login

---

### Attack Scenarios & Defenses

**Attack 1: Stolen Access Token**

**Attacker:**
- Intercepts network traffic (MITM on coffee shop WiFi)
- Extracts access token from HTTPS payload (encryption broken?)
- Attempts to use token from attacker's machine

**Defense:**
```python
# Session binding check:
stored_binding = "7f3a1b9c..."  # HMAC(Alice's IP + UA)
attacker_ip = "198.51.100.5"    # Different IP
attacker_ua = "curl/7.68.0"     # Different UA
attacker_binding = hmac_sha256(f"{attacker_ip}|{attacker_ua}", secret)
# Result: "2d9e6a1c..." ≠ "7f3a1b9c..."

if attacker_binding != stored_binding:
    logout_user()
    return False  # BLOCKED
```

**Attack 2: Replay Attack**

**Attacker:**
- Records Alice's login request at 9:00 AM
- Replays identical request at 10:00 AM (after Alice logged out)

**Defense:**
```python
# Token validation:
if redis.exists(f"revoked_jti:{access_jti}"):
    raise TokenRevokedError("Token revoked on logout")
    # BLOCKED: Token was revoked at logout
```

**Attack 3: Certificate Forgery**

**Attacker:**
- Creates fake certificate: `CN=alice,O=Trading Platform,C=US`
- Self-signs with attacker's CA
- Attempts to connect

**Defense:**
```
Nginx verifies:
  Certificate signature = RSA_Verify(cert, Trading Platform CA public key)

Attacker's cert signed by attacker's CA:
  Signature ≠ Expected signature

Nginx rejects connection: "SSL certificate problem: unable to verify"
BLOCKED: Connection terminated before reaching Streamlit
```

**Attack 4: Token Forgery**

**Attacker:**
- Captures valid token
- Modifies `sub` claim: `CN=alice` → `CN=admin`
- Recalculates signature with guessed private key

**Defense:**
```python
# JWT verification:
signature = base64url_decode(token.split(".")[2])
expected_sig = RSA_Verify(header + payload, public_key)

if signature != expected_sig:
    raise InvalidTokenError("Invalid signature")
    # BLOCKED: Cannot forge signature without 4096-bit RSA private key
```

---

## Summary

**What We Learned:**

1. **mTLS**: Client and server both prove identity with certificates
2. **PKI**: Certificate Authority signs certificates to establish trust
3. **JWT**: Self-contained tokens with cryptographic signatures
4. **RS256**: Asymmetric signing allows distribution of verification without signing capability
5. **Session Management**: Idle and absolute timeouts enforce re-authentication
6. **Token Revocation**: JTI blacklist in Redis enables immediate invalidation
7. **Session Binding**: IP + User-Agent binding prevents session hijacking
8. **Fail-Closed**: Deny by default when verification cannot be performed

**Security Layers:**
1. Network: Only nginx exposed (443/tcp)
2. TLS: Client certificate required (cryptographic identity)
3. JWT: Valid signature + unexpired + not revoked
4. Session: IP + User-Agent binding
5. Audit: All auth events logged

**Key Takeaways:**

- **Defense in depth**: Multiple independent security checks
- **Fail-closed design**: Deny when uncertain (security > availability)
- **Cryptographic proof**: Certificates and signatures > passwords
- **Stateful + Stateless**: JWT benefits + revocation capability
- **User experience trade-offs**: Security convenience (session binding breaks on network change)

**Further Reading:**

- RFC 5280: X.509 Certificates
- RFC 7519: JSON Web Tokens
- RFC 8446: TLS 1.3
- OWASP Authentication Cheat Sheet
- NIST Digital Identity Guidelines (SP 800-63B)

---

**Related Documentation:**
- ADR 0018: Web Console mTLS Authentication (architecture decision)
- RUNBOOKS/web-console-mtls-setup.md (deployment guide)
- RUNBOOKS/web-console-cert-rotation.md (operations guide)
