"""Database setup using SQLModel + aiosqlite (SQLite) or asyncpg (PostgreSQL).

Migration strategy:
  SQLite  (dev / test) — create_all on fresh DB + inline _migrate_* helpers
  PostgreSQL (production) — alembic upgrade head via subprocess in executor
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
from pathlib import Path
from typing import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

log = logging.getLogger(__name__)

DB_URL = os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./platform.db")

# create_async_engine requires an async driver prefix; normalize bare postgresql:// URLs.
# asyncpg also doesn't understand the psycopg2-style sslmode= query param — strip it
# and pass ssl=True via connect_args when sslmode is require/verify-ca/verify-full.
_ssl = False
if DB_URL.startswith("postgres://"):
    DB_URL = "postgresql+asyncpg://" + DB_URL[len("postgres://"):]
    _ssl = True
elif DB_URL.startswith("postgresql://"):
    DB_URL = "postgresql+asyncpg://" + DB_URL[len("postgresql://"):]
    _ssl = True

# Strip query params asyncpg doesn't understand (sslmode, channel_binding, etc.)
_STRIP_PARAMS = {"sslmode", "channel_binding", "connect_timeout", "application_name"}
if any(f"{p}=" in DB_URL for p in _STRIP_PARAMS):
    from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
    _parsed = urlparse(DB_URL)
    _params = parse_qs(_parsed.query, keep_blank_values=True)
    _sslmode = _params.pop("sslmode", ["disable"])[0]
    _ssl = _ssl or (_sslmode in ("require", "verify-ca", "verify-full"))
    for _p in ("channel_binding", "connect_timeout", "application_name"):
        _params.pop(_p, None)
    DB_URL = urlunparse(_parsed._replace(query=urlencode({k: v[0] for k, v in _params.items()})))

_connect_args: dict = {"ssl": True} if _ssl else {}
# Explicit pool config so workers × pool_size stays below the DB connection limit.
# SQLite uses a StaticPool internally and ignores these kwargs.
_pool_kwargs: dict = (
    {
        "pool_size":     int(os.getenv("DB_POOL_SIZE", "10")),
        "max_overflow":  int(os.getenv("DB_MAX_OVERFLOW", "5")),
        "pool_timeout":  int(os.getenv("DB_POOL_TIMEOUT", "30")),
        "pool_pre_ping": True,   # re-validate connections on checkout; drops stale ones silently
    }
    if not DB_URL.startswith("sqlite")
    else {}
)
engine = create_async_engine(DB_URL, echo=False, connect_args=_connect_args, **_pool_kwargs)

_PROJECT_ROOT = Path(__file__).parent.parent.parent  # app/core/database.py → project root

# Stable advisory-lock key for serialising concurrent Alembic runs across replicas.
# Computed once: abs(hash("antcrew_migration")) % (2**31) — fits in PostgreSQL int4.
_MIGRATION_ADVISORY_LOCK = 938271406


async def _run_alembic_upgrade() -> None:
    """Run `alembic upgrade head` for the current DATABASE_URL.

    Runs in a thread-pool executor so the async event loop is not blocked and
    there is no nested-asyncio issue (alembic's env.py calls asyncio.run()
    internally, which requires a fresh event loop in a separate thread).

    Set ANTCREW_SKIP_MIGRATION=true to bypass this function entirely — use when
    migrations are already handled externally (init container, compose pre-command,
    K8s job).  When the flag is absent, a PostgreSQL session-level advisory lock
    serialises concurrent replicas so only one actually runs the upgrade; the others
    wait, then see the schema is already at head and exit immediately.
    """
    if os.environ.get("ANTCREW_SKIP_MIGRATION", "").lower() in ("1", "true", "yes"):
        log.info("alembic: ANTCREW_SKIP_MIGRATION set — skipping in-process migration")
        return

    env = {**os.environ, "DATABASE_URL": DB_URL}
    loop = asyncio.get_running_loop()

    # Acquire a PostgreSQL session-level advisory lock before running alembic.
    # pg_advisory_lock() blocks until the lock is available, so concurrent replicas
    # serialise here: the first runs the upgrade, the rest run a no-op.
    # The lock is released automatically when the connection closes.
    async with engine.connect() as _lock_conn:
        await _lock_conn.execute(
            text("SELECT pg_advisory_lock(:key)"), {"key": _MIGRATION_ADVISORY_LOCK}
        )
        log.info("alembic: advisory lock %d acquired — running upgrade head", _MIGRATION_ADVISORY_LOCK)
        result: subprocess.CompletedProcess = await loop.run_in_executor(
            None,
            lambda: subprocess.run(
                ["alembic", "upgrade", "head"],
                cwd=str(_PROJECT_ROOT),
                capture_output=True,
                text=True,
                env=env,
            ),
        )
        # Lock released when _lock_conn exits.

    if result.stdout.strip():
        log.info("alembic: %s", result.stdout.strip())
    if result.returncode != 0:
        raise RuntimeError(
            f"alembic upgrade head failed (exit {result.returncode}):\n{result.stderr}"
        )


async def _migrate_webhook_events(eng) -> None:
    """One-time idempotent migration: move WebhookConfig.events JSON → webhook_event rows.

    The old 'events' column (JSON string) is no longer in the SQLModel definition
    but may still exist in existing SQLite databases. This reads it via raw SQL
    and populates the webhook_event table. Safe to run on fresh DBs (no-op).
    """
    try:
        async with eng.begin() as conn:
            rows = (await conn.execute(
                text(
                    "SELECT id, events FROM webhook_config "
                    "WHERE events IS NOT NULL AND events != '' AND events != '[]'"
                )
            )).fetchall()
    except Exception as exc:
        log.debug("_migrate_webhook_events: skipped (column absent or table missing): %s", exc)
        return

    if not rows:
        return

    async with eng.begin() as conn:
        for webhook_id, events_json in rows:
            try:
                events_list = json.loads(events_json)
            except Exception as exc:
                log.debug("json.loads failed for webhook %s: %s", webhook_id, exc)
                continue
            for event_type in (e for e in events_list if isinstance(e, str)):
                existing = (await conn.execute(
                    text(
                        "SELECT id FROM webhook_event "
                        "WHERE webhook_id = :wid AND event_type = :et"
                    ),
                    {"wid": webhook_id, "et": event_type},
                )).first()
                if not existing:
                    await conn.execute(
                        text(
                            "INSERT INTO webhook_event (webhook_id, event_type) "
                            "VALUES (:wid, :et)"
                        ),
                        {"wid": webhook_id, "et": event_type},
                    )


async def _migrate_drop_budget_exceeded(eng) -> None:
    """Idempotent migration: drop workspace.budget_exceeded column if present."""
    try:
        async with eng.begin() as conn:
            cols = (await conn.execute(text("PRAGMA table_info(workspace)"))).fetchall()
            col_names = [row[1] for row in cols]
            if "budget_exceeded" in col_names:
                await conn.execute(text("ALTER TABLE workspace DROP COLUMN budget_exceeded"))
    except Exception as exc:
        log.debug("migration step skipped (PostgreSQL or table absent): %s", exc)


async def _migrate_eval_run_id(eng) -> None:
    """Idempotent migration: add eval_run.run_id column if absent."""
    try:
        async with eng.begin() as conn:
            cols = (await conn.execute(text("PRAGMA table_info(eval_run)"))).fetchall()
            col_names = [row[1] for row in cols]
            if "run_id" not in col_names:
                await conn.execute(text("ALTER TABLE eval_run ADD COLUMN run_id TEXT"))
    except Exception as exc:
        log.debug("migration step skipped (PostgreSQL or table absent): %s", exc)


async def _migrate_workspace_membership(eng) -> None:
    """Idempotent migration: create workspace_membership table if absent."""
    try:
        async with eng.begin() as conn:
            tables = (await conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name='workspace_membership'")
            )).fetchall()
            if not tables:
                await conn.execute(text(
                    "CREATE TABLE workspace_membership ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "api_key_id INTEGER NOT NULL, "
                    "workspace_id INTEGER NOT NULL, "
                    "created_at DATETIME"
                    ")"
                ))
                await conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS ix_workspace_membership_api_key_id "
                    "ON workspace_membership(api_key_id)"
                ))
                await conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS ix_workspace_membership_workspace_id "
                    "ON workspace_membership(workspace_id)"
                ))
    except Exception as exc:
        log.debug("migration step skipped (handled by Alembic): %s", exc)


async def _migrate_stripe_fields(eng) -> None:
    """Idempotent migration: add/rename billing columns on workspace if absent."""
    try:
        async with eng.begin() as conn:
            cols = (await conn.execute(text("PRAGMA table_info(workspace)"))).fetchall()
            col_names = {row[1] for row in cols}

            # Stripe-specific fields (named; kept as-is)
            for col_def, col_name in [
                ("stripe_customer_id TEXT", "stripe_customer_id"),
                ("stripe_subscription_id TEXT", "stripe_subscription_id"),
            ]:
                if col_name not in col_names:
                    await conn.execute(text(f"ALTER TABLE workspace ADD COLUMN {col_def}"))  # nosemgrep

            if "stripe_customer_id" not in col_names:
                await conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS ix_workspace_stripe_customer_id "
                    "ON workspace(stripe_customer_id)"
                ))

            # Rename legacy stripe_subscription_status → subscription_status
            if "stripe_subscription_status" in col_names and "subscription_status" not in col_names:
                await conn.execute(text(
                    "ALTER TABLE workspace RENAME COLUMN "
                    "stripe_subscription_status TO subscription_status"
                ))
                col_names.discard("stripe_subscription_status")
                col_names.add("subscription_status")

            # Provider-neutral and MoR lane fields
            for col_def, col_name in [
                ("subscription_status TEXT", "subscription_status"),
                ("billing_provider TEXT NOT NULL DEFAULT 'mor'", "billing_provider"),
                ("mor_customer_id TEXT", "mor_customer_id"),
                ("mor_subscription_id TEXT", "mor_subscription_id"),
            ]:
                if col_name not in col_names:
                    await conn.execute(text(f"ALTER TABLE workspace ADD COLUMN {col_def}"))  # nosemgrep
    except Exception as exc:
        log.debug("migration step skipped (PostgreSQL or table absent): %s", exc)


async def _migrate_workspace_is_trial(eng) -> None:
    """Idempotent migration: add is_trial column to workspace if absent."""
    try:
        async with eng.begin() as conn:
            cols = (await conn.execute(text("PRAGMA table_info(workspace)"))).fetchall()
            col_names = {row[1] for row in cols}
            if "is_trial" not in col_names:
                # Default 0 (False) for existing workspaces — only new ones start in trial.
                await conn.execute(text(
                    "ALTER TABLE workspace ADD COLUMN is_trial BOOLEAN NOT NULL DEFAULT 0"
                ))
    except Exception as exc:
        log.debug("migration step skipped (PostgreSQL or table absent): %s", exc)


async def _migrate_llm_base_url(eng) -> None:
    """Idempotent migration: add base_url column to llm_provider_key if absent."""
    try:
        async with eng.begin() as conn:
            cols = (await conn.execute(text("PRAGMA table_info(llm_provider_key)"))).fetchall()
            col_names = {row[1] for row in cols}
            if "base_url" not in col_names:
                await conn.execute(text("ALTER TABLE llm_provider_key ADD COLUMN base_url TEXT"))
    except Exception as exc:
        log.debug("migration step skipped (PostgreSQL or table absent): %s", exc)


async def _migrate_run_client_label(eng) -> None:
    """Idempotent migration: add client_label column to run if absent."""
    try:
        async with eng.begin() as conn:
            cols = (await conn.execute(text("PRAGMA table_info(run)"))).fetchall()
            col_names = {row[1] for row in cols}
            if "client_label" not in col_names:
                await conn.execute(text("ALTER TABLE run ADD COLUMN client_label TEXT"))
    except Exception as exc:
        log.debug("migration step skipped (PostgreSQL or table absent): %s", exc)


async def _migrate_hitl_client_token(eng) -> None:
    """Idempotent migration: add client_token column to hitl_review if absent."""
    try:
        async with eng.begin() as conn:
            cols = (await conn.execute(text("PRAGMA table_info(hitl_review)"))).fetchall()
            col_names = {row[1] for row in cols}
            if "client_token" not in col_names:
                await conn.execute(text("ALTER TABLE hitl_review ADD COLUMN client_token TEXT"))
                await conn.execute(text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS ix_hitl_review_client_token "
                    "ON hitl_review(client_token)"
                ))
    except Exception as exc:
        log.debug("migration step skipped (PostgreSQL or table absent): %s", exc)


async def _migrate_compare_run(eng) -> None:
    """Idempotent migration: create compare_run table if absent."""
    try:
        async with eng.begin() as conn:
            tables = (await conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name='compare_run'")
            )).fetchall()
            if not tables:
                await conn.execute(text(
                    "CREATE TABLE compare_run ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "compare_id TEXT NOT NULL, "
                    "run_id_a TEXT NOT NULL, "
                    "run_id_b TEXT NOT NULL, "
                    "model_a TEXT NOT NULL, "
                    "model_b TEXT NOT NULL, "
                    "team TEXT NOT NULL, "
                    "request TEXT NOT NULL, "
                    "status TEXT NOT NULL DEFAULT 'running', "
                    "workspace_id INTEGER, "
                    "created_at DATETIME, "
                    "finished_at DATETIME"
                    ")"
                ))
                await conn.execute(text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS ix_compare_run_compare_id "
                    "ON compare_run(compare_id)"
                ))
                await conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS ix_compare_run_run_id_a ON compare_run(run_id_a)"
                ))
                await conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS ix_compare_run_run_id_b ON compare_run(run_id_b)"
                ))
    except Exception as exc:
        log.debug("migration step skipped (handled by Alembic): %s", exc)


async def _migrate_eval_regression_id(eng) -> None:
    """Idempotent migration: add regression_id column to eval_run if absent."""
    try:
        async with eng.begin() as conn:
            cols = (await conn.execute(text("PRAGMA table_info(eval_run)"))).fetchall()
            col_names = {row[1] for row in cols}
            if "regression_id" not in col_names:
                await conn.execute(text("ALTER TABLE eval_run ADD COLUMN regression_id TEXT"))
                await conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS ix_eval_run_regression_id "
                    "ON eval_run(regression_id)"
                ))
    except Exception as exc:
        log.debug("migration step skipped (PostgreSQL or table absent): %s", exc)


async def _migrate_user_table(eng) -> None:
    """Idempotent migration: create user table if absent; add display_name column if missing."""
    try:
        async with eng.begin() as conn:
            tables = (await conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name='user'")
            )).fetchall()
            if not tables:
                await conn.execute(text(
                    "CREATE TABLE user ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "email TEXT NOT NULL UNIQUE, "
                    "password_hash TEXT NOT NULL, "
                    "display_name TEXT, "
                    "created_at DATETIME"
                    ")"
                ))
                await conn.execute(text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS ix_user_email ON user(email)"
                ))
            else:
                # Add display_name to existing tables created before migration 029
                cols = (await conn.execute(text("PRAGMA table_info(user)"))).fetchall()
                col_names = {row[1] for row in cols}
                if "display_name" not in col_names:
                    await conn.execute(text("ALTER TABLE user ADD COLUMN display_name TEXT"))
    except Exception as exc:
        log.debug("migration step skipped (handled by Alembic 022, 029): %s", exc)


async def _migrate_user_session_table(eng) -> None:
    """Idempotent migration: create user_session table if absent; add token_hash column if missing."""
    try:
        async with eng.begin() as conn:
            tables = (await conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name='user_session'")
            )).fetchall()
            if not tables:
                await conn.execute(text(
                    "CREATE TABLE user_session ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "token TEXT UNIQUE, "
                    "token_hash TEXT UNIQUE, "
                    "user_id INTEGER, "
                    "api_key_id INTEGER, "
                    "created_at DATETIME, "
                    "expires_at DATETIME NOT NULL, "
                    "revoked BOOLEAN NOT NULL DEFAULT 0"
                    ")"
                ))
                await conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS ix_user_session_user_id ON user_session(user_id)"
                ))
            else:
                # Add token_hash column to existing tables that were created without it
                cols = (await conn.execute(text("PRAGMA table_info(user_session)"))).fetchall()
                col_names = {row[1] for row in cols}
                if "token_hash" not in col_names:
                    await conn.execute(text("ALTER TABLE user_session ADD COLUMN token_hash TEXT"))
                    await conn.execute(text(
                        "CREATE UNIQUE INDEX IF NOT EXISTS ix_user_session_token_hash "
                        "ON user_session(token_hash) WHERE token_hash IS NOT NULL"
                    ))
    except Exception as exc:
        log.debug("migration step skipped (handled by Alembic 022, 033, 041): %s", exc)


async def _migrate_apikey_user_id(eng) -> None:
    """Idempotent migration: add user_id FK column to api_key if absent."""
    try:
        async with eng.begin() as conn:
            cols = (await conn.execute(text("PRAGMA table_info(api_key)"))).fetchall()
            col_names = {row[1] for row in cols}
            if "user_id" not in col_names:
                await conn.execute(text("ALTER TABLE api_key ADD COLUMN user_id INTEGER"))
    except Exception as exc:
        log.debug("migration step skipped (handled by Alembic): %s", exc)


async def _migrate_apikey_notification_fields(eng) -> None:
    """Idempotent migration: add slack_user_id and telegram_chat_id to api_key."""
    try:
        async with eng.begin() as conn:
            cols = (await conn.execute(text("PRAGMA table_info(api_key)"))).fetchall()
            col_names = {row[1] for row in cols}
            if "slack_user_id" not in col_names:
                await conn.execute(text("ALTER TABLE api_key ADD COLUMN slack_user_id TEXT"))
            if "telegram_chat_id" not in col_names:
                await conn.execute(text("ALTER TABLE api_key ADD COLUMN telegram_chat_id TEXT"))
    except Exception as exc:
        log.debug("migration step skipped (handled by Alembic): %s", exc)


async def _migrate_workspace_owner_user_id(eng) -> None:
    """Idempotent migration: add owner_user_id column to workspace if absent."""
    try:
        async with eng.begin() as conn:
            cols = (await conn.execute(text("PRAGMA table_info(workspace)"))).fetchall()
            col_names = {row[1] for row in cols}
            if "owner_user_id" not in col_names:
                await conn.execute(text("ALTER TABLE workspace ADD COLUMN owner_user_id INTEGER"))
                await conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS ix_workspace_owner_user_id "
                    "ON workspace(owner_user_id)"
                ))
    except Exception as exc:
        log.debug("migration step skipped (handled by Alembic): %s", exc)


async def _migrate_byok_managed_fallback(eng) -> None:
    """Idempotent migration: add byok_managed_fallback column to workspace if absent."""
    try:
        async with eng.begin() as conn:
            cols = (await conn.execute(text("PRAGMA table_info(workspace)"))).fetchall()
            col_names = {row[1] for row in cols}
            if "byok_managed_fallback" not in col_names:
                await conn.execute(text(
                    "ALTER TABLE workspace ADD COLUMN byok_managed_fallback BOOLEAN NOT NULL DEFAULT 0"
                ))
    except Exception as exc:
        log.debug("migration step skipped (handled by Alembic): %s", exc)


async def _migrate_pipeline_def(eng) -> None:
    """Idempotent migration: create pipeline_def table if absent."""
    try:
        async with eng.begin() as conn:
            tables = (await conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name='pipeline_def'")
            )).fetchall()
            if not tables:
                await conn.execute(text(
                    "CREATE TABLE pipeline_def ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "workspace_id INTEGER REFERENCES workspace(id), "
                    "name TEXT NOT NULL, "
                    "description TEXT, "
                    "is_template BOOLEAN NOT NULL DEFAULT 0, "
                    "definition TEXT NOT NULL, "
                    "created_at DATETIME"
                    ")"
                ))
                await conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS ix_pipeline_def_workspace_id "
                    "ON pipeline_def(workspace_id)"
                ))
    except Exception as exc:
        log.debug("migration step skipped (handled by Alembic): %s", exc)


async def _migrate_eval_scores(eng) -> None:
    """Idempotent migration: add overall_score and passed columns to eval_run if absent."""
    try:
        async with eng.begin() as conn:
            cols = (await conn.execute(text("PRAGMA table_info(eval_run)"))).fetchall()
            col_names = {row[1] for row in cols}
            if "overall_score" not in col_names:
                await conn.execute(text("ALTER TABLE eval_run ADD COLUMN overall_score REAL"))
            if "passed" not in col_names:
                await conn.execute(text("ALTER TABLE eval_run ADD COLUMN passed BOOLEAN"))
    except Exception as exc:
        log.debug("migration step skipped (handled by Alembic): %s", exc)


async def _migrate_run_billed_usd(eng) -> None:
    """Idempotent migration: add billed_usd column to run if absent."""
    try:
        async with eng.begin() as conn:
            cols = (await conn.execute(text("PRAGMA table_info(run)"))).fetchall()
            col_names = {row[1] for row in cols}
            if "billed_usd" not in col_names:
                await conn.execute(text("ALTER TABLE run ADD COLUMN billed_usd REAL"))
    except Exception as exc:
        log.debug("migration step skipped (handled by Alembic): %s", exc)


async def _migrate_apikey_client_label(eng) -> None:
    """Idempotent migration: add client_label column to api_key if absent."""
    try:
        async with eng.begin() as conn:
            cols = (await conn.execute(text("PRAGMA table_info(api_key)"))).fetchall()
            col_names = {row[1] for row in cols}
            if "client_label" not in col_names:
                await conn.execute(text("ALTER TABLE api_key ADD COLUMN client_label TEXT"))
    except Exception as exc:
        log.debug("migration step skipped (handled by Alembic): %s", exc)


async def _migrate_ticket_destinations(eng) -> None:
    """Idempotent migration: create ticket_destination table if absent."""
    try:
        async with eng.begin() as conn:
            tables = {
                row[0]
                for row in (await conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table'")
                )).fetchall()
            }
            if "ticket_destination" not in tables:
                await conn.execute(text(
                    """
                    CREATE TABLE ticket_destination (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        workspace_id INTEGER NOT NULL,
                        provider TEXT NOT NULL,
                        label TEXT NOT NULL,
                        config_json TEXT NOT NULL DEFAULT '{}',
                        team_filter TEXT,
                        enabled INTEGER NOT NULL DEFAULT 1,
                        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                ))
                await conn.execute(text(
                    "CREATE INDEX ix_ticket_destination_workspace_id ON ticket_destination(workspace_id)"
                ))
    except Exception as exc:
        log.debug("migration step skipped (handled by Alembic): %s", exc)


async def _migrate_team_snapshots(eng) -> None:
    """Idempotent migration: create team_snapshot table if absent."""
    try:
        async with eng.begin() as conn:
            tables = {
                row[0]
                for row in (await conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table'")
                )).fetchall()
            }
            if "team_snapshot" not in tables:
                await conn.execute(text(
                    """
                    CREATE TABLE team_snapshot (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        workspace_id INTEGER,
                        team_name TEXT NOT NULL,
                        team_hash TEXT NOT NULL,
                        agents_json TEXT NOT NULL DEFAULT '[]',
                        label TEXT,
                        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                ))
                await conn.execute(text(
                    "CREATE INDEX ix_team_snapshot_workspace_id ON team_snapshot(workspace_id)"
                ))
                await conn.execute(text(
                    "CREATE INDEX ix_team_snapshot_team_name ON team_snapshot(team_name)"
                ))
    except Exception as exc:
        log.debug("migration step skipped (handled by Alembic): %s", exc)


async def _migrate_run_memory(eng) -> None:
    """Idempotent migration: create run_memory table if absent."""
    try:
        async with eng.begin() as conn:
            tables = {
                row[0]
                for row in (await conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table'")
                )).fetchall()
            }
            if "run_memory" not in tables:
                await conn.execute(text(
                    """
                    CREATE TABLE run_memory (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        workspace_id INTEGER,
                        team_name TEXT NOT NULL,
                        memory_json TEXT NOT NULL DEFAULT '{}',
                        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        CONSTRAINT uq_run_memory_workspace_team UNIQUE (workspace_id, team_name)
                    )
                    """
                ))
                await conn.execute(text(
                    "CREATE INDEX ix_run_memory_workspace_id ON run_memory(workspace_id)"
                ))
                await conn.execute(text(
                    "CREATE INDEX ix_run_memory_team_name ON run_memory(team_name)"
                ))
    except Exception as exc:
        log.debug("migration step skipped (handled by Alembic): %s", exc)


async def _migrate_workspace_docs_s3(eng) -> None:
    """Idempotent migration: add docs S3 config columns to workspace if absent."""
    try:
        async with eng.begin() as conn:
            cols = {row[1] for row in (await conn.execute(text("PRAGMA table_info(workspace)"))).fetchall()}
            for col_def, col_name in [
                ("docs_s3_bucket TEXT", "docs_s3_bucket"),
                ("docs_s3_prefix TEXT", "docs_s3_prefix"),
                ("docs_s3_region TEXT", "docs_s3_region"),
                ("docs_s3_access_key_enc TEXT", "docs_s3_access_key_enc"),
                ("docs_s3_secret_key_enc TEXT", "docs_s3_secret_key_enc"),
                ("docs_schema_yaml TEXT", "docs_schema_yaml"),
            ]:
                if col_name not in cols:
                    await conn.execute(text(f"ALTER TABLE workspace ADD COLUMN {col_def}"))  # nosemgrep
    except Exception as exc:
        log.debug("migration step skipped (handled by Alembic): %s", exc)


async def init_db() -> None:
    if "postgresql" in DB_URL:
        await _run_alembic_upgrade()
        return
    # SQLite: create_all for fresh DBs + inline helpers for existing ones
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    await _migrate_webhook_events(engine)
    await _migrate_drop_budget_exceeded(engine)
    await _migrate_eval_run_id(engine)
    await _migrate_workspace_membership(engine)
    await _migrate_stripe_fields(engine)
    await _migrate_workspace_is_trial(engine)
    await _migrate_llm_base_url(engine)
    await _migrate_run_client_label(engine)
    await _migrate_hitl_client_token(engine)
    await _migrate_pipeline_def(engine)
    await _migrate_compare_run(engine)
    await _migrate_eval_regression_id(engine)
    await _migrate_user_table(engine)
    await _migrate_user_session_table(engine)
    await _migrate_apikey_user_id(engine)
    await _migrate_apikey_notification_fields(engine)
    await _migrate_workspace_owner_user_id(engine)
    await _migrate_byok_managed_fallback(engine)
    await _migrate_eval_scores(engine)
    await _migrate_run_billed_usd(engine)
    await _migrate_apikey_client_label(engine)
    await _migrate_ticket_destinations(engine)
    await _migrate_team_snapshots(engine)
    await _migrate_run_memory(engine)
    await _migrate_workspace_docs_s3(engine)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session


class AsyncSessionFactory:
    """Async context manager that yields a standalone AsyncSession.

    Used by background tasks that cannot use FastAPI's get_session dependency.

    Usage::

        async with AsyncSessionFactory() as session:
            ...
    """

    async def __aenter__(self) -> AsyncSession:
        self._session = AsyncSession(engine, expire_on_commit=False)
        return self._session

    async def __aexit__(self, *args) -> None:
        await self._session.close()
