"""Persist bounded per-run guest egress decisions.

Revision ID: 20260717_12
Revises: 20260717_11
Create Date: 2026-07-17 00:00:11
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260717_12"
down_revision = "20260717_11"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "egress_requests",
        sa.Column("request_id", sa.String(128), primary_key=True),
        sa.Column("run_id", sa.String(128), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("hostname", sa.String(253), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("scheme", sa.String(8), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("reason_code", sa.String(128), nullable=False),
        sa.Column(
            "resolved_ips",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("bytes_up", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("bytes_down", sa.BigInteger(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"]),
        sa.CheckConstraint("port BETWEEN 1 AND 65535", name="egress_port_valid"),
        sa.CheckConstraint(
            "decision IN ('allowed', 'denied', 'failed')",
            name="egress_decision_valid",
        ),
        sa.CheckConstraint(
            "bytes_up >= 0 AND bytes_down >= 0", name="egress_bytes_nonnegative"
        ),
    )
    op.create_index(
        "ix_egress_requests_run_time",
        "egress_requests",
        ["run_id", "requested_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_egress_requests_run_time", table_name="egress_requests")
    op.drop_table("egress_requests")
