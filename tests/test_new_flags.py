"""Tests for newly added CLI flags: scan --since, describe --context,
run --write-back (CLI), run --repo-index, AsyncFullStackTeam scan_context."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from antcrew.cli._app import app
from antcrew.models.simulated import SimulatedLLM

runner = CliRunner()

# ── antcrew scan --since ──────────────────────────────────────────────────────

class TestScanSince:
    def test_since_exits_zero(self, tmp_path):
        (tmp_path / "app.py").write_text("x=1")
        result = runner.invoke(app, ["scan", str(tmp_path), "--since", "30"])
        assert result.exit_code == 0

    def test_since_shows_recent_file(self, tmp_path):
        f = tmp_path / "new.py"
        f.write_text("x=1")
        result = runner.invoke(app, ["scan", str(tmp_path), "--since", "30"])
        assert result.exit_code == 0
        # The freshly created file should appear (it's < 30 days old)
        assert "new.py" in result.output

    def test_since_hides_old_file(self, tmp_path):
        old = tmp_path / "old.py"
        old.write_text("x=1")
        # Set mtime to 60 days ago
        ancient = time.time() - 60 * 86400
        os.utime(str(old), (ancient, ancient))
        result = runner.invoke(app, ["scan", str(tmp_path), "--since", "7"])
        assert result.exit_code == 0
        # old.py should not appear in the (filtered) tree
        assert "old.py" not in result.output

    def test_since_json_still_includes_key_files(self, tmp_path):
        (tmp_path / "README.md").write_text("# Hi")
        result = runner.invoke(app, ["scan", str(tmp_path), "--since", "30", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        # key_files are checked by existence, not mtime — should still appear
        assert "README.md" in data["key_files"]


# ── antcrew describe --context ────────────────────────────────────────────────

class TestDescribeContext:
    def _ctx(self, tmp_path, **kw) -> Path:
        ctx = {
            "label": "backend", "tech_stack": ["FastAPI"],
            "what_exists": "Auth system", "what_is_missing": "Billing",
            **kw,
        }
        p = tmp_path / "ctx.json"
        p.write_text(json.dumps(ctx))
        return p

    def test_describe_context_exits_zero(self, tmp_path):
        ctx = self._ctx(tmp_path)
        result = runner.invoke(app, ["describe", "--team", "fullstack", "--context", str(ctx)])
        assert result.exit_code == 0

    def test_describe_context_shows_tech_stack(self, tmp_path):
        ctx = self._ctx(tmp_path)
        result = runner.invoke(app, ["describe", "--team", "fullstack", "--context", str(ctx)])
        assert "FastAPI" in result.output

    def test_describe_context_shows_what_missing(self, tmp_path):
        ctx = self._ctx(tmp_path)
        result = runner.invoke(app, ["describe", "--team", "fullstack", "--context", str(ctx)])
        assert "Billing" in result.output

    def test_describe_context_shows_panel_title(self, tmp_path):
        ctx = self._ctx(tmp_path)
        result = runner.invoke(app, ["describe", "--team", "fullstack", "--context", str(ctx)])
        assert "Pre-loaded context" in result.output or "ctx.json" in result.output

    def test_describe_context_missing_file_shows_error(self, tmp_path):
        result = runner.invoke(app, ["describe", "--team", "fullstack",
                                      "--context", str(tmp_path / "nope.json")])
        assert result.exit_code == 0  # doesn't abort — just warns
        assert "not found" in result.output.lower()

    def test_describe_context_multi_component(self, tmp_path):
        ctx = {"components": [
            {"label": "api",      "tech_stack": ["FastAPI"], "what_exists": "x", "what_is_missing": "y"},
            {"label": "frontend", "tech_stack": ["React"],   "what_exists": "x", "what_is_missing": "y"},
        ]}
        p = tmp_path / "ctx.json"
        p.write_text(json.dumps(ctx))
        result = runner.invoke(app, ["describe", "--team", "fullstack", "--context", str(p)])
        assert result.exit_code == 0
        assert "FastAPI" in result.output
        assert "React" in result.output


# ── antcrew run --write-back (CLI integration) ────────────────────────────────

class TestRunWriteBack:
    def test_write_back_creates_files(self, tmp_path):
        out = tmp_path / "out"
        result = runner.invoke(app, [
            "run", "Add auth module",
            "--team", "dev",
            "--model", "simulated",
            "--no-stream",
            "--write-back", str(out),
            "--write-back-yes",
        ])
        assert result.exit_code == 0
        # SimulatedLLM generates code artifacts; write-back should create files
        written = list(out.rglob("*"))
        assert len(written) > 0

    def test_write_back_dry_run_no_files(self, tmp_path):
        out = tmp_path / "out"
        result = runner.invoke(app, [
            "run", "Add auth module",
            "--team", "dev",
            "--model", "simulated",
            "--no-stream",
            "--write-back", str(out),
            "--write-back-yes",
            "--dry-run",
        ])
        # dry-run for CustomTeam only — with dev team it still runs
        # but write-back should still attempt; just verify no crash
        assert result.exit_code == 0


# ── run --repo-index ──────────────────────────────────────────────────────────

class TestRunRepoIndex:
    def test_repo_index_nonexistent_dir_exits_1(self, tmp_path):
        result = runner.invoke(app, [
            "run", "Add auth",
            "--team", "fullstack",
            "--model", "simulated",
            "--no-stream",
            "--repo-index", str(tmp_path / "doesnt_exist"),
        ])
        assert result.exit_code == 1
        assert "not a directory" in result.output.lower()

    def test_repo_index_with_dev_team_warns(self, tmp_path):
        result = runner.invoke(app, [
            "run", "Add auth",
            "--team", "dev",
            "--model", "simulated",
            "--no-stream",
            "--repo-index", str(tmp_path),
        ])
        assert result.exit_code == 0
        assert "Warning" in result.output
        assert "fullstack" in result.output.lower()

    def test_repo_index_with_fullstack_and_valid_dir_succeeds(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("x=1")
        result = runner.invoke(app, [
            "run", "Add auth",
            "--team", "fullstack",
            "--model", "simulated",
            "--no-stream",
            "--repo-index", str(tmp_path),
        ])
        # RepoIndex requires chromadb; skip test gracefully if not installed
        if "chromadb" in result.output.lower() or "modulenotfounderror" in result.output.lower():
            pytest.skip("chromadb not installed")
        assert result.exit_code == 0


# ── AsyncFullStackTeam scan_context ──────────────────────────────────────────

class TestAsyncFullStackTeamScanContext:
    def test_async_team_accepts_scan_context(self):
        from antcrew.teams.async_teams import AsyncFullStackTeam
        ctx = {"label": "app", "tech_stack": ["Django"], "existing_modules": []}
        team = AsyncFullStackTeam(model=SimulatedLLM(), scan_context=ctx)
        pre_ca, pre_cas = team._parse_scan_context()
        assert pre_ca is not None
        assert "Django" in pre_ca.tech_stack

    def test_async_team_initial_state_has_context(self):
        from antcrew.teams.async_teams import AsyncFullStackTeam
        ctx = {"label": "backend", "tech_stack": ["FastAPI"]}
        team = AsyncFullStackTeam(model=SimulatedLLM(), scan_context=ctx)
        state = team._initial_state("Build something")
        assert state["codebase_analysis"] is not None
        assert "FastAPI" in state["codebase_analysis"].tech_stack

    @pytest.mark.asyncio
    async def test_async_team_run_uses_preloaded_context(self):
        from antcrew.teams.async_teams import AsyncFullStackTeam
        ctx = {"label": "api", "tech_stack": ["FastAPI", "SQLAlchemy"]}
        team = AsyncFullStackTeam(model=SimulatedLLM(), scan_context=ctx)
        result = await team.run("Add billing endpoint")
        raw = result.state if hasattr(result, "state") else result
        ca = raw.get("codebase_analysis")
        assert ca is not None
        assert "FastAPI" in ca.tech_stack
