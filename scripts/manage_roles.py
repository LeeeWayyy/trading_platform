#!/usr/bin/env python3
"""CLI utilities for managing web console roles and strategy access."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime

import psycopg


def get_conn() -> psycopg.Connection:
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL environment variable must be set")
    return psycopg.connect(dsn, autocommit=True)


def bootstrap_admin(args: argparse.Namespace) -> None:
    user_id = args.user_id
    email = args.email
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO user_roles (user_id, role, session_version, updated_by)
            VALUES (%s, 'admin', 1, %s)
            ON CONFLICT (user_id) DO NOTHING
            """,
            (user_id, email),
        )
    print(f"Bootstrapped admin user {user_id}")


def set_role(args: argparse.Namespace) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO user_roles (user_id, role, session_version, updated_by, updated_at)
            VALUES (%s, %s, 1, %s, NOW())
            ON CONFLICT (user_id) DO UPDATE
            SET role = EXCLUDED.role,
                updated_by = EXCLUDED.updated_by,
                updated_at = NOW(),
                session_version = user_roles.session_version + 1
            RETURNING user_id, role, session_version, updated_at
            """,
            (args.user_id, args.role, args.by),
        )
        row = cur.fetchone()
    print(f"Set role: {row}")


def grant_strategy(args: argparse.Namespace) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        # Ensure strategy exists
        cur.execute(
            """
            INSERT INTO strategies (strategy_id, name, description, created_at)
            VALUES (%s, %s, %s, NOW())
            ON CONFLICT (strategy_id) DO NOTHING
            """,
            (args.strategy_id, args.strategy_id, args.description or ""),
        )

        cur.execute(
            """
            INSERT INTO user_strategy_access (user_id, strategy_id, granted_by)
            VALUES (%s, %s, %s)
            ON CONFLICT (user_id, strategy_id) DO NOTHING
            """,
            (args.user_id, args.strategy_id, args.by),
        )
    print(f"Granted {args.strategy_id} to {args.user_id}")


def list_users(_: argparse.Namespace) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT user_id, role, session_version, updated_at FROM user_roles ORDER BY user_id")
        rows = cur.fetchall()
    for row in rows:
        print(f"{row[0]:<35} {row[1]:<10} v{row[2]} updated {row[3]}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage web console roles")
    sub = parser.add_subparsers(dest="command", required=True)

    p_bootstrap = sub.add_parser("bootstrap-admin", help="Create initial admin user")
    p_bootstrap.add_argument("--user-id", required=True)
    p_bootstrap.add_argument("--email", required=True)
    p_bootstrap.set_defaults(func=bootstrap_admin)

    p_set_role = sub.add_parser("set-role", help="Set role for user")
    p_set_role.add_argument("--user-id", required=True)
    p_set_role.add_argument("--role", required=True, choices=["viewer", "operator", "admin"])
    p_set_role.add_argument("--by", required=True, help="Admin performing the change")
    p_set_role.set_defaults(func=set_role)

    p_grant = sub.add_parser("grant-strategy", help="Grant strategy access")
    p_grant.add_argument("--user-id", required=True)
    p_grant.add_argument("--strategy-id", required=True)
    p_grant.add_argument("--by", required=True)
    p_grant.add_argument("--description")
    p_grant.set_defaults(func=grant_strategy)

    p_list = sub.add_parser("list-users", help="List provisioned users")
    p_list.set_defaults(func=list_users)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
