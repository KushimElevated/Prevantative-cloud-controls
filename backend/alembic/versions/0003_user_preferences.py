"""Per-user experience preferences (Classic Experience vs AI Control Workspace).

A single nullable-free JSONB column on users; no new tables and no change to domain data.
Existing rows default to an empty object, which means "Classic Experience".

Revision ID: 0003
Revises: 0002
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("preferences", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")))


def downgrade() -> None:
    op.drop_column("users", "preferences")
