"""Persist pending authority requests separately from authenticated decisions.

Revision ID: 20260717_09
Revises: 20260717_08
Create Date: 2026-07-17 00:00:08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260717_09"
down_revision = "20260717_08"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "approval_requests",
        sa.Column("approval_id", sa.String(128), primary_key=True),
        sa.Column("run_id", sa.String(128), nullable=False),
        sa.Column("authority", sa.String(64), nullable=False),
        sa.Column(
            "affected_versions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("decided_by", sa.String(128)),
        sa.Column("comment", sa.String(4096)),
        sa.Column("idempotency_key", sa.String(256), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"]),
        sa.ForeignKeyConstraint(["decided_by"], ["users.user_id"]),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')",
            name="approval_request_status_allowed",
        ),
        sa.UniqueConstraint(
            "run_id", "idempotency_key", name="uq_approval_request_idempotency"
        ),
    )
    op.create_index(
        "ix_approval_requests_run_status",
        "approval_requests",
        ["run_id", "status", "requested_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_approval_requests_run_status", table_name="approval_requests")
    op.drop_table("approval_requests")
