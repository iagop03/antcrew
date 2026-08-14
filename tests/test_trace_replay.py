"""Tests for TraceLog.replay() and _llm_usage_totals fix."""
import asyncio
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from antcrew.trace import TraceLog, ReplayError
from antcrew.core.events import _llm_usage_totals


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_trace(tmp_path: Path, *, full_trace: bool = True) -> TraceLog:
    return TraceLog(tmp_path / "trace.db", full_trace=full_trace)


def _seed_call(trace: TraceLog, run_id: str, *, prompt: str = "sys", user: str = "usr",
               response: str = "reply", agent: str = "ba") -> int:
    return trace.record_call(
        run_id=run_id,
        agent_name=agent,
        duration_ms=100.0,
        input_tokens=10,
        output_tokens=5,
        cost_usd=0.001,
        prompt_full=prompt,
        response_full=response,
        user_full=user,
    )


def _fake_llm(response: str = "new reply") -> MagicMock:
    llm = MagicMock()
    llm.system.return_value = response
    llm.get_usage_summary.return_value = {
        "total_input_tokens": 10,
        "total_output_tokens": 5,
        "total_cost_usd": 0.001,
    }
    return llm


# ---------------------------------------------------------------------------
# _llm_usage_totals — Fix 1
# ---------------------------------------------------------------------------

class TestLlmUsageTotals:
    def test_reads_agent_dot_llm(self):
        """BaseAgent stores LLM as self.llm — must resolve correctly."""
        llm = MagicMock()
        llm.get_usage_summary.return_value = {
            "total_input_tokens": 42,
            "total_output_tokens": 17,
            "total_cost_usd": 0.005,
        }
        agent = MagicMock(spec=[])
        agent.llm = llm

        result = _llm_usage_totals(agent)
        assert result["tokens_in"] == 42
        assert result["tokens_out"] == 17
        assert result["cost_usd"] == pytest.approx(0.005)

    def test_reads_via_kb_proxy(self):
        """_KBProxy wraps ._agent.llm — must walk through proxy."""
        llm = MagicMock()
        llm.get_usage_summary.return_value = {
            "total_input_tokens": 7,
            "total_output_tokens": 3,
            "total_cost_usd": 0.002,
        }
        inner_agent = MagicMock(spec=[])
        inner_agent.llm = llm

        kb_proxy = MagicMock(spec=[])
        kb_proxy._agent = inner_agent
        # _KBProxy has no .llm of its own

        result = _llm_usage_totals(kb_proxy)
        assert result["tokens_in"] == 7
        assert result["tokens_out"] == 3

    def test_returns_zeros_when_no_llm(self):
        agent = MagicMock(spec=[])  # no .llm, no ._llm, no ._agent
        result = _llm_usage_totals(agent)
        assert result == {"tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0}

    def test_returns_zeros_on_exception(self):
        llm = MagicMock()
        llm.get_usage_summary.side_effect = RuntimeError("api down")
        agent = MagicMock(spec=[])
        agent.llm = llm
        result = _llm_usage_totals(agent)
        assert result == {"tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0}


# ---------------------------------------------------------------------------
# TraceLog.replay — Fix 3
# ---------------------------------------------------------------------------

class TestTraceLogReplay:
    def test_replay_returns_original_and_replayed(self, tmp_path):
        trace = _make_trace(tmp_path)
        run_id = trace.begin_run(thread_id="t1", request="build auth", team="DevTeam")
        call_id = _seed_call(trace, run_id, prompt="You are BA", user="Build login", response="PRD v1")
        trace.end_run(run_id)

        llm = _fake_llm("PRD v2")
        result = trace.replay(call_id, llm)

        assert result["call_id"] == call_id
        assert result["agent_name"] == "ba"
        assert result["original"] == "PRD v1"
        assert result["replayed"] == "PRD v2"
        assert result["matched"] is False
        llm.system.assert_called_once_with("You are BA", "Build login")

    def test_replay_matched_when_same_response(self, tmp_path):
        trace = _make_trace(tmp_path)
        run_id = trace.begin_run(thread_id="t2", request="r", team="DevTeam")
        call_id = _seed_call(trace, run_id, response="identical")
        trace.end_run(run_id)

        llm = _fake_llm("identical")
        result = trace.replay(call_id, llm)
        assert result["matched"] is True

    def test_replay_raises_for_missing_call_id(self, tmp_path):
        trace = _make_trace(tmp_path)
        llm = _fake_llm()
        with pytest.raises(ReplayError, match="not found"):
            trace.replay(9999, llm)

    def test_replay_raises_when_full_trace_false(self, tmp_path):
        trace = _make_trace(tmp_path, full_trace=False)
        run_id = trace.begin_run(thread_id="t3", request="r", team="DevTeam")
        call_id = trace.record_call(
            run_id=run_id, agent_name="ba", duration_ms=50.0,
            prompt_snippet="You are", response_snippet="done",
        )
        with pytest.raises(ReplayError, match="full_trace=True"):
            trace.replay(call_id, _fake_llm())

    def test_replay_all_runs_all_calls(self, tmp_path):
        trace = _make_trace(tmp_path)
        run_id = trace.begin_run(thread_id="t4", request="r", team="DevTeam")
        _seed_call(trace, run_id, agent="ba", response="r1")
        _seed_call(trace, run_id, agent="pm", response="r2")
        trace.end_run(run_id)

        call_count = 0
        original_system = None

        def _system(prompt, user):
            nonlocal call_count, original_system
            call_count += 1
            original_system = prompt
            return f"new_{call_count}"

        llm = MagicMock()
        llm.system.side_effect = _system
        llm.get_usage_summary.return_value = {
            "total_input_tokens": 0, "total_output_tokens": 0, "total_cost_usd": 0.0,
        }

        results = trace.replay_all(run_id, llm)
        assert len(results) == 2
        assert call_count == 2
        assert results[0]["agent_name"] == "ba"
        assert results[1]["agent_name"] == "pm"

    def test_replay_all_raises_for_unknown_run(self, tmp_path):
        trace = _make_trace(tmp_path)
        with pytest.raises(ReplayError, match="No agent_calls"):
            trace.replay_all("nonexistent-run-id", _fake_llm())

    def test_user_full_stored_when_full_trace(self, tmp_path):
        trace = _make_trace(tmp_path)
        run_id = trace.begin_run(thread_id="t5", request="r", team="DevTeam")
        call_id = _seed_call(trace, run_id, user="Build JWT auth")
        detail = trace.get_call_detail(call_id)
        assert detail["user_full"] == "Build JWT auth"

    def test_user_full_empty_when_not_full_trace(self, tmp_path):
        trace = _make_trace(tmp_path, full_trace=False)
        run_id = trace.begin_run(thread_id="t6", request="r", team="DevTeam")
        call_id = trace.record_call(
            run_id=run_id, agent_name="ba", duration_ms=10.0,
            prompt_full="sys", response_full="resp", user_full="user msg",
        )
        detail = trace.get_call_detail(call_id)
        assert detail["user_full"] == ""

    def test_replay_error_exported(self):
        from antcrew import ReplayError as RE
        assert issubclass(RE, RuntimeError)
