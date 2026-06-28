"""Tests for v0.11.3: trace --prune, export, plugin system, structured output, agent cost cap."""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel
from typer.testing import CliRunner

from antcrew.cli._app import app
from antcrew.trace import TraceLog

runner = CliRunner()


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_db(tmp_path: Path) -> Path:
    tl = TraceLog(tmp_path / "t.db")
    for i, (team, ts) in enumerate([
        ("dev",      "2020-01-01T00:00:00+00:00"),
        ("dev",      "2099-01-01T00:00:00+00:00"),
        ("research", "2020-06-01T00:00:00+00:00"),
    ]):
        run_id = str(uuid.uuid4())
        tl._conn.execute(
            "INSERT INTO runs(id, thread_id, request, team, started_at, status, cost_usd) "
            "VALUES (?,?,?,?,?,?,?)",
            (run_id, f"t{i}", f"req{i}", team, ts, "done", 0.01 * (i + 1)),
        )
    tl._conn.commit()
    tl.close()
    return tmp_path / "t.db"


# ── antcrew trace --prune flag ────────────────────────────────────────────────

class TestTracePruneFlag:
    def test_prune_deletes_old(self, tmp_path):
        db = _make_db(tmp_path)
        result = runner.invoke(app, ["trace", str(db), "--prune", "365", "--yes"])
        assert result.exit_code == 0
        assert "Deleted" in result.output
        tl = TraceLog(db)
        assert len(tl.list_runs()) == 1  # only the 2099 run remains
        tl.close()

    def test_prune_zero_deletes_past_runs(self, tmp_path):
        db = _make_db(tmp_path)
        result = runner.invoke(app, ["trace", str(db), "--prune", "0", "--yes"])
        assert result.exit_code == 0
        assert "Deleted" in result.output
        tl = TraceLog(db)
        # 2020 runs are deleted; the 2099 run is still in the future so kept
        remaining = tl.list_runs()
        assert all("2099" in r["started_at"] for r in remaining)
        tl.close()

    def test_trace_list_still_works(self, tmp_path):
        db = _make_db(tmp_path)
        result = runner.invoke(app, ["trace", str(db)])
        assert result.exit_code == 0

    def test_trace_db_subcommand_gone(self):
        result = runner.invoke(app, ["trace-db", "prune", "nonexistent.db", "30"])
        assert result.exit_code != 0


# ── antcrew export ────────────────────────────────────────────────────────────

class TestTraceDump:
    """antcrew trace DB --dump FORMAT tests."""

    def test_json_to_stdout(self, tmp_path):
        db = _make_db(tmp_path)
        result = runner.invoke(app, ["trace", str(db), "--dump", "json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list) and len(data) == 3

    def test_csv_to_file(self, tmp_path):
        db = _make_db(tmp_path)
        out = tmp_path / "out.csv"
        result = runner.invoke(app, ["trace", str(db), "--dump", "csv", "--dump-output", str(out)])
        assert result.exit_code == 0
        assert out.exists()
        assert "thread_id" in out.read_text()

    def test_filter_team(self, tmp_path):
        db = _make_db(tmp_path)
        result = runner.invoke(app, ["trace", str(db), "--dump", "json", "--dump-team", "research"])
        data = json.loads(result.output)
        assert len(data) == 1 and data[0]["team"] == "research"

    def test_since_filters_old(self, tmp_path):
        db = _make_db(tmp_path)
        result = runner.invoke(app, ["trace", str(db), "--dump", "json", "--dump-since", "30"])
        data = json.loads(result.output)
        assert all("2099" in r["started_at"] for r in data)

    def test_include_calls(self, tmp_path):
        db = _make_db(tmp_path)
        result = runner.invoke(app, ["trace", str(db), "--dump", "json", "--dump-calls"])
        data = json.loads(result.output)
        assert all("agent_calls" in r for r in data)

    def test_csv_with_calls_has_agent_name_column(self, tmp_path):
        db = _make_db(tmp_path)
        out = tmp_path / "calls.csv"
        result = runner.invoke(app, ["trace", str(db), "--dump", "csv", "--dump-calls", "--dump-output", str(out)])
        assert result.exit_code == 0
        assert "agent_name" in out.read_text()

    def test_bad_format_rejected(self, tmp_path):
        db = _make_db(tmp_path)
        result = runner.invoke(app, ["trace", str(db), "--dump", "xml"])
        assert result.exit_code != 0 or "Unknown format" in result.output

    def test_output_flag_writes_file_and_prints_message(self, tmp_path):
        db = _make_db(tmp_path)
        out = tmp_path / "runs.json"
        result = runner.invoke(app, ["trace", str(db), "--dump", "json", "--dump-output", str(out)])
        assert result.exit_code == 0 and out.exists()
        assert "Dumped" in result.output


# ── Plugin system ─────────────────────────────────────────────────────────────

class TestPluginSystem:
    def test_plugin_registered(self):
        from antcrew.agents import registry as reg

        fake_ep = MagicMock()
        fake_ep.name = "test_plugin_xyz"
        fake_ep.value = "antcrew.agents.doc_writer:DocWriterAgent"

        reg.AGENT_REGISTRY.pop("test_plugin_xyz", None)
        with patch("importlib.metadata.entry_points", return_value=[fake_ep]):
            reg._load_plugins()
        assert "test_plugin_xyz" in reg.AGENT_REGISTRY
        reg.AGENT_REGISTRY.pop("test_plugin_xyz", None)

    def test_builtin_not_overwritten(self):
        from antcrew.agents import registry as reg

        fake_ep = MagicMock()
        fake_ep.name = "pm"
        fake_ep.value = "some.pkg:SomeAgent"

        original = reg.AGENT_REGISTRY["pm"]
        with patch("importlib.metadata.entry_points", return_value=[fake_ep]):
            reg._load_plugins()
        assert reg.AGENT_REGISTRY["pm"] == original

    def test_bad_value_skipped(self):
        from antcrew.agents import registry as reg

        fake_ep = MagicMock()
        fake_ep.name = "bad_plugin_xyz"
        fake_ep.value = "no_colon_here"

        with patch("importlib.metadata.entry_points", return_value=[fake_ep]):
            reg._load_plugins()
        assert "bad_plugin_xyz" not in reg.AGENT_REGISTRY

    def test_load_error_handled(self):
        from antcrew.agents import registry as reg
        with patch("importlib.metadata.entry_points", side_effect=Exception("boom")):
            reg._load_plugins()  # must not raise

    def test_get_agent_class_intact(self):
        from antcrew.agents.registry import get_agent_class
        cls = get_agent_class("pm")
        assert cls.__name__ == "PMAgent"


# ── Structured output ─────────────────────────────────────────────────────────

class _Out(BaseModel):
    title: str
    count: int


def _mock_agent(responses=None):
    from antcrew.agents.doc_writer import DocWriterAgent
    llm = MagicMock()
    if responses:
        llm.system.side_effect = list(responses)
    else:
        llm.system.return_value = '{"title": "x", "count": 1}'
    llm._usage_log = []
    llm.current_agent = ""
    agent = DocWriterAgent(llm=llm)
    agent.max_cost_usd = None
    return agent


class TestStructuredOutput:
    def test_returns_model_instance(self):
        agent = _mock_agent()
        result = agent.system_structured("sys", "user", schema=_Out)
        assert isinstance(result, _Out)
        assert result.title == "x" and result.count == 1

    def test_uses_output_schema_attr(self):
        from antcrew.agents.doc_writer import DocWriterAgent
        llm = MagicMock()
        llm.system.return_value = '{"title": "hi", "count": 5}'
        llm._usage_log = []
        agent = DocWriterAgent(llm=llm, output_schema=_Out)
        result = agent.system_structured("sys", "user")
        assert isinstance(result, _Out)

    def test_no_schema_raises(self):
        agent = _mock_agent()
        with pytest.raises(ValueError, match="schema"):
            agent.system_structured("sys", "user")

    def test_retries_on_invalid(self):
        from antcrew.agents.doc_writer import DocWriterAgent
        llm = MagicMock()
        llm.system.side_effect = [
            "not json",
            '{"title": "retry_ok", "count": 2}',
        ]
        llm._usage_log = []
        agent = DocWriterAgent(llm=llm, output_schema=_Out)
        result = agent.system_structured("sys", "user", max_retries=1)
        assert result.title == "retry_ok"

    def test_output_schema_in_constructor(self):
        from antcrew.agents.doc_writer import DocWriterAgent
        llm = MagicMock()
        llm._usage_log = []
        agent = DocWriterAgent(llm=llm, output_schema=_Out)
        assert agent.output_schema is _Out


# ── Per-agent cost cap ────────────────────────────────────────────────────────

class TestAgentCostCap:
    def _agent(self, cap, spent):
        from antcrew.agents.doc_writer import DocWriterAgent
        llm = MagicMock()
        llm.system.return_value = "ok"
        llm._usage_log = [{"agent": "doc_writer", "cost_usd": spent}]
        llm.current_agent = ""
        return DocWriterAgent(llm=llm, max_cost_usd=cap)

    def test_under_limit_ok(self):
        a = self._agent(1.0, 0.5)
        a._check_agent_cost()  # no raise

    def test_over_limit_raises(self):
        from antcrew.core.exceptions import CostLimitExceeded
        a = self._agent(0.01, 0.5)
        with pytest.raises(CostLimitExceeded):
            a._check_agent_cost()

    def test_no_cap_never_raises(self):
        a = self._agent(None, 9999.0)  # type: ignore[arg-type]
        a.max_cost_usd = None
        a._check_agent_cost()

    def test_system_raises_after_call(self):
        from antcrew.agents.doc_writer import DocWriterAgent
        from antcrew.core.exceptions import CostLimitExceeded
        llm = MagicMock()
        llm._usage_log = []
        llm.current_agent = ""

        def fake_sys(sp, u, **kw):
            llm._usage_log.append({"agent": "doc_writer", "cost_usd": 0.5})
            return "done"

        llm.system = fake_sys
        agent = DocWriterAgent(llm=llm, max_cost_usd=0.1)
        with pytest.raises(CostLimitExceeded):
            agent.system("sp", "u")

    def test_cost_only_counted_for_this_agent(self):
        from antcrew.agents.doc_writer import DocWriterAgent
        llm = MagicMock()
        # Other agent used 5.0, this agent used 0.05
        llm._usage_log = [
            {"agent": "other_agent", "cost_usd": 5.0},
            {"agent": "doc_writer", "cost_usd": 0.05},
        ]
        llm.current_agent = ""
        agent = DocWriterAgent(llm=llm, max_cost_usd=0.10)
        agent._check_agent_cost()  # 0.05 < 0.10, should not raise
