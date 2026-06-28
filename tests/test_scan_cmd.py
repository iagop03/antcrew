"""Tests for antcrew scan and _parse_project_dirs."""
from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from antcrew.cli._app import app
from antcrew.cli.run_cmd import _parse_project_dirs

runner = CliRunner()


# ── _parse_project_dirs ───────────────────────────────────────────────────────

class TestParseProjectDirs:
    def test_none_returns_both_none(self):
        pd, pds = _parse_project_dirs(None)
        assert pd is None and pds is None

    def test_single_bare_path_returns_project_dir(self, tmp_path):
        entry = str(tmp_path)
        pd, pds = _parse_project_dirs([entry])
        assert pd == entry
        assert pds is None

    def test_single_labeled_path_returns_project_dir(self, tmp_path):
        entry = str(tmp_path)
        pd, pds = _parse_project_dirs([f"backend:{entry}"])
        assert pd == entry
        assert pds is None

    def test_multiple_labeled_paths_returns_dict(self, tmp_path):
        a, b = tmp_path / "a", tmp_path / "b"
        a.mkdir(); b.mkdir()
        pd, pds = _parse_project_dirs([f"backend:{a}", f"frontend:{b}"])
        assert pd is None
        assert pds == {"backend": str(a), "frontend": str(b)}

    def test_multiple_bare_paths_use_dir_name_as_label(self, tmp_path):
        a, b = tmp_path / "src", tmp_path / "client"
        a.mkdir(); b.mkdir()
        ea, eb = str(a), str(b)
        pd, pds = _parse_project_dirs([ea, eb])
        assert pds == {"src": ea, "client": eb}

    def test_empty_list_returns_both_none(self):
        pd, pds = _parse_project_dirs([])
        assert pd is None and pds is None

    def test_label_with_windows_drive_letter(self, tmp_path):
        # Colons in drive letters shouldn't confuse the parser
        # (only affects Windows but the function should handle it gracefully)
        pd, pds = _parse_project_dirs([f"myapp:{tmp_path}"])
        assert pd == str(tmp_path)


# ── antcrew scan CLI ──────────────────────────────────────────────────────────

class TestScanCmd:
    def test_scan_shows_file_tree(self, tmp_path):
        (tmp_path / "main.py").write_text("x=1")
        (tmp_path / "README.md").write_text("# Hello")
        result = runner.invoke(app, ["scan", str(tmp_path)])
        assert result.exit_code == 0
        assert "main.py" in result.output or "File tree" in result.output

    def test_scan_detects_key_files(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'")
        result = runner.invoke(app, ["scan", str(tmp_path)])
        assert result.exit_code == 0
        assert "pyproject.toml" in result.output

    def test_scan_named_component(self, tmp_path):
        (tmp_path / "app.py").write_text("x=1")
        result = runner.invoke(app, ["scan", f"myapp:{tmp_path}"])
        assert result.exit_code == 0
        assert "myapp" in result.output

    def test_scan_multiple_dirs(self, tmp_path):
        be = tmp_path / "backend"; be.mkdir()
        fe = tmp_path / "frontend"; fe.mkdir()
        (be / "main.py").write_text("x=1")
        (fe / "index.ts").write_text("export {}")
        result = runner.invoke(app, ["scan", f"backend:{be}", f"frontend:{fe}"])
        assert result.exit_code == 0
        assert "backend" in result.output
        assert "frontend" in result.output

    def test_scan_nonexistent_dir_exits_1(self, tmp_path):
        result = runner.invoke(app, ["scan", str(tmp_path / "doesnt_exist")])
        assert result.exit_code == 1
        assert "Not a directory" in result.output

    def test_no_tree_flag_suppresses_tree(self, tmp_path):
        (tmp_path / "app.py").write_text("x=1")
        result = runner.invoke(app, ["scan", str(tmp_path), "--no-tree"])
        assert result.exit_code == 0
        assert "File tree" not in result.output

    def test_scan_empty_dir(self, tmp_path):
        result = runner.invoke(app, ["scan", str(tmp_path)])
        assert result.exit_code == 0
        assert "No key files" in result.output
