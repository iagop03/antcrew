"""Coverage tests for ContentTeam, ResearchTeam (trace_log + memory paths)
and AGENT_REGISTRY entries for sprint_planner / doc_writer."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from antcrew.memory.store import InMemoryMemory
from antcrew.models.simulated import SimulatedLLM
from antcrew.teams.content_team import ContentTeam
from antcrew.teams.research_team import ResearchTeam
from antcrew.trace import TraceLog


def _trace(tmp_path: Path) -> TraceLog:
    return TraceLog(str(tmp_path / "trace.db"))


# ── AGENT_REGISTRY — sprint_planner / doc_writer ─────────────────────────────

class TestAgentRegistry:
    def test_sprint_planner_in_registry(self):
        from antcrew.agents.registry import AGENT_REGISTRY
        assert "sprint_planner" in AGENT_REGISTRY

    def test_doc_writer_in_registry(self):
        from antcrew.agents.registry import AGENT_REGISTRY
        assert "doc_writer" in AGENT_REGISTRY

    def test_get_agent_class_sprint_planner(self):
        from antcrew.agents.registry import get_agent_class
        from antcrew.agents.sprint_planner import SprintPlannerAgent
        cls = get_agent_class("sprint_planner")
        assert cls is SprintPlannerAgent

    def test_get_agent_class_doc_writer(self):
        from antcrew.agents.doc_writer import DocWriterAgent
        from antcrew.agents.registry import get_agent_class
        cls = get_agent_class("doc_writer")
        assert cls is DocWriterAgent

    def test_instantiate_sprint_planner(self):
        from antcrew.agents.registry import instantiate_agent
        agent = instantiate_agent("sprint_planner", SimulatedLLM())
        assert agent is not None
        assert agent.name == "sprint_planner"

    def test_instantiate_doc_writer(self):
        from antcrew.agents.registry import instantiate_agent
        agent = instantiate_agent("doc_writer", SimulatedLLM())
        assert agent is not None
        assert agent.name == "doc_writer"

    def test_agents_cmd_lists_sprint_planner(self):
        import json

        from typer.testing import CliRunner

        from antcrew.cli._app import app
        runner = CliRunner()
        result = runner.invoke(app, ["agents", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        names = {e["name"] for e in data}
        assert "sprint_planner" in names
        assert "doc_writer" in names


# ── watch --repo-index tests ──────────────────────────────────────────────────

class TestWatchRepoIndex:
    def _invoke(self, *args):
        from typer.testing import CliRunner

        from antcrew.cli._app import app
        return CliRunner().invoke(app, list(args))

    def test_nonexistent_repo_index_exits_1(self, tmp_path):
        spec = tmp_path / "spec.md"
        spec.write_text("Build x")
        result = self._invoke(
            "watch", str(spec),
            "--team", "fullstack",
            "--model", "simulated",
            "--repo-index", str(tmp_path / "nope"),
        )
        assert result.exit_code == 1
        assert "not a directory" in result.output.lower()

    def test_repo_index_warns_for_non_fullstack(self, tmp_path, monkeypatch):
        import builtins
        real = builtins.__import__
        def _block(name, *a, **kw):
            if "watchdog" in name:
                raise ImportError("no watchdog")
            return real(name, *a, **kw)
        monkeypatch.setattr(builtins, "__import__", _block)

        spec = tmp_path / "spec.md"
        spec.write_text("Build x")
        result = self._invoke(
            "watch", str(spec),
            "--team", "dev",
            "--model", "simulated",
            "--repo-index", str(tmp_path),
        )
        assert "Warning" in result.output
        assert "fullstack" in result.output.lower()
        assert result.exit_code == 1  # watchdog blocked

    def test_repo_index_file_not_dir_exits_1(self, tmp_path):
        spec = tmp_path / "spec.md"
        spec.write_text("Build x")
        f = tmp_path / "notadir.txt"
        f.write_text("x")
        result = self._invoke(
            "watch", str(spec),
            "--team", "fullstack",
            "--model", "simulated",
            "--repo-index", str(f),
        )
        assert result.exit_code == 1
        assert "not a directory" in result.output.lower()


# ── ContentTeam trace_log path ────────────────────────────────────────────────

class TestContentTeamTraceLog:
    def test_run_with_trace_log(self, tmp_path):
        tl = _trace(tmp_path)
        team = ContentTeam(model=SimulatedLLM(), trace_log=tl)
        result = team.run("Write a blog post about AI agents")
        assert result is not None
        stats = tl.get_stats()
        assert stats["total_runs"] == 1
        assert stats["done_runs"] == 1

    def test_trace_log_error_path(self, tmp_path):
        tl = _trace(tmp_path)
        team = ContentTeam(model=SimulatedLLM(), trace_log=tl)
        with patch.object(team._supervisor, "build") as mb:
            mock_app = MagicMock()
            mock_app.invoke.side_effect = RuntimeError("boom")
            mb.return_value = mock_app
            with pytest.raises(RuntimeError):
                team.run("crash")
        stats = tl.get_stats()
        assert stats["error_runs"] == 1

    def test_max_cost_usd_set(self):
        team = ContentTeam(model=SimulatedLLM(), max_cost_usd=0.10)
        assert team.llm.max_cost_usd == 0.10


# ── ContentTeam memory path ───────────────────────────────────────────────────

class TestContentTeamMemory:
    def test_memory_propagated_to_agents(self):
        mem = InMemoryMemory()
        team = ContentTeam(model=SimulatedLLM(), memory=mem)
        for agent in team._agents.values():
            assert agent.memory is mem

    def test_run_calls_store_run(self):
        mem = InMemoryMemory()
        called = []
        orig = mem.store_run
        mem.store_run = lambda s: (called.append(s), orig(s))
        team = ContentTeam(model=SimulatedLLM(), memory=mem)
        team.run("Write a blog post about AI")
        assert len(called) == 1


# ── ResearchTeam trace_log path ───────────────────────────────────────────────

class TestResearchTeamTraceLog:
    def test_run_with_trace_log(self, tmp_path):
        tl = _trace(tmp_path)
        team = ResearchTeam(model=SimulatedLLM(), trace_log=tl)
        result = team.run("Research the best Python web frameworks")
        assert result is not None
        stats = tl.get_stats()
        assert stats["total_runs"] == 1
        assert stats["done_runs"] == 1

    def test_trace_log_error_path(self, tmp_path):
        tl = _trace(tmp_path)
        team = ResearchTeam(model=SimulatedLLM(), trace_log=tl)
        with patch.object(team._supervisor, "build") as mb:
            mock_app = MagicMock()
            mock_app.invoke.side_effect = RuntimeError("boom")
            mb.return_value = mock_app
            with pytest.raises(RuntimeError):
                team.run("crash")
        stats = tl.get_stats()
        assert stats["error_runs"] == 1

    def test_max_cost_usd_set(self):
        team = ResearchTeam(model=SimulatedLLM(), max_cost_usd=0.05)
        assert team.llm.max_cost_usd == 0.05


# ── ResearchTeam memory path ──────────────────────────────────────────────────

class TestResearchTeamMemory:
    def test_memory_propagated_to_agents(self):
        mem = InMemoryMemory()
        team = ResearchTeam(model=SimulatedLLM(), memory=mem)
        for agent in team._agents.values():
            assert agent.memory is mem

    def test_run_calls_store_run(self):
        mem = InMemoryMemory()
        called = []
        orig = mem.store_run
        mem.store_run = lambda s: (called.append(s), orig(s))
        team = ResearchTeam(model=SimulatedLLM(), memory=mem)
        team.run("Research distributed systems")
        assert len(called) == 1
