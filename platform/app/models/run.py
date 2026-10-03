"""Database models for pipeline runs and their artifacts.

Core run models (Run, Ticket, Event, RunTemplate, RunSchedule, PipelineDef) are defined
here. All other models are imported from their domain modules and re-exported so that
existing ``from app.models.run import XYZ`` imports continue to work unchanged.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Column, Index, UniqueConstraint
from sqlmodel import JSON, Field, SQLModel

from app.core.encryption import EncryptedJSON
from app.models._utils import _utcnow
from app.models.accounting import Invoice, Receipt
from app.models.auth import (
    ApiKey,
    EmailVerification,
    User,
    UserSession,
    WorkspaceInvite,
    WorkspaceJoinRequest,
    WorkspaceMembership,
)
from app.models.eval import CompareRun, EvalRun, EvalSchedule
from app.models.feedback import UserFeedback
from app.models.integrations import TeamSnapshot, TicketDestination
from app.models.memory import RunMemory
from app.models.review import HitlAuditEntry, HitlReview, HitlReviewAssignee
from app.models.security import AuditFinding, SecurityAuditConfig, SecurityAuditRun
from app.models.webhook import WebhookConfig, WebhookDelivery, WebhookEvent

# ── Re-exports from domain modules ────────────────────────────────────────────
from app.models.workspace import (
    BYOKAuditEvent,
    CustomAgentDef,
    LLMProviderKey,
    Workspace,
    WorkspaceContractSchema,
)

# ── Core run models ───────────────────────────────────────────────────────────


class PipelineDef(SQLModel, table=True):
    """User-defined visual pipeline stored as a JSON graph."""

    __tablename__ = "pipeline_def"

    id: Optional[int] = Field(default=None, primary_key=True)
    workspace_id: Optional[int] = Field(default=None, foreign_key="workspace.id", index=True)
    name: str
    description: Optional[str] = Field(default=None)
    is_template: bool = Field(default=False)
    definition: str  # JSON: {nodes: [...], edges: [...]}
    created_at: datetime = Field(default_factory=_utcnow)


class Run(SQLModel, table=True):
    """One pipeline execution."""

    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: str = Field(index=True, unique=True)
    thread_id: str = Field(default="default")
    team: str
    request: str
    status: str = Field(default="running")  # running | success | error | cancelled
    cost_usd: float = Field(default=0.0)
    duration_s: Optional[float] = Field(default=None)
    created_by: Optional[str] = Field(default=None)  # API key label
    workspace_id: Optional[int] = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow)
    finished_at: Optional[datetime] = Field(default=None)
    state: Optional[dict] = Field(default=None, sa_column=Column(EncryptedJSON))
    client_label: Optional[str] = Field(default=None, index=True)  # cost-center / client tag for spend breakdown
    model: Optional[str] = Field(default=None, index=True)         # LLM model used (e.g. "claude-sonnet-4-5"); populated when known
    model_overrides: Optional[dict] = Field(default=None, sa_column=Column(JSON))  # per-agent overrides: {"BackendDevAgent": "groq:llama-3.3-70b"}
    llm_key_mode: Optional[str] = Field(default=None)  # snapshotted from workspace at run creation; use for attribution queries
    tokens_in: int = Field(default=0)   # cumulative input tokens across all agent.end events (migration 055)
    tokens_out: int = Field(default=0)  # cumulative output tokens across all agent.end events (migration 055)
    billed_usd: Optional[float] = Field(default=None)  # amount billed to the client (for margin tracking)


class Sprint(SQLModel, table=True):
    """A named sequence of tickets scoped to a workspace (one run → one sprint)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    sprint_id: str = Field(index=True, unique=True)
    workspace_id: Optional[int] = Field(default=None, index=True)
    name: str
    status: str = Field(default="planning")  # planning | active | done
    backlog_order: int = Field(default=0)    # display order in the backlog view
    default_team: Optional[str] = Field(default=None)  # team used when dispatching tickets in this sprint
    created_at: datetime = Field(default_factory=_utcnow)


class Ticket(SQLModel, table=True):
    """A PM ticket produced by a pipeline run — stable by deterministic ID."""

    id: Optional[int] = Field(default=None, primary_key=True)
    ticket_id: str = Field(index=True)
    run_id: str = Field(index=True)
    title: str
    description: str = Field(default="")
    acceptance_criteria: str = Field(default="")
    dependencies: str = Field(default="")  # JSON-encoded list of ticket_ids
    priority: str = Field(default="medium")
    status: str = Field(default="open")   # open | in_progress | done | blocked
    prd_title: str = Field(default="")
    # Manual-action fields
    ticket_type: str = Field(default="task")          # task | manual_action | bug
    blocking: bool = Field(default=False)             # when True, blocks the run until resolved
    assignee: Optional[str] = Field(default=None)    # email of the human responsible
    # Workspace-scoped display ID (e.g. "PROJ-00001") — set on creation, null for legacy tickets
    workspace_id: Optional[int] = Field(default=None, index=True)
    display_id: Optional[str] = Field(default=None, index=True)
    # Sprint backlog fields
    sprint_id: Optional[str] = Field(default=None, index=True)    # references Sprint.sprint_id
    depends_on: Optional[list] = Field(default=None, sa_column=Column(JSON))  # list of ticket_ids (direct deps only)
    backlog_order: int = Field(default=0)
    implementing_run_id: Optional[str] = Field(default=None, index=True)  # run that implemented this ticket
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class AgentEvent(SQLModel, table=True):
    """Per-agent cost/token row persisted from each agent.end event.

    One row per agent invocation — a run with N agents produces N rows.
    Queryable via GET /runs/{run_id}/agents.
    """

    __tablename__ = "agent_event"

    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: str = Field(index=True)
    agent_name: str
    duration_s: float = Field(default=0.0)
    tokens_in: int = Field(default=0)
    tokens_out: int = Field(default=0)
    cost_usd: float = Field(default=0.0)
    produced_keys: str = Field(default="[]")  # JSON-encoded list[str]
    recorded_at: datetime = Field(default_factory=_utcnow)


class Event(SQLModel, table=True):
    """Raw event emitted by the antcrew event bus."""

    __table_args__ = (Index("ix_event_run_id_ts", "run_id", "timestamp"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: Optional[str] = Field(default=None, index=True)
    thread_id: Optional[str] = Field(default=None)
    event_type: str = Field(index=True)
    payload: dict = Field(default_factory=dict, sa_column=Column(JSON))
    timestamp: float
    recorded_at: datetime = Field(default_factory=_utcnow)


class RunTemplate(SQLModel, table=True):
    """A reusable run configuration saved by the user."""

    __tablename__ = "run_template"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    team: str
    request: str
    max_cost_usd: Optional[float] = Field(default=None)
    hitl: bool = Field(default=False)
    repo_url: Optional[str] = Field(default=None)
    workspace_id: Optional[int] = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow)


class RunPreset(SQLModel, table=True):
    """Named model-override configuration for a team, scoped to a workspace."""

    __tablename__ = "run_preset"

    id: Optional[int] = Field(default=None, primary_key=True)
    workspace_id: int = Field(index=True)
    name: str
    team: str
    model_overrides: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    max_messages: Optional[int] = Field(default=None)  # team-level limit (overrides workspace.max_messages)
    created_by: Optional[str] = None
    created_at: Optional[datetime] = Field(default_factory=_utcnow)


class RunSchedule(SQLModel, table=True):
    """Recurring engine run — fires on a cron expression, scoped to a workspace."""

    __tablename__ = "run_schedule"

    id: Optional[int] = Field(default=None, primary_key=True)
    workspace_id: int = Field(index=True)
    name: str
    goal: str
    model: str = Field(default="claude")
    conditions: Optional[str] = Field(default=None)   # JSON list, NULL = full default set
    full: bool = Field(default=True)
    max_cost_usd: Optional[float] = Field(default=None)
    cron_expr: str                                     # e.g. "0 8 * * 1" (Mon 08:00 UTC)
    enabled: bool = Field(default=True)
    next_run_at: datetime = Field(default_factory=_utcnow)
    last_run_id: Optional[str] = Field(default=None)
    created_by: Optional[str] = Field(default=None)   # API key label
    created_at: datetime = Field(default_factory=_utcnow)


__all__ = [
    # utilities
    "_utcnow",
    # workspace domain
    "Workspace",
    "LLMProviderKey",
    "BYOKAuditEvent",
    "WorkspaceContractSchema",
    "CustomAgentDef",
    # auth domain
    "ApiKey",
    "WorkspaceMembership",
    "User",
    "UserSession",
    "EmailVerification",
    "WorkspaceInvite",
    "WorkspaceJoinRequest",
    # review domain
    "HitlReview",
    "HitlReviewAssignee",
    "HitlAuditEntry",
    # webhook domain
    "WebhookDelivery",
    "WebhookConfig",
    "WebhookEvent",
    # eval domain
    "EvalRun",
    "EvalSchedule",
    "CompareRun",
    # security domain
    "SecurityAuditConfig",
    "SecurityAuditRun",
    "AuditFinding",
    # core run models
    "PipelineDef",
    "Run",
    "Ticket",
    "Event",
    "RunTemplate",
    "RunPreset",
    "RunSchedule",
]
