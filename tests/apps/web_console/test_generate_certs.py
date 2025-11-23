"""
Unit tests for scripts/generate_certs.py

Tests certificate generation script for:
- CA certificate generation and validation
- Server certificate generation with correct SANs
- Client certificate generation
- JWT key pair generation
- Certificate chain validation
- Key size validation (4096-bit RSA)
- File permission validation (0600 for private keys)
- Certificate expiration date validation
"""

import os
import subprocess
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtensionOID, NameOID

# RSA key size expected (from generate_certs.py)
EXPECTED_RSA_KEY_SIZE = 4096

# Expected private key permissions (owner read/write only)
EXPECTED_PRIVATE_KEY_PERMISSIONS = 0o600


@pytest.fixture()
def temp_cert_dir() -> Path:
    """Create temporary directory for certificate generation."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


def run_generate_certs(args: list[str], output_dir: Path) -> subprocess.CompletedProcess:
    """
    Run scripts/generate_certs.py with given arguments.

    Args:
        args: Command-line arguments (e.g., ["--ca-only"])
        output_dir: Directory for certificate output

    Returns:
        CompletedProcess with stdout, stderr, returncode
    """
    script_path = Path(__file__).parent.parent.parent.parent / "scripts" / "generate_certs.py"
    cmd = ["python3", str(script_path), "--output", str(output_dir)] + args
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result


def load_certificate(cert_path: Path) -> x509.Certificate:
    """Load X.509 certificate from PEM file."""
    cert_pem = cert_path.read_bytes()
    return x509.load_pem_x509_certificate(cert_pem, default_backend())


def load_private_key(key_path: Path) -> rsa.RSAPrivateKey:
    """Load RSA private key from PEM file."""
    key_pem = key_path.read_bytes()
    return serialization.load_pem_private_key(key_pem, password=None, backend=default_backend())


def verify_certificate_chain(ca_cert: x509.Certificate, cert: x509.Certificate) -> bool:
    """
    Verify that cert is signed by ca_cert.

    Args:
        ca_cert: CA certificate
        cert: Certificate to verify

    Returns:
        True if signature verification succeeds
    """
    try:
        ca_public_key = ca_cert.public_key()
        ca_public_key.verify(
            cert.signature,
            cert.tbs_certificate_bytes,
            padding=cert.signature_algorithm_parameters,
            algorithm=cert.signature_hash_algorithm,
        )
        return True
    except Exception:
        return False


class TestCAGeneration:
    """Test CA certificate generation."""

    def test_ca_only_flag_generates_ca_cert_and_key(self, temp_cert_dir: Path):
        """Test --ca-only generates CA certificate and private key."""
        result = run_generate_certs(["--ca-only"], temp_cert_dir)

        assert result.returncode == 0, f"CA generation failed: {result.stderr}"

        ca_crt = temp_cert_dir / "ca.crt"
        ca_key = temp_cert_dir / "ca.key"

        assert ca_crt.exists(), "CA certificate not generated"
        assert ca_key.exists(), "CA private key not generated"

    def test_ca_certificate_validity_period_is_10_years(self, temp_cert_dir: Path):
        """Test CA certificate has 10-year validity."""
        run_generate_certs(["--ca-only"], temp_cert_dir)
        ca_cert = load_certificate(temp_cert_dir / "ca.crt")

        not_before = ca_cert.not_valid_before_utc
        not_after = ca_cert.not_valid_after_utc
        validity_days = (not_after - not_before).days

        # 10 years = ~3650-3653 days (accounting for leap years)
        assert 3648 <= validity_days <= 3655, f"CA validity is {validity_days} days, expected ~3650"

    def test_ca_certificate_uses_rsa_4096(self, temp_cert_dir: Path):
        """Test CA certificate uses RSA 4096-bit key."""
        run_generate_certs(["--ca-only"], temp_cert_dir)
        ca_key = load_private_key(temp_cert_dir / "ca.key")

        assert isinstance(ca_key, rsa.RSAPrivateKey), "CA key is not RSA"
        assert (
            ca_key.key_size == EXPECTED_RSA_KEY_SIZE
        ), f"CA key size is {ca_key.key_size}, expected {EXPECTED_RSA_KEY_SIZE}"

    def test_ca_private_key_has_0600_permissions(self, temp_cert_dir: Path):
        """Test CA private key has 0600 permissions (owner read/write only)."""
        run_generate_certs(["--ca-only"], temp_cert_dir)
        ca_key = temp_cert_dir / "ca.key"

        file_permissions = os.stat(ca_key).st_mode & 0o777
        assert (
            file_permissions == EXPECTED_PRIVATE_KEY_PERMISSIONS
        ), f"CA key permissions are {oct(file_permissions)}, expected {oct(EXPECTED_PRIVATE_KEY_PERMISSIONS)}"

    def test_ca_certificate_subject_fields(self, temp_cert_dir: Path):
        """Test CA certificate has correct subject fields."""
        run_generate_certs(["--ca-only"], temp_cert_dir)
        ca_cert = load_certificate(temp_cert_dir / "ca.crt")

        subject = ca_cert.subject

        # Check common name
        cn = subject.get_attributes_for_oid(NameOID.COMMON_NAME)
        assert len(cn) == 1, "CA certificate missing Common Name"
        assert (
            cn[0].value == "Trading Platform CA"
        ), f"CA CN is '{cn[0].value}', expected 'Trading Platform CA'"

        # Check organization
        org = subject.get_attributes_for_oid(NameOID.ORGANIZATION_NAME)
        assert len(org) == 1, "CA certificate missing Organization"
        assert (
            org[0].value == "Trading Platform"
        ), f"CA Org is '{org[0].value}', expected 'Trading Platform'"


class TestServerGeneration:
    """Test server certificate generation."""

    def test_server_only_flag_generates_server_cert_and_key(self, temp_cert_dir: Path):
        """Test --server-only generates server certificate and key (requires CA)."""
        # Generate CA first
        run_generate_certs(["--ca-only"], temp_cert_dir)

        # Generate server certificate
        result = run_generate_certs(["--server-only"], temp_cert_dir)

        assert result.returncode == 0, f"Server generation failed: {result.stderr}"

        server_crt = temp_cert_dir / "server.crt"
        server_key = temp_cert_dir / "server.key"

        assert server_crt.exists(), "Server certificate not generated"
        assert server_key.exists(), "Server private key not generated"

    def test_server_certificate_validity_period_is_1_year(self, temp_cert_dir: Path):
        """Test server certificate has 1-year validity."""
        run_generate_certs(["--ca-only"], temp_cert_dir)
        run_generate_certs(["--server-only"], temp_cert_dir)

        server_cert = load_certificate(temp_cert_dir / "server.crt")

        not_before = server_cert.not_valid_before_utc
        not_after = server_cert.not_valid_after_utc
        validity_days = (not_after - not_before).days

        # 1 year = 365 or 366 days (leap year)
        assert 364 <= validity_days <= 366, f"Server validity is {validity_days} days, expected 365"

    def test_server_certificate_uses_rsa_4096(self, temp_cert_dir: Path):
        """Test server certificate uses RSA 4096-bit key."""
        run_generate_certs(["--ca-only"], temp_cert_dir)
        run_generate_certs(["--server-only"], temp_cert_dir)

        server_key = load_private_key(temp_cert_dir / "server.key")

        assert isinstance(server_key, rsa.RSAPrivateKey), "Server key is not RSA"
        assert (
            server_key.key_size == EXPECTED_RSA_KEY_SIZE
        ), f"Server key size is {server_key.key_size}, expected {EXPECTED_RSA_KEY_SIZE}"

    def test_server_certificate_subject_alternative_names(self, temp_cert_dir: Path):
        """Test server certificate has correct SANs (DNS and IP)."""
        run_generate_certs(["--ca-only"], temp_cert_dir)
        run_generate_certs(["--server-only"], temp_cert_dir)

        server_cert = load_certificate(temp_cert_dir / "server.crt")

        # Extract Subject Alternative Name extension
        san_extension = server_cert.extensions.get_extension_for_oid(
            ExtensionOID.SUBJECT_ALTERNATIVE_NAME
        )
        san = san_extension.value

        # Expected SANs (from plan)
        expected_dns = {"web-console.trading-platform.local", "localhost"}
        expected_ip = {"127.0.0.1"}

        # Extract DNS names and IP addresses from SAN extension
        # Note: cryptography returns strings directly for DNS names
        dns_names = set()
        ip_addresses = set()
        for san_value in san:
            if isinstance(san_value, x509.DNSName):
                dns_names.add(san_value.value)
            elif isinstance(san_value, x509.IPAddress):
                ip_addresses.add(str(san_value.value))

        assert dns_names == expected_dns, f"DNS SANs are {dns_names}, expected {expected_dns}"
        assert ip_addresses == expected_ip, f"IP SANs are {ip_addresses}, expected {expected_ip}"

    def test_server_certificate_signed_by_ca(self, temp_cert_dir: Path):
        """Test server certificate is signed by CA."""
        run_generate_certs(["--ca-only"], temp_cert_dir)
        run_generate_certs(["--server-only"], temp_cert_dir)

        ca_cert = load_certificate(temp_cert_dir / "ca.crt")
        server_cert = load_certificate(temp_cert_dir / "server.crt")

        # Verify certificate chain
        assert verify_certificate_chain(ca_cert, server_cert), "Server certificate not signed by CA"

    def test_server_private_key_has_0600_permissions(self, temp_cert_dir: Path):
        """Test server private key has 0600 permissions."""
        run_generate_certs(["--ca-only"], temp_cert_dir)
        run_generate_certs(["--server-only"], temp_cert_dir)

        server_key = temp_cert_dir / "server.key"
        file_permissions = os.stat(server_key).st_mode & 0o777

        assert (
            file_permissions == EXPECTED_PRIVATE_KEY_PERMISSIONS
        ), f"Server key permissions are {oct(file_permissions)}, expected {oct(EXPECTED_PRIVATE_KEY_PERMISSIONS)}"


class TestClientGeneration:
    """Test client certificate generation."""

    def test_client_flag_generates_client_cert_and_key(self, temp_cert_dir: Path):
        """Test --client <username> generates client certificate and key."""
        run_generate_certs(["--ca-only"], temp_cert_dir)
        result = run_generate_certs(["--client", "alice"], temp_cert_dir)

        assert result.returncode == 0, f"Client generation failed: {result.stderr}"

        client_crt = temp_cert_dir / "client-alice.crt"
        client_key = temp_cert_dir / "client-alice.key"

        assert client_crt.exists(), "Client certificate not generated"
        assert client_key.exists(), "Client private key not generated"

    def test_client_certificate_validity_period_is_90_days(self, temp_cert_dir: Path):
        """Test client certificate has 90-day validity."""
        run_generate_certs(["--ca-only"], temp_cert_dir)
        run_generate_certs(["--client", "bob"], temp_cert_dir)

        client_cert = load_certificate(temp_cert_dir / "client-bob.crt")

        not_before = client_cert.not_valid_before_utc
        not_after = client_cert.not_valid_after_utc
        validity_days = (not_after - not_before).days

        assert 89 <= validity_days <= 91, f"Client validity is {validity_days} days, expected 90"

    def test_client_certificate_uses_rsa_4096(self, temp_cert_dir: Path):
        """Test client certificate uses RSA 4096-bit key."""
        run_generate_certs(["--ca-only"], temp_cert_dir)
        run_generate_certs(["--client", "charlie"], temp_cert_dir)

        client_key = load_private_key(temp_cert_dir / "client-charlie.key")

        assert isinstance(client_key, rsa.RSAPrivateKey), "Client key is not RSA"
        assert (
            client_key.key_size == EXPECTED_RSA_KEY_SIZE
        ), f"Client key size is {client_key.key_size}, expected {EXPECTED_RSA_KEY_SIZE}"

    def test_client_certificate_subject_alternative_name(self, temp_cert_dir: Path):
        """Test client certificate has correct SAN (DNS name)."""
        run_generate_certs(["--ca-only"], temp_cert_dir)
        run_generate_certs(["--client", "david"], temp_cert_dir)

        client_cert = load_certificate(temp_cert_dir / "client-david.crt")

        # Extract Subject Alternative Name extension
        san_extension = client_cert.extensions.get_extension_for_oid(
            ExtensionOID.SUBJECT_ALTERNATIVE_NAME
        )
        san = san_extension.value

        # Extract DNS names
        dns_names = set()
        for san_value in san:
            if isinstance(san_value, x509.DNSName):
                dns_names.add(san_value.value)

        expected_dns = {"client-david.trading-platform.local"}
        assert (
            dns_names == expected_dns
        ), f"Client DNS SANs are {dns_names}, expected {expected_dns}"

    def test_client_certificate_signed_by_ca(self, temp_cert_dir: Path):
        """Test client certificate is signed by CA."""
        run_generate_certs(["--ca-only"], temp_cert_dir)
        run_generate_certs(["--client", "eve"], temp_cert_dir)

        ca_cert = load_certificate(temp_cert_dir / "ca.crt")
        client_cert = load_certificate(temp_cert_dir / "client-eve.crt")

        # Verify certificate chain
        assert verify_certificate_chain(ca_cert, client_cert), "Client certificate not signed by CA"

    def test_client_private_key_has_0600_permissions(self, temp_cert_dir: Path):
        """Test client private key has 0600 permissions."""
        run_generate_certs(["--ca-only"], temp_cert_dir)
        run_generate_certs(["--client", "frank"], temp_cert_dir)

        client_key = temp_cert_dir / "client-frank.key"
        file_permissions = os.stat(client_key).st_mode & 0o777

        assert (
            file_permissions == EXPECTED_PRIVATE_KEY_PERMISSIONS
        ), f"Client key permissions are {oct(file_permissions)}, expected {oct(EXPECTED_PRIVATE_KEY_PERMISSIONS)}"

    def test_client_certificate_common_name(self, temp_cert_dir: Path):
        """Test client certificate has correct Common Name (client-{username})."""
        run_generate_certs(["--ca-only"], temp_cert_dir)
        run_generate_certs(["--client", "grace"], temp_cert_dir)

        client_cert = load_certificate(temp_cert_dir / "client-grace.crt")
        subject = client_cert.subject

        cn = subject.get_attributes_for_oid(NameOID.COMMON_NAME)
        assert len(cn) == 1, "Client certificate missing Common Name"
        assert (
            cn[0].value == "client-grace"
        ), f"Client CN is '{cn[0].value}', expected 'client-grace'"


class TestJWTKeyGeneration:
    """Test JWT signing key pair generation."""

    def test_default_generation_creates_jwt_keys(self, temp_cert_dir: Path):
        """Test default generation creates JWT private and public keys."""
        result = run_generate_certs([], temp_cert_dir)

        assert result.returncode == 0, f"Default generation failed: {result.stderr}"

        jwt_private = temp_cert_dir / "jwt_private.key"
        jwt_public = temp_cert_dir / "jwt_public.pem"

        assert jwt_private.exists(), "JWT private key not generated"
        assert jwt_public.exists(), "JWT public key not generated"

    def test_jwt_private_key_uses_rsa_4096(self, temp_cert_dir: Path):
        """Test JWT private key uses RSA 4096-bit."""
        run_generate_certs([], temp_cert_dir)

        jwt_private_key = load_private_key(temp_cert_dir / "jwt_private.key")

        assert isinstance(jwt_private_key, rsa.RSAPrivateKey), "JWT key is not RSA"
        assert (
            jwt_private_key.key_size == EXPECTED_RSA_KEY_SIZE
        ), f"JWT key size is {jwt_private_key.key_size}, expected {EXPECTED_RSA_KEY_SIZE}"

    def test_jwt_public_key_matches_private_key(self, temp_cert_dir: Path):
        """Test JWT public key matches JWT private key."""
        run_generate_certs([], temp_cert_dir)

        jwt_private_key = load_private_key(temp_cert_dir / "jwt_private.key")
        jwt_public_pem = (temp_cert_dir / "jwt_public.pem").read_bytes()

        # Verify public key matches private key
        expected_public_key = jwt_private_key.public_key()
        expected_public_pem = expected_public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )

        assert jwt_public_pem == expected_public_pem, "JWT public key does not match private key"

    def test_jwt_private_key_has_0600_permissions(self, temp_cert_dir: Path):
        """Test JWT private key has 0600 permissions."""
        run_generate_certs([], temp_cert_dir)

        jwt_private = temp_cert_dir / "jwt_private.key"
        file_permissions = os.stat(jwt_private).st_mode & 0o777

        assert (
            file_permissions == EXPECTED_PRIVATE_KEY_PERMISSIONS
        ), f"JWT private key permissions are {oct(file_permissions)}, expected {oct(EXPECTED_PRIVATE_KEY_PERMISSIONS)}"


class TestDefaultGeneration:
    """Test default generation (all certificates)."""

    def test_default_generation_creates_all_certificates(self, temp_cert_dir: Path):
        """Test default run generates CA, server, client-admin, and JWT keys."""
        result = run_generate_certs([], temp_cert_dir)

        assert result.returncode == 0, f"Default generation failed: {result.stderr}"

        # Check all expected files exist
        expected_files = [
            "ca.crt",
            "ca.key",
            "server.crt",
            "server.key",
            "client-admin.crt",
            "client-admin.key",
            "jwt_private.key",
            "jwt_public.pem",
        ]

        for filename in expected_files:
            filepath = temp_cert_dir / filename
            assert filepath.exists(), f"Expected file '{filename}' not generated"

    def test_default_generation_all_private_keys_have_0600_permissions(self, temp_cert_dir: Path):
        """Test all private keys generated with default run have 0600 permissions."""
        run_generate_certs([], temp_cert_dir)

        private_key_files = [
            "ca.key",
            "server.key",
            "client-admin.key",
            "jwt_private.key",
        ]

        for key_file in private_key_files:
            key_path = temp_cert_dir / key_file
            file_permissions = os.stat(key_path).st_mode & 0o777

            assert (
                file_permissions == EXPECTED_PRIVATE_KEY_PERMISSIONS
            ), f"{key_file} permissions are {oct(file_permissions)}, expected {oct(EXPECTED_PRIVATE_KEY_PERMISSIONS)}"


class TestErrorHandling:
    """Test error handling in certificate generation."""

    def test_server_only_fails_without_ca(self, temp_cert_dir: Path):
        """Test --server-only fails gracefully if CA not present."""
        result = run_generate_certs(["--server-only"], temp_cert_dir)

        # Should fail (non-zero return code) because CA does not exist
        assert result.returncode != 0, "Server generation should fail without CA"
        # Error message may be in stdout or stderr
        output = (result.stdout + result.stderr).lower()
        assert "ca" in output, "Error message should mention CA"

    def test_client_only_fails_without_ca(self, temp_cert_dir: Path):
        """Test --client fails gracefully if CA not present."""
        result = run_generate_certs(["--client", "alice"], temp_cert_dir)

        # Should fail because CA does not exist
        assert result.returncode != 0, "Client generation should fail without CA"
        # Error message may be in stdout or stderr
        output = (result.stdout + result.stderr).lower()
        assert "ca" in output, "Error message should mention CA"

    def test_client_flag_requires_username(self, temp_cert_dir: Path):
        """Test --client flag requires username argument."""
        result = run_generate_certs(["--client"], temp_cert_dir)

        # Should fail because --client requires argument
        assert result.returncode != 0, "--client without username should fail"


class TestCertificateExpiration:
    """Test certificate expiration date validation."""

    def test_ca_expiration_is_approximately_10_years_from_now(self, temp_cert_dir: Path):
        """Test CA certificate expires approximately 10 years from generation."""
        run_generate_certs(["--ca-only"], temp_cert_dir)
        ca_cert = load_certificate(temp_cert_dir / "ca.crt")

        now = datetime.now(UTC)
        expected_expiry = now + timedelta(days=3650)

        # Allow 2-day tolerance for test execution time
        expiry_diff = abs((ca_cert.not_valid_after_utc - expected_expiry).days)
        assert expiry_diff <= 2, f"CA expiry is {expiry_diff} days off, expected ~10 years from now"

    def test_server_expiration_is_approximately_1_year_from_now(self, temp_cert_dir: Path):
        """Test server certificate expires approximately 1 year from generation."""
        run_generate_certs(["--ca-only"], temp_cert_dir)
        run_generate_certs(["--server-only"], temp_cert_dir)

        server_cert = load_certificate(temp_cert_dir / "server.crt")

        now = datetime.now(UTC)
        expected_expiry = now + timedelta(days=365)

        # Allow 2-day tolerance
        expiry_diff = abs((server_cert.not_valid_after_utc - expected_expiry).days)
        assert (
            expiry_diff <= 2
        ), f"Server expiry is {expiry_diff} days off, expected ~1 year from now"

    def test_client_expiration_is_approximately_90_days_from_now(self, temp_cert_dir: Path):
        """Test client certificate expires approximately 90 days from generation."""
        run_generate_certs(["--ca-only"], temp_cert_dir)
        run_generate_certs(["--client", "test-user"], temp_cert_dir)

        client_cert = load_certificate(temp_cert_dir / "client-test-user.crt")

        now = datetime.now(UTC)
        expected_expiry = now + timedelta(days=90)

        # Allow 2-day tolerance
        expiry_diff = abs((client_cert.not_valid_after_utc - expected_expiry).days)
        assert (
            expiry_diff <= 2
        ), f"Client expiry is {expiry_diff} days off, expected ~90 days from now"
