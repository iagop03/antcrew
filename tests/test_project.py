"""Tests for Project — persistent project sessions."""
from __future__ import annotations

import json
import time

import pytest

from antcrew.models.simulated import SimulatedLLM
from antcrew.project import Project, _deserialize_state, _serialize_state
from antcrew.teams.dev_team import DevTeam
from antcrew.teams.research_team import ResearchTeam
from antcrew.teams.content_team import ContentTeam


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dev_team() -> DevTeam:
    return DevTeam(model=SimulatedLLM())


def _research_team() -> ResearchTeam:
    return ResearchTeam(model=SimulatedLLM())


# ---------------------------------------------------------------------------
# Basic run
# ---------------------------------------------------------------------------

def test_project_run_returns_state():
    p = Project(_dev_team())
    state = p.run("Build a login page")
    assert isinstance(state, dict)
    assert state.get("prd") is not None

def test_project_state_after_first_run():
    p = Project(_dev_team())
    p.run("Build a login page")
    assert p.state.get("prd") is not None

def test_project_history_after_first_run():
    p = Project(_dev_team())
    p.run("Build a login page")
    assert len(p.history) == 1
    assert p.history[0]["request"] == "Build a login page"

def test_project_history_entry_has_expected_keys():
    p = Project(_dev_team())
    p.run("Build login")
    h = p.history[0]
    assert "request" in h
    assert "timestamp" in h
    assert "new_tickets" in h
    assert "new_code_files" in h


# ---------------------------------------------------------------------------
# State accumulation across runs
# ---------------------------------------------------------------------------

def test_project_tickets_accumulate():
    p = Project(_dev_team())
    p.run("Build login")
    n_after_1 = len(p.state.get("tickets") or [])
    p.run("Add OAuth")
    n_after_2 = len(p.state.get("tickets") or [])
    assert n_after_2 >= n_after_1  # tickets never decrease

def test_project_code_artifacts_accumulate():
    p = Project(_dev_team())
    p.run("Build login")
    n1 = len(p.state.get("code_artifacts") or [])
    p.run("Add OAuth")
    n2 = len(p.state.get("code_artifacts") or [])
    assert n2 >= n1

def test_project_prd_replaced_by_latest():
    p = Project(_dev_team())
    p.run("Build login")
    prd1 = p.state.get("prd")
    p.run("Redesign architecture")
    prd2 = p.state.get("prd")
    # PRD is always set (latest wins)
    assert prd2 is not None

def test_project_two_runs_history():
    p = Project(_dev_team())
    p.run("Run 1")
    p.run("Run 2")
    assert len(p.history) == 2
    assert p.history[1]["request"] == "Run 2"

def test_project_state_is_snapshot_copy():
    p = Project(_dev_team())
    p.run("Build login")
    snap = p.state
    snap["fake_key"] = "tampered"
    assert "fake_key" not in p.state  # mutating the snapshot doesn't affect project


# ---------------------------------------------------------------------------
# Request enrichment
# ---------------------------------------------------------------------------

def test_project_enriches_second_request(monkeypatch):
    """The second run's enriched request should include prior context."""
    captured: list[str] = []

    original_run = DevTeam.run
    def _intercept(self, request, **kw):
        captured.append(request)
        return original_run(self, request, **kw)

    monkeypatch.setattr(DevTeam, "run", _intercept)

    p = Project(_dev_team())
    p.run("Build login")       # first run — no enrichment
    p.run("Add OAuth")         # second run — should be enriched

    # First call: no prefix
    assert "Project context" not in captured[0]
    # Second call: context injected
    assert "Project context" in captured[1]
    assert "New request: Add OAuth" in captured[1]

def test_project_no_enrichment_on_first_run(monkeypatch):
    captured: list[str] = []

    original_run = DevTeam.run
    def _intercept(self, request, **kw):
        captured.append(request)
        return original_run(self, request, **kw)

    monkeypatch.setattr(DevTeam, "run", _intercept)

    p = Project(_dev_team())
    p.run("Build login")
    assert captured[0] == "Build login"  # unchanged


# ---------------------------------------------------------------------------
# save / load
# ---------------------------------------------------------------------------

def test_project_save_creates_file(tmp_path):
    p = Project(_dev_team(), name="my-project")
    p.run("Build login")
    dest = tmp_path / "proj.json"
    p.save(dest)
    assert dest.exists()

def test_project_save_contains_expected_keys(tmp_path):
    p = Project(_dev_team(), name="my-project")
    p.run("Build login")
    dest = tmp_path / "proj.json"
    p.save(dest)
    data = json.loads(dest.read_text())
    assert data["name"] == "my-project"
    assert "state" in data
    assert "history" in data
    assert "created_at" in data

def test_project_load_restores_name(tmp_path):
    p = Project(_dev_team(), name="auth-service")
    p.run("Build login")
    dest = tmp_path / "proj.json"
    p.save(dest)

    p2 = Project.load(dest, team=_dev_team())
    assert p2.name == "auth-service"

def test_project_load_restores_history(tmp_path):
    p = Project(_dev_team())
    p.run("Build login")
    p.run("Add OAuth")
    dest = tmp_path / "proj.json"
    p.save(dest)

    p2 = Project.load(dest, team=_dev_team())
    assert len(p2.history) == 2
    assert p2.history[0]["request"] == "Build login"

def test_project_load_restores_prd(tmp_path):
    p = Project(_dev_team())
    p.run("Build login")
    dest = tmp_path / "proj.json"
    p.save(dest)

    p2 = Project.load(dest, team=_dev_team())
    assert p2.state.get("prd") is not None

def test_project_load_restores_tickets(tmp_path):
    p = Project(_dev_team())
    p.run("Build login")
    n_original = len(p.state.get("tickets") or [])
    dest = tmp_path / "proj.json"
    p.save(dest)

    p2 = Project.load(dest, team=_dev_team())
    assert len(p2.state.get("tickets") or []) == n_original

def test_project_load_then_run_continues_accumulating(tmp_path):
    p = Project(_dev_team())
    p.run("Build login")
    n1 = len(p.state.get("tickets") or [])
    dest = tmp_path / "proj.json"
    p.save(dest)

    p2 = Project.load(dest, team=_dev_team())
    p2.run("Add OAuth")
    n2 = len(p2.state.get("tickets") or [])
    assert n2 >= n1  # continued accumulation after load

def test_project_auto_save_on_run(tmp_path):
    dest = tmp_path / "proj.json"
    p = Project(_dev_team(), path=dest)
    p.run("Build login")
    assert dest.exists()

def test_project_save_no_path_raises():
    p = Project(_dev_team())
    p.run("Build login")
    with pytest.raises(ValueError, match="[Pp]ath"):
        p.save()

def test_project_save_creates_parent_dirs(tmp_path):
    dest = tmp_path / "deep" / "nested" / "proj.json"
    p = Project(_dev_team())
    p.run("Build login")
    p.save(dest)
    assert dest.exists()


# ---------------------------------------------------------------------------
# summary()
# ---------------------------------------------------------------------------

def test_project_summary_contains_name():
    p = Project(_dev_team(), name="auth-service")
    p.run("Build login")
    s = p.summary()
    assert "auth-service" in s

def test_project_summary_contains_run_count():
    p = Project(_dev_team())
    p.run("Run 1")
    p.run("Run 2")
    s = p.summary()
    assert "2" in s

def test_project_summary_contains_request():
    p = Project(_dev_team())
    p.run("Build login page")
    s = p.summary()
    assert "Build login page" in s

def test_project_summary_empty_project():
    p = Project(_dev_team(), name="empty")
    s = p.summary()
    assert "empty" in s
    assert "0" in s  # zero runs


# ---------------------------------------------------------------------------
# Works with different team types
# ---------------------------------------------------------------------------

def test_project_with_research_team():
    p = Project(_research_team())
    state = p.run("Research AI safety")
    assert isinstance(state, dict)

def test_project_with_content_team():
    p = Project(ContentTeam(model=SimulatedLLM()))
    state = p.run("Write a blog post about Python")
    assert isinstance(state, dict)


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------

def test_serialize_state_round_trip():
    from antcrew.core.artifacts import PRD, Ticket
    state = {
        "prd": PRD(title="T", summary="S", goals=["g"], constraints=[], stakeholders=[]),
        "tickets": [Ticket(id="t1", title="Login", description="desc", priority="high",
                           status="open", acceptance_criteria=[], dependencies=[])],
        "errors": [],
        "current_agent": "pm",
    }
    serialized = _serialize_state(state)
    restored = _deserialize_state(serialized)

    assert isinstance(restored["prd"], PRD)
    assert restored["prd"].title == "T"
    assert len(restored["tickets"]) == 1
    assert isinstance(restored["tickets"][0], Ticket)
    assert restored["tickets"][0].title == "Login"


# ---------------------------------------------------------------------------
# Top-level imports
# ---------------------------------------------------------------------------

def test_importable_from_antcrew():
    from antcrew import Project as P
    assert P is Project
