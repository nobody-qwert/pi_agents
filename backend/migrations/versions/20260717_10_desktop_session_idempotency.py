"""Make desktop session grants replay-safe.

Revision ID: 20260717_10
Revises: 20260717_09
Create Date: 2026-07-17 00:00:09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260717_10"
down_revision = "20260717_09"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("desktop_sessions", sa.Column("idempotency_key", sa.String(256)))
    op.execute(
        "UPDATE desktop_sessions SET idempotency_key = 'legacy:' || session_id"
    )
    op.alter_column("desktop_sessions", "idempotency_key", nullable=False)
    op.create_unique_constraint(
        "uq_desktop_sessions_idempotency",
        "desktop_sessions",
        ["run_id", "user_id", "idempotency_key"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_desktop_sessions_idempotency", "desktop_sessions", type_="unique"
    )
    op.drop_column("desktop_sessions", "idempotency_key")
