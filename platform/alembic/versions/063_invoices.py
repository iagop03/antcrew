"""Create invoice table (facturas emitidas / ingresos).

Revision ID: 063
Revises: 062
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "063"
down_revision = "062"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "invoice",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("invoice_number", sa.String(), nullable=False, unique=True),  # ANT-2025-001
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("customer_name", sa.String(), nullable=False),
        sa.Column("customer_nif", sa.String(), nullable=True),
        sa.Column("customer_country", sa.String(2), nullable=False, server_default="ES"),
        sa.Column("customer_address", sa.Text(), nullable=True),
        sa.Column("line_items", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("base_imponible", sa.Numeric(10, 2), nullable=True),
        sa.Column("tax_rate", sa.Numeric(5, 2), nullable=True, server_default="21"),
        sa.Column("tax_amount", sa.Numeric(10, 2), nullable=True),
        sa.Column("total_amount", sa.Numeric(10, 2), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False, server_default="EUR"),
        sa.Column("status", sa.String(), nullable=False, server_default="draft"),
        sa.Column("payment_date", sa.Date(), nullable=True),
        sa.Column("payment_method", sa.String(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_invoice_date", "invoice", ["date"])
    op.create_index("ix_invoice_status", "invoice", ["status"])
    op.create_index("ix_invoice_number", "invoice", ["invoice_number"])


def downgrade() -> None:
    op.drop_table("invoice")
