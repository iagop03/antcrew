"""Add agent_event table for per-agent cost/token tracking.

Revision ID: 066
Revises: 065
Create Date: 2026-08-13
"""
from alembic import op
import sqlalchemy as sa

revision = "066"
down_revision = "065"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_event",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("agent_name", sa.String(), nullable=False),
        sa.Column("duration_s", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("tokens_in", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_out", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("produced_keys", sa.String(), nullable=False, server_default="[]"),
        sa.Column("recorded_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_agent_event_run_id", "agent_event", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_event_run_id", table_name="agent_event")
    op.drop_table("agent_event")
