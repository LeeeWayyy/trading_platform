#!/usr/bin/env python3
"""Ensure local dev JWT keys exist for web console integrations.

This script only generates the JWT signing keypair used by the web console
and execution gateway. It does not touch CA/server/client certs.
"""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root))
    from scripts.generate_certs import generate_jwt_keypair
    certs_dir = repo_root / "apps" / "web_console_ng" / "certs"
    jwt_private = certs_dir / "jwt_private.key"
    jwt_public = certs_dir / "jwt_public.pem"

    if jwt_private.exists() and jwt_public.exists():
        print(f"✅ JWT keypair already present at {certs_dir}")
        return 0

    certs_dir.mkdir(parents=True, exist_ok=True)
    print(f"🔐 Generating JWT keypair in {certs_dir}")
    generate_jwt_keypair(certs_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
