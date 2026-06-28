"""Tests for CodebaseScannerAgent — unit + integration."""
from __future__ import annotations

from pathlib import Path

import pytest

from antcrew.agents.codebase_scanner import (
    _IGNORE_DIRS, _KEY_FILES,
    _build_tree, _read_key_files, _scan_one,
    CodebaseScannerAgent,
)
from antcrew.core.artifacts import CodebaseAnalysis
from antcrew.models.simulated import SimulatedLLM


# ── helpers ───────────────────────────────────────────────────────────────────

def _agent() -> CodebaseScannerAgent:
    return CodebaseScannerAgent(llm=SimulatedLLM())


# ── _build_tree ───────────────────────────────────────────────────────────────

class TestBuildTree:
    def test_lists_files(self, tmp_path):
        (tmp_path / "main.py").write_text("x=1")
        tree = _build_tree(tmp_path, _IGNORE_DIRS)
        assert "main.py" in tree

    def test_lists_subdirectory(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.py").write_text("x=1")
        tree = _build_tree(tmp_path, _IGNORE_DIRS)
        assert "src/" in tree
        assert "app.py" in tree

    def test_ignores_git_directory(self, tmp_path):
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "config").write_text("[core]")
        tree = _build_tree(tmp_path, _IGNORE_DIRS)
        assert ".git" not in tree

    def test_ignores_pycache(self, tmp_path):
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / "__pycache__" / "main.cpython-311.pyc").write_text("")
        tree = _build_tree(tmp_path, _IGNORE_DIRS)
        assert "__pycache__" not in tree

    def test_empty_dir_returns_empty_string(self, tmp_path):
        tree = _build_tree(tmp_path, _IGNORE_DIRS)
        assert tree == ""

    def test_respects_extra_ignore_dirs(self, tmp_path):
        (tmp_path / "dist").mkdir()
        (tmp_path / "dist" / "bundle.js").write_text("x")
        tree = _build_tree(tmp_path, _IGNORE_DIRS | {"dist"})
        assert "dist" not in tree

    def test_max_tree_lines_respected(self, tmp_path):
        for i in range(250):
            (tmp_path / f"file_{i:04d}.py").write_text("x=1")
        tree = _build_tree(tmp_path, _IGNORE_DIRS)
        assert len(tree.splitlines()) <= 200


# ── _read_key_files ───────────────────────────────────────────────────────────

class TestReadKeyFiles:
    def test_reads_readme(self, tmp_path):
        (tmp_path / "README.md").write_text("# My Project")
        result = _read_key_files(tmp_path)
        assert "README.md" in result
        assert "My Project" in result

    def test_reads_pyproject_toml(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text('[project]\nname="myapp"')
        result = _read_key_files(tmp_path)
        assert "pyproject.toml" in result

    def test_reads_package_json(self, tmp_path):
        (tmp_path / "package.json").write_text('{"name": "frontend"}')
        result = _read_key_files(tmp_path)
        assert "package.json" in result

    def test_missing_key_files_returns_empty(self, tmp_path):
        result = _read_key_files(tmp_path)
        assert result == ""

    def test_truncates_large_files(self, tmp_path):
        large = "x" * 10_000
        (tmp_path / "README.md").write_text(large)
        result = _read_key_files(tmp_path)
        assert len(result) < len(large)


# ── _scan_one ─────────────────────────────────────────────────────────────────

class TestScanOne:
    def test_returns_codebase_analysis(self, tmp_path):
        (tmp_path / "main.py").write_text("x=1")
        agent = _agent()
        result = _scan_one(agent, "myapp", str(tmp_path))
        assert isinstance(result, CodebaseAnalysis)
        assert result.label == "myapp"

    def test_returns_none_for_nonexistent_dir(self, tmp_path):
        agent = _agent()
        result = _scan_one(agent, "myapp", str(tmp_path / "doesnt_exist"))
        assert result is None

    def test_simulated_llm_returns_codebase_fixture(self, tmp_path):
        (tmp_path / "README.md").write_text("# Test project")
        agent = _agent()
        result = _scan_one(agent, "myapp", str(tmp_path))
        assert result is not None
        # SimulatedLLM should return the _CODEBASE_ANALYSIS_FIXTURE
        assert result.tech_stack or result.label == "myapp"

    def test_populates_fixture_fields(self, tmp_path):
        (tmp_path / "src").mkdir()
        agent = _agent()
        result = _scan_one(agent, "backend", str(tmp_path))
        assert isinstance(result, CodebaseAnalysis)
        assert result.tech_stack  # SimulatedLLM fills this in


# ── CodebaseScannerAgent.run() ────────────────────────────────────────────────

class TestCodebaseScannerAgent:
    def test_run_with_project_dir(self, tmp_path):
        (tmp_path / "app.py").write_text("x=1")
        agent = _agent()
        output = agent.run({"project_dir": str(tmp_path)})
        assert "codebase_analysis" in output
        assert isinstance(output["codebase_analysis"], CodebaseAnalysis)

    def test_run_with_project_dirs(self, tmp_path):
        be = tmp_path / "backend"; be.mkdir(); (be / "main.py").write_text("x=1")
        fe = tmp_path / "frontend"; fe.mkdir(); (fe / "index.ts").write_text("export {}")
        agent = _agent()
        output = agent.run({"project_dirs": {"backend": str(be), "frontend": str(fe)}})
        assert "codebase_analyses" in output
        analyses = output["codebase_analyses"]
        assert len(analyses) == 2
        labels = {a.label for a in analyses}
        assert labels == {"backend", "frontend"}

    def test_run_without_dirs_returns_none(self):
        agent = _agent()
        output = agent.run({})
        assert output["codebase_analysis"] is None
        assert output["codebase_analyses"] is None

    def test_run_with_nonexistent_dir_skips(self, tmp_path):
        agent = _agent()
        output = agent.run({"project_dir": str(tmp_path / "doesnt_exist")})
        assert output["codebase_analysis"] is None

    def test_run_backward_compat_single_alias(self, tmp_path):
        (tmp_path / "app.py").write_text("x=1")
        agent = _agent()
        output = agent.run({"project_dirs": {"only": str(tmp_path)}})
        # codebase_analysis should be the first analysis (backward compat alias)
        assert output["codebase_analysis"] is not None

    def test_extra_ignore_dirs(self, tmp_path):
        vendor = tmp_path / "vendor"; vendor.mkdir()
        (vendor / "lib.py").write_text("x=1")
        (tmp_path / "src").mkdir(); (tmp_path / "src" / "app.py").write_text("x=1")
        agent = CodebaseScannerAgent(llm=SimulatedLLM(), extra_ignore_dirs=["vendor"])
        assert "vendor" in agent._ignore


# ── Integration: FullStackTeam + CodebaseScannerAgent ────────────────────────

class TestBrownfieldIntegration:
    """Full pipeline: scan → FullStackTeam → write_back."""

    def test_fullstack_team_runs_scanner(self, tmp_path):
        """When project_dir is set, FullStackTeam scans it and the state has codebase_analysis."""
        from antcrew.teams.fullstack_team import FullStackTeam

        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "auth.py").write_text("# existing auth")
        (tmp_path / "README.md").write_text("# My SaaS")

        team = FullStackTeam(model=SimulatedLLM(), project_dir=str(tmp_path))
        result = team.run("Add billing module")

        raw = result.state if hasattr(result, "state") else result
        # Scanner produces codebase_analysis in state
        assert raw.get("codebase_analysis") is not None
        ca = raw["codebase_analysis"]
        assert isinstance(ca, CodebaseAnalysis)
        assert ca.tech_stack  # SimulatedLLM returns fixture data

    def test_fullstack_team_with_project_dirs(self, tmp_path):
        from antcrew.teams.fullstack_team import FullStackTeam

        be = tmp_path / "api"; be.mkdir(); (be / "app.py").write_text("x=1")
        fe = tmp_path / "web"; fe.mkdir(); (fe / "index.ts").write_text("export {}")

        team = FullStackTeam(
            model=SimulatedLLM(),
            project_dirs={"api": str(be), "web": str(fe)},
        )
        result = team.run("Add OAuth login")
        raw = result.state if hasattr(result, "state") else result
        assert raw.get("codebase_analyses") is not None
        assert len(raw["codebase_analyses"]) == 2

    def test_write_back_after_fullstack_run(self, tmp_path):
        """End-to-end: run pipeline, then write_back artifacts to project dir."""
        from antcrew.teams.fullstack_team import FullStackTeam
        from antcrew.core.writeback import write_back

        project = tmp_path / "myproject"
        project.mkdir()
        (project / "README.md").write_text("# My Project")

        output = tmp_path / "generated"

        team = FullStackTeam(model=SimulatedLLM(), project_dir=str(project))
        result = team.run("Add auth module")

        wb_result = write_back(result, output, dry_run=False, yes=True)
        # FullStackTeam generates code_artifacts and devops_artifacts
        assert wb_result.total_written > 0
        # Files should exist in output dir
        written_files = list(output.rglob("*"))
        assert len(written_files) > 0
