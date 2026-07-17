"""Add the durable runner queue and fenced lease state.

Revision ID: 20260717_04
Revises: 20260717_03
Create Date: 2026-07-17 00:00:03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260717_04"
down_revision = "20260717_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "run_queue",
        sa.Column("run_id", sa.String(length=128), primary_key=True),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(length=256), nullable=True),
        sa.Column("lease_token", sa.String(length=64), nullable=True),
        sa.Column("lease_epoch", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("cancellation_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("budget_exhausted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"]),
        sa.CheckConstraint("attempt_count >= 0", name="run_queue_attempt_count_nonnegative"),
        sa.CheckConstraint("max_attempts >= 1", name="run_queue_max_attempts_positive"),
        sa.CheckConstraint(
            "attempt_count <= max_attempts", name="run_queue_attempt_count_bounded"
        ),
        sa.CheckConstraint(
            "(lease_owner IS NULL) = (lease_token IS NULL)",
            name="run_queue_lease_owner_token_pair",
        ),
    )
    op.create_index(
        "ix_run_queue_claimable",
        "run_queue",
        ["available_at", "lease_expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_run_queue_claimable", table_name="run_queue")
    op.drop_table("run_queue")
