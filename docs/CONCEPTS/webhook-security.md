# Webhook Security

## Plain English Explanation

A webhook is a way for external services (like Alpaca) to send real-time notifications to your application when events occur (like an order being filled). However, webhooks create a security risk: **how do you know the webhook actually came from Alpaca and not from a malicious attacker?**

Webhook security solves this problem using **digital signatures**. Think of it like a wax seal on a letter in medieval times:
1. Alpaca "seals" each webhook message with a secret code (HMAC signature)
2. You verify the seal matches what you expect
3. If the seal is correct, you know the message is authentic
4. If someone tries to fake a webhook, they can't create the correct seal (because they don't have the secret)

Without webhook security, an attacker could:
- Send fake "fill" notifications to manipulate your position tracking
- Trigger fake order cancellations
- Cause your system to act on fraudulent data

## Why It Matters

### Real-World Attack Scenario

Imagine you're running a trading bot that relies on webhooks for position updates:

**Without webhook security:**
1. Attacker discovers your webhook URL: `https://yourbot.com/api/webhooks/orders`
2. Attacker sends fake webhook: `{"event": "fill", "order": {"id": "123", "symbol": "AAPL", "filled_qty": 1000}}`
3. Your bot thinks it owns 1000 shares of AAPL (but it doesn't)
4. Your bot makes trading decisions based on fake position
5. **Result:** Your bot enters incorrect trades, loses money

**With webhook security:**
1. Attacker sends same fake webhook
2. Your bot checks the HMAC signature
3. Signature doesn't match (attacker doesn't have the secret)
4. **Your bot rejects the webhook** → no damage done

### Financial Impact

A successful webhook attack could:
- Create phantom positions leading to over-leveraging
- Trigger unintended hedging or rebalancing
- Cause cascading failures if position data is corrupted
- Result in regulatory issues (incorrect position reporting)

For a $100,000 portfolio, a fake fill notification could cause:
- Incorrect position sizing (2x leverage when you think you have 1x)
- Forced liquidations from margin calls
- Potential loss of entire account

## Common Pitfalls

### 1. String Comparison Timing Attacks

**WRONG:**
```python
def verify_signature(payload: bytes, signature: str, secret: str) -> bool:
    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return expected == signature  # ❌ VULNERABLE TO TIMING ATTACKS
```

**Why it's wrong:** Python's `==` operator compares strings character by character and returns `False` as soon as it finds a mismatch. An attacker can measure how long the comparison takes to figure out the correct signature one character at a time.

**Example timing attack:**
- Signature "a000..." takes 1 microsecond to reject (fails on first char)
- Signature "h000..." takes 2 microseconds to reject (fails on second char)
- Signature "ha00..." takes 3 microseconds to reject (fails on third char)
- Attacker can brute-force the correct signature by measuring response times

**RIGHT:**
```python
def verify_signature(payload: bytes, signature: str, secret: str) -> bool:
    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)  # ✅ CONSTANT-TIME COMPARISON
```

**Why it's right:** `hmac.compare_digest()` always takes the same amount of time regardless of where the mismatch occurs, preventing timing attacks.

### 2. Signature Format Assumptions

Webhooks may send signatures in different formats:
- Simple: `a1b2c3d4e5f6...` (just the hex string)
- Prefixed: `sha256=a1b2c3d4e5f6...` (with algorithm prefix)

**Solution:**
```python
def extract_signature(header: str) -> str:
    """Extract signature from header, handling both formats."""
    if not header:
        return None

    # Strip "sha256=" prefix if present
    if header.startswith("sha256="):
        return header[7:]  # Remove "sha256=" (7 characters)

    return header  # Already in simple format
```

### 3. Empty or Missing Signatures

**WRONG:**
```python
signature = request.headers.get("X-Signature")
if verify_signature(payload, signature, secret):  # ❌ Crashes if signature is None
    process_webhook(payload)
```

**RIGHT:**
```python
signature = request.headers.get("X-Signature")
if not signature:
    return {"error": "Missing signature"}, 401

if not verify_signature(payload, signature, secret):
    return {"error": "Invalid signature"}, 403

process_webhook(payload)  # Only if signature is present AND valid
```

### 4. Using Body String Instead of Bytes

**WRONG:**
```python
@app.post("/webhooks")
async def webhook(request: Request):
    body = await request.body()
    payload_str = body.decode('utf-8')  # ❌ Converting to string
    signature = request.headers.get("X-Signature")

    # This will fail! HMAC needs exact bytes
    expected = hmac.new(secret.encode(), payload_str.encode(), hashlib.sha256).hexdigest()
```

**Why it's wrong:** Re-encoding the string might produce different bytes than the original (due to encoding differences, whitespace, etc.). The signature was computed on the **exact original bytes**.

**RIGHT:**
```python
@app.post("/webhooks")
async def webhook(request: Request):
    payload = await request.body()  # Keep as bytes
    signature = request.headers.get("X-Signature")

    # Use original bytes directly
    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
```

### 5. Hardcoded Secrets in Code

**WRONG:**
```python
WEBHOOK_SECRET = "my_secret_key_12345"  # ❌ NEVER hardcode secrets
```

**RIGHT:**
```python
import os
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")

if not WEBHOOK_SECRET:
    raise ValueError("WEBHOOK_SECRET environment variable is required")
```

Store secrets in:
- Environment variables (`.env` file, not committed to git)
- Secret management services (AWS Secrets Manager, HashiCorp Vault)
- Kubernetes secrets (for production deployments)

## Examples

### Example 1: Basic HMAC Signature Verification

```python
import hmac
import hashlib

def generate_webhook_signature(payload: bytes, secret: str) -> str:
    """
    Generate HMAC-SHA256 signature for webhook payload.

    This is what Alpaca does on their side before sending the webhook.

    Args:
        payload: Raw webhook body as bytes (JSON string)
        secret: Shared secret key (WEBHOOK_SECRET)

    Returns:
        64-character hexadecimal signature string

    Example:
        >>> payload = b'{"event":"fill","order":{"id":"123"}}'
        >>> secret = "my_webhook_secret"
        >>> sig = generate_webhook_signature(payload, secret)
        >>> len(sig)
        64
        >>> sig[:8]
        'a1b2c3d4'
    """
    return hmac.new(
        key=secret.encode('utf-8'),
        msg=payload,
        digestmod=hashlib.sha256
    ).hexdigest()


def verify_webhook_signature(payload: bytes, signature: str, secret: str) -> bool:
    """
    Verify webhook signature using constant-time comparison.

    This is what your application does when receiving a webhook.

    Args:
        payload: Raw webhook body as bytes (exact bytes received)
        signature: Signature from X-Signature header
        secret: Your shared secret key (same one Alpaca has)

    Returns:
        True if signature is valid, False otherwise

    Example:
        >>> payload = b'{"event":"fill","order":{"id":"123"}}'
        >>> secret = "my_webhook_secret"
        >>> signature = generate_webhook_signature(payload, secret)
        >>> verify_webhook_signature(payload, signature, secret)
        True
        >>> verify_webhook_signature(payload, "wrong_signature", secret)
        False
    """
    expected_signature = generate_webhook_signature(payload, secret)

    # Use constant-time comparison to prevent timing attacks
    return hmac.compare_digest(expected_signature, signature.lower())
```

### Example 2: Complete FastAPI Webhook Endpoint

```python
from fastapi import FastAPI, Request, HTTPException
import os

app = FastAPI()
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")

@app.post("/api/v1/webhooks/orders")
async def receive_order_webhook(request: Request):
    """
    Receive order update webhooks from Alpaca.

    Security:
    - Verifies HMAC-SHA256 signature to ensure webhook authenticity
    - Rejects webhooks without valid signature (403 Forbidden)
    - Uses constant-time comparison to prevent timing attacks

    Example valid request:
        POST /api/v1/webhooks/orders
        Headers:
            X-Signature: sha256=a1b2c3d4e5f6...
            Content-Type: application/json
        Body:
            {"event": "fill", "order": {"id": "123", "symbol": "AAPL"}}
    """
    # Step 1: Get raw payload bytes (MUST be exact bytes for signature match)
    payload = await request.body()

    # Step 2: Extract signature from header
    signature_header = request.headers.get("X-Signature")
    if not signature_header:
        raise HTTPException(status_code=401, detail="Missing X-Signature header")

    # Step 3: Handle both "sha256=..." and plain signature formats
    signature = extract_signature_from_header(signature_header)
    if not signature:
        raise HTTPException(status_code=400, detail="Invalid signature format")

    # Step 4: Verify signature (if secret is configured)
    if WEBHOOK_SECRET:
        if not verify_webhook_signature(payload, signature, WEBHOOK_SECRET):
            # Log the failed attempt for security monitoring
            logger.warning(
                "Webhook signature verification failed",
                extra={
                    "signature_provided": signature[:16] + "...",  # Only log prefix
                    "payload_size": len(payload)
                }
            )
            raise HTTPException(status_code=403, detail="Invalid signature")
    else:
        # Warning: No signature verification (development mode only)
        logger.warning("WEBHOOK_SECRET not set - skipping signature verification")

    # Step 5: Parse and process webhook (only if signature valid)
    import json
    webhook_data = json.loads(payload)

    # Process the webhook event
    await process_order_update(webhook_data)

    return {"status": "ok"}


def extract_signature_from_header(header: str) -> str:
    """
    Extract signature from header, handling multiple formats.

    Supported formats:
    - Simple: "a1b2c3d4e5f6..."
    - Prefixed: "sha256=a1b2c3d4e5f6..."

    Args:
        header: Value from X-Signature header

    Returns:
        Extracted signature (lowercase hex string), or None if invalid

    Example:
        >>> extract_signature_from_header("sha256=abcd1234")
        'abcd1234'
        >>> extract_signature_from_header("abcd1234")
        'abcd1234'
        >>> extract_signature_from_header("")
        None
    """
    if not header:
        return None

    # Remove "sha256=" prefix if present
    if header.startswith("sha256="):
        return header[7:]

    # Already in simple format
    return header
```

### Example 3: Testing Webhook Security

```python
import pytest
from fastapi.testclient import TestClient

def test_webhook_valid_signature():
    """Valid signature should be accepted."""
    client = TestClient(app)

    payload = b'{"event":"fill","order":{"id":"123"}}'
    secret = "test_secret"

    # Generate valid signature
    signature = generate_webhook_signature(payload, secret)

    # Send webhook with valid signature
    response = client.post(
        "/api/v1/webhooks/orders",
        content=payload,
        headers={"X-Signature": f"sha256={signature}"}
    )

    assert response.status_code == 200


def test_webhook_invalid_signature():
    """Invalid signature should be rejected."""
    client = TestClient(app)

    payload = b'{"event":"fill","order":{"id":"123"}}'

    # Send webhook with wrong signature
    response = client.post(
        "/api/v1/webhooks/orders",
        content=payload,
        headers={"X-Signature": "sha256=wrong_signature"}
    )

    assert response.status_code == 403  # Forbidden


def test_webhook_missing_signature():
    """Missing signature should be rejected."""
    client = TestClient(app)

    payload = b'{"event":"fill","order":{"id":"123"}}'

    # Send webhook without signature header
    response = client.post(
        "/api/v1/webhooks/orders",
        content=payload
    )

    assert response.status_code == 401  # Unauthorized


def test_webhook_timing_attack_resistance():
    """Signature verification should be resistant to timing attacks."""
    import time

    payload = b'{"event":"fill","order":{"id":"123"}}'
    secret = "test_secret"
    correct_sig = generate_webhook_signature(payload, secret)

    # Try signatures that fail at different positions
    wrong_sig_1 = "a" + correct_sig[1:]  # Fails on first character
    wrong_sig_2 = correct_sig[:30] + "a" + correct_sig[31:]  # Fails in middle

    # Measure time for both (should be similar)
    start = time.perf_counter()
    verify_webhook_signature(payload, wrong_sig_1, secret)
    time_1 = time.perf_counter() - start

    start = time.perf_counter()
    verify_webhook_signature(payload, wrong_sig_2, secret)
    time_2 = time.perf_counter() - start

    # Times should be within 10% of each other (constant-time)
    # Note: This is a simplified test; real timing attacks need many samples
    assert abs(time_1 - time_2) / max(time_1, time_2) < 0.10
```

## How HMAC-SHA256 Works

### Step-by-Step Breakdown

```python
# 1. Shared secret (known by both Alpaca and your app)
secret = "my_webhook_secret"

# 2. Webhook payload (exact bytes Alpaca sends)
payload = b'{"event":"fill","order":{"id":"123","symbol":"AAPL"}}'

# 3. Alpaca computes signature before sending
signature = hmac.new(
    key=secret.encode('utf-8'),      # Secret as bytes
    msg=payload,                      # Payload as bytes
    digestmod=hashlib.sha256          # SHA256 hash algorithm
).hexdigest()                         # Convert to hex string
# Result: "a1b2c3d4e5f6..." (64 characters)

# 4. Alpaca sends webhook with signature in header
#    POST https://yourapp.com/webhooks
#    X-Signature: sha256=a1b2c3d4e5f6...
#    Body: {"event":"fill","order":{"id":"123","symbol":"AAPL"}}

# 5. Your app receives webhook and verifies
received_signature = "a1b2c3d4e5f6..."  # From X-Signature header
received_payload = b'{"event":"fill",...}'  # Request body as bytes

# 6. Compute expected signature using same secret
expected_signature = hmac.new(
    key=secret.encode('utf-8'),
    msg=received_payload,
    digestmod=hashlib.sha256
).hexdigest()

# 7. Compare using constant-time comparison
if hmac.compare_digest(expected_signature, received_signature):
    # Signature matches → webhook is authentic
    process_webhook(received_payload)
else:
    # Signature doesn't match → reject webhook
    return 403 Forbidden
```

### Why SHA256?

- **Cryptographically secure**: Computationally infeasible to reverse
- **Deterministic**: Same input always produces same output
- **Collision-resistant**: Nearly impossible to find two inputs with same hash
- **Fast**: Can verify thousands of webhooks per second
- **Standard**: Widely supported and well-tested

### Why HMAC (not just SHA256)?

Plain SHA256 wouldn't be secure:
```python
# ❌ INSECURE: Attacker can compute hash without secret
hash = hashlib.sha256(payload).hexdigest()
# Attacker can create any payload and compute valid hash
```

HMAC adds the secret key:
```python
# ✅ SECURE: Attacker can't compute valid HMAC without secret
hmac_hash = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
# Only someone with the secret can create valid signature
```

## Configuration Best Practices

### Development Environment

```bash
# .env (for local development)
WEBHOOK_SECRET=dev_secret_not_for_production_12345

# Optional: Disable signature verification for local testing
# WEBHOOK_SECRET=  # Empty = skip verification (logs warning)
```

### Production Environment

```bash
# .env (NEVER commit this file)
WEBHOOK_SECRET=prod_xK9mP2vR8nQ4wL7jF3hS6tD1zY5bC0aE  # Strong random secret

# Or use environment variables (Kubernetes, Docker, etc.)
kubectl create secret generic webhook-secret \
  --from-literal=WEBHOOK_SECRET=prod_xK9mP2vR8nQ4wL7jF3hS6tD1zY5bC0aE
```

### Generating Strong Secrets

```python
import secrets

# Generate cryptographically strong secret (32 bytes = 64 hex chars)
webhook_secret = secrets.token_hex(32)
print(f"WEBHOOK_SECRET={webhook_secret}")

# Example output:
# WEBHOOK_SECRET=a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6q7r8s9t0u1v2w3x4y5z6...
```

### Secret Rotation

If your secret is compromised:

1. Generate new secret: `new_secret = secrets.token_hex(32)`
2. Update your application config with new secret
3. Update Alpaca webhook configuration with new secret
4. Restart your application
5. Old webhooks with old signature will be rejected (good!)

## Security Checklist

Before deploying webhook endpoints to production:

- [ ] Signature verification is enabled (WEBHOOK_SECRET is set)
- [ ] Using `hmac.compare_digest()` for constant-time comparison
- [ ] Rejecting webhooks with missing or invalid signatures (401/403)
- [ ] Logging signature verification failures for monitoring
- [ ] Secret stored in environment variables (not hardcoded)
- [ ] Using original request bytes for signature verification (not re-encoded)
- [ ] Handling both "sha256=" and plain signature formats
- [ ] Testing with valid, invalid, and missing signatures
- [ ] Rate limiting webhook endpoint to prevent DoS attacks
- [ ] HTTPS enabled (TLS encryption for webhook delivery)

## Further Reading

### Official Documentation
- [Alpaca Webhooks Documentation](https://alpaca.markets/docs/trading/webhooks/)
- [HMAC Wikipedia](https://en.wikipedia.org/wiki/HMAC) - Technical details
- [OWASP Webhook Security](https://cheatsheetseries.owasp.org/cheatsheets/Webhook_Security_Cheat_Sheet.html)

### Related Concepts
- `/docs/CONCEPTS/idempotency.md` - Why webhooks need to be idempotent
- `/docs/ADRs/0014-execution-gateway-architecture.md` - T4 architecture decisions

### Python Security
- [Python hmac module](https://docs.python.org/3/library/hmac.html) - Official documentation
- [Timing Attack Explanation](https://codahale.com/a-lesson-in-timing-attacks/) - Why constant-time matters
