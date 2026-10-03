"""Add hitl_channel, feedback_schema_json, structured_feedback_json to hitl_review.

Revision ID: 067
Revises: 066
Create Date: 2026-08-13
"""
from alembic import op
import sqlalchemy as sa

revision = "067"
down_revision = "066"
branch_labels = None
depends_on = None

_COLS = [
    ("hitl_channel",             sa.String(),  False, "default"),
    ("feedback_schema_json",     sa.Text(),    True,  None),
    ("structured_feedback_json", sa.Text(),    True,  None),
]


def upgrade() -> None:
    for col_name, col_type, nullable, default in _COLS:
        kwargs: dict = {"nullable": nullable}
        if default is not None:
            kwargs["server_default"] = default
        op.add_column("hitl_review", sa.Column(col_name, col_type, **kwargs))


def downgrade() -> None:
    for col_name, *_ in reversed(_COLS):
        op.drop_column("hitl_review", col_name)
