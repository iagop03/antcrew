"""Add Sprint table and backlog fields to Ticket.

Revision ID: 074
Revises: 073
Create Date: 2026-08-25
"""
from alembic import op
import sqlalchemy as sa

revision = "074"
down_revision = "073"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sprint",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("sprint_id", sa.String(), nullable=False, index=True, unique=True),
        sa.Column("workspace_id", sa.Integer(), nullable=True, index=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="planning"),
        sa.Column("backlog_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )

    op.add_column("ticket", sa.Column("sprint_id", sa.String(), nullable=True, index=True))
    op.add_column("ticket", sa.Column("depends_on", sa.JSON(), nullable=True))
    op.add_column("ticket", sa.Column("backlog_order", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("ticket", "backlog_order")
    op.drop_column("ticket", "depends_on")
    op.drop_column("ticket", "sprint_id")
    op.drop_table("sprint")
