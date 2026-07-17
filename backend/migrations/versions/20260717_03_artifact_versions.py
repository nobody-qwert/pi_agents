"""Add immutable versioned metadata for validated artifact content.

Revision ID: 20260717_03
Revises: 20260717_02
Create Date: 2026-07-17 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260717_03"
down_revision = "20260717_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "artifact_version_heads",
        sa.Column("artifact_id", sa.String(length=128), primary_key=True),
        sa.Column("current_version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "current_version >= 0", name="artifact_head_version_nonnegative"
        ),
    )
    op.create_table(
        "artifact_versions",
        sa.Column("artifact_id", sa.String(length=128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("media_type", sa.String(length=255), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("storage_key", sa.String(length=1024), nullable=False),
        sa.Column("scope", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"]),
        sa.PrimaryKeyConstraint(
            "artifact_id", "version", name="artifact_versions_pkey"
        ),
        sa.CheckConstraint("version >= 1", name="artifact_version_positive"),
        sa.CheckConstraint("size_bytes >= 0", name="artifact_size_nonnegative"),
        sa.UniqueConstraint("storage_key", name="uq_artifact_versions_storage_key"),
    )


def downgrade() -> None:
    op.drop_table("artifact_versions")
    op.drop_table("artifact_version_heads")
