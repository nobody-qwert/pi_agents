"""Bind approval requests to an expiry and authoritative run version.

Revision ID: 20260717_13
Revises: 20260717_12
Create Date: 2026-07-17 00:00:12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260717_13"
down_revision = "20260717_12"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "approval_requests",
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now() + interval '24 hours'"),
        ),
    )
    op.add_column(
        "approval_requests",
        sa.Column(
            "requested_record_version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )
    op.execute(
        "UPDATE approval_requests AS request SET requested_record_version = "
        "COALESCE((run.payload -> 'metadata' ->> 'record_version')::integer, 1) "
        "FROM runs AS run WHERE run.run_id = request.run_id"
    )
    op.alter_column("approval_requests", "expires_at", server_default=None)
    op.alter_column(
        "approval_requests", "requested_record_version", server_default=None
    )


def downgrade() -> None:
    op.drop_column("approval_requests", "requested_record_version")
    op.drop_column("approval_requests", "expires_at")
