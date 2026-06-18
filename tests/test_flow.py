"""Tests for flow YAML/JSON loading, validation, and CLI commands."""
from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from antcrew.cli import app
from antcrew.flow import (
    _KNOWN_AGENTS,
    _parse_flow,
    format_flow,
    load_flow,
    validate_flow,
)

cli = CliRunner()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _yaml_file(tmp_path, content, name="flow.yaml"):
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p

def _json_file(tmp_path, data, name="flow.json"):
    p = tmp_path / name
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# _parse_flow — unit tests (no file I/O)
# ---------------------------------------------------------------------------

def test_parse_list():
    data = [["business_analyst", "pm"], ["pm", "backend_dev"]]
    flow = _parse_flow(data)
    assert flow == [("business_analyst", "pm"), ("pm", "backend_dev")]

def test_parse_dict_with_flow_key():
    data = {"team": "dev", "flow": [["pm", "backend_dev"]]}
    flow = _parse_flow(data)
    assert flow == [("pm", "backend_dev")]

def test_parse_dict_missing_flow_key_raises():
    with pytest.raises(ValueError, match="flow"):
        _parse_flow({"team": "dev", "model": "claude"})

def test_parse_conditional_edge():
    data = [["qa", "reviewer", "no_critical_bugs"]]
    flow = _parse_flow(data)
    assert flow[0] == ("qa", "reviewer", "no_critical_bugs")

def test_parse_invalid_edge_raises():
    with pytest.raises(ValueError, match="Edge 0"):
        _parse_flow([["only_one_element"]])

def test_parse_empty_list():
    assert _parse_flow([]) == []

def test_parse_wrong_type_raises():
    with pytest.raises(ValueError, match="dict"):
        _parse_flow("just a string")


# ---------------------------------------------------------------------------
# load_flow — YAML
# ---------------------------------------------------------------------------

def test_load_flow_from_yaml_list(tmp_path):
    p = _yaml_file(tmp_path, "- [business_analyst, pm]\n- [pm, backend_dev]\n")
    flow = load_flow(p)
    assert flow == [("business_analyst", "pm"), ("pm", "backend_dev")]

def test_load_flow_from_yaml_dict(tmp_path):
    p = _yaml_file(tmp_path, "team: dev\nflow:\n  - [pm, backend_dev]\n")
    flow = load_flow(p)
    assert flow == [("pm", "backend_dev")]

def test_load_flow_yaml_with_condition(tmp_path):
    content = (
        "- [qa, reviewer, no_critical_bugs]\n"
        "- [qa, backend_dev, has_critical_bugs]\n"
    )
    p = _yaml_file(tmp_path, content)
    flow = load_flow(p)
    assert flow[0] == ("qa", "reviewer", "no_critical_bugs")
    assert flow[1] == ("qa", "backend_dev", "has_critical_bugs")


# ---------------------------------------------------------------------------
# load_flow — JSON
# ---------------------------------------------------------------------------

def test_load_flow_from_json_list(tmp_path):
    p = _json_file(tmp_path, [["business_analyst", "pm"], ["pm", "backend_dev"]])
    flow = load_flow(p)
    assert flow == [("business_analyst", "pm"), ("pm", "backend_dev")]

def test_load_flow_from_json_dict(tmp_path):
    p = _json_file(tmp_path, {"team": "dev", "model": "claude", "flow": [["pm", "backend_dev"]]})
    flow = load_flow(p)
    assert flow == [("pm", "backend_dev")]

def test_load_flow_json_conditional(tmp_path):
    p = _json_file(tmp_path, [["qa", "reviewer", "no_critical_bugs"]])
    flow = load_flow(p)
    assert flow[0] == ("qa", "reviewer", "no_critical_bugs")

def test_load_flow_json_invalid_json_raises(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not valid}", encoding="utf-8")
    with pytest.raises(ValueError, match="[Ii]nvalid JSON"):
        load_flow(p)

def test_load_flow_file_not_found():
    with pytest.raises(FileNotFoundError):
        load_flow("/tmp/does_not_exist_xyz.json")


# ---------------------------------------------------------------------------
# validate_flow
# ---------------------------------------------------------------------------

def test_validate_valid_flow():
    flow = [("business_analyst", "pm"), ("pm", "backend_dev")]
    assert validate_flow(flow) == []

def test_validate_unknown_agent():
    flow = [("business_analyst", "nonexistent_agent")]
    errors = validate_flow(flow)
    assert len(errors) == 1
    assert "nonexistent_agent" in errors[0]

def test_validate_unknown_src():
    flow = [("mystery_agent", "pm")]
    errors = validate_flow(flow)
    assert any("mystery_agent" in e for e in errors)

def test_validate_extra_agents():
    flow = [("business_analyst", "my_custom_agent")]
    errors = validate_flow(flow, extra_agents={"my_custom_agent"})
    assert errors == []

def test_validate_each_unknown_reported_once():
    flow = [("bad", "pm"), ("bad", "backend_dev")]
    errors = validate_flow(flow)
    bad_errors = [e for e in errors if "bad" in e]
    assert len(bad_errors) == 1  # reported once even though 'bad' appears twice

def test_validate_non_string_agent_name():
    flow = [(123, "pm")]
    errors = validate_flow(flow)
    assert any("string" in e.lower() for e in errors)

def test_validate_conditional_edge_valid():
    flow = [("qa", "reviewer", "no_critical_bugs")]
    assert validate_flow(flow) == []

def test_validate_empty_flow():
    assert validate_flow([]) == []

def test_validate_all_known_agents_valid():
    flow = [(a, b) for a, b in zip(sorted(_KNOWN_AGENTS), sorted(_KNOWN_AGENTS)[1:])]
    assert validate_flow(flow) == []


# ---------------------------------------------------------------------------
# format_flow
# ---------------------------------------------------------------------------

def test_format_flow_contains_arrows():
    flow = [("business_analyst", "pm")]
    text = format_flow(flow)
    assert "→" in text

def test_format_flow_contains_agent_names():
    flow = [("business_analyst", "pm"), ("pm", "backend_dev")]
    text = format_flow(flow)
    assert "business_analyst" in text
    assert "backend_dev" in text

def test_format_flow_shows_condition():
    flow = [("qa", "reviewer", "no_critical_bugs")]
    text = format_flow(flow)
    assert "[no_critical_bugs]" in text

def test_format_flow_with_title():
    flow = [("pm", "backend_dev")]
    text = format_flow(flow, title="My Pipeline")
    assert "My Pipeline" in text

def test_format_flow_shows_summary_counts():
    flow = [("business_analyst", "pm"), ("pm", "backend_dev")]
    text = format_flow(flow)
    assert "3 agent" in text  # ba, pm, backend_dev
    assert "2 edge" in text

def test_format_flow_empty():
    text = format_flow([])
    assert "empty" in text.lower()


# ---------------------------------------------------------------------------
# config.load() — JSON support
# ---------------------------------------------------------------------------

def test_config_load_json(tmp_path):
    p = _json_file(tmp_path, {"team": "dev", "model": "simulated"}, "team.json")
    from antcrew.config import load
    from antcrew.teams.dev_team import DevTeam
    team = load(p)
    assert isinstance(team, DevTeam)

def test_config_load_json_with_flow(tmp_path):
    p = _json_file(tmp_path, {
        "team": "dev",
        "model": "simulated",
        "flow": [["business_analyst", "pm"], ["pm", "backend_dev"]],
    }, "team.json")
    from antcrew.config import load
    team = load(p)
    state = team.run("Build login module")
    assert state.get("prd") is not None

def test_config_load_json_fullstack(tmp_path):
    p = _json_file(tmp_path, {"team": "fullstack", "model": "simulated"}, "team.json")
    from antcrew.config import load
    from antcrew.teams.fullstack_team import FullStackTeam
    team = load(p)
    assert isinstance(team, FullStackTeam)

def test_config_load_json_research(tmp_path):
    p = _json_file(tmp_path, {"team": "research", "model": "simulated"}, "team.json")
    from antcrew.config import load
    from antcrew.teams.research_team import ResearchTeam
    team = load(p)
    assert isinstance(team, ResearchTeam)

def test_config_load_json_invalid_json_raises(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("not json", encoding="utf-8")
    from antcrew.config import load
    with pytest.raises(Exception):
        load(p)

def test_config_load_json_agents_override(tmp_path):
    p = _json_file(tmp_path, {
        "team": "dev",
        "model": "simulated",
        "agents": {
            "backend_dev": {"model": "simulated", "approval_required": False}
        },
    }, "team.json")
    from antcrew.config import load
    team = load(p)
    state = team.run("Build x")
    assert state.get("prd") is not None


# ---------------------------------------------------------------------------
# antcrew flow show (CLI)
# ---------------------------------------------------------------------------

def test_cli_flow_show_yaml(tmp_path):
    p = _yaml_file(tmp_path, "- [business_analyst, pm]\n- [pm, backend_dev]\n")
    result = cli.invoke(app, ["flow", "show", str(p)])
    assert result.exit_code == 0, result.output
    assert "business_analyst" in result.output
    assert "→" in result.output

def test_cli_flow_show_json(tmp_path):
    p = _json_file(tmp_path, [["business_analyst", "pm"]])
    result = cli.invoke(app, ["flow", "show", str(p)])
    assert result.exit_code == 0, result.output
    assert "business_analyst" in result.output

def test_cli_flow_show_full_config(tmp_path):
    p = _json_file(tmp_path, {
        "team": "dev",
        "model": "simulated",
        "flow": [["business_analyst", "pm"], ["pm", "backend_dev"]],
    })
    result = cli.invoke(app, ["flow", "show", str(p)])
    assert result.exit_code == 0, result.output
    assert "pm" in result.output

def test_cli_flow_show_missing_file(tmp_path):
    result = cli.invoke(app, ["flow", "show", str(tmp_path / "nope.yaml")])
    assert result.exit_code == 1
    assert "not found" in result.output.lower()

def test_cli_flow_show_shows_condition(tmp_path):
    p = _json_file(tmp_path, [["qa", "reviewer", "no_critical_bugs"]])
    result = cli.invoke(app, ["flow", "show", str(p)])
    assert result.exit_code == 0
    assert "no_critical_bugs" in result.output

def test_cli_flow_show_warns_on_unknown_agent(tmp_path):
    p = _json_file(tmp_path, [["business_analyst", "unknown_xyz"]])
    result = cli.invoke(app, ["flow", "show", str(p)])
    assert result.exit_code == 0  # show still succeeds
    assert "unknown_xyz" in result.output  # but warns


# ---------------------------------------------------------------------------
# antcrew flow validate (CLI)
# ---------------------------------------------------------------------------

def test_cli_flow_validate_valid(tmp_path):
    p = _json_file(tmp_path, [["business_analyst", "pm"], ["pm", "backend_dev"]])
    result = cli.invoke(app, ["flow", "validate", str(p)])
    assert result.exit_code == 0, result.output
    assert "valid" in result.output.lower()

def test_cli_flow_validate_invalid_agent(tmp_path):
    p = _json_file(tmp_path, [["business_analyst", "bad_agent_name"]])
    result = cli.invoke(app, ["flow", "validate", str(p)])
    assert result.exit_code == 1
    assert "bad_agent_name" in result.output

def test_cli_flow_validate_missing_file(tmp_path):
    result = cli.invoke(app, ["flow", "validate", str(tmp_path / "nope.json")])
    assert result.exit_code == 1

def test_cli_flow_validate_yaml(tmp_path):
    p = _yaml_file(tmp_path, "- [business_analyst, pm]\n")
    result = cli.invoke(app, ["flow", "validate", str(p)])
    assert result.exit_code == 0
    assert "valid" in result.output.lower()

def test_cli_flow_validate_reports_all_errors(tmp_path):
    p = _json_file(tmp_path, [["bad1", "bad2"]])
    result = cli.invoke(app, ["flow", "validate", str(p)])
    assert result.exit_code == 1
    assert "bad1" in result.output
    assert "bad2" in result.output


# ---------------------------------------------------------------------------
# Top-level imports
# ---------------------------------------------------------------------------

def test_importable_from_antcrew():
    from antcrew import load_flow as lf, validate_flow as vf, format_flow as ff
    assert lf is load_flow
    assert vf is validate_flow
    assert ff is format_flow
