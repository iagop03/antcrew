"""Compliance Pack supplementary models."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from app.models._utils import _utcnow


class ApprovedAgentHash(SQLModel, table=True):
    """Registry of approved governance hashes for certified agents.

    When a hash is registered for (workspace, team, agent_name), any run of
    that team whose agent produced a different hash is flagged as 'drifted'
    in the run certificate.  A run with no registered hashes is 'unchecked'.
    """

    __tablename__ = "approved_agent_hash"

    id: Optional[int] = Field(default=None, primary_key=True)
    workspace_id: int = Field(index=True)
    team: str
    agent_name: str
    governance_hash: str
    label: str = Field(default="")
    registered_at: datetime = Field(default_factory=_utcnow)
    active: bool = Field(default=True)
