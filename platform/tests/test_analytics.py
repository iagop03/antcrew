"""Tests for GET /workspaces/{id}/analytics."""
from __future__ import annotations

from datetime import datetime

import pytest

from app.models.workspace import Workspace
from app.models.run import Run, AgentEvent, Ticket


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_workspace(slug: str = "analytics-ws") -> Workspace:
    return Workspace(name="Analytics WS", slug=slug, max_cost_usd=100.0, total_cost_usd=0.0)


def _make_run(
    run_id: str,
    workspace_id: int,
    *,
    team: str = "DevTeam",
    cost: float = 1.0,
    model: str = "claude:claude-sonnet-5",
    status: str = "success",
) -> Run:
    return Run(
        run_id=run_id,
        team=team,
        request="x",
        status=status,
        workspace_id=workspace_id,
        cost_usd=cost,
        model=model,
    )


def _make_agent_event(run_id: str, agent_name: str, cost: float = 0.5, tokens_in: int = 100, tokens_out: int = 50) -> AgentEvent:
    return AgentEvent(
        run_id=run_id,
        agent_name=agent_name,
        cost_usd=cost,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        duration_s=1.0,
    )


# ---------------------------------------------------------------------------
# Analytics endpoint tests
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_analytics_returns_200_for_valid_workspace(client, session):
    ws = _make_workspace(slug="anl-ws-1")
    session.add(ws)
    await session.commit()
    await session.refresh(ws)

    r = await client.get(f"/workspaces/{ws.id}/analytics")
    assert r.status_code == 200


@pytest.mark.anyio
async def test_analytics_structure(client, session):
    ws = _make_workspace(slug="anl-ws-2")
    session.add(ws)
    await session.commit()
    await session.refresh(ws)

    r = await client.get(f"/workspaces/{ws.id}/analytics")
    data = r.json()
    assert "workspace_id" in data
    assert "runs_by_day" in data
    assert "tickets_by_status" in data
    assert "total_cost_usd" in data
    assert "by_team" in data
    assert "by_model" in data
    assert "by_agent" in data


@pytest.mark.anyio
async def test_analytics_empty_workspace(client, session):
    ws = _make_workspace(slug="anl-empty")
    session.add(ws)
    await session.commit()
    await session.refresh(ws)

    r = await client.get(f"/workspaces/{ws.id}/analytics")
    assert r.status_code == 200
    data = r.json()
    assert data["runs_by_day"] == []
    assert data["by_team"] == []
    assert data["by_model"] == []
    assert data["by_agent"] == []
    assert data["total_cost_usd"] == 0.0


@pytest.mark.anyio
async def test_analytics_by_team_groups_correctly(client, session):
    ws = _make_workspace(slug="anl-byteam")
    session.add(ws)
    await session.commit()
    await session.refresh(ws)

    session.add(_make_run("r-t1", ws.id, team="DevTeam", cost=2.0))
    session.add(_make_run("r-t2", ws.id, team="DevTeam", cost=3.0))
    session.add(_make_run("r-t3", ws.id, team="ResearchTeam", cost=1.5))
    await session.commit()

    r = await client.get(f"/workspaces/{ws.id}/analytics")
    data = r.json()
    teams = {row["team"]: row for row in data["by_team"]}
    assert "DevTeam" in teams
    assert teams["DevTeam"]["runs"] == 2
    assert teams["DevTeam"]["cost_usd"] == pytest.approx(5.0, abs=0.01)
    assert "ResearchTeam" in teams
    assert teams["ResearchTeam"]["runs"] == 1


@pytest.mark.anyio
async def test_analytics_by_model_groups_correctly(client, session):
    ws = _make_workspace(slug="anl-bymodel")
    session.add(ws)
    await session.commit()
    await session.refresh(ws)

    session.add(_make_run("r-m1", ws.id, model="claude:claude-sonnet-5", cost=1.0))
    session.add(_make_run("r-m2", ws.id, model="claude:claude-sonnet-5", cost=2.0))
    session.add(_make_run("r-m3", ws.id, model="openai:gpt-4o", cost=0.5))
    await session.commit()

    r = await client.get(f"/workspaces/{ws.id}/analytics")
    data = r.json()
    models = {row["model"]: row for row in data["by_model"]}
    assert "claude:claude-sonnet-5" in models
    assert models["claude:claude-sonnet-5"]["runs"] == 2
    assert models["claude:claude-sonnet-5"]["cost_usd"] == pytest.approx(3.0, abs=0.01)
    assert "openai:gpt-4o" in models
    assert models["openai:gpt-4o"]["runs"] == 1


@pytest.mark.anyio
async def test_analytics_by_agent_groups_correctly(client, session):
    ws = _make_workspace(slug="anl-byagent")
    session.add(ws)
    await session.commit()
    await session.refresh(ws)

    run = _make_run("r-ag1", ws.id, cost=3.0)
    session.add(run)
    await session.commit()

    session.add(_make_agent_event("r-ag1", "BusinessAnalystAgent", cost=1.0, tokens_in=100, tokens_out=50))
    session.add(_make_agent_event("r-ag1", "BackendDevAgent",      cost=2.0, tokens_in=200, tokens_out=100))
    await session.commit()

    r = await client.get(f"/workspaces/{ws.id}/analytics")
    data = r.json()
    agents = {row["agent_name"]: row for row in data["by_agent"]}
    assert "BusinessAnalystAgent" in agents
    assert "BackendDevAgent" in agents
    assert agents["BackendDevAgent"]["cost_usd"] == pytest.approx(2.0, abs=0.001)
    assert agents["BusinessAnalystAgent"]["tokens_in"] == 100
    assert agents["BusinessAnalystAgent"]["tokens_out"] == 50
    assert agents["BackendDevAgent"]["invocations"] == 1


@pytest.mark.anyio
async def test_analytics_by_agent_multiple_invocations(client, session):
    """Same agent invoked across multiple runs — totals are aggregated."""
    ws = _make_workspace(slug="anl-multiinvoke")
    session.add(ws)
    await session.commit()
    await session.refresh(ws)

    for i in range(3):
        run = _make_run(f"r-mi-{i}", ws.id, cost=1.0)
        session.add(run)
    await session.commit()

    for i in range(3):
        session.add(_make_agent_event(f"r-mi-{i}", "PMAgent", cost=0.5, tokens_in=50, tokens_out=25))
    await session.commit()

    r = await client.get(f"/workspaces/{ws.id}/analytics")
    data = r.json()
    agents = {row["agent_name"]: row for row in data["by_agent"]}
    assert "PMAgent" in agents
    assert agents["PMAgent"]["invocations"] == 3
    assert agents["PMAgent"]["cost_usd"] == pytest.approx(1.5, abs=0.001)
    assert agents["PMAgent"]["tokens_in"] == 150
    assert agents["PMAgent"]["tokens_out"] == 75


@pytest.mark.anyio
async def test_analytics_by_agent_empty_when_no_events(client, session):
    ws = _make_workspace(slug="anl-noevents")
    session.add(ws)
    await session.commit()
    await session.refresh(ws)

    session.add(_make_run("r-ne1", ws.id, cost=5.0))
    await session.commit()

    r = await client.get(f"/workspaces/{ws.id}/analytics")
    data = r.json()
    assert data["by_agent"] == []


@pytest.mark.anyio
async def test_analytics_runs_by_day_structure(client, session):
    ws = _make_workspace(slug="anl-days")
    session.add(ws)
    await session.commit()
    await session.refresh(ws)

    session.add(_make_run("r-day1", ws.id, cost=1.0, status="success"))
    session.add(_make_run("r-day2", ws.id, cost=2.0, status="error"))
    await session.commit()

    r = await client.get(f"/workspaces/{ws.id}/analytics")
    data = r.json()
    days = data["runs_by_day"]
    assert isinstance(days, list)
    for day in days:
        assert "date" in day
        assert "count" in day
        assert "success" in day
        assert "failed" in day
        assert "cost" in day


@pytest.mark.anyio
async def test_analytics_by_agent_sorted_by_cost_descending(client, session):
    """by_agent must be sorted by cost descending (most expensive first)."""
    ws = _make_workspace(slug="anl-sorted")
    session.add(ws)
    await session.commit()
    await session.refresh(ws)

    run = _make_run("r-sort1", ws.id, cost=10.0)
    session.add(run)
    await session.commit()

    session.add(_make_agent_event("r-sort1", "CheapAgent", cost=0.1))
    session.add(_make_agent_event("r-sort1", "ExpensiveAgent", cost=5.0))
    session.add(_make_agent_event("r-sort1", "MidAgent", cost=2.0))
    await session.commit()

    r = await client.get(f"/workspaces/{ws.id}/analytics")
    data = r.json()
    costs = [row["cost_usd"] for row in data["by_agent"]]
    assert costs == sorted(costs, reverse=True)


@pytest.mark.anyio
async def test_analytics_workspace_not_found(client):
    r = await client.get("/workspaces/99999/analytics")
    assert r.status_code in (403, 404)
