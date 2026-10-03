"""Models for third-party PM integrations and team governance snapshots."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Column
from sqlmodel import JSON, Field, SQLModel

from app.core.encryption import EncryptedJSON
from app.models._utils import _utcnow


class TicketDestination(SQLModel, table=True):
    """A configured PM integration target for a workspace.

    When a run produces tickets they are synced to all matching destinations.
    provider is 'github', 'linear', or 'jira'.
    config_json holds provider-specific auth and routing (token, repo, team_id, …).
    team_filter restricts sync to a specific team name (None = all teams).
    """

    __tablename__ = "ticket_destination"

    id: Optional[int] = Field(default=None, primary_key=True)
    workspace_id: int = Field(index=True)
    provider: str  # github | linear | jira
    label: str
    config_json: dict = Field(default_factory=dict, sa_column=Column(EncryptedJSON))
    team_filter: Optional[str] = Field(default=None)
    enabled: bool = Field(default=True)
    created_at: datetime = Field(default_factory=_utcnow)


class TeamSnapshot(SQLModel, table=True):
    """Governance snapshot of a team configuration captured when the hash changes.

    Automatically created when a run completes with a different team governance
    hash than the previous run, enabling history and eval score correlation.
    """

    __tablename__ = "team_snapshot"

    id: Optional[int] = Field(default=None, primary_key=True)
    workspace_id: Optional[int] = Field(default=None, index=True)
    team_name: str = Field(index=True)
    team_hash: str  # 16-char SHA-256 prefix; changes when any agent config changes
    agents_json: list = Field(default_factory=list, sa_column=Column(JSON))
    # [{agent_name, governance_hash, stage}, …]
    label: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow)
