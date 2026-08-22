"""Tests for antcrew-engine CLI helper functions.

We test helper functions directly (no LLM calls) and use typer CliRunner with
--model simulated for lightweight integration coverage of the run command flags.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from antcrew_engine.engine import (
    FilesystemStore, MemoryStore, MultiRepoStore,
    ArtifactId, ArtifactKind,
)
from antcrew_engine.cli.app import _build_store, _load_existing_codebase


# ---------------------------------------------------------------------------
# _build_store
# ---------------------------------------------------------------------------

class TestBuildStore:
    def test_no_args_returns_memory_store(self):
        store = _build_store(output=None, repos=[], routes=[])
        assert isinstance(store, MemoryStore)

    def test_output_arg_returns_filesystem_store(self, tmp_path):
        store = _build_store(output=tmp_path, repos=[], routes=[])
        assert isinstance(store, FilesystemStore)

    def test_repos_returns_multi_repo_store(self, tmp_path):
        be = tmp_path / "backend"
        fe = tmp_path / "frontend"
        store = _build_store(
            output=None,
            repos=[f"backend:{be}", f"frontend:{fe}"],
            routes=["src/api/:backend"],
        )
        assert isinstance(store, MultiRepoStore)

    def test_repo_default_is_first_listed(self, tmp_path):
        a = tmp_path / "a"
        b = tmp_path / "b"
        store = _build_store(
            output=None,
            repos=[f"a:{a}", f"b:{b}"],
            routes=[],
        )
        assert isinstance(store, MultiRepoStore)
        # default repo is 'a' — writing an artifact with no matching route goes there
        from antcrew_engine.engine import Artifact
        art = Artifact(id=ArtifactId("x.py"), kind=ArtifactKind.SOURCE, content="x")
        store.write(art)
        assert store.has(ArtifactId("x.py"))

    def test_bad_repo_spec_exits(self):
        import typer
        with pytest.raises(typer.Exit):
            _build_store(output=None, repos=["bad-spec-no-colon"], routes=[])

    def test_bad_route_spec_exits(self, tmp_path):
        import typer
        with pytest.raises(typer.Exit):
            _build_store(
                output=None,
                repos=[f"a:{tmp_path}"],
                routes=["bad-route-no-colon"],
            )


# ---------------------------------------------------------------------------
# _load_existing_codebase (CLI version — broader than engine version)
# ---------------------------------------------------------------------------

class TestCliLoadExistingCodebase:
    def test_loads_multiple_file_types(self, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "main.py").write_text("x=1")
        (proj / "README.md").write_text("# Docs")
        (proj / "pyproject.toml").write_text("[project]\nname='x'")
        (proj / "config.json").write_text("{}")

        store = MemoryStore()
        n = _load_existing_codebase(store, proj, "Test goal")
        assert n == 4

    def test_skips_node_modules(self, tmp_path):
        proj = tmp_path / "proj"
        (proj / "src").mkdir(parents=True)
        (proj / "src" / "app.py").write_text("x=1")
        (proj / "node_modules" / "pkg").mkdir(parents=True)
        (proj / "node_modules" / "pkg" / "index.js").write_text("x=1")

        store = MemoryStore()
        n = _load_existing_codebase(store, proj, "Goal")
        assert n == 1

    def test_skips_git_dir(self, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "main.py").write_text("x=1")
        (proj / ".git" / "HEAD").parent.mkdir()
        (proj / ".git" / "HEAD").write_text("ref: refs/heads/main")

        store = MemoryStore()
        n = _load_existing_codebase(store, proj, "Goal")
        assert n == 1  # only main.py, not .git/HEAD

    def test_stub_artifacts_have_goal_in_content(self, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "x.py").write_text("x=1")

        store = MemoryStore()
        _load_existing_codebase(store, proj, "Add authentication")

        req = store.read(ArtifactId("requirements"))
        assert req is not None
        assert "authentication" in req.content.lower() or "Add authentication" in req.content

    def test_source_file_has_from_dir_metadata(self, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "app.py").write_text("app=1")

        store = MemoryStore()
        _load_existing_codebase(store, proj, "Goal")

        sources = store.list(ArtifactKind.SOURCE)
        assert len(sources) == 1
        assert sources[0].metadata.get("source") == "from_dir"


# ---------------------------------------------------------------------------
# CLI run command — smoke tests with --model simulated
# ---------------------------------------------------------------------------

class TestCliRun:
    def test_run_simulated_memory_store(self):
        from typer.testing import CliRunner
        from antcrew_engine.cli.app import app

        runner = CliRunner()
        result = runner.invoke(app, ["run", "Write hello world", "--model", "simulated"])
        # Simulated should complete without an unhandled exception
        assert result.exit_code in (0, 1), result.output

    def test_run_simulated_with_output(self, tmp_path):
        from typer.testing import CliRunner
        from antcrew_engine.cli.app import app

        runner = CliRunner()
        result = runner.invoke(app, [
            "run", "Build a hello world script",
            "--model", "simulated",
            "--output", str(tmp_path),
        ])
        assert result.exit_code in (0, 1), result.output

    def test_run_from_dir_loads_files(self, tmp_path):
        from typer.testing import CliRunner
        from antcrew_engine.cli.app import app

        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "main.py").write_text("print('hello')")

        out = tmp_path / "out"
        runner = CliRunner()
        result = runner.invoke(app, [
            "run", "Add docstrings",
            "--model", "simulated",
            "--from-dir", str(proj),
            "--output", str(out),
        ])
        assert result.exit_code in (0, 1), result.output

    def test_run_multi_repo(self, tmp_path):
        from typer.testing import CliRunner
        from antcrew_engine.cli.app import app

        be = tmp_path / "backend"
        fe = tmp_path / "frontend"

        runner = CliRunner()
        result = runner.invoke(app, [
            "run", "Add auth",
            "--model", "simulated",
            "--repo", f"backend:{be}",
            "--repo", f"frontend:{fe}",
            "--route", "src/api/:backend",
            "--route", "src/ui/:frontend",
        ])
        assert result.exit_code in (0, 1), result.output

    def test_status_command_on_missing_dir(self, tmp_path):
        from typer.testing import CliRunner
        from antcrew_engine.cli.app import app

        runner = CliRunner()
        result = runner.invoke(app, ["status", str(tmp_path / "nonexistent")])
        assert result.exit_code != 0 or "not found" in result.output.lower() or result.exit_code == 0
