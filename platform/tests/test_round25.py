"""Round 25 — Governance endpoint + antcrew test CLI scaffolding.

GOV1  GET /runs/{run_id}/governance → 200 with required top-level keys
GOV2  agents list contains correct agent names and governance_hash values
GOV3  team_hash is a deterministic SHA-256 prefix over sorted agent hashes
GOV4  governance endpoint for run in another workspace → 403
GOV5  governance for unknown run → 404
GOV6  governance with no agent.end events → team_hash is empty, agents list uses AgentEvent rows
"""
from __future__ import annotations

import hashlib
import time
import uuid
from datetime import datetime, timezone

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import WorkspaceContext, get_workspace_context
from app.main import app
from app.models.run import AgentEvent, Event, Run


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _ws_ctx(workspace_id: int = 1) -> WorkspaceContext:
    return WorkspaceContext(workspace_id=workspace_id, created_by="test", role="admin")


@pytest.fixture(autouse=True)
def override_ws_context():
    app.dependency_overrides[get_workspace_context] = lambda: _ws_ctx(1)
    yield
    app.dependency_overrides.pop(get_workspace_context, None)


async def _make_run(
    session: AsyncSession,
    *,
    workspace_id: int = 1,
    status: str = "success",
) -> Run:
    run = Run(
        run_id=uuid.uuid4().hex,
        thread_id="t1",
        team="DevTeam",
        request="Build JWT auth",
        status=status,
        cost_usd=0.004,
        workspace_id=workspace_id,
        created_at=_utcnow(),
        finished_at=_utcnow(),
    )
    session.add(run)
    await session.flush()
    return run


async def _add_agent_events(
    session: AsyncSession,
    run: Run,
    agents: list[dict],
) -> None:
    """Add AgentEvent rows + agent.end Event rows with governance hashes."""
    for ag in agents:
        ae = AgentEvent(
            run_id=run.run_id,
            agent_name=ag["name"],
            duration_s=1.5,
            tokens_in=1000,
            tokens_out=500,
            cost_usd=0.001,
            produced_keys='[]',
            recorded_at=_utcnow(),
        )
        session.add(ae)

        ev = Event(
            run_id=run.run_id,
            event_type="agent.end",
            timestamp=time.time(),
            payload={
                "agent_name": ag["name"],
                "governance_hash": ag.get("hash", ""),
                "stage": ag.get("stage", ""),
                "duration_s": 1.5,
            },
        )
        session.add(ev)

    await session.flush()


# ---------------------------------------------------------------------------
# GOV1  basic 200 + required keys
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_gov1_returns_200_with_required_keys(db_session, api_client):
    run = await _make_run(db_session)
    await _add_agent_events(db_session, run, [
        {"name": "BA", "hash": "aabbccdd11223344", "stage": "analysis"},
    ])
    await db_session.commit()

    r = await api_client.get(f"/runs/{run.run_id}/governance")

    assert r.status_code == 200
    data = r.json()
    for key in ("run_id", "team", "team_hash", "agents", "engine_version"):
        assert key in data, f"missing key: {key}"
    assert data["run_id"] == run.run_id
    assert data["team"] == "DevTeam"


# ---------------------------------------------------------------------------
# GOV2  agents list matches inserted data
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_gov2_agents_list_contains_correct_data(db_session, api_client):
    run = await _make_run(db_session)
    await _add_agent_events(db_session, run, [
        {"name": "BA",          "hash": "hash_ba_1234abcd", "stage": "analysis"},
        {"name": "BackendDev",  "hash": "hash_dev_5678efgh", "stage": "implementation"},
    ])
    await db_session.commit()

    r = await api_client.get(f"/runs/{run.run_id}/governance")

    agents = {a["agent_name"]: a for a in r.json()["agents"]}
    assert "BA" in agents
    assert agents["BA"]["governance_hash"] == "hash_ba_1234abcd"
    assert agents["BA"]["stage"] == "analysis"
    assert "BackendDev" in agents
    assert agents["BackendDev"]["governance_hash"] == "hash_dev_5678efgh"


# ---------------------------------------------------------------------------
# GOV3  team_hash is deterministic SHA-256 of sorted agent hashes
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_gov3_team_hash_is_deterministic(db_session, api_client):
    run = await _make_run(db_session)
    h1 = "aaa111bbb222ccc3"
    h2 = "ddd444eee555fff6"
    await _add_agent_events(db_session, run, [
        {"name": "AgentA", "hash": h1},
        {"name": "AgentB", "hash": h2},
    ])
    await db_session.commit()

    r = await api_client.get(f"/runs/{run.run_id}/governance")

    expected_team_hash = (
        "sha256:"
        + hashlib.sha256("|".join(sorted([h1, h2])).encode()).hexdigest()[:16]
    )
    assert r.json()["team_hash"] == expected_team_hash


# ---------------------------------------------------------------------------
# GOV4  cross-workspace → 403
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_gov4_cross_workspace_returns_403(db_session, api_client):
    run = await _make_run(db_session, workspace_id=99)
    await db_session.commit()

    r = await api_client.get(f"/runs/{run.run_id}/governance")

    assert r.status_code == 403


# ---------------------------------------------------------------------------
# GOV5  unknown run → 404
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_gov5_unknown_run_returns_404(api_client):
    r = await api_client.get("/runs/nonexistent_run_id/governance")

    assert r.status_code == 404


# ---------------------------------------------------------------------------
# GOV6  no agent.end events → team_hash empty, agents from AgentEvent rows
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_gov6_no_end_events_graceful(db_session, api_client):
    run = await _make_run(db_session)
    ae = AgentEvent(
        run_id=run.run_id,
        agent_name="OrphanAgent",
        duration_s=2.0,
        tokens_in=500,
        tokens_out=200,
        cost_usd=0.0005,
        produced_keys='[]',
        recorded_at=_utcnow(),
    )
    db_session.add(ae)
    await db_session.commit()

    r = await api_client.get(f"/runs/{run.run_id}/governance")

    data = r.json()
    assert r.status_code == 200
    assert data["team_hash"] == ""
    assert len(data["agents"]) == 1
    assert data["agents"][0]["agent_name"] == "OrphanAgent"
    assert data["agents"][0]["governance_hash"] == ""
