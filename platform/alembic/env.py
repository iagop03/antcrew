"""Alembic migration environment — sync psycopg2 (asyncpg's loop.getaddrinfo fails on Fly.io)."""
from __future__ import annotations

import os
import re
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from sqlmodel import SQLModel

# Import all models so their tables are registered in SQLModel.metadata
import app.models.run  # noqa: F401
import app.models.admin  # noqa: F401
import app.models.feedback  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = SQLModel.metadata


def _get_url() -> str:
    url = os.environ.get("DATABASE_URL") or config.get_main_option("sqlalchemy.url") or ""
    # Strip stray newlines/whitespace that can appear when a secret is pasted
    # across multiple lines (e.g. in GitHub Secrets or Fly secrets UI).
    url = url.replace("\n", "").replace("\r", "").strip()
    if not url:
        raise RuntimeError(
            "No database URL found. Set DATABASE_URL env var or sqlalchemy.url in alembic.ini."
        )
    # Normalize async drivers to sync equivalents.
    if url.startswith("sqlite+aiosqlite://"):
        url = "sqlite" + url[len("sqlite+aiosqlite"):]
    else:
        for prefix in (
            "postgresql+asyncpg://",
            "postgresql+asyncio://",
            "postgresql://",
            "postgres://",
        ):
            if url.startswith(prefix):
                url = "postgresql+psycopg2://" + url[len(prefix):]
                break
    # asyncpg uses ssl=require; psycopg2 uses sslmode=require — convert back if needed.
    url = url.replace("?ssl=require", "?sslmode=require")
    url = url.replace("&ssl=require", "&sslmode=require")
    url = re.sub(r"[&?]ssl=[^&]*", "", url)
    return url


def run_migrations_offline() -> None:
    url = _get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    cfg = config.get_section(config.config_ini_section, {})
    cfg["sqlalchemy.url"] = _get_url()
    connectable = create_engine(cfg["sqlalchemy.url"], poolclass=pool.NullPool)
    with connectable.connect() as conn:
        do_run_migrations(conn)
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
