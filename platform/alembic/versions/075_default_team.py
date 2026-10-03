"""Add default_team to workspace and sprint.

Revision ID: 075
Revises: 074
Create Date: 2026-08-25
"""
from alembic import op
import sqlalchemy as sa

revision = "075"
down_revision = "074"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("workspace", sa.Column("default_team", sa.String(), nullable=True))
    op.add_column("sprint", sa.Column("default_team", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("workspace", "default_team")
    op.drop_column("sprint", "default_team")
