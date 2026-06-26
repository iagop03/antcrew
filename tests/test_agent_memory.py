"""Tests for automatic memory injection in BaseAgent.system()."""
from __future__ import annotations

import pytest

from antcrew.models.simulated import SimulatedLLM
from antcrew.memory.store import InMemoryMemory, MemoryResult
from antcrew.agents.pm import PMAgent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_agent(**kwargs):
    return PMAgent(SimulatedLLM(), **kwargs)


class _CaptureLLM(SimulatedLLM):
    """Records every (system, user) pair passed to complete()."""
    captured: list[tuple[str, str]]

    def __init__(self):
        super().__init__()
        self.captured = []

    def complete(self, messages, *, max_tokens=4096, json_mode=False):
        self.captured.append((messages[0].content, messages[1].content))
        return super().complete(messages, max_tokens=max_tokens, json_mode=json_mode)


# ===========================================================================
# _inject_memory — unit tests
# ===========================================================================

def test_inject_memory_no_memory_returns_unchanged():
    agent = _make_agent()
    assert agent._inject_memory("Build auth") == "Build auth"


def test_inject_memory_empty_store_returns_unchanged():
    agent = _make_agent()
    agent.memory = InMemoryMemory()
    assert agent._inject_memory("Build auth") == "Build auth"


def test_inject_memory_prepends_context():
    agent = _make_agent()
    mem = InMemoryMemory()
    mem.add("Login system: authentication module", {"artifact_type": "prd"})
    agent.memory = mem

    result = agent._inject_memory("Build a login system")
    assert "[Relevant context from memory]" in result
    assert "Login system" in result
    assert result.endswith("Build a login system")


def test_inject_memory_multiple_results():
    agent = _make_agent(memory_n=3)
    mem = InMemoryMemory()
    mem.add("User registration system", {"artifact_type": "prd"})
    mem.add("User authentication login endpoint", {"artifact_type": "ticket"})
    agent.memory = mem

    result = agent._inject_memory("Build user authentication system")
    assert "User registration" in result or "authentication" in result


def test_inject_memory_score_threshold_filters_low_scores():
    """A very high threshold excludes entries with weak overlap."""
    agent = _make_agent(memory_score_threshold=1.0)  # only exact match
    mem = InMemoryMemory()
    mem.add("login system authentication", {"artifact_type": "prd"})
    agent.memory = mem

    # "build login" vs "login system authentication": partial overlap, score < 1.0
    result = agent._inject_memory("build login")
    assert "[Relevant context from memory]" not in result


def test_inject_memory_score_threshold_zero_includes_any_overlap():
    agent = _make_agent(memory_score_threshold=0.0)
    mem = InMemoryMemory()
    mem.add("login page", {"artifact_type": "prd"})
    agent.memory = mem

    result = agent._inject_memory("build login")
    assert "[Relevant context from memory]" in result


def test_inject_memory_exception_returns_unchanged():
    """If memory.search() raises, _inject_memory falls back gracefully."""
    agent = _make_agent()

    class BrokenMemory(InMemoryMemory):
        def search(self, query, *, n=5, filter=None):
            raise RuntimeError("DB down")

    agent.memory = BrokenMemory()
    agent.memory.add("something", {})
    result = agent._inject_memory("query")
    assert result == "query"


def test_inject_memory_respects_memory_n():
    agent = _make_agent(memory_n=1)
    mem = InMemoryMemory()
    mem.add("Entry A: login authentication", {"artifact_type": "prd"})
    mem.add("Entry B: login system auth", {"artifact_type": "ticket"})
    agent.memory = mem

    result = agent._inject_memory("login system authentication")
    # Only 1 result should be included (memory_n=1)
    lines = result.split("\n")
    context_entries = [l for l in lines if l.startswith("Entry")]
    assert len(context_entries) <= 1


# ===========================================================================
# system() integration — memory injection via LLM call
# ===========================================================================

def test_system_injects_memory_into_user_message():
    llm = _CaptureLLM()
    agent = PMAgent(llm)
    mem = InMemoryMemory()
    mem.add("Login system: JWT auth module", {"artifact_type": "prd"})
    agent.memory = mem

    agent.system("You are a PM.", "Build a login system")

    _, user_msg = llm.captured[0]
    assert "[Relevant context from memory]" in user_msg
    assert "Login system" in user_msg
    assert "Build a login system" in user_msg


def test_system_no_memory_user_unchanged():
    llm = _CaptureLLM()
    agent = PMAgent(llm)

    agent.system("You are a PM.", "Build X")

    _, user_msg = llm.captured[0]
    assert user_msg == "Build X"


def test_system_memory_context_before_user_message():
    llm = _CaptureLLM()
    agent = PMAgent(llm)
    mem = InMemoryMemory()
    mem.add("Login page implementation details", {"artifact_type": "ticket"})
    agent.memory = mem

    agent.system("sys", "build login page")

    _, user_msg = llm.captured[0]
    ctx_idx = user_msg.index("[Relevant context from memory]")
    user_idx = user_msg.index("build login page")
    assert ctx_idx < user_idx


def test_system_empty_memory_no_context_block():
    llm = _CaptureLLM()
    agent = PMAgent(llm)
    agent.memory = InMemoryMemory()  # empty

    agent.system("sys", "build X")

    _, user_msg = llm.captured[0]
    assert "[Relevant context from memory]" not in user_msg


# ===========================================================================
# Constructor args
# ===========================================================================

def test_default_memory_n():
    agent = _make_agent()
    assert agent.memory_n == 3


def test_custom_memory_n():
    agent = _make_agent(memory_n=5)
    assert agent.memory_n == 5


def test_default_score_threshold():
    agent = _make_agent()
    assert agent.memory_score_threshold == 0.0


def test_custom_score_threshold():
    agent = _make_agent(memory_score_threshold=0.5)
    assert agent.memory_score_threshold == 0.5


def test_memory_none_by_default():
    agent = _make_agent()
    assert agent.memory is None


# ===========================================================================
# Interaction with preset and system_prompt_suffix
# ===========================================================================

def test_preset_and_memory_both_applied():
    llm = _CaptureLLM()
    agent = PMAgent(llm, preset="concise")
    mem = InMemoryMemory()
    mem.add("Login auth module context", {"artifact_type": "prd"})
    agent.memory = mem

    agent.system("You are a PM.", "Build login module")

    sys_msg, user_msg = llm.captured[0]
    from antcrew.presets import CONCISE
    assert sys_msg.startswith(CONCISE.instruction)
    assert "[Relevant context from memory]" in user_msg


def test_memory_does_not_modify_system_prompt():
    llm = _CaptureLLM()
    agent = PMAgent(llm)
    mem = InMemoryMemory()
    mem.add("Login module memory context", {"artifact_type": "prd"})
    agent.memory = mem

    agent.system("You are a PM.", "Build login module")

    sys_msg, _ = llm.captured[0]
    assert "[Relevant context from memory]" not in sys_msg
