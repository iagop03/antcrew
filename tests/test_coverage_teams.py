"""Coverage tests for teams: DevTeam + FullStackTeam paths that aren't hit
by the integration tests — trace_log, memory, sandbox runner, max_cost_usd."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from antcrew.models.simulated import SimulatedLLM
from antcrew.teams.dev_team import DevTeam
from antcrew.teams.fullstack_team import FullStackTeam
from antcrew.memory.store import InMemoryMemory
from antcrew.trace import TraceLog


def _trace(tmp_path: Path) -> TraceLog:
    return TraceLog(str(tmp_path / "trace.db"))


# ── DevTeam trace_log path ────────────────────────────────────────────────────

class TestDevTeamTraceLog:
    def test_run_with_trace_log(self, tmp_path):
        tl = _trace(tmp_path)
        team = DevTeam(model=SimulatedLLM(), trace_log=tl)
        result = team.run("Add auth module")
        assert result is not None
        stats = tl.get_stats()
        assert stats["total_runs"] == 1
        assert stats["done_runs"] == 1

    def test_trace_log_records_cost(self, tmp_path):
        tl = _trace(tmp_path)
        team = DevTeam(model=SimulatedLLM(), trace_log=tl)
        team.run("Add billing")
        stats = tl.get_stats()
        assert "total_cost_usd" in stats

    def test_trace_log_error_path(self, tmp_path):
        tl = _trace(tmp_path)
        team = DevTeam(model=SimulatedLLM(), trace_log=tl)
        # Force an error in invoke to exercise the except branch
        with patch.object(team._supervisor, "build") as mock_build:
            mock_app = MagicMock()
            mock_app.invoke.side_effect = RuntimeError("boom")
            mock_build.return_value = mock_app
            with pytest.raises(RuntimeError, match="boom"):
                team.run("crash")
        stats = tl.get_stats()
        assert stats["total_runs"] == 1
        assert stats["error_runs"] == 1


# ── DevTeam memory path ───────────────────────────────────────────────────────

class TestDevTeamMemory:
    def test_run_with_memory_calls_store(self):
        mem = InMemoryMemory()
        called = []
        original = mem.store_run
        def _spy(state):
            called.append(state)
            return original(state)
        mem.store_run = _spy
        team = DevTeam(model=SimulatedLLM(), memory=mem)
        result = team.run("Add auth module")
        assert result is not None
        assert len(called) == 1

    def test_memory_agents_receive_memory(self):
        mem = InMemoryMemory()
        team = DevTeam(model=SimulatedLLM(), memory=mem)
        for agent in team._agents.values():
            assert agent.memory is mem


# ── DevTeam sandbox runner path ───────────────────────────────────────────────

class TestDevTeamSandboxRunner:
    def test_sandbox_runner_called_when_test_artifacts(self):
        mock_runner = MagicMock()
        mock_runner.run.return_value = [{"test": "passed"}]
        team = DevTeam(model=SimulatedLLM(), runner=mock_runner)
        result = team.run("Add auth module")
        # SimulatedLLM produces test_artifacts → runner.run should be called
        if result.state.get("test_artifacts"):
            mock_runner.run.assert_called_once()

    def test_sandbox_runner_exception_logged(self):
        mock_runner = MagicMock()
        mock_runner.run.side_effect = RuntimeError("sandbox error")
        team = DevTeam(model=SimulatedLLM(), runner=mock_runner)
        # Should not raise — exception is swallowed with a warning
        result = team.run("Add auth module")
        assert result is not None


# ── FullStackTeam extra init paths ────────────────────────────────────────────

class TestFullStackTeamInit:
    def test_max_cost_usd_set_on_llm(self):
        llm = SimulatedLLM()
        team = FullStackTeam(model=llm, max_cost_usd=0.50)
        assert team.llm.max_cost_usd == 0.50

    def test_memory_propagated_to_agents(self):
        mem = InMemoryMemory()
        team = FullStackTeam(model=SimulatedLLM(), memory=mem)
        for agent in team._agents.values():
            assert agent.memory is mem

    def test_project_dirs_accepted(self):
        team = FullStackTeam(
            model=SimulatedLLM(),
            project_dirs={"api": "/tmp/api", "frontend": "/tmp/frontend"},
        )
        assert team.project_dirs is not None

    def test_sprint_size_propagated(self):
        team = FullStackTeam(model=SimulatedLLM(), sprint_size=6)
        sp_agent = team._agents["sprint_planner"]
        assert sp_agent.sprint_size == 6


# ── FullStackTeam trace_log path ──────────────────────────────────────────────

class TestFullStackTeamTraceLog:
    def test_run_with_trace_log(self, tmp_path):
        tl = _trace(tmp_path)
        team = FullStackTeam(model=SimulatedLLM(), trace_log=tl)
        result = team.run("Add auth endpoint")
        assert result is not None
        stats = tl.get_stats()
        assert stats["total_runs"] == 1

    def test_trace_log_error_marks_run(self, tmp_path):
        tl = _trace(tmp_path)
        team = FullStackTeam(model=SimulatedLLM(), trace_log=tl)
        with patch.object(team._supervisor, "build") as mb:
            mock_app = MagicMock()
            mock_app.invoke.side_effect = RuntimeError("fail")
            mb.return_value = mock_app
            with pytest.raises(RuntimeError):
                team.run("crash")
        stats = tl.get_stats()
        assert stats["error_runs"] == 1


# ── FullStackTeam memory path ─────────────────────────────────────────────────

class TestFullStackTeamMemory:
    def test_run_with_memory_stores_state(self):
        mem = InMemoryMemory()
        called = []
        original = mem.store_run
        def _spy(state):
            called.append(state)
            return original(state)
        mem.store_run = _spy
        team = FullStackTeam(model=SimulatedLLM(), memory=mem)
        result = team.run("Add billing endpoint")
        assert result is not None
        assert len(called) == 1


# ── AGENT_ARTIFACT dict coverage ─────────────────────────────────────────────

class TestAgentArtifact:
    def test_all_agents_in_artifact_map(self):
        from antcrew.teams.base import AGENT_ARTIFACT
        expected = {"business_analyst", "pm", "backend_dev", "frontend_dev",
                    "qa", "reviewer", "researcher", "devops", "doc_writer",
                    "writer", "idea", "copywriter", "editor"}
        for key in expected:
            assert key in AGENT_ARTIFACT, f"{key!r} missing from AGENT_ARTIFACT"

    def test_artifact_map_values_are_tuples(self):
        from antcrew.teams.base import AGENT_ARTIFACT
        for name, val in AGENT_ARTIFACT.items():
            assert isinstance(val, tuple), f"{name} value is not a tuple"
            assert len(val) == 2
