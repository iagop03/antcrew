"""Tests for GET /runs/artifact-timeline.

AT01  Empty workspace → empty timeline
AT02  Single run with artifacts → version 1, all files "added"
AT03  Two identical runs → version 2 shows all files "unchanged"
AT04  Two runs with changed content → version 2 shows "changed" status + correct line counts
AT05  Runs without matching artifacts are skipped
AT06  team filter only includes matching runs
AT07  artifact_type filter limits extracted keys
AT08  Invalid artifact_type returns 422
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.models.run import Run


def _run(run_id: str, *, status: str = "success", state: dict | None = None, team: str = "DevTeam") -> Run:
    return Run(run_id=run_id, team=team, request="test", status=status, state=state)


# ---------------------------------------------------------------------------
# AT01 — empty timeline
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_at01_empty_timeline(client: AsyncClient):
    r = await client.get("/runs/artifact-timeline")
    assert r.status_code == 200
    data = r.json()
    assert data["versions_with_artifacts"] == 0
    assert data["timeline"] == []


# ---------------------------------------------------------------------------
# AT02 — single run; all files are "added" (no previous version)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_at02_single_run_all_added(client: AsyncClient, session):
    state = {
        "code_artifacts": [
            {"file_path": "src/app.py", "content": "print('hello')\n"},
            {"file_path": "src/utils.py", "content": "def f(): pass\n"},
        ]
    }
    session.add(_run("at02-run-1", state=state))
    await session.commit()

    r = await client.get("/runs/artifact-timeline")
    assert r.status_code == 200
    data = r.json()

    assert data["versions_with_artifacts"] == 1
    v = data["timeline"][0]
    assert v["version"] == 1
    assert v["run_id"] == "at02-run-1"
    assert v["files_total"] == 2
    assert all(f["status"] == "added" for f in v["files"])
    assert v["lines_added"] == 2
    assert v["lines_removed"] == 0


# ---------------------------------------------------------------------------
# AT03 — two identical runs → second version shows "unchanged"
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_at03_two_identical_runs_unchanged(client: AsyncClient, session):
    state = {"code_artifacts": [{"file_path": "main.py", "content": "x = 1\n"}]}
    session.add(_run("at03-run-1", state=state))
    session.add(_run("at03-run-2", state=state))
    await session.commit()

    r = await client.get("/runs/artifact-timeline")
    data = r.json()
    assert data["versions_with_artifacts"] == 2

    v1, v2 = data["timeline"]
    assert v1["files"][0]["status"] == "added"
    assert v2["files"][0]["status"] == "unchanged"
    assert v2["lines_added"] == 0
    assert v2["lines_removed"] == 0


# ---------------------------------------------------------------------------
# AT04 — changed content → "changed" status with correct line counts
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_at04_changed_content(client: AsyncClient, session):
    state_a = {"code_artifacts": [{"file_path": "app.py", "content": "a = 1\nb = 2\n"}]}
    state_b = {"code_artifacts": [{"file_path": "app.py", "content": "a = 1\nb = 3\nc = 4\n"}]}
    session.add(_run("at04-run-1", state=state_a))
    session.add(_run("at04-run-2", state=state_b))
    await session.commit()

    r = await client.get("/runs/artifact-timeline")
    data = r.json()
    v2 = data["timeline"][1]

    f = next(f for f in v2["files"] if f["file_path"] == "app.py")
    assert f["status"] == "changed"
    assert f["lines_added"] >= 1
    assert f["lines_removed"] >= 1


# ---------------------------------------------------------------------------
# AT05 — runs without artifacts are skipped; version counter doesn't advance
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_at05_runs_without_artifacts_skipped(client: AsyncClient, session):
    state_empty = {"tickets": []}
    state_with = {"code_artifacts": [{"file_path": "x.py", "content": "pass\n"}]}

    session.add(_run("at05-run-1", state=state_empty))
    session.add(_run("at05-run-2", state=state_with))
    session.add(_run("at05-run-3", state=state_empty))
    session.add(_run("at05-run-4", state=state_with))
    await session.commit()

    r = await client.get("/runs/artifact-timeline")
    data = r.json()
    assert data["versions_with_artifacts"] == 2
    assert data["runs_scanned"] == 4


# ---------------------------------------------------------------------------
# AT06 — team filter
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_at06_team_filter(client: AsyncClient, session):
    state = {"code_artifacts": [{"file_path": "a.py", "content": "pass\n"}]}
    session.add(_run("at06-dev", team="DevTeam", state=state))
    session.add(_run("at06-full", team="FullStackTeam", state=state))
    await session.commit()

    r = await client.get("/runs/artifact-timeline?team=DevTeam")
    data = r.json()
    assert data["versions_with_artifacts"] == 1
    assert data["timeline"][0]["team"] == "DevTeam"


# ---------------------------------------------------------------------------
# AT07 — artifact_type filter: code_artifacts only
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_at07_artifact_type_filter(client: AsyncClient, session):
    state = {
        "code_artifacts": [{"file_path": "src/app.py", "content": "x = 1\n"}],
        "test_artifacts": [{"file_path": "tests/test_app.py", "content": "def t(): pass\n"}],
    }
    session.add(_run("at07-run-1", state=state))
    await session.commit()

    r = await client.get("/runs/artifact-timeline?artifact_type=code_artifacts")
    data = r.json()
    assert data["versions_with_artifacts"] == 1
    paths = [f["file_path"] for f in data["timeline"][0]["files"]]
    assert "src/app.py" in paths
    assert "tests/test_app.py" not in paths


# ---------------------------------------------------------------------------
# AT08 — invalid artifact_type returns 422
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_at08_invalid_artifact_type(client: AsyncClient):
    r = await client.get("/runs/artifact-timeline?artifact_type=nonexistent")
    assert r.status_code == 422
