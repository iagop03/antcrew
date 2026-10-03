"""Add invoice_type to invoice table (nacional | intracomunitaria | exportacion).

Revision ID: 064
Revises: 063
Create Date: 2026-08-10
"""
from alembic import op
import sqlalchemy as sa

revision = "064"
down_revision = "063"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "invoice",
        sa.Column(
            "invoice_type",
            sa.String(),
            nullable=False,
            server_default="nacional",
        ),
    )


def downgrade() -> None:
    op.drop_column("invoice", "invoice_type")
