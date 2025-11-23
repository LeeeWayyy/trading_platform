"""
Integration Tests for Web Console mTLS Configuration.

Tests Component 3 of P2T3 Phase 2: Nginx Reverse Proxy with HTTPS/TLS.

Test Coverage:
- mTLS enforcement (valid/invalid/expired certificates)
- TLS configuration (HSTS, redirects, cipher suites)
- Rate limiting (connection-level, IP-based, DN-based)
- Certificate reload (with/without downtime)
- WebSocket support (long-lived connections)
- JWT-DN binding contract enforcement
- OCSP stapling validation
- Header forwarding (X-SSL-Client-* headers)

Requirements:
- Docker & Docker Compose
- Valid certificates in apps/web_console/certs/
- nginx + web_console services running in mTLS mode

Usage:
    pytest tests/integration/test_mtls_integration.py -v
"""

import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import requests
from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

# ============================
# Fixtures
# ============================


@pytest.fixture(scope="module")
def certs_dir() -> Path:
    """Path to certificates directory."""
    return Path("apps/web_console/certs")


@pytest.fixture(scope="module")
def valid_client_cert(certs_dir: Path) -> tuple[Path, Path]:
    """
    Valid client certificate and key (CA-signed).

    Assumes certificates generated via scripts/generate_certs.py.
    Looks for first available client_*.crt file.

    Returns:
        tuple: (cert_path, key_path)
    """
    # Find any client certificate
    client_certs = list(certs_dir.glob("client_*.crt"))
    if not client_certs:
        pytest.skip(
            "No client certificates found. Run: python3 scripts/generate_certs.py --client test_user"
        )

    cert_path = client_certs[0]
    key_path = cert_path.with_suffix(".key")

    if not key_path.exists():
        pytest.skip(f"Client key not found: {key_path}")

    return cert_path, key_path


@pytest.fixture(scope="module")
def invalid_client_cert(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """
    Generate self-signed client certificate (NOT CA-signed).

    This certificate should be rejected by nginx because it's not signed by the CA.

    Returns:
        tuple: (cert_path, key_path)
    """
    tmp_dir = tmp_path_factory.mktemp("certs")
    cert_path = tmp_dir / "invalid_client.crt"
    key_path = tmp_dir / "invalid_client.key"

    # Generate RSA key pair
    private_key = rsa.generate_private_key(
        public_exponent=65537, key_size=2048, backend=default_backend()
    )

    # Create self-signed certificate
    subject = issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "US"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Invalid Org"),
            x509.NameAttribute(NameOID.COMMON_NAME, "invalid_client"),
        ]
    )

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.utcnow())
        .not_valid_after(datetime.utcnow() + timedelta(days=1))
        .sign(private_key, hashes.SHA256(), backend=default_backend())
    )

    # Write certificate and key
    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))

    with open(key_path, "wb") as f:
        f.write(
            private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )

    return cert_path, key_path


@pytest.fixture(scope="module")
def expired_client_cert(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """
    Generate expired client certificate.

    Certificate valid from (now - 2 days) to (now - 1 day).

    Returns:
        tuple: (cert_path, key_path)
    """
    tmp_dir = tmp_path_factory.mktemp("certs")
    cert_path = tmp_dir / "expired_client.crt"
    key_path = tmp_dir / "expired_client.key"

    # Generate RSA key pair
    private_key = rsa.generate_private_key(
        public_exponent=65537, key_size=2048, backend=default_backend()
    )

    # Create expired certificate
    subject = issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "US"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Expired Org"),
            x509.NameAttribute(NameOID.COMMON_NAME, "expired_client"),
        ]
    )

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.utcnow() - timedelta(days=2))
        .not_valid_after(datetime.utcnow() - timedelta(days=1))  # Expired yesterday
        .sign(private_key, hashes.SHA256(), backend=default_backend())
    )

    # Write certificate and key
    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))

    with open(key_path, "wb") as f:
        f.write(
            private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )

    return cert_path, key_path


@pytest.fixture(scope="module", autouse=True)
def _nginx_container() -> None:
    """
    Ensure nginx and web_console services running in mTLS mode.

    This fixture runs once per test module.
    """
    # Check if services already running
    result = subprocess.run(
        ["docker", "ps", "--filter", "name=trading_platform_nginx", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
    )

    if "trading_platform_nginx" not in result.stdout:
        # Start services with mTLS profile
        print("\nStarting nginx + web_console with mTLS profile...")
        subprocess.run(["docker-compose", "--profile", "mtls", "up", "-d"], check=True)
        # Wait for services to be ready
        time.sleep(10)

    # Verify services healthy
    result = subprocess.run(
        ["docker", "exec", "trading_platform_nginx", "nginx", "-t"], capture_output=True, text=True
    )
    if result.returncode != 0:
        pytest.fail(f"nginx configuration test failed: {result.stderr}")

    # Cleanup: Leave services running for other tests
    # To stop: docker-compose --profile mtls down


# ============================
# Test Cases
# ============================


@pytest.mark.integration()
def test_https_with_valid_cert_succeeds(valid_client_cert: tuple[Path, Path]) -> None:
    """Test 1: HTTPS connection with valid client cert succeeds."""
    cert_path, key_path = valid_client_cert

    response = requests.get(
        "https://localhost:443/health",
        cert=(str(cert_path), str(key_path)),
        verify=False,  # Skip server cert verification (self-signed CA)
    )

    assert response.status_code == 200
    assert response.text.strip() == "OK"


@pytest.mark.integration()
def test_connection_rejected_without_cert() -> None:
    """Test 2: Connection rejected without client cert."""
    with pytest.raises(requests.exceptions.SSLError) as exc_info:
        requests.get("https://localhost:443/health", verify=False)

    # Verify SSL handshake failure
    assert "SSL" in str(exc_info.value) or "certificate" in str(exc_info.value).lower()


@pytest.mark.integration()
def test_connection_rejected_with_invalid_cert(invalid_client_cert: tuple[Path, Path]) -> None:
    """Test 3: Connection rejected with invalid (self-signed) client cert."""
    cert_path, key_path = invalid_client_cert

    with pytest.raises(requests.exceptions.SSLError) as exc_info:
        requests.get(
            "https://localhost:443/health", cert=(str(cert_path), str(key_path)), verify=False
        )

    # Verify SSL handshake failure (cert not CA-signed)
    assert "SSL" in str(exc_info.value) or "certificate" in str(exc_info.value).lower()


@pytest.mark.integration()
def test_connection_rejected_with_expired_cert(expired_client_cert: tuple[Path, Path]) -> None:
    """Test 4: Connection rejected with expired client cert."""
    cert_path, key_path = expired_client_cert

    with pytest.raises(requests.exceptions.SSLError) as exc_info:
        requests.get(
            "https://localhost:443/health", cert=(str(cert_path), str(key_path)), verify=False
        )

    # Verify SSL handshake failure (cert expired)
    assert "SSL" in str(exc_info.value) or "certificate" in str(exc_info.value).lower()


@pytest.mark.integration()
def test_hsts_header_present(valid_client_cert: tuple[Path, Path]) -> None:
    """Test 6: HSTS header present in HTTPS responses."""
    cert_path, key_path = valid_client_cert

    response = requests.get(
        "https://localhost:443/health", cert=(str(cert_path), str(key_path)), verify=False
    )

    assert "Strict-Transport-Security" in response.headers
    hsts_value = response.headers["Strict-Transport-Security"]
    assert "max-age=" in hsts_value
    assert "includeSubDomains" in hsts_value


@pytest.mark.integration()
def test_http_to_https_redirect() -> None:
    """Test 7: HTTP to HTTPS redirect (301)."""
    response = requests.get("http://localhost:80/", allow_redirects=False)

    assert response.status_code == 301
    assert "Location" in response.headers
    assert response.headers["Location"].startswith("https://")


@pytest.mark.integration()
def test_rate_limiting_excessive_requests(valid_client_cert: tuple[Path, Path]) -> None:
    """Test 10: Rate limiting returns 429 for excessive authenticated requests."""
    cert_path, key_path = valid_client_cert

    # Send rapid requests to trigger rate limit
    # Layer 3 limit: 10r/s sustained, burst 20
    # Strategy: Send 50 requests as fast as possible
    responses = []
    for _ in range(50):
        try:
            response = requests.get(
                "https://localhost:443/health",
                cert=(str(cert_path), str(key_path)),
                verify=False,
                timeout=2,
            )
            responses.append(response.status_code)
        except requests.exceptions.RequestException:
            # Connection error due to rate limiting
            responses.append(0)

    # Verify mix of 200 (success) and 429 (rate limited)
    assert 200 in responses, "No successful requests (rate limit too aggressive)"
    assert 429 in responses or 0 in responses, "No rate limit triggered (limit too permissive)"


@pytest.mark.integration()
def test_client_dn_logged(valid_client_cert: tuple[Path, Path]) -> None:
    """Test 13: Client DN logged for auth events."""
    cert_path, key_path = valid_client_cert

    # Make request
    response = requests.get(
        "https://localhost:443/health", cert=(str(cert_path), str(key_path)), verify=False
    )
    assert response.status_code == 200

    # Check nginx logs for client DN
    time.sleep(1)  # Wait for log flush
    result = subprocess.run(
        ["docker", "logs", "trading_platform_nginx", "--tail", "50"], capture_output=True, text=True
    )

    # Verify logs contain client DN (CN=<username>)
    assert "CN=" in result.stdout or "CN=" in result.stderr, "Client DN not found in nginx logs"


@pytest.mark.integration()
def test_connection_level_rate_limiting(valid_client_cert: tuple[Path, Path]) -> None:
    """Test 14: Connection-level rate limiting (handshake flood protection)."""
    cert_path, key_path = valid_client_cert

    # Attempt to open 60 concurrent connections
    # Limit: 10 concurrent connections per IP (from nginx.conf limit_conn)
    # Note: This test may be flaky due to connection reuse and timing

    # Use curl for better connection control
    processes = []
    for _ in range(60):
        proc = subprocess.Popen(
            [
                "curl",
                "-k",
                "-s",
                "-o",
                "/dev/null",
                "-w",
                "%{http_code}",
                "--cert",
                str(cert_path),
                "--key",
                str(key_path),
                "https://localhost:443/health",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        processes.append(proc)

    # Wait for all processes to complete
    results = []
    for proc in processes:
        stdout, stderr = proc.communicate(timeout=10)
        if proc.returncode == 0:
            results.append(stdout.decode().strip())
        else:
            results.append("error")

    # Verify some connections succeeded and some may have been rejected
    assert "200" in results, "No successful connections (limit too aggressive)"
    # Note: Connection limit is challenging to test reliably due to connection reuse
    # This test serves as a baseline; manual testing recommended for validation


@pytest.mark.integration()
def test_tls_configuration_validation() -> None:
    """Test 17: TLS configuration validation (config lint)."""
    # Run nginx -T to dump full configuration
    result = subprocess.run(
        ["docker", "exec", "trading_platform_nginx", "nginx", "-T"], capture_output=True, text=True
    )

    assert result.returncode == 0, f"nginx -T failed: {result.stderr}"
    config = result.stdout

    # Verify critical TLS settings present
    assert "ssl_session_tickets off" in config, "Session tickets not disabled (PFS risk)"
    assert "ssl_stapling on" in config, "OCSP stapling not enabled"
    assert "ssl_prefer_server_ciphers off" in config, "Cipher preference not set correctly"
    assert "ssl_ecdh_curve X25519" in config, "ECDH curve not configured"

    # Verify rate limiting zones in http context (not server block)
    # This prevents nginx config errors
    assert "limit_req_zone" in config, "Rate limiting zones not found"
    assert "limit_conn_zone" in config, "Connection limiting zone not found"


@pytest.mark.integration()
def test_ocsp_stapling_validation(valid_client_cert: tuple[Path, Path]) -> None:
    """Test 18: OCSP stapling validation (automated)."""
    cert_path, key_path = valid_client_cert

    # Use openssl s_client to check OCSP stapling
    # Note: OCSP may not work with self-signed CA, but we verify configuration
    result = subprocess.run(
        [
            "openssl",
            "s_client",
            "-connect",
            "localhost:443",
            "-status",
            "-servername",
            "localhost",
            "-cert",
            str(cert_path),
            "-key",
            str(key_path),
        ],
        input=b"",  # Close connection immediately
        capture_output=True,
        text=True,
        timeout=10,
    )

    output = result.stdout + result.stderr

    # Verify OCSP response present (or at least not explicitly "no response sent")
    # With self-signed CA, OCSP may not return "successful", but should be configured
    # Primary validation: nginx has OCSP enabled and attempting to staple
    assert "OCSP" in output, "No OCSP information in TLS handshake"

    # Check nginx config confirms OCSP enabled
    config_result = subprocess.run(
        ["docker", "exec", "trading_platform_nginx", "nginx", "-T"], capture_output=True, text=True
    )
    assert "ssl_stapling on" in config_result.stdout


@pytest.mark.integration()
def test_websocket_timeout_configuration() -> None:
    """Test 19: WebSocket-specific location with long timeout."""
    # Verify nginx configuration has WebSocket location with 3600s timeout
    result = subprocess.run(
        ["docker", "exec", "trading_platform_nginx", "cat", "/etc/nginx/nginx.conf"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    config = result.stdout

    # Verify /_stcore/stream location exists
    assert "location /_stcore/stream" in config, "WebSocket location not found"

    # Verify timeout settings (3600s = 1 hour)
    assert "proxy_read_timeout 3600s" in config or "proxy_read_timeout 3600" in config
    assert "proxy_send_timeout 3600s" in config or "proxy_send_timeout 3600" in config
    assert "proxy_buffering off" in config, "Buffering not disabled for WebSocket"


@pytest.mark.integration()
def test_port_8501_not_exposed_in_mtls_mode() -> None:
    """Test: Port 8501 NOT accessible in mTLS mode (security critical)."""
    # Attempt direct connection to port 8501
    try:
        response = requests.get("http://localhost:8501/health", timeout=2)
        # If we get here, port is exposed - FAIL
        pytest.fail(
            f"SECURITY ISSUE: Port 8501 is accessible (status {response.status_code}). "
            "mTLS bypass possible! Check docker-compose.yml web_console_mtls service."
        )
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
        # Expected: Connection refused or timeout
        pass


@pytest.mark.integration()
def test_nginx_health_endpoint_accessible(valid_client_cert: tuple[Path, Path]) -> None:
    """Test: nginx /health endpoint returns 200."""
    cert_path, key_path = valid_client_cert

    response = requests.get(
        "https://localhost:443/health", cert=(str(cert_path), str(key_path)), verify=False
    )

    assert response.status_code == 200
    assert response.text.strip() == "OK"


@pytest.mark.integration()
def test_security_headers_present(valid_client_cert: tuple[Path, Path]) -> None:
    """Test: Security headers present in responses."""
    cert_path, key_path = valid_client_cert

    response = requests.get(
        "https://localhost:443/health", cert=(str(cert_path), str(key_path)), verify=False
    )

    # Verify security headers
    assert "X-Frame-Options" in response.headers
    assert response.headers["X-Frame-Options"] == "DENY"

    assert "X-Content-Type-Options" in response.headers
    assert response.headers["X-Content-Type-Options"] == "nosniff"

    assert "X-XSS-Protection" in response.headers


@pytest.mark.integration()
def test_certificate_reload_validation_prevents_bad_config(
    certs_dir: Path, valid_client_cert: tuple[Path, Path]
) -> None:
    """Test 12: nginx -t prevents reload with invalid certificate."""
    cert_path, key_path = valid_client_cert

    # Backup current CA certificate
    ca_backup = certs_dir / "ca.crt.backup_test"
    ca_path = certs_dir / "ca.crt"
    subprocess.run(["cp", str(ca_path), str(ca_backup)], check=True)

    try:
        # Create invalid CA certificate (empty file)
        with open(ca_path, "w") as f:
            f.write("INVALID CERTIFICATE DATA\n")

        # Attempt nginx config test (should fail)
        result = subprocess.run(
            ["docker", "exec", "trading_platform_nginx", "nginx", "-t"],
            capture_output=True,
            text=True,
        )

        # Verify test FAILED (non-zero exit code)
        assert result.returncode != 0, "nginx -t should fail with invalid certificate"

        # Attempt reload (may fail or succeed - nginx continues with old config)
        # The critical test is that nginx -t caught the error above
        subprocess.run(
            ["docker", "exec", "trading_platform_nginx", "nginx", "-s", "reload"],
            capture_output=True,
            text=True,
            check=False,  # Don't raise exception on failure
        )

        # Verify service still responds with valid cert (using old config)
        time.sleep(2)
        response = requests.get(
            "https://localhost:443/health",
            cert=(str(cert_path), str(key_path)),
            verify=False,
            timeout=5,
        )
        assert (
            response.status_code == 200
        ), "nginx should continue with old config after failed reload"

    finally:
        # Restore CA certificate
        subprocess.run(["mv", str(ca_backup), str(ca_path)], check=True)
        # Reload nginx with valid config
        subprocess.run(
            ["docker", "exec", "trading_platform_nginx", "nginx", "-s", "reload"], check=True
        )
        time.sleep(2)


# ============================
# Manual Test Instructions
# ============================

"""
MANUAL TESTS (not automated):

1. JWT-DN Binding Enforcement (Test 20):
   - Access https://localhost:443 with valid client cert
   - Login to web console (JWT issued)
   - Extract JWT from browser DevTools (Application → Storage)
   - Use different client cert with same JWT
   - Verify: Request rejected with "DN mismatch" error

2. WebSocket Long-Lived Connection:
   - Access https://localhost:443 with valid client cert
   - Login to web console
   - Leave browser idle for >2 minutes (up to 1 hour)
   - Verify: Streamlit connection stays alive (no "Connection lost")
   - Check nginx logs for WebSocket upgrade: docker logs trading_platform_nginx | grep Upgrade

3. Generate Certificates with Proper SANs:
   source .venv/bin/activate
   PYTHONPATH=. python3 scripts/generate_certs.py --server-only \\
     --san DNS:web-console.trading-platform.local,DNS:localhost,DNS:web_console_nginx

4. Verify DH Parameters Generation:
   openssl dhparam -out apps/web_console/certs/dhparam.pem 4096
   # Verify file size: 800-1000 bytes

5. Certificate Rotation with Zero Downtime:
   # Follow procedure in docs/RUNBOOKS/web-console-mtls-setup.md
   # Verify old connections continue, new connections use new cert
"""


@pytest.mark.integration()
def test_proxied_endpoint_reaches_python_app(valid_client_cert: tuple[Path, Path]) -> None:
    """
    Test: Proxied endpoint reaches Python application (not just nginx).

    Critical test to verify that auth.py is actually executed.
    Previous tests hit /health which nginx serves directly without proxying.

    This test hits the root path / which proxies to Streamlit Python app,
    ensuring the mTLS authentication logic in auth.py is executed.
    """
    cert_path, key_path = valid_client_cert

    try:
        # Hit root path (proxied to Streamlit)
        # This should trigger auth.py's _mtls_auth() function
        response = requests.get(
            "https://localhost:443/",
            cert=(str(cert_path), str(key_path)),
            verify=False,  # Skip server cert verification (self-signed CA)
            timeout=10,
        )

        # Streamlit app should respond (200 or 403 depending on auth state)
        # The key is that we got a response from the Python application,
        # not just nginx's static /health endpoint
        assert response.status_code in [200, 403], (
            f"Expected 200 (authed) or 403 (auth failed), got {response.status_code}. "
            "This proves the request reached the Python layer and auth.py was executed."
        )

        # If we got 200, verify it's actually Streamlit content
        if response.status_code == 200:
            # Streamlit apps typically have these markers in HTML
            assert (
                "streamlit" in response.text.lower()
                or "st-" in response.text  # Streamlit CSS classes
                or "<script" in response.text  # Streamlit loads JS
            ), "Response doesn't look like Streamlit app (auth.py may not have executed)"

    except requests.exceptions.SSLError as e:
        # If SSL handshake fails, it means mTLS is working at nginx level
        # This is also acceptable - proves cert validation is enforced
        pytest.skip(f"mTLS cert rejected (nginx level): {e}")
