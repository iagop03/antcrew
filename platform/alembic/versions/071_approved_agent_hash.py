"""Add approved_agent_hash table for UC3 Certified Agent flow.

Revision ID: 071
Revises: 070
Create Date: 2026-08-23
"""
from alembic import op
import sqlalchemy as sa

revision = "071"
down_revision = "070"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "approved_agent_hash",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("team", sa.String(), nullable=False),
        sa.Column("agent_name", sa.String(), nullable=False),
        sa.Column("governance_hash", sa.String(), nullable=False),
        sa.Column("label", sa.String(), nullable=False, server_default=""),
        sa.Column("registered_at", sa.DateTime(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="1"),
    )
    op.create_index("ix_approved_agent_hash_workspace_id", "approved_agent_hash", ["workspace_id"])


def downgrade() -> None:
    op.drop_index("ix_approved_agent_hash_workspace_id", table_name="approved_agent_hash")
    op.drop_table("approved_agent_hash")
