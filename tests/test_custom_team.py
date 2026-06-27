"""Tests for CustomTeam (v0.8.3)."""
from __future__ import annotations

from pathlib import Path

import pytest

from antcrew.teams.custom_team import CustomTeam
from antcrew.models.simulated import SimulatedLLM
from antcrew.core.run_result import RunResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _llm():
    return SimulatedLLM()


def _steps(**overrides) -> list[dict]:
    base = [
        {"name": "step1", "system_prompt": "Do step 1.", "output_key": "out1"},
    ]
    if overrides:
        base[0].update(overrides)
    return base


def _two_steps() -> list[dict]:
    return [
        {"name": "planner",  "system_prompt": "Plan it.",    "output_key": "plan"},
        {"name": "executor", "system_prompt": "Execute: {plan}", "input_key": "plan",
         "output_key": "result"},
    ]


# ===========================================================================
# Construction
# ===========================================================================

def test_empty_steps_raises():
    with pytest.raises(ValueError, match="at least one step"):
        CustomTeam([], _llm())


def test_single_step_ok():
    team = CustomTeam(_steps(), _llm())
    assert len(team._agents) == 1
    assert team._agents[0].name == "step1"


def test_multiple_steps_ok():
    team = CustomTeam(_two_steps(), _llm())
    assert len(team._agents) == 2


def test_max_cost_propagated():
    llm = _llm()
    CustomTeam(_steps(), llm, max_cost_usd=0.5)
    assert llm.max_cost_usd == 0.5


def test_checkpointer_is_none():
    team = CustomTeam(_steps(), _llm())
    assert team._checkpointer is None


# ===========================================================================
# run() return value
# ===========================================================================

def test_run_returns_run_result():
    team = CustomTeam(_steps(), _llm())
    result = team.run("Build login")
    assert isinstance(result, RunResult)


def test_run_thread_id_preserved():
    team = CustomTeam(_steps(), _llm())
    result = team.run("task", thread_id="my-thread")
    assert result.thread_id == "my-thread"


def test_run_default_thread_id():
    team = CustomTeam(_steps(), _llm())
    result = team.run("task")
    assert result.thread_id == "default"


def test_run_state_has_request():
    team = CustomTeam(_steps(), _llm())
    result = team.run("Build JWT auth")
    assert result.state["request"] == "Build JWT auth"


def test_run_state_has_output_key():
    team = CustomTeam(_steps(), _llm())
    result = team.run("task")
    assert "out1" in result.state


def test_run_result_dict_access():
    team = CustomTeam(_steps(), _llm())
    result = team.run("task")
    assert "out1" in result          # __contains__
    assert result["out1"] is not None  # __getitem__


# ===========================================================================
# Multi-step state threading
# ===========================================================================

def test_two_steps_run():
    team = CustomTeam(_two_steps(), _llm())
    result = team.run("Build login")
    assert "plan" in result.state
    assert "result" in result.state


def test_later_step_receives_earlier_output():
    """The second step's input_key must be populated from step 1's output_key."""
    from antcrew.testing import SequencedLLM
    llm = SequencedLLM(["My plan output", "Execution done"])
    steps = [
        {"name": "planner",  "system_prompt": "Plan it.",        "output_key": "plan"},
        {"name": "executor", "system_prompt": "Execute: {plan}", "input_key": "plan",
         "output_key": "result"},
    ]
    team = CustomTeam(steps, llm)
    result = team.run("Build login")
    # The executor's input should contain the planner's output
    assert result["plan"] == "My plan output"
    assert result["result"] == "Execution done"


def test_three_step_chain():
    from antcrew.testing import SequencedLLM
    llm = SequencedLLM(["plan", "code", "review"])
    steps = [
        {"name": "planner",  "system_prompt": "Plan.",         "output_key": "plan"},
        {"name": "coder",    "system_prompt": "Code: {plan}",  "input_key": "plan",   "output_key": "code"},
        {"name": "reviewer", "system_prompt": "Review: {code}","input_key": "code",   "output_key": "review"},
    ]
    team = CustomTeam(steps, llm)
    result = team.run("task")
    assert result["plan"] == "plan"
    assert result["code"] == "code"
    assert result["review"] == "review"


# ===========================================================================
# _initial_state (Pipeline compatibility)
# ===========================================================================

def test_initial_state_has_request():
    team = CustomTeam(_steps(), _llm())
    state = team._initial_state("Do something")
    assert state["request"] == "Do something"


def test_initial_state_is_dict():
    team = CustomTeam(_steps(), _llm())
    assert isinstance(team._initial_state("x"), dict)


# ===========================================================================
# Pipeline compatibility
# ===========================================================================

def test_pipeline_with_custom_team():
    from antcrew.core.pipeline import Pipeline
    team = CustomTeam(_steps(), _llm())
    pipeline = Pipeline([team])
    result = pipeline.run("Build login")
    assert "out1" in result.state


# ===========================================================================
# TraceLog integration
# ===========================================================================

def test_trace_log_begin_run_called(tmp_path):
    from antcrew.trace import TraceLog
    db = tmp_path / "trace.db"
    tlog = TraceLog(db)

    team = CustomTeam(_steps(), _llm(), trace_log=tlog)
    team.run("Build login", thread_id="t1")
    tlog.close()

    tlog2 = TraceLog(db)
    runs = tlog2.list_runs()
    tlog2.close()

    assert len(runs) == 1
    assert runs[0]["thread_id"] == "t1"
    assert runs[0]["status"] == "done"


def test_trace_log_records_error_status(tmp_path):
    from antcrew.trace import TraceLog

    class _BoomLLM(SimulatedLLM):
        def complete(self, messages, **kw):
            raise RuntimeError("boom")

    db = tmp_path / "trace.db"
    tlog = TraceLog(db)
    team = CustomTeam(_steps(), _BoomLLM(), trace_log=tlog)

    with pytest.raises(RuntimeError):
        team.run("task", thread_id="err-thread")

    tlog.close()

    tlog2 = TraceLog(db)
    runs = tlog2.list_runs()
    tlog2.close()

    assert runs[0]["status"] == "error"


# ===========================================================================
# config.py integration
# ===========================================================================

def test_config_load_custom_team(tmp_path):
    import yaml
    from antcrew.config import load

    cfg = {
        "team": "custom",
        "model": "simulated",
        "steps": [
            {"name": "summarizer", "system_prompt": "Summarise.", "output_key": "summary"},
        ],
    }
    p = tmp_path / "team.yaml"
    p.write_text(yaml.dump(cfg), encoding="utf-8")

    from antcrew.teams.custom_team import CustomTeam as CT
    team = load(p)
    assert isinstance(team, CT)
    assert len(team._agents) == 1
    assert team._agents[0].name == "summarizer"


def test_config_custom_team_no_steps_raises(tmp_path):
    import yaml
    from antcrew.config import load

    cfg = {"team": "custom", "model": "simulated"}
    p = tmp_path / "team.yaml"
    p.write_text(yaml.dump(cfg), encoding="utf-8")

    with pytest.raises(ValueError, match="steps"):
        load(p)


def test_config_custom_team_empty_steps_raises(tmp_path):
    import yaml
    from antcrew.config import load

    cfg = {"team": "custom", "model": "simulated", "steps": []}
    p = tmp_path / "team.yaml"
    p.write_text(yaml.dump(cfg), encoding="utf-8")

    with pytest.raises((ValueError, Exception)):
        load(p)


# ===========================================================================
# antcrew init --template custom
# ===========================================================================

def test_init_custom_template(tmp_path):
    from typer.testing import CliRunner
    from antcrew.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["init", "--template", "custom", "--output", str(tmp_path)])
    assert result.exit_code == 0, result.output

    yaml_file = tmp_path / "agentteam.yaml"
    main_file = tmp_path / "main.py"
    assert yaml_file.exists()
    assert main_file.exists()
    content = yaml_file.read_text()
    assert "team: custom" in content
    assert "steps:" in content


# ===========================================================================
# antcrew namespace export
# ===========================================================================

def test_custom_team_importable_from_top_level():
    from antcrew import CustomTeam as CT
    assert CT is CustomTeam
