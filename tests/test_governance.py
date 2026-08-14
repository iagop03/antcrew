"""Tests for BaseAgent.stage, BaseAgent.governance_hash, and compute_team_hash."""
from __future__ import annotations

import pytest

from antcrew.core.agent import BaseAgent, compute_team_hash
from antcrew.core.tools import BaseTool, ToolResult
from antcrew.models.simulated import SimulatedLLM


# ---------------------------------------------------------------------------
# Concrete agents for testing
# ---------------------------------------------------------------------------

class _AgentA(BaseAgent):
    name = "agent_a"
    role_description = "Does A"
    stage = "planning"

    def run(self, state): return {}


class _AgentB(BaseAgent):
    name = "agent_b"
    role_description = "Does B"
    stage = "implementation"

    def run(self, state): return {}


class _AgentNoStage(BaseAgent):
    name = "agent_no_stage"
    role_description = "Legacy agent"

    def run(self, state): return {}


class _ToolX(BaseTool):
    name = "tool_x"
    description = "tool x"
    def run(self, i): return ToolResult(i)


class _ToolY(BaseTool):
    name = "tool_y"
    description = "tool y"
    def run(self, i): return ToolResult(i)


def _llm(): return SimulatedLLM()


# ---------------------------------------------------------------------------
# stage
# ---------------------------------------------------------------------------

def test_stage_defaults_to_empty_string():
    assert _AgentNoStage(llm=_llm()).stage == ""


def test_stage_class_level_value():
    assert _AgentA(llm=_llm()).stage == "planning"
    assert _AgentB(llm=_llm()).stage == "implementation"


def test_stage_different_instances_independent():
    a = _AgentA(llm=_llm())
    b = _AgentB(llm=_llm())
    assert a.stage != b.stage


# ---------------------------------------------------------------------------
# governance_hash — format
# ---------------------------------------------------------------------------

def test_governance_hash_is_string():
    assert isinstance(_AgentA(llm=_llm()).governance_hash, str)


def test_governance_hash_is_16_chars():
    assert len(_AgentA(llm=_llm()).governance_hash) == 16


def test_governance_hash_is_valid_hex():
    h = _AgentA(llm=_llm()).governance_hash
    int(h, 16)  # raises ValueError if not valid hex


# ---------------------------------------------------------------------------
# governance_hash — determinism
# ---------------------------------------------------------------------------

def test_governance_hash_same_config_same_hash():
    assert _AgentA(llm=_llm()).governance_hash == _AgentA(llm=_llm()).governance_hash


def test_governance_hash_different_name():
    assert _AgentA(llm=_llm()).governance_hash != _AgentB(llm=_llm()).governance_hash


def test_governance_hash_different_role():
    class _R1(BaseAgent):
        name = "x"; role_description = "role1"; stage = ""
        def run(self, s): return {}

    class _R2(BaseAgent):
        name = "x"; role_description = "role2"; stage = ""
        def run(self, s): return {}

    assert _R1(llm=_llm()).governance_hash != _R2(llm=_llm()).governance_hash


def test_governance_hash_different_stage():
    class _S1(BaseAgent):
        name = "x"; role_description = "r"; stage = "planning"
        def run(self, s): return {}

    class _S2(BaseAgent):
        name = "x"; role_description = "r"; stage = "implementation"
        def run(self, s): return {}

    assert _S1(llm=_llm()).governance_hash != _S2(llm=_llm()).governance_hash


def test_governance_hash_different_suffix():
    a = _AgentA(llm=_llm())
    b = _AgentA(llm=_llm(), system_prompt_suffix="Always use TypeScript.")
    assert a.governance_hash != b.governance_hash


def test_governance_hash_different_tools():
    a = _AgentA(llm=_llm())
    b = _AgentA(llm=_llm(), tools=[_ToolX()])
    assert a.governance_hash != b.governance_hash


def test_governance_hash_tool_order_independent():
    """Sorted tool names → same hash regardless of construction order."""
    a = _AgentA(llm=_llm(), tools=[_ToolX(), _ToolY()])
    b = _AgentA(llm=_llm(), tools=[_ToolY(), _ToolX()])
    assert a.governance_hash == b.governance_hash


# ---------------------------------------------------------------------------
# governance_hash — tracelog emits it
# ---------------------------------------------------------------------------

def test_tracelog_emits_to_bus():
    from antcrew.core.events import capture
    agent = _AgentA(llm=_llm())
    with capture("agent.tracelog") as events:
        agent._tracelog("parse_failure", schema="MySchema", error="bad json")
    assert len(events) == 1
    assert events[0].payload["event"] == "parse_failure"
    assert events[0].payload["agent"] == "agent_a"
    assert events[0].payload["stage"] == "planning"


def test_tracelog_run_id_is_none_by_default():
    from antcrew.core.events import capture
    agent = _AgentA(llm=_llm())
    with capture("agent.tracelog") as events:
        agent._tracelog("test.event")
    assert events[0].run_id is None


def test_tracelog_uses_injected_run_id():
    from antcrew.core.events import capture
    agent = _AgentA(llm=_llm())
    agent._run_id = "run-abc123"
    agent._thread_id = "thread-1"
    with capture("agent.tracelog") as events:
        agent._tracelog("test.event")
    assert events[0].run_id == "run-abc123"
    assert events[0].thread_id == "thread-1"


# ---------------------------------------------------------------------------
# compute_team_hash
# ---------------------------------------------------------------------------

def test_compute_team_hash_is_16_char_hex():
    h = compute_team_hash([_AgentA(llm=_llm()), _AgentB(llm=_llm())])
    assert len(h) == 16
    int(h, 16)


def test_compute_team_hash_same_team_same_hash():
    h1 = compute_team_hash([_AgentA(llm=_llm()), _AgentB(llm=_llm())])
    h2 = compute_team_hash([_AgentA(llm=_llm()), _AgentB(llm=_llm())])
    assert h1 == h2


def test_compute_team_hash_order_independent():
    a = _AgentA(llm=_llm())
    b = _AgentB(llm=_llm())
    assert compute_team_hash([a, b]) == compute_team_hash([b, a])


def test_compute_team_hash_changes_when_agent_changes():
    before = compute_team_hash([_AgentA(llm=_llm()), _AgentB(llm=_llm())])
    after  = compute_team_hash([_AgentA(llm=_llm()), _AgentNoStage(llm=_llm())])
    assert before != after


def test_compute_team_hash_single_agent():
    h = compute_team_hash([_AgentA(llm=_llm())])
    assert len(h) == 16


def test_compute_team_hash_empty_list():
    h = compute_team_hash([])
    assert len(h) == 16


# ---------------------------------------------------------------------------
# _make_evented_run sets run context on agent
# ---------------------------------------------------------------------------

def test_make_evented_run_sets_run_id_on_agent():
    from antcrew.core.events import _make_evented_run, capture

    captured_run_id = []

    class _TracingAgent(BaseAgent):
        name = "tracer"
        stage = "planning"
        def run(self, state):
            self._tracelog("inside.run")
            return {}

    agent = _TracingAgent(llm=_llm())
    wrapped = _make_evented_run(lambda s: agent.run(s), "tracer", agent)

    with capture("agent.tracelog") as events:
        wrapped({"_run_id": "run-xyz", "_thread_id": "t1"})

    assert any(e.run_id == "run-xyz" for e in events)


def test_agent_end_event_includes_stage_and_governance_hash():
    from antcrew.core.events import _make_evented_run, capture

    agent = _AgentA(llm=_llm())
    wrapped = _make_evented_run(lambda s: {}, "agent_a", agent)

    with capture("agent.end") as events:
        wrapped({"_run_id": "r1", "_thread_id": "t1"})

    assert events[0].payload["stage"] == "planning"
    assert len(events[0].payload["governance_hash"]) == 16
