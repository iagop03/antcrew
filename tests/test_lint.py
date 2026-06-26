"""Tests for antcrew.linter and `antcrew lint` CLI command."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from antcrew.cli import app
from antcrew.linter import LintError, lint_config, _detect_cycle

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write(tmp: Path, content: dict | str, name: str = "cfg.yaml") -> Path:
    p = tmp / name
    if isinstance(content, dict):
        p.write_text(json.dumps(content), encoding="utf-8")
    else:
        p.write_text(content, encoding="utf-8")
    return p


def _errors(issues):
    return [i for i in issues if i.severity == "error"]


def _warnings(issues):
    return [i for i in issues if i.severity == "warning"]


# ===========================================================================
# lint_config — valid config
# ===========================================================================

def test_valid_minimal_config(tmp_path):
    p = _write(tmp_path, {"team": "dev", "model": "simulated"})
    issues = lint_config(p)
    assert _errors(issues) == []


def test_valid_fullstack_config(tmp_path):
    cfg = {"team": "fullstack", "model": "claude"}
    p = _write(tmp_path, cfg)
    issues = lint_config(p)
    assert _errors(issues) == []


def test_valid_research_config(tmp_path):
    p = _write(tmp_path, {"team": "research", "model": "gpt-4o"})
    issues = lint_config(p)
    assert _errors(issues) == []


def test_valid_content_config(tmp_path):
    p = _write(tmp_path, {"team": "content", "model": "gemini-1.5-flash"})
    issues = lint_config(p)
    assert _errors(issues) == []


# ===========================================================================
# File not found
# ===========================================================================

def test_missing_file_returns_error(tmp_path):
    issues = lint_config(tmp_path / "nope.yaml")
    assert any(i.severity == "error" and "not found" in i.message.lower() for i in issues)


# ===========================================================================
# team validation
# ===========================================================================

def test_unknown_team_is_error(tmp_path):
    p = _write(tmp_path, {"team": "unicorn", "model": "simulated"})
    issues = lint_config(p)
    assert any(i.severity == "error" and "unicorn" in i.message for i in issues)


def test_missing_team_is_info(tmp_path):
    p = _write(tmp_path, {"model": "simulated"})
    issues = lint_config(p)
    infos = [i for i in issues if i.severity == "info"]
    assert any("team" in i.message for i in infos)


# ===========================================================================
# model validation
# ===========================================================================

def test_unknown_model_is_error(tmp_path):
    p = _write(tmp_path, {"team": "dev", "model": "xyzzy-model"})
    issues = lint_config(p)
    assert any(i.severity == "error" and "xyzzy-model" in i.message for i in issues)


def test_simulated_model_ok(tmp_path):
    p = _write(tmp_path, {"team": "dev", "model": "simulated"})
    assert _errors(lint_config(p)) == []


def test_ollama_prefix_ok(tmp_path):
    p = _write(tmp_path, {"team": "dev", "model": "ollama:llama3"})
    assert _errors(lint_config(p)) == []


def test_groq_prefix_ok(tmp_path):
    p = _write(tmp_path, {"team": "dev", "model": "groq:llama3-70b"})
    assert _errors(lint_config(p)) == []


def test_gpt_prefix_ok(tmp_path):
    p = _write(tmp_path, {"team": "dev", "model": "gpt-4o"})
    assert _errors(lint_config(p)) == []


def test_o3_mini_ok(tmp_path):
    p = _write(tmp_path, {"team": "dev", "model": "o3-mini"})
    assert _errors(lint_config(p)) == []


def test_azure_prefix_ok(tmp_path):
    p = _write(tmp_path, {"team": "dev", "model": "azure:my-gpt4o"})
    assert _errors(lint_config(p)) == []


def test_missing_model_is_info(tmp_path):
    p = _write(tmp_path, {"team": "dev"})
    issues = lint_config(p)
    infos = [i for i in issues if i.severity == "info"]
    assert any("model" in i.message for i in infos)


# ===========================================================================
# max_cost_usd
# ===========================================================================

def test_valid_max_cost(tmp_path):
    p = _write(tmp_path, {"team": "dev", "model": "simulated", "max_cost_usd": 2.5})
    assert _errors(lint_config(p)) == []


def test_negative_max_cost_is_error(tmp_path):
    p = _write(tmp_path, {"team": "dev", "model": "simulated", "max_cost_usd": -1})
    issues = lint_config(p)
    assert any(i.severity == "error" and "max_cost_usd" in i.message for i in issues)


def test_zero_max_cost_is_error(tmp_path):
    p = _write(tmp_path, {"team": "dev", "model": "simulated", "max_cost_usd": 0})
    issues = lint_config(p)
    assert any(i.severity == "error" and "max_cost_usd" in i.message for i in issues)


def test_string_max_cost_is_error(tmp_path):
    p = _write(tmp_path, {"team": "dev", "model": "simulated", "max_cost_usd": "lots"})
    issues = lint_config(p)
    assert any(i.severity == "error" and "max_cost_usd" in i.message for i in issues)


# ===========================================================================
# agents
# ===========================================================================

def test_valid_agent_override(tmp_path):
    cfg = {
        "team": "dev", "model": "simulated",
        "agents": {"backend_dev": {"model": "simulated", "approval_required": True}},
    }
    p = _write(tmp_path, cfg)
    assert _errors(lint_config(p)) == []


def test_unknown_agent_name_is_warning(tmp_path):
    cfg = {"team": "dev", "model": "simulated", "agents": {"my_agent": {"model": "simulated"}}}
    p = _write(tmp_path, cfg)
    issues = lint_config(p)
    assert any(i.severity == "warning" and "my_agent" in i.message for i in issues)


def test_agent_unknown_model_is_error(tmp_path):
    cfg = {"team": "dev", "model": "simulated", "agents": {"backend_dev": {"model": "badmodel"}}}
    p = _write(tmp_path, cfg)
    issues = lint_config(p)
    assert any(i.severity == "error" and "badmodel" in i.message for i in issues)


def test_agent_valid_preset(tmp_path):
    cfg = {"team": "dev", "model": "simulated", "agents": {"pm": {"preset": "strict"}}}
    p = _write(tmp_path, cfg)
    assert _errors(lint_config(p)) == []


def test_agent_unknown_preset_is_warning(tmp_path):
    cfg = {"team": "dev", "model": "simulated", "agents": {"pm": {"preset": "ultrafast"}}}
    p = _write(tmp_path, cfg)
    issues = lint_config(p)
    assert any(i.severity == "warning" and "ultrafast" in i.message for i in issues)


# ===========================================================================
# flow
# ===========================================================================

def test_valid_flow(tmp_path):
    cfg = {
        "team": "dev", "model": "simulated",
        "flow": [["pm", "backend_dev"], ["backend_dev", "qa"]],
    }
    p = _write(tmp_path, cfg)
    assert _errors(lint_config(p)) == []


def test_flow_cycle_is_error(tmp_path):
    cfg = {
        "team": "dev", "model": "simulated",
        "flow": [["a", "b"], ["b", "c"], ["c", "a"]],
    }
    p = _write(tmp_path, cfg)
    issues = lint_config(p)
    assert any(i.severity == "error" and "cycle" in i.message.lower() for i in issues)


def test_flow_not_a_list_is_error(tmp_path):
    cfg = {"team": "dev", "model": "simulated", "flow": "bad"}
    p = _write(tmp_path, cfg)
    issues = lint_config(p)
    assert any(i.severity == "error" and "list" in i.message.lower() for i in issues)


# ===========================================================================
# channel
# ===========================================================================

def test_valid_console_channel(tmp_path):
    cfg = {"team": "dev", "model": "simulated", "channel": {"type": "console"}}
    p = _write(tmp_path, cfg)
    assert _errors(lint_config(p)) == []


def test_unknown_channel_type_is_error(tmp_path):
    cfg = {"team": "dev", "model": "simulated", "channel": {"type": "discord"}}
    p = _write(tmp_path, cfg)
    issues = lint_config(p)
    assert any(i.severity == "error" and "discord" in i.message for i in issues)


def test_missing_channel_type_is_error(tmp_path):
    cfg = {"team": "dev", "model": "simulated", "channel": {"token": "abc"}}
    p = _write(tmp_path, cfg)
    issues = lint_config(p)
    assert any(i.severity == "error" and "type" in i.message for i in issues)


# ===========================================================================
# runner
# ===========================================================================

def test_valid_local_runner(tmp_path):
    cfg = {"team": "dev", "model": "simulated", "runner": {"type": "local"}}
    p = _write(tmp_path, cfg)
    assert _errors(lint_config(p)) == []


def test_valid_docker_runner(tmp_path):
    cfg = {"team": "dev", "model": "simulated", "runner": {"type": "docker"}}
    p = _write(tmp_path, cfg)
    assert _errors(lint_config(p)) == []


def test_unknown_runner_type_is_error(tmp_path):
    cfg = {"team": "dev", "model": "simulated", "runner": {"type": "kubernetes"}}
    p = _write(tmp_path, cfg)
    issues = lint_config(p)
    assert any(i.severity == "error" and "kubernetes" in i.message for i in issues)


# ===========================================================================
# Unresolved env vars
# ===========================================================================

def test_unresolved_env_var_is_warning(tmp_path, monkeypatch):
    monkeypatch.delenv("MY_SECRET_TOKEN", raising=False)
    cfg_text = "team: dev\nmodel: simulated\nchannel:\n  type: telegram\n  token: ${MY_SECRET_TOKEN}\n  chat_id: '123'\n"
    p = tmp_path / "cfg.yaml"
    p.write_text(cfg_text, encoding="utf-8")
    issues = lint_config(p)
    assert any(i.severity == "warning" and "MY_SECRET_TOKEN" in i.message for i in issues)


# ===========================================================================
# _detect_cycle
# ===========================================================================

def test_detect_cycle_simple():
    flow = [("a", "b"), ("b", "c"), ("c", "a")]
    cycle = _detect_cycle(flow)
    assert cycle is not None
    assert "a" in cycle


def test_detect_cycle_self_loop():
    flow = [("a", "a")]
    cycle = _detect_cycle(flow)
    assert cycle is not None


def test_detect_no_cycle():
    flow = [("a", "b"), ("b", "c"), ("a", "c")]
    assert _detect_cycle(flow) is None


def test_detect_cycle_empty():
    assert _detect_cycle([]) is None


# ===========================================================================
# CLI: antcrew lint
# ===========================================================================

def test_lint_cmd_clean_config(tmp_path):
    p = _write(tmp_path, {"team": "dev", "model": "simulated"})
    result = runner.invoke(app, ["lint", str(p)])
    assert result.exit_code == 0
    assert "no issues" in result.output.lower() or "✓" in result.output


def test_lint_cmd_error_exits_1(tmp_path):
    p = _write(tmp_path, {"team": "bad_team", "model": "simulated"})
    result = runner.invoke(app, ["lint", str(p)])
    assert result.exit_code == 1


def test_lint_cmd_warning_exits_0(tmp_path):
    cfg = {"team": "dev", "model": "simulated", "agents": {"custom_agent": {"model": "simulated"}}}
    p = _write(tmp_path, cfg)
    result = runner.invoke(app, ["lint", str(p)])
    assert result.exit_code == 0


def test_lint_cmd_warning_strict_exits_1(tmp_path):
    cfg = {"team": "dev", "model": "simulated", "agents": {"custom_agent": {"model": "simulated"}}}
    p = _write(tmp_path, cfg)
    result = runner.invoke(app, ["lint", str(p), "--strict"])
    assert result.exit_code == 1


def test_lint_cmd_missing_file_exits_1(tmp_path):
    result = runner.invoke(app, ["lint", str(tmp_path / "nope.yaml")])
    assert result.exit_code == 1


def test_lint_cmd_shows_error_messages(tmp_path):
    p = _write(tmp_path, {"team": "dev", "model": "totally-unknown-model"})
    result = runner.invoke(app, ["lint", str(p)])
    assert "totally-unknown-model" in result.output


def test_lint_cmd_shows_warning_symbol(tmp_path):
    cfg = {"team": "dev", "model": "simulated", "agents": {"unknown_agent": {"model": "simulated"}}}
    p = _write(tmp_path, cfg)
    result = runner.invoke(app, ["lint", str(p)])
    assert "⚠" in result.output or "warning" in result.output.lower()


def test_lint_cmd_quiet_hides_info(tmp_path):
    p = _write(tmp_path, {"model": "simulated"})  # no team key → info message
    result = runner.invoke(app, ["lint", str(p), "--quiet"])
    assert "team" not in result.output.lower() or "no issues" in result.output.lower()


def test_lint_cmd_cycle_shows_error(tmp_path):
    cfg = {"team": "dev", "model": "simulated", "flow": [["a", "b"], ["b", "a"]]}
    p = _write(tmp_path, cfg)
    result = runner.invoke(app, ["lint", str(p)])
    assert result.exit_code == 1
    assert "cycle" in result.output.lower()


def test_lint_cmd_default_filename(tmp_path, monkeypatch):
    """Defaults to agentteam.yaml in cwd when no argument given."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "agentteam.yaml").write_text(
        json.dumps({"team": "dev", "model": "simulated"}), encoding="utf-8"
    )
    result = runner.invoke(app, ["lint"])
    assert result.exit_code == 0
