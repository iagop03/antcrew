"""Tests for antcrew.core.writeback and the antcrew write-back CLI command."""
from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from antcrew.cli._app import app
from antcrew.core.writeback import _smart_apply, write_back

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

class TestResolveRoot:
    """Tests for _resolve_root() in writeback_cmd."""

    def test_explicit_root_wins(self, tmp_path):
        from antcrew.cli.writeback_cmd import _resolve_root
        other = tmp_path / "other"
        other.mkdir()
        state = {"project_dir": str(tmp_path)}
        assert _resolve_root(state, other) == other.resolve()

    def test_falls_back_to_project_dirs(self, tmp_path):
        from antcrew.cli.writeback_cmd import _resolve_root
        state = {"project_dirs": {"backend": str(tmp_path)}}
        assert _resolve_root(state, None) == tmp_path.resolve()

    def test_falls_back_to_project_dir(self, tmp_path):
        from antcrew.cli.writeback_cmd import _resolve_root
        state = {"project_dir": str(tmp_path)}
        assert _resolve_root(state, None) == tmp_path.resolve()

    def test_falls_back_to_cwd_when_no_state_info(self, tmp_path):
        from antcrew.cli.writeback_cmd import _resolve_root
        result = _resolve_root({}, None)
        assert result == Path.cwd()

    def test_ignores_nonexistent_project_dir(self, tmp_path):
        from antcrew.cli.writeback_cmd import _resolve_root
        state = {"project_dir": str(tmp_path / "doesnt_exist")}
        result = _resolve_root(state, None)
        assert result == Path.cwd()


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


# ── _smart_apply (patch mode) ────────────────────────────────────────────────

class TestSmartApply:
    def test_identical_returns_old(self):
        content = "line1\nline2\nline3\n"
        assert _smart_apply(content, content) == content

    def test_changed_line_uses_new(self):
        old = "line1\nline2\nline3\n"
        new = "line1\nLINE2\nline3\n"
        result = _smart_apply(old, new)
        assert "LINE2" in result
        assert "line1" in result
        assert "line3" in result

    def test_equal_lines_kept_from_old(self):
        old = "aaa\nbbb\nccc\n"
        new = "aaa\nBBB\nccc\n"
        result = _smart_apply(old, new)
        # equal lines come from old
        assert result.startswith("aaa\n")
        assert result.endswith("ccc\n")

    def test_insertion_applied(self):
        old = "a\nb\n"
        new = "a\nnew\nb\n"
        result = _smart_apply(old, new)
        assert "new" in result
        assert "a" in result
        assert "b" in result

    def test_deletion_applied(self):
        old = "a\nremove_me\nb\n"
        new = "a\nb\n"
        result = _smart_apply(old, new)
        assert "remove_me" not in result

    def test_empty_old(self):
        result = _smart_apply("", "new content\n")
        assert result == "new content\n"

    def test_empty_new(self):
        result = _smart_apply("old content\n", "")
        assert result == ""

    def test_multiline_block_change(self):
        old = "def foo():\n    return 1\n\ndef bar():\n    return 2\n"
        new = "def foo():\n    return 99\n\ndef bar():\n    return 2\n"
        result = _smart_apply(old, new)
        assert "return 99" in result
        assert "def bar" in result
        assert "return 2" in result


# ── patch-mode in write_back ─────────────────────────────────────────────────

class TestPatchMode:
    def _state(self, path, content):
        return {"code_artifacts": [{"ticket_id": "T-1", "file_path": path,
                                     "content": content, "description": ""}]}

    def test_patch_mode_applies_only_changed_lines(self, tmp_path):
        existing = tmp_path / "app.py"
        existing.write_text("line1\nline2\nline3\n")
        state = self._state("app.py", "line1\nLINE2\nline3\n")
        write_back(state, tmp_path, yes=True, patch_mode=True)
        result = existing.read_text()
        assert "LINE2" in result
        assert "line1" in result
        assert "line3" in result

    def test_patch_mode_skips_truly_identical(self, tmp_path):
        content = "hello\nworld\n"
        existing = tmp_path / "f.py"
        existing.write_text(content)
        state = self._state("f.py", content)
        result = write_back(state, tmp_path, yes=True, patch_mode=True)
        assert result.total_written == 0

    def test_no_patch_mode_replaces_fully(self, tmp_path):
        existing = tmp_path / "f.py"
        existing.write_text("OLD\n")
        state = self._state("f.py", "NEW\n")
        write_back(state, tmp_path, yes=True, patch_mode=False)
        assert existing.read_text() == "NEW\n"

    def test_patch_mode_creates_new_files(self, tmp_path):
        state = self._state("new_file.py", "content\n")
        result = write_back(state, tmp_path, yes=True, patch_mode=True)
        assert result.total_written == 1
        assert (tmp_path / "new_file.py").read_text() == "content\n"


# ── FeedbackLoop pipeline integration ────────────────────────────────────────

class TestFeedbackLoopIntegration:
    def test_dev_team_accepts_feedback_rounds(self):
        from antcrew.models.simulated import SimulatedLLM
        from antcrew.teams.dev_team import DevTeam
        team = DevTeam(model=SimulatedLLM(), feedback_rounds=3)
        assert team._feedback_rounds == 3

    def test_fullstack_team_accepts_feedback_rounds(self):
        from antcrew.models.simulated import SimulatedLLM
        from antcrew.teams.fullstack_team import FullStackTeam
        team = FullStackTeam(model=SimulatedLLM(), feedback_rounds=2)
        assert team._feedback_rounds == 2

    def test_backend_dev_fix_test_failures_no_error(self):
        from antcrew.agents.backend_dev import BackendDevAgent
        from antcrew.models.simulated import SimulatedLLM
        agent = BackendDevAgent(SimulatedLLM())
        assert agent.fix_test_failures({"_feedback_error": ""}) is None

    def test_backend_dev_fix_test_failures_no_artifacts(self):
        from antcrew.agents.backend_dev import BackendDevAgent
        from antcrew.models.simulated import SimulatedLLM
        agent = BackendDevAgent(SimulatedLLM())
        assert agent.fix_test_failures({"_feedback_error": "error!", "code_artifacts": []}) is None

    def test_run_test_feedback_loop_passes_on_success(self):
        from unittest.mock import MagicMock

        from antcrew.core.feedback import run_test_feedback_loop

        mock_results = MagicMock()
        mock_results.success = True
        mock_results.output = ""

        runner = MagicMock()
        runner.run.return_value = mock_results

        agent = MagicMock()
        state = {"test_artifacts": ["t"], "code_artifacts": []}

        final = run_test_feedback_loop(state, agent, runner, max_rounds=3)
        assert final["feedback_ok"] is True
        assert final["feedback_rounds_used"] == 1
        agent.fix_test_failures.assert_not_called()

    def test_run_test_feedback_loop_calls_fix_on_failure(self):
        from unittest.mock import MagicMock

        from antcrew.core.feedback import run_test_feedback_loop

        fail_result = MagicMock()
        fail_result.success = False
        fail_result.output = "FAILED: assertion error"

        pass_result = MagicMock()
        pass_result.success = True
        pass_result.output = ""

        runner = MagicMock()
        runner.run.side_effect = [fail_result, pass_result]

        agent = MagicMock()
        agent.fix_test_failures.return_value = {"code_artifacts": ["fixed"]}

        state = {"test_artifacts": ["t"], "code_artifacts": []}
        final = run_test_feedback_loop(state, agent, runner, max_rounds=3)

        assert final["feedback_ok"] is True
        assert final["feedback_rounds_used"] == 2
        agent.fix_test_failures.assert_called_once()

    def test_run_test_feedback_loop_exhausted(self):
        from unittest.mock import MagicMock

        from antcrew.core.feedback import run_test_feedback_loop

        fail_result = MagicMock()
        fail_result.success = False
        fail_result.output = "always fails"

        runner = MagicMock()
        runner.run.return_value = fail_result

        agent = MagicMock()
        agent.fix_test_failures.return_value = {"code_artifacts": ["fixed"]}

        state = {"test_artifacts": ["t"], "code_artifacts": []}
        final = run_test_feedback_loop(state, agent, runner, max_rounds=2)

        assert final["feedback_ok"] is False
        assert final["feedback_rounds_used"] == 2
