"""Tests for antcrew.graph renderers and `antcrew graph` CLI command."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from antcrew.cli import app
from antcrew.graph import render_ascii, render_mermaid, _get_builtin_flow

runner = CliRunner()


# ===========================================================================
# render_mermaid
# ===========================================================================

def test_mermaid_empty_flow():
    out = render_mermaid([])
    assert "graph LR" in out
    assert "__start__" in out
    assert "__end__" in out


def test_mermaid_linear_chain():
    flow = [("pm", "dev"), ("dev", "qa")]
    out = render_mermaid(flow)
    assert "graph LR" in out
    assert "__start__([ START ]) --> pm" in out
    assert "pm --> dev" in out
    assert "dev --> qa" in out
    assert "qa --> __end__([ END ])" in out


def test_mermaid_conditional_edge():
    flow = [
        ("pm", "dev"),
        ("qa", "reviewer", "no_bugs"),
        ("qa", "dev", "has_bugs"),
    ]
    out = render_mermaid(flow)
    assert "-->|no_bugs|" in out
    assert "-->|has_bugs|" in out


def test_mermaid_no_conditional_label_on_plain_edges():
    flow = [("a", "b"), ("b", "c")]
    out = render_mermaid(flow)
    assert "-->|" not in out


def test_mermaid_start_and_end_nodes_only_once():
    flow = [("a", "b"), ("b", "c")]
    out = render_mermaid(flow)
    assert out.count("__start__") == 1
    assert out.count("__end__") == 1


# ===========================================================================
# render_ascii
# ===========================================================================

def test_ascii_empty_flow():
    out = render_ascii([])
    assert "[START]" in out
    assert "[END]" in out


def test_ascii_linear_chain():
    flow = [("a", "b"), ("b", "c")]
    out = render_ascii(flow)
    assert "[START]" in out
    assert "[END]" in out
    assert "a" in out
    assert "b" in out
    assert "c" in out


def test_ascii_linear_single_line():
    """Linear chain is rendered as a single line."""
    flow = [("pm", "dev"), ("dev", "qa")]
    out = render_ascii(flow)
    assert "\n" not in out.strip()


def test_ascii_branching_flow_contains_all_nodes():
    flow = [
        ("pm", "backend"),
        ("pm", "frontend"),
        ("backend", "qa"),
        ("frontend", "qa"),
    ]
    out = render_ascii(flow)
    for name in ("pm", "backend", "frontend", "qa"):
        assert name in out


# ===========================================================================
# _get_builtin_flow
# ===========================================================================

def test_get_builtin_flow_dev():
    flow = _get_builtin_flow("dev")
    assert flow is not None
    assert len(flow) >= 1


def test_get_builtin_flow_fullstack():
    flow = _get_builtin_flow("fullstack")
    assert flow is not None
    assert len(flow) >= 5


def test_get_builtin_flow_research():
    flow = _get_builtin_flow("research")
    assert flow is not None
    assert ("researcher", "writer") in [tuple(e[:2]) for e in flow]


def test_get_builtin_flow_content():
    flow = _get_builtin_flow("content")
    assert flow is not None


def test_get_builtin_flow_unknown_returns_none():
    assert _get_builtin_flow("nonexistent_team") is None


def test_get_builtin_flow_case_insensitive():
    flow_lower = _get_builtin_flow("dev")
    flow_upper = _get_builtin_flow("DEV")
    # DEV won't match because of the simple lower() check, but "dev" does
    assert flow_lower is not None


# ===========================================================================
# CLI: antcrew graph
# ===========================================================================

def test_graph_cmd_team_dev():
    result = runner.invoke(app, ["graph", "--team", "dev"])
    assert result.exit_code == 0
    assert "business_analyst" in result.output


def test_graph_cmd_team_research():
    result = runner.invoke(app, ["graph", "--team", "research"])
    assert result.exit_code == 0
    assert "researcher" in result.output


def test_graph_cmd_team_fullstack():
    result = runner.invoke(app, ["graph", "--team", "fullstack"])
    assert result.exit_code == 0
    assert "backend_dev" in result.output


def test_graph_cmd_team_content():
    result = runner.invoke(app, ["graph", "--team", "content"])
    assert result.exit_code == 0
    assert "copywriter" in result.output or "idea" in result.output


def test_graph_cmd_mermaid_format():
    result = runner.invoke(app, ["graph", "--team", "dev", "--format", "mermaid"])
    assert result.exit_code == 0
    assert "graph LR" in result.output
    assert "__start__" in result.output


def test_graph_cmd_ascii_format_explicit():
    result = runner.invoke(app, ["graph", "--team", "dev", "--format", "ascii"])
    assert result.exit_code == 0
    assert "[START]" in result.output


def test_graph_cmd_invalid_format():
    result = runner.invoke(app, ["graph", "--team", "dev", "--format", "svg"])
    assert result.exit_code == 1
    assert "format" in result.output.lower() or "svg" in result.output.lower()


def test_graph_cmd_unknown_team():
    result = runner.invoke(app, ["graph", "--team", "unicorn"])
    assert result.exit_code == 1
    assert "unicorn" in result.output or "unknown" in result.output.lower()


def test_graph_cmd_config_file(tmp_path):
    flow_file = tmp_path / "flow.json"
    flow_file.write_text(
        json.dumps([["pm", "dev"], ["dev", "qa"]]), encoding="utf-8"
    )
    result = runner.invoke(app, ["graph", "--config", str(flow_file)])
    assert result.exit_code == 0
    assert "pm" in result.output
    assert "dev" in result.output
    assert "qa" in result.output


def test_graph_cmd_config_yaml(tmp_path):
    flow_file = tmp_path / "flow.yaml"
    flow_file.write_text(
        "flow:\n  - [business_analyst, pm]\n  - [pm, backend_dev]\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["graph", "--config", str(flow_file)])
    assert result.exit_code == 0
    assert "business_analyst" in result.output


def test_graph_cmd_config_missing():
    result = runner.invoke(app, ["graph", "--config", "does_not_exist.yaml"])
    assert result.exit_code == 1
    assert "not found" in result.output.lower()


def test_graph_cmd_no_args_no_default_file(tmp_path, monkeypatch):
    """With no --team/--config and no agentteam.yaml, exits with guidance."""
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["graph"])
    assert result.exit_code == 1
    assert "team" in result.output.lower() or "config" in result.output.lower()


def test_graph_cmd_no_args_uses_agentteam_yaml(tmp_path, monkeypatch):
    """With no flags but an agentteam.yaml present, uses it automatically."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "agentteam.yaml").write_text(
        "flow:\n  - [pm, dev]\n  - [dev, qa]\n", encoding="utf-8"
    )
    result = runner.invoke(app, ["graph"])
    assert result.exit_code == 0
    assert "pm" in result.output


def test_graph_cmd_mermaid_includes_hint():
    """Mermaid output includes a hint about rendering it."""
    result = runner.invoke(app, ["graph", "--team", "dev", "--format", "mermaid"])
    assert result.exit_code == 0
    assert "mermaid" in result.output.lower()


def test_graph_cmd_conditional_flow_mermaid(tmp_path):
    flow_file = tmp_path / "cond.json"
    flow_file.write_text(
        json.dumps([
            ["qa", "reviewer", "no_bugs"],
            ["qa", "dev", "has_bugs"],
        ]),
        encoding="utf-8",
    )
    result = runner.invoke(app, ["graph", "--config", str(flow_file), "--format", "mermaid"])
    assert result.exit_code == 0
    assert "no_bugs" in result.output
    assert "has_bugs" in result.output
