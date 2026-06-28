"""Tests for watch --context and benchmark --context flags."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from antcrew.cli._app import app

runner = CliRunner()


# ── benchmark --context ───────────────────────────────────────────────────────

class TestBenchmarkContext:
    def _cases(self, tmp_path: Path, team: str = "fullstack") -> Path:
        data = [{"request": "Add auth", "team": team, "label": "Auth"}]
        p = tmp_path / "cases.json"; p.write_text(json.dumps(data))
        return p

    def _ctx(self, tmp_path: Path) -> Path:
        ctx = {"label": "backend", "tech_stack": ["FastAPI"], "what_exists": "Auth"}
        p = tmp_path / "ctx.json"; p.write_text(json.dumps(ctx))
        return p

    def test_benchmark_context_missing_file_exits_1(self, tmp_path):
        cases = self._cases(tmp_path)
        result = runner.invoke(app, [
            "benchmark", str(cases),
            "--model", "simulated",
            "--context", str(tmp_path / "nope.json"),
        ])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_benchmark_context_injected_for_fullstack(self, tmp_path):
        cases = self._cases(tmp_path, team="fullstack")
        ctx = self._ctx(tmp_path)
        result = runner.invoke(app, [
            "benchmark", str(cases),
            "--model", "simulated",
            "--context", str(ctx),
        ])
        assert result.exit_code == 0

    def test_benchmark_context_not_applied_to_dev_team(self, tmp_path):
        cases = self._cases(tmp_path, team="dev")
        ctx = self._ctx(tmp_path)
        # dev team ignores --context — should still complete normally
        result = runner.invoke(app, [
            "benchmark", str(cases),
            "--model", "simulated",
            "--context", str(ctx),
        ])
        assert result.exit_code == 0

    def test_benchmark_context_invalid_json_exits_1(self, tmp_path):
        cases = self._cases(tmp_path)
        bad = tmp_path / "bad.json"; bad.write_text("{not json}")
        result = runner.invoke(app, [
            "benchmark", str(cases),
            "--model", "simulated",
            "--context", str(bad),
        ])
        assert result.exit_code == 1
        assert "parse" in result.output.lower() or "Failed" in result.output


# ── watch --context ───────────────────────────────────────────────────────────

class TestWatchContext:
    """watch --context validation tests (no filesystem watching required)."""

    def test_watch_context_missing_file_exits_1(self, tmp_path):
        spec = tmp_path / "spec.md"; spec.write_text("Build auth")
        result = runner.invoke(app, [
            "watch", str(spec),
            "--team", "fullstack",
            "--model", "simulated",
            "--context", str(tmp_path / "nope.json"),
        ])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_watch_context_warns_for_non_fullstack(self, tmp_path, monkeypatch):
        import builtins
        real_import = builtins.__import__

        def _block_watchdog(name, *args, **kwargs):
            if "watchdog" in name:
                raise ImportError("no module named watchdog")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _block_watchdog)

        spec = tmp_path / "spec.md"; spec.write_text("Build auth")
        ctx = tmp_path / "ctx.json"
        ctx.write_text(json.dumps({"label": "api", "tech_stack": ["FastAPI"]}))
        result = runner.invoke(app, [
            "watch", str(spec),
            "--team", "dev",
            "--model", "simulated",
            "--context", str(ctx),
        ])
        # Warning is printed before watchdog is imported, then exits with watchdog error
        assert "Warning" in result.output and "fullstack" in result.output.lower()
        assert result.exit_code == 1

    def test_watch_context_invalid_json_exits_1(self, tmp_path):
        spec = tmp_path / "spec.md"; spec.write_text("Build auth")
        bad = tmp_path / "bad.json"; bad.write_text("{not json}")
        result = runner.invoke(app, [
            "watch", str(spec),
            "--team", "fullstack",
            "--model", "simulated",
            "--context", str(bad),
        ])
        assert result.exit_code == 1
        assert "parse" in result.output.lower() or "Failed" in result.output
