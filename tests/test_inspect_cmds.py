"""Tests for antcrew show, extract, describe, and agents commands."""
from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from antcrew.cli._app import app

runner = CliRunner()


def _state(tmp_path, **extra) -> Path:
    """Write a minimal dev-team state JSON and return its path."""
    state = {
        "request": "Build auth module",
        "prd": {
            "title": "Auth Module",
            "summary": "Add login/logout.",
            "goals": ["Secure login"],
            "out_of_scope": [],
            "functional_requirements": ["POST /login"],
            "non_functional_requirements": [],
            "open_questions": [],
        },
        "tickets": [
            {
                "id": "TICKET-001",
                "title": "Implement login",
                "description": "Add JWT login endpoint.",
                "priority": "high",
                "acceptance_criteria": ["Returns 200 on valid creds"],
                "dependencies": [],
            }
        ],
        "code_artifacts": [
            {
                "file_path": "src/auth.py",
                "description": "Auth module",
                "language": "python",
                "content": "def login(): pass\n",
                "ticket_id": "TICKET-001",
            }
        ],
        "devops_artifacts": [
            {
                "file_path": "Dockerfile",
                "description": "Docker image",
                "language": "dockerfile",
                "content": "FROM python:3.12-slim\n",
            }
        ],
        "test_artifacts": [
            {
                "ticket_id": "TICKET-001",
                "file_path": "tests/test_auth.py",
                "description": "Auth tests",
                "language": "python",
                "content": "def test_login(): pass\n",
                "coverage_areas": ["unit"],
            }
        ],
        **extra,
    }
    p = tmp_path / "run.json"
    p.write_text(json.dumps(state), encoding="utf-8")
    return p


def _brownfield_state(tmp_path) -> Path:
    """State that includes codebase_analysis (brownfield run)."""
    return _state(tmp_path, codebase_analysis={
        "label": "backend",
        "tech_stack": ["Python 3.12", "FastAPI"],
        "existing_modules": ["src/auth"],
        "entry_points": ["src/main.py"],
        "test_coverage_summary": "Tests exist for auth only",
        "what_exists": "Auth system, basic CRUD",
        "what_is_missing": "Billing module",
        "continuation_context": "MVP SaaS, billing not started",
    })


# ── antcrew show ─────────────────────────────────────────────────────────────

class TestShowCmd:
    def test_show_exits_zero(self, tmp_path):
        p = _state(tmp_path)
        result = runner.invoke(app, ["show", str(p)])
        assert result.exit_code == 0

    def test_show_displays_prd_title(self, tmp_path):
        p = _state(tmp_path)
        result = runner.invoke(app, ["show", str(p)])
        assert "Auth Module" in result.output

    def test_show_displays_ticket(self, tmp_path):
        p = _state(tmp_path)
        result = runner.invoke(app, ["show", str(p)])
        assert "TICKET-001" in result.output or "login" in result.output.lower()

    def test_show_displays_code_artifact(self, tmp_path):
        p = _state(tmp_path)
        result = runner.invoke(app, ["show", str(p)])
        assert "src/auth.py" in result.output

    def test_show_json_flag_outputs_valid_json(self, tmp_path):
        p = _state(tmp_path)
        result = runner.invoke(app, ["show", str(p), "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["request"] == "Build auth module"

    def test_show_missing_file_exits_1(self, tmp_path):
        result = runner.invoke(app, ["show", str(tmp_path / "nope.json")])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_show_displays_codebase_analysis(self, tmp_path):
        """Brownfield state: codebase scan panel appears before PRD."""
        p = _brownfield_state(tmp_path)
        result = runner.invoke(app, ["show", str(p)])
        assert result.exit_code == 0
        assert "Codebase scan" in result.output
        assert "FastAPI" in result.output

    def test_show_codebase_analysis_what_exists(self, tmp_path):
        p = _brownfield_state(tmp_path)
        result = runner.invoke(app, ["show", str(p)])
        assert "Auth system" in result.output or "what_exists" in result.output.lower()

    def test_show_codebase_analysis_what_missing(self, tmp_path):
        p = _brownfield_state(tmp_path)
        result = runner.invoke(app, ["show", str(p)])
        assert "Billing" in result.output

    def test_show_codebase_analysis_appears_before_prd(self, tmp_path):
        p = _brownfield_state(tmp_path)
        result = runner.invoke(app, ["show", str(p)])
        scan_pos = result.output.find("Codebase scan")
        prd_pos = result.output.find("Auth Module")
        assert scan_pos != -1 and prd_pos != -1
        assert scan_pos < prd_pos

    def test_show_multi_codebase_analyses(self, tmp_path):
        p = _state(tmp_path, codebase_analyses=[
            {"label": "api",     "tech_stack": ["FastAPI"], "existing_modules": []},
            {"label": "frontend","tech_stack": ["React"],   "existing_modules": []},
        ])
        result = runner.invoke(app, ["show", str(p)])
        assert result.exit_code == 0
        assert "FastAPI" in result.output
        assert "React" in result.output


# ── antcrew extract ───────────────────────────────────────────────────────────

class TestExtractCmd:
    def test_extract_writes_code_artifact(self, tmp_path):
        p = _state(tmp_path)
        out = tmp_path / "out"
        result = runner.invoke(app, ["extract", str(p), "--output", str(out)])
        assert result.exit_code == 0
        assert (out / "src" / "auth.py").exists()

    def test_extract_writes_test_artifact(self, tmp_path):
        p = _state(tmp_path)
        out = tmp_path / "out"
        result = runner.invoke(app, ["extract", str(p), "--output", str(out)])
        assert result.exit_code == 0
        assert (out / "tests" / "test_auth.py").exists()

    def test_extract_writes_devops_artifact(self, tmp_path):
        p = _state(tmp_path)
        out = tmp_path / "out"
        result = runner.invoke(app, ["extract", str(p), "--output", str(out)])
        assert result.exit_code == 0
        assert (out / "Dockerfile").exists()

    def test_extract_no_tests_skips_test_files(self, tmp_path):
        p = _state(tmp_path)
        out = tmp_path / "out"
        result = runner.invoke(app, ["extract", str(p), "--output", str(out), "--no-tests"])
        assert result.exit_code == 0
        assert not (out / "tests" / "test_auth.py").exists()

    def test_extract_no_devops_skips_devops_files(self, tmp_path):
        p = _state(tmp_path)
        out = tmp_path / "out"
        result = runner.invoke(app, ["extract", str(p), "--output", str(out), "--no-devops"])
        assert result.exit_code == 0
        assert not (out / "Dockerfile").exists()

    def test_extract_dry_run_does_not_write(self, tmp_path):
        p = _state(tmp_path)
        out = tmp_path / "out"
        result = runner.invoke(app, ["extract", str(p), "--output", str(out), "--dry-run"])
        assert result.exit_code == 0
        assert not out.exists() or not (out / "src" / "auth.py").exists()

    def test_extract_missing_file_exits_1(self, tmp_path):
        result = runner.invoke(app, ["extract", str(tmp_path / "nope.json")])
        assert result.exit_code == 1

    def test_extract_empty_state_exits_zero(self, tmp_path):
        p = tmp_path / "empty.json"
        p.write_text(json.dumps({"request": "x"}), encoding="utf-8")
        result = runner.invoke(app, ["extract", str(p)])
        assert result.exit_code == 0
        assert "No artifacts" in result.output


# ── antcrew agents --json ─────────────────────────────────────────────────────

class TestAgentsJsonFlag:
    def test_json_outputs_valid_json(self):
        result = runner.invoke(app, ["agents", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) > 0

    def test_json_contains_name_and_class(self):
        result = runner.invoke(app, ["agents", "--json"])
        data = json.loads(result.output)
        first = data[0]
        assert "name" in first
        assert "class" in first
        assert "role" in first

    def test_json_includes_all_registry_agents(self):
        from antcrew.agents.registry import AGENT_REGISTRY
        result = runner.invoke(app, ["agents", "--json"])
        data = json.loads(result.output)
        names = {e["name"] for e in data}
        assert set(AGENT_REGISTRY.keys()) == names

    def test_json_suppresses_rich_output(self):
        result = runner.invoke(app, ["agents", "--json"])
        # JSON mode: no Rich markup, no trailing text
        assert "Built-in" not in result.output
        assert "post_process" not in result.output
