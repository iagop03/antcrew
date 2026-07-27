"""Tests for FeatureAgent, FeatureTeam, WriteFileTool, ListDirTool (v0.11.8)."""
from __future__ import annotations

import os

import pytest

from antcrew.models.simulated import SimulatedLLM

# ---------------------------------------------------------------------------
# WriteFileTool
# ---------------------------------------------------------------------------

class TestWriteFileTool:
    def test_writes_file(self, tmp_path):
        from antcrew.core.tools import WriteFileTool
        tool = WriteFileTool(root=str(tmp_path))
        result = tool.run("hello.txt\n---\nhello world")
        assert result.ok
        assert (tmp_path / "hello.txt").read_text() == "hello world"

    def test_creates_parent_dirs(self, tmp_path):
        from antcrew.core.tools import WriteFileTool
        tool = WriteFileTool(root=str(tmp_path), allow_create_dirs=True)
        result = tool.run("deep/nested/file.py\n---\npass\n")
        assert result.ok
        assert (tmp_path / "deep" / "nested" / "file.py").exists()

    def test_rejects_path_traversal(self, tmp_path):
        from antcrew.core.tools import WriteFileTool
        tool = WriteFileTool(root=str(tmp_path))
        result = tool.run("../../etc/passwd\n---\nevil")
        assert not result.ok
        assert "escapes" in (result.error or "")

    def test_bad_input_format(self, tmp_path):
        from antcrew.core.tools import WriteFileTool
        tool = WriteFileTool(root=str(tmp_path))
        result = tool.run("no separator here at all")
        assert not result.ok

    def test_no_root_rejects_absolute_path(self, tmp_path):
        """Without a root, absolute paths are rejected to prevent unconstrained writes."""
        from antcrew.core.tools import WriteFileTool
        dest = tmp_path / "out.txt"
        tool = WriteFileTool()
        result = tool.run(f"{dest}\n---\ncontent")
        assert not result.ok
        assert "absolute" in (result.error or "").lower() or "root" in (result.error or "").lower()

    def test_no_root_allows_relative_path(self, tmp_path):
        """Without a root, relative paths are resolved from CWD and allowed."""
        from antcrew.core.tools import WriteFileTool
        tool = WriteFileTool()
        # Use a relative path; it will be written relative to CWD
        orig = os.getcwd()
        try:
            os.chdir(tmp_path)
            result = tool.run("relative_out.txt\n---\nhello")
            assert result.ok, result.error
            assert (tmp_path / "relative_out.txt").read_text() == "hello"
        finally:
            os.chdir(orig)

    def test_overwrite_existing_file(self, tmp_path):
        from antcrew.core.tools import WriteFileTool
        f = tmp_path / "f.txt"
        f.write_text("old")
        tool = WriteFileTool(root=str(tmp_path))
        tool.run("f.txt\n---\nnew content")
        assert f.read_text() == "new content"


# ---------------------------------------------------------------------------
# ListDirTool
# ---------------------------------------------------------------------------

class TestListDirTool:
    def test_lists_files(self, tmp_path):
        from antcrew.core.tools import ListDirTool
        (tmp_path / "a.py").write_text("")
        (tmp_path / "b.py").write_text("")
        tool = ListDirTool(root=str(tmp_path))
        result = tool.run(".")
        assert result.ok
        assert "a.py" in result.output
        assert "b.py" in result.output

    def test_skips_pycache(self, tmp_path):
        from antcrew.core.tools import ListDirTool
        pycache = tmp_path / "__pycache__"
        pycache.mkdir()
        (pycache / "mod.pyc").write_text("")
        (tmp_path / "real.py").write_text("")
        tool = ListDirTool(root=str(tmp_path))
        result = tool.run(".")
        assert "__pycache__" not in result.output
        assert "real.py" in result.output

    def test_missing_dir_returns_error(self, tmp_path):
        from antcrew.core.tools import ListDirTool
        tool = ListDirTool(root=str(tmp_path))
        result = tool.run("nonexistent")
        assert not result.ok

    def test_empty_dir(self, tmp_path):
        from antcrew.core.tools import ListDirTool
        tool = ListDirTool(root=str(tmp_path))
        result = tool.run(".")
        assert result.ok
        assert "empty" in result.output.lower()

    def test_nested_structure(self, tmp_path):
        from antcrew.core.tools import ListDirTool
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("")
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_main.py").write_text("")
        tool = ListDirTool(root=str(tmp_path))
        result = tool.run(".")
        assert "main.py" in result.output
        assert "test_main.py" in result.output


# ---------------------------------------------------------------------------
# FeatureAgent
# ---------------------------------------------------------------------------

class TestFeatureAgent:
    def test_run_returns_feature_output(self):
        from antcrew.agents.feature_agent import FeatureAgent
        agent = FeatureAgent(SimulatedLLM())
        result = agent.run({"request": "Add a health check endpoint"})
        assert "feature_output" in result
        assert isinstance(result["feature_output"], str)

    def test_run_returns_files_written(self):
        from antcrew.agents.feature_agent import FeatureAgent
        agent = FeatureAgent(SimulatedLLM())
        result = agent.run({"request": "Write hello.py"})
        assert "files_written" in result
        assert isinstance(result["files_written"], list)

    def test_accepts_project_dir(self, tmp_path):
        from antcrew.agents.feature_agent import FeatureAgent
        agent = FeatureAgent(SimulatedLLM(), project_dir=str(tmp_path))
        result = agent.run({"request": "Add a config file"})
        assert "feature_output" in result

    def test_uses_plan_from_state(self):
        from antcrew.agents.feature_agent import FeatureAgent
        agent = FeatureAgent(SimulatedLLM())
        result = agent.run({"request": "Implement step 2", "plan": "1. Design\n2. Implement"})
        assert "feature_output" in result

    def test_max_tool_steps_propagated(self):
        from antcrew.agents.feature_agent import FeatureAgent
        agent = FeatureAgent(SimulatedLLM(), max_tool_steps=15)
        assert agent.max_tool_steps == 15

    def test_system_prompt_override(self):
        from antcrew.agents.feature_agent import FeatureAgent
        agent = FeatureAgent(SimulatedLLM(), system_prompt_override="Custom instructions")
        assert agent._system_prompt_override == "Custom instructions"

    def test_extra_tools_added(self):
        from antcrew.agents.feature_agent import FeatureAgent
        from antcrew.core.tools import WebSearchTool
        extra = [WebSearchTool()]
        agent = FeatureAgent(SimulatedLLM(), extra_tools=extra)
        names = [t.name for t in agent.tools]
        assert "web_search" in names

    def test_registered_in_registry(self):
        from antcrew.agents.registry import AGENT_REGISTRY
        assert "feature" in AGENT_REGISTRY

    def test_instantiate_via_registry(self):
        from antcrew.agents.registry import instantiate_agent
        agent = instantiate_agent(
            "feature",
            SimulatedLLM(),
            agent_cfg={"project_dir": "/tmp", "max_tool_steps": 8},
        )
        assert agent is not None
        assert agent.max_tool_steps == 8


# ---------------------------------------------------------------------------
# FeatureTeam
# ---------------------------------------------------------------------------

class TestFeatureTeam:
    def test_run_returns_run_result(self):
        from antcrew.agents.feature_agent import FeatureTeam
        from antcrew.core.run_result import RunResult
        team = FeatureTeam(llm=SimulatedLLM())
        result = team.run("Build a login page")
        assert isinstance(result, RunResult)

    def test_run_result_has_feature_output(self):
        from antcrew.agents.feature_agent import FeatureTeam
        team = FeatureTeam(llm=SimulatedLLM())
        result = team.run("Build a login page")
        assert "feature_output" in result.state
        assert result.state["feature_output"]

    def test_run_result_has_files_written(self):
        from antcrew.agents.feature_agent import FeatureTeam
        team = FeatureTeam(llm=SimulatedLLM())
        result = team.run("Write something")
        assert "files_written" in result.state

    def test_project_dir_passed_to_agent(self, tmp_path):
        from antcrew.agents.feature_agent import FeatureTeam
        team = FeatureTeam(llm=SimulatedLLM(), project_dir=str(tmp_path))
        result = team.run("Add a README")
        assert result is not None

    def test_max_cost_usd_applied(self):
        from antcrew.agents.feature_agent import FeatureTeam
        team = FeatureTeam(llm=SimulatedLLM(), max_cost_usd=1.0)
        assert team._agent.max_cost_usd == 1.0


# ---------------------------------------------------------------------------
# YAML config: team: feature
# ---------------------------------------------------------------------------

class TestConfigFeatureTeam:
    def test_load_feature_team_from_yaml(self, tmp_path):
        yaml_content = """
team: feature
model: simulated
project_dir: .
max_tool_steps: 5
"""
        cfg_file = tmp_path / "team.yaml"
        cfg_file.write_text(yaml_content, encoding="utf-8")
        from antcrew.config import load
        team = load(cfg_file)
        from antcrew.agents.feature_agent import FeatureTeam
        assert isinstance(team, FeatureTeam)

    def test_feature_team_runnable_from_yaml(self, tmp_path):
        yaml_content = """
team: feature
model: simulated
max_tool_steps: 3
"""
        cfg_file = tmp_path / "team.yaml"
        cfg_file.write_text(yaml_content, encoding="utf-8")
        from antcrew.config import load
        team = load(cfg_file)
        result = team.run("Add error handling")
        assert result is not None

    def test_feature_team_error_on_unknown_model(self, tmp_path):
        yaml_content = """
team: feature
model: not_a_real_model_xyz
"""
        cfg_file = tmp_path / "team.yaml"
        cfg_file.write_text(yaml_content, encoding="utf-8")
        from antcrew.config import load
        with pytest.raises(ValueError, match="Unknown model"):
            load(cfg_file)


# ---------------------------------------------------------------------------
# Public exports
# ---------------------------------------------------------------------------

class TestPublicExports:
    def test_write_file_tool_exported(self):
        from antcrew import WriteFileTool
        assert WriteFileTool is not None

    def test_list_dir_tool_exported(self):
        from antcrew import ListDirTool
        assert ListDirTool is not None

    def test_feature_agent_exported(self):
        from antcrew import FeatureAgent
        assert FeatureAgent is not None

    def test_feature_team_exported(self):
        from antcrew import FeatureTeam
        assert FeatureTeam is not None
