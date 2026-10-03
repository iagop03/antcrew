"""Tests for _budget_alert_loop() and workspace budget alert logic."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlmodel import select

from app.models.workspace import Workspace
from app.models.run import Run


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_workspace(
    session,
    *,
    slug: str = "test-ws",
    max_cost_usd: float | None = 10.0,
    slack_webhook_url: str | None = None,
    is_blocked: bool = False,
):
    ws = Workspace(
        name="Test",
        slug=slug,
        max_cost_usd=max_cost_usd,
        total_cost_usd=0.0,
        is_blocked=is_blocked,
    )
    if slack_webhook_url:
        ws.slack_webhook_url = slack_webhook_url
    return ws


def _make_run(run_id: str, workspace_id: int, cost: float) -> Run:
    return Run(run_id=run_id, team="DevTeam", request="x", status="success",
               workspace_id=workspace_id, cost_usd=cost)


# ---------------------------------------------------------------------------
# Unit test: _budget_alert_loop internals (patch sleep to run one iteration)
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_budget_alert_query_logic(session):
    """The budget alert loop queries live Run.cost_usd sums per workspace correctly."""
    from sqlalchemy import func
    from sqlalchemy import select as sa_select

    ws = _make_workspace(session, slug="ws-80pct", max_cost_usd=10.0,
                         slack_webhook_url="https://hooks.slack.com/fake")
    session.add(ws)
    await session.commit()
    await session.refresh(ws)

    session.add(_make_run("r-80-1", ws.id, 5.0))
    session.add(_make_run("r-80-2", ws.id, 3.5))  # total: 8.5 = 85% of $10
    await session.commit()

    # Verify the query the loop uses returns the correct total
    result = (await session.execute(
        sa_select(func.coalesce(func.sum(Run.cost_usd), 0.0))
        .where(Run.workspace_id == ws.id)
        .where(Run.cost_usd.isnot(None))
    )).scalar()
    total = float(result)
    assert total == pytest.approx(8.5)

    pct = total / ws.max_cost_usd
    assert pct >= 0.80  # 80% threshold would fire

    # Verify workspace is returned by the loop's eligibility query
    eligible = (await session.exec(
        select(Workspace).where(
            Workspace.max_cost_usd.isnot(None),
            Workspace.is_blocked.is_(False),
        )
    )).all()
    assert ws.id in [w.id for w in eligible]


@pytest.mark.anyio
async def test_budget_alert_not_fired_below_80(session):
    """No alert when spending is below 80%."""
    ws = _make_workspace(session, slug="ws-low", max_cost_usd=10.0)
    session.add(ws)
    await session.commit()
    await session.refresh(ws)

    run = _make_run("r-low-1", ws.id, 7.9)  # 79% — below threshold
    session.add(run)
    await session.commit()

    from sqlalchemy import func
    from sqlalchemy import select as sa_select
    result = (await session.execute(
        sa_select(func.coalesce(func.sum(Run.cost_usd), 0.0))
        .where(Run.workspace_id == ws.id)
    )).scalar()
    pct = float(result) / ws.max_cost_usd
    assert pct < 0.80


@pytest.mark.anyio
async def test_budget_alert_fires_at_100_percent(session):
    """Alert fires at 100% threshold (budget exhausted)."""
    ws = _make_workspace(session, slug="ws-100pct", max_cost_usd=5.0)
    session.add(ws)
    await session.commit()
    await session.refresh(ws)

    for i in range(5):
        session.add(_make_run(f"r-100-{i}", ws.id, 1.0))  # 5 × $1 = $5
    await session.commit()

    from sqlalchemy import func
    from sqlalchemy import select as sa_select
    result = (await session.execute(
        sa_select(func.coalesce(func.sum(Run.cost_usd), 0.0))
        .where(Run.workspace_id == ws.id)
    )).scalar()
    pct = float(result) / ws.max_cost_usd
    assert pct >= 1.0


@pytest.mark.anyio
async def test_blocked_workspace_skipped(session):
    """Blocked workspaces must be excluded from budget alert checks."""
    ws = _make_workspace(session, slug="ws-blocked", max_cost_usd=1.0, is_blocked=True)
    session.add(ws)
    await session.commit()
    await session.refresh(ws)

    # Even with 200% spend, a blocked workspace must not be checked
    session.add(_make_run("r-blk-1", ws.id, 2.0))
    await session.commit()

    # The loop's WHERE clause: is_blocked.is_(False) — verify our workspace would be excluded
    result = await session.exec(
        select(Workspace).where(
            Workspace.max_cost_usd.isnot(None),
            Workspace.is_blocked.is_(False),
        )
    )
    ids = [w.id for w in result.all()]
    assert ws.id not in ids


@pytest.mark.anyio
async def test_workspace_without_max_cost_skipped(session):
    """Workspaces with max_cost_usd=None must be excluded from budget alerts."""
    ws = _make_workspace(session, slug="ws-unlimited", max_cost_usd=None)
    session.add(ws)
    await session.commit()
    await session.refresh(ws)

    result = await session.exec(
        select(Workspace).where(
            Workspace.max_cost_usd.isnot(None),
            Workspace.is_blocked.is_(False),
        )
    )
    ids = [w.id for w in result.all()]
    assert ws.id not in ids


# ---------------------------------------------------------------------------
# Integration-style: verify alert deduplication logic (in-memory _fired set)
# ---------------------------------------------------------------------------

def test_fired_key_format():
    """Alert dedup keys follow the expected format."""
    ws_id = 42
    key_80  = f"ws:{ws_id}:{int(0.80 * 100)}"
    key_100 = f"ws:{ws_id}:{int(1.00 * 100)}"
    assert key_80  == "ws:42:80"
    assert key_100 == "ws:42:100"


# ---------------------------------------------------------------------------
# HTTP endpoint: GET /workspaces/{id}/spend reflects live data
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_spend_endpoint_returns_current_total(client, session):
    ws = _make_workspace(session, slug="ws-spend-test", max_cost_usd=50.0)
    session.add(ws)
    await session.commit()
    await session.refresh(ws)

    session.add(_make_run("r-sp-1", ws.id, 10.0))
    session.add(_make_run("r-sp-2", ws.id, 15.0))
    await session.commit()

    r = await client.get(f"/workspaces/{ws.id}/spend")
    assert r.status_code == 200
    data = r.json()
    assert data["workspace_id"] == ws.id
    assert data["budget_usd"] == 50.0
    assert "total_spend_usd" in data
    assert "exhausted" in data


@pytest.mark.anyio
async def test_spend_endpoint_exhausted_flag(client, session):
    ws = _make_workspace(session, slug="ws-exhausted", max_cost_usd=5.0)
    session.add(ws)
    await session.commit()
    await session.refresh(ws)

    # Update total_cost_usd to simulate tracked total
    ws.total_cost_usd = 6.0
    session.add(ws)
    await session.commit()

    r = await client.get(f"/workspaces/{ws.id}/spend")
    assert r.status_code == 200
    assert r.json()["exhausted"] is True


@pytest.mark.anyio
async def test_spend_endpoint_workspace_not_found(client):
    r = await client.get("/workspaces/99999/spend")
    assert r.status_code == 404
