"""Tests for CLI HITL mode: _run_with_hitl returns RunResult, --save works."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from antcrew.core.run_result import RunResult

# ---------------------------------------------------------------------------
# _run_with_hitl returns RunResult
# ---------------------------------------------------------------------------

def test_run_with_hitl_returns_run_result():
    """_run_with_hitl must return a RunResult, not a plain dict."""
    from antcrew.cli._run_helpers import _run_with_hitl

    fake_state = {
        "_run_id": "hitl-run-abc",
        "_cost_usd": 0.042,
        "prd": {"title": "Auth PRD"},
    }

    fake_team = MagicMock()
    fake_team._agents = {}
    fake_team.run_interactive.return_value = fake_state

    result = _run_with_hitl(fake_team, "Build auth", "default")

    assert isinstance(result, RunResult)
    assert result.thread_id == "default"
    assert result.cost_usd == pytest.approx(0.042)
    assert result["prd"]["title"] == "Auth PRD"
    assert result.state is fake_state


def test_run_with_hitl_cost_zero_when_missing():
    """cost_usd defaults to 0.0 when _cost_usd is absent from state."""
    from antcrew.cli._run_helpers import _run_with_hitl

    fake_team = MagicMock()
    fake_team._agents = {}
    fake_team.run_interactive.return_value = {"_run_id": "r1"}

    result = _run_with_hitl(fake_team, "task", "t1")
    assert result.cost_usd == 0.0


def test_run_with_hitl_injects_channel_into_all_agents_when_none_marked():
    """When no agent has approval_required, all agents get the channel (force mode)."""
    from antcrew.cli._run_helpers import _run_with_hitl

    agent_a = MagicMock()
    agent_a.approval_required = False
    agent_b = MagicMock()
    agent_b.approval_required = False

    fake_team = MagicMock()
    fake_team._agents = {"a": agent_a, "b": agent_b}
    fake_team.run_interactive.return_value = {}

    _run_with_hitl(fake_team, "task", "t1")

    assert agent_a.approval_required is True
    assert agent_b.approval_required is True


def test_run_with_hitl_only_injects_marked_agents():
    """When some agents have approval_required=True, only those get the channel."""
    from antcrew.cli._run_helpers import _run_with_hitl

    agent_hitl = MagicMock()
    agent_hitl.approval_required = True
    agent_hitl.channel = None

    agent_normal = MagicMock()
    agent_normal.approval_required = False

    fake_team = MagicMock()
    fake_team._agents = {"pm": agent_hitl, "dev": agent_normal}
    fake_team.run_interactive.return_value = {}

    _run_with_hitl(fake_team, "task", "t1")

    # Only the HITL agent gets a channel
    assert agent_hitl.channel is not None
    # Normal agent's approval_required is unchanged
    assert agent_normal.approval_required is False


# ---------------------------------------------------------------------------
# --save works with HITL RunResult
# ---------------------------------------------------------------------------

def test_save_state_works_with_run_result(tmp_path: Path):
    """save_state handles RunResult (the HITL return type) correctly."""
    from antcrew.utils.persistence import load_state, save_state

    state = {"_run_id": "hitl-r1", "prd": {"title": "T"}}
    result = RunResult(state=state, thread_id="default", cost_usd=0.01)

    out = tmp_path / "hitl_state.json"
    save_state(result, out)

    loaded = load_state(out)
    assert loaded.get("prd", {}).get("title") == "T"


# ---------------------------------------------------------------------------
# antcrew serve: registered as a CLI command
# ---------------------------------------------------------------------------

def test_serve_command_registered():
    """antcrew serve must be a registered CLI command."""
    from typer.testing import CliRunner

    from antcrew.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["serve", "--help"])
    assert result.exit_code == 0
    assert "platform" in result.output.lower() or "uvicorn" in result.output.lower() or "port" in result.output.lower()


def test_serve_exits_cleanly_without_uvicorn(monkeypatch):
    """antcrew serve exits with code 1 when uvicorn is not installed."""
    import builtins

    from typer.testing import CliRunner

    from antcrew.cli import app

    original_import = builtins.__import__

    def _block_uvicorn(name, *args, **kwargs):
        if name == "uvicorn":
            raise ImportError("No module named 'uvicorn'")
        return original_import(name, *args, **kwargs)

    runner = CliRunner()
    with patch("builtins.__import__", side_effect=_block_uvicorn):
        result = runner.invoke(app, ["serve"])

    assert result.exit_code == 1
