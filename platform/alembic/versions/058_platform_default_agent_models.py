"""Add default_agent_models JSON column to platform_config.

Revision ID: 058
Revises: 057
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "058"
down_revision = "057"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("platform_config") as batch:
        batch.add_column(sa.Column("default_agent_models", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("platform_config") as batch:
        batch.drop_column("default_agent_models")
