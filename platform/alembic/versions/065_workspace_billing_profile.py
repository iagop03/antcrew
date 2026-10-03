"""Add billing profile fields to workspace table.

Revision ID: 065
Revises: 064
Create Date: 2026-08-10
"""
from alembic import op
import sqlalchemy as sa

revision = "065"
down_revision = "064"
branch_labels = None
depends_on = None

_COLS = [
    ("billing_entity_type", sa.String(), True,  None),   # empresa | autonomo | particular
    ("billing_razon_social", sa.String(), True,  None),  # razón social / nombre completo
    ("billing_nif",          sa.String(), True,  None),  # NIF / CIF / NIE / DNI
    ("billing_address",      sa.String(), True,  None),  # calle y número
    ("billing_postal_code",  sa.String(), True,  None),
    ("billing_city",         sa.String(), True,  None),
    ("billing_country",      sa.String(), False, "ES"),  # ISO 3166-1 alpha-2
    ("billing_email",        sa.String(), True,  None),  # contacto de facturación
    ("billing_phone",        sa.String(), True,  None),
]


def upgrade() -> None:
    for col_name, col_type, nullable, default in _COLS:
        kwargs: dict = {"nullable": nullable}
        if default is not None:
            kwargs["server_default"] = default
        op.add_column("workspace", sa.Column(col_name, col_type, **kwargs))


def downgrade() -> None:
    for col_name, *_ in reversed(_COLS):
        op.drop_column("workspace", col_name)
