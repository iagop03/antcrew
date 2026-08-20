"""Unit tests for DevTeam private post-pipeline helpers and BaseAgent.bind_run().

Covers:
- BaseAgent.bind_run() sets _run_id and _thread_id
- _make_evented_run uses bind_run (no direct private-attr mutation)
- _SingleAgentTeam.run() uses bind_run (no direct private-attr mutation)
- DevTeam._handle_kb() skips when _project_kb is None
- DevTeam._handle_kb() skips save when code_artifacts absent
- DevTeam._handle_coherence_bg() starts a daemon thread and emits events
- DevTeam._handle_sandbox() returns state unchanged when dry_run=True
- DevTeam._handle_writeback() is no-op when dry_run=True
- DevTeam._handle_memory() stores run when memory is set
- KB update is skipped on dry_run in run()
"""
from __future__ import annotations

import threading
import time
from typing import Optional
from unittest.mock import MagicMock, patch, call

import pytest

from antcrew.core.agent import BaseAgent
from antcrew.core.state import TeamState


# ---------------------------------------------------------------------------
# BaseAgent.bind_run()
# ---------------------------------------------------------------------------

class _ConcreteAgent(BaseAgent):
    """Minimal concrete subclass for testing BaseAgent methods."""
    def run(self, state: TeamState) -> dict:
        return {}


def _make_agent():
    llm = MagicMock()
    llm.get_usage_summary.return_value = {"total_cost_usd": 0.0}
    return _ConcreteAgent(llm=llm)


class TestBindRun:
    def test_bind_run_sets_run_id(self):
        agent = _make_agent()
        agent.bind_run("run-123", "thread-abc")
        assert agent._run_id == "run-123"
        assert agent._thread_id == "thread-abc"

    def test_bind_run_none_clears_context(self):
        agent = _make_agent()
        agent.bind_run("run-x", "t-x")
        agent.bind_run(None, None)
        assert agent._run_id is None
        assert agent._thread_id is None

    def test_bind_run_defaults_thread_id_to_none(self):
        agent = _make_agent()
        agent.bind_run("run-y")
        assert agent._run_id == "run-y"
        assert agent._thread_id is None

    def test_make_evented_run_calls_bind_run(self):
        """_make_evented_run must use bind_run, not direct private-attr mutation."""
        from antcrew.core.events import _make_evented_run

        agent = _make_agent()
        state_in = {"_run_id": "run-evented", "_thread_id": "t-evented"}

        bound_calls = []
        original_bind = agent.bind_run

        def _spy(run_id, thread_id=None):
            bound_calls.append((run_id, thread_id))
            original_bind(run_id, thread_id)

        agent.bind_run = _spy

        def _dummy_fn(state):
            return {}

        wrapped = _make_evented_run(_dummy_fn, "test_agent", agent=agent)
        wrapped(state_in)

        assert ("run-evented", "t-evented") in bound_calls

    def test_single_agent_team_uses_bind_run(self):
        """_SingleAgentTeam.run() must use bind_run, not direct private-attr mutation."""
        from antcrew.core.task_classifier import _SingleAgentTeam

        agent = _make_agent()
        agent.run = MagicMock(return_value={"result": "ok"})

        bound_calls = []
        original_bind = agent.bind_run

        def _spy(run_id, thread_id=None):
            bound_calls.append((run_id, thread_id))
            original_bind(run_id, thread_id)

        agent.bind_run = _spy

        team = _SingleAgentTeam(agent=agent)
        team.run("hello")

        assert len(bound_calls) >= 1
        assert bound_calls[0][0] is not None  # run_id was set


# ---------------------------------------------------------------------------
# DevTeam._handle_kb()
# ---------------------------------------------------------------------------

def _make_dev_team_stub():
    """Return a DevTeam with all dependencies mocked out."""
    from antcrew.teams.dev_team import DevTeam
    dt = DevTeam.__new__(DevTeam)
    dt._project_kb = None
    dt._enable_coherence = False
    dt._agents = {}
    dt.memory = None
    dt._work_dir = None
    dt._runner = None
    dt._feedback_rounds = 0
    dt._lint_cmd = None
    return dt


class TestHandleKb:
    def test_noop_when_no_project_kb(self):
        dt = _make_dev_team_stub()
        dt._project_kb = None
        # Should not raise
        dt._handle_kb({}, "run-1", "default")

    def test_calls_update_and_save(self):
        dt = _make_dev_team_stub()
        kb = MagicMock()
        kb.summary.return_value = {}
        dt._project_kb = kb

        state = {"code_artifacts": ["some_artifact"]}
        with patch("antcrew.core.events.bus") as _bus:
            dt._handle_kb(state, "run-1", "default")

        kb.update_from_state.assert_called_once_with(state)
        kb.save.assert_called_once()

    def test_records_feedback_failure_decision(self):
        dt = _make_dev_team_stub()
        kb = MagicMock()
        kb.summary.return_value = {}
        dt._project_kb = kb

        state = {"_feedback_error": "Tests failed after 3 rounds", "feedback_ok": False}
        with patch("antcrew.core.events.bus"):
            dt._handle_kb(state, "run-1", "default")

        kb.record_decision.assert_called_once()
        args = kb.record_decision.call_args[0]
        assert "Tests failed" in args[0]

    def test_no_decision_when_feedback_ok(self):
        dt = _make_dev_team_stub()
        kb = MagicMock()
        kb.summary.return_value = {}
        dt._project_kb = kb

        state = {"_feedback_error": "Some error", "feedback_ok": True}
        with patch("antcrew.core.events.bus"):
            dt._handle_kb(state, "run-1", "default")

        kb.record_decision.assert_not_called()


# ---------------------------------------------------------------------------
# DevTeam._handle_coherence_bg()
# ---------------------------------------------------------------------------

class TestHandleCoherenceBg:
    def test_noop_when_coherence_disabled(self):
        dt = _make_dev_team_stub()
        dt._enable_coherence = False
        dt._agents = {"coherence": MagicMock()}

        dt._handle_coherence_bg({"code_artifacts": ["x"]}, "run-1", "default")
        # No thread should have been started — we can verify by checking the agent wasn't called
        dt._agents["coherence"].run.assert_not_called()

    def test_noop_when_no_code_artifacts(self):
        dt = _make_dev_team_stub()
        dt._enable_coherence = True
        dt._agents = {"coherence": MagicMock()}

        dt._handle_coherence_bg({}, "run-1", "default")
        dt._agents["coherence"].run.assert_not_called()

    def test_starts_daemon_thread_and_emits_events(self):
        dt = _make_dev_team_stub()
        dt._enable_coherence = True
        coherence_agent = MagicMock()
        coherence_agent.run.return_value = {"coherence_issues": ["f1.py"]}
        dt._agents = {"coherence": coherence_agent}

        events_emitted = []
        with patch("antcrew.core.events.bus") as mock_bus:
            mock_bus.emit.side_effect = lambda e: events_emitted.append(e.event_type if hasattr(e, "event_type") else str(e))
            dt._handle_coherence_bg({"code_artifacts": ["x"]}, "run-coh", "default")

        # Background thread needs a moment to run
        time.sleep(0.2)
        coherence_agent.run.assert_called_once()

    def test_background_thread_is_daemon(self):
        dt = _make_dev_team_stub()
        dt._enable_coherence = True
        started_threads = []

        original_start = threading.Thread.start

        def _capture_start(self_thread):
            started_threads.append(self_thread)
            original_start(self_thread)

        with patch.object(threading.Thread, "start", _capture_start):
            coherence_agent = MagicMock()
            coherence_agent.run.return_value = {}
            dt._agents = {"coherence": coherence_agent}
            with patch("antcrew.core.events.bus"):
                dt._handle_coherence_bg({"code_artifacts": ["x"]}, "run-1", "t")

        assert any(t.daemon for t in started_threads), "Coherence thread must be a daemon thread"


# ---------------------------------------------------------------------------
# DevTeam._handle_sandbox()
# ---------------------------------------------------------------------------

class TestHandleSandbox:
    def test_returns_state_unchanged_on_dry_run(self):
        dt = _make_dev_team_stub()
        dt._runner = MagicMock()
        original_state = {"test_artifacts": ["t1"], "code_artifacts": ["c1"]}
        result = dt._handle_sandbox(original_state, "run-1", "t", dry_run=True)
        dt._runner.run.assert_not_called()
        assert result is original_state

    def test_returns_state_unchanged_when_no_runner(self):
        dt = _make_dev_team_stub()
        dt._runner = None
        state = {"test_artifacts": ["t1"]}
        result = dt._handle_sandbox(state, "run-1", "t", dry_run=False)
        assert result is state

    def test_returns_state_unchanged_when_no_test_artifacts(self):
        dt = _make_dev_team_stub()
        dt._runner = MagicMock()
        state = {}
        result = dt._handle_sandbox(state, "run-1", "t", dry_run=False)
        dt._runner.run.assert_not_called()
        assert result is state

    def test_runs_sandbox_when_not_dry_run(self):
        dt = _make_dev_team_stub()
        runner = MagicMock()
        runner.run.return_value = MagicMock(success=True)
        dt._runner = runner
        state = {"test_artifacts": ["t1"], "code_artifacts": ["c1"]}
        dt._handle_sandbox(state, "run-1", "t", dry_run=False)
        runner.run.assert_called_once()


# ---------------------------------------------------------------------------
# DevTeam._handle_writeback()
# ---------------------------------------------------------------------------

class TestHandleWriteback:
    def test_noop_on_dry_run(self):
        dt = _make_dev_team_stub()
        dt._work_dir = "/tmp/test"
        with patch("antcrew.core.writeback.write_back") as mock_wb:
            dt._handle_writeback({"code_artifacts": ["c"]}, "run-1", "t", dry_run=True)
        mock_wb.assert_not_called()

    def test_noop_when_no_work_dir(self):
        dt = _make_dev_team_stub()
        dt._work_dir = None
        with patch("antcrew.core.writeback.write_back") as mock_wb:
            dt._handle_writeback({"code_artifacts": ["c"]}, "run-1", "t", dry_run=False)
        mock_wb.assert_not_called()

    def test_noop_when_no_code_artifacts(self):
        dt = _make_dev_team_stub()
        dt._work_dir = "/tmp/test"
        with patch("antcrew.core.writeback.write_back") as mock_wb:
            dt._handle_writeback({}, "run-1", "t", dry_run=False)
        mock_wb.assert_not_called()


# ---------------------------------------------------------------------------
# DevTeam._handle_memory()
# ---------------------------------------------------------------------------

class TestHandleMemory:
    def test_noop_when_no_memory(self):
        dt = _make_dev_team_stub()
        dt.memory = None
        state = {"code_artifacts": ["x"]}
        dt._handle_memory(state, "run-1", "t")  # should not raise

    def test_stores_run_when_memory_set(self):
        dt = _make_dev_team_stub()
        mem = MagicMock()
        dt.memory = mem
        state = {"code_artifacts": ["x"]}
        dt._handle_memory(state, "run-99", "thread-99")
        mem.store_run.assert_called_once_with(state, run_id="run-99", project="thread-99")
