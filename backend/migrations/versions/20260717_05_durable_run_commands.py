"""Add indexed ownership and workspace projections for durable run commands.

Revision ID: 20260717_05
Revises: 20260717_04
Create Date: 2026-07-17 00:00:04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260717_05"
down_revision = "20260717_04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # These projections remain nullable for compatibility with authoritative
    # records written before the command API existed. New command writes always
    # populate all four values in the same transaction as the run record.
    op.add_column("runs", sa.Column("user_id", sa.String(length=128)))
    op.add_column("runs", sa.Column("conversation_id", sa.String(length=128)))
    op.add_column("runs", sa.Column("project_id", sa.String(length=128)))
    op.add_column("runs", sa.Column("source_fingerprint", sa.String(length=64)))
    op.create_foreign_key(
        "fk_runs_user_owner", "runs", "users", ["user_id"], ["user_id"]
    )
    op.create_foreign_key(
        "fk_runs_conversation_owner",
        "runs",
        "conversations",
        ["conversation_id"],
        ["conversation_id"],
    )
    op.create_index("ix_runs_user_created", "runs", ["user_id", "created_at"])
    op.create_index("ix_runs_conversation", "runs", ["conversation_id"])
    op.create_check_constraint(
        "runs_source_fingerprint_shape",
        "runs",
        "source_fingerprint IS NULL OR source_fingerprint ~ '^[0-9a-f]{64}$'",
    )
    op.add_column(
        "transition_log", sa.Column("previous_state", sa.String(length=64))
    )
    op.add_column("transition_log", sa.Column("next_state", sa.String(length=64)))
    op.execute(
        "UPDATE transition_log SET previous_state = payload ->> 'previous_state', "
        "next_state = payload ->> 'next_state'"
    )
    op.alter_column("transition_log", "previous_state", nullable=False)
    op.alter_column("transition_log", "next_state", nullable=False)
    op.create_index(
        "ix_transition_log_run_states",
        "transition_log",
        ["run_id", "previous_state", "next_state"],
    )


def downgrade() -> None:
    op.drop_index("ix_transition_log_run_states", table_name="transition_log")
    op.drop_column("transition_log", "next_state")
    op.drop_column("transition_log", "previous_state")
    op.drop_constraint("runs_source_fingerprint_shape", "runs", type_="check")
    op.drop_index("ix_runs_conversation", table_name="runs")
    op.drop_index("ix_runs_user_created", table_name="runs")
    op.drop_constraint("fk_runs_conversation_owner", "runs", type_="foreignkey")
    op.drop_constraint("fk_runs_user_owner", "runs", type_="foreignkey")
    op.drop_column("runs", "source_fingerprint")
    op.drop_column("runs", "project_id")
    op.drop_column("runs", "conversation_id")
    op.drop_column("runs", "user_id")
