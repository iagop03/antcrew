"""Tests for the AntCrew REST API server."""
from __future__ import annotations

import time

import pytest

pytest.importorskip("fastapi", reason="fastapi not installed")

from fastapi.testclient import TestClient

from antcrew.server import app, _runs

client = TestClient(app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _wait_done(run_id: str, timeout: float = 10.0) -> dict:
    """Poll GET /run/{run_id} until status != 'running'."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/run/{run_id}")
        data = r.json()
        if data["status"] != "running":
            return data
        time.sleep(0.05)
    raise TimeoutError(f"Run {run_id} still running after {timeout}s")


def setup_function():
    """Clear run registry between tests."""
    _runs.clear()


# ---------------------------------------------------------------------------
# POST /run
# ---------------------------------------------------------------------------

def test_create_run_returns_202():
    r = client.post("/run", json={"request": "Build auth", "team": "dev", "model": "simulated"})
    assert r.status_code == 202


def test_create_run_returns_run_id():
    r = client.post("/run", json={"request": "Build auth", "team": "dev", "model": "simulated"})
    data = r.json()
    assert "run_id" in data
    assert len(data["run_id"]) == 36  # UUID format


def test_create_run_initial_status_running():
    r = client.post("/run", json={"request": "test", "team": "dev", "model": "simulated"})
    assert r.json()["status"] == "running"


def test_create_run_unknown_team_ends_in_error():
    r = client.post("/run", json={"request": "test", "team": "unknown", "model": "simulated"})
    run_id = r.json()["run_id"]
    data = _wait_done(run_id)
    assert data["status"] == "error"
    assert "unknown" in data["error"].lower()


# ---------------------------------------------------------------------------
# GET /run/{run_id}
# ---------------------------------------------------------------------------

def test_get_run_not_found():
    r = client.get("/run/nonexistent-id")
    assert r.status_code == 404


def test_get_run_running_has_no_state():
    r = client.post("/run", json={"request": "test", "team": "dev", "model": "simulated"})
    run_id = r.json()["run_id"]
    # Immediately check — might still be running
    r2 = client.get(f"/run/{run_id}")
    # state key only present when done
    if r2.json()["status"] == "running":
        assert "state" not in r2.json()


def test_get_run_done_has_state():
    r = client.post("/run", json={"request": "Build auth", "team": "dev", "model": "simulated"})
    run_id = r.json()["run_id"]
    data = _wait_done(run_id)
    assert data["status"] == "done"
    assert "state" in data
    assert data["state"]["prd"]["title"] is not None


def test_get_run_research_team():
    r = client.post("/run", json={"request": "LLM risks", "team": "research", "model": "simulated"})
    run_id = r.json()["run_id"]
    data = _wait_done(run_id)
    assert data["status"] == "done"
    assert "state" in data


def test_get_run_content_team():
    r = client.post("/run", json={"request": "Blog about AI", "team": "content", "model": "simulated"})
    run_id = r.json()["run_id"]
    data = _wait_done(run_id)
    assert data["status"] == "done"


# ---------------------------------------------------------------------------
# GET /run/{run_id}/artifacts
# ---------------------------------------------------------------------------

def test_get_artifacts_not_found():
    r = client.get("/run/nosuchrun/artifacts")
    assert r.status_code == 404


def test_get_artifacts_while_running_returns_409():
    r = client.post("/run", json={"request": "test", "team": "dev", "model": "simulated"})
    run_id = r.json()["run_id"]
    # Manually set status to running to test 409
    _runs[run_id]["status"] = "running"
    r2 = client.get(f"/run/{run_id}/artifacts")
    assert r2.status_code == 409


def test_get_artifacts_done():
    r = client.post("/run", json={"request": "Build auth", "team": "dev", "model": "simulated"})
    run_id = r.json()["run_id"]
    _wait_done(run_id)
    r2 = client.get(f"/run/{run_id}/artifacts")
    assert r2.status_code == 200
    data = r2.json()
    assert "code_artifacts" in data
    assert "devops_artifacts" in data
    assert "doc_artifacts" in data
    assert isinstance(data["code_artifacts"], list)


# ---------------------------------------------------------------------------
# GET /runs
# ---------------------------------------------------------------------------

def test_list_runs_empty():
    r = client.get("/runs")
    assert r.status_code == 200
    assert r.json() == []


def test_list_runs_after_creation():
    client.post("/run", json={"request": "r1", "team": "dev", "model": "simulated"})
    client.post("/run", json={"request": "r2", "team": "dev", "model": "simulated"})
    r = client.get("/runs")
    assert len(r.json()) == 2


def test_list_runs_contains_request():
    client.post("/run", json={"request": "my request", "team": "dev", "model": "simulated"})
    r = client.get("/runs")
    assert any(run["request"] == "my request" for run in r.json())


# ---------------------------------------------------------------------------
# DELETE /run/{run_id}
# ---------------------------------------------------------------------------

def test_delete_run():
    r = client.post("/run", json={"request": "to delete", "team": "dev", "model": "simulated"})
    run_id = r.json()["run_id"]
    d = client.delete(f"/run/{run_id}")
    assert d.status_code == 204
    assert client.get(f"/run/{run_id}").status_code == 404


def test_delete_run_not_found():
    r = client.delete("/run/ghost-id")
    assert r.status_code == 404
