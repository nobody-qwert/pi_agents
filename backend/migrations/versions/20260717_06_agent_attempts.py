"""Make model attempts restart-durable and independently auditable.

Revision ID: 20260717_06
Revises: 20260717_05
Create Date: 2026-07-17 00:00:05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260717_06"
down_revision = "20260717_05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_attempts", sa.Column("agent_id", sa.String(length=64)))
    op.add_column("agent_attempts", sa.Column("work_node_id", sa.String(length=128)))
    op.add_column("agent_attempts", sa.Column("design_version", sa.Integer()))
    op.add_column("agent_attempts", sa.Column("config_hash", sa.String(length=64)))
    op.add_column("agent_attempts", sa.Column("prompt_hash", sa.String(length=64)))
    op.add_column("agent_attempts", sa.Column("model_id", sa.String(length=256)))
    op.add_column("agent_attempts", sa.Column("completed_at", sa.DateTime(timezone=True)))
    op.add_column("agent_attempts", sa.Column("rejection_code", sa.String(length=128)))
    op.add_column("agent_attempts", sa.Column("retryable", sa.Boolean()))
    op.add_column("agent_attempts", sa.Column("result_type", sa.String(length=128)))
    op.add_column(
        "agent_attempts",
        sa.Column("input_context", postgresql.JSONB(astext_type=sa.Text())),
    )
    op.add_column(
        "agent_attempts",
        sa.Column("result_payload", postgresql.JSONB(astext_type=sa.Text())),
    )
    op.execute(
        "UPDATE agent_attempts SET status = 'rejected', completed_at = created_at "
        "WHERE status NOT IN ('started', 'accepted', 'rejected')"
    )
    op.execute(
        "UPDATE agent_attempts SET completed_at = created_at "
        "WHERE status IN ('accepted', 'rejected') AND completed_at IS NULL"
    )
    op.create_check_constraint(
        "agent_attempts_design_version_positive",
        "agent_attempts",
        "design_version IS NULL OR design_version >= 1",
    )
    op.create_check_constraint(
        "agent_attempts_status_allowed",
        "agent_attempts",
        "status IN ('started', 'accepted', 'rejected')",
    )
    op.create_check_constraint(
        "agent_attempts_completion_consistent",
        "agent_attempts",
        "(status = 'started' AND completed_at IS NULL) OR "
        "(status <> 'started' AND completed_at IS NOT NULL)",
    )
    op.create_index(
        "ix_agent_attempts_run_agent_created",
        "agent_attempts",
        ["run_id", "agent_id", "created_at"],
    )
    op.create_index(
        "ix_agent_attempts_run_status", "agent_attempts", ["run_id", "status"]
    )


def downgrade() -> None:
    op.drop_index("ix_agent_attempts_run_status", table_name="agent_attempts")
    op.drop_index("ix_agent_attempts_run_agent_created", table_name="agent_attempts")
    op.drop_constraint(
        "agent_attempts_completion_consistent", "agent_attempts", type_="check"
    )
    op.drop_constraint("agent_attempts_status_allowed", "agent_attempts", type_="check")
    op.drop_constraint(
        "agent_attempts_design_version_positive", "agent_attempts", type_="check"
    )
    for column in (
        "result_payload",
        "input_context",
        "result_type",
        "retryable",
        "rejection_code",
        "completed_at",
        "model_id",
        "prompt_hash",
        "config_hash",
        "design_version",
        "work_node_id",
        "agent_id",
    ):
        op.drop_column("agent_attempts", column)
