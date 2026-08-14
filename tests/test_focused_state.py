"""Tests for BaseAgent.focused_state() — context compression."""
from __future__ import annotations

import pytest

from antcrew.core.agent import BaseAgent
from antcrew.core.state import TeamState
from antcrew.models.simulated import SimulatedLLM


# ---------------------------------------------------------------------------
# Minimal concrete agent for testing
# ---------------------------------------------------------------------------

class _AgentWithConsumes(BaseAgent):
    name = "test_with_consumes"
    consumes = ["request", "tickets", "metadata"]
    produces = ["code_artifacts"]

    def run(self, state: TeamState) -> dict:
        return {}


class _AgentNoConsumes(BaseAgent):
    name = "test_no_consumes"

    def run(self, state: TeamState) -> dict:
        return {}


def _llm() -> SimulatedLLM:
    return SimulatedLLM()


# ---------------------------------------------------------------------------
# Tests: agent WITH consumes
# ---------------------------------------------------------------------------

def test_focused_state_keeps_consumes_keys():
    agent = _AgentWithConsumes(llm=_llm())
    state = {
        "request": "build auth",
        "tickets": [{"id": "T-1"}],
        "metadata": {"sprint": 1},
        "prd": {"title": "PRD"},
        "code_artifacts": [],
    }
    result = agent.focused_state(state)
    assert "request" in result
    assert "tickets" in result
    assert "metadata" in result


def test_focused_state_drops_unreferenced_keys():
    agent = _AgentWithConsumes(llm=_llm())
    state = {
        "request": "build auth",
        "tickets": [{"id": "T-1"}],
        "prd": {"title": "PRD"},       # not in consumes
        "review": {"verdict": "ok"},   # not in consumes
    }
    result = agent.focused_state(state)
    assert "prd" not in result
    assert "review" not in result


def test_focused_state_always_includes_passthrough_keys():
    agent = _AgentWithConsumes(llm=_llm())
    state = {
        "request": "build auth",
        "tickets": [],
        "messages": [{"role": "user", "content": "hi"}],
        "thread_id": "t-001",
        "_kb_context": "some kb data",
        "metadata": {},
        "unrelated": "garbage",
    }
    result = agent.focused_state(state)
    # All pass-through keys present
    assert "messages" in result
    assert "thread_id" in result
    assert "_kb_context" in result
    assert "metadata" in result
    # Unrelated key excluded
    assert "unrelated" not in result


def test_focused_state_excludes_none_values():
    agent = _AgentWithConsumes(llm=_llm())
    state = {
        "request": "build auth",
        "tickets": None,      # in consumes but None → excluded
        "metadata": {},
    }
    result = agent.focused_state(state)
    assert "request" in result
    assert "tickets" not in result   # None filtered out
    assert "metadata" in result


def test_focused_state_empty_state():
    agent = _AgentWithConsumes(llm=_llm())
    result = agent.focused_state({})
    assert result == {}


# ---------------------------------------------------------------------------
# Tests: agent WITHOUT consumes (legacy agents)
# ---------------------------------------------------------------------------

def test_focused_state_no_consumes_returns_full_state():
    """Agents without consumes get the full state — no regression."""
    agent = _AgentNoConsumes(llm=_llm())
    state = {
        "request": "build auth",
        "prd": {"title": "PRD"},
        "tickets": [],
        "code_artifacts": [],
        "review": None,
    }
    result = agent.focused_state(state)
    # Full state returned unchanged
    assert set(result.keys()) == set(state.keys())


def test_focused_state_no_consumes_does_not_filter_none():
    """Without consumes, None values are kept (full pass-through)."""
    agent = _AgentNoConsumes(llm=_llm())
    state = {"request": "x", "prd": None, "tickets": []}
    result = agent.focused_state(state)
    assert result == state


# ---------------------------------------------------------------------------
# Tests: return type
# ---------------------------------------------------------------------------

def test_focused_state_returns_new_dict():
    """Modifying the result does not mutate the original state."""
    agent = _AgentWithConsumes(llm=_llm())
    state = {"request": "build auth", "tickets": [1, 2]}
    result = agent.focused_state(state)
    result["extra"] = "injected"
    assert "extra" not in state


def test_focused_state_returns_dict_not_teamstate():
    agent = _AgentWithConsumes(llm=_llm())
    state = {"request": "x", "tickets": []}
    result = agent.focused_state(state)
    assert isinstance(result, dict)
