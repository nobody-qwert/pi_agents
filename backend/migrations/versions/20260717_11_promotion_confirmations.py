"""Persist short-lived promotion confirmation authority.

Revision ID: 20260717_11
Revises: 20260717_10
Create Date: 2026-07-17 00:00:10
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260717_11"
down_revision = "20260717_10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "promotion_previews", sa.Column("confirmation_nonce_digest", sa.String(64))
    )
    op.add_column(
        "promotion_previews",
        sa.Column("confirmation_expires_at", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    op.drop_column("promotion_previews", "confirmation_expires_at")
    op.drop_column("promotion_previews", "confirmation_nonce_digest")
