"""Multi-LLM cost routing — test suite.

CR01  classify_team returns correct tier for known team names
CR02  classify_team falls back to 'standard' for unknown names
CR03  classify_agent returns correct tier for known keyword patterns
CR04  classify_agent falls back to 'standard' for unknown names
CR05  build_routing_overrides returns {} for policy='none'
CR06  build_routing_overrides economy → {"default": cheap_model}
CR07  build_routing_overrides economy with no cheap model → {}
CR08  build_routing_overrides auto → default is team tier, agents get per-tier models
CR09  build_routing_overrides auto skips agent entry when model equals default
CR10  build_routing_overrides auto with empty known_agents → only default key
CR11  GET /workspaces/{id}/routing → 200 with policy=none and platform tier defaults
CR12  PATCH /workspaces/{id}/routing sets policy to 'economy' → 200 reflected in GET
CR13  PATCH /workspaces/{id}/routing sets policy to 'auto' → 200
CR14  PATCH /workspaces/{id}/routing with invalid policy → 422
CR15  PATCH /admin/workspaces/{id} sets cost_routing_policy=economy
CR16  PATCH /admin/workspaces/{id} rejects invalid cost_routing_policy
CR17  PATCH /admin/billing-rates updates tier model strings
CR18  WorkspaceRow in admin list includes cost_routing_policy
"""
from __future__ import annotations

import uuid
import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import WorkspaceContext, get_workspace_context
from app.core.admin_auth import require_platform_admin
from app.main import app
from app.models.workspace import Workspace

from app.services.cost_router import (
    classify_agent,
    classify_team,
    build_routing_overrides,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcnow():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _ws_ctx(workspace_id: int = 1) -> WorkspaceContext:
    return WorkspaceContext(workspace_id=workspace_id, created_by="test", role="admin")


def _admin_stub():
    return {"id": 1, "email": "admin@test.com", "is_platform_admin": True}


async def _make_workspace(session: AsyncSession, **kwargs) -> Workspace:
    ws = Workspace(
        name="test-ws",
        slug=f"test-{uuid.uuid4().hex[:6]}",
        **kwargs,
    )
    session.add(ws)
    await session.flush()
    return ws


_TIERS = {
    "cheap": "groq:llama-3.3-70b-versatile",
    "standard": "claude:claude-sonnet-5",
    "premium": "claude:claude-opus-5",
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _override_auth(session):
    app.dependency_overrides[get_workspace_context] = lambda: _ws_ctx(1)
    app.dependency_overrides[require_platform_admin] = _admin_stub
    yield
    app.dependency_overrides.pop(get_workspace_context, None)
    app.dependency_overrides.pop(require_platform_admin, None)


# ---------------------------------------------------------------------------
# CR01 — classify_team known names
# ---------------------------------------------------------------------------

def test_cr01_classify_team_known():
    assert classify_team("DevTeam") == "standard"
    assert classify_team("LegalReviewTeam") == "premium"
    assert classify_team("WebhookSink") == "cheap"
    assert classify_team("FullStackTeam") == "standard"
    assert classify_team("ContentTeam") == "standard"
    assert classify_team("AuditTeam") == "premium"
    assert classify_team("SecurityTeam") == "premium"
    assert classify_team("CodeMigrationTeam") == "standard"
    assert classify_team("ResearchTeam") == "standard"
    assert classify_team("FeatureTeam") == "standard"


# ---------------------------------------------------------------------------
# CR02 — classify_team unknown falls back to standard
# ---------------------------------------------------------------------------

def test_cr02_classify_team_unknown_fallback():
    assert classify_team("SomeRandomTeam") == "standard"
    assert classify_team("") == "standard"
    assert classify_team("CustomerSupportTeam") == "standard"


# ---------------------------------------------------------------------------
# CR03 — classify_agent keyword matching
# ---------------------------------------------------------------------------

def test_cr03_classify_agent_known():
    assert classify_agent("ValidatorAgent") == "cheap"
    assert classify_agent("FormatterAgent") == "cheap"
    assert classify_agent("ExtractorAgent") == "cheap"
    assert classify_agent("SummarizerAgent") == "cheap"
    assert classify_agent("RouterAgent") == "cheap"
    assert classify_agent("WebhookSink") == "cheap"

    assert classify_agent("ArchitectAgent") == "premium"
    assert classify_agent("PlannerAgent") == "premium"
    assert classify_agent("LegalReviewer") == "premium"
    assert classify_agent("ComplianceChecker") == "premium"
    assert classify_agent("SecurityAnalyzer") == "premium"
    assert classify_agent("AuditorAgent") == "premium"

    assert classify_agent("DeveloperAgent") == "standard"
    assert classify_agent("ReviewerAgent") == "standard"
    assert classify_agent("ResearcherAgent") == "standard"
    assert classify_agent("WriterAgent") == "standard"
    assert classify_agent("TesterAgent") == "standard"
    assert classify_agent("EngineerAgent") == "standard"


# ---------------------------------------------------------------------------
# CR04 — classify_agent unknown falls back to standard
# ---------------------------------------------------------------------------

def test_cr04_classify_agent_unknown_fallback():
    assert classify_agent("SomeWeirdAgent") == "standard"
    assert classify_agent("") == "standard"
    assert classify_agent("CustomerServiceBot") == "standard"


# ---------------------------------------------------------------------------
# CR05 — build_routing_overrides none → {}
# ---------------------------------------------------------------------------

def test_cr05_policy_none_returns_empty():
    result = build_routing_overrides("DevTeam", "none", _TIERS)
    assert result == {}


# ---------------------------------------------------------------------------
# CR06 — build_routing_overrides economy → {"default": cheap_model}
# ---------------------------------------------------------------------------

def test_cr06_policy_economy_default_cheap():
    result = build_routing_overrides("DevTeam", "economy", _TIERS)
    assert result == {"default": "groq:llama-3.3-70b-versatile"}


def test_cr06_policy_economy_ignores_agents():
    result = build_routing_overrides(
        "DevTeam", "economy", _TIERS,
        known_agents=["ArchitectAgent", "ValidatorAgent"]
    )
    assert result == {"default": "groq:llama-3.3-70b-versatile"}


# ---------------------------------------------------------------------------
# CR07 — economy with no cheap model → {}
# ---------------------------------------------------------------------------

def test_cr07_economy_no_cheap_model_returns_empty():
    result = build_routing_overrides("DevTeam", "economy", {"cheap": "", "standard": "s", "premium": "p"})
    assert result == {}


# ---------------------------------------------------------------------------
# CR08 — auto → team-level default + per-agent overrides
# ---------------------------------------------------------------------------

def test_cr08_policy_auto_team_and_agents():
    result = build_routing_overrides(
        "LegalReviewTeam", "auto", _TIERS,
        known_agents=["SummarizerAgent", "ComplianceChecker"]
    )
    # Legal team → premium tier as default
    assert result["default"] == _TIERS["premium"]
    # SummarizerAgent → cheap, differs from premium default
    assert result["SummarizerAgent"] == _TIERS["cheap"]
    # ComplianceChecker → premium, same as default → should NOT appear as separate entry
    assert "ComplianceChecker" not in result


def test_cr08_auto_devteam_standard_default():
    result = build_routing_overrides(
        "DevTeam", "auto", _TIERS,
        known_agents=["ArchitectAgent", "ValidatorAgent"]
    )
    assert result["default"] == _TIERS["standard"]
    assert result["ArchitectAgent"] == _TIERS["premium"]
    assert result["ValidatorAgent"] == _TIERS["cheap"]


# ---------------------------------------------------------------------------
# CR09 — auto skips agent entry when model equals default
# ---------------------------------------------------------------------------

def test_cr09_auto_skips_duplicate_model():
    tiers_same = {
        "cheap": "same-model",
        "standard": "same-model",
        "premium": "same-model",
    }
    result = build_routing_overrides(
        "DevTeam", "auto", tiers_same,
        known_agents=["ValidatorAgent", "ArchitectAgent", "DeveloperAgent"]
    )
    # Default is same-model; all agents resolve to same-model → no per-agent entries
    assert result == {"default": "same-model"}


# ---------------------------------------------------------------------------
# CR10 — auto with empty known_agents → only default
# ---------------------------------------------------------------------------

def test_cr10_auto_no_known_agents_only_default():
    result = build_routing_overrides("DevTeam", "auto", _TIERS, known_agents=None)
    assert list(result.keys()) == ["default"]

    result2 = build_routing_overrides("DevTeam", "auto", _TIERS, known_agents=[])
    assert list(result2.keys()) == ["default"]


# ---------------------------------------------------------------------------
# CR11 — GET /workspaces/{id}/routing → 200 with defaults
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_cr11_get_routing_default(client, session):
    ws = await _make_workspace(session)
    app.dependency_overrides[get_workspace_context] = lambda: _ws_ctx(ws.id)

    r = await client.get(f"/workspaces/{ws.id}/routing")
    assert r.status_code == 200
    data = r.json()
    assert data["workspace_id"] == ws.id
    assert data["cost_routing_policy"] == "none"
    assert "tier_cheap_model" in data
    assert "tier_standard_model" in data
    assert "tier_premium_model" in data


# ---------------------------------------------------------------------------
# CR12 — PATCH routing sets economy
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_cr12_patch_routing_economy(client, session):
    ws = await _make_workspace(session)
    app.dependency_overrides[get_workspace_context] = lambda: _ws_ctx(ws.id)

    r = await client.patch(
        f"/workspaces/{ws.id}/routing",
        json={"cost_routing_policy": "economy"},
    )
    assert r.status_code == 200
    assert r.json()["cost_routing_policy"] == "economy"

    r2 = await client.get(f"/workspaces/{ws.id}/routing")
    assert r2.json()["cost_routing_policy"] == "economy"


# ---------------------------------------------------------------------------
# CR13 — PATCH routing sets auto
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_cr13_patch_routing_auto(client, session):
    ws = await _make_workspace(session)
    app.dependency_overrides[get_workspace_context] = lambda: _ws_ctx(ws.id)

    r = await client.patch(
        f"/workspaces/{ws.id}/routing",
        json={"cost_routing_policy": "auto"},
    )
    assert r.status_code == 200
    assert r.json()["cost_routing_policy"] == "auto"


# ---------------------------------------------------------------------------
# CR14 — PATCH routing with invalid policy → 422
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_cr14_patch_routing_invalid_policy(client, session):
    ws = await _make_workspace(session)
    app.dependency_overrides[get_workspace_context] = lambda: _ws_ctx(ws.id)

    r = await client.patch(
        f"/workspaces/{ws.id}/routing",
        json={"cost_routing_policy": "turbo"},
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# CR15 — PATCH /admin/workspaces/{id} sets cost_routing_policy
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_cr15_admin_patch_workspace_routing_policy(client, session):
    ws = await _make_workspace(session)

    r = await client.patch(
        f"/admin/workspaces/{ws.id}",
        json={"cost_routing_policy": "economy"},
    )
    assert r.status_code == 200
    assert r.json()["cost_routing_policy"] == "economy"


# ---------------------------------------------------------------------------
# CR16 — PATCH /admin/workspaces/{id} rejects invalid policy
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_cr16_admin_patch_workspace_invalid_policy(client, session):
    ws = await _make_workspace(session)

    r = await client.patch(
        f"/admin/workspaces/{ws.id}",
        json={"cost_routing_policy": "ultrafast"},
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# CR17 — PATCH /admin/billing-rates updates tier models
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_cr17_admin_billing_rates_tier_models(client, session):
    r = await client.patch(
        "/admin/billing-rates",
        json={
            "tier_cheap_model": "groq:llama-3.1-8b",
            "tier_standard_model": "claude:claude-sonnet-4",
            "tier_premium_model": "claude:claude-opus-4",
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["tier_cheap_model"] == "groq:llama-3.1-8b"
    assert data["tier_standard_model"] == "claude:claude-sonnet-4"
    assert data["tier_premium_model"] == "claude:claude-opus-4"

    r2 = await client.get("/admin/billing-rates")
    assert r2.status_code == 200
    d2 = r2.json()
    assert d2["tier_cheap_model"] == "groq:llama-3.1-8b"
    assert d2["tier_standard_model"] == "claude:claude-sonnet-4"
    assert d2["tier_premium_model"] == "claude:claude-opus-4"


# ---------------------------------------------------------------------------
# CR18 — WorkspaceRow in admin list includes cost_routing_policy
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_cr18_admin_list_includes_routing_policy(client, session):
    ws = await _make_workspace(session)
    ws.cost_routing_policy = "auto"
    session.add(ws)
    await session.flush()

    r = await client.get("/admin/workspaces")
    assert r.status_code == 200
    rows = r.json()
    target = next((row for row in rows if row["id"] == ws.id), None)
    assert target is not None
    assert target["cost_routing_policy"] == "auto"
