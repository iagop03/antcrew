"""Add data_retention_days to workspace for per-workspace run purge policy.

Revision ID: 056
Revises: 055
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "056"
down_revision = "055"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("workspace", sa.Column("data_retention_days", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("workspace", "data_retention_days")
