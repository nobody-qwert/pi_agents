"""Persist checkpoint, desktop, and promotion command state.

Revision ID: 20260717_08
Revises: 20260717_07
Create Date: 2026-07-17 00:00:07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260717_08"
down_revision = "20260717_07"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Baseline, user-accepted, and rollback checkpoints are workspace-scoped and
    # therefore do not always have a work-node owner.
    op.alter_column("workspace_checkpoints", "work_node_id", nullable=True)
    op.add_column(
        "workspace_checkpoints", sa.Column("checkpoint_kind", sa.String(32))
    )
    op.add_column(
        "workspace_checkpoints", sa.Column("commit_hash", sa.String(64))
    )
    op.add_column("workspace_checkpoints", sa.Column("tree_hash", sa.String(64)))
    op.add_column(
        "workspace_checkpoints", sa.Column("design_version", sa.Integer())
    )
    op.add_column(
        "workspace_checkpoints",
        sa.Column(
            "evidence_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
        ),
    )
    op.add_column(
        "workspace_checkpoints",
        sa.Column("rollback_from_checkpoint_id", sa.String(128)),
    )
    op.create_foreign_key(
        "fk_workspace_checkpoints_rollback_from",
        "workspace_checkpoints",
        "workspace_checkpoints",
        ["rollback_from_checkpoint_id"],
        ["checkpoint_id"],
    )
    op.execute(
        "UPDATE workspace_checkpoints SET "
        "checkpoint_kind = COALESCE(payload ->> 'checkpoint_kind', 'service_accepted'), "
        "commit_hash = payload ->> 'commit_hash', "
        "tree_hash = payload ->> 'tree_hash', "
        "design_version = COALESCE((payload ->> 'design_version')::integer, 1), "
        "evidence_ids = COALESCE(payload -> 'accepted_evidence_ids', '[]'::jsonb), "
        "rollback_from_checkpoint_id = payload ->> 'rollback_from_checkpoint_id'"
    )
    for column in (
        "checkpoint_kind",
        "commit_hash",
        "tree_hash",
        "design_version",
        "evidence_ids",
    ):
        op.alter_column("workspace_checkpoints", column, nullable=False)
    op.alter_column("workspace_checkpoints", "evidence_ids", server_default=None)
    op.create_check_constraint(
        "workspace_checkpoints_kind_allowed",
        "workspace_checkpoints",
        "checkpoint_kind IN ('baseline', 'service_accepted', 'user_accepted', 'rollback')",
    )
    op.create_check_constraint(
        "workspace_checkpoints_commit_shape",
        "workspace_checkpoints",
        "commit_hash ~ '^[0-9a-f]{40}([0-9a-f]{24})?$' AND "
        "tree_hash ~ '^[0-9a-f]{40}([0-9a-f]{24})?$'",
    )
    op.create_check_constraint(
        "workspace_checkpoints_design_version_positive",
        "workspace_checkpoints",
        "design_version >= 1",
    )
    op.create_index(
        "ix_workspace_checkpoints_lineage",
        "workspace_checkpoints",
        ["workspace_id", "created_at"],
    )

    op.create_table(
        "desktop_sessions",
        sa.Column("session_id", sa.String(128), primary_key=True),
        sa.Column("run_id", sa.String(128), nullable=False),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("token_digest", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("websocket_used_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"]),
    )
    op.create_index(
        "ix_desktop_sessions_run_user",
        "desktop_sessions",
        ["run_id", "user_id", "expires_at"],
    )
    op.create_table(
        "workspace_input_ownership",
        sa.Column("run_id", sa.String(128), primary_key=True),
        sa.Column("owner", sa.String(16), nullable=False),
        sa.Column("record_version", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"]),
        sa.CheckConstraint("owner IN ('AGENT', 'USER', 'PAUSED')", name="input_owner_allowed"),
        sa.CheckConstraint("record_version >= 1", name="input_owner_version_positive"),
    )

    op.add_column("promotion_previews", sa.Column("preview_hash", sa.String(64)))
    op.add_column(
        "promotion_previews", sa.Column("checkpoint_id", sa.String(128))
    )
    op.add_column(
        "promotion_previews",
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text())),
    )
    op.add_column(
        "promotion_previews", sa.Column("direct_eligible", sa.Boolean())
    )
    op.add_column(
        "promotion_previews", sa.Column("idempotency_key", sa.String(256))
    )
    op.create_unique_constraint(
        "uq_promotion_previews_hash", "promotion_previews", ["preview_hash"]
    )
    op.create_unique_constraint(
        "uq_promotion_previews_idempotency", "promotion_previews", ["idempotency_key"]
    )


def downgrade() -> None:
    for constraint in (
        "uq_promotion_previews_idempotency",
        "uq_promotion_previews_hash",
    ):
        op.drop_constraint(constraint, "promotion_previews", type_="unique")
    for column in (
        "idempotency_key",
        "direct_eligible",
        "payload",
        "checkpoint_id",
        "preview_hash",
    ):
        op.drop_column("promotion_previews", column)
    op.drop_table("workspace_input_ownership")
    op.drop_index("ix_desktop_sessions_run_user", table_name="desktop_sessions")
    op.drop_table("desktop_sessions")
    op.drop_index(
        "ix_workspace_checkpoints_lineage", table_name="workspace_checkpoints"
    )
    for constraint in (
        "workspace_checkpoints_design_version_positive",
        "workspace_checkpoints_commit_shape",
        "workspace_checkpoints_kind_allowed",
    ):
        op.drop_constraint(constraint, "workspace_checkpoints", type_="check")
    op.drop_constraint(
        "fk_workspace_checkpoints_rollback_from",
        "workspace_checkpoints",
        type_="foreignkey",
    )
    for column in (
        "rollback_from_checkpoint_id",
        "evidence_ids",
        "design_version",
        "tree_hash",
        "commit_hash",
        "checkpoint_kind",
    ):
        op.drop_column("workspace_checkpoints", column)
    op.alter_column("workspace_checkpoints", "work_node_id", nullable=False)
