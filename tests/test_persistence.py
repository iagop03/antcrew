"""Tests for save_state / load_state."""
from __future__ import annotations

import json

import pytest

from antcrew.core.artifacts import (
    PRD,
    CodeArtifact,
    ContentPiece,
    DevOpsArtifact,
    Priority,
    Ticket,
    TicketStatus,
)
from antcrew.utils.persistence import load_state, save_state

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _full_state() -> dict:
    return {
        "request": "Build auth",
        "current_agent": "backend_dev",
        "prd": PRD(
            title="Auth",
            summary="JWT auth",
            goals=["Secure login"],
            out_of_scope=[],
            functional_requirements=["POST /auth"],
            non_functional_requirements=[],
            open_questions=[],
        ),
        "tickets": [
            Ticket(
                id="T-001", title="Auth endpoint", description="JWT",
                priority=Priority.HIGH, status=TicketStatus.DONE,
                acceptance_criteria=["Tests pass"], dependencies=[],
            )
        ],
        "code_artifacts": [
            CodeArtifact(
                ticket_id="T-001",
                file_path="src/auth.py",
                description="Auth",
                language="python",
                content="def auth(): pass\n",
            )
        ],
        "devops_artifacts": [
            DevOpsArtifact(
                file_path="Dockerfile",
                description="Image",
                language="dockerfile",
                content="FROM python:3.12-slim\n",
            )
        ],
        "content_piece": ContentPiece(
            title="About AntCrew",
            target_audience="devs",
            tone="professional",
            outline=["Intro", "Usage"],
            body="Some text",
            word_count=2,
        ),
        "messages": [{"role": "user", "content": "Build auth"}],
        "errors": [],
        "metadata": {"elapsed": 3.5},
        "test_artifacts": None,
        "review": None,
        "research_document": None,
    }


# ---------------------------------------------------------------------------
# save_state
# ---------------------------------------------------------------------------

def test_save_state_creates_file(tmp_path):
    state = _full_state()
    dest = tmp_path / "state.json"
    save_state(state, dest)
    assert dest.exists()


def test_save_state_valid_json(tmp_path):
    state = _full_state()
    dest = tmp_path / "state.json"
    save_state(state, dest)
    data = json.loads(dest.read_text())
    assert isinstance(data, dict)


def test_save_state_prd_serialised(tmp_path):
    state = _full_state()
    dest = tmp_path / "state.json"
    save_state(state, dest)
    data = json.loads(dest.read_text())
    assert data["prd"]["title"] == "Auth"
    assert data["prd"]["summary"] == "JWT auth"


def test_save_state_tickets_serialised(tmp_path):
    state = _full_state()
    dest = tmp_path / "state.json"
    save_state(state, dest)
    data = json.loads(dest.read_text())
    assert len(data["tickets"]) == 1
    assert data["tickets"][0]["id"] == "T-001"
    assert data["tickets"][0]["priority"] == "high"


def test_save_state_code_artifacts_serialised(tmp_path):
    state = _full_state()
    dest = tmp_path / "state.json"
    save_state(state, dest)
    data = json.loads(dest.read_text())
    assert data["code_artifacts"][0]["file_path"] == "src/auth.py"


def test_save_state_devops_artifacts_serialised(tmp_path):
    state = _full_state()
    dest = tmp_path / "state.json"
    save_state(state, dest)
    data = json.loads(dest.read_text())
    assert data["devops_artifacts"][0]["file_path"] == "Dockerfile"


def test_save_state_none_values(tmp_path):
    state = _full_state()
    state["review"] = None
    dest = tmp_path / "state.json"
    save_state(state, dest)
    data = json.loads(dest.read_text())
    assert data["review"] is None


def test_save_state_creates_parent_dirs(tmp_path):
    state = _full_state()
    dest = tmp_path / "deep" / "nested" / "state.json"
    save_state(state, dest)
    assert dest.exists()


def test_save_state_indent(tmp_path):
    state = {"request": "test", "prd": None, "tickets": None, "code_artifacts": None,
             "devops_artifacts": None, "content_piece": None, "messages": [],
             "errors": [], "metadata": {}, "current_agent": "",
             "test_artifacts": None, "review": None, "research_document": None}
    dest = tmp_path / "state.json"
    save_state(state, dest, indent=4)
    text = dest.read_text()
    # 4-space indent means lines start with 4 spaces
    assert "    " in text


# ---------------------------------------------------------------------------
# load_state
# ---------------------------------------------------------------------------

def test_load_state_round_trip(tmp_path):
    state = _full_state()
    dest = tmp_path / "state.json"
    save_state(state, dest)
    loaded = load_state(dest)
    assert loaded["prd"]["title"] == "Auth"
    assert loaded["tickets"][0]["id"] == "T-001"


def test_load_state_returns_dict(tmp_path):
    state = _full_state()
    dest = tmp_path / "state.json"
    save_state(state, dest)
    loaded = load_state(dest)
    assert isinstance(loaded, dict)


def test_load_state_prd_is_dict_not_pydantic(tmp_path):
    state = _full_state()
    dest = tmp_path / "state.json"
    save_state(state, dest)
    loaded = load_state(dest)
    # load_state does NOT re-hydrate Pydantic models
    assert isinstance(loaded["prd"], dict)
    assert not hasattr(loaded["prd"], "model_fields")


def test_load_state_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_state(tmp_path / "missing.json")


def test_load_state_string_path(tmp_path):
    state = _full_state()
    dest = tmp_path / "state.json"
    save_state(state, dest)
    loaded = load_state(str(dest))
    assert loaded["request"] == "Build auth"


def test_load_state_re_hydrate_prd(tmp_path):
    """Demonstrate that callers can re-hydrate Pydantic objects after load."""
    state = _full_state()
    dest = tmp_path / "state.json"
    save_state(state, dest)
    loaded = load_state(dest)
    prd = PRD.model_validate(loaded["prd"])
    assert prd.title == "Auth"
    assert isinstance(prd.goals, list)
