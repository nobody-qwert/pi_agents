"""Represent provisional executor checkpoints explicitly.

Revision ID: 20260717_14
Revises: 20260717_13
Create Date: 2026-07-17 00:00:13
"""

from __future__ import annotations

from alembic import op

revision = "20260717_14"
down_revision = "20260717_13"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "workspace_checkpoints_kind_allowed",
        "workspace_checkpoints",
        type_="check",
    )
    op.create_check_constraint(
        "workspace_checkpoints_kind_allowed",
        "workspace_checkpoints",
        "checkpoint_kind IN ('baseline', 'execution', 'service_accepted', "
        "'user_accepted', 'rollback')",
    )


def downgrade() -> None:
    op.execute("DELETE FROM workspace_checkpoints WHERE checkpoint_kind = 'execution'")
    op.drop_constraint(
        "workspace_checkpoints_kind_allowed",
        "workspace_checkpoints",
        type_="check",
    )
    op.create_check_constraint(
        "workspace_checkpoints_kind_allowed",
        "workspace_checkpoints",
        "checkpoint_kind IN ('baseline', 'service_accepted', 'user_accepted', 'rollback')",
    )
