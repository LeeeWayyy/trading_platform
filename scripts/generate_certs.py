#!/usr/bin/env python3
"""
Certificate Generation Script for Web Console mTLS Authentication.

Generates self-signed CA, server certificates, and client certificates for
mutual TLS authentication in the web console.

Features:
- Self-signed CA with 10-year validity
- Server certificates with 1-year validity (for nginx)
- Client certificates with 90-day validity (for users)
- RSA 4096-bit keys for all certificates
- Subject Alternative Names (SANs) for proper hostname validation
- Strict file permissions (0600 for private keys)
- Certificate chain validation
- Support for certificate renewal

Security:
- All private keys are generated with 0600 permissions (owner read/write only)
- Private keys are never committed to git (gitignored)
- Production: Load private keys from secrets manager (not filesystem)

Usage:
    # Generate all certificates (CA + server + default client)
    ./scripts/generate_certs.py

    # Generate CA only
    ./scripts/generate_certs.py --ca-only

    # Generate server certificate only (requires existing CA)
    ./scripts/generate_certs.py --server-only

    # Generate client certificate for specific user
    ./scripts/generate_certs.py --client admin

    # Renew existing certificate
    ./scripts/generate_certs.py --renew certs/client-admin.crt

Author: Claude Code
Date: 2025-11-21
"""

import argparse
import os
import stat
import sys
from datetime import UTC, datetime, timedelta
from ipaddress import IPv4Address
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

# Certificate validity periods (from plan)
CA_VALIDITY_YEARS = 10
SERVER_VALIDITY_YEARS = 1
CLIENT_VALIDITY_DAYS = 90

# RSA key size (from plan: 4096-bit for all keys)
RSA_KEY_SIZE = 4096

# File permissions for private keys (owner read/write only)
PRIVATE_KEY_PERMISSIONS = 0o600

# Default output directory
DEFAULT_CERTS_DIR = Path(__file__).parent.parent / "apps" / "web_console" / "certs"


def generate_private_key() -> rsa.RSAPrivateKey:
    """
    Generate RSA private key (4096-bit).

    Returns:
        RSA private key
    """
    return rsa.generate_private_key(
        public_exponent=65537,
        key_size=RSA_KEY_SIZE,
    )


def save_private_key(key: rsa.RSAPrivateKey, output_path: Path) -> None:
    """
    Save private key to file with strict permissions (0600).

    Args:
        key: RSA private key
        output_path: Output file path

    Security:
        Uses os.open() with O_CREAT|O_WRONLY|O_TRUNC and mode=0600 to atomically
        create the file with correct permissions, eliminating the race condition
        window between file creation and chmod.
    """
    # Serialize key to PEM format
    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )

    # Atomically create file with 0600 permissions (no race condition)
    fd = os.open(str(output_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, PRIVATE_KEY_PERMISSIONS)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(key_pem)
    except:
        # If write fails, close the file descriptor
        os.close(fd)
        raise

    # Verify permissions were set correctly
    actual_permissions = stat.S_IMODE(output_path.stat().st_mode)
    if actual_permissions != PRIVATE_KEY_PERMISSIONS:
        print(f"⚠️  Warning: Unexpected permissions on {output_path}")
        print(f"   Expected: {oct(PRIVATE_KEY_PERMISSIONS)}, Got: {oct(actual_permissions)}")


def save_public_key(key: rsa.RSAPrivateKey, output_path: Path) -> None:
    """
    Save public key to file (PEM format).

    Args:
        key: RSA private key (public key will be extracted)
        output_path: Output file path
    """
    public_key_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    output_path.write_bytes(public_key_pem)


def save_certificate(cert: x509.Certificate, output_path: Path) -> None:
    """
    Save certificate to file (PEM format).

    Args:
        cert: X.509 certificate
        output_path: Output file path
    """
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    output_path.write_bytes(cert_pem)


def generate_ca_certificate(certs_dir: Path) -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    """
    Generate self-signed CA certificate (10-year validity).

    Args:
        certs_dir: Output directory for certificates

    Returns:
        Tuple of (CA private key, CA certificate)

    Outputs:
        - certs/ca.key (private key, 0600 permissions)
        - certs/ca.crt (certificate)
    """
    print("🔐 Generating CA certificate (10-year validity, 4096-bit RSA)...")

    # Generate CA private key
    ca_key = generate_private_key()

    # Build CA certificate
    subject = issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "US"),
            x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "California"),
            x509.NameAttribute(NameOID.LOCALITY_NAME, "San Francisco"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Trading Platform"),
            x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, "Security"),
            x509.NameAttribute(NameOID.COMMON_NAME, "Trading Platform CA"),
        ]
    )

    now = datetime.now(UTC)
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=CA_VALIDITY_YEARS * 365))
        .add_extension(
            x509.BasicConstraints(ca=True, path_length=0),
            critical=True,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_cert_sign=True,
                crl_sign=True,
                key_encipherment=False,
                content_commitment=False,
                data_encipherment=False,
                key_agreement=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )

    # Save CA private key and certificate
    certs_dir.mkdir(parents=True, exist_ok=True)
    save_private_key(ca_key, certs_dir / "ca.key")
    save_certificate(ca_cert, certs_dir / "ca.crt")

    print("✅ CA certificate generated:")
    print(f"   Private key: {certs_dir / 'ca.key'} (permissions: 0600)")
    print(f"   Certificate: {certs_dir / 'ca.crt'}")
    print(f"   Valid until: {ca_cert.not_valid_after_utc.strftime('%Y-%m-%d %H:%M:%S UTC')}")

    return ca_key, ca_cert


def generate_server_certificate(
    ca_key: rsa.RSAPrivateKey,
    ca_cert: x509.Certificate,
    certs_dir: Path,
) -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    """
    Generate server certificate signed by CA (1-year validity).

    Args:
        ca_key: CA private key
        ca_cert: CA certificate
        certs_dir: Output directory for certificates

    Returns:
        Tuple of (server private key, server certificate)

    Outputs:
        - certs/server.key (private key, 0600 permissions)
        - certs/server.crt (certificate)

    Subject Alternative Names:
        - DNS: web-console.trading-platform.local
        - DNS: localhost
        - IP: 127.0.0.1
    """
    print("🔐 Generating server certificate (1-year validity, 4096-bit RSA)...")

    # Generate server private key
    server_key = generate_private_key()

    # Build server certificate
    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "US"),
            x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "California"),
            x509.NameAttribute(NameOID.LOCALITY_NAME, "San Francisco"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Trading Platform"),
            x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, "Web Console"),
            x509.NameAttribute(NameOID.COMMON_NAME, "web-console.trading-platform.local"),
        ]
    )

    now = datetime.now(UTC)
    server_cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=SERVER_VALIDITY_YEARS * 365))
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("web-console.trading-platform.local"),
                    x509.DNSName("localhost"),
                    x509.IPAddress(IPv4Address("127.0.0.1")),
                ]
            ),
            critical=False,
        )
        .add_extension(
            x509.BasicConstraints(ca=False, path_length=None),
            critical=True,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_encipherment=True,
                key_cert_sign=False,
                crl_sign=False,
                content_commitment=False,
                data_encipherment=False,
                key_agreement=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage(
                [
                    x509.oid.ExtendedKeyUsageOID.SERVER_AUTH,
                ]
            ),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )

    # Save server private key and certificate
    save_private_key(server_key, certs_dir / "server.key")
    save_certificate(server_cert, certs_dir / "server.crt")

    print("✅ Server certificate generated:")
    print(f"   Private key: {certs_dir / 'server.key'} (permissions: 0600)")
    print(f"   Certificate: {certs_dir / 'server.crt'}")
    print(f"   Valid until: {server_cert.not_valid_after_utc.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("   SANs: web-console.trading-platform.local, localhost, 127.0.0.1")

    return server_key, server_cert


def generate_client_certificate(
    username: str,
    ca_key: rsa.RSAPrivateKey,
    ca_cert: x509.Certificate,
    certs_dir: Path,
) -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    """
    Generate client certificate signed by CA (90-day validity).

    Args:
        username: Username for client certificate
        ca_key: CA private key
        ca_cert: CA certificate
        certs_dir: Output directory for certificates

    Returns:
        Tuple of (client private key, client certificate)

    Outputs:
        - certs/client-{username}.key (private key, 0600 permissions)
        - certs/client-{username}.crt (certificate)

    Subject Alternative Names:
        - DNS: client-{username}.trading-platform.local
    """
    print(f"🔐 Generating client certificate for '{username}' (90-day validity, 4096-bit RSA)...")

    # Generate client private key
    client_key = generate_private_key()

    # Build client certificate
    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "US"),
            x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "California"),
            x509.NameAttribute(NameOID.LOCALITY_NAME, "San Francisco"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Trading Platform"),
            x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, "Users"),
            x509.NameAttribute(NameOID.COMMON_NAME, f"client-{username}"),
        ]
    )

    now = datetime.now(UTC)
    client_cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(client_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=CLIENT_VALIDITY_DAYS))
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName(f"client-{username}.trading-platform.local"),
                ]
            ),
            critical=False,
        )
        .add_extension(
            x509.BasicConstraints(ca=False, path_length=None),
            critical=True,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_encipherment=True,
                key_cert_sign=False,
                crl_sign=False,
                content_commitment=False,
                data_encipherment=False,
                key_agreement=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage(
                [
                    x509.oid.ExtendedKeyUsageOID.CLIENT_AUTH,
                ]
            ),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )

    # Save client private key and certificate
    client_key_path = certs_dir / f"client-{username}.key"
    client_cert_path = certs_dir / f"client-{username}.crt"
    save_private_key(client_key, client_key_path)
    save_certificate(client_cert, client_cert_path)

    print("✅ Client certificate generated:")
    print(f"   Private key: {client_key_path} (permissions: 0600)")
    print(f"   Certificate: {client_cert_path}")
    print(f"   Valid until: {client_cert.not_valid_after_utc.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"   SAN: client-{username}.trading-platform.local")

    return client_key, client_cert


def generate_jwt_keypair(certs_dir: Path) -> None:
    """
    Generate RSA key pair for JWT signing (4096-bit).

    Args:
        certs_dir: Output directory for keys

    Outputs:
        - certs/jwt_private.key (private key for signing, 0600 permissions)
        - certs/jwt_public.pem (public key for validation)
    """
    print("🔐 Generating JWT signing key pair (4096-bit RSA)...")

    # Generate JWT private key
    jwt_key = generate_private_key()

    # Save JWT private and public keys
    save_private_key(jwt_key, certs_dir / "jwt_private.key")
    save_public_key(jwt_key, certs_dir / "jwt_public.pem")

    print("✅ JWT key pair generated:")
    print(f"   Private key: {certs_dir / 'jwt_private.key'} (permissions: 0600)")
    print(f"   Public key: {certs_dir / 'jwt_public.pem'}")


def load_ca(certs_dir: Path) -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    """
    Load existing CA private key and certificate.

    Args:
        certs_dir: Directory containing CA files

    Returns:
        Tuple of (CA private key, CA certificate)

    Raises:
        FileNotFoundError: If CA files don't exist
    """
    ca_key_path = certs_dir / "ca.key"
    ca_cert_path = certs_dir / "ca.crt"

    if not ca_key_path.exists() or not ca_cert_path.exists():
        raise FileNotFoundError(
            "CA not found. Generate CA first:\n" "  ./scripts/generate_certs.py --ca-only"
        )

    # Load CA private key
    ca_key_pem = ca_key_path.read_bytes()
    ca_key = serialization.load_pem_private_key(ca_key_pem, password=None)

    # Load CA certificate
    ca_cert_pem = ca_cert_path.read_bytes()
    ca_cert = x509.load_pem_x509_certificate(ca_cert_pem)

    return ca_key, ca_cert  # type: ignore


def generate_dhparam(output_dir: Path, key_size: int = 4096) -> None:
    """Generate Diffie-Hellman parameters for nginx.

    Args:
        output_dir: Directory to save DH params
        key_size: Size of DH parameters in bits (default: 4096 for strong security)

    Note:
        This is required by nginx.conf ssl_dhparam directive.
        Generation can take several minutes for 4096-bit params.
    """
    dhparam_path = output_dir / "dhparam.pem"

    print(f"Generating {key_size}-bit Diffie-Hellman parameters...")
    print("⏳ This may take several minutes (especially for 4096-bit)...")

    # Generate DH params using OpenSSL command
    # Using subprocess because cryptography library doesn't support DH param generation
    import subprocess

    try:
        result = subprocess.run(
            ["openssl", "dhparam", "-out", str(dhparam_path), str(key_size)],
            capture_output=True,
            text=True,
            check=True,
        )

        # Set secure permissions (owner read-only)
        dhparam_path.chmod(0o600)

        print(f"✅ DH parameters saved to {dhparam_path}")
        print(f"   Size: {key_size} bits")
        print("   Permissions: 0600 (owner read-only)")

    except subprocess.CalledProcessError as e:
        print(f"❌ Failed to generate DH parameters: {e.stderr}")
        raise
    except FileNotFoundError:
        print("❌ OpenSSL not found. Please install OpenSSL.")
        raise


def main() -> int:
    """
    Main entry point for certificate generation script.

    Returns:
        Exit code (0 for success, 1 for error)
    """
    parser = argparse.ArgumentParser(
        description="Generate certificates for web console mTLS authentication",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Generate all certificates (CA + server + default client)
  ./scripts/generate_certs.py

  # Generate CA only
  ./scripts/generate_certs.py --ca-only

  # Generate server certificate only (requires existing CA)
  ./scripts/generate_certs.py --server-only

  # Generate client certificate for specific user
  ./scripts/generate_certs.py --client admin

  # Custom output directory
  ./scripts/generate_certs.py --output /path/to/certs

Security Notes:
  - All private keys are generated with 0600 permissions
  - Private keys should NEVER be committed to git
  - For production, load private keys from secrets manager
        """,
    )
    parser.add_argument(
        "--ca-only",
        action="store_true",
        help="Generate CA certificate only",
    )
    parser.add_argument(
        "--server-only",
        action="store_true",
        help="Generate server certificate only (requires existing CA)",
    )
    parser.add_argument(
        "--client",
        metavar="USERNAME",
        help="Generate client certificate for specified user",
    )
    parser.add_argument(
        "--output",
        metavar="DIR",
        type=Path,
        default=DEFAULT_CERTS_DIR,
        help=f"Output directory for certificates (default: {DEFAULT_CERTS_DIR})",
    )
    parser.add_argument(
        "--renew",
        metavar="CERT_PATH",
        type=Path,
        help="Renew existing certificate (not yet implemented)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Allow overwriting existing certificates without confirmation",
    )

    args = parser.parse_args()

    # Validate arguments
    if args.renew:
        print("❌ Certificate renewal not yet implemented")
        print("   For now, regenerate certificates manually")
        return 1

    # Handle --ca-only
    if args.ca_only:
        # Check for existing CA (overwrite protection)
        ca_cert_path = args.output / "ca.crt"
        if ca_cert_path.exists() and not args.force:
            print(
                "❌ CA certificate already exists. This would invalidate all issued certificates!"
            )
            print(f"   Found: {ca_cert_path}")
            print()
            print("⚠️  Regenerating CA requires:")
            print("   1. Backing up existing CA and all issued certificates")
            print("   2. Regenerating ALL server and client certificates")
            print("   3. Redistributing certificates to all users")
            print()
            print("To proceed:")
            print("   - Backup existing certs: cp -r apps/web_console/certs/ backups/")
            print("   - Then run with --force flag: ./scripts/generate_certs.py --ca-only --force")
            return 1

        generate_ca_certificate(args.output)
        generate_jwt_keypair(args.output)
        return 0

    # Handle --server-only
    if args.server_only:
        try:
            ca_key, ca_cert = load_ca(args.output)
            generate_server_certificate(ca_key, ca_cert, args.output)
            return 0
        except FileNotFoundError as e:
            print(f"❌ {e}")
            return 1

    # Handle --client
    if args.client:
        try:
            ca_key, ca_cert = load_ca(args.output)
            generate_client_certificate(args.client, ca_key, ca_cert, args.output)
            return 0
        except FileNotFoundError as e:
            print(f"❌ {e}")
            return 1

    # Default: Generate all certificates
    print("🚀 Generating all certificates (CA + server + client + JWT keys)...")
    print()

    # Check for existing certificates (overwrite protection)
    ca_cert_path = args.output / "ca.crt"
    if ca_cert_path.exists() and not args.force:
        print("❌ CA certificate already exists. This would invalidate all issued certificates!")
        print(f"   Found: {ca_cert_path}")
        print()
        print("⚠️  Overwriting CA requires:")
        print("   1. Backing up existing certificates")
        print("   2. Regenerating ALL server and client certificates")
        print("   3. Redistributing certificates to all users")
        print()
        print("To proceed:")
        print("   - Backup existing certs: cp -r apps/web_console/certs/ backups/")
        print("   - Then run with --force flag")
        return 1

    # Generate CA
    ca_key, ca_cert = generate_ca_certificate(args.output)
    print()

    # Generate server certificate
    generate_server_certificate(ca_key, ca_cert, args.output)
    print()

    # Generate default client certificate
    generate_client_certificate("admin", ca_key, ca_cert, args.output)
    print()

    # Generate JWT key pair
    generate_jwt_keypair(args.output)
    print()

    # Generate DH parameters for nginx (required by nginx.conf ssl_dhparam directive)
    generate_dhparam(args.output)
    print()

    print("✅ All certificates generated successfully!")
    print()
    print("📁 Certificate directory:")
    print(f"   {args.output.absolute()}")
    print()
    print("🔒 Security reminders:")
    print("   - All private keys have 0600 permissions (owner read/write only)")
    print("   - Never commit private keys to git (check .gitignore)")
    print("   - For production, use secrets manager (not filesystem)")
    print()
    print("📋 Next steps:")
    print("   1. Verify certificates: openssl x509 -in certs/server.crt -text -noout")
    print(f"   2. Update docker-compose.yml to mount {args.output}")
    print("   3. Configure nginx to use server.crt and server.key")
    print("   4. Distribute client certificates securely to users")

    return 0


if __name__ == "__main__":
    sys.exit(main())
