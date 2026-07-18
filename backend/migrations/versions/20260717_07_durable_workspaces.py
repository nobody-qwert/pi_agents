"""Make disposable guest and workspace transfer state restart-durable.

Revision ID: 20260717_07
Revises: 20260717_06
Create Date: 2026-07-17 00:00:06
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260717_07"
down_revision = "20260717_06"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("workspace_sessions", sa.Column("selected_source", sa.String(128)))
    op.add_column(
        "workspace_sessions", sa.Column("source_fingerprint", sa.String(64))
    )
    op.add_column("workspace_sessions", sa.Column("guest_identity", sa.String(128)))
    op.add_column("workspace_sessions", sa.Column("overlay_id", sa.String(128)))
    op.add_column("workspace_sessions", sa.Column("guest_path", sa.String(1024)))
    op.add_column(
        "workspace_sessions",
        sa.Column("lifecycle_status", sa.String(32), server_default="creating"),
    )
    op.add_column("workspace_sessions", sa.Column("last_error_code", sa.String(128)))
    op.add_column(
        "workspace_sessions", sa.Column("provisioned_at", sa.DateTime(timezone=True))
    )
    op.add_column(
        "workspace_sessions", sa.Column("ready_at", sa.DateTime(timezone=True))
    )
    op.add_column(
        "workspace_sessions", sa.Column("destroyed_at", sa.DateTime(timezone=True))
    )
    op.execute(
        "UPDATE workspace_sessions SET "
        "selected_source = payload ->> 'selected_source', "
        "source_fingerprint = payload ->> 'source_fingerprint', "
        "guest_identity = payload ->> 'guest_identity', "
        "guest_path = payload ->> 'guest_path', "
        "overlay_id = 'overlay-' || replace(run_id, 'run_', ''), "
        "lifecycle_status = CASE WHEN payload ->> 'status' = 'destroyed' "
        "THEN 'destroyed' ELSE 'ready' END, "
        "provisioned_at = updated_at, "
        "ready_at = CASE WHEN payload ->> 'status' <> 'destroyed' "
        "THEN updated_at ELSE NULL END, "
        "destroyed_at = CASE WHEN payload ->> 'status' = 'destroyed' "
        "THEN updated_at ELSE NULL END"
    )
    for column in (
        "selected_source",
        "source_fingerprint",
        "guest_identity",
        "overlay_id",
        "guest_path",
        "lifecycle_status",
    ):
        op.alter_column("workspace_sessions", column, nullable=False)
    op.alter_column("workspace_sessions", "lifecycle_status", server_default=None)
    op.create_unique_constraint(
        "uq_workspace_sessions_guest_identity",
        "workspace_sessions",
        ["guest_identity"],
    )
    op.create_unique_constraint(
        "uq_workspace_sessions_overlay_id", "workspace_sessions", ["overlay_id"]
    )
    op.create_check_constraint(
        "workspace_sessions_fingerprint_shape",
        "workspace_sessions",
        "source_fingerprint ~ '^[0-9a-f]{64}$'",
    )
    op.create_check_constraint(
        "workspace_sessions_lifecycle_allowed",
        "workspace_sessions",
        "lifecycle_status IN ('creating', 'ready', 'failed', 'destroying', 'destroyed')",
    )
    op.create_check_constraint(
        "workspace_sessions_lifecycle_timestamps",
        "workspace_sessions",
        "(lifecycle_status <> 'ready' OR ready_at IS NOT NULL) AND "
        "(lifecycle_status <> 'destroyed' OR destroyed_at IS NOT NULL)",
    )
    op.create_index(
        "ix_workspace_sessions_lifecycle",
        "workspace_sessions",
        ["lifecycle_status", "updated_at"],
    )

    op.add_column(
        "workspace_transfers", sa.Column("source_fingerprint", sa.String(64))
    )
    op.add_column(
        "workspace_transfers",
        sa.Column(
            "excluded_paths",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
        ),
    )
    op.add_column(
        "workspace_transfers",
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.add_column("workspace_transfers", sa.Column("error_code", sa.String(128)))
    op.execute(
        "UPDATE workspace_transfers SET source_fingerprint = "
        "workspace_sessions.source_fingerprint, status = 'completed', "
        "completed_at = COALESCE(workspace_transfers.completed_at, "
        "workspace_transfers.created_at), error_code = NULL FROM workspace_sessions "
        "WHERE workspace_transfers.workspace_id = workspace_sessions.workspace_id"
    )
    op.alter_column("workspace_transfers", "source_fingerprint", nullable=False)
    op.alter_column("workspace_transfers", "excluded_paths", nullable=False)
    op.alter_column("workspace_transfers", "excluded_paths", server_default=None)
    op.create_check_constraint(
        "workspace_transfers_fingerprint_shape",
        "workspace_transfers",
        "source_fingerprint ~ '^[0-9a-f]{64}$'",
    )
    op.create_check_constraint(
        "workspace_transfers_status_allowed",
        "workspace_transfers",
        "status IN ('started', 'completed', 'failed')",
    )
    op.create_check_constraint(
        "workspace_transfers_completion_consistent",
        "workspace_transfers",
        "(status = 'started' AND completed_at IS NULL AND error_code IS NULL) OR "
        "(status = 'completed' AND completed_at IS NOT NULL AND error_code IS NULL) OR "
        "(status = 'failed' AND completed_at IS NOT NULL AND error_code IS NOT NULL)",
    )
    op.create_index(
        "ix_workspace_transfers_workspace_status",
        "workspace_transfers",
        ["workspace_id", "status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_workspace_transfers_workspace_status", table_name="workspace_transfers"
    )
    op.drop_constraint(
        "workspace_transfers_completion_consistent",
        "workspace_transfers",
        type_="check",
    )
    op.drop_constraint(
        "workspace_transfers_status_allowed", "workspace_transfers", type_="check"
    )
    op.drop_constraint(
        "workspace_transfers_fingerprint_shape", "workspace_transfers", type_="check"
    )
    for column in ("error_code", "completed_at", "excluded_paths", "source_fingerprint"):
        op.drop_column("workspace_transfers", column)

    op.drop_index("ix_workspace_sessions_lifecycle", table_name="workspace_sessions")
    op.drop_constraint(
        "workspace_sessions_lifecycle_timestamps",
        "workspace_sessions",
        type_="check",
    )
    op.drop_constraint(
        "workspace_sessions_lifecycle_allowed", "workspace_sessions", type_="check"
    )
    op.drop_constraint(
        "workspace_sessions_fingerprint_shape", "workspace_sessions", type_="check"
    )
    op.drop_constraint(
        "uq_workspace_sessions_overlay_id", "workspace_sessions", type_="unique"
    )
    op.drop_constraint(
        "uq_workspace_sessions_guest_identity", "workspace_sessions", type_="unique"
    )
    for column in (
        "destroyed_at",
        "ready_at",
        "provisioned_at",
        "last_error_code",
        "lifecycle_status",
        "guest_path",
        "overlay_id",
        "guest_identity",
        "source_fingerprint",
        "selected_source",
    ):
        op.drop_column("workspace_sessions", column)
