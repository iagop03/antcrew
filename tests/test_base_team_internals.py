"""Coverage tests for antcrew/teams/base.py — _fmt_time, _sync_run,
_ProgressPanel, and _apply_edit (the HITL-adjacent utilities)."""
from __future__ import annotations

import json

import pytest

from antcrew.models.simulated import SimulatedLLM
from antcrew.teams.dev_team import DevTeam

# ── helpers imported from the private surface ────────────────────────────────

def _import_base():
    import antcrew.teams.base as _base
    return _base


# ── _fmt_time ─────────────────────────────────────────────────────────────────

class TestFmtTime:
    def test_zero(self):
        b = _import_base()
        assert b._fmt_time(0) == "00:00"

    def test_sixty_seconds(self):
        b = _import_base()
        assert b._fmt_time(60) == "01:00"

    def test_ninety_seconds(self):
        b = _import_base()
        assert b._fmt_time(90) == "01:30"

    def test_large_value(self):
        b = _import_base()
        assert b._fmt_time(3661) == "61:01"

    def test_fractional_truncated(self):
        b = _import_base()
        assert b._fmt_time(59.9) == "00:59"


# ── _sync_run ─────────────────────────────────────────────────────────────────

class TestSyncRun:
    def test_returns_value(self):
        b = _import_base()
        async def _coro():
            return 42
        assert b._sync_run(_coro()) == 42

    def test_runs_inside_thread(self):
        b = _import_base()
        async def _coro():
            return "done"
        # Simulates the "no running loop" branch
        result = b._sync_run(_coro())
        assert result == "done"

    @pytest.mark.asyncio
    async def test_runs_from_async_context(self):
        b = _import_base()
        async def _inner():
            return "inner"
        # When a loop IS running, _sync_run uses ThreadPoolExecutor
        result = b._sync_run(_inner())
        assert result == "inner"


# ── _ProgressPanel ────────────────────────────────────────────────────────────

class TestProgressPanel:
    def _panel(self):
        b = _import_base()
        return b._ProgressPanel()

    def test_init(self):
        p = self._panel()
        assert p._cur == ""
        assert p._buf == ""
        assert p._completed == []

    def test_update_sets_cur(self):
        p = self._panel()
        p.update("hello", "backend_dev")
        assert p._cur == "backend_dev"
        assert "hello" in p._buf

    def test_update_agent_change_flushes(self):
        p = self._panel()
        p.update("tok1", "business_analyst")
        p.update("tok2", "pm")
        # previous agent flushed to completed
        assert len(p._completed) == 1
        assert p._completed[0][0] == "business_analyst"

    def test_update_no_agent(self):
        p = self._panel()
        p.update("tok", "")
        # no agent name → no cur set
        assert p._cur == ""

    def test_update_extracts_file_path_hint(self):
        p = self._panel()
        p.update('"file_path": "src/auth.py"', "backend_dev")
        assert p._cur_hint == "src/auth.py"

    def test_flush_current(self):
        p = self._panel()
        p.update("x", "qa")
        p.flush_current()
        assert len(p._completed) == 1
        assert p._cur == ""

    def test_flush_current_when_empty(self):
        p = self._panel()
        p.flush_current()  # Should not raise
        assert p._completed == []

    def test_render_returns_panel(self):
        from rich.panel import Panel
        p = self._panel()
        p.update("writing code…", "backend_dev")
        result = p.render()
        assert isinstance(result, Panel)

    def test_render_with_completed_agents(self):
        from rich.panel import Panel
        p = self._panel()
        p.update("tok", "pm")
        p.flush_current()
        p.update("tok2", "backend_dev")
        result = p.render()
        assert isinstance(result, Panel)

    def test_avg_per_agent_empty(self):
        p = self._panel()
        assert p._avg_per_agent() == 0.0

    def test_avg_per_agent_single(self):
        p = self._panel()
        p.update("x", "pm")
        p.flush_current()
        avg = p._avg_per_agent()
        assert avg >= 0.0

    def test_avg_per_agent_multiple(self):
        p = self._panel()
        p.update("x", "pm")
        p.flush_current()
        p.update("y", "backend_dev")
        p.flush_current()
        avg = p._avg_per_agent()
        assert avg >= 0.0


# ── _apply_edit ───────────────────────────────────────────────────────────────

class TestApplyEdit:
    """Test BaseTeam._apply_edit via DevTeam (which inherits it)."""

    def _team(self):
        return DevTeam(model=SimulatedLLM())

    def _mock_app(self):
        """Minimal mock for a LangGraph app that records update_state calls."""
        updates = {}

        class _MockApp:
            def update_state(self, config, patch):
                updates.update(patch)

        return _MockApp(), updates

    def test_invalid_json_skips(self):
        team = self._team()
        mock_app, updates = self._mock_app()
        team._apply_edit(mock_app, {}, "pm", "not valid json")
        assert updates == {}

    def test_unknown_agent_skips(self):
        team = self._team()
        mock_app, updates = self._mock_app()
        team._apply_edit(mock_app, {}, "unknown_agent_xyz", '{"key": "val"}')
        assert updates == {}

    def test_dict_artifact(self):
        team = self._team()
        mock_app, updates = self._mock_app()
        prd_json = json.dumps({
            "title": "Auth system", "summary": "Provides JWT auth.",
            "goals": [], "out_of_scope": [],
            "functional_requirements": [], "non_functional_requirements": [],
            "open_questions": [],
        })
        team._apply_edit(mock_app, {}, "business_analyst", prd_json)
        assert "prd" in updates

    def test_list_artifact(self):
        team = self._team()
        mock_app, updates = self._mock_app()
        tickets_json = '[{"id": "T-1", "title": "Add auth", "description": "...", "priority": "high", "estimate_days": 1, "labels": []}]'
        team._apply_edit(mock_app, {}, "pm", tickets_json)
        assert "tickets" in updates
        assert len(updates["tickets"]) == 1

    def test_raw_data_fallback(self):
        """A JSON type that doesn't match dict/list with cls → stored raw."""
        team = self._team()
        mock_app, updates = self._mock_app()
        # reviewer produces a "review" key with a CodeReview model; give it a dict
        review_json = '{"summary": "ok", "approved": true, "issues": [], "suggestions": []}'
        team._apply_edit(mock_app, {}, "reviewer", review_json)
        assert "review" in updates
