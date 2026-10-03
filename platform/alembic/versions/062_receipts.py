"""Create receipt table (facturas recibidas / gastos).

Revision ID: 062
Revises: 061
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "062"
down_revision = "061"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "receipt",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("invoice_number", sa.String(), nullable=True),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("vendor_name", sa.String(), nullable=False),
        sa.Column("vendor_nif", sa.String(), nullable=True),
        sa.Column("vendor_country", sa.String(2), nullable=False, server_default="ES"),
        sa.Column("vendor_address", sa.Text(), nullable=True),
        sa.Column("base_imponible", sa.Numeric(10, 2), nullable=True),
        sa.Column("tax_rate", sa.Numeric(5, 2), nullable=True),
        sa.Column("tax_amount", sa.Numeric(10, 2), nullable=True),
        sa.Column("total_amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="EUR"),
        sa.Column("eur_amount", sa.Numeric(10, 2), nullable=True),
        sa.Column("reverse_charge", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("doc_type", sa.String(), nullable=False, server_default="factura_completa"),
        sa.Column("category", sa.String(), nullable=False, server_default="other"),
        sa.Column("deductible", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("file_path", sa.String(), nullable=True),
        sa.Column("filename", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_receipt_date", "receipt", ["date"])
    op.create_index("ix_receipt_status", "receipt", ["status"])
    op.create_index("ix_receipt_category", "receipt", ["category"])


def downgrade() -> None:
    op.drop_table("receipt")
