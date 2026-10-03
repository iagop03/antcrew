"""Workspace, BYOK key, contract schema, and custom-agent models."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import UniqueConstraint
from sqlmodel import JSON, Column, Field, SQLModel

from app.models._utils import _utcnow


class Workspace(SQLModel, table=True):
    """Isolated project scope for multi-team deployments."""

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    slug: str = Field(unique=True, index=True)
    max_cost_usd: Optional[float] = Field(default=None)
    total_cost_usd: float = Field(default=0.0)  # cached total; updated after each run via SQL SUM
    default_repo_url: Optional[str] = Field(default=None)
    slack_webhook_url: Optional[str] = Field(default=None)   # per-workspace HITL incoming webhook URL
    slack_channel_id: Optional[str] = Field(default=None)    # Slack channel ID for interactive HITL
    slack_bot_token_enc: Optional[str] = Field(default=None) # encrypted xoxb-… (Fernet, key=SLACK_TOKEN_ENCRYPTION_KEY)
    slack_app_token_enc: Optional[str] = Field(default=None) # encrypted xapp-… for Socket Mode
    hitl_default: bool = Field(default=False)
    hitl_timeout_s: Optional[float] = Field(default=None)  # per-workspace HITL timeout (overrides env HITL_TIMEOUT_S)
    stripe_customer_id: Optional[str] = Field(default=None, index=True)  # cus_...
    stripe_subscription_id: Optional[str] = Field(default=None)           # sub_...
    subscription_status: Optional[str] = Field(default=None)              # active | trialing | past_due | canceled | unpaid
    billing_provider: str = Field(default="mor")                          # mor | stripe
    mor_customer_id: Optional[str] = Field(default=None)                  # Lemon Squeezy customer ID
    mor_subscription_id: Optional[str] = Field(default=None)              # Lemon Squeezy subscription ID
    llm_key_mode: str = Field(default="managed")  # managed | byok | proxy
    byok_managed_fallback: bool = Field(default=False)  # fall back to platform key when no BYOK key for a model
    proxy_url: Optional[str] = Field(default=None)          # keybridge base URL (e.g. https://keybridge.example.com)
    proxy_token_enc: Optional[str] = Field(default=None)    # Fernet-encrypted UUID token sent to the proxy
    is_trial: bool = Field(default=True)  # workspace is on the free-trial credit; costs at TRIAL_MULTIPLIER
    cost_multiplier_override: Optional[float] = Field(default=None)  # NULL = use default from llm_key_mode
    multiplier_locked: bool = Field(default=False)  # if True, active campaigns do not apply
    # Base rates snapshotted from PlatformConfig at workspace creation — rate changes don't affect existing workspaces
    base_managed_mult: Optional[float] = Field(default=None)
    base_byok_mult: Optional[float] = Field(default=None)
    base_proxy_mult: Optional[float] = Field(default=None)
    owner_user_id: Optional[int] = Field(default=None, index=True)  # user.id of the registering user
    ticket_prefix: str = Field(default="TKT")   # e.g. "PROJ" → ticket display IDs are PROJ-00001
    ticket_counter: int = Field(default=0)       # incremented atomically on each new ticket
    # Per-agent model defaults: {"default": "deepseek:deepseek-chat", "BackendDevAgent": "claude:claude-sonnet-5"}
    # "default" key applies to any agent not explicitly listed; overridden by run-level model_overrides.
    agent_models: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    data_retention_days: Optional[int] = Field(default=None)  # if set, purge Runs older than this many days
    is_blocked: bool = Field(default=False)  # admin-set fraud/abuse block; rejects all new runs immediately
    max_messages: Optional[int] = Field(default=None)  # max TeamState messages kept per run (overrideable per team via RunPreset)
    default_team: Optional[str] = Field(default=None)  # pre-selected team for new runs (e.g. "FullStackTeam")
    cli_working_dir: Optional[str] = Field(default=None)  # working directory passed to remote-gateway CLI drivers (claude-code, gemini, codex); derived from workspace_id when None

    # ── Documentation S3 config ───────────────────────────────────────────────
    docs_s3_bucket: Optional[str] = Field(default=None)              # S3 bucket name
    docs_s3_prefix: Optional[str] = Field(default=None)              # optional key prefix (e.g. "docs/")
    docs_s3_region: Optional[str] = Field(default=None)              # AWS region (default: us-east-1)
    docs_s3_access_key_enc: Optional[str] = Field(default=None)      # Fernet-encrypted AWS access key ID
    docs_s3_secret_key_enc: Optional[str] = Field(default=None)      # Fernet-encrypted AWS secret access key
    docs_schema_yaml: Optional[str] = Field(default=None)            # raw schema.yaml content

    # ── Cost routing ─────────────────────────────────────────────────────────
    # none   — no automatic routing; models come from agent_models / run-level overrides
    # economy — all agents routed to the platform's cheap tier model
    # auto   — agents classified by name/team pattern; routed to cheap/standard/premium tier
    cost_routing_policy: str = Field(default="none")

    # ── Compliance Pack ───────────────────────────────────────────────────────
    compliance_pack_enabled: bool = Field(default=False)           # admin-toggled; enables /compliance/* endpoints
    compliance_pack_price_monthly: Optional[float] = Field(default=None)  # per-workspace override (NULL = use PlatformConfig default)
    compliance_pack_price_annual: Optional[float] = Field(default=None)   # per-workspace override

    # ── Perfil fiscal / facturación ───────────────────────────────────────────
    billing_entity_type: Optional[str] = Field(default=None)   # empresa | autonomo | particular
    billing_razon_social: Optional[str] = Field(default=None)  # razón social o nombre completo
    billing_nif: Optional[str] = Field(default=None)           # NIF / CIF / NIE / DNI
    billing_address: Optional[str] = Field(default=None)       # calle y número
    billing_postal_code: Optional[str] = Field(default=None)
    billing_city: Optional[str] = Field(default=None)
    billing_country: str = Field(default="ES")                 # ISO 3166-1 alpha-2
    billing_email: Optional[str] = Field(default=None)
    billing_phone: Optional[str] = Field(default=None)

    created_at: datetime = Field(default_factory=_utcnow)


class LLMProviderKey(SQLModel, table=True):
    """Per-workspace, per-provider LLM API key for BYOK mode.

    key_enc is Fernet-encrypted with BYOK_ENCRYPTION_KEY, or plaintext in dev mode.
    Unique per (workspace_id, provider) — upsert by deleting and re-inserting.
    """

    __tablename__ = "llm_provider_key"
    __table_args__ = (UniqueConstraint("workspace_id", "provider", name="uq_llm_key_ws_provider"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    workspace_id: int = Field(index=True)
    provider: str  # anthropic | openai | groq | gemini | ollama | moonshot
    key_enc: str   # Fernet-encrypted or plaintext (dev mode without BYOK_ENCRYPTION_KEY); empty for keyless providers
    base_url: Optional[str] = Field(default=None)  # required for ollama / custom OpenAI-compat endpoints
    created_at: datetime = Field(default_factory=_utcnow)

    # ── Usage tracking (anomaly detection) ────────────────────────────────────
    last_used_at: Optional[datetime] = Field(default=None)
    use_count_24h: int = Field(default=0)        # rolling 24 h counter; resets when window expires
    use_window_start: Optional[datetime] = Field(default=None)  # start of current window
    anomaly_threshold: Optional[int] = Field(default=None)  # None = platform default (200/24h)


class BYOKAuditEvent(SQLModel, table=True):
    """Immutable log of key lifecycle events (store / rotate / delete) for a workspace provider.

    Does NOT log individual decrypt calls (too verbose); use byok-status for usage counters.
    """

    __tablename__ = "byok_audit_event"

    id: Optional[int] = Field(default=None, primary_key=True)
    workspace_id: int = Field(index=True)
    provider: str
    event_type: str  # "store" | "rotate" | "delete"
    actor_key_id: Optional[int] = Field(default=None)  # ApiKey.id; None for platform-admin ops
    ip_address: Optional[str] = Field(default=None, max_length=45)
    note: Optional[str] = Field(default=None)  # e.g. "overwrite=true", "rotation batch N keys"
    created_at: datetime = Field(default_factory=_utcnow)


class WorkspaceContractSchema(SQLModel, table=True):
    """Per-workspace JSON Schema for the custom_fields extension point of an artifact contract.

    Stores a JSON Schema that describes what keys are expected in the PRD.custom_fields
    (or any other extendable contract) for a specific workspace.  Purely informational
    in Phase 1 — operators ignore custom_fields; the schema is used for documentation
    and future prompt-injection.

    contract_name must match an entry in EXTENDABLE_CONTRACTS (e.g. "PRD").
    json_schema is any valid JSON Schema object.
    """

    __tablename__ = "workspace_contract_schema"
    __table_args__ = (
        UniqueConstraint("workspace_id", "contract_name", name="uq_contract_schema_ws_contract"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    workspace_id: int = Field(foreign_key="workspace.id", index=True)
    contract_name: str  # e.g. "PRD"
    json_schema: dict = Field(default_factory=dict, sa_column=Column(JSON))
    description: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class CustomAgentDef(SQLModel, table=True):
    """User-defined agent backed by TemplateAgent — scoped to workspace.

    agent_type is stable (never reused after delete) and matches the ``type``
    field stored on pipeline nodes — e.g. "custom_3".  The system_prompt is
    passed directly to TemplateAgent at runtime via node.agent_cfg.
    """

    __tablename__ = "custom_agent_def"
    __table_args__ = (
        UniqueConstraint("workspace_id", "agent_type", name="uq_custom_agent_ws_type"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    workspace_id: int = Field(index=True)
    agent_type: str = Field(index=True)   # e.g. "custom_3"
    label: str
    color: str = Field(default="#7c3aed")
    system_prompt: str
    role_description: Optional[str] = Field(default=None)
    phase: str = Field(default="build")
    glyph: str = Field(default="✦")
    created_at: datetime = Field(default_factory=_utcnow)
