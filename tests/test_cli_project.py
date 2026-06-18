"""Tests for antcrew project CLI commands and runner: config support."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from antcrew.cli import app
from antcrew.models.simulated import SimulatedLLM
from antcrew.project import Project
from antcrew.teams.dev_team import DevTeam

cli = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _simulated_config(tmp_path: Path, team: str = "dev") -> Path:
    """Write a minimal JSON config using SimulatedLLM."""
    p = tmp_path / "team.json"
    p.write_text(json.dumps({"team": team, "model": "simulated"}))
    return p


def _make_project(tmp_path: Path, runs: int = 1) -> Path:
    """Create a saved project with N runs and return its path."""
    proj_path = tmp_path / "proj.json"
    p = Project(DevTeam(model=SimulatedLLM()), name="test-project", path=proj_path)
    p._team_spec = {"type": "inline", "team": "dev", "model": "simulated"}
    for i in range(runs):
        p.run(f"Request {i + 1}")
    return proj_path


# ===========================================================================
# antcrew project run
# ===========================================================================

class TestProjectRun:

    def test_creates_new_project_file(self, tmp_path):
        proj = tmp_path / "proj.json"
        result = cli.invoke(app, [
            "project", "run", str(proj), "Build login",
            "--team", "dev", "--model", "simulated",
        ])
        assert result.exit_code == 0, result.output
        assert proj.exists()

    def test_new_project_requires_team_or_config(self, tmp_path):
        proj = tmp_path / "new.json"
        result = cli.invoke(app, ["project", "run", str(proj), "Build login"])
        assert result.exit_code == 1
        assert "--team" in result.output or "--config" in result.output

    def test_project_continues_existing(self, tmp_path):
        proj = _make_project(tmp_path, runs=1)
        result = cli.invoke(app, [
            "project", "run", str(proj), "Second request",
        ])
        assert result.exit_code == 0, result.output
        data = json.loads(proj.read_text())
        assert len(data["history"]) == 2

    def test_project_stores_team_spec(self, tmp_path):
        proj = tmp_path / "proj.json"
        cli.invoke(app, [
            "project", "run", str(proj), "Build login",
            "--team", "dev", "--model", "simulated",
        ])
        data = json.loads(proj.read_text())
        assert "team_spec" in data
        assert data["team_spec"]["team"] == "dev"

    def test_project_stores_inline_model(self, tmp_path):
        proj = tmp_path / "proj.json"
        cli.invoke(app, [
            "project", "run", str(proj), "Build",
            "--team", "dev", "--model", "simulated",
        ])
        data = json.loads(proj.read_text())
        assert data["team_spec"]["model"] == "simulated"

    def test_project_run_with_config_file(self, tmp_path):
        cfg = _simulated_config(tmp_path)
        proj = tmp_path / "proj.json"
        result = cli.invoke(app, [
            "project", "run", str(proj), "Build login",
            "--config", str(cfg),
        ])
        assert result.exit_code == 0, result.output
        data = json.loads(proj.read_text())
        assert data["team_spec"]["type"] == "config"

    def test_project_run_with_name(self, tmp_path):
        proj = tmp_path / "proj.json"
        cli.invoke(app, [
            "project", "run", str(proj), "Build",
            "--team", "dev", "--model", "simulated",
            "--name", "my-auth-service",
        ])
        data = json.loads(proj.read_text())
        assert data["name"] == "my-auth-service"

    def test_project_run_defaults_name_to_stem(self, tmp_path):
        proj = tmp_path / "auth-service.json"
        cli.invoke(app, [
            "project", "run", str(proj), "Build",
            "--team", "dev", "--model", "simulated",
        ])
        data = json.loads(proj.read_text())
        assert data["name"] == "auth-service"

    def test_project_auto_saves(self, tmp_path):
        proj = tmp_path / "proj.json"
        cli.invoke(app, [
            "project", "run", str(proj), "Build",
            "--team", "dev", "--model", "simulated",
        ])
        assert proj.exists()

    def test_project_shows_run_number(self, tmp_path):
        proj = _make_project(tmp_path, runs=1)
        result = cli.invoke(app, ["project", "run", str(proj), "Second"])
        assert "run #2" in result.output.replace("\n", " ")

    def test_project_shows_accumulated_counts(self, tmp_path):
        proj = _make_project(tmp_path, runs=1)
        result = cli.invoke(app, ["project", "run", str(proj), "Second"])
        assert "total:" in result.output

    def test_project_with_research_team(self, tmp_path):
        proj = tmp_path / "research.json"
        result = cli.invoke(app, [
            "project", "run", str(proj), "Research AI safety",
            "--team", "research", "--model", "simulated",
        ])
        assert result.exit_code == 0, result.output

    def test_project_with_content_team(self, tmp_path):
        proj = tmp_path / "content.json"
        result = cli.invoke(app, [
            "project", "run", str(proj), "Write a blog post",
            "--team", "content", "--model", "simulated",
        ])
        assert result.exit_code == 0, result.output

    def test_project_resumes_without_team_flag(self, tmp_path):
        """Second run must work without --team because team_spec is stored."""
        proj = tmp_path / "proj.json"
        # First run — provides team
        cli.invoke(app, [
            "project", "run", str(proj), "First",
            "--team", "dev", "--model", "simulated",
        ])
        # Second run — no team flags
        result = cli.invoke(app, ["project", "run", str(proj), "Second"])
        assert result.exit_code == 0, result.output

    def test_project_override_team_on_resume(self, tmp_path):
        """Passing --team on a subsequent run overrides the stored spec."""
        proj = _make_project(tmp_path)
        result = cli.invoke(app, [
            "project", "run", str(proj), "Another run",
            "--team", "dev", "--model", "simulated",
        ])
        assert result.exit_code == 0, result.output


# ===========================================================================
# antcrew project show
# ===========================================================================

class TestProjectShow:

    def test_show_project_exists(self, tmp_path):
        proj = _make_project(tmp_path)
        result = cli.invoke(app, ["project", "show", str(proj)])
        assert result.exit_code == 0, result.output

    def test_show_displays_project_name(self, tmp_path):
        proj = _make_project(tmp_path)
        result = cli.invoke(app, ["project", "show", str(proj)])
        assert "test-project" in result.output

    def test_show_displays_run_count(self, tmp_path):
        proj = _make_project(tmp_path, runs=2)
        result = cli.invoke(app, ["project", "show", str(proj)])
        assert "2 run" in result.output

    def test_show_missing_file(self, tmp_path):
        result = cli.invoke(app, ["project", "show", str(tmp_path / "nope.json")])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_show_json_flag(self, tmp_path):
        proj = _make_project(tmp_path)
        result = cli.invoke(app, ["project", "show", str(proj), "--json"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert "history" in parsed

    def test_show_empty_project_no_crash(self, tmp_path):
        """Project with no runs yet should show a 'no state' message."""
        proj = tmp_path / "empty.json"
        data = {
            "name": "empty-project",
            "created_at": 0.0,
            "state": {},
            "history": [],
            "team_spec": {"type": "inline", "team": "dev", "model": "simulated"},
        }
        proj.write_text(json.dumps(data))
        result = cli.invoke(app, ["project", "show", str(proj)])
        assert result.exit_code == 0


# ===========================================================================
# antcrew project history
# ===========================================================================

class TestProjectHistory:

    def test_history_shows_requests(self, tmp_path):
        proj = _make_project(tmp_path, runs=2)
        result = cli.invoke(app, ["project", "history", str(proj)])
        assert result.exit_code == 0, result.output
        assert "Request 1" in result.output
        assert "Request 2" in result.output

    def test_history_shows_run_numbers(self, tmp_path):
        proj = _make_project(tmp_path, runs=3)
        result = cli.invoke(app, ["project", "history", str(proj)])
        assert "1" in result.output
        assert "2" in result.output
        assert "3" in result.output

    def test_history_shows_totals(self, tmp_path):
        proj = _make_project(tmp_path, runs=1)
        result = cli.invoke(app, ["project", "history", str(proj)])
        assert "Total:" in result.output

    def test_history_empty_project(self, tmp_path):
        proj = tmp_path / "empty.json"
        proj.write_text(json.dumps({"name": "x", "created_at": 0, "state": {}, "history": []}))
        result = cli.invoke(app, ["project", "history", str(proj)])
        assert result.exit_code == 0
        assert "No runs" in result.output

    def test_history_missing_file(self, tmp_path):
        result = cli.invoke(app, ["project", "history", str(tmp_path / "nope.json")])
        assert result.exit_code == 1


# ===========================================================================
# Project.load() with team_spec auto-restore
# ===========================================================================

class TestProjectLoadTeamSpec:

    def test_load_without_team_uses_stored_spec(self, tmp_path):
        proj = _make_project(tmp_path)  # stores team_spec
        p = Project.load(proj)          # no team= arg
        assert p.team is not None

    def test_load_without_team_no_spec_raises(self, tmp_path):
        proj = tmp_path / "no_spec.json"
        proj.write_text(json.dumps({
            "name": "x", "created_at": 0, "state": {}, "history": []
        }))
        with pytest.raises(ValueError, match="team_spec"):
            Project.load(proj)

    def test_load_team_arg_overrides_stored_spec(self, tmp_path):
        proj = _make_project(tmp_path)
        fresh_team = DevTeam(model=SimulatedLLM())
        p = Project.load(proj, team=fresh_team)
        assert p.team is fresh_team

    def test_team_from_spec_inline(self):
        spec = {"type": "inline", "team": "dev", "model": "simulated"}
        team = Project._team_from_spec(spec)
        assert isinstance(team, DevTeam)

    def test_team_from_spec_config(self, tmp_path):
        cfg = _simulated_config(tmp_path)
        spec = {"type": "config", "path": str(cfg)}
        team = Project._team_from_spec(spec)
        assert isinstance(team, DevTeam)

    def test_team_spec_persisted_in_save(self, tmp_path):
        proj = tmp_path / "proj.json"
        p = Project(DevTeam(model=SimulatedLLM()), path=proj)
        p._team_spec = {"type": "inline", "team": "dev", "model": "simulated"}
        p.run("Build login")
        data = json.loads(proj.read_text())
        assert "team_spec" in data

    def test_load_and_run_continues_accumulating(self, tmp_path):
        proj = _make_project(tmp_path, runs=1)
        p = Project.load(proj)
        p.run("Second run")
        data = json.loads(proj.read_text())
        assert len(data["history"]) == 2


# ===========================================================================
# runner: in config YAML/JSON
# ===========================================================================

class TestConfigRunner:

    def test_config_local_runner(self, tmp_path):
        cfg = tmp_path / "team.json"
        cfg.write_text(json.dumps({
            "team": "dev", "model": "simulated",
            "runner": {"type": "local", "timeout": 30},
        }))
        from antcrew.config import load
        from antcrew.sandbox.runner import LocalRunner
        team = load(cfg)
        assert isinstance(team._runner, LocalRunner)

    def test_config_docker_runner(self, tmp_path):
        cfg = tmp_path / "team.json"
        cfg.write_text(json.dumps({
            "team": "dev", "model": "simulated",
            "runner": {
                "type": "docker",
                "image": "python:3.11-slim",
                "requirements": ["pytest", "requests"],
                "timeout": 60,
                "network": "none",
                "memory": "256m",
                "cpus": "0.5",
            },
        }))
        from antcrew.config import load
        from antcrew.sandbox.runner import DockerRunner
        team = load(cfg)
        assert isinstance(team._runner, DockerRunner)
        assert team._runner._image == "python:3.11-slim"
        assert "requests" in team._runner._requirements
        assert team._runner._memory == "256m"

    def test_config_docker_runner_defaults(self, tmp_path):
        cfg = tmp_path / "team.json"
        cfg.write_text(json.dumps({
            "team": "dev", "model": "simulated",
            "runner": {"type": "docker"},
        }))
        from antcrew.config import load
        from antcrew.sandbox.runner import DockerRunner
        team = load(cfg)
        assert isinstance(team._runner, DockerRunner)
        assert team._runner._image == "python:3.12-slim"
        assert "pytest" in team._runner._requirements

    def test_config_no_runner_key_means_no_runner(self, tmp_path):
        cfg = tmp_path / "team.json"
        cfg.write_text(json.dumps({"team": "dev", "model": "simulated"}))
        from antcrew.config import load
        team = load(cfg)
        assert team._runner is None

    def test_config_unknown_runner_type_raises(self, tmp_path):
        cfg = tmp_path / "team.json"
        cfg.write_text(json.dumps({
            "team": "dev", "model": "simulated",
            "runner": {"type": "kubernetes"},
        }))
        from antcrew.config import load
        with pytest.raises(ValueError, match="kubernetes"):
            load(cfg)

    def test_config_yaml_runner(self, tmp_path):
        cfg = tmp_path / "team.yaml"
        cfg.write_text(
            "team: dev\nmodel: simulated\n"
            "runner:\n  type: local\n  timeout: 45\n"
        )
        from antcrew.config import load
        from antcrew.sandbox.runner import LocalRunner
        team = load(cfg)
        assert isinstance(team._runner, LocalRunner)
        assert team._runner._timeout == 45

    def test_config_fullstack_docker_runner(self, tmp_path):
        cfg = tmp_path / "team.json"
        cfg.write_text(json.dumps({
            "team": "fullstack", "model": "simulated",
            "runner": {"type": "docker", "timeout": 90},
        }))
        from antcrew.config import load
        from antcrew.sandbox.runner import DockerRunner
        from antcrew.teams.fullstack_team import FullStackTeam
        team = load(cfg)
        assert isinstance(team, FullStackTeam)
        assert isinstance(team._runner, DockerRunner)

    def test_build_runner_local(self):
        from antcrew.config import build_runner
        from antcrew.sandbox.runner import LocalRunner
        r = build_runner({"type": "local", "timeout": 10})
        assert isinstance(r, LocalRunner)
        assert r._timeout == 10

    def test_build_runner_docker(self):
        from antcrew.config import build_runner
        from antcrew.sandbox.runner import DockerRunner
        r = build_runner({"type": "docker", "image": "python:3.11"})
        assert isinstance(r, DockerRunner)
        assert r._image == "python:3.11"

    def test_build_runner_unknown_raises(self):
        from antcrew.config import build_runner
        with pytest.raises(ValueError, match="Unknown runner"):
            build_runner({"type": "podman"})


# ===========================================================================
# Dashboard — test_results in CLI output
# ===========================================================================

class TestDashboardTestResults:

    def test_print_state_shows_test_results_on_success(self, tmp_path):
        """_print_state() must show a green panel when all tests passed."""
        from antcrew.sandbox.runner import RunResult
        from antcrew.cli import _print_state
        from rich.console import Console
        from io import StringIO

        buf = StringIO()
        con = Console(file=buf, highlight=False)

        # Patch the module-level console
        import antcrew.cli as cli_mod
        original = cli_mod.console
        cli_mod.console = con

        try:
            state = {
                "test_results": RunResult(
                    passed=3, failed=0, errors=0, total=3,
                    duration_ms=120.0, output="3 passed in 0.12s", returncode=0
                )
            }
            _print_state(state, "dev")
        finally:
            cli_mod.console = original

        output = buf.getvalue()
        assert "3 passed" in output

    def test_print_state_shows_failure(self, tmp_path):
        from antcrew.sandbox.runner import RunResult
        from antcrew.cli import _print_state
        from rich.console import Console
        from io import StringIO

        buf = StringIO()
        con = Console(file=buf, highlight=False)

        import antcrew.cli as cli_mod
        original = cli_mod.console
        cli_mod.console = con

        try:
            state = {
                "test_results": RunResult(
                    passed=0, failed=2, errors=0, total=2,
                    duration_ms=80.0, output="2 failed in 0.08s", returncode=1
                )
            }
            _print_state(state, "dev")
        finally:
            cli_mod.console = original

        output = buf.getvalue()
        assert "2 failed" in output

    def test_print_test_results_with_dict(self):
        from antcrew.cli import _print_test_results
        from rich.console import Console
        from io import StringIO

        buf = StringIO()
        con = Console(file=buf, highlight=False)

        import antcrew.cli as cli_mod
        original = cli_mod.console
        cli_mod.console = con

        try:
            _print_test_results({
                "passed": 5, "failed": 0, "errors": 0, "total": 5,
                "duration_ms": 200.0, "success": True, "output": "5 passed",
                "returncode": 0,
            })
        finally:
            cli_mod.console = original

        assert "5 passed" in buf.getvalue()

    def test_print_test_results_none_is_noop(self):
        from antcrew.cli import _print_test_results
        # Must not raise
        _print_test_results(None)


# ===========================================================================
# antcrew run --project / --cache integration
# ===========================================================================

class TestRunProjectFlag:

    def test_run_creates_project_file(self, tmp_path):
        proj = tmp_path / "proj.json"
        result = cli.invoke(app, [
            "run", "Build login",
            "--team", "dev", "--model", "simulated",
            "--project", str(proj),
        ])
        assert result.exit_code == 0, result.output
        assert proj.exists()

    def test_run_project_shows_run_number(self, tmp_path):
        proj = tmp_path / "proj.json"
        result = cli.invoke(app, [
            "run", "Build login",
            "--team", "dev", "--model", "simulated",
            "--project", str(proj),
        ])
        assert "run #1" in result.output

    def test_run_project_second_run_accumulates(self, tmp_path):
        proj = tmp_path / "proj.json"
        for req in ("First request", "Second request"):
            cli.invoke(app, [
                "run", req,
                "--team", "dev", "--model", "simulated",
                "--project", str(proj),
            ])
        data = json.loads(proj.read_text())
        assert len(data["history"]) == 2

    def test_run_project_stores_inline_team_spec(self, tmp_path):
        proj = tmp_path / "proj.json"
        cli.invoke(app, [
            "run", "Build",
            "--team", "dev", "--model", "simulated",
            "--project", str(proj),
        ])
        data = json.loads(proj.read_text())
        assert data["team_spec"]["type"] == "inline"
        assert data["team_spec"]["team"] == "dev"

    def test_run_project_shows_project_footer(self, tmp_path):
        proj = tmp_path / "my-proj.json"
        result = cli.invoke(app, [
            "run", "Build",
            "--team", "dev", "--model", "simulated",
            "--project", str(proj),
        ])
        assert "my-proj" in result.output

    def test_run_project_with_config(self, tmp_path):
        cfg = _simulated_config(tmp_path)
        proj = tmp_path / "proj.json"
        result = cli.invoke(app, [
            "run", "Build",
            "--config", str(cfg),
            "--project", str(proj),
        ])
        assert result.exit_code == 0, result.output
        data = json.loads(proj.read_text())
        assert data["team_spec"]["type"] == "config"


class TestRunCacheFlag:

    def test_run_cache_creates_db_file(self, tmp_path):
        db = tmp_path / "cache.db"
        result = cli.invoke(app, [
            "run", "Build login",
            "--team", "dev", "--model", "simulated",
            "--cache", str(db),
        ])
        assert result.exit_code == 0, result.output
        assert db.exists()

    def test_run_cache_db_has_table(self, tmp_path):
        """The SQLite DB must be initialised with the llm_cache table."""
        db = tmp_path / "cache.db"
        cli.invoke(app, [
            "run", "Build login",
            "--team", "dev", "--model", "simulated",
            "--cache", str(db),
        ])
        import sqlite3
        conn = sqlite3.connect(str(db))
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        conn.close()
        assert "llm_cache" in tables

    def test_run_cache_and_project_combined(self, tmp_path):
        db  = tmp_path / "cache.db"
        proj = tmp_path / "proj.json"
        result = cli.invoke(app, [
            "run", "Build login",
            "--team", "dev", "--model", "simulated",
            "--cache", str(db),
            "--project", str(proj),
        ])
        assert result.exit_code == 0, result.output
        assert db.exists()
        assert proj.exists()


class TestConfigCacheKey:

    def test_config_cache_key_attaches_file_cache(self, tmp_path):
        db = tmp_path / "c.db"
        cfg = tmp_path / "team.json"
        cfg.write_text(json.dumps({
            "team": "dev", "model": "simulated",
            "cache": str(db),
        }))
        from antcrew.config import load
        from antcrew.models.cache import FileLLMCache
        team = load(cfg)
        assert isinstance(team.llm.cache, FileLLMCache)
        assert db.exists()

    def test_config_project_key_returns_team_context(self, tmp_path):
        proj = tmp_path / "proj.json"
        cfg = tmp_path / "team.json"
        cfg.write_text(json.dumps({
            "team": "dev", "model": "simulated",
            "project": str(proj),
        }))
        from antcrew.config import load_context, TeamContext
        from antcrew.project import Project
        ctx = load_context(cfg)
        assert isinstance(ctx, TeamContext)
        assert isinstance(ctx.project, Project)

    def test_config_project_key_creates_project_file_on_run(self, tmp_path):
        proj = tmp_path / "proj.json"
        cfg = tmp_path / "team.json"
        cfg.write_text(json.dumps({
            "team": "dev", "model": "simulated",
            "project": str(proj),
        }))
        from antcrew.config import load_context
        ctx = load_context(cfg)
        ctx.project.run("Build login")
        assert proj.exists()

    def test_config_project_key_no_project(self, tmp_path):
        cfg = tmp_path / "team.json"
        cfg.write_text(json.dumps({"team": "dev", "model": "simulated"}))
        from antcrew.config import load_context
        ctx = load_context(cfg)
        assert ctx.project is None

    def test_config_both_cache_and_project(self, tmp_path):
        db   = tmp_path / "c.db"
        proj = tmp_path / "proj.json"
        cfg  = tmp_path / "team.json"
        cfg.write_text(json.dumps({
            "team": "dev", "model": "simulated",
            "cache":   str(db),
            "project": str(proj),
        }))
        from antcrew.config import load_context, TeamContext
        from antcrew.models.cache import FileLLMCache
        from antcrew.project import Project
        ctx = load_context(cfg)
        assert isinstance(ctx, TeamContext)
        assert isinstance(ctx.project, Project)
        assert isinstance(ctx.team.llm.cache, FileLLMCache)

    def test_load_context_loads_existing_project(self, tmp_path):
        """load_context() resumes an existing project file."""
        proj = tmp_path / "proj.json"
        cfg  = tmp_path / "team.json"
        cfg.write_text(json.dumps({
            "team": "dev", "model": "simulated",
            "project": str(proj),
        }))
        from antcrew.config import load_context
        # First call — creates project, runs once
        ctx1 = load_context(cfg)
        ctx1.project.run("Run 1")

        # Second call — loads existing project
        ctx2 = load_context(cfg)
        assert len(ctx2.project.history) == 1

    def test_run_cli_with_config_cache_and_project(self, tmp_path):
        db   = tmp_path / "c.db"
        proj = tmp_path / "proj.json"
        cfg  = tmp_path / "team.yaml"
        cfg.write_text(
            f"team: dev\nmodel: simulated\n"
            f"cache: {db}\n"
            f"project: {proj}\n"
        )
        result = cli.invoke(app, ["run", "Build", "--config", str(cfg)])
        assert result.exit_code == 0, result.output
        assert db.exists()
        assert proj.exists()

    def test_team_context_exports_from_antcrew(self):
        import antcrew
        assert hasattr(antcrew, "TeamContext")
        assert hasattr(antcrew, "load_context")
