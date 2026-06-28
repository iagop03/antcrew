"""Tests for antcrew describe command."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from antcrew.cli._app import app

runner = CliRunner()


def _yaml_cfg(tmp_path, **extra) -> Path:
    cfg = {"team": "dev", "model": "simulated", **extra}
    p = tmp_path / "team.yaml"
    p.write_text(yaml.dump(cfg), encoding="utf-8")
    return p


# ── describe (no config) ──────────────────────────────────────────────────────

class TestDescribeNoConfig:
    def test_exits_zero(self):
        result = runner.invoke(app, ["describe"])
        assert result.exit_code == 0

    def test_shows_pipeline_label(self):
        result = runner.invoke(app, ["describe"])
        assert "Pipeline" in result.output

    def test_shows_team_preset(self):
        result = runner.invoke(app, ["describe", "--team", "dev"])
        assert "dev" in result.output

    def test_shows_fullstack_preset(self):
        result = runner.invoke(app, ["describe", "--team", "fullstack"])
        assert "fullstack" in result.output
        assert "codebase_scanner" in result.output

    def test_fullstack_shows_codebase_scanner(self):
        result = runner.invoke(app, ["describe", "--team", "fullstack"])
        assert "codebase_scanner" in result.output

    def test_fullstack_shows_consumes_project_dir(self):
        result = runner.invoke(app, ["describe", "--team", "fullstack"])
        assert "project_dir" in result.output

    def test_research_preset(self):
        result = runner.invoke(app, ["describe", "--team", "research"])
        assert "researcher" in result.output

    def test_content_preset(self):
        result = runner.invoke(app, ["describe", "--team", "content"])
        assert "copywriter" in result.output or "idea" in result.output

    def test_shows_coherence_ok(self):
        result = runner.invoke(app, ["describe", "--team", "dev"])
        assert "Coherencia" in result.output
        assert "OK" in result.output

    def test_shows_consumes_and_produces_columns(self):
        result = runner.invoke(app, ["describe"])
        assert "Consumes" in result.output
        assert "Produces" in result.output


# ── describe --config ─────────────────────────────────────────────────────────

class TestDescribeWithConfig:
    def test_dev_yaml_exits_zero(self, tmp_path):
        p = _yaml_cfg(tmp_path)
        result = runner.invoke(app, ["describe", "--config", str(p)])
        assert result.exit_code == 0

    def test_shows_config_stem_as_pipeline_name(self, tmp_path):
        p = _yaml_cfg(tmp_path)
        result = runner.invoke(app, ["describe", "--config", str(p)])
        assert "team" in result.output

    def test_missing_config_exits_1(self, tmp_path):
        result = runner.invoke(app, ["describe", "--config", str(tmp_path / "nope.yaml")])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_custom_team_shows_steps(self, tmp_path):
        cfg = {
            "team": "custom",
            "model": "simulated",
            "steps": [
                {"name": "planner", "system_prompt": "Plan.", "output_key": "plan"},
            ],
        }
        p = tmp_path / "custom.yaml"
        p.write_text(yaml.dump(cfg), encoding="utf-8")
        result = runner.invoke(app, ["describe", "--config", str(p)])
        assert result.exit_code == 0

    def test_json_config_accepted(self, tmp_path):
        cfg = {"team": "dev", "model": "simulated"}
        p = tmp_path / "team.json"
        p.write_text(json.dumps(cfg), encoding="utf-8")
        result = runner.invoke(app, ["describe", "--config", str(p)])
        assert result.exit_code == 0

    def test_fullstack_config_shows_scanner(self, tmp_path):
        p = _yaml_cfg(tmp_path, team="fullstack")
        result = runner.invoke(app, ["describe", "--config", str(p)])
        assert result.exit_code == 0
        assert "codebase_scanner" in result.output


# ── antcrew run --context warnings ───────────────────────────────────────────

class TestContextWarnings:
    def test_context_with_dev_team_warns(self, tmp_path):
        import json as _json
        ctx = tmp_path / "ctx.json"
        ctx.write_text(_json.dumps({"label": "app", "tech_stack": ["FastAPI"]}))
        result = runner.invoke(app, [
            "run", "Add feature",
            "--team", "dev",
            "--model", "simulated",
            "--no-stream",
            "--context", str(ctx),
        ])
        assert result.exit_code == 0
        assert "Warning" in result.output
        assert "fullstack" in result.output.lower()

    def test_context_with_fullstack_no_warning(self, tmp_path):
        import json as _json
        ctx = tmp_path / "ctx.json"
        ctx.write_text(_json.dumps({"label": "app", "tech_stack": ["FastAPI"]}))
        result = runner.invoke(app, [
            "run", "Add feature",
            "--team", "fullstack",
            "--model", "simulated",
            "--no-stream",
            "--context", str(ctx),
        ])
        assert result.exit_code == 0
        assert "Warning" not in result.output

    def test_context_plus_project_dir_shows_note(self, tmp_path):
        import json as _json
        ctx = tmp_path / "ctx.json"
        ctx.write_text(_json.dumps({"label": "app", "tech_stack": ["FastAPI"]}))
        proj = tmp_path / "src"; proj.mkdir()
        result = runner.invoke(app, [
            "run", "Add feature",
            "--team", "fullstack",
            "--model", "simulated",
            "--no-stream",
            "--context", str(ctx),
            "--project-dir", str(proj),
        ])
        assert result.exit_code == 0
        assert "precedence" in result.output.lower() or "Note" in result.output
