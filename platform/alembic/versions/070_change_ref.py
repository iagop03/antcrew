"""Add change_ref to Run for CR traceability (T1)."""
from alembic import op
import sqlalchemy as sa

revision = "070"
down_revision = "069"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("run", sa.Column("change_ref", sa.String(), nullable=True, server_default=None))
    op.create_index("ix_run_change_ref", "run", ["change_ref"])


def downgrade():
    op.drop_index("ix_run_change_ref", table_name="run")
    op.drop_column("run", "change_ref")
