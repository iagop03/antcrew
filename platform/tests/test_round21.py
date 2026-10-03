"""Round 21 tests — PM integrations, cost estimation, team governance history.

I1   GET /integrations/ → 200 empty list (no destinations configured)
I2   POST /integrations/ → 201 creates github destination, credentials masked in response
I3   POST /integrations/ → 422 for invalid provider
I4   POST /integrations/ → 422 when required config keys are missing
I5   GET /integrations/ → 200 lists all destinations with masked credentials
I6   PATCH /integrations/{id} → 200 updates label and enabled
I7   DELETE /integrations/{id} → 204 removes destination
I8   POST /integrations/{id}/test → 502 when provider rejects (adapter returns None)

E1   GET /runs/estimate?team=DevTeam → based_on_runs=0, null fields when no history
E2   GET /runs/estimate?team=DevTeam → returns correct percentiles from history
E3   GET /runs/estimate?team=DevTeam → only counts successful runs with cost > 0

T1   GET /teams/{team}/history → 200 empty snapshots when none exist
T2   GET /teams/{team}/history → returns snapshots newest-first with agents list
T3   GET /teams/{team}/history → regression_warning when score drops ≥10pp
T4   GET /teams/{team}/history → no warning when drop is <10pp
T5   GET /teams/{team}/history → no warning when eval_count < 2 per snapshot
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import WorkspaceContext, get_workspace_context
from app.main import app
from app.models.integrations import TeamSnapshot, TicketDestination
from app.models.run import Run
from app.models.eval import EvalRun


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# In ANTCREW_TESTING=1 open mode the context has workspace_id=None.
# Override it to workspace_id=1 so endpoints that require a scoped workspace work.
def _ws_ctx() -> WorkspaceContext:
    return WorkspaceContext(workspace_id=1, created_by="test", role="admin")


@pytest.fixture(autouse=True)
def override_ws_context():
    app.dependency_overrides[get_workspace_context] = _ws_ctx
    yield
    app.dependency_overrides.pop(get_workspace_context, None)


# ── helpers ───────────────────────────────────────────────────────────────────

async def _make_run(
    session: AsyncSession,
    *,
    team: str = "DevTeam",
    status: str = "success",
    cost_usd: float = 0.004,
    workspace_id: Optional[int] = 1,
) -> Run:
    import uuid
    run = Run(
        run_id=uuid.uuid4().hex,
        thread_id="t1",
        team=team,
        request="test request",
        status=status,
        cost_usd=cost_usd,
        workspace_id=workspace_id,
        created_at=_utcnow(),
    )
    session.add(run)
    await session.flush()
    return run


async def _make_eval(
    session: AsyncSession,
    *,
    team: str = "DevTeam",
    workspace_id: Optional[int] = 1,
    overall_score: Optional[float] = 0.85,
    created_at: Optional[datetime] = None,
) -> EvalRun:
    import uuid
    ev = EvalRun(
        eval_id=uuid.uuid4().hex,
        team=team,
        request="eval request",
        status="done",
        workspace_id=workspace_id,
        overall_score=overall_score,
        created_at=created_at or _utcnow(),
    )
    session.add(ev)
    await session.flush()
    return ev


async def _make_snapshot(
    session: AsyncSession,
    *,
    team_name: str = "DevTeam",
    team_hash: str = "aaaa1111bbbb2222",
    workspace_id: Optional[int] = 1,
    created_at: Optional[datetime] = None,
) -> TeamSnapshot:
    snap = TeamSnapshot(
        workspace_id=workspace_id,
        team_name=team_name,
        team_hash=team_hash,
        agents_json=[{"agent_name": "BA", "governance_hash": team_hash[:8], "stage": "planning"}],
        created_at=created_at or _utcnow(),
    )
    session.add(snap)
    await session.flush()
    return snap


# ── Integration destination tests ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_i1_list_destinations_empty(client: AsyncClient):
    r = await client.get("/integrations/")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_i2_create_github_destination_masks_token(client: AsyncClient):
    r = await client.post("/integrations/", json={
        "provider": "github",
        "label": "Main repo",
        "config_json": {
            "token": "ghp_supersecrettoken",
            "owner": "myorg",
            "repo": "backend",
        },
    })
    assert r.status_code == 201
    data = r.json()
    assert data["provider"] == "github"
    assert data["label"] == "Main repo"
    assert "***" in data["config"]["token"]
    # Token should be masked: first 4 chars preserved
    assert data["config"]["token"].startswith("ghp_")
    assert "supersecrettoken" not in data["config"]["token"]
    assert data["enabled"] is True
    assert data["team_filter"] is None


@pytest.mark.asyncio
async def test_i3_create_invalid_provider(client: AsyncClient):
    r = await client.post("/integrations/", json={
        "provider": "trello",
        "label": "Trello",
        "config_json": {"key": "x"},
    })
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_i4_create_missing_required_keys(client: AsyncClient):
    r = await client.post("/integrations/", json={
        "provider": "github",
        "label": "Incomplete",
        "config_json": {"token": "tok"},  # missing owner and repo
    })
    assert r.status_code == 422
    assert "owner" in r.json()["detail"] or "repo" in r.json()["detail"]


@pytest.mark.asyncio
async def test_i5_list_all_destinations(client: AsyncClient, session: AsyncSession):
    session.add(TicketDestination(
        workspace_id=1,
        provider="linear",
        label="My Linear",
        config_json={"api_key": "lin_api_xyz123", "team_id": "TEAM-1"},
        enabled=True,
    ))
    await session.commit()

    r = await client.get("/integrations/")
    assert r.status_code == 200
    destinations = r.json()
    assert len(destinations) == 1
    assert destinations[0]["provider"] == "linear"
    # api_key should be masked
    assert "***" in destinations[0]["config"]["api_key"]
    assert "xyz123" not in destinations[0]["config"]["api_key"]


@pytest.mark.asyncio
async def test_i6_patch_destination(client: AsyncClient, session: AsyncSession):
    dest = TicketDestination(
        workspace_id=1,
        provider="jira",
        label="Old label",
        config_json={
            "base_url": "https://x.atlassian.net",
            "email": "a@b.com",
            "api_token": "tok",
            "project_key": "BACK",
        },
        enabled=True,
    )
    session.add(dest)
    await session.commit()
    await session.refresh(dest)

    r = await client.patch(f"/integrations/{dest.id}", json={"label": "New label", "enabled": False})
    assert r.status_code == 200
    data = r.json()
    assert data["label"] == "New label"
    assert data["enabled"] is False


@pytest.mark.asyncio
async def test_i7_delete_destination(client: AsyncClient, session: AsyncSession):
    dest = TicketDestination(
        workspace_id=1,
        provider="github",
        label="To delete",
        config_json={"token": "t", "owner": "o", "repo": "r"},
    )
    session.add(dest)
    await session.commit()
    await session.refresh(dest)

    r = await client.delete(f"/integrations/{dest.id}")
    assert r.status_code == 204

    r2 = await client.get("/integrations/")
    assert r2.json() == []


@pytest.mark.asyncio
async def test_i8_test_destination_502_on_adapter_failure(client: AsyncClient, session: AsyncSession):
    dest = TicketDestination(
        workspace_id=1,
        provider="github",
        label="Broken",
        config_json={"token": "bad", "owner": "x", "repo": "y"},
    )
    session.add(dest)
    await session.commit()
    await session.refresh(dest)

    from app.services import ticket_sync as ts_mod

    async def _fail(*_a, **_kw):
        return None

    with patch.object(ts_mod, "_PROVIDER_ADAPTERS", {"github": _fail}):
        r = await client.post(f"/integrations/{dest.id}/test")
    assert r.status_code == 502


# ── Cost estimation tests ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_e1_estimate_no_history(client: AsyncClient):
    r = await client.get("/runs/estimate?team=DevTeam")
    assert r.status_code == 200
    data = r.json()
    assert data["based_on_runs"] == 0
    assert data["min_usd"] is None
    assert data["median_usd"] is None
    assert data["p75_usd"] is None
    assert data["max_usd"] is None


@pytest.mark.asyncio
async def test_e2_estimate_returns_percentiles(client: AsyncClient, session: AsyncSession):
    costs = [0.001, 0.002, 0.003, 0.004, 0.005, 0.006, 0.007, 0.008, 0.009, 0.010]
    for cost in costs:
        await _make_run(session, cost_usd=cost)
    await session.commit()

    r = await client.get("/runs/estimate?team=DevTeam&limit=20")
    assert r.status_code == 200
    data = r.json()
    assert data["based_on_runs"] == 10
    assert data["min_usd"] == pytest.approx(0.001, abs=1e-6)
    assert data["max_usd"] == pytest.approx(0.010, abs=1e-6)
    # Median of sorted list (10 items) = avg of idx 4 and 5 = (0.005+0.006)/2 = 0.0055
    assert data["median_usd"] == pytest.approx(0.0055, abs=1e-5)
    assert data["p75_usd"] is not None
    assert 0.007 <= data["p75_usd"] <= 0.008


@pytest.mark.asyncio
async def test_e3_estimate_only_successful_nonzero(client: AsyncClient, session: AsyncSession):
    await _make_run(session, cost_usd=0.005, status="success")
    await _make_run(session, cost_usd=0.007, status="error")   # excluded
    await _make_run(session, cost_usd=0.000, status="success")  # excluded (cost=0)
    await session.commit()

    r = await client.get("/runs/estimate?team=DevTeam")
    assert r.status_code == 200
    data = r.json()
    assert data["based_on_runs"] == 1
    assert data["min_usd"] == pytest.approx(0.005, abs=1e-6)
    assert data["max_usd"] == pytest.approx(0.005, abs=1e-6)


# ── Team governance history tests ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_t1_team_history_empty(client: AsyncClient):
    r = await client.get("/teams/DevTeam/history")
    assert r.status_code == 200
    data = r.json()
    assert data["team"] == "DevTeam"
    assert data["snapshots"] == []
    assert data["regression_warnings"] == []


@pytest.mark.asyncio
async def test_t2_team_history_snapshots_newest_first(client: AsyncClient, session: AsyncSession):
    older_time = _utcnow() - timedelta(days=10)
    newer_time = _utcnow() - timedelta(days=2)

    await _make_snapshot(session, team_hash="oldhash00000000a", created_at=older_time)
    await _make_snapshot(session, team_hash="newhash00000000b", created_at=newer_time)
    await session.commit()

    r = await client.get("/teams/DevTeam/history")
    assert r.status_code == 200
    snapshots = r.json()["snapshots"]
    assert len(snapshots) == 2
    # Newest first
    assert snapshots[0]["team_hash"] == "newhash00000000b"
    assert snapshots[1]["team_hash"] == "oldhash00000000a"
    # Agents are returned
    assert isinstance(snapshots[0]["agents"], list)
    assert len(snapshots[0]["agents"]) == 1


@pytest.mark.asyncio
async def test_t3_regression_warning_when_score_drops(client: AsyncClient, session: AsyncSession):
    older_time = _utcnow() - timedelta(days=20)
    newer_time = _utcnow() - timedelta(days=5)

    await _make_snapshot(session, team_hash="stable00hash000a", created_at=older_time)
    await _make_snapshot(session, team_hash="changed0hash000b", created_at=newer_time)

    # 3 evals in older period (good scores)
    for score in [0.85, 0.84, 0.83]:
        await _make_eval(session, overall_score=score, created_at=older_time + timedelta(days=1))

    # 3 evals in newer period (bad scores — dropped >10pp)
    for score in [0.70, 0.68, 0.72]:
        await _make_eval(session, overall_score=score, created_at=newer_time + timedelta(days=1))

    await session.commit()

    r = await client.get("/teams/DevTeam/history?days=90")
    assert r.status_code == 200
    data = r.json()
    assert len(data["regression_warnings"]) == 1
    w = data["regression_warnings"][0]
    assert w["team_hash"] == "changed0hash000b"
    assert w["previous_hash"] == "stable00hash000a"
    assert w["drop_pp"] >= 10.0
    assert "Score dropped" in w["message"]


@pytest.mark.asyncio
async def test_t4_no_warning_when_drop_small(client: AsyncClient, session: AsyncSession):
    older_time = _utcnow() - timedelta(days=20)
    newer_time = _utcnow() - timedelta(days=5)

    await _make_snapshot(session, team_hash="stable00hash000c", created_at=older_time)
    await _make_snapshot(session, team_hash="changed0hash000d", created_at=newer_time)

    # 3 evals in older period
    for score in [0.85, 0.84, 0.83]:
        await _make_eval(session, overall_score=score, created_at=older_time + timedelta(days=1))

    # 3 evals in newer period — only 5pp drop, below threshold
    for score in [0.80, 0.79, 0.81]:
        await _make_eval(session, overall_score=score, created_at=newer_time + timedelta(days=1))

    await session.commit()

    r = await client.get("/teams/DevTeam/history?days=90")
    assert r.status_code == 200
    assert r.json()["regression_warnings"] == []


@pytest.mark.asyncio
async def test_t5_no_warning_when_too_few_evals(client: AsyncClient, session: AsyncSession):
    older_time = _utcnow() - timedelta(days=20)
    newer_time = _utcnow() - timedelta(days=5)

    await _make_snapshot(session, team_hash="stable00hash000e", created_at=older_time)
    await _make_snapshot(session, team_hash="changed0hash000f", created_at=newer_time)

    # Only 1 eval in each period — below the minimum of 2
    await _make_eval(session, overall_score=0.85, created_at=older_time + timedelta(days=1))
    await _make_eval(session, overall_score=0.50, created_at=newer_time + timedelta(days=1))

    await session.commit()

    r = await client.get("/teams/DevTeam/history?days=90")
    assert r.status_code == 200
    # No warning despite the huge drop — too few data points
    assert r.json()["regression_warnings"] == []
