"""Tests for DocWriterAgent, FullStackTeam, and FrontendDev accumulation fix."""
from __future__ import annotations

import json

import pytest

from antcrew.agents.doc_writer import DocWriterAgent
from antcrew.core.artifacts import (
    CodeArtifact, DevOpsArtifact, DocumentationArtifact,
    PRD, Priority, Ticket, TicketStatus,
)
from antcrew.models.simulated import SimulatedLLM
from antcrew.teams.fullstack_team import FullStackTeam


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _llm() -> SimulatedLLM:
    return SimulatedLLM()


def _base_state(**overrides) -> dict:
    state = {
        "request": "Build auth",
        "messages": [],
        "prd": PRD(
            title="Auth Module", summary="JWT auth",
            goals=["Secure login"], out_of_scope=[],
            functional_requirements=["POST /auth"],
            non_functional_requirements=[], open_questions=[],
        ),
        "tickets": [
            Ticket(
                id="T-001", title="JWT endpoint", description="Add /auth",
                priority=Priority.HIGH, status=TicketStatus.DONE,
                acceptance_criteria=[], dependencies=[],
            )
        ],
        "code_artifacts": [
            CodeArtifact(ticket_id="T-001", file_path="src/auth.py",
                         description="Auth handler", language="python", content="pass\n"),
        ],
        "devops_artifacts": [
            DevOpsArtifact(file_path="Dockerfile", description="Image",
                           language="dockerfile", content="FROM python:3.12-slim\n"),
        ],
        "test_artifacts": None,
        "review": None,
        "doc_artifacts": None,
        "research_document": None,
        "content_piece": None,
        "current_agent": "",
        "errors": [],
        "metadata": {},
    }
    state.update(overrides)
    return state


# ---------------------------------------------------------------------------
# DocWriterAgent — run()
# ---------------------------------------------------------------------------

def test_doc_writer_produces_artifacts():
    agent = DocWriterAgent(_llm())
    result = agent.run(_base_state())
    assert "doc_artifacts" in result
    assert len(result["doc_artifacts"]) >= 1
    assert all(isinstance(a, DocumentationArtifact) for a in result["doc_artifacts"])


def test_doc_writer_has_readme():
    agent = DocWriterAgent(_llm())
    result = agent.run(_base_state())
    paths = [a.file_path for a in result["doc_artifacts"]]
    assert "README.md" in paths


def test_doc_writer_has_architecture():
    agent = DocWriterAgent(_llm())
    result = agent.run(_base_state())
    paths = [a.file_path for a in result["doc_artifacts"]]
    assert any("ARCHITECTURE" in p for p in paths)


def test_doc_writer_sets_current_agent():
    agent = DocWriterAgent(_llm())
    result = agent.run(_base_state())
    assert result["current_agent"] == "doc_writer"


def test_doc_writer_no_prd_no_code_returns_empty():
    agent = DocWriterAgent(_llm())
    state = _base_state(prd=None, code_artifacts=None)
    result = agent.run(state)
    assert "doc_artifacts" not in result or result.get("doc_artifacts") is None


def test_doc_writer_content_is_markdown():
    agent = DocWriterAgent(_llm())
    result = agent.run(_base_state())
    readme = next(a for a in result["doc_artifacts"] if a.file_path == "README.md")
    assert readme.format == "markdown"
    assert "#" in readme.content


# ---------------------------------------------------------------------------
# DocWriterAgent — refine()
# ---------------------------------------------------------------------------

def test_doc_writer_refine_returns_updated():
    agent = DocWriterAgent(_llm())
    first = agent.run(_base_state())
    artifacts = first["doc_artifacts"]
    refined = agent.refine(_base_state(), artifacts, "Add installation section to README")
    assert "doc_artifacts" in refined
    assert len(refined["doc_artifacts"]) >= 1


def test_doc_writer_refine_returns_documentation_artifacts():
    agent = DocWriterAgent(_llm())
    artifacts = agent.run(_base_state())["doc_artifacts"]
    refined = agent.refine(_base_state(), artifacts, "Make it more concise")
    assert all(isinstance(a, DocumentationArtifact) for a in refined["doc_artifacts"])


# ---------------------------------------------------------------------------
# FrontendDev — accumulation fix
# ---------------------------------------------------------------------------

def test_frontend_dev_accumulates_existing_code_artifacts():
    from antcrew.agents.frontend_dev import FrontendDevAgent

    existing = [
        CodeArtifact(ticket_id="T-001", file_path="src/auth.py",
                     description="Backend", language="python", content="pass\n")
    ]
    state = _base_state(
        code_artifacts=existing,
        tickets=[
            Ticket(id="T-001", title="Feature", description="React UI",
                   priority=Priority.MEDIUM, status=TicketStatus.DONE,
                   acceptance_criteria=[], dependencies=[])
        ],
    )
    agent = FrontendDevAgent(_llm())
    result = agent.run(state)
    # Must contain existing backend artifact + new frontend artifact(s)
    all_paths = [a.file_path for a in result["code_artifacts"]]
    assert "src/auth.py" in all_paths
    assert len(result["code_artifacts"]) > len(existing)


def test_frontend_dev_processes_all_tickets():
    from antcrew.agents.frontend_dev import FrontendDevAgent

    # All tickets are DONE (as if BackendDev ran first)
    state = _base_state(
        code_artifacts=[],
        tickets=[
            Ticket(id="T-001", title="Auth UI", description="Login form",
                   priority=Priority.HIGH, status=TicketStatus.DONE,
                   acceptance_criteria=[], dependencies=[]),
            Ticket(id="T-002", title="Dashboard", description="Main dashboard",
                   priority=Priority.MEDIUM, status=TicketStatus.DONE,
                   acceptance_criteria=[], dependencies=[]),
        ],
    )
    agent = FrontendDevAgent(_llm())
    result = agent.run(state)
    # Should generate frontend artifacts for all tickets, not return empty
    assert len(result["code_artifacts"]) >= 1


# ---------------------------------------------------------------------------
# FullStackTeam — structure
# ---------------------------------------------------------------------------

def test_fullstack_team_has_all_agents():
    team = FullStackTeam(model=_llm())
    expected = {
        "business_analyst", "pm", "backend_dev", "frontend_dev",
        "qa", "reviewer", "devops", "doc_writer",
    }
    assert set(team._agents.keys()) == expected


def test_fullstack_team_run_returns_state():
    team = FullStackTeam(model=_llm())
    state = team.run("Build a task management app")
    assert isinstance(state, dict)
    assert state.get("prd") is not None


def test_fullstack_team_run_produces_code_artifacts():
    team = FullStackTeam(model=_llm())
    state = team.run("Build a task management app")
    artifacts = state.get("code_artifacts") or []
    assert len(artifacts) >= 1


def test_fullstack_team_run_produces_doc_artifacts():
    team = FullStackTeam(model=_llm())
    state = team.run("Build a task management app")
    doc_artifacts = state.get("doc_artifacts") or []
    assert len(doc_artifacts) >= 1


def test_fullstack_team_run_produces_devops_artifacts():
    team = FullStackTeam(model=_llm())
    state = team.run("Build a task management app")
    devops = state.get("devops_artifacts") or []
    assert len(devops) >= 1


def test_fullstack_team_agent_override():
    from antcrew.agents.backend_dev import BackendDevAgent

    custom_llm = _llm()
    team = FullStackTeam(
        model=_llm(),
        agents={"backend_dev": BackendDevAgent(custom_llm)},
    )
    assert team._agents["backend_dev"].llm is custom_llm


def test_fullstack_team_initial_state_has_doc_artifacts_key():
    team = FullStackTeam(model=_llm())
    state = team._initial_state("test")
    assert "doc_artifacts" in state
    assert state["doc_artifacts"] is None


def test_fullstack_team_inherits_interactive_mixin():
    from antcrew.teams.base import InteractiveMixin
    assert isinstance(FullStackTeam(model=_llm()), InteractiveMixin)
