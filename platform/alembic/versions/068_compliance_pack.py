"""Add compliance_pack fields to workspace and platform_config.

Revision ID: 068
Revises: 067
Create Date: 2026-08-22
"""
from alembic import op
import sqlalchemy as sa

revision = "068"
down_revision = "067"
branch_labels = None
depends_on = None

_WS_COLS = [
    ("compliance_pack_enabled",       sa.Boolean(), False, "false"),
    ("compliance_pack_price_monthly", sa.Float(),   True,  None),
    ("compliance_pack_price_annual",  sa.Float(),   True,  None),
]

_PC_COLS = [
    ("compliance_pack_price_monthly", sa.Float(), False, "49.0"),
    ("compliance_pack_price_annual",  sa.Float(), False, "490.0"),
]


def upgrade() -> None:
    for col_name, col_type, nullable, server_default in _WS_COLS:
        kwargs: dict = {"nullable": nullable}
        if server_default is not None:
            kwargs["server_default"] = server_default
        op.add_column("workspace", sa.Column(col_name, col_type, **kwargs))

    for col_name, col_type, nullable, server_default in _PC_COLS:
        kwargs = {"nullable": nullable}
        if server_default is not None:
            kwargs["server_default"] = server_default
        op.add_column("platform_config", sa.Column(col_name, col_type, **kwargs))


def downgrade() -> None:
    for col_name, *_ in reversed(_WS_COLS):
        op.drop_column("workspace", col_name)
    for col_name, *_ in reversed(_PC_COLS):
        op.drop_column("platform_config", col_name)
