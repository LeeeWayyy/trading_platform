"""Add auth_audit_log table for authentication event auditing.

Revision ID: 0d71fbaccbc4
Revises: None
Create Date: 2025-12-31 20:41:00 UTC

Migration naming convention:
- Filename: YYYYMMDD_HHMMSS_slug.py (chronological sorting)
- Revision ID: Random hash (collision-proof for parallel branches)
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0d71fbaccbc4"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "auth_audit_log",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("user_id", sa.String(length=100), nullable=False),
        sa.Column("session_id", sa.String(length=8), nullable=True),
        sa.Column("client_ip", postgresql.INET(), nullable=False),
        sa.Column("user_agent", sa.String(length=256), nullable=True),
        sa.Column("auth_type", sa.String(length=20), nullable=False),
        sa.Column("outcome", sa.String(length=10), nullable=False),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("request_id", postgresql.UUID(), nullable=False),
        sa.Column("extra_data", postgresql.JSONB(), nullable=True),
    )
    op.create_index("ix_auth_audit_log_timestamp", "auth_audit_log", ["timestamp"])
    op.create_index(
        "ix_auth_audit_log_user_id_timestamp",
        "auth_audit_log",
        ["user_id", "timestamp"],
    )
    op.create_index(
        "ix_auth_audit_log_event_outcome",
        "auth_audit_log",
        ["event_type", "outcome"],
    )
    op.create_index("ix_auth_audit_log_session_id", "auth_audit_log", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_auth_audit_log_session_id", table_name="auth_audit_log")
    op.drop_index("ix_auth_audit_log_event_outcome", table_name="auth_audit_log")
    op.drop_index("ix_auth_audit_log_user_id_timestamp", table_name="auth_audit_log")
    op.drop_index("ix_auth_audit_log_timestamp", table_name="auth_audit_log")
    op.drop_table("auth_audit_log")
