"""Persistent KV memory store for agent teams."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import UniqueConstraint
from sqlmodel import JSON, Column, Field, SQLModel

from app.models._utils import _utcnow


class RunMemory(SQLModel, table=True):
    """One row per (workspace_id, team_name) — memory_json holds all KV entries.

    Agents write facts during a run; the platform persists them so the next run
    can pick up where the previous one left off.
    """

    __tablename__ = "run_memory"
    __table_args__ = (
        UniqueConstraint("workspace_id", "team_name", name="uq_run_memory_workspace_team"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    workspace_id: Optional[int] = Field(default=None, index=True)
    team_name: str = Field(index=True)
    memory_json: dict = Field(default_factory=dict, sa_column=Column(JSON))
    updated_at: datetime = Field(default_factory=_utcnow)
