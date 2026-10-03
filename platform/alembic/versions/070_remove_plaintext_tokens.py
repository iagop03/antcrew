"""Remove plaintext session tokens — backfill any remaining token_hash rows, then NULL token.

Migration 033 added token_hash and backfilled it for all sessions. Any sessions created
after 033 already have token=NULL and token_hash set. This migration:
  1. Backfills token_hash for any rows that somehow still have token but no token_hash.
  2. Sets token = NULL on all rows (removing plaintext from the DB entirely).
  3. Drops the legacy index on user_session.token.

After this migration, the application code no longer needs the plaintext fallback lookup
in auth.py (removed in the same commit).
"""
import hashlib

import sqlalchemy as sa
from alembic import op

revision = "070"
down_revision = "069"
branch_labels = None
depends_on = None


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def upgrade() -> None:
    conn = op.get_bind()

    # Backfill any remaining sessions that have a plaintext token but no token_hash.
    # (Should be zero rows on a DB that ran migration 033, but be safe.)
    rows = conn.execute(
        sa.text(
            "SELECT id, token FROM user_session "
            "WHERE token IS NOT NULL AND token_hash IS NULL"
        )
    ).fetchall()
    for row in rows:
        if row[1]:
            conn.execute(
                sa.text("UPDATE user_session SET token_hash = :h WHERE id = :id"),
                {"h": _sha256(row[1]), "id": row[0]},
            )

    # Null out all plaintext tokens — token_hash is now the sole lookup key.
    conn.execute(sa.text("UPDATE user_session SET token = NULL"))

    # Drop the legacy unique index on the plaintext token column (now all NULL).
    dialect = conn.dialect.name
    try:
        if dialect == "postgresql":
            op.drop_index("ix_user_session_token", table_name="user_session")
        else:
            # SQLite index name may vary; ignore if not found.
            op.drop_index("ix_user_session_token", table_name="user_session")
    except Exception:
        pass  # index may not exist or may have a different name on older SQLite DBs


def downgrade() -> None:
    # We cannot restore plaintext tokens from hashes — downgrade is a no-op.
    # The application code falls back gracefully when token IS NULL (returns None).
    pass
