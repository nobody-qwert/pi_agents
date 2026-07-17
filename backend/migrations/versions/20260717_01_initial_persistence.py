"""Create the initial durable PostgreSQL data model.

Revision ID: 20260717_01
Revises:
Create Date: 2026-07-17 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260717_01"
down_revision = None
branch_labels = None
depends_on = None

_AUTHORITATIVE_TABLES = (
    "runs",
    "charters",
    "design_revisions",
    "work_nodes",
    "packets",
    "artifacts",
    "evidence",
    "issues",
    "approvals",
    "workspace_sessions",
    "workspace_checkpoints",
    "promotions",
    "transition_log",
    "run_completions",
)


def _audit_columns(table_name: str) -> list[sa.Column[object]]:
    return [
        sa.Column("record_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idempotency_key", sa.String(length=256), nullable=True),
        sa.Column("trace_id", sa.String(length=32), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint("record_version >= 1", name="record_version_positive"),
        sa.CheckConstraint("updated_at >= created_at", name="audit_time_ordered"),
        sa.UniqueConstraint("idempotency_key", name=f"uq_{table_name}_idempotency_key"),
    ]


def _authoritative_table(
    name: str,
    id_column: str,
    *columns: sa.Column[object] | sa.ForeignKeyConstraint,
) -> None:
    op.create_table(
        name,
        sa.Column(id_column, sa.String(length=128), primary_key=True),
        *columns,
        *_audit_columns(name),
    )


def _install_record_version_trigger(table_name: str) -> None:
    op.execute(
        f"CREATE TRIGGER {table_name}_record_version_increment "
        f"BEFORE UPDATE ON {table_name} FOR EACH ROW "
        "EXECUTE FUNCTION enforce_record_version_increment()"
    )


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("user_id", sa.String(length=128), primary_key=True),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("display_name", sa.String(length=256), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "user_id", name="uq_users_tenant_user"),
    )
    op.create_table(
        "conversations",
        sa.Column("conversation_id", sa.String(length=128), primary_key=True),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"]),
    )
    op.create_table(
        "messages",
        sa.Column("message_id", sa.String(length=128), primary_key=True),
        sa.Column("conversation_id", sa.String(length=128), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.conversation_id"]),
        sa.CheckConstraint("sequence >= 1", name="messages_sequence_positive"),
        sa.UniqueConstraint("conversation_id", "sequence", name="uq_messages_sequence"),
    )
    _authoritative_table(
        "runs", "run_id", sa.Column("tenant_id", sa.String(128), nullable=False)
    )
    op.create_table(
        "run_events",
        sa.Column("event_id", sa.String(length=128), primary_key=True),
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"]),
        sa.CheckConstraint("sequence >= 1", name="run_events_sequence_positive"),
        sa.UniqueConstraint("run_id", "sequence", name="uq_run_events_sequence"),
    )
    op.create_table(
        "agent_registry_versions",
        sa.Column("registry_version_id", sa.String(length=128), primary_key=True),
        sa.Column("config_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    _authoritative_table(
        "charters",
        "charter_id",
        sa.Column("run_id", sa.String(128), nullable=False, unique=True),
        sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"]),
    )
    _authoritative_table(
        "design_revisions",
        "design_revision_id",
        sa.Column("run_id", sa.String(128), nullable=False),
        sa.Column("design_version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"]),
        sa.CheckConstraint("design_version >= 1", name="design_version_positive"),
        sa.UniqueConstraint(
            "run_id", "design_version", name="uq_design_revisions_version"
        ),
    )
    _authoritative_table(
        "work_nodes",
        "work_node_id",
        sa.Column("run_id", sa.String(128), nullable=False),
        sa.Column("parent_id", sa.String(128), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"]),
        sa.ForeignKeyConstraint(["parent_id"], ["work_nodes.work_node_id"]),
    )
    op.create_table(
        "work_edges",
        sa.Column("edge_id", sa.String(length=128), primary_key=True),
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("from_work_node_id", sa.String(length=128), nullable=False),
        sa.Column("to_work_node_id", sa.String(length=128), nullable=False),
        sa.Column("edge_type", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"]),
        sa.ForeignKeyConstraint(["from_work_node_id"], ["work_nodes.work_node_id"]),
        sa.ForeignKeyConstraint(["to_work_node_id"], ["work_nodes.work_node_id"]),
        sa.CheckConstraint(
            "from_work_node_id <> to_work_node_id", name="work_edges_not_self"
        ),
        sa.UniqueConstraint(
            "run_id",
            "from_work_node_id",
            "to_work_node_id",
            "edge_type",
            name="uq_work_edges_relation",
        ),
    )
    _authoritative_table(
        "packets",
        "packet_id",
        sa.Column("run_id", sa.String(128), nullable=False),
        sa.Column("work_node_id", sa.String(128), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"]),
        sa.ForeignKeyConstraint(["work_node_id"], ["work_nodes.work_node_id"]),
    )
    _authoritative_table(
        "artifacts",
        "artifact_id",
        sa.Column("run_id", sa.String(128), nullable=False),
        sa.Column("work_node_id", sa.String(128), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"]),
        sa.ForeignKeyConstraint(["work_node_id"], ["work_nodes.work_node_id"]),
    )
    op.create_table(
        "agent_attempts",
        sa.Column("attempt_id", sa.String(length=128), primary_key=True),
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("registry_version_id", sa.String(length=128), nullable=False),
        sa.Column("input_artifact_id", sa.String(length=128), nullable=True),
        sa.Column("result_artifact_id", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("usage", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("trace_id", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"]),
        sa.ForeignKeyConstraint(
            ["registry_version_id"], ["agent_registry_versions.registry_version_id"]
        ),
        sa.ForeignKeyConstraint(["input_artifact_id"], ["artifacts.artifact_id"]),
        sa.ForeignKeyConstraint(["result_artifact_id"], ["artifacts.artifact_id"]),
    )
    _authoritative_table(
        "evidence",
        "evidence_id",
        sa.Column("run_id", sa.String(128), nullable=False),
        sa.Column("work_node_id", sa.String(128), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"]),
        sa.ForeignKeyConstraint(["work_node_id"], ["work_nodes.work_node_id"]),
    )
    _authoritative_table(
        "issues",
        "issue_id",
        sa.Column("run_id", sa.String(128), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"]),
    )
    _authoritative_table(
        "approvals",
        "approval_id",
        sa.Column("run_id", sa.String(128), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"]),
    )
    _authoritative_table(
        "workspace_sessions",
        "workspace_id",
        sa.Column("run_id", sa.String(128), nullable=False, unique=True),
        sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"]),
    )
    _authoritative_table(
        "workspace_checkpoints",
        "checkpoint_id",
        sa.Column("run_id", sa.String(128), nullable=False),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("work_node_id", sa.String(128), nullable=False),
        sa.Column("parent_checkpoint_id", sa.String(128), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"]),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace_sessions.workspace_id"]),
        sa.ForeignKeyConstraint(["work_node_id"], ["work_nodes.work_node_id"]),
        sa.ForeignKeyConstraint(
            ["parent_checkpoint_id"], ["workspace_checkpoints.checkpoint_id"]
        ),
    )
    op.create_table(
        "workspace_transfers",
        sa.Column("transfer_id", sa.String(length=128), primary_key=True),
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("manifest", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"]),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace_sessions.workspace_id"]),
        sa.CheckConstraint(
            "direction IN ('copy_in', 'copy_out')", name="transfer_direction"
        ),
    )
    op.create_table(
        "promotion_previews",
        sa.Column("preview_id", sa.String(length=128), primary_key=True),
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("artifact_id", sa.String(length=128), nullable=False),
        sa.Column("artifact_version", sa.Integer(), nullable=False),
        sa.Column("manifest", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"]),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace_sessions.workspace_id"]),
        sa.ForeignKeyConstraint(["artifact_id"], ["artifacts.artifact_id"]),
        sa.CheckConstraint("artifact_version >= 1", name="preview_artifact_version"),
    )
    _authoritative_table(
        "promotions",
        "promotion_id",
        sa.Column("run_id", sa.String(128), nullable=False),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"]),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace_sessions.workspace_id"]),
    )
    _authoritative_table(
        "transition_log",
        "transition_id",
        sa.Column("run_id", sa.String(128), nullable=False),
        sa.Column("work_node_id", sa.String(128), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"]),
        sa.ForeignKeyConstraint(["work_node_id"], ["work_nodes.work_node_id"]),
    )
    _authoritative_table(
        "run_completions",
        "completion_id",
        sa.Column("run_id", sa.String(128), nullable=False, unique=True),
        sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"]),
    )
    op.execute(
        """
        CREATE FUNCTION enforce_record_version_increment()
        RETURNS trigger AS $$
        BEGIN
            IF NEW.record_version <> OLD.record_version + 1 THEN
                RAISE EXCEPTION 'record_version must increment by exactly one'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    for table_name in _AUTHORITATIVE_TABLES:
        _install_record_version_trigger(table_name)


def downgrade() -> None:
    for table_name in (
        "run_completions",
        "transition_log",
        "promotions",
        "promotion_previews",
        "workspace_transfers",
        "workspace_checkpoints",
        "workspace_sessions",
        "approvals",
        "issues",
        "evidence",
        "agent_attempts",
        "artifacts",
        "packets",
        "work_edges",
        "work_nodes",
        "design_revisions",
        "charters",
        "agent_registry_versions",
        "run_events",
        "runs",
        "messages",
        "conversations",
        "users",
    ):
        op.drop_table(table_name)
    op.execute("DROP FUNCTION enforce_record_version_increment()")
