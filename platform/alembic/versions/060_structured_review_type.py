"""Add review_type and item_schema columns to hitl_review.

Revision ID: 060
Revises: 059
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "060"
down_revision = "059"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("hitl_review") as batch_op:
        batch_op.add_column(sa.Column("review_type", sa.String(), nullable=False, server_default="approval"))
        batch_op.add_column(sa.Column("item_schema", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("hitl_review") as batch_op:
        batch_op.drop_column("item_schema")
        batch_op.drop_column("review_type")
