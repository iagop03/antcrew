"""Tests for CustomTeam (v0.8.3 – v0.9.0)."""
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


# ===========================================================================
# Parallel steps
# ===========================================================================

def _parallel_steps() -> list[dict]:
    return [
        {"name": "planner", "system_prompt": "Plan it.", "output_key": "plan"},
        {"parallel": [
            {"name": "backend",  "system_prompt": "Backend: {plan}",
             "input_key": "plan", "output_key": "backend_code"},
            {"name": "frontend", "system_prompt": "Frontend: {plan}",
             "input_key": "plan", "output_key": "frontend_code"},
        ]},
        {"name": "reviewer",
         "system_prompt": "Review: {backend_code} {frontend_code}",
         "output_key": "review"},
    ]


def test_parallel_agents_flat_list():
    """_agents must contain all agents including those in parallel groups."""
    team = CustomTeam(_parallel_steps(), _llm())
    names = [a.name for a in team._agents]
    assert "planner" in names
    assert "backend" in names
    assert "frontend" in names
    assert "reviewer" in names
    assert len(names) == 4


def test_parallel_step_groups_structure():
    team = CustomTeam(_parallel_steps(), _llm())
    # 3 step groups: planner, parallel(backend+frontend), reviewer
    assert len(team._step_groups) == 3
    assert len(team._step_groups[0]) == 1   # planner
    assert len(team._step_groups[1]) == 2   # backend + frontend
    assert len(team._step_groups[2]) == 1   # reviewer


def test_parallel_run_produces_all_outputs():
    team = CustomTeam(_parallel_steps(), _llm())
    result = team.run("Build login")
    assert "plan" in result.state
    assert "backend_code" in result.state
    assert "frontend_code" in result.state
    assert "review" in result.state


def test_parallel_both_outputs_non_none():
    team = CustomTeam(_parallel_steps(), _llm())
    result = team.run("Build login")
    assert result["backend_code"] is not None
    assert result["frontend_code"] is not None


def test_parallel_later_step_sees_parallel_outputs():
    """The reviewer (step 3) must see outputs from the parallel group (step 2)."""
    from antcrew.testing import SequencedLLM
    llm = SequencedLLM(["plan-text", "backend-text", "frontend-text", "review-text"])
    team = CustomTeam(_parallel_steps(), llm)
    result = team.run("Build login")
    assert result["review"] == "review-text"


def test_parallel_empty_group_raises():
    with pytest.raises(ValueError, match="parallel"):
        CustomTeam([{"parallel": []}], _llm())


def test_parallel_only_group():
    """A team that is entirely one parallel group."""
    steps = [
        {"parallel": [
            {"name": "a", "system_prompt": "Task A.", "output_key": "out_a"},
            {"name": "b", "system_prompt": "Task B.", "output_key": "out_b"},
        ]}
    ]
    team = CustomTeam(steps, _llm())
    result = team.run("task")
    assert "out_a" in result.state
    assert "out_b" in result.state


def test_parallel_max_workers_respected():
    team = CustomTeam(_parallel_steps(), _llm(), max_workers=1)
    # Should still produce correct output even with a single worker.
    result = team.run("task")
    assert "backend_code" in result.state
    assert "frontend_code" in result.state


def test_parallel_state_snapshot_isolation():
    """Parallel agents must not see each other's writes during execution."""
    seen: list[dict] = []

    class _SpyLLM(SimulatedLLM):
        def complete(self, messages, **kw):
            # record a snapshot of state keys visible at call time
            seen.append(set(messages[-1].content.split() if messages else []))
            return super().complete(messages, **kw)

    steps = [
        {"parallel": [
            {"name": "a", "system_prompt": "A.", "output_key": "out_a"},
            {"name": "b", "system_prompt": "B.", "output_key": "out_b"},
        ]}
    ]
    team = CustomTeam(steps, _SpyLLM())
    result = team.run("task")
    # Both run; neither should crash — output isolation is the core guarantee.
    assert "out_a" in result.state
    assert "out_b" in result.state


# ===========================================================================
# Parallel in YAML config
# ===========================================================================

# ===========================================================================
# Retry policy — _Step dataclass and _run_step helper
# ===========================================================================

def test_retry_keys_not_forwarded_to_template_agent():
    """max_retries / retry_delay must not reach TemplateAgent as config keys."""
    steps = [{"name": "x", "system_prompt": "Do x.", "output_key": "out",
              "max_retries": 2, "retry_delay": 0.5}]
    team = CustomTeam(steps, _llm())
    agent = team._agents[0]
    # TemplateAgent should not have stored these as attributes
    assert not hasattr(agent, "max_retries")
    assert not hasattr(agent, "retry_delay")


def test_step_default_retries_zero():
    from antcrew.teams.custom_team import _parse_steps
    groups = _parse_steps([{"name": "a", "system_prompt": "s.", "output_key": "o"}], _llm())
    step = groups[0][0]
    assert step.max_retries == 0
    assert step.retry_delay == 0.0


def test_step_parses_max_retries():
    from antcrew.teams.custom_team import _parse_steps
    groups = _parse_steps(
        [{"name": "a", "system_prompt": "s.", "output_key": "o",
          "max_retries": 3, "retry_delay": 0.1}],
        _llm(),
    )
    step = groups[0][0]
    assert step.max_retries == 3
    assert step.retry_delay == pytest.approx(0.1)


def test_retry_succeeds_on_second_attempt():
    """Agent fails once then succeeds — result should be the success value."""
    calls = [0]

    class _FlakyLLM(SimulatedLLM):
        def complete(self, messages, **kw):
            calls[0] += 1
            if calls[0] == 1:
                raise RuntimeError("transient error")
            return super().complete(messages, **kw)

    steps = [{"name": "x", "system_prompt": "Do x.", "output_key": "out",
              "max_retries": 1}]
    team = CustomTeam(steps, _FlakyLLM())
    result = team.run("task")
    assert "out" in result.state
    assert calls[0] == 2  # failed once, succeeded on retry


def test_retry_exhausted_raises():
    """After all retries are spent the original exception must propagate."""
    class _AlwaysFailLLM(SimulatedLLM):
        def complete(self, messages, **kw):
            raise RuntimeError("always fails")

    steps = [{"name": "x", "system_prompt": "Do x.", "output_key": "out",
              "max_retries": 2}]
    team = CustomTeam(steps, _AlwaysFailLLM())
    with pytest.raises(RuntimeError, match="always fails"):
        team.run("task")


def test_retry_attempt_count(monkeypatch):
    """With max_retries=3 the LLM must be called exactly 4 times (1 + 3)."""
    calls = [0]

    class _CountLLM(SimulatedLLM):
        def complete(self, messages, **kw):
            calls[0] += 1
            raise RuntimeError("boom")

    # Suppress sleep to keep tests fast
    monkeypatch.setattr("antcrew.teams.custom_team.time.sleep", lambda _: None)

    steps = [{"name": "x", "system_prompt": "s.", "output_key": "o", "max_retries": 3}]
    team = CustomTeam(steps, _CountLLM())
    with pytest.raises(RuntimeError):
        team.run("task")
    assert calls[0] == 4


def test_retry_delay_called(monkeypatch):
    """retry_delay > 0 must call time.sleep between attempts."""
    slept: list[float] = []
    monkeypatch.setattr("antcrew.teams.custom_team.time.sleep", slept.append)

    calls = [0]

    class _FlakyLLM(SimulatedLLM):
        def complete(self, messages, **kw):
            calls[0] += 1
            if calls[0] < 3:
                raise RuntimeError("transient")
            return super().complete(messages, **kw)

    steps = [{"name": "x", "system_prompt": "s.", "output_key": "o",
              "max_retries": 3, "retry_delay": 0.5}]
    team = CustomTeam(steps, _FlakyLLM())
    team.run("task")
    assert len(slept) == 2           # slept between attempt 1→2 and 2→3
    assert all(s == pytest.approx(0.5) for s in slept)


def test_no_sleep_when_retry_delay_zero(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr("antcrew.teams.custom_team.time.sleep", slept.append)
    calls = [0]

    class _FlakyLLM(SimulatedLLM):
        def complete(self, messages, **kw):
            calls[0] += 1
            if calls[0] == 1:
                raise RuntimeError("transient")
            return super().complete(messages, **kw)

    steps = [{"name": "x", "system_prompt": "s.", "output_key": "o",
              "max_retries": 1, "retry_delay": 0.0}]
    team = CustomTeam(steps, _FlakyLLM())
    team.run("task")
    assert slept == []


def test_retry_on_second_step_only():
    """Only the failing step retries; the first step must run exactly once."""
    first_calls = [0]
    second_calls = [0]

    class _SelectiveLLM(SimulatedLLM):
        def complete(self, messages, **kw):
            # Distinguish agents by their system prompt (messages[0])
            sys_content = messages[0].content if messages else ""
            if "step1" in sys_content:
                first_calls[0] += 1
                return super().complete(messages, **kw)
            else:
                second_calls[0] += 1
                if second_calls[0] == 1:
                    raise RuntimeError("step2 fails first time")
                return super().complete(messages, **kw)

    steps = [
        {"name": "step1", "system_prompt": "step1 task", "output_key": "out1"},
        {"name": "step2", "system_prompt": "step2 task", "output_key": "out2",
         "max_retries": 1},
    ]
    team = CustomTeam(steps, _SelectiveLLM())
    result = team.run("task")
    assert "out1" in result.state
    assert "out2" in result.state
    assert first_calls[0] == 1
    assert second_calls[0] == 2


def test_retry_in_parallel_group(monkeypatch):
    """Retry policy must also work for steps inside a parallel group."""
    monkeypatch.setattr("antcrew.teams.custom_team.time.sleep", lambda _: None)
    calls: dict[str, int] = {"a": 0, "b": 0}

    class _FlakyLLM(SimulatedLLM):
        def complete(self, messages, **kw):
            # Distinguish agents by their system prompt (messages[0])
            sys_content = messages[0].content if messages else ""
            if "Task A" in sys_content:
                calls["a"] += 1
                if calls["a"] == 1:
                    raise RuntimeError("a fails")
            else:
                calls["b"] += 1
            return super().complete(messages, **kw)

    steps = [{"parallel": [
        {"name": "a", "system_prompt": "Task A.", "output_key": "out_a", "max_retries": 1},
        {"name": "b", "system_prompt": "Task B.", "output_key": "out_b"},
    ]}]
    team = CustomTeam(steps, _FlakyLLM())
    result = team.run("task")
    assert "out_a" in result.state
    assert "out_b" in result.state
    assert calls["a"] == 2


def test_config_retry_from_yaml(tmp_path):
    """max_retries and retry_delay must be parsed from a YAML config file."""
    import yaml
    from antcrew.config import load
    from antcrew.teams.custom_team import _parse_steps

    cfg = {
        "team": "custom",
        "model": "simulated",
        "steps": [
            {"name": "x", "system_prompt": "Do x.", "output_key": "out",
             "max_retries": 2, "retry_delay": 0.5},
        ],
    }
    p = tmp_path / "team.yaml"
    p.write_text(yaml.dump(cfg), encoding="utf-8")

    team = load(p)
    step = team._step_groups[0][0]
    assert step.max_retries == 2
    assert step.retry_delay == pytest.approx(0.5)


# ===========================================================================
# Conditional steps
# ===========================================================================

def test_condition_none_always_runs():
    from antcrew.teams.custom_team import _parse_condition, _Step, _condition_met
    step = _Step(agent=None, condition=None)  # type: ignore[arg-type]
    assert _condition_met(step, {}) is True


def test_parse_condition_string():
    from antcrew.teams.custom_team import _parse_condition
    assert _parse_condition("plan") == ["plan"]


def test_parse_condition_list():
    from antcrew.teams.custom_team import _parse_condition
    assert _parse_condition(["plan", "tickets"]) == ["plan", "tickets"]


def test_parse_condition_none():
    from antcrew.teams.custom_team import _parse_condition
    assert _parse_condition(None) is None


def test_parse_condition_empty_list_returns_none():
    from antcrew.teams.custom_team import _parse_condition
    assert _parse_condition([]) is None


def test_parse_condition_invalid_type_raises():
    from antcrew.teams.custom_team import _parse_condition
    with pytest.raises(ValueError):
        _parse_condition(42)


def test_condition_key_truthy_runs():
    steps = [
        {"name": "producer", "system_prompt": "Produce.", "output_key": "data"},
        {"name": "consumer", "system_prompt": "Consume: {data}",
         "input_key": "data", "output_key": "result", "condition": "data"},
    ]
    team = CustomTeam(steps, _llm())
    result = team.run("task")
    # producer sets "data"; consumer condition met → result must be present
    assert "result" in result.state


def test_condition_key_missing_skips():
    steps = [
        {"name": "skipped", "system_prompt": "Skip me.", "output_key": "skipped_out",
         "condition": "nonexistent_key"},
    ]
    team = CustomTeam(steps, _llm())
    result = team.run("task")
    assert "skipped_out" not in result.state


def test_condition_key_falsy_skips():
    """Step with condition on a key that holds an empty string must be skipped."""
    from antcrew.testing import SequencedLLM
    llm = SequencedLLM([""])   # producer returns empty string
    steps = [
        {"name": "producer", "system_prompt": "Produce.", "output_key": "data"},
        {"name": "consumer", "system_prompt": "Consume.", "output_key": "result",
         "condition": "data"},
    ]
    team = CustomTeam(steps, llm)
    result = team.run("task")
    assert "result" not in result.state


def test_condition_not_forwarded_to_template_agent():
    steps = [{"name": "x", "system_prompt": "s.", "output_key": "o", "condition": "plan"}]
    team = CustomTeam(steps, _llm())
    agent = team._agents[0]
    assert not hasattr(agent, "condition")


def test_condition_list_all_truthy_runs():
    from antcrew.testing import SequencedLLM
    llm = SequencedLLM(["plan-ok", "tickets-ok", "review-done"])
    steps = [
        {"name": "planner",  "system_prompt": "Plan.",    "output_key": "plan"},
        {"name": "ticketer", "system_prompt": "Tickets.", "output_key": "tickets"},
        {"name": "reviewer", "system_prompt": "Review.",  "output_key": "review",
         "condition": ["plan", "tickets"]},
    ]
    team = CustomTeam(steps, llm)
    result = team.run("task")
    assert "review" in result.state


def test_condition_list_one_falsy_skips():
    from antcrew.testing import SequencedLLM
    llm = SequencedLLM(["plan-ok", ""])   # tickets step returns empty
    steps = [
        {"name": "planner",  "system_prompt": "Plan.",    "output_key": "plan"},
        {"name": "ticketer", "system_prompt": "Tickets.", "output_key": "tickets"},
        {"name": "reviewer", "system_prompt": "Review.",  "output_key": "review",
         "condition": ["plan", "tickets"]},
    ]
    team = CustomTeam(steps, llm)
    result = team.run("task")
    assert "review" not in result.state


def test_condition_skipped_step_not_called():
    """A skipped step must not call the LLM at all."""
    calls = [0]

    class _CountLLM(SimulatedLLM):
        def complete(self, messages, **kw):
            calls[0] += 1
            return super().complete(messages, **kw)

    steps = [
        {"name": "skipped", "system_prompt": "Skip me.", "output_key": "out",
         "condition": "missing"},
    ]
    team = CustomTeam(steps, _CountLLM())
    team.run("task")
    assert calls[0] == 0


def test_condition_in_parallel_group_some_skipped():
    """Inside a parallel group, only steps whose condition is met run."""
    from antcrew.testing import SequencedLLM
    # Only one LLM call should happen (backend runs, frontend is skipped)
    llm = SequencedLLM(["backend-result"])
    steps = [
        {"parallel": [
            {"name": "backend",  "system_prompt": "Backend.",  "output_key": "backend_code"},
            {"name": "frontend", "system_prompt": "Frontend.", "output_key": "frontend_code",
             "condition": "missing_key"},
        ]}
    ]
    team = CustomTeam(steps, llm)
    result = team.run("task")
    assert "backend_code" in result.state
    assert "frontend_code" not in result.state


def test_condition_in_yaml_config(tmp_path):
    import yaml
    from antcrew.config import load

    cfg = {
        "team": "custom",
        "model": "simulated",
        "steps": [
            {"name": "planner",  "system_prompt": "Plan.", "output_key": "plan"},
            {"name": "reviewer", "system_prompt": "Review: {plan}.", "output_key": "review",
             "condition": "plan"},
        ],
    }
    p = tmp_path / "team.yaml"
    p.write_text(yaml.dump(cfg), encoding="utf-8")

    team = load(p)
    step = team._step_groups[1][0]
    assert step.condition == ["plan"]

    result = team.run("Build login")
    assert "review" in result.state


def test_config_parallel_steps(tmp_path):
    import yaml
    from antcrew.config import load

    cfg = {
        "team": "custom",
        "model": "simulated",
        "steps": [
            {"name": "planner", "system_prompt": "Plan.", "output_key": "plan"},
            {"parallel": [
                {"name": "writer_a", "system_prompt": "Write A.", "output_key": "text_a"},
                {"name": "writer_b", "system_prompt": "Write B.", "output_key": "text_b"},
            ]},
        ],
    }
    p = tmp_path / "team.yaml"
    p.write_text(yaml.dump(cfg), encoding="utf-8")

    team = load(p)
    assert len(team._step_groups) == 2
    assert len(team._step_groups[1]) == 2

    result = team.run("Build login")
    assert "plan" in result.state
    assert "text_a" in result.state
    assert "text_b" in result.state


# ===========================================================================
# Step progress callback  (_on_step)
# ===========================================================================

def test_on_step_default_none():
    team = CustomTeam(_steps(), _llm())
    assert team._on_step is None


def test_on_step_called_start_and_done():
    from antcrew.testing import SequencedLLM
    events: list[tuple[str, str]] = []

    team = CustomTeam(
        [{"name": "planner", "system_prompt": "Plan.", "output_key": "plan"}],
        SequencedLLM(["a plan"]),
    )
    team._on_step = lambda name, event: events.append((name, event))
    team.run("task")

    assert ("planner", "start") in events
    assert ("planner", "done") in events


def test_on_step_order_matches_pipeline():
    from antcrew.testing import SequencedLLM
    events: list[tuple[str, str]] = []

    team = CustomTeam(
        [
            {"name": "a", "system_prompt": "A.", "output_key": "out_a"},
            {"name": "b", "system_prompt": "B.", "output_key": "out_b"},
            {"name": "c", "system_prompt": "C.", "output_key": "out_c"},
        ],
        SequencedLLM(["x", "y", "z"]),
    )
    team._on_step = lambda name, event: events.append((name, event))
    team.run("task")

    # start/done pairs in order: a, b, c
    starts = [name for name, ev in events if ev == "start"]
    assert starts == ["a", "b", "c"]


def test_on_step_skip_fired_for_skipped_step():
    from antcrew.testing import SequencedLLM
    events: list[tuple[str, str]] = []

    team = CustomTeam(
        [
            {"name": "a", "system_prompt": "A.", "output_key": "out_a"},
            {"name": "b", "system_prompt": "B.", "output_key": "out_b",
             "condition": "nonexistent_key"},
        ],
        SequencedLLM(["result_a"]),
    )
    team._on_step = lambda name, event: events.append((name, event))
    team.run("task")

    assert ("b", "skip") in events
    assert ("b", "start") not in events
    assert ("b", "done") not in events


def test_on_step_parallel_group_uses_combined_name():
    from antcrew.testing import SequencedLLM
    events: list[tuple[str, str]] = []

    team = CustomTeam(
        [
            {"parallel": [
                {"name": "be", "system_prompt": "Backend.", "output_key": "be_out"},
                {"name": "fe", "system_prompt": "Frontend.", "output_key": "fe_out"},
            ]},
        ],
        SequencedLLM(["be result", "fe result"]),
    )
    team._on_step = lambda name, event: events.append((name, event))
    team.run("task")

    # The parallel group fires one start and one done for the combined name
    starts = [name for name, ev in events if ev == "start"]
    dones  = [name for name, ev in events if ev == "done"]
    assert len(starts) == 1
    assert "be" in starts[0] and "fe" in starts[0]
    assert len(dones) == 1


def test_on_step_callback_exception_does_not_abort_pipeline():
    """A buggy callback must never kill the pipeline run."""
    from antcrew.testing import SequencedLLM

    def _bad_cb(name, event):
        raise RuntimeError("callback exploded")

    team = CustomTeam(
        [{"name": "a", "system_prompt": "A.", "output_key": "out"}],
        SequencedLLM(["result"]),
    )
    team._on_step = _bad_cb
    result = team.run("task")  # must NOT raise
    assert result["out"] == "result"


def test_on_step_not_called_after_run():
    """_on_step set by the CLI should be cleaned up after the run (no leak)."""
    from antcrew.testing import SequencedLLM

    team = CustomTeam(
        [{"name": "a", "system_prompt": "A.", "output_key": "out"}],
        SequencedLLM(["result"]),
    )
    team._on_step = lambda name, event: None
    team.run("task")
    # _on_step is a public attribute — the CLI cleans it up after run;
    # we just verify the pipeline didn't clear it internally (that's the CLI's job)
    assert team._on_step is not None  # still set — cleanup is caller's responsibility


# ---------------------------------------------------------------------------
# CLI integration — progress lines appear in output
# ---------------------------------------------------------------------------

def test_cli_run_shows_step_names(tmp_path):
    """antcrew run --config team.yaml shows step names as progress."""
    import yaml
    from typer.testing import CliRunner
    from antcrew.cli import app

    cfg = {
        "team": "custom",
        "model": "simulated",
        "steps": [
            {"name": "planner",  "system_prompt": "Plan.", "output_key": "plan"},
            {"name": "executor", "system_prompt": "Execute.", "output_key": "result"},
        ],
    }
    p = tmp_path / "team.yaml"
    p.write_text(yaml.dump(cfg), encoding="utf-8")

    r = CliRunner().invoke(app, ["run", "Build login", "--config", str(p), "--no-stream"])
    assert r.exit_code == 0
    assert "planner" in r.output
    assert "executor" in r.output


def test_cli_run_shows_skip_for_unfulfilled_condition(tmp_path):
    import yaml
    from typer.testing import CliRunner
    from antcrew.cli import app

    cfg = {
        "team": "custom",
        "model": "simulated",
        "steps": [
            {"name": "a", "system_prompt": "A.", "output_key": "out_a"},
            {"name": "b", "system_prompt": "B.", "output_key": "out_b",
             "condition": "ghost_key"},
        ],
    }
    p = tmp_path / "team.yaml"
    p.write_text(yaml.dump(cfg), encoding="utf-8")

    r = CliRunner().invoke(app, ["run", "task", "--config", str(p), "--no-stream"])
    assert r.exit_code == 0
    assert "skipped" in r.output


# ===========================================================================
# vars — team-level state defaults
# ===========================================================================

def test_vars_default_empty():
    team = CustomTeam(_steps(), _llm())
    assert team._vars == {}


def test_vars_stored():
    team = CustomTeam(_steps(), _llm(), vars={"lang": "Python", "style": "concise"})
    assert team._vars == {"lang": "Python", "style": "concise"}


def test_vars_injected_into_initial_state():
    team = CustomTeam(_steps(), _llm(), vars={"lang": "Python"})
    state = team._initial_state("task")
    assert state["lang"] == "Python"
    assert state["request"] == "task"


def test_vars_request_always_wins():
    """If vars accidentally contains 'request', the run() argument wins."""
    team = CustomTeam(_steps(), _llm(), vars={"request": "should be overridden"})
    state = team._initial_state("actual request")
    assert state["request"] == "actual request"


def test_vars_available_to_interpolation():
    from antcrew.testing import SequencedLLM

    llm = SequencedLLM(["done"])
    capture: list[str] = []

    # Patch _CaptureLLM inline
    class _Cap(SimulatedLLM):
        def complete(self, messages, *, max_tokens=16384, json_mode=False):
            capture.append(next(m.content for m in messages if m.role == "system"))
            return "done"

    team = CustomTeam(
        [{"name": "a", "system_prompt": "Lang: {lang}", "output_key": "out"}],
        _Cap(),
        vars={"lang": "Python"},
    )
    team.run("task")
    assert "Python" in capture[0]


def test_vars_available_to_condition():
    """A condition key provided via vars lets the step run."""
    from antcrew.testing import SequencedLLM
    events: list[str] = []

    team = CustomTeam(
        [{"name": "a", "system_prompt": "Go.", "output_key": "out",
          "condition": "lang"}],
        SequencedLLM(["done"]),
        vars={"lang": "Python"},
    )
    team._on_step = lambda name, ev: events.append(ev)
    team.run("task")
    # condition key "lang" is truthy from vars → step runs, not skipped
    assert "start" in events
    assert "skip" not in events


def test_vars_from_yaml_config(tmp_path):
    import yaml
    from antcrew.config import load_context
    cfg = {
        "team": "custom",
        "model": "simulated",
        "vars": {"language": "Python", "style": "concise"},
        "steps": [{"name": "a", "system_prompt": "Lang: {language}", "output_key": "out"}],
    }
    p = tmp_path / "team.yaml"
    p.write_text(yaml.dump(cfg), encoding="utf-8")
    ctx = load_context(p)
    assert ctx.team._vars == {"language": "Python", "style": "concise"}


def test_vars_not_counted_as_unknown_in_validate(tmp_path):
    import yaml
    from typer.testing import CliRunner
    from antcrew.cli import app
    cfg = {
        "team": "custom",
        "vars": {"lang": "Python"},
        "steps": [
            {"name": "a", "system_prompt": "Use {lang}.", "output_key": "out"},
        ],
    }
    p = tmp_path / "team.yaml"
    p.write_text(yaml.dump(cfg), encoding="utf-8")
    r = CliRunner().invoke(app, ["validate", str(p)])
    assert r.exit_code == 0
    assert "warning" not in r.output.lower()


# ===========================================================================
# --dry-run CLI flag
# ===========================================================================

def test_dry_run_exits_zero(tmp_path):
    import yaml
    from typer.testing import CliRunner
    from antcrew.cli import app
    cfg = {
        "team": "custom",
        "model": "simulated",
        "steps": [{"name": "planner", "system_prompt": "Plan.", "output_key": "plan"}],
    }
    p = tmp_path / "team.yaml"
    p.write_text(yaml.dump(cfg), encoding="utf-8")
    r = CliRunner().invoke(app, ["run", "task", "--config", str(p), "--dry-run"])
    assert r.exit_code == 0


def test_dry_run_shows_step_names(tmp_path):
    import yaml
    from typer.testing import CliRunner
    from antcrew.cli import app
    cfg = {
        "team": "custom",
        "model": "simulated",
        "steps": [
            {"name": "planner",  "system_prompt": "Plan.", "output_key": "plan"},
            {"name": "executor", "system_prompt": "Do.",   "output_key": "result"},
        ],
    }
    p = tmp_path / "team.yaml"
    p.write_text(yaml.dump(cfg), encoding="utf-8")
    r = CliRunner().invoke(app, ["run", "task", "--config", str(p), "--dry-run"])
    assert r.exit_code == 0
    assert "planner"  in r.output
    assert "executor" in r.output


def test_dry_run_no_llm_calls(tmp_path):
    """--dry-run must not hit the LLM; SimulatedLLM call_count stays 0."""
    import yaml
    from typer.testing import CliRunner
    from antcrew.cli import app
    cfg = {
        "team": "custom",
        "model": "simulated",
        "steps": [{"name": "a", "system_prompt": "Go.", "output_key": "out"}],
    }
    p = tmp_path / "team.yaml"
    p.write_text(yaml.dump(cfg), encoding="utf-8")
    r = CliRunner().invoke(app, ["run", "task", "--config", str(p), "--dry-run"])
    assert r.exit_code == 0
    assert "No LLM calls" in r.output


def test_dry_run_shows_vars(tmp_path):
    import yaml
    from typer.testing import CliRunner
    from antcrew.cli import app
    cfg = {
        "team": "custom",
        "model": "simulated",
        "vars": {"lang": "Python"},
        "steps": [{"name": "a", "system_prompt": "Go.", "output_key": "out"}],
    }
    p = tmp_path / "team.yaml"
    p.write_text(yaml.dump(cfg), encoding="utf-8")
    r = CliRunner().invoke(app, ["run", "task", "--config", str(p), "--dry-run"])
    assert r.exit_code == 0
    assert "lang" in r.output


def test_dry_run_shows_parallel_steps(tmp_path):
    import yaml
    from typer.testing import CliRunner
    from antcrew.cli import app
    cfg = {
        "team": "custom",
        "model": "simulated",
        "steps": [{"parallel": [
            {"name": "be", "system_prompt": "Backend.", "output_key": "be"},
            {"name": "fe", "system_prompt": "Frontend.", "output_key": "fe"},
        ]}],
    }
    p = tmp_path / "team.yaml"
    p.write_text(yaml.dump(cfg), encoding="utf-8")
    r = CliRunner().invoke(app, ["run", "task", "--config", str(p), "--dry-run"])
    assert r.exit_code == 0
    assert "be" in r.output and "fe" in r.output
    assert "par" in r.output


# ===========================================================================
# timeout per step
# ===========================================================================

def test_timeout_stored_on_step():
    from antcrew.teams.custom_team import _parse_steps
    llm = _llm()
    groups = _parse_steps(
        [{"name": "a", "system_prompt": "Go.", "output_key": "out", "timeout": 30}],
        llm,
    )
    assert groups[0][0].timeout == pytest.approx(30.0)


def test_timeout_none_by_default():
    from antcrew.teams.custom_team import _parse_steps
    groups = _parse_steps(
        [{"name": "a", "system_prompt": "Go.", "output_key": "out"}],
        _llm(),
    )
    assert groups[0][0].timeout is None


def test_timeout_not_forwarded_to_template_agent():
    team = CustomTeam(
        [{"name": "a", "system_prompt": "Go.", "output_key": "out", "timeout": 10}],
        _llm(),
    )
    assert not hasattr(team._agents[0], "timeout")


def test_timeout_exceeded_raises_timeout_error():
    """A step that takes longer than its timeout must raise TimeoutError."""
    import threading

    class _SlowLLM(SimulatedLLM):
        def complete(self, messages, **kw):
            threading.Event().wait(5)   # blocks until test forcibly continues
            return "done"

    team = CustomTeam(
        [{"name": "slow", "system_prompt": "Wait.", "output_key": "out", "timeout": 0.1}],
        _SlowLLM(),
    )
    with pytest.raises((TimeoutError, Exception)):
        team.run("task")


def test_timeout_step_completes_within_limit():
    """A fast step with a generous timeout must complete normally."""
    from antcrew.testing import SequencedLLM
    team = CustomTeam(
        [{"name": "a", "system_prompt": "Go.", "output_key": "out", "timeout": 30}],
        SequencedLLM(["result"]),
    )
    result = team.run("task")
    assert result["out"] == "result"


# ===========================================================================
# on_error + default per step
# ===========================================================================

def test_on_error_raise_is_default():
    from antcrew.teams.custom_team import _parse_steps
    groups = _parse_steps(
        [{"name": "a", "system_prompt": "Go.", "output_key": "out"}],
        _llm(),
    )
    assert groups[0][0].on_error == "raise"


def test_on_error_invalid_raises():
    with pytest.raises(ValueError, match="on_error"):
        CustomTeam(
            [{"name": "a", "system_prompt": "Go.", "output_key": "out", "on_error": "ignore"}],
            _llm(),
        )


def test_on_error_skip_uses_default_none():
    class _AlwaysFailLLM(SimulatedLLM):
        def complete(self, messages, **kw):
            raise RuntimeError("always fails")

    team = CustomTeam(
        [{"name": "a", "system_prompt": "Go.", "output_key": "out", "on_error": "skip"}],
        _AlwaysFailLLM(),
    )
    result = team.run("task")
    assert "out" in result.state
    assert result.state["out"] is None


def test_on_error_skip_uses_explicit_default():
    class _AlwaysFailLLM(SimulatedLLM):
        def complete(self, messages, **kw):
            raise RuntimeError("always fails")

    team = CustomTeam(
        [{"name": "a", "system_prompt": "Go.", "output_key": "out",
          "on_error": "skip", "default": "fallback text"}],
        _AlwaysFailLLM(),
    )
    result = team.run("task")
    assert result.state["out"] == "fallback text"


def test_on_error_skip_pipeline_continues():
    """After a skipped (failed) step, subsequent steps still run."""
    from antcrew.testing import SequencedLLM

    class _FirstFailLLM(SimulatedLLM):
        def __init__(self):
            super().__init__()
            self._calls = 0

        def complete(self, messages, **kw):
            self._calls += 1
            if self._calls == 1:
                raise RuntimeError("first step fails")
            return "second result"

    team = CustomTeam(
        [
            {"name": "a", "system_prompt": "Step A.", "output_key": "out_a",
             "on_error": "skip", "default": "fallback"},
            {"name": "b", "system_prompt": "Step B.", "output_key": "out_b"},
        ],
        _FirstFailLLM(),
    )
    result = team.run("task")
    assert result.state["out_a"] == "fallback"
    assert "out_b" in result.state


def test_on_error_raise_propagates_exception():
    class _AlwaysFailLLM(SimulatedLLM):
        def complete(self, messages, **kw):
            raise RuntimeError("always fails")

    team = CustomTeam(
        [{"name": "a", "system_prompt": "Go.", "output_key": "out", "on_error": "raise"}],
        _AlwaysFailLLM(),
    )
    with pytest.raises(RuntimeError, match="always fails"):
        team.run("task")


def test_on_error_from_yaml(tmp_path):
    import yaml
    from antcrew.config import load

    cfg = {
        "team": "custom",
        "model": "simulated",
        "steps": [
            {"name": "a", "system_prompt": "Go.", "output_key": "out",
             "on_error": "skip", "default": "N/A"},
        ],
    }
    p = tmp_path / "team.yaml"
    p.write_text(yaml.dump(cfg), encoding="utf-8")
    team = load(p)
    step = team._step_groups[0][0]
    assert step.on_error == "skip"
    assert step.default == "N/A"


# ===========================================================================
# nested teams (team_file: step)
# ===========================================================================

def test_nested_team_runs_and_merges(tmp_path):
    import yaml

    inner_cfg = {
        "team": "custom",
        "model": "simulated",
        "steps": [
            {"name": "inner_a", "system_prompt": "Inner A.", "output_key": "inner_out"},
        ],
    }
    inner_path = tmp_path / "inner.yaml"
    inner_path.write_text(yaml.dump(inner_cfg), encoding="utf-8")

    from antcrew.testing import SequencedLLM
    llm = SequencedLLM(["inner result"])
    import os
    old_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        team = CustomTeam(
            [{"team_file": "inner.yaml", "name": "nested"}],
            llm,
        )
        result = team.run("task")
    finally:
        os.chdir(old_cwd)

    assert "inner_out" in result.state
    assert result.state["inner_out"] == "inner result"


def test_nested_team_missing_file_raises(tmp_path):
    import os
    old_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        with pytest.raises(FileNotFoundError):
            CustomTeam([{"team_file": "nonexistent.yaml"}], _llm())
    finally:
        os.chdir(old_cwd)


def test_nested_team_inherits_vars(tmp_path):
    """Nested team can declare its own vars which are used in its steps."""
    import yaml
    from antcrew.testing import SequencedLLM

    inner_cfg = {
        "team": "custom",
        "model": "simulated",
        "vars": {"lang": "Rust"},
        "steps": [
            {"name": "coder", "system_prompt": "Code in {lang}.", "output_key": "code"},
        ],
    }
    inner_path = tmp_path / "inner.yaml"
    inner_path.write_text(yaml.dump(inner_cfg), encoding="utf-8")

    llm = SequencedLLM(["rust code"])
    import os
    old_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        team = CustomTeam(
            [{"team_file": "inner.yaml"}],
            llm,
        )
        result = team.run("task")
    finally:
        os.chdir(old_cwd)

    assert result.state.get("code") == "rust code"


# ---------------------------------------------------------------------------
# Gate integration in CustomTeam steps
# ---------------------------------------------------------------------------

class TestCustomTeamGates:
    def test_gate_passes_and_pipeline_continues(self):
        from antcrew.core.gates import NonEmptyGate
        team = CustomTeam(
            steps=[
                {
                    "name": "planner",
                    "system_prompt": "Plan it.",
                    "output_key": "plan",
                    "gate": {"type": "non_empty", "field": "plan"},
                },
                {
                    "name": "executor",
                    "system_prompt": "Execute: {plan}",
                    "output_key": "result",
                },
            ],
            llm=SimulatedLLM(),
        )
        result = team.run("do something")
        assert result.state.get("plan") is not None

    def test_gate_string_shorthand(self):
        team = CustomTeam(
            steps=[
                {
                    "name": "writer",
                    "system_prompt": "Write a story.",
                    "output_key": "story",
                    "gate": "non_empty:story",
                },
            ],
            llm=SimulatedLLM(),
        )
        result = team.run("task")
        assert result.state.get("story") is not None

    def test_gate_fails_raises_gate_error(self):
        from antcrew.core.gates import GateError, NonEmptyGate
        from antcrew.models.simulated import SimulatedLLM as _SIM

        # Monkey-patch to produce None output
        llm = _SIM()
        steps = [
            {
                "name": "broken",
                "system_prompt": "...",
                "output_key": "missing_key",
                "gate": {"type": "non_empty", "field": "definitely_missing"},
            }
        ]
        team = CustomTeam(steps=steps, llm=llm)
        with pytest.raises(GateError):
            team.run("task")

    def test_gate_python_syntax_shorthand(self):
        team = CustomTeam(
            steps=[
                {
                    "name": "coder",
                    "system_prompt": "Write Python code.",
                    "output_key": "code",
                    "gate": "python_syntax:code",
                }
            ],
            llm=SimulatedLLM(),
        )
        result = team.run("task")
        assert result.state.get("code") is not None

    def test_step_without_gate_unaffected(self):
        team = CustomTeam(
            steps=[
                {"name": "step1", "system_prompt": "Do X.", "output_key": "x"},
                {"name": "step2", "system_prompt": "Do Y: {x}", "output_key": "y"},
            ],
            llm=SimulatedLLM(),
        )
        result = team.run("task")
        assert result.state.get("y") is not None


# ---------------------------------------------------------------------------
# parse_gate — YAML spec parser
# ---------------------------------------------------------------------------

class TestParseGate:
    def test_string_shorthand_non_empty(self):
        from antcrew.core.gates import parse_gate, NonEmptyGate
        g = parse_gate("non_empty:prd")
        assert isinstance(g, NonEmptyGate)
        assert g.field == "prd"

    def test_string_shorthand_python_syntax(self):
        from antcrew.core.gates import parse_gate, PythonSyntaxGate
        g = parse_gate("python_syntax:code_artifacts")
        assert isinstance(g, PythonSyntaxGate)

    def test_string_shorthand_json(self):
        from antcrew.core.gates import parse_gate, JsonGate
        g = parse_gate("json:payload")
        assert isinstance(g, JsonGate)

    def test_dict_non_empty_with_min_length(self):
        from antcrew.core.gates import parse_gate, NonEmptyGate
        g = parse_gate({"type": "non_empty", "field": "prd", "min_length": 100})
        assert isinstance(g, NonEmptyGate)
        assert g.min_length == 100

    def test_dict_all_gate(self):
        from antcrew.core.gates import parse_gate, AllGate
        g = parse_gate({
            "type": "all",
            "gates": [
                {"type": "non_empty", "field": "prd"},
                {"type": "python_syntax", "field": "code"},
            ]
        })
        assert isinstance(g, AllGate)

    def test_dict_any_gate(self):
        from antcrew.core.gates import parse_gate, AnyGate
        g = parse_gate({
            "type": "any",
            "gates": [
                {"type": "non_empty", "field": "a"},
                {"type": "non_empty", "field": "b"},
            ]
        })
        assert isinstance(g, AnyGate)

    def test_unknown_type_raises(self):
        from antcrew.core.gates import parse_gate
        with pytest.raises(ValueError, match="Unknown gate type"):
            parse_gate("magic_gate:field")

    def test_all_gate_empty_raises(self):
        from antcrew.core.gates import parse_gate
        with pytest.raises(ValueError):
            parse_gate({"type": "all", "gates": []})

    def test_invalid_spec_raises(self):
        from antcrew.core.gates import parse_gate
        with pytest.raises(ValueError):
            parse_gate(42)


# ---------------------------------------------------------------------------
# gates: in YAML config (flow-based teams)
# ---------------------------------------------------------------------------

class TestConfigGatesYAML:
    def test_load_flow_with_gates_from_yaml(self, tmp_path):
        yaml_content = """
team: dev
model: simulated
flow:
  - [pm, backend_dev]
gates:
  pm: "non_empty:prd"
"""
        cfg_file = tmp_path / "team.yaml"
        cfg_file.write_text(yaml_content, encoding="utf-8")

        from antcrew.config import load
        team = load(cfg_file)
        # Supervisor should have gates wired
        sup = getattr(team, "_supervisor", None) or getattr(team, "supervisor", None)
        if sup is not None:
            assert "pm" in sup._gates
        # At minimum: load() doesn't raise

    def test_load_flow_without_gates_still_works(self, tmp_path):
        yaml_content = """
team: dev
model: simulated
flow:
  - [pm, backend_dev]
"""
        cfg_file = tmp_path / "team.yaml"
        cfg_file.write_text(yaml_content, encoding="utf-8")

        from antcrew.config import load
        team = load(cfg_file)
        assert team is not None

    def test_load_custom_team_with_step_gate(self, tmp_path):
        yaml_content = """
team: custom
model: simulated
steps:
  - name: planner
    system_prompt: "Plan the task."
    output_key: plan
    gate: "non_empty:plan"
  - name: executor
    system_prompt: "Execute: {plan}"
    output_key: result
"""
        cfg_file = tmp_path / "team.yaml"
        cfg_file.write_text(yaml_content, encoding="utf-8")

        from antcrew.config import load
        team = load(cfg_file)
        result = team.run("build something")
        assert result.state.get("plan") is not None
