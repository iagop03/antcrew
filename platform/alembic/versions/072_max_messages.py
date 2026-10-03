"""Add max_messages to workspace and run_preset.

Revision ID: 072
Revises: 071
Create Date: 2026-08-23
"""
from alembic import op
import sqlalchemy as sa

revision = "072"
down_revision = "071"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("workspace",   sa.Column("max_messages", sa.Integer(), nullable=True))
    op.add_column("run_preset",  sa.Column("max_messages", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("workspace",  "max_messages")
    op.drop_column("run_preset", "max_messages")
