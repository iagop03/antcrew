"""079 — portal improvements: run limits, SSO domain, instance usage tracking

Revision ID: 079_portal_improvements
Revises: 078_portal
Create Date: 2026-10-05
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "079_portal_improvements"
down_revision = "078_portal"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # portal_license: monthly run cap + SSO domain
    op.add_column("portal_license", sa.Column("max_runs_per_month", sa.Integer(), nullable=True))
    op.add_column("portal_license", sa.Column("sso_domain", sa.String(), nullable=True))

    # portal_instance: usage counters
    op.add_column("portal_instance", sa.Column("runs_this_month", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("portal_instance", sa.Column("total_runs", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("portal_instance", sa.Column("last_usage_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("portal_instance", "last_usage_at")
    op.drop_column("portal_instance", "total_runs")
    op.drop_column("portal_instance", "runs_this_month")
    op.drop_column("portal_license", "sso_domain")
    op.drop_column("portal_license", "max_runs_per_month")
