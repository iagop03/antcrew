"""080 — GitHub SSO: github_id + github_login columns on user table

Revision ID: 080_github_sso
Revises: 079_portal_improvements
Create Date: 2026-10-06
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "080_github_sso"
down_revision = "079_portal_improvements"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("user") as batch_op:
        batch_op.add_column(sa.Column("github_id", sa.BigInteger(), nullable=True))
        batch_op.add_column(sa.Column("github_login", sa.String(), nullable=True))
        batch_op.create_index("ix_user_github_id", ["github_id"], unique=True)


def downgrade() -> None:
    with op.batch_alter_table("user") as batch_op:
        batch_op.drop_index("ix_user_github_id")
        batch_op.drop_column("github_login")
        batch_op.drop_column("github_id")
