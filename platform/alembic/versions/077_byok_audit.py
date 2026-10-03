"""BYOK audit trail + usage tracking (anomaly detection).

Adds:
  - byok_audit_event table (store / rotate / delete lifecycle events)
  - llm_provider_key.last_used_at, use_count_24h, use_window_start, anomaly_threshold

Revision ID: 077
Revises: 076
Create Date: 2026-08-27
"""
from alembic import op
import sqlalchemy as sa

revision = "077"
down_revision = "076"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "byok_audit_event",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.Integer(), nullable=False, index=True),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("actor_key_id", sa.Integer(), nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("note", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
    )

    op.add_column("llm_provider_key", sa.Column("last_used_at", sa.DateTime(), nullable=True))
    op.add_column("llm_provider_key", sa.Column("use_count_24h", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("llm_provider_key", sa.Column("use_window_start", sa.DateTime(), nullable=True))
    op.add_column("llm_provider_key", sa.Column("anomaly_threshold", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("llm_provider_key", "anomaly_threshold")
    op.drop_column("llm_provider_key", "use_window_start")
    op.drop_column("llm_provider_key", "use_count_24h")
    op.drop_column("llm_provider_key", "last_used_at")
    op.drop_table("byok_audit_event")
