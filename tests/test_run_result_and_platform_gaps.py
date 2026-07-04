"""Tests for platform-readiness gaps:

  - RunResult.to_dict() / to_json() serialization
  - Stable (deterministic) ticket IDs from PMAgent
  - _KBProxy per-agent context injection via _build_agent_map
  - MinimalPipeline loads ProjectKB for single-agent task types
  - _build_agent_map promoted to InteractiveMixin (FullStackTeam benefits)
"""
from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from antcrew.core.run_result import RunResult, _serialize
from antcrew.core.artifacts import PRD, Ticket, CodeArtifact, Priority, TicketStatus
from antcrew.agents.pm import _stable_ticket_id, PMAgent
from antcrew.core.project_kb import ProjectKB
from antcrew.teams.dev_team import _KBProxy


# ---------------------------------------------------------------------------
# _serialize helper
# ---------------------------------------------------------------------------

def test_serialize_primitives():
    assert _serialize(None) is None
    assert _serialize(42) == 42
    assert _serialize(3.14) == 3.14
    assert _serialize(True) is True
    assert _serialize("hello") == "hello"


def test_serialize_pydantic_model():
    prd = PRD(
        title="Auth", summary="JWT auth",
        goals=[], out_of_scope=[],
        functional_requirements=[], non_functional_requirements=[],
        open_questions=[],
    )
    result = _serialize(prd)
    assert isinstance(result, dict)
    assert result["title"] == "Auth"


def test_serialize_list_of_pydantic():
    ticket = Ticket(
        id="TICKET-001", title="Login", description="POST /login",
        priority=Priority.HIGH, status=TicketStatus.OPEN,
    )
    result = _serialize([ticket])
    assert isinstance(result, list)
    assert result[0]["title"] == "Login"


def test_serialize_nested_dict():
    data = {"prd": {"title": "T"}, "count": 2}
    assert _serialize(data) == {"prd": {"title": "T"}, "count": 2}


def test_serialize_unknown_falls_back_to_str():
    class Weird:
        def __str__(self):
            return "weird-object"
    result = _serialize(Weird())
    assert result == "weird-object"


# ---------------------------------------------------------------------------
# RunResult.to_dict()
# ---------------------------------------------------------------------------

def _make_run_result(**state_overrides) -> RunResult:
    prd = PRD(
        title="Auth", summary="JWT auth",
        goals=[], out_of_scope=[],
        functional_requirements=[], non_functional_requirements=[],
        open_questions=[],
    )
    state = {
        "request": "Build auth",
        "prd": prd,
        "tickets": None,
        "code_artifacts": None,
        "current_agent": "pm",
        "errors": [],
        "metadata": {},
        "_run_id": "abc123def456",
        "_kb_context": "## KB\n...",
    }
    state.update(state_overrides)
    return RunResult(state=state, thread_id="t1", cost_usd=0.05)


def test_to_dict_is_json_serializable():
    rr = _make_run_result()
    d = rr.to_dict()
    # Must not raise
    json.dumps(d)


def test_to_dict_includes_metadata():
    rr = _make_run_result()
    d = rr.to_dict()
    assert d["thread_id"] == "t1"
    assert d["cost_usd"] == 0.05
    assert d["run_id"] == "abc123def456"


def test_to_dict_serializes_pydantic():
    rr = _make_run_result()
    d = rr.to_dict()
    assert isinstance(d["state"]["prd"], dict)
    assert d["state"]["prd"]["title"] == "Auth"


def test_to_dict_excludes_private_keys_by_default():
    rr = _make_run_result()
    d = rr.to_dict()
    assert "_run_id" not in d["state"]
    assert "_kb_context" not in d["state"]


def test_to_dict_includes_private_keys_when_requested():
    rr = _make_run_result()
    d = rr.to_dict(include_private=True)
    assert "_run_id" in d["state"]
    assert "_kb_context" in d["state"]


def test_to_json_returns_string():
    rr = _make_run_result()
    s = rr.to_json()
    assert isinstance(s, str)
    parsed = json.loads(s)
    assert parsed["thread_id"] == "t1"


def test_to_json_accepts_indent():
    rr = _make_run_result()
    s = rr.to_json(indent=2)
    assert "\n" in s


def test_to_dict_handles_list_of_artifacts():
    art = CodeArtifact(
        ticket_id="T-1", file_path="src/auth.py",
        description="Auth", content="def login(): pass", language="python",
    )
    rr = _make_run_result(code_artifacts=[art])
    d = rr.to_dict()
    serialized = d["state"]["code_artifacts"]
    assert isinstance(serialized, list)
    assert serialized[0]["file_path"] == "src/auth.py"


# ---------------------------------------------------------------------------
# Stable ticket IDs
# ---------------------------------------------------------------------------

def test_stable_ticket_id_format():
    tid = _stable_ticket_id("Auth Module", "Create login endpoint", 0)
    assert tid.startswith("TICKET-")
    assert len(tid) == len("TICKET-") + 8


def test_stable_ticket_id_is_deterministic():
    id1 = _stable_ticket_id("Auth Module", "Create login endpoint", 0)
    id2 = _stable_ticket_id("Auth Module", "Create login endpoint", 0)
    assert id1 == id2


def test_stable_ticket_id_differs_by_prd():
    id1 = _stable_ticket_id("Auth Module", "Login endpoint", 0)
    id2 = _stable_ticket_id("Dashboard", "Login endpoint", 0)
    assert id1 != id2


def test_stable_ticket_id_differs_by_title():
    id1 = _stable_ticket_id("Auth", "Login endpoint", 0)
    id2 = _stable_ticket_id("Auth", "Register endpoint", 0)
    assert id1 != id2


def test_stable_ticket_id_differs_by_index():
    id1 = _stable_ticket_id("Auth", "Login endpoint", 0)
    id2 = _stable_ticket_id("Auth", "Login endpoint", 1)
    assert id1 != id2


def test_pm_agent_generates_stable_ids():
    _VALID_PRD = {
        "title": "Auth Module",
        "summary": "JWT authentication.",
        "goals": ["Secure login"],
        "out_of_scope": [],
        "functional_requirements": ["POST /login"],
        "non_functional_requirements": [],
        "open_questions": [],
    }
    _TICKETS = [{"title": "Create login endpoint", "description": "POST /login",
                 "priority": "high", "acceptance_criteria": [], "dependencies": []}]

    llm = MagicMock()
    llm.system.return_value = json.dumps(_TICKETS)

    from antcrew.core.state import TeamState
    prd = PRD(**_VALID_PRD)
    state: dict = {"request": "Build auth", "messages": [], "prd": prd,
                   "errors": [], "metadata": {}, "_kb_context": ""}

    # Run twice with same input
    result1 = PMAgent(llm).run(state)
    llm.system.return_value = json.dumps(_TICKETS)
    result2 = PMAgent(llm).run(state)

    assert result1["tickets"][0].id == result2["tickets"][0].id
    assert result1["tickets"][0].id.startswith("TICKET-")


# ---------------------------------------------------------------------------
# _KBProxy
# ---------------------------------------------------------------------------

def test_kb_proxy_injects_context():
    agent = MagicMock()
    agent.run.return_value = {}
    proxy = _KBProxy(agent, "## KB Context\n")

    proxy.run({"_kb_context": "OLD", "request": "x"})

    call_state = agent.run.call_args[0][0]
    assert call_state["_kb_context"] == "## KB Context\n"


def test_kb_proxy_forwards_approval_required():
    agent = MagicMock()
    agent.approval_required = True
    proxy = _KBProxy(agent, "ctx")
    assert proxy.approval_required is True


def test_kb_proxy_false_when_agent_has_no_attr():
    proxy = _KBProxy(object(), "ctx")
    assert proxy.approval_required is False


def test_build_agent_map_returns_proxies_with_kb(tmp_path):
    from antcrew.teams.dev_team import DevTeam

    kb = ProjectKB()
    kb.tech_stack = ["Python", "FastAPI"]

    team = DevTeam.__new__(DevTeam)
    team._project_kb = kb
    team._agents = {"backend_dev": MagicMock(), "pm": MagicMock()}

    agent_map = team._build_agent_map()

    assert all(isinstance(v, _KBProxy) for v in agent_map.values())
    assert set(agent_map.keys()) == {"backend_dev", "pm"}


def test_build_agent_map_no_kb_returns_agents_unchanged():
    from antcrew.teams.dev_team import DevTeam

    team = DevTeam.__new__(DevTeam)
    team._project_kb = None
    team._agents = {"backend_dev": MagicMock()}

    assert team._build_agent_map() is team._agents


def test_fullstack_team_inherits_build_agent_map():
    from antcrew.teams.fullstack_team import FullStackTeam

    kb = ProjectKB()
    team = FullStackTeam.__new__(FullStackTeam)
    team._project_kb = kb
    team._agents = {"backend_dev": MagicMock(), "frontend_dev": MagicMock()}

    agent_map = team._build_agent_map()
    assert all(isinstance(v, _KBProxy) for v in agent_map.values())


# ---------------------------------------------------------------------------
# MinimalPipeline: KB loading for single-agent task types
# ---------------------------------------------------------------------------

def test_minimal_pipeline_loads_kb_for_single_agent(tmp_path):
    from antcrew.core.task_classifier import MinimalPipeline, TaskType

    # Write a minimal KB file
    kb = ProjectKB()
    kb.tech_stack = ["Python"]
    kb_path = tmp_path / "kb.json"
    kb.save(str(kb_path))

    # Mock the agent run so no LLM is called
    class FakeLLM:
        pass

    pipeline = MinimalPipeline(
        FakeLLM(),
        task_type=TaskType.DOCS,
        project_kb_path=str(kb_path),
    )
    team = pipeline._build_team(TaskType.DOCS)

    # _SingleAgentTeam should have a live ProjectKB instance, not None
    assert team._project_kb is not None
    assert "Python" in team._project_kb.tech_stack


def test_minimal_pipeline_no_kb_path_gives_none():
    from antcrew.core.task_classifier import MinimalPipeline, TaskType

    class FakeLLM:
        pass

    pipeline = MinimalPipeline(FakeLLM(), task_type=TaskType.DOCS)
    team = pipeline._build_team(TaskType.DOCS)

    assert team._project_kb is None


def test_minimal_pipeline_invalid_kb_path_does_not_crash():
    from antcrew.core.task_classifier import MinimalPipeline, TaskType

    class FakeLLM:
        pass

    pipeline = MinimalPipeline(
        FakeLLM(),
        task_type=TaskType.DOCS,
        project_kb_path="/nonexistent/path/kb.json",
    )
    # Should not raise; missing file returns empty KB
    team = pipeline._build_team(TaskType.DOCS)
    # ProjectKB.load returns empty KB for missing file, not None
    assert team._project_kb is not None
