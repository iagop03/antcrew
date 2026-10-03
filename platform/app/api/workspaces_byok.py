"""BYOK — per-workspace LLM API key management."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, field_validator, model_validator
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.workspaces import WorkspacePublic
from app.core.auth import WorkspaceContext, require_api_key, require_role, ws_accessible
from app.core.byok import BYOK_ANOMALY_THRESHOLD_DEFAULT, log_byok_event
from app.core.database import get_session
from app.core.exceptions import WorkspaceNotFoundError
from app.models.run import LLMProviderKey, Workspace
from app.models.workspace import BYOKAuditEvent

router = APIRouter(
    prefix="/workspaces",
    tags=["workspaces"],
    dependencies=[Depends(require_api_key)],
)

_BYOK_PROVIDERS = frozenset({
    "anthropic", "openai", "groq", "gemini", "ollama", "moonshot",
    "deepseek", "mistral", "xai", "together", "fireworks", "cerebras",
    "lmstudio", "vllm",
})
_KEYLESS_PROVIDERS = frozenset({"ollama", "lmstudio", "vllm"})


def _client_ip(request: Request) -> Optional[str]:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


class SetLLMModeRequest(BaseModel):
    mode: str  # "managed" | "byok"
    byok_managed_fallback: Optional[bool] = None  # when byok: fall back to platform key for unconfigured models

    @field_validator("mode")
    @classmethod
    def mode_valid(cls, v: str) -> str:
        if v not in ("managed", "byok"):
            raise ValueError("mode must be 'managed' or 'byok'")
        return v


class StoreLLMKeyRequest(BaseModel):
    provider: str
    api_key: str = ""
    base_url: Optional[str] = None
    confirm_overwrite: bool = False

    @field_validator("provider")
    @classmethod
    def provider_valid(cls, v: str) -> str:
        if v not in _BYOK_PROVIDERS:
            raise ValueError(f"provider must be one of: {', '.join(sorted(_BYOK_PROVIDERS))}")
        return v

    @field_validator("api_key")
    @classmethod
    def key_valid(cls, v: str) -> str:
        return v.strip()

    @model_validator(mode="after")
    def key_required_unless_keyless(self) -> "StoreLLMKeyRequest":
        if self.provider not in _KEYLESS_PROVIDERS and not self.api_key:
            raise ValueError(f"api_key is required for provider '{self.provider}'")
        return self


class LLMKeyOut(BaseModel):
    provider: str
    configured: bool = True
    base_url: Optional[str] = None
    created_at: datetime
    last_used_at: Optional[datetime] = None


class BYOKAuditEventOut(BaseModel):
    id: int
    workspace_id: int
    provider: str
    event_type: str
    actor_key_id: Optional[int] = None
    ip_address: Optional[str] = None
    note: Optional[str] = None
    created_at: datetime


class BYOKProviderStatus(BaseModel):
    provider: str
    last_used_at: Optional[datetime]
    use_count_24h: int
    anomaly_threshold: int
    is_anomalous: bool


@router.patch("/{workspace_id}/llm-mode", response_model=WorkspacePublic)
async def set_llm_mode(
    workspace_id: int,
    body: SetLLMModeRequest,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(require_role("admin")),
) -> WorkspacePublic:
    """Switch a workspace between managed (platform key) and byok (customer key) modes.

    Cannot switch to 'byok' unless at least one LLM key is already stored.
    Switching back to 'managed' is always allowed (stored keys are preserved).
    """
    if not ws_accessible(workspace_id, ctx):
        raise HTTPException(403, "This workspace is not accessible with the current API key")
    ws = (await session.exec(select(Workspace).where(Workspace.id == workspace_id))).first()
    if not ws:
        raise WorkspaceNotFoundError(workspace_id)

    if body.mode == "byok":
        existing = (await session.exec(
            select(LLMProviderKey).where(LLMProviderKey.workspace_id == workspace_id).limit(1)
        )).first()
        if not existing:
            raise HTTPException(
                422,
                "Cannot switch to BYOK mode: no LLM keys configured. "
                "Store at least one key via POST /workspaces/{id}/llm-keys first."
            )

    ws.llm_key_mode = body.mode
    if body.byok_managed_fallback is not None:
        ws.byok_managed_fallback = body.byok_managed_fallback
    session.add(ws)
    await session.commit()
    await session.refresh(ws)
    return WorkspacePublic.model_validate(ws)


@router.get("/{workspace_id}/llm-keys", response_model=list[LLMKeyOut])
async def list_llm_keys(
    workspace_id: int,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(require_role("admin")),
) -> list[LLMKeyOut]:
    """List configured LLM providers for a workspace. Never returns plaintext keys."""
    if not ws_accessible(workspace_id, ctx):
        raise HTTPException(403, "This workspace is not accessible with the current API key")
    if not (await session.exec(select(Workspace).where(Workspace.id == workspace_id))).first():
        raise WorkspaceNotFoundError(workspace_id)
    rows = (await session.exec(
        select(LLMProviderKey).where(LLMProviderKey.workspace_id == workspace_id)
    )).all()
    return [
        LLMKeyOut(
            provider=r.provider,
            base_url=getattr(r, "base_url", None),
            created_at=r.created_at,
            last_used_at=getattr(r, "last_used_at", None),
        )
        for r in rows
    ]


@router.post("/{workspace_id}/llm-keys", status_code=201)
async def store_llm_key(
    workspace_id: int,
    body: StoreLLMKeyRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(require_role("admin")),
) -> dict:
    """Store or rotate a BYOK LLM API key for a workspace.

    The key is encrypted at rest using Fernet (BYOK_ENCRYPTION_KEY env var).
    Overwriting an existing key for the same provider requires confirm_overwrite=true.
    """
    if not ws_accessible(workspace_id, ctx):
        raise HTTPException(403, "This workspace is not accessible with the current API key")
    if not (await session.exec(select(Workspace).where(Workspace.id == workspace_id))).first():
        raise WorkspaceNotFoundError(workspace_id)

    existing = (await session.exec(
        select(LLMProviderKey)
        .where(LLMProviderKey.workspace_id == workspace_id)
        .where(LLMProviderKey.provider == body.provider)
    )).first()

    if existing and not body.confirm_overwrite:
        raise HTTPException(
            409,
            f"A key for provider '{body.provider}' already exists. "
            "Set confirm_overwrite=true to replace it."
        )

    from app.core.byok import _encrypt
    encrypted = _encrypt(body.api_key) if body.api_key else ""

    actor = ctx.created_by or "unknown"
    note = f"actor={actor}" + (" overwrite=true" if existing else "")
    if existing:
        existing.key_enc = encrypted
        existing.base_url = body.base_url
        from app.models._utils import _utcnow
        existing.created_at = _utcnow()
        session.add(existing)
    else:
        session.add(LLMProviderKey(
            workspace_id=workspace_id,
            provider=body.provider,
            key_enc=encrypted,
            base_url=body.base_url,
        ))

    await log_byok_event(
        session,
        workspace_id=workspace_id,
        provider=body.provider,
        event_type="store",
        actor_key_id=None,
        ip_address=_client_ip(request),
        note=note,
    )

    await session.commit()
    return {"workspace_id": workspace_id, "provider": body.provider, "configured": True}


@router.delete("/{workspace_id}/llm-keys/{provider}", status_code=204)
async def delete_llm_key(
    workspace_id: int,
    provider: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(require_role("admin")),
):
    """Remove a BYOK key. If it was the last key, resets llm_key_mode to 'managed'."""
    if not ws_accessible(workspace_id, ctx):
        raise HTTPException(403, "This workspace is not accessible with the current API key")
    if provider not in _BYOK_PROVIDERS:
        raise HTTPException(422, f"provider must be one of: {', '.join(sorted(_BYOK_PROVIDERS))}")

    row = (await session.exec(
        select(LLMProviderKey)
        .where(LLMProviderKey.workspace_id == workspace_id)
        .where(LLMProviderKey.provider == provider)
    )).first()
    if not row:
        raise HTTPException(404, f"No key for provider '{provider}' in workspace {workspace_id}")

    await session.delete(row)

    remaining = (await session.exec(
        select(LLMProviderKey).where(LLMProviderKey.workspace_id == workspace_id)
    )).first()
    if not remaining:
        ws = (await session.exec(select(Workspace).where(Workspace.id == workspace_id))).first()
        if ws and ws.llm_key_mode == "byok":
            ws.llm_key_mode = "managed"
            session.add(ws)

    await log_byok_event(
        session,
        workspace_id=workspace_id,
        provider=provider,
        event_type="delete",
        actor_key_id=None,
        ip_address=_client_ip(request),
        note=f"actor={ctx.created_by or 'unknown'}",
    )

    await session.commit()


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------

@router.get("/{workspace_id}/byok-audit", response_model=list[BYOKAuditEventOut])
async def list_byok_audit(
    workspace_id: int,
    provider: Optional[str] = Query(default=None),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(require_role("admin")),
) -> list[BYOKAuditEventOut]:
    """List BYOK key lifecycle events (store / rotate / delete) for a workspace.

    Results are ordered newest-first. Filter by provider to scope to one key.
    """
    if not ws_accessible(workspace_id, ctx):
        raise HTTPException(403, "This workspace is not accessible with the current API key")
    if not (await session.exec(select(Workspace).where(Workspace.id == workspace_id))).first():
        raise WorkspaceNotFoundError(workspace_id)

    q = select(BYOKAuditEvent).where(BYOKAuditEvent.workspace_id == workspace_id)
    if provider:
        q = q.where(BYOKAuditEvent.provider == provider)
    q = q.order_by(BYOKAuditEvent.created_at.desc()).offset(offset).limit(limit)

    rows = (await session.exec(q)).all()
    return [BYOKAuditEventOut.model_validate(r, from_attributes=True) for r in rows]


# ---------------------------------------------------------------------------
# Anomaly status
# ---------------------------------------------------------------------------

@router.get("/{workspace_id}/byok-status", response_model=list[BYOKProviderStatus])
async def byok_status(
    workspace_id: int,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(require_role("admin")),
) -> list[BYOKProviderStatus]:
    """Return per-provider usage counters and anomaly flags for the last 24 hours.

    is_anomalous=true when use_count_24h exceeds the provider's anomaly_threshold
    (default: BYOK_ANOMALY_THRESHOLD env var, or 200).
    """
    if not ws_accessible(workspace_id, ctx):
        raise HTTPException(403, "This workspace is not accessible with the current API key")
    if not (await session.exec(select(Workspace).where(Workspace.id == workspace_id))).first():
        raise WorkspaceNotFoundError(workspace_id)

    rows = (await session.exec(
        select(LLMProviderKey).where(LLMProviderKey.workspace_id == workspace_id)
    )).all()

    result = []
    for r in rows:
        threshold = r.anomaly_threshold if r.anomaly_threshold is not None else BYOK_ANOMALY_THRESHOLD_DEFAULT
        count = r.use_count_24h or 0
        result.append(BYOKProviderStatus(
            provider=r.provider,
            last_used_at=getattr(r, "last_used_at", None),
            use_count_24h=count,
            anomaly_threshold=threshold,
            is_anomalous=count >= threshold,
        ))

    return result
