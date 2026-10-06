"""Add Release, ReleaseItem, ReleaseApproval tables for T6 roadmap."""
from alembic import op
import sqlalchemy as sa

revision = "071b"
down_revision = "071"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "release",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("release_id", sa.String(), nullable=False, unique=True),
        sa.Column("workspace_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("state", sa.String(), nullable=False, server_default="draft"),
        sa.Column("target_date", sa.String(), nullable=True),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_release_release_id", "release", ["release_id"])
    op.create_index("ix_release_workspace_id", "release", ["workspace_id"])

    op.create_table(
        "release_item",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("release_id", sa.String(), nullable=False),
        sa.Column("change_ref", sa.String(), nullable=False),
        sa.Column("run_ids", sa.JSON(), nullable=True),
        sa.Column("origin", sa.String(), nullable=False, server_default="antcrew"),
        sa.Column("summary", sa.String(), nullable=True),
        sa.Column("impact_risk", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_release_item_release_id", "release_item", ["release_id"])
    op.create_index("ix_release_item_change_ref", "release_item", ["change_ref"])

    op.create_table(
        "release_approval",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("release_id", sa.String(), nullable=False),
        sa.Column("approver_id", sa.String(), nullable=False),
        sa.Column("approver_role", sa.String(), nullable=False, server_default=""),
        sa.Column("decision", sa.String(), nullable=False),
        sa.Column("reason", sa.String(), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=False),
        sa.Column("row_hash", sa.String(), nullable=False, server_default=""),
    )
    op.create_index("ix_release_approval_release_id", "release_approval", ["release_id"])


def downgrade():
    op.drop_table("release_approval")
    op.drop_table("release_item")
    op.drop_table("release")
