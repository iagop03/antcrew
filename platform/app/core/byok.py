"""BYOK (Bring Your Own Key) — per-workspace LLM API key management.

Fernet encryption mirrors the pattern in slack_hitl.py, but uses a separate
env var (BYOK_ENCRYPTION_KEY) so the two secrets are independent.

Cost multipliers (applied in listener.py at pipeline.end):
  MANAGED_COST_MULTIPLIER: client pays raw LLM cost × 3.0 (platform provides the key)
  BYOK_SERVICE_MULTIPLIER: client pays raw LLM cost × 0.4 (service fee only)
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

BYOK_ANOMALY_THRESHOLD_DEFAULT: int = int(os.environ.get("BYOK_ANOMALY_THRESHOLD", "200"))

log = logging.getLogger(__name__)

MANAGED_COST_MULTIPLIER: float = 3.0
BYOK_SERVICE_MULTIPLIER: float = 0.4
PROXY_SERVICE_MULTIPLIER: float = 0.7   # proxy: customer holds key; platform charges service fee
TRIAL_MULTIPLIER: float = 1.0  # trial runs at raw cost (no margin); change via env if needed

# Credit granted to new workspaces in trial mode. Configurable at runtime — no redeploy needed.
TRIAL_CREDIT_USD: float = float(os.environ.get("TRIAL_CREDIT_USD", "5.0"))

_VALID_PROVIDERS = frozenset({
    "anthropic", "openai", "groq", "gemini", "ollama", "moonshot",
    "deepseek", "mistral", "xai", "together", "fireworks", "cerebras",
    "lmstudio", "vllm",
})

_IS_DEV = (
    os.environ.get("APP_ENV", "production").lower() in ("dev", "development", "local")
    or os.environ.get("ANTCREW_TESTING") == "1"
)


@dataclass
class BYOKKey:
    """Decrypted BYOK credentials for a workspace provider."""
    key: Optional[str]      # API key; None for keyless providers (ollama)
    base_url: Optional[str]  # custom endpoint URL; None unless provider needs it


def _provider_for_model(model_str: str) -> str:
    """Infer the BYOK provider name from a model string."""
    s = model_str.strip().lower()
    if s.startswith("gpt") or s.startswith("o1") or s.startswith("o3") or s.startswith("openai:"):
        return "openai"
    if s.startswith("groq:"):
        return "groq"
    if s.startswith("gemini"):
        return "gemini"
    if s.startswith("ollama:"):
        return "ollama"
    if s.startswith("moonshot:"):
        return "moonshot"
    if s.startswith("deepseek:"):
        return "deepseek"
    if s.startswith("mistral:"):
        return "mistral"
    if s.startswith("xai:"):
        return "xai"
    if s.startswith("together:"):
        return "together"
    if s.startswith("fireworks:"):
        return "fireworks"
    if s.startswith("cerebras:"):
        return "cerebras"
    if s.startswith("lmstudio:"):
        return "lmstudio"
    if s.startswith("vllm:"):
        return "vllm"
    return "anthropic"


def _encrypt(key: str) -> str:
    enc_key = os.environ.get("BYOK_ENCRYPTION_KEY", "")
    if not enc_key:
        if not _IS_DEV:
            raise RuntimeError(
                "BYOK_ENCRYPTION_KEY is required in production. "
                "Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
            )
        log.warning("byok: BYOK_ENCRYPTION_KEY not set — storing key as plain text (dev only)")
        return key
    try:
        from cryptography.fernet import Fernet
        return Fernet(enc_key.encode()).encrypt(key.encode()).decode()
    except Exception as exc:
        raise RuntimeError(f"BYOK key encryption failed: {exc}") from exc


def _decrypt(key_enc: str) -> str:
    """Decrypt a stored BYOK key.

    Tries BYOK_ENCRYPTION_KEY first.  If decryption fails and BYOK_ENCRYPTION_KEY_OLD
    is set, tries the old key — this is the zero-downtime rotation window where both
    keys are live simultaneously.  See admin POST /admin/byok/rotate-key for the full
    rotation procedure.
    """
    primary = os.environ.get("BYOK_ENCRYPTION_KEY", "")
    fallback = os.environ.get("BYOK_ENCRYPTION_KEY_OLD", "")
    if not primary:
        if not _IS_DEV:
            raise RuntimeError(
                "BYOK_ENCRYPTION_KEY is required in production — cannot decrypt stored BYOK keys."
            )
        return key_enc  # dev only: key stored as plain text
    try:
        from cryptography.fernet import Fernet
        return Fernet(primary.encode()).decrypt(key_enc.encode()).decode()
    except Exception as primary_exc:
        if fallback:
            try:
                from cryptography.fernet import Fernet as _F
                decrypted = _F(fallback.encode()).decrypt(key_enc.encode()).decode()
                log.debug("byok: decrypted with BYOK_ENCRYPTION_KEY_OLD (rotation in progress)")
                return decrypted
            except Exception:
                pass  # fall through to raise primary error
        raise RuntimeError(
            "BYOK key decryption failed — the stored value may pre-date encryption. "
            "Re-enter the BYOK key for this workspace to re-encrypt it. "
            f"Detail: {primary_exc}"
        ) from primary_exc


async def get_workspace_llm_key(
    session,
    workspace_id: int,
    provider: str,
) -> Optional[BYOKKey]:
    """Return decrypted BYOK credentials for a workspace provider, or None if not configured.

    Updates the rolling 24 h usage counter on each call for anomaly detection.
    The update is best-effort: if the caller's session is read-only or rolls back,
    the counter simply won't increment for that call.
    """
    from sqlmodel import select

    from app.models.workspace import LLMProviderKey

    row = (await session.exec(
        select(LLMProviderKey)
        .where(LLMProviderKey.workspace_id == workspace_id)
        .where(LLMProviderKey.provider == provider)
    )).first()
    if row is None:
        return None

    now = datetime.now(timezone.utc)
    window_start = row.use_window_start
    if window_start is None or (now - window_start).total_seconds() > 86400:
        row.use_count_24h = 1
        row.use_window_start = now
    else:
        row.use_count_24h = (row.use_count_24h or 0) + 1
    row.last_used_at = now
    try:
        session.add(row)
    except Exception:
        pass  # read-only session; counter update is best-effort

    raw_key = _decrypt(row.key_enc) if row.key_enc else None
    return BYOKKey(key=raw_key or None, base_url=getattr(row, "base_url", None))


async def log_byok_event(
    session,
    workspace_id: int,
    provider: str,
    event_type: str,
    actor_key_id: Optional[int] = None,
    ip_address: Optional[str] = None,
    note: Optional[str] = None,
) -> None:
    """Append an immutable lifecycle event to byok_audit_event.

    event_type: "store" | "rotate" | "delete"
    """
    from app.models.workspace import BYOKAuditEvent
    session.add(BYOKAuditEvent(
        workspace_id=workspace_id,
        provider=provider,
        event_type=event_type,
        actor_key_id=actor_key_id,
        ip_address=ip_address,
        note=note,
    ))


async def get_workspace_llm_key_for_model(
    session,
    workspace_id: int,
    model_str: str,
) -> Optional[BYOKKey]:
    """Infer provider from model string and return BYOK credentials, or None."""
    provider = _provider_for_model(model_str)
    return await get_workspace_llm_key(session, workspace_id, provider)


def get_cost_multiplier(
    llm_key_mode: str,
    is_trial: bool = False,
    multiplier_override: Optional[float] = None,
    multiplier_locked: bool = False,
    campaign_multiplier: Optional[float] = None,
    managed_rate: Optional[float] = None,
    byok_rate: Optional[float] = None,
    proxy_rate: Optional[float] = None,
) -> float:
    """Return the billing multiplier for a workspace.

    Priority (highest to lowest):
      1. multiplier_override  — set by admin per workspace; applies even in trial
      2. campaign_multiplier  — discount factor applied on top of the mode base rate
      3. is_trial             — flat TRIAL_MULTIPLIER
      4. default from llm_key_mode (uses platform-configured rates when provided)
    """
    if multiplier_override is not None:
        return multiplier_override
    if is_trial:
        base = TRIAL_MULTIPLIER
    elif llm_key_mode == "byok":
        base = byok_rate if byok_rate is not None else BYOK_SERVICE_MULTIPLIER
    elif llm_key_mode == "proxy":
        base = proxy_rate if proxy_rate is not None else PROXY_SERVICE_MULTIPLIER
    else:
        base = managed_rate if managed_rate is not None else MANAGED_COST_MULTIPLIER
    if campaign_multiplier is not None and not multiplier_locked:
        return round(base * campaign_multiplier, 6)
    return base
