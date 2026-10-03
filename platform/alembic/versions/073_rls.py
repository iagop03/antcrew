"""Enable PostgreSQL Row-Level Security on workspace-scoped tables.

Revision ID: 073
Revises: 072
Create Date: 2026-08-23

When ANTCREW_ENABLE_RLS=true, the platform calls
    SELECT set_config('app.workspace_id', '<id>', true)
at the start of each request. These policies then restrict each connection
to only rows belonging to that workspace. Requests without the config set
(background tasks, admin/open-mode) retain full access via the IS NULL branch.

Only PostgreSQL — the upgrade() short-circuits on SQLite.
"""
from alembic import op
import sqlalchemy as sa

revision = "073"
down_revision = "072"
branch_labels = None
depends_on = None

# Tables with a direct workspace_id column.
_TABLES = ("run", "ticket")

_POLICY_SQL = """\
CREATE POLICY ws_isolation ON {table}
    FOR ALL
    USING (
        current_setting('app.workspace_id', true) IS NULL
        OR workspace_id IS NULL
        OR workspace_id::text = current_setting('app.workspace_id', true)
    )
    WITH CHECK (
        current_setting('app.workspace_id', true) IS NULL
        OR workspace_id IS NULL
        OR workspace_id::text = current_setting('app.workspace_id', true)
    )
"""


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if not _is_postgres():
        return
    for table in _TABLES:
        op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
        op.execute(sa.text(_POLICY_SQL.format(table=table)))


def downgrade() -> None:
    if not _is_postgres():
        return
    for table in _TABLES:
        op.execute(sa.text(f"DROP POLICY IF EXISTS ws_isolation ON {table}"))
        op.execute(sa.text(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY"))
