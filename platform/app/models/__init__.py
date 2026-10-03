"""Model package — re-exports all SQLModel table classes for convenience.

``from app.models import Run`` works in addition to
``from app.models.run import Run``.
"""
from app.models.run import (
    # auth domain
    ApiKey,
    AuditFinding,
    CompareRun,
    CustomAgentDef,
    EmailVerification,
    # eval domain
    EvalRun,
    EvalSchedule,
    Event,
    HitlAuditEntry,
    # review domain
    HitlReview,
    HitlReviewAssignee,
    Invoice,
    LLMProviderKey,
    # core run models
    PipelineDef,
    # accounting domain
    Receipt,
    Run,
    RunSchedule,
    RunTemplate,
    # security domain
    SecurityAuditConfig,
    SecurityAuditRun,
    Ticket,
    User,
    UserSession,
    WebhookConfig,
    # webhook domain
    WebhookDelivery,
    WebhookEvent,
    # workspace domain
    Workspace,
    WorkspaceContractSchema,
    WorkspaceInvite,
    WorkspaceJoinRequest,
    WorkspaceMembership,
    # utilities
    _utcnow,
)

__all__ = [
    "_utcnow",
    "Workspace",
    "LLMProviderKey",
    "WorkspaceContractSchema",
    "CustomAgentDef",
    "ApiKey",
    "WorkspaceMembership",
    "User",
    "UserSession",
    "EmailVerification",
    "WorkspaceInvite",
    "WorkspaceJoinRequest",
    "HitlReview",
    "HitlReviewAssignee",
    "HitlAuditEntry",
    "WebhookDelivery",
    "WebhookConfig",
    "WebhookEvent",
    "EvalRun",
    "EvalSchedule",
    "CompareRun",
    "SecurityAuditConfig",
    "SecurityAuditRun",
    "AuditFinding",
    "PipelineDef",
    "Run",
    "Ticket",
    "Event",
    "RunTemplate",
    "RunSchedule",
    "Receipt",
    "Invoice",
]
