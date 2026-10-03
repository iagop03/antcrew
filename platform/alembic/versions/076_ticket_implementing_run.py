"""Add implementing_run_id to Ticket for sprint DAG context passing.

Revision ID: 076
Revises: 075
Create Date: 2026-08-25
"""
from alembic import op
import sqlalchemy as sa

revision = "076"
down_revision = "075"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ticket", sa.Column("implementing_run_id", sa.String(), nullable=True, index=True))


def downgrade() -> None:
    op.drop_column("ticket", "implementing_run_id")
