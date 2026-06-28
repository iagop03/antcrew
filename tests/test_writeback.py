"""Tests for antcrew.core.writeback and the antcrew write-back CLI command."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from antcrew.cli._app import app
from antcrew.core.writeback import WriteBackResult, write_back

runner = CliRunner()


# ── helpers ───────────────────────────────────────────────────────────────────

def _state_with_artifacts(
    code: list[dict] | None = None,
    tests: list[dict] | None = None,
    devops: list[dict] | None = None,
    docs: list[dict] | None = None,
) -> dict:
    return {
        "code_artifacts": [
            {"ticket_id": "T-1", "file_path": a["file_path"], "content": a["content"], "description": ""}
            for a in (code or [])
        ],
        "test_artifacts": [
            {"ticket_id": "T-1", "file_path": a["file_path"], "content": a["content"], "description": ""}
            for a in (tests or [])
        ],
        "devops_artifacts": [
            {"file_path": a["file_path"], "content": a["content"], "description": "", "language": "yaml"}
            for a in (devops or [])
        ],
        "documentation_artifacts": [
            {"file_path": a["file_path"], "content": a["content"], "title": "doc", "doc_type": "readme"}
            for a in (docs or [])
        ],
    }


# ── write_back() unit tests ───────────────────────────────────────────────────

class TestWriteBack:
    def test_creates_new_file(self, tmp_path):
        state = _state_with_artifacts(code=[{"file_path": "src/auth.py", "content": "# auth"}])
        result = write_back(state, tmp_path, dry_run=False, yes=True)
        assert (tmp_path / "src" / "auth.py").read_text() == "# auth"
        assert result.total_written == 1
        assert len(result.created) == 1

    def test_creates_nested_directories(self, tmp_path):
        state = _state_with_artifacts(code=[{"file_path": "a/b/c/module.py", "content": "x=1"}])
        write_back(state, tmp_path, dry_run=False, yes=True)
        assert (tmp_path / "a" / "b" / "c" / "module.py").exists()

    def test_overwrites_existing_file_with_yes(self, tmp_path):
        target = tmp_path / "main.py"
        target.write_text("old content")
        state = _state_with_artifacts(code=[{"file_path": "main.py", "content": "new content"}])
        result = write_back(state, tmp_path, dry_run=False, yes=True)
        assert target.read_text() == "new content"
        assert result.modified[0].file_path == "main.py"

    def test_dry_run_does_not_write(self, tmp_path):
        state = _state_with_artifacts(code=[{"file_path": "src/x.py", "content": "x"}])
        result = write_back(state, tmp_path, dry_run=True)
        assert not (tmp_path / "src" / "x.py").exists()
        assert len(result.entries) == 1
        assert not result.entries[0].written

    def test_operation_is_modify_for_existing_file(self, tmp_path):
        (tmp_path / "existing.py").write_text("old")
        state = _state_with_artifacts(code=[{"file_path": "existing.py", "content": "new"}])
        result = write_back(state, tmp_path, dry_run=True)
        assert result.entries[0].operation == "modify"

    def test_operation_is_create_for_new_file(self, tmp_path):
        state = _state_with_artifacts(code=[{"file_path": "new_file.py", "content": "x"}])
        result = write_back(state, tmp_path, dry_run=True)
        assert result.entries[0].operation == "create"

    def test_skips_file_when_confirm_returns_false(self, tmp_path):
        (tmp_path / "keep.py").write_text("keep this")
        state = _state_with_artifacts(code=[{"file_path": "keep.py", "content": "overwrite"}])
        result = write_back(state, tmp_path, dry_run=False, yes=False, confirm_fn=lambda _: False)
        assert (tmp_path / "keep.py").read_text() == "keep this"
        assert result.skipped[0].file_path == "keep.py"

    def test_writes_file_when_confirm_returns_true(self, tmp_path):
        (tmp_path / "ok.py").write_text("old")
        state = _state_with_artifacts(code=[{"file_path": "ok.py", "content": "new"}])
        result = write_back(state, tmp_path, dry_run=False, yes=False, confirm_fn=lambda _: True)
        assert (tmp_path / "ok.py").read_text() == "new"
        assert result.total_written == 1

    def test_no_change_file_is_skipped_without_prompt(self, tmp_path):
        content = "unchanged content"
        (tmp_path / "same.py").write_text(content)
        state = _state_with_artifacts(code=[{"file_path": "same.py", "content": content}])
        confirmed = []
        result = write_back(state, tmp_path, dry_run=False, yes=False,
                            confirm_fn=lambda _: confirmed.append(True) or True)
        assert len(confirmed) == 0  # no prompt because diff is empty
        assert result.skipped[0].file_path == "same.py"

    def test_all_artifact_types(self, tmp_path):
        state = _state_with_artifacts(
            code=[{"file_path": "src/a.py", "content": "a"}],
            tests=[{"file_path": "tests/test_a.py", "content": "ta"}],
            devops=[{"file_path": ".github/ci.yml", "content": "ci"}],
            docs=[{"file_path": "docs/README.md", "content": "# doc"}],
        )
        result = write_back(state, tmp_path, dry_run=False, yes=True)
        assert result.total_written == 4
        assert (tmp_path / "src" / "a.py").exists()
        assert (tmp_path / "tests" / "test_a.py").exists()
        assert (tmp_path / ".github" / "ci.yml").exists()
        assert (tmp_path / "docs" / "README.md").exists()

    def test_leading_slash_stripped_from_path(self, tmp_path):
        state = _state_with_artifacts(code=[{"file_path": "/src/auth.py", "content": "x"}])
        result = write_back(state, tmp_path, dry_run=False, yes=True)
        assert (tmp_path / "src" / "auth.py").exists()
        assert result.total_written == 1

    def test_empty_state_returns_empty_result(self, tmp_path):
        result = write_back({}, tmp_path, dry_run=False, yes=True)
        assert result.total_written == 0
        assert result.entries == []

    def test_result_properties(self, tmp_path):
        (tmp_path / "existing.py").write_text("old")
        state = _state_with_artifacts(
            code=[
                {"file_path": "new.py", "content": "new"},
                {"file_path": "existing.py", "content": "updated"},
            ],
        )
        result = write_back(state, tmp_path, dry_run=False, yes=True)
        assert len(result.created) == 1
        assert len(result.modified) == 1
        assert result.total_written == 2


# ── CLI: antcrew write-back ───────────────────────────────────────────────────

class TestWriteBackCmd:
    def _write_state(self, tmp_path: Path, state: dict) -> Path:
        p = tmp_path / "state.json"
        p.write_text(json.dumps(state), encoding="utf-8")
        return p

    def test_basic_write(self, tmp_path):
        state = _state_with_artifacts(code=[{"file_path": "out.py", "content": "hello"}])
        p = self._write_state(tmp_path, state)
        result = runner.invoke(app, ["write-back", str(p), "--project-root", str(tmp_path), "--yes"])
        assert result.exit_code == 0
        assert (tmp_path / "out.py").read_text() == "hello"

    def test_dry_run_does_not_write(self, tmp_path):
        state = _state_with_artifacts(code=[{"file_path": "out.py", "content": "hello"}])
        p = self._write_state(tmp_path, state)
        result = runner.invoke(app, ["write-back", str(p), "--project-root", str(tmp_path), "--dry-run"])
        assert result.exit_code == 0
        assert not (tmp_path / "out.py").exists()
        assert "dry run" in result.output.lower() or "would" in result.output.lower()

    def test_missing_state_file_exits_1(self, tmp_path):
        result = runner.invoke(app, ["write-back", str(tmp_path / "missing.json")])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_done_summary_shown(self, tmp_path):
        state = _state_with_artifacts(code=[{"file_path": "f.py", "content": "x"}])
        p = self._write_state(tmp_path, state)
        result = runner.invoke(app, ["write-back", str(p), "--project-root", str(tmp_path), "--yes"])
        assert result.exit_code == 0
        assert "Done" in result.output or "1" in result.output

    def test_dry_run_shows_file_count(self, tmp_path):
        state = _state_with_artifacts(
            code=[{"file_path": "a.py", "content": "a"}],
            tests=[{"file_path": "test_a.py", "content": "t"}],
        )
        p = self._write_state(tmp_path, state)
        result = runner.invoke(app, ["write-back", str(p), "--project-root", str(tmp_path), "--dry-run"])
        assert result.exit_code == 0
        assert "2" in result.output
