"""Tests for FallbackLLM — model fallback chain behaviour."""
from __future__ import annotations

import pytest

from antcrew.models.base import BaseLLM, Message
from antcrew.models.fallback import FallbackLLM
from antcrew.models.simulated import SimulatedLLM


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

class FailingLLM(BaseLLM):
    """Always raises; max_retries=0 so it fails immediately (no sleep)."""

    def __init__(self, error: str = "Intentional failure") -> None:
        self._error = error
        self.max_retries = 0

    def complete(self, messages: list[Message], *, max_tokens: int = 4096) -> str:
        raise RuntimeError(self._error)


class CountingLLM(BaseLLM):
    """Fails the first N calls then delegates to SimulatedLLM."""

    def __init__(self, fail_count: int = 1) -> None:
        self._fail_count = fail_count
        self._calls = 0
        self._delegate = SimulatedLLM()
        self.max_retries = 0

    def complete(self, messages: list[Message], *, max_tokens: int = 4096) -> str:
        self._calls += 1
        if self._calls <= self._fail_count:
            raise RuntimeError(f"Fail #{self._calls}")
        return self._delegate.complete(messages, max_tokens=max_tokens)


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

def test_requires_at_least_one_model():
    with pytest.raises(ValueError, match="at least one"):
        FallbackLLM([])

def test_single_model_works():
    llm = FallbackLLM([SimulatedLLM()])
    result = llm.system("You are a PM. Output JSON tickets array.", "Build login")
    assert result  # SimulatedLLM returns a fixture

def test_accepts_multiple_models():
    llm = FallbackLLM([SimulatedLLM(), SimulatedLLM()])
    assert len(llm._models) == 2


# ---------------------------------------------------------------------------
# Fallback behaviour
# ---------------------------------------------------------------------------

def test_uses_primary_when_available():
    primary   = SimulatedLLM()
    secondary = SimulatedLLM()
    llm = FallbackLLM([primary, secondary])
    llm.system("You are a PM. Output JSON tickets array.", "Build login")
    # Primary was used — it has usage entries
    assert primary._usage_log
    # Secondary was not touched
    assert not secondary._usage_log

def test_falls_back_on_failure():
    llm = FallbackLLM([FailingLLM(), SimulatedLLM()])
    result = llm.system("You are a PM. Output JSON tickets array.", "Build login")
    assert result  # succeeded via secondary

def test_fallback_records_event():
    llm = FallbackLLM([FailingLLM("boom"), SimulatedLLM()])
    llm.system("You are a PM. Output JSON tickets array.", "Build x")
    events = llm.fallback_events()
    assert len(events) == 1
    assert events[0]["failed_model"] == "FailingLLM"
    assert events[0]["next_model"]   == "SimulatedLLM"
    assert "boom" in events[0]["error"]

def test_all_fail_raises_last_exception():
    llm = FallbackLLM([FailingLLM("err1"), FailingLLM("err2")])
    with pytest.raises(RuntimeError, match="err2"):
        llm.system("You are a PM.", "Build x")

def test_multiple_fallbacks_tries_all():
    llm = FallbackLLM([FailingLLM(), FailingLLM(), SimulatedLLM()])
    result = llm.system("You are a PM. Output JSON tickets array.", "Build x")
    assert result
    assert len(llm.fallback_events()) == 2  # two failures before success

def test_no_fallback_events_when_primary_succeeds():
    llm = FallbackLLM([SimulatedLLM(), FailingLLM()])
    llm.system("You are a PM. Output JSON tickets array.", "Build x")
    assert llm.fallback_events() == []


# ---------------------------------------------------------------------------
# Attribute propagation
# ---------------------------------------------------------------------------

def test_current_agent_propagates_to_all_models():
    m1, m2 = SimulatedLLM(), SimulatedLLM()
    llm = FallbackLLM([m1, m2])
    llm.current_agent = "backend_dev"
    assert m1.current_agent == "backend_dev"
    assert m2.current_agent == "backend_dev"

def test_on_token_propagates_to_all_models():
    m1, m2 = SimulatedLLM(), SimulatedLLM()
    llm = FallbackLLM([m1, m2])
    cb = lambda t: None
    llm.on_token = cb
    assert m1.on_token is cb
    assert m2.on_token is cb

def test_on_token_none_clears_on_all():
    m1, m2 = SimulatedLLM(), SimulatedLLM()
    llm = FallbackLLM([m1, m2])
    llm.on_token = lambda t: None
    llm.on_token = None
    assert m1.on_token is None
    assert m2.on_token is None


# ---------------------------------------------------------------------------
# Usage aggregation
# ---------------------------------------------------------------------------

def test_usage_empty_before_any_call():
    llm = FallbackLLM([SimulatedLLM()])
    usage = llm.get_usage_summary()
    assert usage["total_input_tokens"]  == 0
    assert usage["total_output_tokens"] == 0

def test_usage_from_successful_primary():
    llm = FallbackLLM([SimulatedLLM()])
    llm.system("You are a PM. Output JSON tickets array.", "Build x")
    usage = llm.get_usage_summary()
    assert usage["total_input_tokens"]  > 0
    assert usage["total_output_tokens"] > 0

def test_usage_from_fallback_model():
    """When primary fails, tokens from the fallback model are counted."""
    llm = FallbackLLM([FailingLLM(), SimulatedLLM()])
    llm.system("You are a PM. Output JSON tickets array.", "Build x")
    usage = llm.get_usage_summary()
    assert usage["total_input_tokens"]  > 0

def test_usage_aggregates_across_multiple_calls():
    primary = SimulatedLLM()
    llm = FallbackLLM([primary])
    llm.system("You are a PM. Output JSON tickets array.", "Build x")
    llm.system("You are a PM. Output JSON tickets array.", "Build y")
    usage = llm.get_usage_summary()
    assert len(usage["by_agent"]) == 2  # two entries

def test_complete_falls_back_too():
    """FallbackLLM.complete() also falls back (used for direct calls)."""
    llm = FallbackLLM([FailingLLM(), SimulatedLLM()])
    msgs = [Message(role="system", content="You are a PM. Output JSON tickets array."),
            Message(role="user", content="Build x")]
    result = llm.complete(msgs)
    assert result


# ---------------------------------------------------------------------------
# Fluent API — BaseLLM.with_fallback()
# ---------------------------------------------------------------------------

def test_with_fallback_returns_fallback_llm():
    primary   = SimulatedLLM()
    secondary = SimulatedLLM()
    llm = primary.with_fallback(secondary)
    assert isinstance(llm, FallbackLLM)
    assert llm._models[0] is primary
    assert llm._models[1] is secondary

def test_with_fallback_single():
    llm = SimulatedLLM().with_fallback(SimulatedLLM())
    result = llm.system("You are a PM. Output JSON tickets array.", "Build x")
    assert result

def test_with_fallback_chain_of_three():
    llm = FailingLLM().with_fallback(FailingLLM(), SimulatedLLM())
    result = llm.system("You are a PM. Output JSON tickets array.", "Build x")
    assert result
    assert len(llm.fallback_events()) == 2


# ---------------------------------------------------------------------------
# Integration with teams
# ---------------------------------------------------------------------------

def test_dev_team_accepts_fallback_llm():
    from antcrew.teams.dev_team import DevTeam
    llm = FallbackLLM([SimulatedLLM()])
    team = DevTeam(model=llm)
    state = team.run("Build a login module")
    assert state.get("code_artifacts")

def test_dev_team_uses_fallback_transparently():
    """Team should work even when primary always fails."""
    from antcrew.teams.dev_team import DevTeam
    llm = FallbackLLM([FailingLLM(), SimulatedLLM()])
    team = DevTeam(model=llm)
    state = team.run("Build a login module")
    # Pipeline completed — fallback was used silently
    assert state.get("prd") is not None
    assert llm.fallback_events()  # at least one fallback happened


# ---------------------------------------------------------------------------
# Cost guard (max_cost_usd)
# ---------------------------------------------------------------------------

def test_cost_limit_raises_cost_limit_exceeded():
    from antcrew.core.exceptions import CostLimitExceeded
    llm = FallbackLLM([SimulatedLLM()])
    llm.max_cost_usd = 0.0  # limit of $0 — any spend triggers it
    with pytest.raises(CostLimitExceeded):
        llm.system("You are a PM. Output JSON tickets array.", "Build x")


def test_cost_limit_not_raised_before_threshold():
    from antcrew.core.exceptions import CostLimitExceeded
    llm = FallbackLLM([SimulatedLLM()])
    llm.max_cost_usd = 999.0
    result = llm.system("You are a PM. Output JSON tickets array.", "Build x")
    assert result  # no exception — limit not reached


def test_cost_guard_uses_aggregate_across_models():
    """Limit checks total spend even when split across primary + fallback."""
    from antcrew.core.exceptions import CostLimitExceeded
    llm = FallbackLLM([FailingLLM(), SimulatedLLM()])
    llm.max_cost_usd = 0.0
    with pytest.raises(CostLimitExceeded):
        llm.system("You are a PM. Output JSON tickets array.", "Build x")


# ---------------------------------------------------------------------------
# Trace propagation
# ---------------------------------------------------------------------------

def test_trace_propagates_to_inner_models():
    """Setting trace on FallbackLLM propagates to all inner models."""
    from antcrew.trace import TraceLog
    import tempfile, os
    m1, m2 = SimulatedLLM(), SimulatedLLM()
    llm = FallbackLLM([m1, m2])
    with tempfile.TemporaryDirectory() as d:
        tlog = TraceLog(os.path.join(d, "t.db"))
        llm.trace = tlog
        assert m1.trace is tlog
        assert m2.trace is tlog
        tlog.close()


def test_trace_run_id_propagates_to_inner_models():
    m1, m2 = SimulatedLLM(), SimulatedLLM()
    llm = FallbackLLM([m1, m2])
    llm._trace_run_id = "run-abc"
    assert m1._trace_run_id == "run-abc"
    assert m2._trace_run_id == "run-abc"


def test_trace_none_clears_on_all_models():
    from antcrew.trace import TraceLog
    import tempfile, os
    m1, m2 = SimulatedLLM(), SimulatedLLM()
    llm = FallbackLLM([m1, m2])
    with tempfile.TemporaryDirectory() as d:
        tlog = TraceLog(os.path.join(d, "t.db"))
        llm.trace = tlog
        llm.trace = None
        assert m1.trace is None
        assert m2.trace is None
        tlog.close()


# ---------------------------------------------------------------------------
# Top-level import
# ---------------------------------------------------------------------------

def test_fallback_importable_from_antcrew():
    from antcrew import FallbackLLM as FL
    assert FL is FallbackLLM
