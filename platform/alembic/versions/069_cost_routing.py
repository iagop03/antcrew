"""Add cost routing policy to workspace and model tier config to platform_config."""
from alembic import op
import sqlalchemy as sa

revision = "069"
down_revision = "068"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("workspace", sa.Column("cost_routing_policy", sa.String(), nullable=True, server_default="none"))
    op.add_column("platform_config", sa.Column("tier_cheap_model", sa.String(), nullable=True, server_default="groq:llama-3.3-70b-versatile"))
    op.add_column("platform_config", sa.Column("tier_standard_model", sa.String(), nullable=True, server_default="claude:claude-sonnet-5"))
    op.add_column("platform_config", sa.Column("tier_premium_model", sa.String(), nullable=True, server_default="claude:claude-opus-5"))


def downgrade():
    op.drop_column("workspace", "cost_routing_policy")
    op.drop_column("platform_config", "tier_cheap_model")
    op.drop_column("platform_config", "tier_standard_model")
    op.drop_column("platform_config", "tier_premium_model")
