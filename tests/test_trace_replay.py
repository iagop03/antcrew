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

    def test_hitl_record_and_read(self, tmp_path):
        """record_hitl() inserts a row and get_hitl_decisions() returns it."""
        trace = _make_trace(tmp_path)
        run_id = trace.begin_run(thread_id="t7", request="build auth", team="DevTeam")
        trace.record_hitl(
            run_id=run_id,
            step="backend_dev",
            decision="approved",
            reviewer_id="alice@example.com",
            reason="LGTM",
        )
        trace.end_run(run_id)

        decisions = trace.get_hitl_decisions(run_id)
        assert len(decisions) == 1
        d = decisions[0]
        assert d["run_id"] == run_id
        assert d["step"] == "backend_dev"
        assert d["decision"] == "approved"
        assert d["reviewer_id"] == "alice@example.com"
        assert d["reason"] == "LGTM"

    def test_hitl_multiple_steps(self, tmp_path):
        """Multiple HITL decisions for one run all appear in order."""
        trace = _make_trace(tmp_path)
        run_id = trace.begin_run(thread_id="t8", request="r", team="DevTeam")
        trace.record_hitl(run_id=run_id, step="pm", decision="approved")
        trace.record_hitl(run_id=run_id, step="qa", decision="rejected", reason="tests missing")
        trace.end_run(run_id)

        decisions = trace.get_hitl_decisions(run_id)
        assert len(decisions) == 2
        assert decisions[0]["step"] == "pm"
        assert decisions[1]["step"] == "qa"
        assert decisions[1]["reason"] == "tests missing"

    def test_hitl_empty_for_run_without_decisions(self, tmp_path):
        """Runs without HITL events return an empty list."""
        trace = _make_trace(tmp_path)
        run_id = trace.begin_run(thread_id="t9", request="r", team="DevTeam")
        trace.end_run(run_id)
        assert trace.get_hitl_decisions(run_id) == []

    def test_hitl_table_created_on_existing_db(self, tmp_path):
        """Opening a pre-existing TraceDB without hitl_decisions creates the table."""
        import sqlite3
        db_path = tmp_path / "old.db"
        # Create a minimal DB without the hitl_decisions table
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute("""
                CREATE TABLE runs (id TEXT PRIMARY KEY, thread_id TEXT NOT NULL,
                request TEXT NOT NULL, team TEXT NOT NULL, started_at TEXT NOT NULL,
                ended_at TEXT, cost_usd REAL, status TEXT NOT NULL DEFAULT 'running')
            """)
            conn.execute("""
                CREATE TABLE agent_calls (id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL, agent_name TEXT NOT NULL, started_at TEXT NOT NULL,
                duration_ms REAL NOT NULL, input_tokens INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0, cost_usd REAL DEFAULT 0,
                prompt_snippet TEXT DEFAULT '', response_snippet TEXT DEFAULT '',
                prompt_full TEXT DEFAULT '', response_full TEXT DEFAULT '',
                user_full TEXT DEFAULT '')
            """)
            conn.commit()
        # Opening with TraceLog should migrate the table in
        trace = TraceLog(db_path)
        run_id = trace.begin_run(thread_id="t10", request="r", team="DevTeam")
        # Should not raise — table is now present
        trace.record_hitl(run_id=run_id, step="ba", decision="approved")
        assert len(trace.get_hitl_decisions(run_id)) == 1


# ---------------------------------------------------------------------------
# SimulatedLLM team integration — no real LLM tokens consumed
# ---------------------------------------------------------------------------

class TestReplayWithSimulatedTeam:
    """Integration tests that run a full DevTeam pipeline with SimulatedLLM.

    No real API calls are made — SimulatedLLM returns pre-baked fixture JSON.
    The tests verify that TraceLog records calls end-to-end when injected via
    trace_log= and that record_hitl integrates with the same DB cleanly.
    """

    def test_simulated_run_records_trace(self, tmp_path):
        """A DevTeam run with trace_log= produces run + agent_calls rows."""
        from antcrew import DevTeam, SimulatedLLM

        trace = TraceLog(tmp_path / "trace.db", full_trace=True)
        team = DevTeam(model=SimulatedLLM(), trace_log=trace)
        team.run("Build a login module", thread_id="sim-1")

        runs = trace.list_runs()
        assert len(runs) >= 1, "at least one run row should have been created"
        run_id = runs[0]["id"]
        calls = trace.get_calls(run_id)
        assert len(calls) >= 1, "at least one agent_call should have been recorded"

    def test_simulated_run_no_tokens_consumed(self, tmp_path):
        """SimulatedLLM tracks usage without making real API calls."""
        from antcrew import DevTeam, SimulatedLLM

        llm = SimulatedLLM()
        trace = TraceLog(tmp_path / "trace.db")
        team = DevTeam(model=llm, trace_log=trace)
        team.run("Add password reset", thread_id="sim-2")

        summary = llm.get_usage_summary()
        # SimulatedLLM accumulates approximate token counts but spends $0
        assert summary.get("total_cost_usd", 0.0) == pytest.approx(0.0)

    def test_simulated_run_with_hitl_decision(self, tmp_path):
        """record_hitl() and a DevTeam run coexist in the same TraceLog DB."""
        from antcrew import DevTeam, SimulatedLLM

        trace = TraceLog(tmp_path / "trace.db")
        team = DevTeam(model=SimulatedLLM(), trace_log=trace)
        team.run("Build search feature", thread_id="sim-3")

        runs = trace.list_runs()
        assert runs, "expected at least one run"
        run_id = runs[0]["id"]

        # Simulate a HITL review outcome recorded against this run
        trace.record_hitl(
            run_id=run_id,
            step="backend_dev",
            decision="approved",
            reviewer_id="reviewer@example.com",
        )

        decisions = trace.get_hitl_decisions(run_id)
        assert len(decisions) == 1
        assert decisions[0]["decision"] == "approved"
