"""Platform Release and ReleaseItem SQLModel tables."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Column
from sqlmodel import JSON, Field, SQLModel

from app.models._utils import _utcnow


class Release(SQLModel, table=True):
    """A named group of CRs scheduled for one production deployment."""

    __tablename__ = "release"

    id: Optional[int] = Field(default=None, primary_key=True)
    release_id: str = Field(index=True, unique=True)      # UUID or human-readable slug
    workspace_id: Optional[int] = Field(default=None, index=True)
    name: str
    state: str = Field(default="draft")   # draft | pending_approval | approved | rejected | deployed
    target_date: Optional[str] = Field(default=None)       # ISO-8601 date
    notes: Optional[str] = Field(default=None)
    created_by: Optional[str] = Field(default=None)        # API key label
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class ReleaseItem(SQLModel, table=True):
    """One CR / change-ref line within a Release."""

    __tablename__ = "release_item"

    id: Optional[int] = Field(default=None, primary_key=True)
    release_id: str = Field(index=True)        # references Release.release_id
    change_ref: str = Field(index=True)        # e.g. "CR-1234"
    run_ids: Optional[list] = Field(default=None, sa_column=Column(JSON))  # AntCrew run UUIDs
    origin: str = Field(default="antcrew")     # antcrew | vcs_only
    summary: Optional[str] = Field(default=None)
    impact_risk: Optional[str] = Field(default=None)   # low | medium | high
    created_at: datetime = Field(default_factory=_utcnow)


class ReleaseApproval(SQLModel, table=True):
    """One approval decision for a release, linked to the hash-chain audit."""

    __tablename__ = "release_approval"

    id: Optional[int] = Field(default=None, primary_key=True)
    release_id: str = Field(index=True)        # references Release.release_id
    approver_id: str                           # email or user ID
    approver_role: str = Field(default="")    # e.g. "qa_lead", "risk_officer"
    decision: str                              # approved | rejected
    reason: Optional[str] = Field(default=None)
    decided_at: datetime = Field(default_factory=_utcnow)
    row_hash: str = Field(default="")         # SHA-256 hash chain
