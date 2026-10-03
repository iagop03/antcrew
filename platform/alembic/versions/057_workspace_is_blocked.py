"""Add is_blocked to workspace for admin fraud/abuse blocks.

Revision ID: 057
Revises: 056
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "057"
down_revision = "056"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("workspace") as batch:
        batch.add_column(sa.Column("is_blocked", sa.Boolean(), nullable=False, server_default="false"))


def downgrade() -> None:
    with op.batch_alter_table("workspace") as batch:
        batch.drop_column("is_blocked")
