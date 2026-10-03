"""Add push trigger fields to github_installation.

Revision ID: 059
Revises: 058
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "059"
down_revision = "058"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("github_installation") as batch:
        batch.add_column(sa.Column("push_goal", sa.Text(), nullable=True))
        batch.add_column(sa.Column("push_model", sa.String(), nullable=False, server_default="claude"))
        batch.add_column(sa.Column("push_branch_filter", sa.String(), nullable=False, server_default="*"))


def downgrade() -> None:
    with op.batch_alter_table("github_installation") as batch:
        batch.drop_column("push_branch_filter")
        batch.drop_column("push_model")
        batch.drop_column("push_goal")
